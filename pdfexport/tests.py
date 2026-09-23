from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from trips.models import BudgetBreakdown, Itinerary, ItineraryActivity, ItineraryDay, SavedTrip, TripRequest

User = get_user_model()


def create_saved_trip(user, **overrides):
    """TripRequest + BudgetBreakdown + a one-day Itinerary, built directly
    via the ORM — the PDF template only needs valid related objects to
    render, not a real (network-backed) generated plan.
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
        hotels=[],
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
    itinerary = Itinerary.objects.create(trip_request=trip_request)
    day = ItineraryDay.objects.create(itinerary=itinerary, day_number=1, title="Day 1 — Arrival")
    ItineraryActivity.objects.create(day=day, order=0, time="15:20", title="Arrive", cost_display="Flight arrival")
    return SavedTrip.objects.create(trip_request=trip_request, user=user)


class ExportPdfViewTests(TestCase):
    def test_anonymous_user_is_redirected_to_login(self):
        owner = User.objects.create_user(username="pdfowner", password="pw12345!")
        saved_trip = create_saved_trip(owner)
        response = self.client.get(reverse("pdfexport:export", kwargs={"pk": saved_trip.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_owner_can_download_a_valid_pdf(self):
        owner = User.objects.create_user(username="pdfowner2", password="pw12345!")
        saved_trip = create_saved_trip(owner)
        self.client.force_login(owner)
        response = self.client.get(reverse("pdfexport:export", kwargs={"pk": saved_trip.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_other_user_cannot_download_someone_elses_pdf(self):
        owner = User.objects.create_user(username="pdfowner3", password="pw12345!")
        other = User.objects.create_user(username="pdfother", password="pw12345!")
        saved_trip = create_saved_trip(owner)
        self.client.force_login(other)
        response = self.client.get(reverse("pdfexport:export", kwargs={"pk": saved_trip.pk}))
        self.assertEqual(response.status_code, 404)

    def test_missing_weasyprint_system_libraries_degrades_gracefully(self):
        # Simulates a host (serverless platforms especially) that has the
        # weasyprint package installed but not its native Pango/Cairo/
        # GDK-Pixbuf dependencies — WeasyPrint raises OSError in that case.
        # This must return a clean error response, not crash the request.
        owner = User.objects.create_user(username="pdfowner4", password="pw12345!")
        saved_trip = create_saved_trip(owner)
        self.client.force_login(owner)
        with patch("weasyprint.HTML", side_effect=OSError("cannot load library libpango-1.0")):
            response = self.client.get(reverse("pdfexport:export", kwargs={"pk": saved_trip.pk}))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Content-Type"], "text/plain")
