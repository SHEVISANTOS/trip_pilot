from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from accounts.forms import SignupForm
from trips.models import SavedTrip


def signup(request):
    if request.user.is_authenticated:
        return redirect("accounts:dashboard")
    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            # Explicit backend required now that allauth's is also
            # registered (AUTHENTICATION_BACKENDS has two) — this signup
            # form authenticates via plain username/password, so that's
            # the backend that actually vouches for this user, not allauth's.
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            return redirect("accounts:dashboard")
    else:
        form = SignupForm()
    return render(request, "accounts/signup.html", {"form": form})


@login_required
def dashboard(request):
    saved_trips = (
        SavedTrip.objects.filter(user=request.user)
        .select_related("trip_request", "trip_request__budget_breakdown")
    )
    return render(request, "accounts/dashboard.html", {"saved_trips": saved_trips})
