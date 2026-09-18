from dataclasses import asdict
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from integrations.clients import IntegrationClients
from pricing.budget import CURRENCY_SYMBOLS, TripInputs, build_plan, format_money
from pricing.itinerary import build_itinerary
from pricing.optimizer import optimize as optimize_plan
from trips.forms import TripRequestForm
from trips.models import (
    CHECKLIST_ITEMS,
    BudgetBreakdown,
    Itinerary,
    ItineraryActivity,
    ItineraryDay,
    SavedTrip,
    TripRequest,
)


def _default_initial():
    next_year = date.today().year + 1
    return {
        "departure": "Dar es Salaam",
        "destination": "Istanbul, Türkiye",
        "nationality": "Tanzanian",
        "purpose": "Holiday",
        "start_date": date(next_year, 5, 10),
        "end_date": date(next_year, 5, 18),
        "adults": 2,
        "children": 1,
        "travel_style": "balanced",
        "hotel_preference": "3–4 Star Hotel",
        "interests": "History, food, sightseeing",
        "currency": "USD",
        "budget": 5000,
    }


def _inputs_from_trip_request(trip_request: TripRequest) -> TripInputs:
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


def _persist_plan(trip_request: TripRequest, plan):
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


@ratelimit(key="ip", rate="20/h", block=True)
def planner(request):
    if request.method == "POST":
        form = TripRequestForm(request.POST)
        if form.is_valid():
            trip_request = form.save(commit=False)
            if request.user.is_authenticated:
                trip_request.created_by = request.user
            trip_request.save()
            plan = build_plan(_inputs_from_trip_request(trip_request), IntegrationClients())
            _persist_plan(trip_request, plan)
            return redirect("trips:results", pk=trip_request.pk)
    else:
        form = TripRequestForm(initial=_default_initial())
    return render(request, "trips/planner.html", {"form": form})


def results(request, pk):
    trip_request = get_object_or_404(TripRequest, pk=pk)
    breakdown = trip_request.budget_breakdown
    itinerary = trip_request.itinerary
    is_saved = (
        request.user.is_authenticated
        and SavedTrip.objects.filter(trip_request=trip_request, user=request.user).exists()
    )
    saved_trip = None
    if is_saved:
        saved_trip = SavedTrip.objects.get(trip_request=trip_request, user=request.user)

    people, nights = breakdown.people, breakdown.nights
    scale = people / 3
    flights_display = [
        {**f, "display_price": round(f["price"] * scale)} for f in breakdown.flights
    ]
    hotels_display = []
    for i, h in enumerate(breakdown.hotels):
        total = breakdown.hotel_cost if i == 0 else round(h["total"] * (nights / 8) * scale)
        hotels_display.append({**h, "display_total": total, "per_night": round(total / nights) if nights else 0})
    attractions_display = [
        {**a, "display_cost": round(a["cost"] * scale)} for a in breakdown.attractions
    ]
    percent_used = round(float(breakdown.total) / float(trip_request.budget) * 100, 1) if trip_request.budget else 0
    per_day = round(float(breakdown.food_cost) / nights) if nights else 0

    context = {
        "transfer_leg": round(float(breakdown.transfer_cost) / 2),
        "transport_total": breakdown.transfer_cost + breakdown.transport_cost,
        "per_day_lunch": round(per_day * 0.38),
        "per_day_dinner": round(per_day * 0.52),
        "per_day_snacks": round(per_day * 0.1),
        "checklist_items": CHECKLIST_ITEMS,
        "trip_request": trip_request,
        "breakdown": breakdown,
        "itinerary": itinerary,
        "currency_symbol": CURRENCY_SYMBOLS.get(trip_request.currency, trip_request.currency),
        "is_saved": is_saved,
        "saved_trip": saved_trip,
        "flights_display": flights_display,
        "hotels_display": hotels_display,
        "attractions_display": attractions_display,
        "percent_used": percent_used,
    }
    return render(request, "trips/results.html", context)


@require_POST
def optimize(request, pk):
    trip_request = get_object_or_404(TripRequest, pk=pk)
    clients = IntegrationClients()
    plan = build_plan(_inputs_from_trip_request(trip_request), clients)
    optimized = optimize_plan(plan, clients)
    if optimized is not plan:
        trip_request.travel_style = optimized.inputs.travel_style
        trip_request.hotel_preference = optimized.inputs.hotel_preference
        trip_request.save(update_fields=["travel_style", "hotel_preference"])
        _persist_plan(trip_request, optimized)
        messages.success(request, "Plan optimized for your budget.")
    else:
        remaining_display = format_money(plan.remaining, plan.inputs.currency)
        messages.info(request, f"Your plan is already within budget with {remaining_display} remaining.")
    return redirect("trips:results", pk=pk)


@login_required
@require_POST
def save_trip(request, pk):
    trip_request = get_object_or_404(TripRequest, pk=pk)
    SavedTrip.objects.get_or_create(trip_request=trip_request, user=request.user)
    messages.success(request, "Trip saved to your dashboard.")
    return redirect("trips:results", pk=pk)


@login_required
@require_POST
def toggle_checklist(request, pk, index):
    saved_trip = get_object_or_404(SavedTrip, pk=pk, user=request.user)
    if 0 <= index < len(saved_trip.checklist):
        saved_trip.checklist[index]["done"] = not saved_trip.checklist[index]["done"]
        saved_trip.save(update_fields=["checklist"])
    return redirect("trips:results", pk=saved_trip.trip_request_id)
