from datetime import date, timedelta

from django.contrib.auth import get_user_model
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
            data={"username": "freshtraveller", "password1": "Sup3rSecurePass!23", "password2": "Sup3rSecurePass!23"},
        )
        self.assertRedirects(response, reverse("accounts:dashboard"))
        self.assertTrue(User.objects.filter(username="freshtraveller").exists())
        response = self.client.get(reverse("accounts:dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_mismatched_passwords_do_not_create_user(self):
        before = User.objects.count()
        self.client.post(
            reverse("accounts:signup"),
            data={"username": "baduser", "password1": "Sup3rSecurePass!23", "password2": "DifferentPass!45"},
        )
        self.assertEqual(User.objects.count(), before)

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
