"""Framework-agnostic budget engine — a direct port of calculatePlan() from
the original app.js, now server-side and unit-testable without a DB or
network access (see pricing/tests.py).
"""
from dataclasses import dataclass, field, replace
from datetime import date
from math import asin, cos, radians, sin, sqrt

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


def haversine_km(origin: tuple[float, float], destination: tuple[float, float]) -> float:
    lat1, lon1 = (radians(v) for v in origin)
    lat2, lon2 = (radians(v) for v in destination)
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * asin(sqrt(a))


def estimate_flights(departure: str, destination: str) -> list[FlightOffer] | None:
    """Distance-based per-person round-trip estimate, for routes Travelpayouts
    has no cached fare for. Returns None if either endpoint can't be located,
    in which case callers fall back to the illustrative fixtures.
    """
    origin_code, destination_code = resolve_iata(departure), resolve_iata(destination)
    if not origin_code or not destination_code:
        return None
    origin_coords = airport_coordinates(origin_code)
    destination_coords = airport_coordinates(destination_code)
    if not origin_coords or not destination_coords:
        return None

    km = haversine_km(origin_coords, destination_coords)
    short = min(km, FLIGHT_SHORT_HAUL_KM)
    long_haul = max(0.0, km - FLIGHT_SHORT_HAUL_KM)
    one_way = FLIGHT_BASE_FARE + short * FLIGHT_PER_KM_SHORT + long_haul * FLIGHT_PER_KM_LONG
    round_trip = one_way * 2

    hours = km / 800  # rough cruise speed including ground time
    stops = "Direct" if km < 4000 else "1+ stops likely"
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
class TripInputs:
    departure: str
    destination: str
    nationality: str
    adults: int
    children: int
    start_date: date | None
    end_date: date | None
    travel_style: str
    hotel_preference: str
    currency: str
    budget: float


@dataclass
class BudgetPlan:
    inputs: TripInputs
    people: int
    nights: int
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


def build_plan(inputs: TripInputs, clients) -> BudgetPlan:
    people = inputs.adults + inputs.children
    nights = calculate_nights(inputs.start_date, inputs.end_date)
    mult = STYLE_MULTIPLIERS.get(inputs.travel_style, 1.0)

    visa = clients.visa.get_visa_info(inputs.nationality, inputs.destination)
    # Live cached fares where they exist, a distance-based estimate for the
    # many routes Travelpayouts has no cached price for, and the illustrative
    # fixtures only if the route can't be located at all.
    flights = (
        clients.flights.search_flights(
            inputs.departure, inputs.destination, people, inputs.start_date, inputs.end_date
        )
        or estimate_flights(inputs.departure, inputs.destination)
        or sample_data_for(inputs.destination).flights
    )
    city = inputs.destination.split(",")[0].strip() or inputs.destination
    # Live per-property rates where LiteAPI has inventory; the regional
    # estimate only covers destinations it doesn't reach.
    hotels = clients.hotels.search_hotels(
        inputs.destination, inputs.start_date, inputs.end_date, inputs.adults, nights, inputs.nationality
    ) or estimate_hotels(inputs.destination, nights)
    attractions = clients.activities.search_attractions(inputs.destination)
    esim_bundle = clients.esim.get_bundle(inputs.destination, nights)

    # Real cost-of-living signal for this specific destination, from the
    # actual (USD) hotel rates just fetched — must run before the currency
    # conversion below, since COST_OF_LIVING_BASELINE is in USD.
    col_index = cost_of_living_index(hotels)

    # Every provider above prices in USD. Convert once, here, so every cost
    # computed below — and every raw flight/hotel/attraction price stored on
    # the plan for display — is already in the traveller's chosen currency;
    # nothing downstream needs to know a conversion happened.
    exchange_rate = clients.exchange.get_rate("USD", inputs.currency).rate
    visa = replace(visa, cost=round(visa.cost * exchange_rate, 2))
    flights = [replace(f, price=round(f.price * exchange_rate, 2)) for f in flights]
    hotels = [
        replace(h, night=round(h.night * exchange_rate, 2), total=round(h.total * exchange_rate, 2))
        for h in hotels
    ]
    attractions = [replace(a, cost=round(a.cost * exchange_rate, 2)) for a in attractions]
    if esim_bundle:
        esim_bundle = replace(esim_bundle, price=round(esim_bundle.price * exchange_rate, 2))

    # Flight prices are per person (Travelpayouts quotes that way, and the
    # fixtures are normalised to match), so this scales by party size.
    flight_cost = round(flights[0].price * mult * people)
    hotel_cost = round(hotels[0].night * nights * mult)

    # Geocoders need "Istanbul Airport", not "Istanbul, Türkiye Airport", and
    # the transfer allowance covers airport -> city centre generally, so this
    # routes to the centre rather than to one specific hotel.
    one_way_transfer = clients.maps.estimate_transfer_cost(f"{city} Airport", city)
    transfer_cost = round(one_way_transfer * 2 * mult * exchange_rate)

    transport_cost = round(LOCAL_TRANSPORT_USD_PER_NIGHT * nights * mult * col_index * exchange_rate)
    food_cost = round(FOOD_USD_PER_PERSON_PER_NIGHT * nights * people * mult * col_index * exchange_rate)
    attractions_cost = round(sum(a.cost for a in attractions if not a.optional) * mult)
    insurance_cost = round(100 * (people / 3) * exchange_rate)
    if esim_bundle:
        sim_cost = round(esim_bundle.price)
        data_label = "Unlimited data" if esim_bundle.unlimited else f"{esim_bundle.data_mb / 1000:g}GB"
        sim_detail = f"{data_label} eSIM, {esim_bundle.duration_days} days ({esim_bundle.name})"
    else:
        sim_cost_usd, sim_detail = estimate_sim_cost(inputs.destination, nights)
        sim_cost = round(sim_cost_usd * exchange_rate)
    emergency_cost = round(max(200 * exchange_rate, (flight_cost + hotel_cost + food_cost) * 0.12))
    visa_cost = round(visa.cost * people)

    items = [
        LineItem("Visa", visa.type, visa_cost),
        LineItem("Flights", f"{flights[0].airline} — {flights[0].route}", flight_cost),
        LineItem("Hotel", f"{hotels[0].name} — {nights} nights", hotel_cost),
        LineItem("Airport transfers", "Airport → Hotel → Airport", transfer_cost),
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
