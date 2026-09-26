from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from integrations.clients import IntegrationClients
from pricing.budget import CURRENCY_SYMBOLS, build_plan, format_money
from pricing.optimizer import optimize as optimize_plan
from trips.forms import LegFormSet, TripRequestForm
from trips.models import CHECKLIST_ITEMS, SavedTrip, TripLeg, TripRequest
from trips.services import get_showcase_plan, inputs_from_trip_request, persist_plan, retry_stalled_plan_if_needed
from trips.tasks import build_and_persist_plan_task


@ratelimit(key="ip", rate="20/h", method="POST", block=True)
def planner(request):
    if request.method == "POST":
        form = TripRequestForm(request.POST)
        leg_formset = LegFormSet(request.POST, prefix="legs")
        if form.is_valid() and leg_formset.is_valid():
            legs_data = [
                f.cleaned_data for f in leg_formset.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")
            ]
            trip_request = form.save(commit=False)
            if request.user.is_authenticated:
                trip_request.created_by = request.user
            # destination/start_date/end_date/hotel_preference stay in sync
            # with leg 0 — every template, the PDF export, dashboard list
            # etc. that read them directly as a plain destination/date pair
            # keep working for the (still by far most common) single-city
            # case without needing to know legs exist at all.
            first_leg, last_leg = legs_data[0], legs_data[-1]
            trip_request.destination = first_leg["city"]
            trip_request.start_date = first_leg["arrival_date"]
            trip_request.end_date = last_leg["departure_date"]
            trip_request.hotel_preference = first_leg["hotel_preference"]
            trip_request.save()
            TripLeg.objects.bulk_create(
                [
                    TripLeg(
                        trip_request=trip_request,
                        order=i,
                        city=leg["city"],
                        arrival_date=leg["arrival_date"],
                        departure_date=leg["departure_date"],
                        hotel_preference=leg["hotel_preference"],
                    )
                    for i, leg in enumerate(legs_data)
                ]
            )
            build_and_persist_plan_task.delay(trip_request.pk)
            return redirect("trips:results", pk=trip_request.pk)
    else:
        form = TripRequestForm()
        leg_formset = LegFormSet(prefix="legs")
    return render(
        request, "trips/planner.html", {"form": form, "leg_formset": leg_formset, "showcase": get_showcase_plan()}
    )


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
    is_multi_city = len(breakdown.legs) > 1
    flights_display = [
        {**f, "display_price": round(f["price"] * scale)} for f in breakdown.flights
    ]
    if is_multi_city:
        # breakdown.hotels/attractions are every leg's items concatenated —
        # the single-city logic below (one "selected" hotel absorbing the
        # whole trip's hotel_cost, everything else a scaled alternative)
        # doesn't apply once there's more than one destination's worth of
        # hotels in that list. Each leg's own already-correct hotel_cost/
        # attractions_cost (see pricing.budget._build_leg) is used instead.
        hotels_display = []
        attractions_display = []
        for leg in breakdown.legs:
            leg_nights = leg["nights"] or 1
            for i, h in enumerate(leg["hotels"]):
                total = leg["hotel_cost"] if i == 0 else round(h["total"] * scale)
                hotels_display.append(
                    {
                        **h,
                        "display_total": total,
                        "per_night": round(total / leg_nights),
                        "leg_city": leg["city"],
                    }
                )
            attractions_display.extend(
                {**a, "display_cost": round(a["cost"] * scale), "leg_city": leg["city"]} for a in leg["attractions"]
            )
    else:
        hotels_display = []
        for i, h in enumerate(breakdown.hotels):
            total = breakdown.hotel_cost if i == 0 else round(h["total"] * (nights / 8) * scale)
            hotels_display.append({**h, "display_total": total, "per_night": round(total / nights) if nights else 0})
        attractions_display = [
            {**a, "display_cost": round(a["cost"] * scale)} for a in breakdown.attractions
        ]
    percent_used = round(float(breakdown.total) / float(trip_request.budget) * 100, 1) if trip_request.budget else 0
    per_day = round(float(breakdown.food_cost) / nights) if nights else 0
    legs_with_total = [
        {
            **leg,
            "total": leg["hotel_cost"]
            + leg["attractions_cost"]
            + leg["food_cost"]
            + leg["transport_cost"]
            + leg["transfer_cost"]
            + leg["visa_cost"]
            + leg["sim_cost"],
        }
        for leg in breakdown.legs
    ]

    context = {
        "is_multi_city": is_multi_city,
        "legs": legs_with_total,
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
        trip_request.hotel_preference = optimized.inputs.legs[0].hotel_preference
        trip_request.save(update_fields=["travel_style", "hotel_preference"])
        for leg, leg_input in zip(trip_request.legs.all(), optimized.inputs.legs):
            leg.hotel_preference = leg_input.hotel_preference
            leg.save(update_fields=["hotel_preference"])
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
