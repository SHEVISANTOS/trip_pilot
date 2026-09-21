import requests
from django.conf import settings

from integrations.base import BaseClient, NotConfigured
from integrations.countries import resolve_country
from integrations.dataclasses import EsimBundle

CATALOGUE_URL = "https://api.esim-go.com/v2.5/catalogue"

# "Essential"/"Plus" unlimited tiers are premium up-sells over the base
# "Lite" unlimited tier at the same duration — skipped so the recommended
# plan stays the budget-conscious pick, consistent with the rest of the
# budget engine.
PREFERRED_GROUPS = {"Standard Fixed", "Standard Unlimited Lite"}


class EsimGoClient(BaseClient):
    """eSIM Go (api.esim-go.com) bundle catalogue — real per-destination
    eSIM pricing when ESIM_GO_API_KEY is set. Picks the cheapest eligible
    bundle whose validity covers the trip length (or the longest available
    one if nothing covers the full trip). Returns None — so pricing.budget
    falls back to the static regional estimate — when unconfigured, the
    destination can't be resolved to a country, or the call fails.
    """

    def get_bundle(self, destination: str, nights: int) -> EsimBundle | None:
        def fetch():
            if not settings.ESIM_GO_API_KEY:
                raise NotConfigured("ESIM_GO_API_KEY not set")
            country_code = resolve_country(destination)
            if not country_code:
                raise ValueError(f"could not resolve destination={destination!r} to a country code")

            resp = requests.get(
                CATALOGUE_URL,
                headers={"X-API-KEY": settings.ESIM_GO_API_KEY},
                params={"countries": country_code, "perPage": 100},
                timeout=10,
            )
            resp.raise_for_status()
            bundles = resp.json().get("bundles", [])

            eligible = [
                b
                for b in bundles
                if b.get("durationUnit") == "day" and PREFERRED_GROUPS & set(b.get("groups") or [])
            ]
            if not eligible:
                return None

            covering = [b for b in eligible if b["duration"] >= nights]
            if covering:
                best = min(covering, key=lambda b: b["price"])
            else:
                longest = max(b["duration"] for b in eligible)
                best = min((b for b in eligible if b["duration"] == longest), key=lambda b: b["price"])

            return EsimBundle(
                name=best["name"],
                description=best["description"],
                data_mb=best["dataAmount"],
                unlimited=bool(best.get("unlimited")),
                duration_days=best["duration"],
                price=float(best["price"]),
            )

        cache_key = self.make_cache_key("esim", destination, nights)
        return self.call("esim", fetch, None, cache_key, settings.CACHE_TTL_ESIM)
