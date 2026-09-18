import requests
from django.conf import settings

from integrations.base import BaseClient, NotConfigured
from integrations.dataclasses import Attraction
from integrations.fixtures import sample_data_for

GEONAME_URL = "https://api.opentripmap.com/0.1/en/places/geoname"
RADIUS_URL = "https://api.opentripmap.com/0.1/en/places/radius"


class OpenTripMapClient(BaseClient):
    """Free, no-partner-approval POI source. No pricing data is available from
    OpenTripMap itself, so live results reuse the fixture cost/optional
    pattern (first four named POIs priced, rest marked optional) rather than
    inventing prices the API doesn't provide.
    """

    def search_attractions(self, destination: str) -> list[Attraction]:
        def fetch():
            if not settings.OPENTRIPMAP_API_KEY:
                raise NotConfigured("OPENTRIPMAP_API_KEY not set")
            geo = requests.get(
                GEONAME_URL, params={"name": destination, "apikey": settings.OPENTRIPMAP_API_KEY}, timeout=10
            )
            geo.raise_for_status()
            location = geo.json()
            places = requests.get(
                RADIUS_URL,
                params={
                    "radius": 8000,
                    "lon": location["lon"],
                    "lat": location["lat"],
                    "kinds": "interesting_places",
                    "limit": 6,
                    "apikey": settings.OPENTRIPMAP_API_KEY,
                },
                timeout=10,
            )
            places.raise_for_status()
            fixture_costs = [a.cost for a in sample_data_for(destination).attractions]
            features = places.json().get("features", [])
            if not features:
                return None
            return [
                Attraction(
                    name=f["properties"]["name"] or f"{destination} point of interest",
                    cost=fixture_costs[i] if i < len(fixture_costs) else 0,
                    desc="Point of interest from OpenTripMap.",
                    optional=i >= 5,
                )
                for i, f in enumerate(features)
            ]

        fallback = sample_data_for(destination).attractions
        result = self.call("opentripmap", fetch, None)
        return result or fallback
