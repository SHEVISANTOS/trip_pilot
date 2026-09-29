from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordResetView
from django.shortcuts import redirect, render

from accounts.forms import SignupForm
from trips.models import SavedTrip


class RequestDomainPasswordResetView(PasswordResetView):
    """Django's PasswordResetForm.save() defaults to the domain stored on
    django.contrib.sites' Site object (added for allauth's benefit) when no
    domain_override is given — that object's factory default is
    "example.com", which is exactly what leaked into a real reset email
    (caught live: the link pointed at https://example.com/...). Passing the
    actual request's host instead is correct in every environment (local
    dev, every Vercel preview URL, a future custom domain) with nothing to
    keep in sync, unlike updating the Site row would be.
    """

    def form_valid(self, form):
        form.save(
            use_https=self.request.is_secure(),
            token_generator=self.token_generator,
            from_email=self.from_email,
            email_template_name=self.email_template_name,
            subject_template_name=self.subject_template_name,
            request=self.request,
            html_email_template_name=self.html_email_template_name,
            extra_email_context=self.extra_email_context,
            domain_override=self.request.get_host(),
        )
        # Skip PasswordResetView.form_valid() in the MRO — it would call
        # form.save() a second time (and send a second email) before
        # redirecting. FormMixin.form_valid() is just the redirect.
        return super(PasswordResetView, self).form_valid(form)


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
