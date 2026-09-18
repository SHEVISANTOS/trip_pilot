import requests
from django.conf import settings

from integrations.base import BaseClient, NotConfigured
from integrations.dataclasses import FlightOffer, HotelOffer
from integrations.fixtures import sample_data_for

AMADEUS_TOKEN_URL = "https://test.api.amadeus.com/v1/security/oauth2/token"
AMADEUS_FLIGHT_OFFERS_URL = "https://test.api.amadeus.com/v2/shopping/flight-offers"
AMADEUS_HOTEL_OFFERS_URL = "https://test.api.amadeus.com/v3/shopping/hotel-offers"


class AmadeusClient(BaseClient):
    """Flight Offers Search + Hotel Offers (self-service, free tier).
    Falls back to the illustrative fixtures whenever no API key is
    configured or the live call fails, so callers never see raw errors.
    """

    def _access_token(self) -> str:
        if not (settings.AMADEUS_API_KEY and settings.AMADEUS_API_SECRET):
            raise NotConfigured("AMADEUS_API_KEY/AMADEUS_API_SECRET not set")
        resp = requests.post(
            AMADEUS_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": settings.AMADEUS_API_KEY,
                "client_secret": settings.AMADEUS_API_SECRET,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def search_flights(self, origin: str, destination: str, adults: int) -> list[FlightOffer]:
        def fetch():
            token = self._access_token()
            resp = requests.get(
                AMADEUS_FLIGHT_OFFERS_URL,
                headers={"Authorization": f"Bearer {token}"},
                params={"originLocationCode": origin, "destinationLocationCode": destination, "adults": adults},
                timeout=10,
            )
            resp.raise_for_status()
            offers = resp.json().get("data", [])
            return [
                FlightOffer(
                    airline=o["validatingAirlineCodes"][0],
                    route=f"{origin} → {destination}",
                    stops=f"{len(o['itineraries'][0]['segments']) - 1} stop(s)",
                    duration=o["itineraries"][0]["duration"],
                    price=float(o["price"]["total"]),
                    label="Live offer",
                )
                for o in offers[:3]
            ] or None

        fallback = sample_data_for(destination).flights
        result = self.call("amadeus_flights", fetch, None)
        return result or fallback

    def search_hotels(self, destination: str, nights: int, adults: int) -> list[HotelOffer]:
        def fetch():
            token = self._access_token()
            resp = requests.get(
                AMADEUS_HOTEL_OFFERS_URL,
                headers={"Authorization": f"Bearer {token}"},
                params={"cityCode": destination, "adults": adults},
                timeout=10,
            )
            resp.raise_for_status()
            offers = resp.json().get("data", [])
            return [
                HotelOffer(
                    name=o["hotel"]["name"],
                    area=o["hotel"].get("address", {}).get("cityName", destination),
                    rating=f"{o['hotel'].get('rating', '?')}★",
                    night=float(o["offers"][0]["price"]["total"]) / max(nights, 1),
                    total=float(o["offers"][0]["price"]["total"]),
                    desc="Live offer from Amadeus Hotel Search.",
                )
                for o in offers[:3]
            ] or None

        fallback = sample_data_for(destination).hotels
        result = self.call("amadeus_hotels", fetch, None)
        return result or fallback
