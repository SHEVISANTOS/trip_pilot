"""Google Gemini — last-resort AI attraction estimates.

Not a live-data provider like SerpApi/OpenTripMap: this generates a
plausible, destination-specific estimate from the model's training data, only
reached when both real attraction sources (SerpApi's "Top sights", then
OpenTripMap) have nothing for the destination — the same gap that used to be
filled by integrations/fixtures.py's generic "sample_data_for()" data, which
returns the exact same made-up numbers for every non-Istanbul destination
regardless of what the destination actually is. An AI estimate for the real
destination is a strictly better fallback than that, provided it's never
confused with real data — every Attraction this returns gets "(AI estimate)"
appended to its description for exactly that reason, and pricing.budget still
falls through to the generic fixture if Gemini itself is unconfigured or down
(see pricing/budget.py's attractions cascade).

Verified live against the real API (not assumed from docs):
- gemini-2.5-flash has "thinking" enabled by default (confirmed via
  GET /v1beta/models), which silently eats most of a small maxOutputTokens
  budget on internal reasoning tokens before ever emitting the answer
  (confirmed live: a 200-token budget produced a truncated 6-token answer,
  finishReason MAX_TOKENS). Setting generationConfig.thinkingConfig.
  thinkingBudget=0 fixes this — same request then returns a complete, direct
  JSON answer with finishReason STOP.
- A plain-language instruction not to use markdown fences was followed in
  live testing, but the parser strips ```json fences defensively anyway
  (LLM output formatting isn't a guaranteed contract).
"""
import json
import re

import requests
from django.conf import settings

from integrations.base import BaseClient, NotConfigured
from integrations.dataclasses import Attraction

GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

ATTRACTIONS_PROMPT = """You are a travel data assistant. List up to 6 real, well-known tourist attractions in {destination}.
Respond with ONLY a JSON array (no markdown fences, no commentary), each item shaped exactly like:
{{"name": "<attraction name>", "cost_usd": <typical entry cost in USD as a number, 0 if free>, "description": "<one short factual sentence>"}}
Use real, specific, well-known places only. If you are not confident {destination} has 6 distinct real attractions, return fewer rather than inventing any. If you cannot name any real attraction there with confidence, respond with an empty JSON array: []"""

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text.strip())


class GeminiEstimateClient(BaseClient):
    def estimate_attractions(self, destination: str) -> list[Attraction] | None:
        def fetch():
            if not settings.GEMINI_API_KEY:
                raise NotConfigured("GEMINI_API_KEY not set")

            resp = requests.post(
                GENERATE_URL,
                params={"key": settings.GEMINI_API_KEY},
                json={
                    "contents": [{"parts": [{"text": ATTRACTIONS_PROMPT.format(destination=destination)}]}],
                    "generationConfig": {
                        "temperature": 0.3,
                        "maxOutputTokens": 800,
                        # Without this, "thinking" tokens (on by default for
                        # gemini-2.5-flash) can consume the whole output
                        # budget before the model emits any answer text —
                        # verified live, see module docstring.
                        "thinkingConfig": {"thinkingBudget": 0},
                    },
                },
                timeout=30,
            )
            resp.raise_for_status()
            payload = resp.json()

            candidates = payload.get("candidates") or []
            if not candidates:
                raise ValueError(f"Gemini returned no candidates: {payload}")
            parts = (candidates[0].get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts)
            if not text:
                raise ValueError(f"Gemini returned an empty response: {payload}")

            items = json.loads(_strip_fences(text))
            if not isinstance(items, list):
                raise ValueError(f"Gemini response was not a JSON array: {text!r}")

            attractions = []
            for item in items:
                name = (item.get("name") or "").strip()
                if not name:
                    continue
                try:
                    cost = float(item.get("cost_usd") or 0)
                except (TypeError, ValueError):
                    cost = 0.0
                desc = (item.get("description") or "").strip() or f"Popular attraction in {destination}."
                attractions.append(
                    Attraction(name=name, cost=cost, desc=f"{desc} (AI estimate)", optional=False)
                )
                if len(attractions) == 6:
                    break
            if not attractions:
                return None
            # Mirrors every other attractions source: the top 5 are core,
            # the 6th (if any) is a nice-to-have, not part of the core budget.
            for attraction in attractions[5:]:
                attraction.optional = True
            return attractions

        cache_key = self.make_cache_key("gemini_attractions", destination)
        return self.call("gemini_attractions", fetch, None, cache_key, settings.CACHE_TTL_GEMINI)
