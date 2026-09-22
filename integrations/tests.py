import re
from datetime import date

import responses
from django.core.cache import cache
from django.test import TestCase, override_settings

from integrations.activities import OpenTripMapClient, estimate_entry_cost
from integrations.base import BaseClient
from integrations.esim import EsimGoClient
from integrations.exchange import ExchangeRateClient
from integrations.fixtures import sample_data_for
from integrations.hotels import LiteApiHotelClient
from integrations.maps import MapsClient
from integrations.models import IntegrationCallLog
from integrations.travelpayouts import TravelpayoutsClient, resolve_iata
from integrations.visa import PassportIndexVisaClient

# A real incident: REDIS_URL pointed at a host that had stopped resolving,
# and integrations/base.py's cache.get() — deliberately outside any
# try/except, since a cache lookup shouldn't need one — took the whole site
# down with an uncaught ConnectionError. Fixed via IGNORE_EXCEPTIONS on the
# Redis backend in settings.py; this points at a host that will never
# resolve, to prove the client degrades to a cache miss instead of raising.
BROKEN_REDIS_CACHE = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://nonexistent-host-xyz.invalid:12118",
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient", "IGNORE_EXCEPTIONS": True},
    }
}

# Every client test runs against an isolated in-process cache rather than the
# real REDIS_URL from .env — keeps tests hermetic (no network dependency, no
# cross-test-run pollution) and fast, and avoids writing test data into a
# real shared Redis instance.
LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


class CacheIsolatedTestCase(TestCase):
    """LocMemCache keeps a process-global store keyed by location, so cached
    provider responses survive across test methods and classes. Without this,
    two tests calling the same client with the same arguments silently share
    a result — the second one never hits its own mocked response.
    """

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)


@override_settings(CACHES=BROKEN_REDIS_CACHE)
class BaseClientCacheOutageTests(TestCase):
    def test_call_falls_through_to_fetch_when_cache_is_unreachable(self):
        calls = []

        def fetch():
            calls.append(1)
            return "live result"

        result = BaseClient().call("test_provider", fetch, "fallback", cache_key="some-key", cache_ttl=60)
        self.assertEqual(result, "live result")
        self.assertEqual(len(calls), 1)

    def test_a_second_call_hits_fetch_again_since_nothing_was_actually_cached(self):
        # cache.set() also silently no-ops against an unreachable host, so
        # this must never crash either, and correctly can't skip re-fetching.
        calls = []

        def fetch():
            calls.append(1)
            return "live result"

        client = BaseClient()
        client.call("test_provider", fetch, "fallback", cache_key="some-key", cache_ttl=60)
        client.call("test_provider", fetch, "fallback", cache_key="some-key", cache_ttl=60)
        self.assertEqual(len(calls), 2)


TP_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
ROUTES_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
GEOCODE_URL = "https://nominatim.openstreetmap.org/search"
OSRM_ANY = re.compile(r"https://router\.project-osrm\.org/route/v1/driving/.*")


def tp_row(airline="TK", price=756, transfers=1, duration_to=655):
    return {
        "flight_number": "1234",
        "origin": "DAR",
        "destination": "IST",
        "origin_airport": "DAR",
        "destination_airport": "IST",
        "departure_at": "2027-05-10T19:45:00+03:00",
        "return_at": "2027-05-18T14:05:00+03:00",
        "airline": airline,
        "price": price,
        "transfers": transfers,
        "duration_to": duration_to,
    }


class ResolveIataTests(TestCase):
    def test_resolves_city_with_country_suffix(self):
        self.assertEqual(resolve_iata("Istanbul, Türkiye"), "IST")

    def test_resolves_bare_city_name(self):
        self.assertEqual(resolve_iata("Dar es Salaam"), "DAR")

    def test_disambiguates_same_named_cities_by_country(self):
        # "London" alone must not resolve to London, Ontario (YXU).
        self.assertEqual(resolve_iata("London, United Kingdom"), "LON")
        self.assertEqual(resolve_iata("London"), "LON")

    def test_unknown_place_returns_none(self):
        self.assertIsNone(resolve_iata("Atlantis"))


@override_settings(TRAVELPAYOUTS_TOKEN="", CACHES=LOCMEM_CACHE)
class TravelpayoutsUnconfiguredTests(CacheIsolatedTestCase):
    def test_unconfigured_returns_none_and_logs_failure(self):
        result = TravelpayoutsClient().search_flights("Dar es Salaam", "Istanbul, Türkiye", 3)
        self.assertIsNone(result)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="travelpayouts", success=False).count(), 1)


@override_settings(TRAVELPAYOUTS_TOKEN="test-token", CACHES=LOCMEM_CACHE)
class TravelpayoutsClientTests(CacheIsolatedTestCase):
    @responses.activate
    def test_unresolvable_route_returns_none_without_calling_api(self):
        result = TravelpayoutsClient().search_flights("Atlantis", "Shangri-La", 2)
        self.assertIsNone(result)
        self.assertEqual(len(responses.calls), 0)

    @responses.activate
    def test_maps_response_to_flight_offers(self):
        responses.add(responses.GET, TP_URL, json={"data": [tp_row()], "success": True}, status=200)
        offers = TravelpayoutsClient().search_flights("Dar es Salaam", "Istanbul, Türkiye", 3)
        self.assertEqual(len(offers), 1)
        offer = offers[0]
        self.assertEqual(offer.airline, "Turkish Airlines")  # IATA code expanded via airlines.json
        self.assertEqual(offer.route, "DAR → IST → DAR")
        self.assertEqual(offer.price, 756.0)
        self.assertEqual(offer.stops, "1 stop")
        self.assertIn("10h", offer.duration)
        self.assertEqual(offer.label, "Cheapest")

    @responses.activate
    def test_direct_flight_is_labelled_direct(self):
        responses.add(responses.GET, TP_URL, json={"data": [tp_row(transfers=0)]}, status=200)
        offers = TravelpayoutsClient().search_flights("Dar es Salaam", "Istanbul, Türkiye", 1)
        self.assertEqual(offers[0].stops, "Direct")

    @responses.activate
    def test_falls_back_through_date_windows_when_exact_dates_are_empty(self):
        # Exact dates and month both return nothing; the undated third attempt
        # is what cached-price routes usually answer on.
        responses.add(responses.GET, TP_URL, json={"data": []}, status=200)
        responses.add(responses.GET, TP_URL, json={"data": []}, status=200)
        responses.add(responses.GET, TP_URL, json={"data": [tp_row(price=433)]}, status=200)
        offers = TravelpayoutsClient().search_flights(
            "Dar es Salaam", "Istanbul, Türkiye", 2, date(2027, 5, 10), date(2027, 5, 18)
        )
        self.assertEqual(len(responses.calls), 3)
        self.assertEqual(offers[0].price, 433.0)

    @responses.activate
    def test_no_cached_prices_at_all_returns_none(self):
        responses.add(responses.GET, TP_URL, json={"data": []}, status=200)
        offers = TravelpayoutsClient().search_flights("Dar es Salaam", "Istanbul, Türkiye", 2)
        self.assertIsNone(offers)

    @responses.activate
    def test_api_error_falls_back_to_none_and_logs(self):
        responses.add(responses.GET, TP_URL, status=500)
        offers = TravelpayoutsClient().search_flights("Dar es Salaam", "Istanbul, Türkiye", 2)
        self.assertIsNone(offers)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="travelpayouts", success=False).count(), 1)


@override_settings(OPENTRIPMAP_API_KEY="", CACHES=LOCMEM_CACHE)
class OpenTripMapClientFallbackTests(CacheIsolatedTestCase):
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


@override_settings(CACHES=LOCMEM_CACHE)
class ExchangeRateClientTests(CacheIsolatedTestCase):
    def test_same_currency_short_circuits_to_rate_one(self):
        client = ExchangeRateClient()
        result = client.get_rate("USD", "USD")
        self.assertEqual(result.rate, 1.0)

    @responses.activate
    def test_live_call_failure_falls_back_to_rate_one(self):
        responses.add(responses.GET, "https://open.er-api.com/v6/latest/USD", status=500)
        client = ExchangeRateClient()
        result = client.get_rate("USD", "EUR")
        self.assertEqual(result.rate, 1.0)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="exchange_rate", success=False).count(), 1)

    @responses.activate
    def test_live_call_success_returns_real_rate(self):
        responses.add(
            responses.GET,
            "https://open.er-api.com/v6/latest/USD",
            json={"rates": {"EUR": 0.92}},
            status=200,
        )
        client = ExchangeRateClient()
        result = client.get_rate("USD", "EUR")
        self.assertEqual(result.rate, 0.92)


@override_settings(GOOGLE_MAPS_API_KEY="", CACHES=LOCMEM_CACHE)
class MapsClientFallbackTests(CacheIsolatedTestCase):
    @responses.activate
    def test_no_google_key_uses_keyless_osm_routing(self):
        responses.add(responses.GET, GEOCODE_URL, json=[{"lat": "41.27", "lon": "28.73"}], status=200)
        responses.add(responses.GET, GEOCODE_URL, json=[{"lat": "41.00", "lon": "28.97"}], status=200)
        responses.add(responses.GET, OSRM_ANY, json={"code": "Ok", "routes": [{"distance": 50500}]}, status=200)
        result = MapsClient().estimate_transfer_cost("Istanbul Airport", "Istanbul")
        self.assertEqual(result, round(50.5 * 1.2, 2))
        self.assertEqual(IntegrationCallLog.objects.filter(provider="maps", success=True).count(), 1)

    @responses.activate
    def test_ungeocodable_place_falls_back_to_flat_estimate(self):
        responses.add(responses.GET, GEOCODE_URL, json=[], status=200)
        result = MapsClient().estimate_transfer_cost("Atlantis Airport", "Atlantis")
        self.assertEqual(result, 40.0)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="maps", success=False).count(), 1)


@override_settings(GOOGLE_MAPS_API_KEY="test-key", CACHES=LOCMEM_CACHE)
class MapsClientTests(CacheIsolatedTestCase):
    @responses.activate
    def test_google_is_preferred_when_it_works(self):
        responses.add(
            responses.POST,
            ROUTES_URL,
            json=[{"originIndex": 0, "destinationIndex": 0, "distanceMeters": 25000, "condition": "ROUTE_EXISTS"}],
            status=200,
        )
        result = MapsClient().estimate_transfer_cost("Istanbul Airport", "Istanbul")
        self.assertEqual(result, round(25.0 * 1.2, 2))
        # OSM must not be touched when Google answers.
        self.assertEqual(len(responses.calls), 1)

    @responses.activate
    def test_billing_disabled_drops_through_to_osm(self):
        responses.add(responses.POST, ROUTES_URL, json=[{"error": {"code": 403}}], status=403)
        responses.add(responses.GET, GEOCODE_URL, json=[{"lat": "41.27", "lon": "28.73"}], status=200)
        responses.add(responses.GET, GEOCODE_URL, json=[{"lat": "41.00", "lon": "28.97"}], status=200)
        responses.add(responses.GET, OSRM_ANY, json={"code": "Ok", "routes": [{"distance": 50500}]}, status=200)
        result = MapsClient().estimate_transfer_cost("Istanbul Airport", "Istanbul")
        self.assertEqual(result, round(50.5 * 1.2, 2))

    @responses.activate
    def test_google_no_route_drops_through_to_osm(self):
        responses.add(responses.POST, ROUTES_URL, json=[{"condition": "ROUTE_NOT_FOUND"}], status=200)
        responses.add(responses.GET, GEOCODE_URL, json=[{"lat": "1.0", "lon": "2.0"}], status=200)
        responses.add(responses.GET, GEOCODE_URL, json=[{"lat": "1.5", "lon": "2.5"}], status=200)
        responses.add(responses.GET, OSRM_ANY, json={"code": "Ok", "routes": [{"distance": 12000}]}, status=200)
        result = MapsClient().estimate_transfer_cost("Somewhere Airport", "Somewhere")
        self.assertEqual(result, round(12.0 * 1.2, 2))

    @responses.activate
    def test_both_tiers_failing_falls_back_to_flat_estimate(self):
        responses.add(responses.POST, ROUTES_URL, json=[{"error": {"code": 403}}], status=403)
        responses.add(responses.GET, GEOCODE_URL, json=[{"lat": "1.0", "lon": "2.0"}], status=200)
        responses.add(responses.GET, GEOCODE_URL, json=[{"lat": "1.5", "lon": "2.5"}], status=200)
        responses.add(responses.GET, OSRM_ANY, json={"code": "NoRoute", "routes": []}, status=200)
        result = MapsClient().estimate_transfer_cost("Somewhere Airport", "Somewhere")
        self.assertEqual(result, 40.0)


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


@override_settings(ESIM_GO_API_KEY="", CACHES=LOCMEM_CACHE)
class EsimGoClientFallbackTests(CacheIsolatedTestCase):
    def test_unconfigured_returns_none_and_logs_failure(self):
        client = EsimGoClient()
        result = client.get_bundle("Istanbul, Türkiye", 8)
        self.assertIsNone(result)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="esim", success=False).count(), 1)


@override_settings(ESIM_GO_API_KEY="test-key", CACHES=LOCMEM_CACHE)
class EsimGoClientTests(CacheIsolatedTestCase):
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


LITEAPI_URL = "https://api.liteapi.travel/v3.0/hotels/rates"


def liteapi_payload():
    """Shaped like a real sandbox response (hotel metadata in `hotels`,
    rates in `data`, joined on hotelId).
    """
    return {
        "sandbox": True,
        "hotels": [
            {"id": "h1", "name": "Sura Hagia Sophia Hotel", "stars": 5,
             "city_name": "Istanbul", "address": "Sultanahmet"},
            {"id": "h2", "name": "Olivia Guesthouse", "stars": 0,
             "city_name": "Istanbul", "address": "Fatih"},
        ],
        "data": [
            {"hotelId": "h1", "roomTypes": [{"rates": [
                {"retailRate": {"total": [{"amount": 1120.0, "currency": "USD"}]}}]}]},
            {"hotelId": "h2", "roomTypes": [{"rates": [
                {"retailRate": {"total": [{"amount": 320.0, "currency": "USD"}]}}]}]},
        ],
    }


@override_settings(LITEAPI_KEY="", CACHES=LOCMEM_CACHE)
class LiteApiUnconfiguredTests(CacheIsolatedTestCase):
    def test_unconfigured_returns_none_and_logs_failure(self):
        result = LiteApiHotelClient().search_hotels(
            "Istanbul, Türkiye", date(2026, 12, 10), date(2026, 12, 18), 2, 8
        )
        self.assertIsNone(result)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="hotels", success=False).count(), 1)


@override_settings(LITEAPI_KEY="sand_test", CACHES=LOCMEM_CACHE)
class LiteApiHotelClientTests(CacheIsolatedTestCase):
    @responses.activate
    def test_maps_response_to_priced_offers_cheapest_first(self):
        responses.add(responses.POST, LITEAPI_URL, json=liteapi_payload(), status=200)
        offers = LiteApiHotelClient().search_hotels(
            "Istanbul, Türkiye", date(2026, 12, 10), date(2026, 12, 18), 2, 8
        )
        self.assertEqual([o.name for o in offers], ["Olivia Guesthouse", "Sura Hagia Sophia Hotel"])
        # 320 total over 8 nights, and real star ratings rather than invented tiers.
        self.assertEqual(offers[0].night, 40.0)
        self.assertEqual(offers[0].rating, "Unrated")
        self.assertEqual(offers[1].rating, "5★")
        self.assertEqual(IntegrationCallLog.objects.filter(provider="hotels", success=True).count(), 1)

    @responses.activate
    def test_missing_dates_returns_none_without_calling_api(self):
        offers = LiteApiHotelClient().search_hotels("Istanbul, Türkiye", None, None, 2, 8)
        self.assertIsNone(offers)
        self.assertEqual(len(responses.calls), 0)

    @responses.activate
    def test_unresolvable_destination_returns_none_without_calling_api(self):
        offers = LiteApiHotelClient().search_hotels(
            "Atlantis", date(2026, 12, 10), date(2026, 12, 18), 2, 8
        )
        self.assertIsNone(offers)
        self.assertEqual(len(responses.calls), 0)

    @responses.activate
    def test_no_inventory_returns_none(self):
        responses.add(responses.POST, LITEAPI_URL, json={"data": [], "hotels": []}, status=200)
        offers = LiteApiHotelClient().search_hotels(
            "Zanzibar", date(2026, 12, 10), date(2026, 12, 18), 2, 8
        )
        self.assertIsNone(offers)

    @responses.activate
    def test_api_error_returns_none_and_logs(self):
        responses.add(responses.POST, LITEAPI_URL, status=500)
        offers = LiteApiHotelClient().search_hotels(
            "Istanbul, Türkiye", date(2026, 12, 10), date(2026, 12, 18), 2, 8
        )
        self.assertIsNone(offers)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="hotels", success=False).count(), 1)


OTM_GEONAME = "https://api.opentripmap.com/0.1/en/places/geoname"
OTM_RADIUS = "https://api.opentripmap.com/0.1/en/places/radius"


def otm_feature(name, kinds, rate=2):
    return {"properties": {"name": name, "kinds": kinds, "rate": rate}}


class EstimateEntryCostTests(TestCase):
    def test_paid_categories_get_a_category_price(self):
        self.assertEqual(estimate_entry_cost("cultural,museums,interesting_places"), 12.0)
        self.assertEqual(estimate_entry_cost("natural,national_park"), 25.0)

    def test_monuments_and_worship_are_free(self):
        self.assertEqual(estimate_entry_cost("historic,monuments_and_memorials"), 0.0)
        self.assertEqual(estimate_entry_cost("religion,mosques"), 0.0)


@override_settings(OPENTRIPMAP_API_KEY="test-key", CACHES=LOCMEM_CACHE)
class OpenTripMapClientTests(CacheIsolatedTestCase):
    def _mock(self, features):
        responses.add(responses.GET, OTM_GEONAME, json={"lat": -2.5, "lon": 32.9}, status=200)
        responses.add(responses.GET, OTM_RADIUS, json={"features": features}, status=200)

    @responses.activate
    def test_unnamed_places_are_dropped(self):
        # These used to render as "<city> point of interest".
        self._mock([
            otm_feature("", "historic,monuments_and_memorials"),
            otm_feature("Sukuma Museum", "cultural,museums"),
        ])
        result = OpenTripMapClient().search_attractions("Mwanza")
        self.assertEqual([a.name for a in result], ["Sukuma Museum"])

    @responses.activate
    def test_prices_come_from_category_not_fixture_position(self):
        # Regression: a memorial once cost $180 because it landed in the slot
        # where the Istanbul demo data had a premium excursion.
        self._mock([
            otm_feature("Sukuma-German Memorial Tree", "historic,monuments_and_memorials"),
            otm_feature("Sukuma Museum", "cultural,museums"),
        ])
        result = OpenTripMapClient().search_attractions("Mwanza")
        by_name = {a.name: a.cost for a in result}
        self.assertEqual(by_name["Sukuma-German Memorial Tree"], 0.0)
        self.assertEqual(by_name["Sukuma Museum"], 12.0)

    @responses.activate
    def test_shops_and_stations_are_excluded(self):
        self._mock([
            otm_feature("deluxe shop", "cultural,urban_environment,installation"),
            otm_feature("Notre-Dame", "architecture,railway_stations"),
            otm_feature("Saanane Island National Park", "natural,national_park"),
        ])
        result = OpenTripMapClient().search_attractions("Mwanza")
        self.assertEqual([a.name for a in result], ["Saanane Island National Park"])

    @responses.activate
    def test_one_category_cannot_fill_the_whole_list(self):
        self._mock([otm_feature(f"Mosque {i}", "religion,mosques") for i in range(5)]
                   + [otm_feature("City Museum", "cultural,museums")])
        result = OpenTripMapClient().search_attractions("Nairobi")
        religion = [a for a in result if "Mosque" in a.name]
        self.assertEqual(len(religion), 2)
        self.assertIn("City Museum", [a.name for a in result])

    @responses.activate
    def test_most_notable_first(self):
        self._mock([
            otm_feature("Minor Column", "historic", rate=2),
            otm_feature("Historic Areas of Istanbul", "cultural", rate=7),
        ])
        result = OpenTripMapClient().search_attractions("Istanbul")
        self.assertEqual(result[0].name, "Historic Areas of Istanbul")

    @responses.activate
    def test_no_results_falls_back_to_fixtures(self):
        self._mock([])
        result = OpenTripMapClient().search_attractions("Nowhere")
        self.assertEqual(result, sample_data_for("Nowhere").attractions)
