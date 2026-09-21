"""Framework-agnostic budget engine — a direct port of calculatePlan() from
the original app.js, now server-side and unit-testable without a DB or
network access (see pricing/tests.py).
"""
from dataclasses import dataclass, field, replace
from datetime import date

from integrations.countries import region_for
from integrations.dataclasses import Attraction, FlightOffer, HotelOffer, VisaInfo

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


def format_money(amount: float, currency: str) -> str:
    symbol = CURRENCY_SYMBOLS.get(currency, f"{currency} ")
    rounded = round(amount)
    sign = "-" if rounded < 0 else ""
    return f"{sign}{symbol}{abs(rounded):,}"


def calculate_nights(start_date: date | None, end_date: date | None, default: int = DEFAULT_NIGHTS) -> int:
    if start_date and end_date and end_date > start_date:
        return max(1, (end_date - start_date).days)
    return default


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
    flights = clients.amadeus.search_flights(inputs.departure, inputs.destination, people)
    hotels = clients.amadeus.search_hotels(inputs.destination, nights, people)
    attractions = clients.activities.search_attractions(inputs.destination)
    esim_bundle = clients.esim.get_bundle(inputs.destination, nights)

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

    flight_cost = round(flights[0].price * mult * (people / 3))
    hotel_cost = round(hotels[0].night * nights * mult)

    one_way_transfer = clients.maps.estimate_transfer_cost(f"{inputs.destination} Airport", hotels[0].name)
    transfer_cost = round(one_way_transfer * 2 * mult * exchange_rate)

    transport_cost = round(25 * nights * mult * exchange_rate)
    food_cost = round(75 * nights * people * mult * exchange_rate)
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
        LineItem("Local transport", "Public transport + taxi allowance", transport_cost),
        LineItem("Food", "Restaurants + daily meal allowance", food_cost),
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
