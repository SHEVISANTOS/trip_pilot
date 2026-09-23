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
from integrations.stayapi import StayApiHotelClient
from integrations.maps import MapsClient
from integrations.models import IntegrationCallLog
from integrations.serpapi import (
    SerpApiAttractionsClient,
    SerpApiFlightsClient,
    SerpApiHotelsClient,
    SerpApiSearchClient,
)
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


def tp_row(airline="TK", price=756, transfers=1, duration_to=655, link=None):
    row = {
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
    if link is not None:
        row["link"] = link
    return row


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
    def test_relative_link_becomes_a_real_aviasales_booking_url(self):
        responses.add(responses.GET, TP_URL, json={"data": [tp_row(link="/searches/DAR1005IST1805?...")]}, status=200)
        offers = TravelpayoutsClient().search_flights("Dar es Salaam", "Istanbul, Türkiye", 1)
        self.assertEqual(offers[0].booking_url, "https://www.aviasales.com/searches/DAR1005IST1805?...")

    @responses.activate
    def test_missing_link_leaves_booking_url_empty(self):
        responses.add(responses.GET, TP_URL, json={"data": [tp_row()]}, status=200)
        offers = TravelpayoutsClient().search_flights("Dar es Salaam", "Istanbul, Türkiye", 1)
        self.assertEqual(offers[0].booking_url, "")

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


STAYAPI_LOOKUP_URL = "https://api.stayapi.com/v1/booking/destinations/lookup"
STAYAPI_SEARCH_URL = "https://api.stayapi.com/v1/booking/search"


def stayapi_lookup_payload(dest_type="CITY", dest_id=-755070, include_city_suggestion=True):
    """The live API's actual lookup shape — confirmed against the real key,
    not the (stale) documented one. `dest_type` here reproduces the real
    incident: the default match for some destinations (Zanzibar) is a
    REGION/AIRPORT/HOTEL, and only a CITY-typed suggestion further down
    actually has search inventory.
    """
    suggestions = [{"dest_id": dest_id, "dest_type": dest_type, "label": "Primary match"}]
    if include_city_suggestion and dest_type != "CITY":
        suggestions.append({"dest_id": -2574823, "dest_type": "CITY", "label": "The actual city"})
    return {"success": True, "dest_id": dest_id, "dest_type": dest_type, "suggestions": suggestions}


def stayapi_hotel(name, amount, stars=None, score=None, sold_out=False):
    return {
        "name": name,
        "display_location": "Test City",
        "address": "123 Test Street",
        "star_rating": stars,
        "price": {"amount": amount, "currency": "USD"},
        "rating": {"score": score, "review_count": 100} if score else {},
        "is_sold_out": sold_out,
    }


@override_settings(STAYAPI_KEY="", CACHES=LOCMEM_CACHE)
class StayApiUnconfiguredTests(CacheIsolatedTestCase):
    def test_unconfigured_returns_none_and_logs_failure(self):
        result = StayApiHotelClient().search_hotels(
            "Istanbul, Türkiye", date(2026, 12, 10), date(2026, 12, 18), 2, 8
        )
        self.assertIsNone(result)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="hotels_stayapi", success=False).count(), 1)


@override_settings(STAYAPI_KEY="sk_live_test", CACHES=LOCMEM_CACHE)
class StayApiHotelClientTests(CacheIsolatedTestCase):
    @responses.activate
    def test_maps_response_to_priced_offers_cheapest_first(self):
        responses.add(responses.GET, STAYAPI_LOOKUP_URL, json=stayapi_lookup_payload(), status=200)
        responses.add(
            responses.GET,
            STAYAPI_SEARCH_URL,
            json={"data": {"hotels": [
                stayapi_hotel("Expensive Hotel", 1120.0, stars=5),
                stayapi_hotel("Cheap Guesthouse", 320.0, score=8.8),
            ]}},
            status=200,
        )
        offers = StayApiHotelClient().search_hotels(
            "Istanbul, Türkiye", date(2026, 12, 10), date(2026, 12, 18), 2, 8
        )
        self.assertEqual([o.name for o in offers], ["Cheap Guesthouse", "Expensive Hotel"])
        self.assertEqual(offers[0].night, 40.0)  # 320 / 8 nights
        self.assertEqual(offers[0].rating, "8.8/10")  # no star_rating -> falls back to review score
        self.assertEqual(offers[1].rating, "5★")
        self.assertEqual(IntegrationCallLog.objects.filter(provider="hotels_stayapi", success=True).count(), 1)

    @responses.activate
    def test_destination_search_url_is_attached_to_every_hotel(self):
        responses.add(responses.GET, STAYAPI_LOOKUP_URL, json=stayapi_lookup_payload(), status=200)
        responses.add(
            responses.GET,
            STAYAPI_SEARCH_URL,
            json={
                "url": "https://www.booking.com/searchresults.html?ss=Istanbul&aid=304142",
                "data": {"hotels": [
                    stayapi_hotel("Hotel A", 320.0),
                    stayapi_hotel("Hotel B", 200.0),
                ]},
            },
            status=200,
        )
        offers = StayApiHotelClient().search_hotels(
            "Istanbul, Türkiye", date(2026, 12, 10), date(2026, 12, 18), 2, 8
        )
        for offer in offers:
            self.assertEqual(offer.booking_url, "https://www.booking.com/searchresults.html?ss=Istanbul&aid=304142")

    @responses.activate
    def test_sold_out_hotels_are_excluded(self):
        responses.add(responses.GET, STAYAPI_LOOKUP_URL, json=stayapi_lookup_payload(), status=200)
        responses.add(
            responses.GET,
            STAYAPI_SEARCH_URL,
            json={"data": {"hotels": [
                stayapi_hotel("Sold Out Hotel", 100.0, sold_out=True),
                stayapi_hotel("Available Hotel", 200.0),
            ]}},
            status=200,
        )
        offers = StayApiHotelClient().search_hotels(
            "Istanbul, Türkiye", date(2026, 12, 10), date(2026, 12, 18), 2, 8
        )
        self.assertEqual([o.name for o in offers], ["Available Hotel"])

    @responses.activate
    def test_region_default_match_falls_through_to_city_suggestion(self):
        # Reproduces the real Zanzibar incident: the default dest_id is a
        # REGION that the search endpoint returns zero hotels for, but a
        # CITY-typed suggestion two entries down actually has inventory.
        responses.add(
            responses.GET,
            STAYAPI_LOOKUP_URL,
            json=stayapi_lookup_payload(dest_type="REGION", dest_id=5140),
            status=200,
        )

        def search_callback(request):
            assert "dest_id=-2574823" in request.url, f"used the REGION id, not the CITY one: {request.url}"
            return (200, {}, '{"data": {"hotels": [%s]}}' % '{"name": "Real Hotel", "price": {"amount": 100.0}}')

        responses.add_callback(responses.GET, STAYAPI_SEARCH_URL, callback=search_callback)
        offers = StayApiHotelClient().search_hotels("Zanzibar", date(2026, 12, 10), date(2026, 12, 18), 2, 8)
        self.assertEqual(offers[0].name, "Real Hotel")

    @responses.activate
    def test_missing_dates_returns_none_without_calling_api(self):
        offers = StayApiHotelClient().search_hotels("Istanbul, Türkiye", None, None, 2, 8)
        self.assertIsNone(offers)
        self.assertEqual(len(responses.calls), 0)

    @responses.activate
    def test_unresolvable_destination_returns_none(self):
        responses.add(responses.GET, STAYAPI_LOOKUP_URL, json={"success": False}, status=200)
        offers = StayApiHotelClient().search_hotels(
            "Atlantis", date(2026, 12, 10), date(2026, 12, 18), 2, 8
        )
        self.assertIsNone(offers)

    @responses.activate
    def test_no_inventory_returns_none(self):
        responses.add(responses.GET, STAYAPI_LOOKUP_URL, json=stayapi_lookup_payload(), status=200)
        responses.add(responses.GET, STAYAPI_SEARCH_URL, json={"data": {"hotels": []}}, status=200)
        offers = StayApiHotelClient().search_hotels(
            "Istanbul, Türkiye", date(2026, 12, 10), date(2026, 12, 18), 2, 8
        )
        self.assertIsNone(offers)

    @responses.activate
    def test_api_error_returns_none_and_logs(self):
        responses.add(responses.GET, STAYAPI_LOOKUP_URL, status=500)
        offers = StayApiHotelClient().search_hotels(
            "Istanbul, Türkiye", date(2026, 12, 10), date(2026, 12, 18), 2, 8
        )
        self.assertIsNone(offers)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="hotels_stayapi", success=False).count(), 1)


OTM_GEONAME = "https://api.opentripmap.com/0.1/en/places/geoname"
OTM_RADIUS = "https://api.opentripmap.com/0.1/en/places/radius"


def otm_feature(name, kinds, rate=2, wikidata=None, osm=None):
    props = {"name": name, "kinds": kinds, "rate": rate}
    if wikidata is not None:
        props["wikidata"] = wikidata
    if osm is not None:
        props["osm"] = osm
    return {"properties": props}


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
    def test_wikidata_link_is_preferred_over_osm(self):
        self._mock([
            otm_feature("Sukuma Museum", "cultural,museums", wikidata="Q1187329", osm="node/1908677124"),
        ])
        result = OpenTripMapClient().search_attractions("Mwanza")
        self.assertEqual(result[0].booking_url, "https://www.wikidata.org/wiki/Q1187329")

    @responses.activate
    def test_osm_link_used_when_no_wikidata_tag(self):
        self._mock([
            otm_feature("Sukuma Museum", "cultural,museums", osm="node/1908677124"),
        ])
        result = OpenTripMapClient().search_attractions("Mwanza")
        self.assertEqual(result[0].booking_url, "https://www.openstreetmap.org/node/1908677124")

    @responses.activate
    def test_no_link_when_neither_tag_present(self):
        self._mock([otm_feature("Sukuma Museum", "cultural,museums")])
        result = OpenTripMapClient().search_attractions("Mwanza")
        self.assertEqual(result[0].booking_url, "")

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


SERPAPI_URL = "https://serpapi.com/search.json"


def serp_flight_search_response(prices=(877,), google_flights_url="https://www.google.com/travel/flights?x"):
    best_flights = []
    for i, price in enumerate(prices):
        best_flights.append(
            {
                "flights": [
                    {
                        "departure_airport": {"id": "DAR", "time": "2027-05-10 15:25"},
                        "arrival_airport": {"id": "IST", "time": "2027-05-10 22:00"},
                        "airline": "Emirates",
                    }
                ],
                "layovers": [{"id": "DXB", "duration": 120}] if i == 0 else [],
                "total_duration": 700,
                "price": price,
                "type": "Round trip",
                "departure_token": f"token-{i}",
            }
        )
    return {
        "search_metadata": {"status": "Success", "google_flights_url": google_flights_url},
        "best_flights": best_flights,
        "other_flights": [],
    }


@override_settings(SERPAPI_KEY="", CACHES=LOCMEM_CACHE)
class SerpApiUnconfiguredTests(CacheIsolatedTestCase):
    def test_flights_unconfigured_returns_none_and_logs(self):
        result = SerpApiFlightsClient().search_flights(
            "Dar es Salaam", "Istanbul, Türkiye", 2, date(2027, 5, 10), date(2027, 5, 18)
        )
        self.assertIsNone(result)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="serpapi_flights", success=False).count(), 1)

    def test_hotels_unconfigured_returns_none_and_logs(self):
        result = SerpApiHotelsClient().search_hotels(
            "Istanbul, Türkiye", date(2027, 5, 10), date(2027, 5, 18), 2, 8
        )
        self.assertIsNone(result)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="serpapi_hotels", success=False).count(), 1)

    def test_search_unconfigured_returns_none_and_logs(self):
        result = SerpApiSearchClient().resolve_link("Hagia Sophia Istanbul")
        self.assertIsNone(result)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="serpapi_search", success=False).count(), 1)

    def test_attractions_unconfigured_returns_none_and_logs(self):
        result = SerpApiAttractionsClient().search_attractions("Cairo, Egypt")
        self.assertIsNone(result)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="serpapi_attractions", success=False).count(), 1)


@override_settings(SERPAPI_KEY="test-serp-key", CACHES=LOCMEM_CACHE)
class SerpApiFlightsClientTests(CacheIsolatedTestCase):
    @responses.activate
    def test_maps_response_to_flight_offers_with_shared_booking_url(self):
        responses.add(responses.GET, SERPAPI_URL, json=serp_flight_search_response(prices=[877, 950]), status=200)
        offers = SerpApiFlightsClient().search_flights(
            "Dar es Salaam", "Istanbul, Türkiye", 2, date(2027, 5, 10), date(2027, 5, 18)
        )
        self.assertEqual(len(offers), 2)
        self.assertEqual(offers[0].price, 877.0)
        self.assertEqual(offers[0].airline, "Emirates")
        self.assertEqual(offers[0].route, "DAR → IST → DAR")
        self.assertEqual(offers[0].label, "Cheapest")
        for offer in offers:
            self.assertEqual(offer.booking_url, "https://www.google.com/travel/flights?x")

    @responses.activate
    def test_sorted_cheapest_first_across_best_and_other_flights(self):
        payload = serp_flight_search_response(prices=[950])
        payload["other_flights"] = [
            {
                "flights": [
                    {"departure_airport": {"id": "DAR"}, "arrival_airport": {"id": "IST"}, "airline": "Turkish Airlines"}
                ],
                "layovers": [],
                "total_duration": 600,
                "price": 700,
                "type": "Round trip",
            }
        ]
        responses.add(responses.GET, SERPAPI_URL, json=payload, status=200)
        offers = SerpApiFlightsClient().search_flights(
            "Dar es Salaam", "Istanbul, Türkiye", 1, date(2027, 5, 10), date(2027, 5, 18)
        )
        self.assertEqual(offers[0].price, 700.0)
        self.assertEqual(offers[0].airline, "Turkish Airlines")
        self.assertEqual(offers[0].stops, "Direct")
        self.assertEqual(offers[1].stops, "1 stop")

    @responses.activate
    def test_missing_dates_returns_none_without_calling_api(self):
        offers = SerpApiFlightsClient().search_flights("Dar es Salaam", "Istanbul, Türkiye", 2, None, None)
        self.assertIsNone(offers)
        self.assertEqual(len(responses.calls), 0)

    @responses.activate
    def test_unresolvable_route_returns_none_without_calling_api(self):
        offers = SerpApiFlightsClient().search_flights(
            "Atlantis", "Shangri-La", 2, date(2027, 5, 10), date(2027, 5, 18)
        )
        self.assertIsNone(offers)
        self.assertEqual(len(responses.calls), 0)

    @responses.activate
    def test_api_error_returns_none_and_logs(self):
        responses.add(responses.GET, SERPAPI_URL, status=500)
        offers = SerpApiFlightsClient().search_flights(
            "Dar es Salaam", "Istanbul, Türkiye", 2, date(2027, 5, 10), date(2027, 5, 18)
        )
        self.assertIsNone(offers)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="serpapi_flights", success=False).count(), 1)

    @responses.activate
    def test_non_success_status_returns_none(self):
        responses.add(responses.GET, SERPAPI_URL, json={"search_metadata": {"status": "Error"}}, status=200)
        offers = SerpApiFlightsClient().search_flights(
            "Dar es Salaam", "Istanbul, Türkiye", 2, date(2027, 5, 10), date(2027, 5, 18)
        )
        self.assertIsNone(offers)


def serp_hotel_property(name, night, total, prop_type="hotel", stars=None, rating=None):
    prop = {
        "type": prop_type,
        "name": name,
        "rate_per_night": {"extracted_lowest": night},
        "total_rate": {"extracted_lowest": total},
        "description": f"{name} description",
    }
    if stars is not None:
        prop["extracted_hotel_class"] = stars
    if rating is not None:
        prop["overall_rating"] = rating
    return prop


def serp_hotel_search_response(properties, google_hotels_url="https://www.google.com/travel/search?q=Istanbul"):
    return {"search_metadata": {"status": "Success", "google_hotels_url": google_hotels_url}, "properties": properties}


@override_settings(SERPAPI_KEY="test-serp-key", CACHES=LOCMEM_CACHE)
class SerpApiHotelsClientTests(CacheIsolatedTestCase):
    @responses.activate
    def test_maps_response_to_priced_offers_cheapest_first_with_shared_url(self):
        responses.add(
            responses.GET,
            SERPAPI_URL,
            json=serp_hotel_search_response([
                serp_hotel_property("JW Marriott", 319, 2554, stars=5),
                serp_hotel_property("Cheers Lighthouse", 23, 184, stars=3),
            ]),
            status=200,
        )
        offers = SerpApiHotelsClient().search_hotels(
            "Istanbul, Türkiye", date(2027, 5, 10), date(2027, 5, 18), 2, 8
        )
        self.assertEqual([o.name for o in offers], ["Cheers Lighthouse", "JW Marriott"])
        self.assertEqual(offers[0].night, 23.0)
        self.assertEqual(offers[0].rating, "3★")
        for offer in offers:
            self.assertEqual(offer.booking_url, "https://www.google.com/travel/search?q=Istanbul")

    @responses.activate
    def test_vacation_rentals_are_excluded(self):
        responses.add(
            responses.GET,
            SERPAPI_URL,
            json=serp_hotel_search_response([
                serp_hotel_property("Sea view apartment", 259, 2075, prop_type="vacation rental"),
                serp_hotel_property("La Quinta", 62, 493, stars=5),
            ]),
            status=200,
        )
        offers = SerpApiHotelsClient().search_hotels(
            "Istanbul, Türkiye", date(2027, 5, 10), date(2027, 5, 18), 2, 8
        )
        self.assertEqual([o.name for o in offers], ["La Quinta"])

    @responses.activate
    def test_rating_falls_back_to_overall_rating_out_of_five_when_no_star_class(self):
        responses.add(
            responses.GET,
            SERPAPI_URL,
            json=serp_hotel_search_response([serp_hotel_property("Boutique Stay", 100, 800, rating=4.6)]),
            status=200,
        )
        offers = SerpApiHotelsClient().search_hotels(
            "Istanbul, Türkiye", date(2027, 5, 10), date(2027, 5, 18), 2, 8
        )
        self.assertEqual(offers[0].rating, "4.6/5")

    @responses.activate
    def test_missing_dates_returns_none_without_calling_api(self):
        offers = SerpApiHotelsClient().search_hotels("Istanbul, Türkiye", None, None, 2, 8)
        self.assertIsNone(offers)
        self.assertEqual(len(responses.calls), 0)

    @responses.activate
    def test_no_properties_returns_none(self):
        responses.add(responses.GET, SERPAPI_URL, json=serp_hotel_search_response([]), status=200)
        offers = SerpApiHotelsClient().search_hotels(
            "Istanbul, Türkiye", date(2027, 5, 10), date(2027, 5, 18), 2, 8
        )
        self.assertIsNone(offers)

    @responses.activate
    def test_api_error_returns_none_and_logs(self):
        responses.add(responses.GET, SERPAPI_URL, status=500)
        offers = SerpApiHotelsClient().search_hotels(
            "Istanbul, Türkiye", date(2027, 5, 10), date(2027, 5, 18), 2, 8
        )
        self.assertIsNone(offers)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="serpapi_hotels", success=False).count(), 1)


@override_settings(SERPAPI_KEY="test-serp-key", CACHES=LOCMEM_CACHE)
class SerpApiSearchClientTests(CacheIsolatedTestCase):
    @responses.activate
    def test_returns_top_organic_result_link(self):
        responses.add(
            responses.GET,
            SERPAPI_URL,
            json={
                "search_metadata": {"status": "Success"},
                "organic_results": [
                    {"position": 1, "title": "Hagia Sophia", "link": "https://en.wikipedia.org/wiki/Hagia_Sophia"},
                ],
            },
            status=200,
        )
        link = SerpApiSearchClient().resolve_link("Hagia Sophia Istanbul")
        self.assertEqual(link, "https://en.wikipedia.org/wiki/Hagia_Sophia")

    @responses.activate
    def test_no_organic_results_returns_none(self):
        responses.add(
            responses.GET,
            SERPAPI_URL,
            json={"search_metadata": {"status": "Success"}, "organic_results": []},
            status=200,
        )
        link = SerpApiSearchClient().resolve_link("Some Obscure Spot Istanbul")
        self.assertIsNone(link)

    @responses.activate
    def test_api_error_returns_none_and_logs(self):
        responses.add(responses.GET, SERPAPI_URL, status=500)
        link = SerpApiSearchClient().resolve_link("Hagia Sophia Istanbul")
        self.assertIsNone(link)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="serpapi_search", success=False).count(), 1)


def serp_sight(title, price=None, rating=4.5, reviews=1000, description="Open", link=None):
    sight = {"title": title, "description": description, "rating": rating, "reviews": reviews}
    if price is not None:
        sight["price"] = f"${price}"
        sight["extracted_price"] = price
    if link is not None:
        sight["link"] = link
    return sight


def serp_attractions_response(sights):
    return {"search_metadata": {"status": "Success"}, "top_sights": {"sights": sights}}


@override_settings(SERPAPI_KEY="test-serp-key", CACHES=LOCMEM_CACHE)
class SerpApiAttractionsClientTests(CacheIsolatedTestCase):
    @responses.activate
    def test_maps_top_sights_to_attractions(self):
        responses.add(
            responses.GET,
            SERPAPI_URL,
            json=serp_attractions_response([
                serp_sight("Egyptian Museum", price=10.61, rating=4.5, reviews=65000, link="https://google.example/museum"),
                serp_sight("Khan el-Khalili", price=None, rating=4.4, reviews=76000, description="Open"),
            ]),
            status=200,
        )
        result = SerpApiAttractionsClient().search_attractions("Cairo, Egypt")
        self.assertEqual(result[0].name, "Egyptian Museum")
        self.assertEqual(result[0].cost, 10.61)
        self.assertEqual(result[0].booking_url, "https://google.example/museum")
        self.assertIn("★", result[0].desc)
        # No extracted_price ("Free") -> cost 0.0, and no link field -> "".
        self.assertEqual(result[1].cost, 0.0)
        self.assertEqual(result[1].booking_url, "")

    @responses.activate
    def test_generic_open_closed_description_is_replaced(self):
        responses.add(
            responses.GET,
            SERPAPI_URL,
            json=serp_attractions_response([serp_sight("Some Place", description="Open")]),
            status=200,
        )
        result = SerpApiAttractionsClient().search_attractions("Cairo, Egypt")
        self.assertNotIn("Open", result[0].desc)
        self.assertIn("Popular attraction", result[0].desc)

    @responses.activate
    def test_duplicate_titles_are_deduplicated(self):
        responses.add(
            responses.GET,
            SERPAPI_URL,
            json=serp_attractions_response([serp_sight("Egyptian Museum"), serp_sight("Egyptian Museum")]),
            status=200,
        )
        result = SerpApiAttractionsClient().search_attractions("Cairo, Egypt")
        self.assertEqual(len(result), 1)

    @responses.activate
    def test_caps_at_six_and_marks_the_sixth_optional(self):
        responses.add(
            responses.GET,
            SERPAPI_URL,
            json=serp_attractions_response([serp_sight(f"Sight {i}") for i in range(10)]),
            status=200,
        )
        result = SerpApiAttractionsClient().search_attractions("Cairo, Egypt")
        self.assertEqual(len(result), 6)
        for a in result[:5]:
            self.assertFalse(a.optional)
        self.assertTrue(result[5].optional)

    @responses.activate
    def test_no_sights_returns_none(self):
        responses.add(responses.GET, SERPAPI_URL, json=serp_attractions_response([]), status=200)
        result = SerpApiAttractionsClient().search_attractions("Nowhere")
        self.assertIsNone(result)

    @responses.activate
    def test_api_error_returns_none_and_logs(self):
        responses.add(responses.GET, SERPAPI_URL, status=500)
        result = SerpApiAttractionsClient().search_attractions("Cairo, Egypt")
        self.assertIsNone(result)
        self.assertEqual(IntegrationCallLog.objects.filter(provider="serpapi_attractions", success=False).count(), 1)
