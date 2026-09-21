import requests
from django.conf import settings

from integrations.base import BaseClient, NotConfigured

ROUTE_MATRIX_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"

# One-way estimate used until Maps is usable. pricing.budget.build_plan()
# doubles this for the round-trip (airport -> hotel -> airport) allowance, so
# this is half of the original flat $80 round-trip placeholder, not $80 itself.
FALLBACK_TRANSFER_COST = 40.0


class MapsClient(BaseClient):
    """Google Routes API (computeRouteMatrix) for airport-transfer cost
    estimation. The classic Distance Matrix API this was originally written
    against now rejects new projects with "legacy API not enabled" (confirmed
    live against this project) — Google's own error message points at Routes
    API as the replacement, which is what this calls. Routes API additionally
    requires a Cloud project with billing enabled (a Maps Platform
    requirement even within the free monthly credit); until that's set up,
    or if GOOGLE_MAPS_API_KEY is unset, this falls back to
    FALLBACK_TRANSFER_COST.
    """

    def estimate_transfer_cost(self, origin: str, destination: str) -> float:
        def fetch():
            if not settings.GOOGLE_MAPS_API_KEY:
                raise NotConfigured("GOOGLE_MAPS_API_KEY not set")
            resp = requests.post(
                ROUTE_MATRIX_URL,
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": settings.GOOGLE_MAPS_API_KEY,
                    "X-Goog-FieldMask": "originIndex,destinationIndex,distanceMeters,condition",
                },
                json={
                    "origins": [{"waypoint": {"address": origin}}],
                    "destinations": [{"waypoint": {"address": destination}}],
                    "travelMode": "DRIVE",
                },
                timeout=10,
            )
            resp.raise_for_status()
            results = resp.json()
            element = next((r for r in results if r.get("condition") == "ROUTE_EXISTS"), None)
            if not element:
                raise ValueError(f"no route found between {origin!r} and {destination!r}")
            km = element["distanceMeters"] / 1000
            return round(km * 1.2, 2)  # flat per-km taxi estimate, one-way

        cache_key = self.make_cache_key("maps_transfer", origin, destination)
        return self.call("maps", fetch, FALLBACK_TRANSFER_COST, cache_key, settings.CACHE_TTL_MAPS)
