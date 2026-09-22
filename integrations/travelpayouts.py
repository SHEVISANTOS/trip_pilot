"""Travelpayouts (Aviasales) flight price data.

Replaces the Amadeus client. Travelpayouts serves *cached* market prices
rather than live bookable fares — a better fit for a budget planner: no
per-search rate pressure, and a usable answer for routes/dates that a live
fare search would return empty for. The tradeoff is that far-future dates
often have no cached data, hence the progressive date fallback in
search_flights().

Note Travelpayouts has no hotel endpoint any more (the Hotellook hosts all
404), so hotels are estimated in pricing.budget instead.
"""
import json
from functools import lru_cache
from pathlib import Path

import requests
from django.conf import settings

from integrations.base import BaseClient, NotConfigured
from integrations.countries import resolve_country
from integrations.dataclasses import FlightOffer

PRICES_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
DATA_DIR = Path(__file__).resolve().parent / "data"
AIRPORTS_PATH = DATA_DIR / "airports.json"
AIRLINES_PATH = DATA_DIR / "airlines.json"
CAPITALS_PATH = DATA_DIR / "capitals.json"

FLIGHT_LABELS = ["Cheapest", "Recommended", "Alternative"]


@lru_cache(maxsize=1)
def _airports_by_name() -> dict[str, list[dict]]:
    """Lowercased city name -> every matching airport record. Names are not
    unique (London GB vs London CA), so this keeps all candidates and lets
    resolve_iata() disambiguate by country.
    """
    index: dict[str, list[dict]] = {}
    for record in json.loads(AIRPORTS_PATH.read_text(encoding="utf-8")):
        index.setdefault(record["name"].casefold(), []).append(record)
    return index


@lru_cache(maxsize=1)
def _airline_names() -> dict[str, str]:
    return json.loads(AIRLINES_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _capitals() -> dict[str, str]:
    """ISO-3166 alpha-2 -> capital city name, so a destination given as a
    country ("Tunisia") still resolves to a flyable airport (Tunis/TUN).
    """
    return json.loads(CAPITALS_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _airports_by_code() -> dict[str, dict]:
    return {r["code"]: r for r in json.loads(AIRPORTS_PATH.read_text(encoding="utf-8"))}


def airport_coordinates(code: str) -> tuple[float, float] | None:
    record = _airports_by_code().get(code or "")
    if not record or record.get("lat") is None or record.get("lon") is None:
        return None
    return record["lat"], record["lon"]


def resolve_iata(location: str) -> str | None:
    """Free-text city -> IATA city code ("Istanbul, Türkiye" -> "IST").
    Disambiguates same-named cities using the country resolver already used
    for visa/eSIM lookups, so "London, United Kingdom" gives LON rather than
    London, Ontario.
    """
    if not location:
        return None
    normalized = location.strip().casefold()
    index = _airports_by_name()

    candidates: list[dict] = []
    segments = [normalized] + [s.strip() for s in normalized.split(",")]
    for segment in segments:
        for record in index.get(segment, []):
            if record not in candidates:
                candidates.append(record)

    country = resolve_country(location)
    if not candidates:
        # The destination may be a country rather than a city ("Tunisia"),
        # in which case route through its capital's airport.
        capital = _capitals().get(country or "")
        if capital:
            candidates = list(index.get(capital.casefold(), []))
        if not candidates:
            return None

    if country:
        in_country = [c for c in candidates if c["country_code"] == country]
        if in_country:
            return in_country[0]["code"]
    return candidates[0]["code"]


def _format_duration(minutes: int | None) -> str:
    if not minutes:
        return "Duration varies"
    return f"Approx. {minutes // 60}h {minutes % 60:02d}m each way"


class TravelpayoutsClient(BaseClient):
    """Cached round-trip flight prices. Returns None (so pricing.budget uses
    its fixture fallback) when unconfigured, when either endpoint of the
    route can't be resolved to an IATA code, or when the API has no cached
    price for the route at all.
    """

    def search_flights(self, origin, destination, adults, start_date=None, end_date=None):
        def fetch():
            if not settings.TRAVELPAYOUTS_TOKEN:
                raise NotConfigured("TRAVELPAYOUTS_TOKEN not set")
            origin_code = resolve_iata(origin)
            destination_code = resolve_iata(destination)
            if not origin_code or not destination_code:
                raise ValueError(
                    f"could not resolve route {origin!r} -> {destination!r} to IATA codes "
                    f"(got {origin_code!r} -> {destination_code!r})"
                )

            # Cached data thins out the further ahead you look, so try exact
            # dates first, then just the month, then whatever the route has.
            attempts = []
            if start_date and end_date:
                attempts.append({"departure_at": start_date.isoformat(), "return_at": end_date.isoformat()})
                attempts.append({"departure_at": start_date.strftime("%Y-%m")})
            attempts.append({})

            for extra_params in attempts:
                resp = requests.get(
                    PRICES_URL,
                    params={
                        "origin": origin_code,
                        "destination": destination_code,
                        "currency": "usd",
                        "one_way": "false",
                        "sorting": "price",
                        "limit": 3,
                        "token": settings.TRAVELPAYOUTS_TOKEN,
                        **extra_params,
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                rows = resp.json().get("data") or []
                if rows:
                    return self._to_offers(rows, origin_code, destination_code)
            return None

        cache_key = self.make_cache_key(
            "travelpayouts", origin, destination, start_date, end_date
        )
        return self.call("travelpayouts", fetch, None, cache_key, settings.CACHE_TTL_FLIGHTS_HOTELS)

    def _to_offers(self, rows, origin_code, destination_code) -> list[FlightOffer]:
        airlines = _airline_names()
        offers = []
        for i, row in enumerate(rows[:3]):
            code = row.get("airline", "")
            transfers = row.get("transfers", 0)
            offers.append(
                FlightOffer(
                    airline=airlines.get(code, code or "Airline"),
                    route=f"{origin_code} → {destination_code} → {origin_code}",
                    stops="Direct" if transfers == 0 else f"{transfers} stop{'s' if transfers > 1 else ''}",
                    duration=_format_duration(row.get("duration_to")),
                    # Travelpayouts quotes per-person round-trip fares.
                    price=float(row["price"]),
                    label=FLIGHT_LABELS[i] if i < len(FLIGHT_LABELS) else "Alternative",
                )
            )
        return offers
