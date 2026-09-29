"""Unified access to Google Flights / Google Hotels / Google Search data
across two SerpApi-compatible providers — SerpApi (integrations/serpapi.py's
long-standing primary) and SearchApi.io (a second, independent key) — behind
one search(params) call, so one provider's outage or exhausted monthly quota
doesn't take flights/hotels/attractions down with it.

search(params) takes the same params integrations/serpapi.py already builds
(no api_key — this module injects the right one per provider) and returns a
SerpApi-shaped response dict: every existing parser in integrations/serpapi.py
(which reads search_metadata.status, best_flights, properties,
organic_results, top_sights, ...) keeps working unchanged regardless of which
provider actually served the request. On total failure (every configured
provider down) it returns {"error": "..."} instead of raising — callers check
for that key the same way they already check search_metadata.status, and
raise from it so BaseClient.call() logs the failure and returns the client's
existing fallback (unchanged demo-data-degradation behavior).

Why a translation layer for SearchApi.io: verified live against its docs
(searchapi.io/docs/google-flights-api, google-hotels-api, google) that it is
NOT a drop-in field-for-field clone of SerpApi:
- google_flights (request): SerpApi's numeric `type` (1/2/3) is SearchApi's
  string `flight_type` (round_trip/one_way/multi_city); numeric
  `travel_class` (1-4) is a string (economy/premium_economy/business/
  first_class); numeric `stops` (0-3) is a string (any/nonstop/
  one_stop_or_fewer/two_stops_or_fewer); and multi_city_json's per-leg date
  key is `date` on SerpApi vs `outbound_date` on SearchApi.
- google_flights (response): best_flights[], .price, .flights[].airline,
  .layovers, .total_duration are identical on both — no response
  translation needed there. SearchApi's response has no search_metadata at
  all though, so a synthetic {"status": "Success"} is added on success.
- google_hotels (request): q, check_in_date, check_out_date, adults,
  currency are identical — no request translation needed.
- google_hotels (response): SerpApi's rate_per_night/total_rate/
  overall_rating are SearchApi's price_per_night/total_price/rating (each
  still nested under ...extracted_price vs ...extracted_lowest) —
  normalized back to SerpApi's field names on a successful SearchApi
  response.
- google (plain search): request param (q) and organic_results[].link are
  identical. SearchApi's docs show no top_sights-carousel equivalent, so a
  SearchApi-served "things to do in <city>" search legitimately returns no
  attractions — integrations/serpapi.py's SerpApiAttractionsClient already
  treats an empty result as "nothing found" (not a failure) and falls
  through to OpenTripMap, so this degrades the same way a genuinely empty
  SerpApi carousel already does.
"""
import hashlib
import json
import logging
import random

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

SERPAPI_URL = "https://serpapi.com/search.json"
SEARCHAPI_URL = "https://www.searchapi.io/api/v1/search"

# A provider that signals it's out of quota (SerpApi: HTTP 429 with an
# {"error": "...run out of searches..."} body — its real, documented shape;
# SearchApi: any error response with a quota/limit-flavored message) is
# skipped for this long rather than retried on every single call for the
# rest of the month.
QUOTA_COOLDOWN_SECONDS = 6 * 60 * 60

CACHE_KEY_PREFIX = "google_travel:v1:"

_FLIGHT_TYPE_MAP = {"1": "round_trip", "2": "one_way", "3": "multi_city"}
_TRAVEL_CLASS_MAP = {"1": "economy", "2": "premium_economy", "3": "business", "4": "first_class"}
_STOPS_MAP = {"0": "any", "1": "nonstop", "2": "one_stop_or_fewer", "3": "two_stops_or_fewer"}


def _provider_keys() -> dict[str, str]:
    return {"serpapi": settings.SERPAPI_KEY, "searchapi": getattr(settings, "SEARCHAPI_KEY", "")}


def _configured_providers() -> list[str]:
    """Providers named in GOOGLE_TRAVEL_PROVIDERS, in order, that also have a
    key set — a provider named there with no key is silently skipped rather
    than attempted and failing every time.
    """
    order = getattr(settings, "GOOGLE_TRAVEL_PROVIDERS", "serpapi,searchapi")
    names = [p.strip() for p in order.split(",") if p.strip()]
    keys = _provider_keys()
    return [name for name in names if keys.get(name)]


def is_configured() -> bool:
    """True when at least one provider in GOOGLE_TRAVEL_PROVIDERS has a key
    set — callers use this in place of checking settings.SERPAPI_KEY alone,
    since a SerpApi-less config (SEARCHAPI_KEY only) is now valid too.
    """
    return bool(_configured_providers())


def _cooldown_cache_key(provider: str) -> str:
    return f"{CACHE_KEY_PREFIX}cooldown:{provider}"


def _in_cooldown(provider: str) -> bool:
    return bool(cache.get(_cooldown_cache_key(provider)))


def _start_cooldown(provider: str) -> None:
    cache.set(_cooldown_cache_key(provider), True, QUOTA_COOLDOWN_SECONDS)
    logger.warning("google_travel: %s looks out of quota, cooling down for %ss", provider, QUOTA_COOLDOWN_SECONDS)


def _result_cache_key(params: dict) -> str:
    # Sorted so key order in the caller's params dict never affects the
    # cache key — the same logical search should always hit the same entry.
    items = sorted((str(k), str(v)) for k, v in params.items())
    raw = "&".join(f"{k}={v}" for k, v in items)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return f"{CACHE_KEY_PREFIX}result:{digest}"


def _translate_flight_params_for_searchapi(params: dict) -> dict:
    translated = dict(params)
    if "type" in translated:
        translated["flight_type"] = _FLIGHT_TYPE_MAP.get(str(translated.pop("type")), "round_trip")
    if "travel_class" in translated:
        translated["travel_class"] = _TRAVEL_CLASS_MAP.get(str(translated["travel_class"]), translated["travel_class"])
    if "stops" in translated:
        translated["stops"] = _STOPS_MAP.get(str(translated["stops"]), translated["stops"])
    if "multi_city_json" in translated:
        try:
            legs = json.loads(translated["multi_city_json"])
            for leg in legs:
                if "date" in leg:
                    leg["outbound_date"] = leg.pop("date")
            translated["multi_city_json"] = json.dumps(legs)
        except (TypeError, ValueError):
            pass  # Malformed input — let SearchApi reject it rather than guess.
    return translated


def _translate_params_for_searchapi(params: dict) -> dict:
    if params.get("engine") == "google_flights":
        return _translate_flight_params_for_searchapi(params)
    return dict(params)  # google_hotels / google: request params are identical.


def _normalize_hotels_properties(payload: dict) -> dict:
    properties = payload.get("properties")
    if not isinstance(properties, list):
        return payload
    normalized = []
    for prop in properties:
        prop = dict(prop)
        if "rate_per_night" not in prop and isinstance(prop.get("price_per_night"), dict):
            ppn = prop["price_per_night"]
            prop["rate_per_night"] = {"lowest": ppn.get("price"), "extracted_lowest": ppn.get("extracted_price")}
        if "total_rate" not in prop and isinstance(prop.get("total_price"), dict):
            tp = prop["total_price"]
            prop["total_rate"] = {"lowest": tp.get("price"), "extracted_lowest": tp.get("extracted_price")}
        if "overall_rating" not in prop and "rating" in prop:
            prop["overall_rating"] = prop["rating"]
        normalized.append(prop)
    payload = dict(payload)
    payload["properties"] = normalized
    return payload


def _normalize_searchapi_payload(engine: str, payload: dict) -> dict:
    """Makes a successful SearchApi.io response look like SerpApi's shape —
    see the module docstring for exactly what differs per engine.
    """
    payload = dict(payload)
    search_metadata = dict(payload.get("search_metadata") or {})
    search_metadata.setdefault("status", "Success")
    payload["search_metadata"] = search_metadata
    if engine == "google_hotels":
        payload = _normalize_hotels_properties(payload)
    return payload


def _response_error(payload: dict, http_status: int) -> str | None:
    if isinstance(payload, dict) and payload.get("error"):
        return str(payload["error"])
    if http_status == 429:
        return "rate limited (HTTP 429)"
    if http_status >= 400:
        return f"HTTP {http_status}"
    return None


def _looks_quota_related(message: str) -> bool:
    message = message.lower()
    return any(word in message for word in ("quota", "run out", "limit", "429"))


def _call_serpapi(params: dict, api_key: str) -> tuple[dict | None, str | None]:
    try:
        resp = requests.get(SERPAPI_URL, params={**params, "api_key": api_key}, timeout=30)
    except requests.RequestException as exc:
        return None, str(exc)
    try:
        payload = resp.json()
    except ValueError:
        return None, f"HTTP {resp.status_code}: response was not JSON"
    error = _response_error(payload, resp.status_code)
    if error:
        return None, error
    return payload, None


def _call_searchapi(params: dict, api_key: str) -> tuple[dict | None, str | None]:
    request_params = {**_translate_params_for_searchapi(params), "api_key": api_key}
    try:
        resp = requests.get(SEARCHAPI_URL, params=request_params, timeout=30)
    except requests.RequestException as exc:
        return None, str(exc)
    try:
        payload = resp.json()
    except ValueError:
        return None, f"HTTP {resp.status_code}: response was not JSON"
    error = _response_error(payload, resp.status_code)
    if error:
        return None, error
    return _normalize_searchapi_payload(params.get("engine", ""), payload), None


_PROVIDER_CALLERS = {"serpapi": _call_serpapi, "searchapi": _call_searchapi}


def search(params: dict) -> dict:
    """SerpApi-style params, without api_key. Tries each configured,
    non-cooled-down provider in GOOGLE_TRAVEL_PROVIDERS order (shuffled
    first when GOOGLE_TRAVEL_STRATEGY=balance) via a shared Django cache
    keyed on the params themselves, so an identical call is never re-issued
    to any provider while cached. Returns a SerpApi-shaped dict on success,
    or {"error": "..."} once every provider has failed — never raises.
    """
    cache_key = _result_cache_key(params)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    providers = _configured_providers()
    if not providers:
        return {"error": "no google_travel provider configured (SERPAPI_KEY/SEARCHAPI_KEY both unset)"}

    keys = _provider_keys()
    candidates = [p for p in providers if not _in_cooldown(p)] or providers
    if getattr(settings, "GOOGLE_TRAVEL_STRATEGY", "failover") == "balance" and len(candidates) > 1:
        candidates = candidates[:]
        random.shuffle(candidates)

    errors = []
    for provider in candidates:
        payload, error = _PROVIDER_CALLERS[provider](params, keys[provider])
        if error is None:
            cache.set(cache_key, payload, settings.CACHE_TTL_SERPAPI)
            return payload
        errors.append(f"{provider}: {error}")
        if _looks_quota_related(error):
            _start_cooldown(provider)

    return {"error": "; ".join(errors)}
