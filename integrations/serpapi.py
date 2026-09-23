"""SerpApi — real Google Flights / Google Hotels / Google Search results.

Free tier is a hard 100 searches/month shared across every engine used here,
so it's the binding constraint, not freshness: every client caches far
longer than the other providers (settings.CACHE_TTL_SERPAPI), and each
engine is called at most once per plan (never once per flight/hotel/
attraction) wherever the response shape allows it.

Verified live against the real API (not assumed from docs, which don't
document the response shape in enough detail to code against blind):
- google_flights: `search_metadata.google_flights_url` is a real, resolvable
  google.com/travel/flights URL for the exact search — confirmed with a live
  request (HTTP 200 on the URL it returned). `best_flights[i].price` is
  already the round-trip total even though `flights` only lists the outbound
  legs (Google's own convention); a second call with `departure_token` would
  fetch return-leg detail, but costs another unit of the monthly quota for
  data this app doesn't display, so it's deliberately not made.
- google_hotels: `search_metadata.google_hotels_url` is a real, resolvable
  google.com/travel/search URL (confirmed HTTP 302, i.e. it resolves).
  Per-property listings have no direct booking link without a second
  property_token call (same quota tradeoff as above), so every hotel in a
  batch shares the one destination-level search URL — the same pattern
  already used for StayAPI's hotels (see integrations/stayapi.py).
- google, q="things to do in <destination>": triggers Google's own "Top
  sights" carousel, returned as `top_sights.sights` — real names, ratings,
  review counts and (for ticketed sights) real entry prices, each with its
  own `link`. Confirmed live for Cairo: 20 real landmarks (Egyptian Museum,
  Giza Necropolis, ...) with real prices ($10.61, $13.50, "Free", ...) and
  a `link` that resolves (HTTP 302). One call covers up to 20 attractions,
  so this is the primary attraction source, not a per-item lookup. Whether
  Google shows the carousel at all is its own call, not fully controlled by
  query phrasing (confirmed live: neither "<city>" alone nor "<city>,
  <country>" reliably wins — each triggers it for some cities and not
  others), so the OpenTripMap fallback matters and isn't just a formality.
- google, q="<name> <city>": `organic_results[0].link` is a real, specific
  page (usually Wikipedia) for a named place — confirmed live for "Hagia
  Sophia Istanbul". Used only to upgrade an attraction's link when it has
  none (top_sights unconfigured/empty and OpenTripMap's own wikidata/osm tag
  also missing) — the rare gap case, not a per-attraction call every time.
"""
from datetime import date

import requests
from django.conf import settings

from integrations.base import BaseClient, NotConfigured
from integrations.dataclasses import Attraction, FlightOffer, HotelOffer
from integrations.travelpayouts import resolve_iata

SEARCH_URL = "https://serpapi.com/search.json"

FLIGHT_LABELS = ["Cheapest", "Recommended", "Alternative"]


def _flight_airline(flight_legs: list[dict]) -> str:
    names = []
    for leg in flight_legs:
        name = leg.get("airline")
        if name and name not in names:
            names.append(name)
    return " + ".join(names) if names else "Airline"


def _flight_stops(item: dict) -> str:
    stops = len(item.get("layovers") or [])
    return "Direct" if stops == 0 else f"{stops} stop{'s' if stops > 1 else ''}"


def _flight_duration(item: dict) -> str:
    minutes = item.get("total_duration")
    if not minutes:
        return "Duration varies"
    return f"Approx. {minutes // 60}h {minutes % 60:02d}m outbound"


class SerpApiFlightsClient(BaseClient):
    """Real Google Flights prices — tier 2, tried when Travelpayouts has no
    cached fare for the route (see pricing.budget.build_plan).
    """

    def search_flights(
        self, origin: str, destination: str, adults: int, start_date: date | None, end_date: date | None
    ) -> list[FlightOffer] | None:
        def fetch():
            if not settings.SERPAPI_KEY:
                raise NotConfigured("SERPAPI_KEY not set")
            if not (start_date and end_date):
                raise ValueError("SerpApi flights needs both outbound and return dates")
            origin_code = resolve_iata(origin)
            destination_code = resolve_iata(destination)
            if not origin_code or not destination_code:
                raise ValueError(
                    f"could not resolve route {origin!r} -> {destination!r} to IATA codes "
                    f"(got {origin_code!r} -> {destination_code!r})"
                )

            resp = requests.get(
                SEARCH_URL,
                params={
                    "engine": "google_flights",
                    "departure_id": origin_code,
                    "arrival_id": destination_code,
                    "outbound_date": start_date.isoformat(),
                    "return_date": end_date.isoformat(),
                    "type": 1,
                    "adults": max(adults, 1),
                    "currency": "USD",
                    "api_key": settings.SERPAPI_KEY,
                },
                timeout=30,
            )
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("search_metadata", {}).get("status") != "Success":
                raise ValueError(f"SerpApi flight search did not succeed: {payload.get('search_metadata')}")

            items = (payload.get("best_flights") or []) + (payload.get("other_flights") or [])
            items = [item for item in items if item.get("price")]
            if not items:
                return None
            items.sort(key=lambda item: item["price"])

            booking_url = payload.get("search_metadata", {}).get("google_flights_url", "")
            offers = []
            for i, item in enumerate(items[:3]):
                offers.append(
                    FlightOffer(
                        airline=_flight_airline(item.get("flights") or []),
                        route=f"{origin_code} → {destination_code} → {origin_code}",
                        stops=_flight_stops(item),
                        duration=_flight_duration(item),
                        price=float(item["price"]),
                        label=FLIGHT_LABELS[i] if i < len(FLIGHT_LABELS) else "Alternative",
                        booking_url=booking_url,
                    )
                )
            return offers

        cache_key = self.make_cache_key("serpapi_flights", origin, destination, start_date, end_date, adults)
        return self.call("serpapi_flights", fetch, None, cache_key, settings.CACHE_TTL_SERPAPI)


class SerpApiHotelsClient(BaseClient):
    """Real Google Hotels prices — tier 2, tried when LiteAPI has no
    inventory for the destination (see pricing.budget.build_plan).
    """

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
            if not settings.SERPAPI_KEY:
                raise NotConfigured("SERPAPI_KEY not set")
            if not (checkin and checkout):
                raise ValueError("SerpApi hotels needs both checkin and checkout dates")
            city = destination.split(",")[0].strip() or destination

            resp = requests.get(
                SEARCH_URL,
                params={
                    "engine": "google_hotels",
                    "q": city,
                    "check_in_date": checkin.isoformat(),
                    "check_out_date": checkout.isoformat(),
                    "adults": max(adults, 1),
                    "currency": "USD",
                    "api_key": settings.SERPAPI_KEY,
                },
                timeout=30,
            )
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("search_metadata", {}).get("status") != "Success":
                raise ValueError(f"SerpApi hotel search did not succeed: {payload.get('search_metadata')}")

            booking_url = payload.get("search_metadata", {}).get("google_hotels_url", "")
            offers = []
            for prop in payload.get("properties") or []:
                # Vacation rentals skew the "hotel" comparison and often lack
                # a per-night breakdown; keep this to actual hotels.
                if prop.get("type") != "hotel":
                    continue
                nightly = (prop.get("rate_per_night") or {}).get("extracted_lowest")
                total = (prop.get("total_rate") or {}).get("extracted_lowest")
                if nightly is None or total is None:
                    continue
                stars = prop.get("extracted_hotel_class")
                rating = prop.get("overall_rating")
                offers.append(
                    HotelOffer(
                        name=prop.get("name") or city,
                        area=city,
                        rating=f"{stars}★" if stars else (f"{rating}/5" if rating else "Unrated"),
                        night=round(float(nightly), 2),
                        total=round(float(total), 2),
                        desc=prop.get("description") or f"Live rate for {city}.",
                        booking_url=booking_url,
                    )
                )

            offers.sort(key=lambda o: o.night)
            return offers[:limit] or None

        cache_key = self.make_cache_key("serpapi_hotels", destination, checkin, checkout, adults, nights, limit)
        return self.call("serpapi_hotels", fetch, None, cache_key, settings.CACHE_TTL_SERPAPI)


class SerpApiSearchClient(BaseClient):
    """Plain Google Search — used only to upgrade an attraction's link when
    OpenTripMap had neither a wikidata nor an osm tag for it (see
    integrations/activities.py and pricing/budget.py's fill_missing_booking_urls),
    never called for every attraction on every plan.
    """

    def resolve_link(self, query: str) -> str | None:
        def fetch():
            if not settings.SERPAPI_KEY:
                raise NotConfigured("SERPAPI_KEY not set")
            resp = requests.get(
                SEARCH_URL,
                params={"engine": "google", "q": query, "api_key": settings.SERPAPI_KEY},
                timeout=20,
            )
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("search_metadata", {}).get("status") != "Success":
                raise ValueError(f"SerpApi search did not succeed: {payload.get('search_metadata')}")
            results = payload.get("organic_results") or []
            if not results:
                return None
            return results[0].get("link") or None

        cache_key = self.make_cache_key("serpapi_search", query)
        return self.call("serpapi_search", fetch, None, cache_key, settings.CACHE_TTL_SERPAPI)


def _sight_desc(sight: dict) -> str:
    category = sight.get("description") or "Popular attraction"
    if category in ("Open", "Closed"):
        category = "Popular attraction"
    rating = sight.get("rating")
    if not rating:
        return f"{category} (Google)."
    reviews = sight.get("reviews")
    reviews_part = f" ({reviews:,} reviews)" if reviews else ""
    return f"{category} — {rating}★{reviews_part} (Google)."


class SerpApiAttractionsClient(BaseClient):
    """Real attractions from Google's own "Top sights" carousel — one search
    covers up to ~20 real, ranked landmarks with real ratings and (for
    ticketed sights) real entry prices, so this is the primary attraction
    source, not a per-item lookup like SerpApiSearchClient above.
    """

    def search_attractions(self, destination: str) -> list[Attraction] | None:
        def fetch():
            if not settings.SERPAPI_KEY:
                raise NotConfigured("SERPAPI_KEY not set")
            # Whether Google surfaces the "Top sights" carousel at all is its
            # own call, not something query phrasing fully controls — tested
            # live, "<city>" alone and "<city>, <country>" each trigger it
            # for some destinations and not others (e.g. "Istanbul" works
            # but "Istanbul, Türkiye" doesn't; "Cairo, Egypt" works but bare
            # "Cairo" doesn't). Neither phrasing dominates, so this uses the
            # full destination as given — when it comes back empty,
            # OpenTripMap (a real, always-available source) is the fallback.
            resp = requests.get(
                SEARCH_URL,
                params={"engine": "google", "q": f"things to do in {destination}", "api_key": settings.SERPAPI_KEY},
                timeout=20,
            )
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("search_metadata", {}).get("status") != "Success":
                raise ValueError(f"SerpApi attractions search did not succeed: {payload.get('search_metadata')}")

            sights = (payload.get("top_sights") or {}).get("sights") or []
            seen: set[str] = set()
            attractions = []
            for sight in sights:
                name = (sight.get("title") or "").strip()
                if not name or name.casefold() in seen:
                    continue
                seen.add(name.casefold())
                attractions.append(
                    Attraction(
                        name=name,
                        cost=sight.get("extracted_price") or 0.0,
                        desc=_sight_desc(sight),
                        optional=False,
                        booking_url=sight.get("link") or "",
                    )
                )
                if len(attractions) == 6:
                    break
            if not attractions:
                return None
            # Mirrors OpenTripMapClient: the top 5 are core, the 6th is a
            # nice-to-have, not part of the core budget.
            for attraction in attractions[5:]:
                attraction.optional = True
            return attractions

        cache_key = self.make_cache_key("serpapi_attractions", destination)
        return self.call("serpapi_attractions", fetch, None, cache_key, settings.CACHE_TTL_SERPAPI)
