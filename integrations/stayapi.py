"""Live hotel rates from StayAPI (Booking.com data), used as the second
hotel tier — after LiteAPI, before the regional estimate. Not a replacement
for LiteAPI: it has better coverage for some destinations (Mwanza: 2 hotels
from LiteAPI vs 25 here) and worse for others, so pricing.budget tries both
before falling back to an estimate. The free tier is 50 requests total, so
this is deliberately the *second* thing tried, not the first — LiteAPI
already resolves most destinations, so this only fires on the gap.

Two-step flow: resolve a destination name to a Booking.com dest_id, then
search that dest_id for rates. The docs' example response shape doesn't
match what the live API actually returns (confirmed by testing against the
real key, not assumed) — e.g. hotel_name -> name, a flat min_total_price ->
a nested price.amount, and destinations/lookup's default suggestion is
sometimes a REGION that the search endpoint silently returns zero hotels
for (Zanzibar did this) even though a CITY-typed alternative a few entries
down the same suggestions list works fine.
"""
from datetime import date

import requests
from django.conf import settings

from integrations.base import BaseClient, NotConfigured
from integrations.dataclasses import HotelOffer

LOOKUP_URL = "https://api.stayapi.com/v1/booking/destinations/lookup"
SEARCH_URL = "https://api.stayapi.com/v1/booking/search"


def _resolve_city_dest_id(payload: dict) -> int | None:
    """destinations/lookup's top-level dest_id is "best match", which is
    sometimes a REGION/AIRPORT/HOTEL that the search endpoint returns no
    results for. Prefer a CITY-typed suggestion when one exists.
    """
    if payload.get("dest_type") == "CITY":
        return payload.get("dest_id")
    for suggestion in payload.get("suggestions") or []:
        if suggestion.get("dest_type") == "CITY":
            return suggestion.get("dest_id")
    return payload.get("dest_id")


class StayApiHotelClient(BaseClient):
    def search_hotels(
        self,
        destination: str,
        checkin: date | None,
        checkout: date | None,
        adults: int,
        nights: int,
        nationality: str = "",
        limit: int = 3,
    ) -> list[HotelOffer] | None:
        def fetch():
            if not settings.STAYAPI_KEY:
                raise NotConfigured("STAYAPI_KEY not set")
            if not (checkin and checkout):
                raise ValueError("StayAPI needs both checkin and checkout dates")
            city = destination.split(",")[0].strip() or destination
            headers = {"x-api-key": settings.STAYAPI_KEY}

            lookup = requests.get(LOOKUP_URL, headers=headers, params={"query": city}, timeout=15)
            lookup.raise_for_status()
            lookup_payload = lookup.json()
            if not lookup_payload.get("success"):
                raise ValueError(f"StayAPI could not resolve {city!r} to a destination")
            dest_id = _resolve_city_dest_id(lookup_payload)
            if dest_id is None:
                raise ValueError(f"StayAPI returned no usable dest_id for {city!r}")

            search = requests.get(
                SEARCH_URL,
                headers=headers,
                params={
                    "dest_id": dest_id,
                    "checkin": checkin.isoformat(),
                    "checkout": checkout.isoformat(),
                    "adults": max(adults, 1),
                    "rooms": 1,
                    "currency": "USD",
                },
                timeout=30,
            )
            search.raise_for_status()
            payload = search.json()
            hotels = ((payload.get("data") or {}).get("hotels")) or []
            # Real Booking.com destination search — dates, party size and an
            # affiliate marker already filled in. Not per-hotel (the search
            # response doesn't include one), but every hotel from this batch
            # gets it: landing on the right destination/dates beats a dead
            # modal, and it's a genuine, verified-working URL either way.
            destination_url = payload.get("url") or ""

            offers = []
            for hotel in hotels:
                if hotel.get("is_sold_out"):
                    continue
                price = hotel.get("price") or {}
                amount = price.get("amount")
                if amount is None:
                    continue
                stars = hotel.get("star_rating")
                rating = hotel.get("rating") or {}
                score = rating.get("score")
                offers.append(
                    HotelOffer(
                        name=hotel.get("name") or city,
                        area=hotel.get("display_location") or city,
                        rating=f"{stars}★" if stars else (f"{score}/10" if score else "Unrated"),
                        night=round(amount / max(nights, 1), 2),
                        total=round(amount, 2),
                        desc=hotel.get("address") or f"Live rate for {city}.",
                        booking_url=destination_url,
                    )
                )

            offers.sort(key=lambda o: o.night)
            return offers[:limit] or None

        cache_key = self.make_cache_key("stayapi", destination, checkin, checkout, adults, nights, limit)
        return self.call("hotels_stayapi", fetch, None, cache_key, settings.CACHE_TTL_FLIGHTS_HOTELS)
