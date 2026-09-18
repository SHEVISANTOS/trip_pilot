"""Framework-agnostic budget engine — a direct port of calculatePlan() from
the original app.js, now server-side and unit-testable without a DB or
network access (see pricing/tests.py).
"""
from dataclasses import dataclass, field
from datetime import date

from integrations.dataclasses import Attraction, FlightOffer, HotelOffer, VisaInfo

STYLE_MULTIPLIERS = {"budget": 0.82, "balanced": 1.0, "luxury": 1.55}
CURRENCY_SYMBOLS = {"USD": "$", "TZS": "TSh ", "EUR": "€", "GBP": "£"}
DEFAULT_NIGHTS = 8


def format_money(amount: float, currency: str) -> str:
    symbol = CURRENCY_SYMBOLS.get(currency, f"{currency} ")
    rounded = round(amount)
    sign = "-" if rounded < 0 else ""
    return f"{sign}{symbol}{abs(rounded):,}"


def calculate_nights(start_date: date | None, end_date: date | None, default: int = DEFAULT_NIGHTS) -> int:
    if start_date and end_date and end_date > start_date:
        return max(1, (end_date - start_date).days)
    return default


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

    flight_cost = round(flights[0].price * mult * (people / 3))
    hotel_cost = round(hotels[0].night * nights * mult)
    transfer_cost = round(80 * mult)
    transport_cost = round(25 * nights * mult)
    food_cost = round(75 * nights * people * mult)
    attractions_cost = round(sum(a.cost for a in attractions if not a.optional) * mult)
    insurance_cost = round(100 * (people / 3))
    sim_cost = 50
    emergency_cost = round(max(200, (flight_cost + hotel_cost + food_cost) * 0.12))
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
        LineItem("SIM/eSIM", "Local connectivity package", sim_cost),
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
