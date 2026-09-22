from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from integrations.clients import IntegrationClients
from pricing.budget import CURRENCY_SYMBOLS, build_plan, format_money
from pricing.optimizer import optimize as optimize_plan
from trips.forms import TripRequestForm
from trips.models import CHECKLIST_ITEMS, SavedTrip, TripRequest
from trips.services import get_showcase_plan, inputs_from_trip_request, persist_plan, retry_stalled_plan_if_needed
from trips.tasks import build_and_persist_plan_task


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


@ratelimit(key="ip", rate="20/h", method="POST", block=True)
def planner(request):
    if request.method == "POST":
        form = TripRequestForm(request.POST)
        if form.is_valid():
            trip_request = form.save(commit=False)
            if request.user.is_authenticated:
                trip_request.created_by = request.user
            trip_request.save()
            build_and_persist_plan_task.delay(trip_request.pk)
            return redirect("trips:results", pk=trip_request.pk)
    else:
        form = TripRequestForm(initial=_default_initial())
    return render(request, "trips/planner.html", {"form": form, "showcase": get_showcase_plan()})


def results(request, pk):
    trip_request = get_object_or_404(TripRequest, pk=pk)
    if not hasattr(trip_request, "budget_breakdown"):
        # Normally still generating (real non-eager worker) or, rarely, a
        # stalled task with nothing left retrying it — retry_stalled_plan_if_needed
        # re-triggers generation once the wait is longer than normal, so this
        # page can't get stuck refreshing forever.
        retry_stalled_plan_if_needed(trip_request)
        return render(request, "trips/pending.html", {"trip_request": trip_request})
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
    plan = build_plan(inputs_from_trip_request(trip_request), clients)
    optimized = optimize_plan(plan, clients)
    if optimized is not plan:
        trip_request.travel_style = optimized.inputs.travel_style
        trip_request.hotel_preference = optimized.inputs.hotel_preference
        trip_request.save(update_fields=["travel_style", "hotel_preference"])
        persist_plan(trip_request, optimized)
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
