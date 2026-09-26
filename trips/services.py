"""Trip-request -> plan generation, shared by the synchronous optimize view
and the async build_and_persist_plan_task (see trips/tasks.py). Kept out of
views.py so tasks.py doesn't have to import a Django view module.
"""
import logging
from dataclasses import asdict
from datetime import date, timedelta

from django.core.cache import cache
from django.utils import timezone

from integrations.clients import IntegrationClients
from integrations.countries import resolve_country
from pricing.budget import BudgetPlan, LegInput, TripInputs, build_plan, format_money
from pricing.itinerary import build_itinerary
from trips.models import BudgetBreakdown, Itinerary, ItineraryActivity, ItineraryDay, TripRequest

logger = logging.getLogger(__name__)

# The landing-page hero card. Computed from the real providers rather than
# hardcoded, but cached hard so homepage traffic doesn't re-run seven API
# calls per visit.
SHOWCASE_CACHE_KEY = "trips:showcase_plan:v1"
SHOWCASE_CACHE_TTL = 6 * 60 * 60
SHOWCASE_ROUTE = ("Dar es Salaam", "Istanbul, Türkiye")
# Chosen to sit comfortably above what this route actually costs for a family
# of three — the figures below it are all real, this is just the sample
# traveller's budget. Revisit if live fares drift past it.
SHOWCASE_BUDGET = 6000
SHOWCASE_NIGHTS = 8
SHOWCASE_LEAD_DAYS = 60


def inputs_from_trip_request(trip_request: TripRequest) -> TripInputs:
    legs = [
        LegInput(
            city=leg.city,
            arrival_date=leg.arrival_date,
            departure_date=leg.departure_date,
            hotel_preference=leg.hotel_preference,
        )
        for leg in trip_request.legs.all()
    ]
    if not legs:
        # Every trip created through the real planner form always gets at
        # least one TripLeg row before generation starts (see
        # trips.views.planner) — this only fires for a TripRequest created
        # some other way (e.g. directly via /admin/ without filling in the
        # inline). Falls back to the legacy destination/dates fields rather
        # than crashing build_plan() on an empty legs list.
        legs = [
            LegInput(
                city=trip_request.destination,
                arrival_date=trip_request.start_date,
                departure_date=trip_request.end_date,
                hotel_preference=trip_request.hotel_preference,
            )
        ]
    return TripInputs(
        departure=trip_request.departure,
        legs=legs,
        nationality=trip_request.nationality,
        adults=trip_request.adults,
        children=trip_request.children,
        travel_style=trip_request.travel_style,
        currency=trip_request.currency,
        budget=float(trip_request.budget),
    )


def persist_plan(trip_request: TripRequest, plan: BudgetPlan) -> None:
    BudgetBreakdown.objects.update_or_create(
        trip_request=trip_request,
        defaults=dict(
            items=[asdict(item) for item in plan.items],
            visa=asdict(plan.visa),
            flights=[asdict(f) for f in plan.flights],
            hotels=[asdict(h) for h in plan.hotels],
            attractions=[asdict(a) for a in plan.attractions],
            legs=[
                {
                    "city": lb.city,
                    "nights": lb.nights,
                    "arrival_date": lb.arrival_date.isoformat() if lb.arrival_date else None,
                    "departure_date": lb.departure_date.isoformat() if lb.departure_date else None,
                    "hotels": [asdict(h) for h in lb.hotels],
                    "attractions": [asdict(a) for a in lb.attractions],
                    "visa": asdict(lb.visa),
                    "sim_detail": lb.sim_detail,
                    "hotel_cost": lb.hotel_cost,
                    "attractions_cost": lb.attractions_cost,
                    "food_cost": lb.food_cost,
                    "transport_cost": lb.transport_cost,
                    "transfer_cost": lb.transfer_cost,
                    "visa_cost": lb.visa_cost,
                    "sim_cost": lb.sim_cost,
                }
                for lb in plan.legs
            ],
            nights=plan.nights,
            people=plan.people,
            flight_cost=plan.flight_cost,
            hotel_cost=plan.hotel_cost,
            transfer_cost=plan.transfer_cost,
            transport_cost=plan.transport_cost,
            food_cost=plan.food_cost,
            attractions_cost=plan.attractions_cost,
            insurance_cost=plan.insurance_cost,
            sim_cost=plan.sim_cost,
            emergency_cost=plan.emergency_cost,
            visa_cost=plan.visa_cost,
            total=plan.total,
            remaining=plan.remaining,
            over_budget=not plan.within_budget,
        ),
    )
    itinerary, _ = Itinerary.objects.get_or_create(trip_request=trip_request)
    itinerary.days.all().delete()
    for day_plan in build_itinerary(plan):
        day = ItineraryDay.objects.create(
            itinerary=itinerary, day_number=day_plan.day_number, title=day_plan.title
        )
        ItineraryActivity.objects.bulk_create(
            [
                ItineraryActivity(day=day, order=order, time=a.time, title=a.title, cost_display=a.cost_display)
                for order, a in enumerate(day_plan.activities)
            ]
        )


def generate_plan_for_trip_request(trip_request_id: int) -> None:
    """Entry point used by both build_and_persist_plan_task (async, the
    normal path once a Celery worker is running) and directly by callers
    that need the plan generated inline.
    """
    trip_request = TripRequest.objects.get(pk=trip_request_id)
    clients = IntegrationClients()
    plan = build_plan(inputs_from_trip_request(trip_request), clients)
    persist_plan(trip_request, plan)


# A trip can end up with no plan and nothing left retrying it: a worker
# restart mid-task, a dropped connection, an unhandled exception in the
# integration layer. Without this, results() would show the "building your
# plan" pending page forever with no path back to a working state.
STALLED_PLAN_RETRY_AFTER_SECONDS = 15
STALLED_PLAN_RETRY_COOLDOWN_SECONDS = 20
STALLED_PLAN_RETRY_COOLDOWN_KEY = "trips:stalled_retry_cooldown:{pk}"


def retry_stalled_plan_if_needed(trip_request: TripRequest) -> None:
    """Called from the pending-state results() view, which the client
    auto-refreshes every few seconds. If a trip has had no plan for longer
    than a normal generation should take, re-triggers generation — at most
    once per cooldown window, so a genuinely broken route can't turn every
    page refresh into a fresh burst of API calls.
    """
    age = (timezone.now() - trip_request.created_at).total_seconds()
    if age < STALLED_PLAN_RETRY_AFTER_SECONDS:
        return

    cooldown_key = STALLED_PLAN_RETRY_COOLDOWN_KEY.format(pk=trip_request.pk)
    if cache.get(cooldown_key):
        return
    cache.set(cooldown_key, True, STALLED_PLAN_RETRY_COOLDOWN_SECONDS)

    logger.warning(
        "Trip request %s has no plan after %.0fs — re-triggering generation",
        trip_request.pk,
        age,
    )
    from trips.tasks import build_and_persist_plan_task  # local import: tasks.py imports this module

    build_and_persist_plan_task.delay(trip_request.pk)


def _flag_emoji(iso2: str | None) -> str:
    """ISO-3166 alpha-2 -> regional indicator flag ("TR" -> 🇹🇷)."""
    if not iso2 or len(iso2) != 2 or not iso2.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in iso2.upper())


def get_showcase_plan() -> dict | None:
    """Figures for the landing-page hero card.

    Prefers a trip a real person actually planned (most recent one that
    finished generating). Falls back to a live-computed demo route only when
    nobody has planned anything yet, so a fresh install still shows a card.

    Returns display-ready strings (not a BudgetPlan) so the template stays
    dumb, or None if neither source works — the card is decorative and must
    never take the homepage down with it.
    """
    latest = _latest_planned_trip()
    if latest is not None:
        return latest
    return _computed_showcase()


def _latest_planned_trip() -> dict | None:
    """Most recently planned real trip. Deliberately not cached: it's a
    single indexed query, and caching would mean a trip you just planned
    doesn't show up on the homepage for minutes.

    Only trip-level figures are exposed — never who planned it.
    """
    try:
        trip = (
            TripRequest.objects.select_related("budget_breakdown")
            .filter(budget_breakdown__isnull=False)
            .order_by("-created_at")
            .first()
        )
    except Exception as exc:  # noqa: BLE001 - decorative card, never break the page
        logger.warning("Could not load the latest planned trip for the hero card: %s", exc)
        return None
    if trip is None:
        return None

    breakdown = trip.budget_breakdown
    budget = float(trip.budget or 0)
    total = float(breakdown.total)
    flights = breakdown.flights or []
    return {
        "destination": trip.destination.split(",")[0].strip() or trip.destination,
        "flag": _flag_emoji(resolve_country(trip.destination)),
        "budget": format_money(budget, trip.currency),
        "total": format_money(total, trip.currency),
        "remaining": format_money(float(breakdown.remaining), trip.currency),
        "percent_used": round(total / budget * 100, 1) if budget else 0.0,
        "within_budget": not breakdown.over_budget,
        "airline": (flights[0] or {}).get("airline", "") if flights else "",
        "nights": breakdown.nights,
    }


def _computed_showcase() -> dict | None:
    """Live-computed demo route, cached — the empty-database fallback."""
    cached = cache.get(SHOWCASE_CACHE_KEY)
    if cached is not None:
        return cached

    departure, destination = SHOWCASE_ROUTE
    start = date.today() + timedelta(days=SHOWCASE_LEAD_DAYS)
    try:
        plan = build_plan(
            TripInputs(
                departure=departure,
                legs=[
                    LegInput(
                        city=destination,
                        arrival_date=start,
                        departure_date=start + timedelta(days=SHOWCASE_NIGHTS),
                        hotel_preference="3–4 Star Hotel",
                    )
                ],
                nationality="Tanzanian",
                adults=2,
                children=1,
                travel_style="balanced",
                currency="USD",
                budget=SHOWCASE_BUDGET,
            ),
            IntegrationClients(),
        )
    except Exception as exc:  # noqa: BLE001 - decorative card, never break the page
        logger.warning("Could not build showcase plan for the hero card: %s", exc)
        return None

    currency = plan.inputs.currency
    showcase = {
        "destination": destination.split(",")[0].strip(),
        "flag": _flag_emoji(resolve_country(destination)),
        "budget": format_money(plan.inputs.budget, currency),
        "total": format_money(plan.total, currency),
        "remaining": format_money(plan.remaining, currency),
        "percent_used": plan.percent_used,
        "within_budget": plan.within_budget,
        "airline": plan.flights[0].airline if plan.flights else "",
        "nights": plan.nights,
    }
    cache.set(SHOWCASE_CACHE_KEY, showcase, SHOWCASE_CACHE_TTL)
    return showcase
