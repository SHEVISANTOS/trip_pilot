"""View/form-level tests for the trips app. Unlike pricing/integrations,
these go through the real IntegrationClients (Celery runs eager in tests, so
planner() POST fully resolves before returning) — every client already
degrades to fixtures/estimates on failure, so this works with or without
configured API keys. Isolated from the real Redis cache to keep runs
hermetic; run with: python manage.py test trips
"""
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from trips.forms import TripRequestForm
from trips.models import BudgetBreakdown, SavedTrip, TripRequest
from trips.services import (
    SHOWCASE_CACHE_KEY,
    STALLED_PLAN_RETRY_AFTER_SECONDS,
    get_showcase_plan,
    retry_stalled_plan_if_needed,
)

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

    def test_return_date_same_as_departure_is_rejected(self):
        start = date.today() + timedelta(days=200)
        form = TripRequestForm(data=trip_form_data(start_date=start.isoformat(), end_date=start.isoformat()))
        self.assertFalse(form.is_valid())

    def test_departure_date_in_the_past_is_rejected(self):
        past = date.today() - timedelta(days=1)
        form = TripRequestForm(
            data=trip_form_data(start_date=past.isoformat(), end_date=(past + timedelta(days=8)).isoformat())
        )
        self.assertFalse(form.is_valid())
        self.assertIn("Departure date can't be in the past.", form.errors["__all__"])

    def test_return_date_in_the_past_is_rejected(self):
        # Both dates in the past also trips the departure-date check, so use
        # a departure of today with a return date before it.
        past = date.today() - timedelta(days=1)
        form = TripRequestForm(data=trip_form_data(start_date=date.today().isoformat(), end_date=past.isoformat()))
        self.assertFalse(form.is_valid())
        self.assertIn("Return date can't be in the past.", form.errors["__all__"])

    def test_todays_date_is_accepted_as_departure(self):
        today = date.today()
        form = TripRequestForm(
            data=trip_form_data(start_date=today.isoformat(), end_date=(today + timedelta(days=8)).isoformat())
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_date_widgets_advertise_todays_date_as_the_minimum(self):
        form = TripRequestForm()
        today = date.today().isoformat()
        self.assertEqual(form.fields["start_date"].widget.attrs["min"], today)
        self.assertEqual(form.fields["end_date"].widget.attrs["min"], today)


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
class PlannerRateLimitTests(TestCase):
    """Regression coverage for a real incident: the rate limit used to apply
    to GET too, so heavy testing traffic from one IP locked that IP out of
    even *viewing* the form, not just submitting it. The limit should only
    ever throttle the expensive action (POST, which fires ~7 live API calls).
    """

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)

    def test_get_is_never_rate_limited(self):
        for _ in range(25):  # comfortably past the 20/h POST limit
            response = self.client.get(reverse("trips:planner"))
            self.assertEqual(response.status_code, 200)

    def test_post_is_still_rate_limited_after_twenty(self):
        # Patch out the expensive pipeline — this test is about the rate
        # limit itself, not plan generation (already covered elsewhere).
        with patch("trips.views.build_and_persist_plan_task"):
            for _ in range(20):
                response = self.client.post(reverse("trips:planner"), data=trip_form_data())
                self.assertEqual(response.status_code, 302)
            blocked = self.client.post(reverse("trips:planner"), data=trip_form_data())
        self.assertEqual(blocked.status_code, 403)


# A real incident: REDIS_URL pointed at a host that stopped resolving, and
# the whole site went down with an uncaught ConnectionError on POST /.
# integrations/base.py's cache.get()/set() calls sit outside any try/except
# (a cache lookup is meant to be cheap and unconditional, not something every
# caller has to guard), and django-ratelimit reads its counter from the same
# cache — so a Redis outage took out both the caching layer and rate
# limiting at once. Fixed with IGNORE_EXCEPTIONS on the Redis backend (a
# failed cache op becomes a miss/no-op, matching django_redis's own
# behaviour, verified directly against an unreachable host) plus
# RATELIMIT_FAIL_OPEN (django-ratelimit's own default is to fail *closed* —
# block everything — when it can't read its counter; wrong call here, since
# every integration client already degrades independently).
BROKEN_REDIS_CACHE = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://nonexistent-host-xyz.invalid:12118",
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient", "IGNORE_EXCEPTIONS": True},
    }
}


@override_settings(CACHES=BROKEN_REDIS_CACHE)
class RedisOutageTests(TestCase):
    def test_planner_submission_survives_an_unreachable_cache_backend(self):
        response = self.client.post(reverse("trips:planner"), data=trip_form_data())
        self.assertEqual(response.status_code, 302, f"crashed instead of redirecting: {response.status_code}")
        results = self.client.get(response.url)
        self.assertEqual(results.status_code, 200)
        self.assertContains(results, "Istanbul")


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


def make_pending_trip(age_seconds=0, **overrides):
    """A TripRequest with no BudgetBreakdown, created_at backdated by
    age_seconds — simulates a plan that's been "generating" for a while.
    """
    defaults = dict(
        departure="Dar es Salaam",
        destination="Arusha",
        nationality="Tanzanian",
        start_date=date.today() + timedelta(days=100),
        end_date=date.today() + timedelta(days=105),
        budget=3000,
    )
    defaults.update(overrides)
    trip_request = TripRequest.objects.create(**defaults)
    if age_seconds:
        TripRequest.objects.filter(pk=trip_request.pk).update(
            created_at=timezone.now() - timedelta(seconds=age_seconds)
        )
        trip_request.refresh_from_db()
    return trip_request


@override_settings(CACHES=LOCMEM_CACHE)
class StalledPlanRetryTests(TestCase):
    """Covers the case that motivated this: a trip stuck showing "Building
    your plan..." forever with nothing left retrying it (e.g. the process
    generating it was killed mid-task). retry_stalled_plan_if_needed() is
    what lets results() self-heal instead of refreshing indefinitely.
    """

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)

    def test_recently_created_trip_is_not_retried_yet(self):
        trip_request = make_pending_trip(age_seconds=1)
        with patch("trips.tasks.build_and_persist_plan_task") as mocked_task:
            retry_stalled_plan_if_needed(trip_request)
        mocked_task.delay.assert_not_called()

    def test_stalled_trip_triggers_a_retry(self):
        trip_request = make_pending_trip(age_seconds=STALLED_PLAN_RETRY_AFTER_SECONDS + 5)
        with patch("trips.tasks.build_and_persist_plan_task") as mocked_task:
            retry_stalled_plan_if_needed(trip_request)
        mocked_task.delay.assert_called_once_with(trip_request.pk)

    def test_cooldown_prevents_a_retry_storm(self):
        trip_request = make_pending_trip(age_seconds=STALLED_PLAN_RETRY_AFTER_SECONDS + 5)
        with patch("trips.tasks.build_and_persist_plan_task") as mocked_task:
            retry_stalled_plan_if_needed(trip_request)
            retry_stalled_plan_if_needed(trip_request)
            retry_stalled_plan_if_needed(trip_request)
        mocked_task.delay.assert_called_once()

    def test_stalled_page_self_heals_on_a_later_refresh(self):
        trip_request = make_pending_trip(age_seconds=STALLED_PLAN_RETRY_AFTER_SECONDS + 5)
        url = reverse("trips:results", kwargs={"pk": trip_request.pk})

        first = self.client.get(url)
        self.assertContains(first, "Building your")
        # CELERY_TASK_ALWAYS_EAGER=True in tests, so the retry triggered
        # above already ran the real pipeline synchronously by this point.
        self.assertTrue(BudgetBreakdown.objects.filter(trip_request=trip_request).exists())

        second = self.client.get(url)
        self.assertContains(second, "Budget Allocation")


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


@override_settings(CACHES=LOCMEM_CACHE)
class ShowcasePlanTests(TestCase):
    """The landing-page hero card is computed from the live providers rather
    than hardcoded, so it needs the same fallback guarantees as everything
    else — it must never take the homepage down.
    """

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)

    def test_prefers_a_real_trip_someone_already_planned(self):
        self.client.post(reverse("trips:planner"), data=trip_form_data(destination="Nairobi, Kenya"))
        showcase = get_showcase_plan()
        self.assertEqual(showcase["destination"], "Nairobi")
        self.assertEqual(showcase["flag"], "🇰🇪")
        self.assertTrue(showcase["total"].startswith("$"))
        self.assertGreater(showcase["nights"], 0)

    def test_shows_the_most_recent_trip_when_several_exist(self):
        self.client.post(reverse("trips:planner"), data=trip_form_data(destination="Nairobi, Kenya"))
        self.client.post(reverse("trips:planner"), data=trip_form_data(destination="Bangkok, Thailand"))
        self.assertEqual(get_showcase_plan()["destination"], "Bangkok")

    def test_never_exposes_who_planned_the_trip(self):
        user = User.objects.create_user(username="private-traveller", password="pw12345!")
        self.client.force_login(user)
        self.client.post(reverse("trips:planner"), data=trip_form_data())
        self.assertNotIn("private-traveller", str(get_showcase_plan()))

    def test_falls_back_to_computed_demo_on_an_empty_database(self):
        self.assertFalse(TripRequest.objects.exists())
        showcase = get_showcase_plan()
        self.assertIsNotNone(showcase)
        self.assertEqual(showcase["destination"], "Istanbul")
        self.assertEqual(showcase["flag"], "🇹🇷")

    def test_computed_fallback_is_cached_so_the_homepage_does_not_refetch(self):
        first = get_showcase_plan()  # empty DB -> computed demo
        self.assertIsNotNone(cache.get(SHOWCASE_CACHE_KEY))
        with patch("trips.services.build_plan") as mocked:
            second = get_showcase_plan()
        mocked.assert_not_called()
        self.assertEqual(first, second)

    def test_provider_failure_never_breaks_the_page(self):
        with patch("trips.services.build_plan", side_effect=RuntimeError("everything is down")):
            self.assertIsNone(get_showcase_plan())

    def test_database_failure_never_breaks_the_page(self):
        with patch("trips.services.TripRequest.objects.select_related", side_effect=RuntimeError("db down")):
            self.assertIsNotNone(get_showcase_plan())  # drops through to the computed demo

    def test_planner_page_renders_without_a_showcase(self):
        with patch("trips.views.get_showcase_plan", return_value=None):
            response = self.client.get(reverse("trips:planner"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Build your trip")
        self.assertNotContains(response, "hero-card")
