import requests
from django.conf import settings

from integrations.base import BaseClient, NotConfigured

DISTANCE_MATRIX_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"

# Flat estimate used until a Maps key is configured — matches the airport
# transfer allowance already baked into pricing.budget.build_plan().
FALLBACK_TRANSFER_COST = 80.0


class MapsClient(BaseClient):
    """Google Maps Distance Matrix for airport-transfer/local-transport cost
    estimation. Not yet wired into pricing.budget.build_plan() (that still
    uses the flat allowance from the original prototype) — available for the
    optimizer/itinerary work to call once real transfer distances matter.
    """

    def estimate_transfer_cost(self, origin: str, destination: str) -> float:
        def fetch():
            if not settings.GOOGLE_MAPS_API_KEY:
                raise NotConfigured("GOOGLE_MAPS_API_KEY not set")
            resp = requests.get(
                DISTANCE_MATRIX_URL,
                params={"origins": origin, "destinations": destination, "key": settings.GOOGLE_MAPS_API_KEY},
                timeout=10,
            )
            resp.raise_for_status()
            element = resp.json()["rows"][0]["elements"][0]
            km = element["distance"]["value"] / 1000
            return round(km * 1.2, 2)  # flat per-km taxi estimate

        return self.call("maps", fetch, FALLBACK_TRANSFER_COST)
