"""Trip-request -> plan generation, shared by the synchronous optimize view
and the async build_and_persist_plan_task (see trips/tasks.py). Kept out of
views.py so tasks.py doesn't have to import a Django view module.
"""
from dataclasses import asdict

from integrations.clients import IntegrationClients
from pricing.budget import BudgetPlan, TripInputs, build_plan
from pricing.itinerary import build_itinerary
from trips.models import BudgetBreakdown, Itinerary, ItineraryActivity, ItineraryDay, TripRequest


def inputs_from_trip_request(trip_request: TripRequest) -> TripInputs:
    return TripInputs(
        departure=trip_request.departure,
        destination=trip_request.destination,
        nationality=trip_request.nationality,
        adults=trip_request.adults,
        children=trip_request.children,
        start_date=trip_request.start_date,
        end_date=trip_request.end_date,
        travel_style=trip_request.travel_style,
        hotel_preference=trip_request.hotel_preference,
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
