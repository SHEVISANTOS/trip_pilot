"""Live hotel rates from LiteAPI (Nuitée Connect).

Unlike the other estimators in this project, this returns *real* per-property
prices, star ratings and addresses — so hotels no longer need invented
nightly rates. pricing.budget falls back to its regional estimate only when
LiteAPI has no coverage for a destination (smaller cities often return
nothing, even in production).

Sandbox keys are prefixed `sand_`; the public/Stripe keys from the dashboard
are for the widget and payment flows and will 401 here.
"""
from datetime import date

import requests
from django.conf import settings

from integrations.base import BaseClient, NotConfigured
from integrations.countries import resolve_country
from integrations.dataclasses import HotelOffer

RATES_URL = "https://api.liteapi.travel/v3.0/hotels/rates"


def _nightly(total: float, nights: int) -> float:
    return round(total / max(nights, 1), 2)


class LiteApiHotelClient(BaseClient):
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
        """Real priced properties for `destination`, cheapest first. Returns
        None when unconfigured, when the destination can't be resolved, or
        when LiteAPI has no inventory there.
        """

        def fetch():
            if not settings.LITEAPI_KEY:
                raise NotConfigured("LITEAPI_KEY not set")
            if not (checkin and checkout):
                raise ValueError("LiteAPI needs both checkin and checkout dates")
            country = resolve_country(destination)
            if not country:
                raise ValueError(f"could not resolve {destination!r} to a country code")
            city = destination.split(",")[0].strip() or destination

            resp = requests.post(
                RATES_URL,
                headers={"X-API-Key": settings.LITEAPI_KEY, "Content-Type": "application/json"},
                json={
                    "cityName": city,
                    "countryCode": country,
                    "checkin": checkin.isoformat(),
                    "checkout": checkout.isoformat(),
                    "currency": "USD",
                    "guestNationality": resolve_country(nationality) or country,
                    "occupancies": [{"adults": max(adults, 1)}],
                    "limit": max(limit * 3, 9),
                },
                timeout=45,
            )
            resp.raise_for_status()
            payload = resp.json()

            metadata = {h["id"]: h for h in (payload.get("hotels") or [])}
            offers = []
            for row in payload.get("data") or []:
                meta = metadata.get(row.get("hotelId"))
                if not meta:
                    continue
                try:
                    total = float(row["roomTypes"][0]["rates"][0]["retailRate"]["total"][0]["amount"])
                except (KeyError, IndexError, TypeError, ValueError):
                    continue
                stars = meta.get("stars") or 0
                offers.append(
                    HotelOffer(
                        name=meta.get("name") or city,
                        area=meta.get("city_name") or city,
                        rating=f"{stars}★" if stars else "Unrated",
                        night=_nightly(total, nights),
                        total=round(total, 2),
                        desc=meta.get("address") or f"Live rate for {city}.",
                    )
                )

            offers.sort(key=lambda o: o.night)
            return offers[:limit] or None

        cache_key = self.make_cache_key(
            "liteapi", destination, checkin, checkout, adults, nights, limit
        )
        return self.call("hotels", fetch, None, cache_key, settings.CACHE_TTL_FLIGHTS_HOTELS)
