"""Airport-transfer distance/cost estimation, in three tiers.

1. Google Routes API — best data, but needs a key *and* billing enabled on
   the Cloud project (a Maps Platform requirement even inside the free
   monthly credit). The legacy Distance Matrix API is no longer available to
   new projects, hence Routes.
2. OpenStreetMap (Nominatim geocoding + OSRM routing) — keyless, free, real
   driving distances. Used whenever tier 1 is unconfigured or refuses.
3. A flat per-transfer figure, if even OSM can't resolve the route.

Results are cached for 30 days (distances don't change), which also keeps
usage of the public OSM endpoints well inside their fair-use policies.
"""
import logging

import requests
from django.conf import settings

from integrations.base import BaseClient

logger = logging.getLogger(__name__)

ROUTE_MATRIX_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_URL = "https://router.project-osrm.org/route/v1/driving"

# Nominatim's usage policy requires a identifying User-Agent.
OSM_USER_AGENT = "TripPilotAI/1.0 (travel budget planner)"

COST_PER_KM = 1.2
# One-way. pricing.budget.build_plan() doubles it for the round trip.
FALLBACK_TRANSFER_COST = 40.0


class MapsClient(BaseClient):
    def estimate_transfer_cost(self, origin: str, destination: str) -> float:
        def fetch():
            km = None
            if settings.GOOGLE_MAPS_API_KEY:
                try:
                    km = self._google_km(origin, destination)
                except Exception as exc:  # noqa: BLE001 - any Google failure just drops a tier
                    logger.warning("Google Routes unavailable (%s); falling back to OSM routing", exc)
            if km is None:
                km = self._osm_km(origin, destination)
            return round(km * COST_PER_KM, 2)

        cache_key = self.make_cache_key("maps_transfer", origin, destination)
        return self.call("maps", fetch, FALLBACK_TRANSFER_COST, cache_key, settings.CACHE_TTL_MAPS)

    def _google_km(self, origin: str, destination: str) -> float:
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
            raise ValueError(f"no Google route between {origin!r} and {destination!r}")
        return element["distanceMeters"] / 1000

    def _geocode(self, place: str) -> tuple[float, float]:
        resp = requests.get(
            NOMINATIM_URL,
            params={"q": place, "format": "json", "limit": 1},
            headers={"User-Agent": OSM_USER_AGENT},
            timeout=10,
        )
        resp.raise_for_status()
        hits = resp.json()
        if not hits:
            raise ValueError(f"Nominatim could not geocode {place!r}")
        return float(hits[0]["lat"]), float(hits[0]["lon"])

    def _osm_km(self, origin: str, destination: str) -> float:
        o_lat, o_lon = self._geocode(origin)
        d_lat, d_lon = self._geocode(destination)
        resp = requests.get(
            f"{OSRM_URL}/{o_lon},{o_lat};{d_lon},{d_lat}",
            params={"overview": "false"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") != "Ok" or not payload.get("routes"):
            raise ValueError(f"OSRM found no route between {origin!r} and {destination!r}")
        return payload["routes"][0]["distance"] / 1000
