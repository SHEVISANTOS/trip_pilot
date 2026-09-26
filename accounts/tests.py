import re
from datetime import date, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from accounts.models import Profile
from trips.models import BudgetBreakdown, SavedTrip, TripRequest

User = get_user_model()


def create_planned_trip_request(**overrides):
    """Builds a TripRequest + minimal BudgetBreakdown directly via the ORM —
    the accounts app only cares about listing/linking saved trips, not
    re-deriving plan numbers (already covered in pricing/trips tests), so
    this skips the real (network-backed) plan generation entirely.
    """
    defaults = dict(
        departure="Dar es Salaam",
        destination="Istanbul, Türkiye",
        nationality="Tanzanian",
        start_date=date.today() + timedelta(days=200),
        end_date=date.today() + timedelta(days=208),
        adults=2,
        children=1,
        budget=5000,
    )
    defaults.update(overrides)
    trip_request = TripRequest.objects.create(**defaults)
    BudgetBreakdown.objects.create(
        trip_request=trip_request,
        items=[{"label": "Flights", "detail": "Test Air", "amount": 1000}],
        visa={"type": "Tourist entry requirement", "method": "Verify.", "stay": "30 days", "cost": 60, "tag": "VERIFY"},
        flights=[],
        hotels=[{"name": "Test Hotel", "area": "Centre", "rating": "4★", "night": 100, "total": 800, "desc": ""}],
        attractions=[],
        nights=8,
        people=3,
        flight_cost=1000,
        hotel_cost=800,
        transfer_cost=80,
        transport_cost=200,
        food_cost=1800,
        attractions_cost=150,
        insurance_cost=100,
        sim_cost=20,
        emergency_cost=500,
        visa_cost=180,
        total=4830,
        remaining=170,
        over_budget=False,
    )
    return trip_request


class ProfileSignalTests(TestCase):
    def test_creating_a_user_auto_creates_a_profile(self):
        user = User.objects.create_user(username="newuser", password="pw12345!")
        self.assertTrue(Profile.objects.filter(user=user).exists())
        self.assertEqual(user.profile.preferred_currency, "USD")


class SignupViewTests(TestCase):
    def test_valid_signup_creates_user_and_logs_in(self):
        response = self.client.post(
            reverse("accounts:signup"),
            data={
                "username": "freshtraveller",
                "email": "fresh@example.com",
                "password1": "Sup3rSecurePass!23",
                "password2": "Sup3rSecurePass!23",
            },
        )
        self.assertRedirects(response, reverse("accounts:dashboard"))
        user = User.objects.get(username="freshtraveller")
        self.assertEqual(user.email, "fresh@example.com")
        response = self.client.get(reverse("accounts:dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_mismatched_passwords_do_not_create_user(self):
        before = User.objects.count()
        self.client.post(
            reverse("accounts:signup"),
            data={
                "username": "baduser",
                "email": "bad@example.com",
                "password1": "Sup3rSecurePass!23",
                "password2": "DifferentPass!45",
            },
        )
        self.assertEqual(User.objects.count(), before)

    def test_email_is_required(self):
        before = User.objects.count()
        response = self.client.post(
            reverse("accounts:signup"),
            data={"username": "noemail", "password1": "Sup3rSecurePass!23", "password2": "Sup3rSecurePass!23"},
        )
        self.assertEqual(User.objects.count(), before)
        self.assertFormError(response.context["form"], "email", "This field is required.")

    def test_duplicate_email_is_rejected(self):
        User.objects.create_user(username="first", email="taken@example.com", password="pw12345!")
        before = User.objects.count()
        response = self.client.post(
            reverse("accounts:signup"),
            data={
                "username": "second",
                "email": "taken@example.com",
                "password1": "Sup3rSecurePass!23",
                "password2": "Sup3rSecurePass!23",
            },
        )
        self.assertEqual(User.objects.count(), before)
        self.assertFormError(response.context["form"], "email", "An account with this email already exists.")

    def test_already_authenticated_user_is_redirected_away(self):
        user = User.objects.create_user(username="existing", password="pw12345!")
        self.client.force_login(user)
        response = self.client.get(reverse("accounts:signup"))
        self.assertRedirects(response, reverse("accounts:dashboard"))


class DashboardViewTests(TestCase):
    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("accounts:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_shows_empty_state_with_no_saved_trips(self):
        user = User.objects.create_user(username="lonely", password="pw12345!")
        self.client.force_login(user)
        response = self.client.get(reverse("accounts:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No saved trips yet")

    def test_lists_only_the_requesting_users_saved_trips(self):
        owner = User.objects.create_user(username="owner2", password="pw12345!")
        other = User.objects.create_user(username="other2", password="pw12345!")
        owned_trip = create_planned_trip_request(destination="Owner's Trip")
        others_trip = create_planned_trip_request(destination="Other's Trip")
        SavedTrip.objects.create(trip_request=owned_trip, user=owner)
        SavedTrip.objects.create(trip_request=others_trip, user=other)

        self.client.force_login(owner)
        response = self.client.get(reverse("accounts:dashboard"))
        self.assertContains(response, "Owner&#x27;s Trip")
        self.assertNotContains(response, "Other&#x27;s Trip")


class PasswordResetFlowTests(TestCase):
    """Django's test runner swaps EMAIL_BACKEND for the in-memory one
    automatically, so these exercise the real reset flow (email → link →
    new password) without touching the real Brevo SMTP relay.
    """

    def test_requesting_a_reset_for_a_known_email_sends_one(self):
        User.objects.create_user(username="resetme", email="resetme@example.com", password="OldPass!123")
        response = self.client.post(reverse("accounts:password_reset"), data={"email": "resetme@example.com"})
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Reset your TripPilot AI password", mail.outbox[0].subject)
        self.assertIn("resetme@example.com", mail.outbox[0].to)

    def test_unknown_email_sends_nothing_but_still_shows_the_done_page(self):
        # Must not leak whether an email is registered.
        response = self.client.post(reverse("accounts:password_reset"), data={"email": "nobody@example.com"})
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_full_reset_flow_changes_the_password(self):
        User.objects.create_user(username="resetme2", email="resetme2@example.com", password="OldPass!123")
        self.client.post(reverse("accounts:password_reset"), data={"email": "resetme2@example.com"})
        body = mail.outbox[0].body
        match = re.search(r"https?://[^\s]+/accounts/reset/[^\s]+", body)
        self.assertIsNotNone(match, body)
        # Strip scheme+domain, keep the path (test client doesn't need a host).
        reset_path = re.sub(r"^https?://[^/]+", "", match.group(0))

        # First GET validates the token and redirects to the session-backed
        # "set-password" URL — this is the same two-step flow a browser follows.
        response = self.client.get(reset_path, follow=True)
        confirm_path = response.request["PATH_INFO"]

        response = self.client.post(
            confirm_path,
            data={"new_password1": "NewSup3rPass!99", "new_password2": "NewSup3rPass!99"},
        )
        self.assertRedirects(response, reverse("accounts:password_reset_complete"))

        self.assertFalse(self.client.login(username="resetme2", password="OldPass!123"))
        self.assertTrue(self.client.login(username="resetme2", password="NewSup3rPass!99"))

    def test_login_page_links_to_password_reset(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertContains(response, reverse("accounts:password_reset"))


class GoogleSignInTests(TestCase):
    """Full OAuth can't be exercised in a unit test (it needs a real browser
    round-trip through accounts.google.com), so this covers what's actually
    ours: the redirect is initiated correctly, with the real configured
    client_id and the exact callback path Google Cloud Console must have
    registered. Verified live against the real endpoint separately (see
    session notes) — this locks that behavior in going forward.
    """

    def test_login_redirects_to_google_with_configured_client_id(self):
        response = self.client.get(reverse("google_login"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("https://accounts.google.com/o/oauth2/v2/auth?"))
        self.assertIn(f"client_id={settings.GOOGLE_OAUTH_CLIENT_ID}", response.url)
        self.assertIn("redirect_uri=", response.url)
        self.assertIn("accounts%2Fgoogle%2Flogin%2Fcallback", response.url)

    def test_login_and_signup_pages_link_to_google(self):
        for url_name in ("accounts:login", "accounts:signup"):
            response = self.client.get(reverse(url_name))
            self.assertContains(response, reverse("google_login"))
