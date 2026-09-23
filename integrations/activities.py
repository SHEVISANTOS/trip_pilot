"""Attractions from OpenTripMap (free, no partner approval).

OpenTripMap has no ticket prices, so entry costs are estimated from the
place's category — a museum charges, a memorial doesn't. That's a far better
signal than the previous behaviour, which recycled the Istanbul demo prices
positionally onto whatever came back (and so billed a roadside memorial tree
at $180).
"""
import requests
from django.conf import settings

from integrations.base import BaseClient, NotConfigured
from integrations.dataclasses import Attraction
from integrations.fixtures import sample_data_for

GEONAME_URL = "https://api.opentripmap.com/0.1/en/places/geoname"
RADIUS_URL = "https://api.opentripmap.com/0.1/en/places/radius"

# Categories worth putting in front of a traveller. Deliberately excludes the
# shop/installation kinds that `interesting_places` alone drags in.
WANTED_KINDS = "museums,historic,monuments_and_memorials,natural,architecture,cultural,religion"

# OpenTripMap scores notability 1-3 (plus 7 for UNESCO sites). Anything at 0
# is typically an unnamed map object, so this is the floor for inclusion.
MIN_RATE = 2

# Ceiling per primary category, so one over-represented kind (usually
# places of worship) can't fill the whole list.
MAX_PER_CATEGORY = 2

# Tagged under wanted kinds but not somewhere you'd visit: OSM files railway
# stations under architecture, and banks/offices under historic buildings.
EXCLUDED_KINDS = (
    "railway_stations",
    "transport",
    "bank",
    "office",
    "shop",
    "installation",
)

# Typical entry cost in USD by category, most specific first. These are
# category estimates, never real ticket prices — anything not listed is
# treated as free to visit, which is true of most monuments, places of
# worship, viewpoints and public squares.
CATEGORY_COSTS = (
    ("national_park", 25.0),
    ("nature_reserve", 20.0),
    ("aquariums", 18.0),
    ("zoos", 18.0),
    ("palaces", 15.0),
    ("theatres_and_entertainments", 15.0),
    ("museums", 12.0),
    ("castles", 12.0),
    ("art_galleries", 10.0),
    ("towers", 8.0),
    ("fortifications", 8.0),
    ("gardens_and_parks", 5.0),
)


def estimate_entry_cost(kinds: str) -> float:
    for keyword, cost in CATEGORY_COSTS:
        if keyword in kinds:
            return cost
    return 0.0


def describe(kinds: str) -> str:
    primary = (kinds.split(",") or ["attraction"])[0].replace("_", " ")
    return f"{primary.capitalize()} in the area (OpenTripMap)."


def _place_url(props: dict) -> str:
    """A real, always-resolvable link for this place — Wikidata (richest,
    has a description and usually a Wikipedia link out) when tagged, OSM's
    own page (shows the exact location) otherwise. No extra API call either
    way: both fields already come back on the radius search.
    """
    wikidata = props.get("wikidata")
    if wikidata:
        return f"https://www.wikidata.org/wiki/{wikidata}"
    osm = props.get("osm")
    if osm:
        return f"https://www.openstreetmap.org/{osm}"
    return ""


class OpenTripMapClient(BaseClient):
    def search_attractions(self, destination: str) -> list[Attraction]:
        def fetch():
            if not settings.OPENTRIPMAP_API_KEY:
                raise NotConfigured("OPENTRIPMAP_API_KEY not set")
            geo = requests.get(
                GEONAME_URL,
                params={"name": destination, "apikey": settings.OPENTRIPMAP_API_KEY},
                timeout=10,
            )
            geo.raise_for_status()
            location = geo.json()

            places = requests.get(
                RADIUS_URL,
                params={
                    "radius": 20000,
                    "lon": location["lon"],
                    "lat": location["lat"],
                    "kinds": WANTED_KINDS,
                    "rate": MIN_RATE,
                    # The API truncates by proximity, not notability, so a
                    # small limit returns whatever happens to sit nearest the
                    # centre. Pull a wide pool and rank it here instead.
                    "limit": 300,
                    "apikey": settings.OPENTRIPMAP_API_KEY,
                },
                timeout=30,
            )
            places.raise_for_status()

            seen: set[str] = set()
            scored = []
            for feature in places.json().get("features", []):
                props = feature.get("properties") or {}
                name = (props.get("name") or "").strip()
                # Unnamed map objects used to surface as "<city> point of
                # interest"; they're not somewhere you can go, so drop them.
                if not name or name.casefold() in seen:
                    continue
                kinds = props.get("kinds") or ""
                if any(excluded in kinds for excluded in EXCLUDED_KINDS):
                    continue
                seen.add(name.casefold())
                url = _place_url(props)
                scored.append((float(props.get("rate") or 0), kinds, name, url))

            if not scored:
                return None
            # Most notable first, so the itinerary builder picks the
            # landmarks rather than whatever the API happened to list first.
            scored.sort(key=lambda row: row[0], reverse=True)

            # OSM tags far more places of worship than anything else, so
            # without a cap a city's list comes back as five mosques or five
            # churches. Limit each category so the day plan has variety.
            per_category: dict[str, int] = {}
            attractions = []
            for _, kinds, name, url in scored:
                category = (kinds.split(",") or ["other"])[0]
                if per_category.get(category, 0) >= MAX_PER_CATEGORY:
                    continue
                per_category[category] = per_category.get(category, 0) + 1
                attractions.append(
                    Attraction(
                        name=name,
                        cost=estimate_entry_cost(kinds),
                        desc=describe(kinds),
                        optional=False,
                        booking_url=url,
                    )
                )
                if len(attractions) == 6:
                    break
            # Anything past the first five is a nice-to-have, not part of the
            # core budget (mirrors how the fixtures mark a premium excursion).
            for attraction in attractions[5:]:
                attraction.optional = True
            return attractions

        fallback = sample_data_for(destination).attractions
        cache_key = self.make_cache_key("opentripmap", destination)
        result = self.call("opentripmap", fetch, None, cache_key, settings.CACHE_TTL_ATTRACTIONS)
        return result or fallback
