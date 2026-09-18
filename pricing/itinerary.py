"""Automated day-by-day itinerary builder — port of the itinerary-building
loop from app.js's render(). Framework-agnostic: trips/views.py persists the
result into Itinerary/ItineraryDay/ItineraryActivity rows.
"""
from dataclasses import dataclass

from pricing.budget import BudgetPlan, format_money


@dataclass
class ActivityPlan:
    time: str
    title: str
    cost_display: str


@dataclass
class DayPlan:
    day_number: int
    title: str
    activities: list[ActivityPlan]


def build_itinerary(plan: BudgetPlan) -> list[DayPlan]:
    currency = plan.inputs.currency
    people = plan.people
    nights = plan.nights
    hotel_name = plan.hotels[0].name
    per_day = round(plan.food_cost / nights) if nights else 0
    transfer_leg = plan.transfer_cost / 2

    headline_attractions = plan.attractions[: min(5, nights)] or plan.attractions[:1]
    attraction_stops = [
        (a.name, round(a.cost * (people / 3))) for a in headline_attractions
    ] or [("Free time to explore", 0)]

    days = [
        DayPlan(
            day_number=1,
            title="Day 1 — Arrival",
            activities=[
                ActivityPlan("15:20", "Arrive at destination", "Flight arrival"),
                ActivityPlan("16:30", f"Transfer to {hotel_name}", format_money(transfer_leg, currency)),
                ActivityPlan("19:00", "Welcome dinner", format_money(round(per_day * 0.55), currency)),
            ],
        )
    ]

    for i in range(1, nights):
        name, cost = attraction_stops[(i - 1) % len(attraction_stops)]
        days.append(
            DayPlan(
                day_number=i + 1,
                title=f"Day {i + 1} — Explore",
                activities=[
                    ActivityPlan("09:00", "Breakfast / depart hotel", "Included"),
                    ActivityPlan("10:00", name, format_money(cost, currency)),
                    ActivityPlan("13:00", "Lunch", format_money(round(per_day * 0.38), currency)),
                    ActivityPlan("18:30", "Dinner / free time", format_money(round(per_day * 0.52), currency)),
                ],
            )
        )

    days.append(
        DayPlan(
            day_number=nights + 1,
            title=f"Day {nights + 1} — Departure",
            activities=[
                ActivityPlan("08:00", "Breakfast and check-out", "Included/varies"),
                ActivityPlan("10:00", "Transfer to airport", format_money(transfer_leg, currency)),
                ActivityPlan("Departure", "Return flight", "Included in flight budget"),
            ],
        )
    )
    return days
