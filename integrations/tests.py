import responses
from django.test import TestCase, override_settings

from integrations.activities import OpenTripMapClient
from integrations.amadeus import AmadeusClient
from integrations.exchange import ExchangeRateClient
from integrations.fixtures import sample_data_for
from integrations.maps import MapsClient
from integrations.models import IntegrationCallLog
from integrations.visa import SherpaVisaClient


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


@override_settings(SHERPA_API_KEY="")
class SherpaVisaClientFallbackTests(TestCase):
    def test_unconfigured_falls_back_to_fixture(self):
        client = SherpaVisaClient()
        result = client.get_visa_info("Tanzanian", "Istanbul")
        self.assertEqual(result, sample_data_for("Istanbul").visa)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="visa", success=False).count(), 1)

    @override_settings(SHERPA_API_KEY="test-key")
    @responses.activate
    def test_configured_success_is_used_and_logged(self):
        responses.add(
            responses.GET,
            "https://api.joinsherpa.com/v2/trips",
            json={"visaType": "e-Visa", "guidance": "Apply online", "maxStay": "30 days", "cost": 75, "tag": "OK"},
            status=200,
        )
        client = SherpaVisaClient()
        result = client.get_visa_info("Tanzanian", "Istanbul")
        self.assertEqual(result.type, "e-Visa")
        self.assertEqual(result.cost, 75)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="visa", success=True).count(), 1)


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
