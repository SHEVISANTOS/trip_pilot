from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.shortcuts import redirect, render

from trips.models import SavedTrip


def signup(request):
    if request.user.is_authenticated:
        return redirect("accounts:dashboard")
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect("accounts:dashboard")
    else:
        form = UserCreationForm()
    return render(request, "accounts/signup.html", {"form": form})


@login_required
def dashboard(request):
    saved_trips = (
        SavedTrip.objects.filter(user=request.user)
        .select_related("trip_request", "trip_request__budget_breakdown")
    )
    return render(request, "accounts/dashboard.html", {"saved_trips": saved_trips})
