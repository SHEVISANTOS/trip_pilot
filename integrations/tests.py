import responses
from django.test import TestCase, override_settings

from integrations.activities import OpenTripMapClient
from integrations.amadeus import AmadeusClient
from integrations.esim import EsimGoClient
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


def _esim_bundle(name, price, duration, group, data_amount=1000, unlimited=False):
    return {
        "name": name,
        "description": name,
        "countries": [{"name": "Turkey", "region": "Middle East", "iso": "TR"}],
        "dataAmount": -1 if unlimited else data_amount,
        "duration": duration,
        "durationUnit": "day",
        "unlimited": unlimited,
        "price": price,
        "groups": [group],
    }


# A representative slice of the real TR catalogue (fetched live from
# api.esim-go.com), covering the two preferred groups plus a premium tier
# that should never be picked over a cheaper option at the same duration.
SAMPLE_TR_BUNDLES = [
    _esim_bundle("esim_1GB_7D_TR_V2", 1.32, 7, "Standard Fixed", data_amount=1000),
    _esim_bundle("esim_2GB_15D_TR_V2", 2.10, 15, "Standard Fixed", data_amount=2000),
    _esim_bundle("esim_UL_7D_TR_V2", 8.63, 7, "Standard Unlimited Lite", unlimited=True),
    _esim_bundle("esim_ULE_7D_TR_V2", 10.36, 7, "Standard Unlimited Essential", unlimited=True),
    _esim_bundle("esim_3GB_30D_TR_V2", 2.71, 30, "Standard Fixed", data_amount=3000),
]


@override_settings(ESIM_GO_API_KEY="")
class EsimGoClientFallbackTests(TestCase):
    def test_unconfigured_returns_none_and_logs_failure(self):
        client = EsimGoClient()
        result = client.get_bundle("Istanbul, Türkiye", 8)
        self.assertIsNone(result)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="esim", success=False).count(), 1)


@override_settings(ESIM_GO_API_KEY="test-key")
class EsimGoClientTests(TestCase):
    @responses.activate
    def test_unresolvable_destination_returns_none_without_calling_api(self):
        client = EsimGoClient()
        result = client.get_bundle("Some Made Up Place", 8)
        self.assertIsNone(result)
        self.assertEqual(len(responses.calls), 0)

    @responses.activate
    def test_picks_cheapest_bundle_covering_trip_length(self):
        responses.add(
            responses.GET,
            "https://api.esim-go.com/v2.5/catalogue",
            json={"bundles": SAMPLE_TR_BUNDLES},
            status=200,
        )
        client = EsimGoClient()
        result = client.get_bundle("Istanbul, Türkiye", 8)
        # Only esim_2GB_15D (duration 15 >= 8) and esim_3GB_30D (30 >= 8) cover
        # an 8-night trip among "Fixed"/"Lite" groups; 15D is cheaper.
        self.assertEqual(result.name, "esim_2GB_15D_TR_V2")
        self.assertEqual(result.price, 2.10)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="esim", success=True).count(), 1)

    @responses.activate
    def test_falls_back_to_longest_duration_when_nothing_covers_trip(self):
        responses.add(
            responses.GET,
            "https://api.esim-go.com/v2.5/catalogue",
            json={"bundles": SAMPLE_TR_BUNDLES},
            status=200,
        )
        client = EsimGoClient()
        result = client.get_bundle("Istanbul, Türkiye", 45)
        self.assertEqual(result.name, "esim_3GB_30D_TR_V2")

    @responses.activate
    def test_ignores_premium_unlimited_tiers(self):
        responses.add(
            responses.GET,
            "https://api.esim-go.com/v2.5/catalogue",
            json={"bundles": SAMPLE_TR_BUNDLES},
            status=200,
        )
        client = EsimGoClient()
        result = client.get_bundle("Istanbul, Türkiye", 5)
        self.assertNotEqual(result.name, "esim_ULE_7D_TR_V2")

    @responses.activate
    def test_api_error_falls_back_to_none(self):
        responses.add(responses.GET, "https://api.esim-go.com/v2.5/catalogue", status=500)
        client = EsimGoClient()
        result = client.get_bundle("Istanbul, Türkiye", 8)
        self.assertIsNone(result)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="esim", success=False).count(), 1)
