"""Framework-agnostic budget engine — a direct port of calculatePlan() from
the original app.js, now server-side and unit-testable without a DB or
network access (see pricing/tests.py).
"""
from dataclasses import dataclass, field, replace
from datetime import date
from math import asin, cos, radians, sin, sqrt
from urllib.parse import quote_plus

from integrations.countries import region_for
from integrations.dataclasses import Attraction, FlightOffer, HotelOffer, VisaInfo
from integrations.fixtures import sample_data_for
from integrations.travelpayouts import airport_coordinates, resolve_iata

STYLE_MULTIPLIERS = {"budget": 0.82, "balanced": 1.0, "luxury": 1.55}
CURRENCY_SYMBOLS = {"USD": "$", "TZS": "TSh ", "EUR": "€", "GBP": "£"}
DEFAULT_NIGHTS = 8

# No eSIM provider has a usable free API (Airalo etc. are all partner-only),
# so this is a static per-region-per-day rate rather than a live quote.
# Regions roughly track real-world data pricing — Asia and Europe tend to
# have cheap, competitive eSIM plans; Africa and Oceania tend to be pricier
# due to less carrier competition. Capped at SIM_COST_CAP_DAYS since
# multi-day bundles plateau in price rather than scaling linearly forever.
REGION_SIM_DAILY_RATES = {
    "Asia": 2.0,
    "Europe": 2.5,
    "Americas": 3.5,
    "Africa": 4.0,
    "Oceania": 4.5,
    "Antarctic": 6.0,
}
DEFAULT_SIM_DAILY_RATE = 3.0
SIM_COST_CAP_DAYS = 15

# Travelpayouts retired its hotel endpoints and no other hotel API offers a
# keyless free tier, so nightly rates are estimated per region the same way
# eSIM data is. The fixture hotels supply the illustrative names/areas; these
# rates supply the money. Mid-range (3–4 star) rates in USD.
REGION_HOTEL_NIGHTLY_RATES = {
    "Asia": 70.0,
    "Africa": 85.0,
    "Europe": 120.0,
    "Americas": 125.0,
    "Oceania": 140.0,
    "Antarctic": 250.0,
}
DEFAULT_HOTEL_NIGHTLY_RATE = 100.0
# Applied to the 1st/2nd/3rd hotel shown, so the list reads as a real tier
# spread (recommended / mid / value) rather than three identical prices.
HOTEL_TIER_MULTIPLIERS = [1.0, 0.88, 0.68]
# Hotels are estimates, so they're labelled as tiers rather than dressed up
# with invented hotel names like "Tunisia Central Hotel".
HOTEL_TIERS = [
    ("Mid-range hotel, city centre", "4★", "Typical 4-star rate for this region, central location."),
    ("Comfort hotel or apartment", "4★", "Slightly outside the centre or a serviced apartment."),
    ("Budget hotel or guesthouse", "3★", "Value option — simpler rooms, still well located."),
]

# Travelpayouts only has cached fares for routes people actually search. For
# everything else, estimate from great-circle distance: a base fee plus a
# per-km rate that tapers, since long-haul fares don't scale linearly.
#
# Calibrated against real round-trip fares observed from Travelpayouts:
#   CAI->TUN  2,088 km  $455      NYC->LON  5,570 km  $441
#   DAR->IST  5,417 km  $756
# which is roughly $0.22/km short-haul falling to $0.08/km long-haul. These
# are one-way figures; estimate_flights() doubles them.
FLIGHT_BASE_FARE = 75.0
FLIGHT_PER_KM_SHORT = 0.10  # up to FLIGHT_SHORT_HAUL_KM
FLIGHT_PER_KM_LONG = 0.0225  # beyond it
FLIGHT_SHORT_HAUL_KM = 1500.0

# Food and local transport have no free pricing API (Numbeo is $260+/month;
# Teleport, which used to be free, no longer resolves). Rather than one flat
# number for every destination on earth, these scale against the hotel rate
# actually returned for this trip — LiteAPI live data when available, the
# regional estimate otherwise — as a real cost-of-living signal: expensive
# hotel markets correlate with expensive meals and transport. USD/night at
# which the base per-person rates below apply at 1.0x.
COST_OF_LIVING_BASELINE = DEFAULT_HOTEL_NIGHTLY_RATE
# Clamped so one unusually cheap/expensive named hotel can't send food or
# transport to an absurd extreme.
COST_OF_LIVING_MIN = 0.4
COST_OF_LIVING_MAX = 2.5

FOOD_USD_PER_PERSON_PER_NIGHT = 75.0
LOCAL_TRANSPORT_USD_PER_NIGHT = 25.0


def format_money(amount: float, currency: str) -> str:
    symbol = CURRENCY_SYMBOLS.get(currency, f"{currency} ")
    rounded = round(amount)
    sign = "-" if rounded < 0 else ""
    return f"{sign}{symbol}{abs(rounded):,}"


def calculate_nights(start_date: date | None, end_date: date | None, default: int = DEFAULT_NIGHTS) -> int:
    if start_date and end_date and end_date > start_date:
        return max(1, (end_date - start_date).days)
    return default


def estimate_hotels(destination: str, nights: int) -> list[HotelOffer]:
    """Fallback accommodation bands for destinations LiteAPI doesn't cover.

    These rates are estimates by region, not quotes — unlike
    integrations.hotels.LiteApiHotelClient, which returns real per-property
    prices. Kept generic on purpose: attaching an invented price to a named
    hotel would be more misleading than showing no name at all.
    """
    city = destination.split(",")[0].strip() or destination
    region = region_for(destination)
    base_rate = REGION_HOTEL_NIGHTLY_RATES.get(region, DEFAULT_HOTEL_NIGHTLY_RATE)

    hotels = []
    for i, (tier_label, rating, tier_desc) in enumerate(HOTEL_TIERS):
        nightly = round(base_rate * HOTEL_TIER_MULTIPLIERS[i], 2)
        hotels.append(
            HotelOffer(
                name=tier_label,
                area=city,
                rating=rating,
                night=nightly,
                total=round(nightly * nights, 2),
                desc=tier_desc,
            )
        )
    return hotels


def cost_of_living_index(hotels: list[HotelOffer]) -> float:
    """How expensive this destination is, relative to COST_OF_LIVING_BASELINE,
    derived from the actual hotel rates fetched for this trip rather than a
    static per-region table. Must be called on USD-denominated hotel data
    (i.e. before build_plan()'s currency conversion pass) since the baseline
    is in USD.
    """
    if not hotels:
        return 1.0
    average_night = sum(h.night for h in hotels) / len(hotels)
    index = average_night / COST_OF_LIVING_BASELINE
    return min(max(index, COST_OF_LIVING_MIN), COST_OF_LIVING_MAX)


def _search_url(query: str) -> str:
    return f"https://www.google.com/search?q={quote_plus(query)}"


def _booking_search_url(city: str, start_date: date | None, end_date: date | None) -> str:
    """Real Booking.com destination search, buildable with zero API calls
    (its `ss=` free-text field needs no resolved destination ID) — the
    fallback for any hotel without its own booking_url (LiteAPI's live
    rates and the regional-estimate tiers both have no native one).
    """
    params = f"ss={quote_plus(city)}"
    if start_date:
        params += f"&checkin={start_date.isoformat()}"
    if end_date:
        params += f"&checkout={end_date.isoformat()}"
    return f"https://www.booking.com/searchresults.html?{params}"


def fill_missing_booking_urls(
    flights: list[FlightOffer],
    hotels: list[HotelOffer],
    attractions: list[Attraction],
    city: str,
    start_date: date | None,
    end_date: date | None,
    attraction_links_client=None,
) -> tuple[list[FlightOffer], list[HotelOffer], list[Attraction]]:
    """"View / Book" must never dead-end on a fake modal — every item gets a
    real destination on the web, either the provider's own link or a
    same-purpose fallback search (never a plain "search this flight/hotel
    name" query, which is far less useful than one scoped to the right site
    and, for hotels, the right dates).

    `attraction_links_client` (SerpApiSearchClient) is only used for an
    attraction that still has no link at all — SerpApiAttractionsClient's own
    "Top sights" results already carry a real link, and OpenTripMap's
    wikidata/osm tag covers most of the rest, so this only fires for the
    rare attraction with neither: a live search call beats a generic query
    link, but it's not worth spending on an attraction that already has one.
    """
    flights = [f if f.booking_url else replace(f, booking_url=_search_url(f"{f.airline} {f.route} flights")) for f in flights]
    hotels = [h if h.booking_url else replace(h, booking_url=_booking_search_url(city, start_date, end_date)) for h in hotels]
    filled_attractions = []
    for a in attractions:
        if a.booking_url:
            filled_attractions.append(a)
            continue
        resolved = attraction_links_client.resolve_link(f"{a.name} {city}") if attraction_links_client else None
        filled_attractions.append(replace(a, booking_url=resolved or _search_url(f"{a.name} {city}")))
    return flights, hotels, filled_attractions


def haversine_km(origin: tuple[float, float], destination: tuple[float, float]) -> float:
    lat1, lon1 = (radians(v) for v in origin)
    lat2, lon2 = (radians(v) for v in destination)
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * asin(sqrt(a))


def _estimate_one_way_fare(origin_code: str, destination_code: str) -> tuple[float, float, str] | None:
    """Shared by estimate_flights() (round trip, doubles this) and
    estimate_flight_segment() (multi-city legs, one-way as-is). Returns
    (fare, distance_km, stops_guess), or None if either airport has no
    known coordinates.
    """
    origin_coords = airport_coordinates(origin_code)
    destination_coords = airport_coordinates(destination_code)
    if not origin_coords or not destination_coords:
        return None
    km = haversine_km(origin_coords, destination_coords)
    short = min(km, FLIGHT_SHORT_HAUL_KM)
    long_haul = max(0.0, km - FLIGHT_SHORT_HAUL_KM)
    fare = FLIGHT_BASE_FARE + short * FLIGHT_PER_KM_SHORT + long_haul * FLIGHT_PER_KM_LONG
    stops = "Direct" if km < 4000 else "1+ stops likely"
    return fare, km, stops


def estimate_flights(departure: str, destination: str) -> list[FlightOffer] | None:
    """Distance-based per-person round-trip estimate, for routes Travelpayouts
    has no cached fare for. Returns None if either endpoint can't be located,
    in which case callers fall back to the illustrative fixtures.
    """
    origin_code, destination_code = resolve_iata(departure), resolve_iata(destination)
    if not origin_code or not destination_code:
        return None
    result = _estimate_one_way_fare(origin_code, destination_code)
    if not result:
        return None
    one_way, km, stops = result
    round_trip = one_way * 2

    hours = km / 800  # rough cruise speed including ground time
    return [
        FlightOffer(
            airline="Estimated fare",
            route=f"{origin_code} → {destination_code} → {origin_code}",
            stops=stops,
            duration=f"Approx. {int(hours)}h {int((hours % 1) * 60):02d}m each way",
            price=round(round_trip),
            label="Estimate",
        )
    ]


def estimate_flight_segment(origin: str, destination: str) -> FlightOffer | None:
    """One-way estimate for a single hop in a multi-city chain (home->city1,
    city1->city2, ..., cityN->home). The live flight APIs (SerpApi,
    Travelpayouts) are built for round-trip queries only — decomposing one
    of their round-trip prices into a fake "outbound-only" figure would be
    fabricating a number they never actually returned, so every segment of
    a multi-city chain uses this real, already-calibrated distance estimate
    instead (same math as estimate_flights(), just not doubled).
    """
    origin_code, destination_code = resolve_iata(origin), resolve_iata(destination)
    if not origin_code or not destination_code:
        return None
    result = _estimate_one_way_fare(origin_code, destination_code)
    if not result:
        return None
    one_way, km, stops = result
    hours = km / 800
    return FlightOffer(
        airline="Estimated fare",
        route=f"{origin_code} → {destination_code}",
        stops=stops,
        duration=f"Approx. {int(hours)}h {int((hours % 1) * 60):02d}m",
        price=round(one_way),
        label="Estimate",
        booking_url=_search_url(f"{origin_code} to {destination_code} flights"),
    )


def estimate_sim_cost(destination: str, nights: int) -> tuple[float, str]:
    region = region_for(destination)
    daily_rate = REGION_SIM_DAILY_RATES.get(region, DEFAULT_SIM_DAILY_RATE)
    billed_days = max(1, min(nights, SIM_COST_CAP_DAYS))
    cost = round(daily_rate * billed_days)
    detail = f"{region or 'Standard'} data estimate — {billed_days} day{'s' if billed_days != 1 else ''} coverage"
    return cost, detail


@dataclass
class LineItem:
    label: str
    detail: str
    amount: float


@dataclass
class LegInput:
    """One stop on the itinerary — trips/services.py builds one of these per
    trips.models.TripLeg row. A single-destination trip is just a one-item
    `legs` list on TripInputs below; nothing downstream needs a separate
    code path for "simple" vs "multi-city" trips.
    """

    city: str
    arrival_date: date | None
    departure_date: date | None
    hotel_preference: str = "3–4 Star Hotel"

    @property
    def nights(self) -> int:
        return calculate_nights(self.arrival_date, self.departure_date)


@dataclass
class TripInputs:
    departure: str
    legs: list[LegInput]
    nationality: str
    adults: int
    children: int
    travel_style: str
    currency: str
    budget: float

    # destination/start_date/end_date/hotel_preference used to be plain
    # fields here; every existing caller (fill_missing_booking_urls, error
    # messages, ...) that wants "the" destination/dates for a simple,
    # one-line description reads these instead of reaching into legs[0]
    # itself, so a single-destination trip behaves exactly as it always did.
    @property
    def destination(self) -> str:
        return self.legs[0].city if self.legs else ""

    @property
    def start_date(self) -> date | None:
        return self.legs[0].arrival_date if self.legs else None

    @property
    def end_date(self) -> date | None:
        return self.legs[-1].departure_date if self.legs else None

    @property
    def is_multi_city(self) -> bool:
        return len(self.legs) > 1


@dataclass
class LegBudget:
    """Per-destination breakdown — pricing.itinerary.build_itinerary() walks
    this list to build arrival/explore/departure days per city instead of
    just one arrival-to-departure block.
    """

    city: str
    nights: int
    arrival_date: date | None
    departure_date: date | None
    visa: VisaInfo
    hotels: list[HotelOffer]
    attractions: list[Attraction]
    col_index: float
    hotel_cost: float
    transfer_cost: float
    food_cost: float
    transport_cost: float
    attractions_cost: float
    visa_cost: float
    sim_cost: float
    sim_detail: str


@dataclass
class BudgetPlan:
    inputs: TripInputs
    people: int
    nights: int
    legs: list[LegBudget]
    visa: VisaInfo
    flights: list[FlightOffer]
    hotels: list[HotelOffer]
    attractions: list[Attraction]
    items: list[LineItem]
    total: float
    remaining: float
    exchange_rate: float
    col_index: float
    flight_cost: float
    hotel_cost: float
    transfer_cost: float
    transport_cost: float
    food_cost: float
    attractions_cost: float
    insurance_cost: float
    sim_cost: float
    emergency_cost: float
    visa_cost: float

    @property
    def within_budget(self) -> bool:
        return self.remaining >= 0

    @property
    def percent_used(self) -> float:
        return round(self.total / self.inputs.budget * 100, 1) if self.inputs.budget else 0.0


def _build_leg(leg: LegInput, inputs: TripInputs, people: int, mult: float, exchange_rate: float, clients) -> LegBudget:
    """Everything build_plan() used to do for the trip's one destination,
    now done once per leg. A single-destination trip is the len(legs)==1
    case, so this is exactly the same computation that ran before —
    multi-city just runs it N times and sums the results.
    """
    nights = leg.nights
    city = leg.city.split(",")[0].strip() or leg.city

    visa = clients.visa.get_visa_info(inputs.nationality, leg.city)
    hotel_args = (leg.city, leg.arrival_date, leg.departure_date, inputs.adults, nights, inputs.nationality)
    hotels = (
        clients.hotels_serp.search_hotels(*hotel_args)
        or clients.hotels.search_hotels(*hotel_args)
        or clients.hotels_backup.search_hotels(*hotel_args)
        or estimate_hotels(leg.city, nights)
    )
    attractions = clients.activities_serp.search_attractions(leg.city) or clients.activities.search_attractions(
        leg.city
    )
    esim_bundle = clients.esim.get_bundle(leg.city, nights)

    # Must run before the currency conversion below — the baseline is USD.
    col_index = cost_of_living_index(hotels)

    visa = replace(visa, cost=round(visa.cost * exchange_rate, 2))
    hotels = [
        replace(h, night=round(h.night * exchange_rate, 2), total=round(h.total * exchange_rate, 2))
        for h in hotels
    ]
    attractions = [replace(a, cost=round(a.cost * exchange_rate, 2)) for a in attractions]
    if esim_bundle:
        esim_bundle = replace(esim_bundle, price=round(esim_bundle.price * exchange_rate, 2))

    _, hotels, attractions = fill_missing_booking_urls(
        [], hotels, attractions, city, leg.arrival_date, leg.departure_date, clients.attraction_links
    )

    hotel_cost = round(hotels[0].night * nights * mult)
    one_way_transfer = clients.maps.estimate_transfer_cost(f"{city} Airport", city)
    transfer_cost = round(one_way_transfer * 2 * mult * exchange_rate)
    transport_cost = round(LOCAL_TRANSPORT_USD_PER_NIGHT * nights * mult * col_index * exchange_rate)
    food_cost = round(FOOD_USD_PER_PERSON_PER_NIGHT * nights * people * mult * col_index * exchange_rate)
    attractions_cost = round(sum(a.cost for a in attractions if not a.optional) * mult)
    visa_cost = round(visa.cost * people)
    if esim_bundle:
        sim_cost = round(esim_bundle.price)
        data_label = "Unlimited data" if esim_bundle.unlimited else f"{esim_bundle.data_mb / 1000:g}GB"
        sim_detail = f"{data_label} eSIM, {esim_bundle.duration_days} days ({esim_bundle.name})"
    else:
        sim_cost_usd, sim_detail = estimate_sim_cost(leg.city, nights)
        sim_cost = round(sim_cost_usd * exchange_rate)

    return LegBudget(
        city=leg.city,
        nights=nights,
        arrival_date=leg.arrival_date,
        departure_date=leg.departure_date,
        visa=visa,
        hotels=hotels,
        attractions=attractions,
        col_index=col_index,
        hotel_cost=hotel_cost,
        transfer_cost=transfer_cost,
        food_cost=food_cost,
        transport_cost=transport_cost,
        attractions_cost=attractions_cost,
        visa_cost=visa_cost,
        sim_cost=sim_cost,
        sim_detail=sim_detail,
    )


def _build_flight_chain(inputs: TripInputs, people: int, mult: float, exchange_rate: float, clients) -> list[FlightOffer]:
    legs = inputs.legs
    if len(legs) == 1:
        # Unchanged from before multi-city existed: the real round-trip
        # tiers (SerpApi, Travelpayouts) both need actual outbound/return
        # dates, which only make sense for a single there-and-back trip.
        flights = (
            clients.flights_backup.search_flights(
                inputs.departure, legs[0].city, people, legs[0].arrival_date, legs[0].departure_date
            )
            or clients.flights.search_flights(
                inputs.departure, legs[0].city, people, legs[0].arrival_date, legs[0].departure_date
            )
            or estimate_flights(inputs.departure, legs[0].city)
            or sample_data_for(legs[0].city).flights
        )
        return [replace(f, price=round(f.price * exchange_rate, 2)) for f in flights]

    # Multi-city: a chain of one-way hops (home -> leg 1 -> leg 2 -> ... ->
    # home). See estimate_flight_segment()'s docstring for why every hop
    # uses the same real, calibrated distance estimate rather than mixing
    # in a live round-trip price that can't honestly be split into one leg.
    stops = [inputs.departure] + [leg.city for leg in legs] + [inputs.departure]
    segments = []
    for origin, destination in zip(stops, stops[1:]):
        segment = estimate_flight_segment(origin, destination)
        if segment:
            segments.append(replace(segment, price=round(segment.price * exchange_rate, 2)))
    return segments or sample_data_for(legs[0].city).flights


def build_plan(inputs: TripInputs, clients) -> BudgetPlan:
    people = inputs.adults + inputs.children
    mult = STYLE_MULTIPLIERS.get(inputs.travel_style, 1.0)
    exchange_rate = clients.exchange.get_rate("USD", inputs.currency).rate

    leg_budgets = [_build_leg(leg, inputs, people, mult, exchange_rate, clients) for leg in inputs.legs]
    flights = _build_flight_chain(inputs, people, mult, exchange_rate, clients)
    flights = [f if f.booking_url else replace(f, booking_url=_search_url(f"{f.airline} {f.route} flights")) for f in flights]

    nights = sum(lb.nights for lb in leg_budgets)
    hotels = [h for lb in leg_budgets for h in lb.hotels]
    attractions = [a for lb in leg_budgets for a in lb.attractions]
    col_index = sum(lb.col_index for lb in leg_budgets) / len(leg_budgets)

    # Flight prices are per person (Travelpayouts quotes that way, the
    # fixtures are normalised to match, and a chained estimate is built
    # per-person from the start) — this scales by party size. For a chain,
    # `flights` holds every hop, so the cost is the whole chain, not [0].
    flight_cost = round(sum(f.price for f in flights) * mult * people) if inputs.is_multi_city else round(
        flights[0].price * mult * people
    )
    hotel_cost = round(sum(lb.hotel_cost for lb in leg_budgets))
    transfer_cost = round(sum(lb.transfer_cost for lb in leg_budgets))
    transport_cost = round(sum(lb.transport_cost for lb in leg_budgets))
    food_cost = round(sum(lb.food_cost for lb in leg_budgets))
    attractions_cost = round(sum(lb.attractions_cost for lb in leg_budgets))
    visa_cost = round(sum(lb.visa_cost for lb in leg_budgets))
    sim_cost = round(sum(lb.sim_cost for lb in leg_budgets))
    insurance_cost = round(100 * (people / 3) * exchange_rate)
    emergency_cost = round(max(200 * exchange_rate, (flight_cost + hotel_cost + food_cost) * 0.12))

    # Primary/first leg's visa and eSIM stand in for the single-item detail
    # text on the summary panel — visa_cost/sim_cost above already sum every
    # leg's own country/bundle, so the *total* is accurate even though the
    # panel names only leg 1's requirements for now.
    visa = leg_budgets[0].visa
    sim_detail = leg_budgets[0].sim_detail
    if inputs.is_multi_city:
        route = " → ".join([inputs.departure] + [leg.city for leg in inputs.legs] + [inputs.departure])
        flight_detail = f"{len(flights)}-flight route — {route}"
        hotel_detail = f"{len(leg_budgets)} destinations — {nights} nights total"
        sim_detail = f"{sim_detail} (+ {len(leg_budgets) - 1} more destination{'s' if len(leg_budgets) > 2 else ''})"
    else:
        flight_detail = f"{flights[0].airline} — {flights[0].route}"
        hotel_detail = f"{leg_budgets[0].hotels[0].name} — {nights} nights"

    items = [
        LineItem("Visa", visa.type, visa_cost),
        LineItem("Flights", flight_detail, flight_cost),
        LineItem("Hotel", hotel_detail, hotel_cost),
        LineItem("Airport transfers", "Airport → Hotel → Airport, per destination", transfer_cost),
        LineItem("Local transport", f"Public transport + taxi allowance ({col_index:.1f}x cost-of-living)", transport_cost),
        LineItem("Food", f"Restaurants + daily meal allowance ({col_index:.1f}x cost-of-living)", food_cost),
        LineItem("Attractions", "Named attractions and activities", attractions_cost),
        LineItem("Travel insurance", "Estimated travel cover", insurance_cost),
        LineItem("SIM/eSIM", sim_detail, sim_cost),
        LineItem("Emergency reserve", "Unallocated contingency fund", emergency_cost),
    ]
    total = sum(i.amount for i in items)
    remaining = inputs.budget - total

    return BudgetPlan(
        inputs=inputs,
        people=people,
        nights=nights,
        legs=leg_budgets,
        visa=visa,
        flights=flights,
        hotels=hotels,
        attractions=attractions,
        items=items,
        total=total,
        remaining=remaining,
        exchange_rate=exchange_rate,
        col_index=col_index,
        flight_cost=flight_cost,
        hotel_cost=hotel_cost,
        transfer_cost=transfer_cost,
        transport_cost=transport_cost,
        food_cost=food_cost,
        attractions_cost=attractions_cost,
        insurance_cost=insurance_cost,
        sim_cost=sim_cost,
        emergency_cost=emergency_cost,
        visa_cost=visa_cost,
    )
