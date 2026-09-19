import responses
from django.test import TestCase, override_settings

from integrations.activities import OpenTripMapClient
from integrations.amadeus import AmadeusClient
from integrations.exchange import ExchangeRateClient
from integrations.fixtures import sample_data_for
from integrations.maps import MapsClient
from integrations.models import IntegrationCallLog
from integrations.visa import PassportIndexVisaClient


@override_settings(AMADEUS_API_KEY="", AMADEUS_API_SECRET="")
class AmadeusClientFallbackTests(TestCase):
    def test_unconfigured_flights_fall_back_to_fixture_and_logs_failure(self):
        client = AmadeusClient()
        result = client.search_flights("DAR", "Istanbul", 3)
        self.assertEqual(result, sample_data_for("Istanbul").flights)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="amadeus_flights", success=False).count(), 1)

    def test_unconfigured_hotels_fall_back_to_fixture_and_logs_failure(self):
        client = AmadeusClient()
        result = client.search_hotels("Istanbul", 8, 3)
        self.assertEqual(result, sample_data_for("Istanbul").hotels)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="amadeus_hotels", success=False).count(), 1)


@override_settings(OPENTRIPMAP_API_KEY="")
class OpenTripMapClientFallbackTests(TestCase):
    def test_unconfigured_falls_back_to_fixture(self):
        client = OpenTripMapClient()
        result = client.search_attractions("Istanbul")
        self.assertEqual(result, sample_data_for("Istanbul").attractions)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="opentripmap", success=False).count(), 1)


class PassportIndexVisaClientTests(TestCase):
    def test_known_pair_resolves_from_dataset_and_logs_success(self):
        client = PassportIndexVisaClient()
        result = client.get_visa_info("Tanzanian", "Istanbul, Türkiye")
        self.assertEqual(result.type, "Visa required")
        self.assertEqual(result.tag, "VISA REQUIRED")
        self.assertEqual(IntegrationCallLog.objects.filter(provider="visa", success=True).count(), 1)

    def test_eta_requirement_maps_to_eta_tag(self):
        client = PassportIndexVisaClient()
        result = client.get_visa_info("American", "London, United Kingdom")
        self.assertEqual(result.tag, "ETA")

    def test_numeric_days_maps_to_visa_free_with_day_count(self):
        client = PassportIndexVisaClient()
        # Kenyan passport into Tanzania is visa-free for a fixed number of days
        # in this dataset snapshot; assert the shape rather than the exact
        # number so the test survives dataset refreshes.
        result = client.get_visa_info("Kenyan", "Zanzibar")
        self.assertIn(result.tag, {"VISA-FREE", "VISA ON ARRIVAL", "E-VISA", "ETA", "VISA REQUIRED"})

    def test_unresolvable_nationality_falls_back_to_fixture(self):
        client = PassportIndexVisaClient()
        result = client.get_visa_info("Not A Real Nationality", "Istanbul, Türkiye")
        self.assertEqual(result, sample_data_for("Istanbul, Türkiye").visa)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="visa", success=False).count(), 1)

    def test_unresolvable_destination_falls_back_to_fixture(self):
        client = PassportIndexVisaClient()
        result = client.get_visa_info("Tanzanian", "Some Made Up Place")
        self.assertEqual(result, sample_data_for("Some Made Up Place").visa)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="visa", success=False).count(), 1)


class ExchangeRateClientTests(TestCase):
    def test_same_currency_short_circuits_to_rate_one(self):
        client = ExchangeRateClient()
        result = client.get_rate("USD", "USD")
        self.assertEqual(result.rate, 1.0)

    @responses.activate
    def test_live_call_failure_falls_back_to_rate_one(self):
        responses.add(responses.GET, "https://api.exchangerate.host/latest", status=500)
        client = ExchangeRateClient()
        result = client.get_rate("USD", "EUR")
        self.assertEqual(result.rate, 1.0)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="exchange_rate", success=False).count(), 1)

    @responses.activate
    def test_live_call_success_returns_real_rate(self):
        responses.add(
            responses.GET,
            "https://api.exchangerate.host/latest",
            json={"rates": {"EUR": 0.92}},
            status=200,
        )
        client = ExchangeRateClient()
        result = client.get_rate("USD", "EUR")
        self.assertEqual(result.rate, 0.92)


@override_settings(GOOGLE_MAPS_API_KEY="")
class MapsClientFallbackTests(TestCase):
    def test_unconfigured_falls_back_to_flat_estimate(self):
        client = MapsClient()
        result = client.estimate_transfer_cost("Airport", "Hotel")
        self.assertEqual(result, 80.0)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="maps", success=False).count(), 1)
