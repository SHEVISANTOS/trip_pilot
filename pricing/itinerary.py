"""Automated day-by-day itinerary builder — port of the itinerary-building
loop from app.js's render(). Framework-agnostic: trips/views.py persists the
result into Itinerary/ItineraryDay/ItineraryActivity rows.

Walks plan.legs so a multi-city trip gets an arrival day for the first
destination, explore days per destination, a combined travel/arrival day
between each pair of consecutive destinations, and a departure day from the
last one — a single-destination trip is just the len(legs)==1 case of the
same loop, and produces byte-identical day numbering/titles to before
multi-city existed.

Day/night accounting: every leg contributes exactly `leg.nights` days (1
arrival-or-travel day + `nights - 1` explore days) except the last leg,
which gets one additional departure day with no night attached — matching
the original single-leg formula (nights -> nights + 1 days) exactly when
there's only one leg, and summing correctly across legs otherwise
(total days = total nights + 1, never double- or under-counting the night
spent in transit between two cities).
"""
from dataclasses import dataclass

from pricing.budget import BudgetPlan, LegBudget, format_money


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


def _leg_attraction_stops(leg: LegBudget, people: int) -> list[tuple[str, int]]:
    headline = leg.attractions[: min(5, leg.nights)] or leg.attractions[:1]
    stops = [(a.name, round(a.cost * (people / 3))) for a in headline]
    return stops or [("Free time to explore", 0)]


def build_itinerary(plan: BudgetPlan) -> list[DayPlan]:
    currency = plan.inputs.currency
    people = plan.people
    days: list[DayPlan] = []
    day_number = 1

    for leg_index, leg in enumerate(plan.legs):
        is_first_leg = leg_index == 0
        is_last_leg = leg_index == len(plan.legs) - 1
        hotel_name = leg.hotels[0].name
        per_day = round(leg.food_cost / leg.nights) if leg.nights else 0
        transfer_leg = leg.transfer_cost / 2
        stops = _leg_attraction_stops(leg, people)

        if is_first_leg:
            days.append(
                DayPlan(
                    day_number=day_number,
                    title=f"Day {day_number} — Arrival" if not plan.inputs.is_multi_city else f"Day {day_number} — Arrival in {leg.city}",
                    activities=[
                        ActivityPlan("15:20", "Arrive at destination", "Flight arrival"),
                        ActivityPlan("16:30", f"Transfer to {hotel_name}", format_money(transfer_leg, currency)),
                        ActivityPlan("19:00", "Welcome dinner", format_money(round(per_day * 0.55), currency)),
                    ],
                )
            )
        else:
            prev_leg = plan.legs[leg_index - 1]
            days.append(
                DayPlan(
                    day_number=day_number,
                    title=f"Day {day_number} — Travel to {leg.city}",
                    activities=[
                        ActivityPlan("10:00", "Transfer to airport", format_money(prev_leg.transfer_cost / 2, currency)),
                        ActivityPlan("Flight", f"{prev_leg.city} → {leg.city}", "Included in flight budget"),
                        ActivityPlan("18:00", f"Transfer to {hotel_name}", format_money(transfer_leg, currency)),
                    ],
                )
            )
        day_number += 1

        explore_nights = leg.nights - 1
        for i in range(explore_nights):
            name, cost = stops[i % len(stops)]
            days.append(
                DayPlan(
                    day_number=day_number,
                    title=f"Day {day_number} — Explore" if not plan.inputs.is_multi_city else f"Day {day_number} — Explore {leg.city}",
                    activities=[
                        ActivityPlan("09:00", "Breakfast / depart hotel", "Included"),
                        ActivityPlan("10:00", name, format_money(cost, currency)),
                        ActivityPlan("13:00", "Lunch", format_money(round(per_day * 0.38), currency)),
                        ActivityPlan("18:30", "Dinner / free time", format_money(round(per_day * 0.52), currency)),
                    ],
                )
            )
            day_number += 1

        if is_last_leg:
            days.append(
                DayPlan(
                    day_number=day_number,
                    title=f"Day {day_number} — Departure" if not plan.inputs.is_multi_city else f"Day {day_number} — Departure from {leg.city}",
                    activities=[
                        ActivityPlan("08:00", "Breakfast and check-out", "Included/varies"),
                        ActivityPlan("10:00", "Transfer to airport", format_money(transfer_leg, currency)),
                        ActivityPlan("Departure", "Return flight", "Included in flight budget"),
                    ],
                )
            )
            day_number += 1

    return days
