"""View/form-level tests for the trips app. Unlike pricing/integrations,
these go through the real IntegrationClients (Celery runs eager in tests, so
planner() POST fully resolves before returning) — every client already
degrades to fixtures/estimates on failure, so this works with or without
configured API keys. Isolated from the real Redis cache to keep runs
hermetic; run with: python manage.py test trips
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from trips.forms import TripRequestForm
from trips.models import SavedTrip, TripRequest

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

User = get_user_model()


def trip_form_data(**overrides):
    start = date.today() + timedelta(days=200)
    end = start + timedelta(days=8)
    data = {
        "departure": "Dar es Salaam",
        "destination": "Istanbul, Türkiye",
        "nationality": "Tanzanian",
        "purpose": "Holiday",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "adults": 2,
        "children": 1,
        "travel_style": "balanced",
        "hotel_preference": "3–4 Star Hotel",
        "interests": "History, food",
        "currency": "USD",
        "budget": 5000,
    }
    data.update(overrides)
    return data


class TripRequestFormTests(TestCase):
    def test_valid_data_is_accepted(self):
        form = TripRequestForm(data=trip_form_data())
        self.assertTrue(form.is_valid(), form.errors)

    def test_return_date_before_departure_is_rejected(self):
        start = date.today() + timedelta(days=200)
        form = TripRequestForm(
            data=trip_form_data(start_date=start.isoformat(), end_date=(start - timedelta(days=1)).isoformat())
        )
        self.assertFalse(form.is_valid())


@override_settings(CACHES=LOCMEM_CACHE)
class PlannerViewTests(TestCase):
    def test_get_shows_form_with_defaults(self):
        response = self.client.get(reverse("trips:planner"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Build your trip")

    def test_post_valid_data_creates_trip_and_generates_plan(self):
        response = self.client.post(reverse("trips:planner"), data=trip_form_data())
        trip_request = TripRequest.objects.latest("created_at")
        self.assertRedirects(response, reverse("trips:results", kwargs={"pk": trip_request.pk}))
        # CELERY_TASK_ALWAYS_EAGER=True in tests, so the plan already exists.
        self.assertTrue(hasattr(trip_request, "budget_breakdown"))
        self.assertTrue(hasattr(trip_request, "itinerary"))

    def test_post_invalid_data_reshows_form_without_creating_trip(self):
        before = TripRequest.objects.count()
        response = self.client.post(reverse("trips:planner"), data=trip_form_data(destination=""))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(TripRequest.objects.count(), before)

    def test_authenticated_submission_is_attributed_to_user(self):
        user = User.objects.create_user(username="traveller", password="pw12345!")
        self.client.force_login(user)
        self.client.post(reverse("trips:planner"), data=trip_form_data())
        trip_request = TripRequest.objects.latest("created_at")
        self.assertEqual(trip_request.created_by, user)


@override_settings(CACHES=LOCMEM_CACHE)
class ResultsViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        client = cls.client_class()
        client.post(reverse("trips:planner"), data=trip_form_data())
        cls.trip_request = TripRequest.objects.latest("created_at")

    def test_renders_generated_plan(self):
        response = self.client.get(reverse("trips:results", kwargs={"pk": self.trip_request.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Istanbul, Türkiye")
        self.assertContains(response, "Budget Allocation")

    def test_missing_trip_request_404s(self):
        response = self.client.get(reverse("trips:results", kwargs={"pk": 999999}))
        self.assertEqual(response.status_code, 404)

    def test_pending_state_shown_before_plan_exists(self):
        pending_trip = TripRequest.objects.create(
            departure="Dar es Salaam",
            destination="Nairobi, Kenya",
            nationality="Tanzanian",
            start_date=date.today() + timedelta(days=100),
            end_date=date.today() + timedelta(days=105),
            budget=3000,
        )
        response = self.client.get(reverse("trips:results", kwargs={"pk": pending_trip.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Building your")


@override_settings(CACHES=LOCMEM_CACHE)
class OptimizeViewTests(TestCase):
    def test_over_budget_trip_gets_downgraded_and_redirects(self):
        response = self.client.post(
            reverse("trips:planner"),
            data=trip_form_data(travel_style="luxury", budget=100),
        )
        trip_request = TripRequest.objects.latest("created_at")
        self.assertTrue(trip_request.budget_breakdown.over_budget)

        response = self.client.post(reverse("trips:optimize", kwargs={"pk": trip_request.pk}))
        self.assertRedirects(response, reverse("trips:results", kwargs={"pk": trip_request.pk}))
        trip_request.refresh_from_db()
        self.assertEqual(trip_request.travel_style, "budget")

    def test_get_is_not_allowed(self):
        response = self.client.post(reverse("trips:planner"), data=trip_form_data())
        trip_request = TripRequest.objects.latest("created_at")
        response = self.client.get(reverse("trips:optimize", kwargs={"pk": trip_request.pk}))
        self.assertEqual(response.status_code, 405)


@override_settings(CACHES=LOCMEM_CACHE)
class SaveTripViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        client = cls.client_class()
        client.post(reverse("trips:planner"), data=trip_form_data())
        cls.trip_request = TripRequest.objects.latest("created_at")

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.post(reverse("trips:save_trip", kwargs={"pk": self.trip_request.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)
        self.assertFalse(SavedTrip.objects.filter(trip_request=self.trip_request).exists())

    def test_authenticated_user_can_save_trip_with_seeded_checklist(self):
        user = User.objects.create_user(username="saver", password="pw12345!")
        self.client.force_login(user)
        self.client.post(reverse("trips:save_trip", kwargs={"pk": self.trip_request.pk}))
        saved = SavedTrip.objects.get(trip_request=self.trip_request, user=user)
        self.assertEqual(len(saved.checklist), 9)
        self.assertTrue(all(item["done"] is False for item in saved.checklist))


@override_settings(CACHES=LOCMEM_CACHE)
class ToggleChecklistViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        client = cls.client_class()
        client.post(reverse("trips:planner"), data=trip_form_data())
        cls.trip_request = TripRequest.objects.latest("created_at")
        cls.owner = User.objects.create_user(username="owner", password="pw12345!")
        cls.other = User.objects.create_user(username="other", password="pw12345!")
        cls.saved_trip = SavedTrip.objects.create(trip_request=cls.trip_request, user=cls.owner)

    def test_owner_can_toggle_checklist_item(self):
        self.client.force_login(self.owner)
        url = reverse("trips:toggle_checklist", kwargs={"pk": self.saved_trip.pk, "index": 0})
        self.client.post(url)
        self.saved_trip.refresh_from_db()
        self.assertTrue(self.saved_trip.checklist[0]["done"])

    def test_other_user_cannot_toggle_someone_elses_checklist(self):
        self.client.force_login(self.other)
        url = reverse("trips:toggle_checklist", kwargs={"pk": self.saved_trip.pk, "index": 0})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)
        self.saved_trip.refresh_from_db()
        self.assertFalse(self.saved_trip.checklist[0]["done"])
