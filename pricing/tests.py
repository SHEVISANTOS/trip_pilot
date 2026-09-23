"""Unit tests for the budget engine and optimizer — pure Python, no DB or
network access. Run with: python manage.py test pricing
"""
import unittest
from datetime import date
from types import SimpleNamespace

from integrations.dataclasses import Attraction, EsimBundle, ExchangeRate, FlightOffer, HotelOffer, VisaInfo
from pricing.budget import (
    COST_OF_LIVING_MAX,
    COST_OF_LIVING_MIN,
    TripInputs,
    build_plan,
    calculate_nights,
    cost_of_living_index,
    estimate_flights,
    estimate_hotels,
    estimate_sim_cost,
    fill_missing_booking_urls,
)
from pricing.itinerary import build_itinerary
from pricing.optimizer import optimize


class StubFlights:
    """Mirrors TravelpayoutsClient: per-person round-trip price, and returns
    None when it has nothing (build_plan then uses the fixture flights).
    """

    def search_flights(self, origin, destination, adults, start_date=None, end_date=None):
        return [FlightOffer("Test Air", f"{origin} → {destination}", "1 stop", "10 hrs", 550, "Cheapest")]


class StubActivities:
    def search_attractions(self, destination):
        return [
            Attraction("Landmark A", 30, "desc"),
            Attraction("Landmark B", 45, "desc"),
            Attraction("Landmark C", 55, "desc"),
            Attraction("Free Market", 0, "desc"),
            Attraction("Viewpoint", 20, "desc"),
            Attraction("Premium Excursion", 180, "desc", optional=True),
        ]


class StubActivitiesSerp:
    """Mirrors SerpApiAttractionsClient (tier 1); None means build_plan()
    falls through to OpenTripMap (StubActivities).
    """

    def search_attractions(self, destination):
        return None


class StubVisa:
    def get_visa_info(self, nationality, destination):
        return VisaInfo("Tourist entry requirement", "Verify before departure.", "Check stay length", 60, "VERIFY")


class StubFlightsBackup:
    """Mirrors SerpApiFlightsClient (tier 2); None means build_plan() falls
    through to estimate_flights()/fixtures.
    """

    def search_flights(self, origin, destination, adults, start_date=None, end_date=None):
        return None


class StubHotels:
    """Mirrors LiteApiHotelClient (tier 1); None means no live inventory, so
    build_plan() tries hotels_serp (tier 2), then hotels_backup (tier 3),
    then estimate_hotels().
    """

    def search_hotels(self, destination, checkin, checkout, adults, nights, nationality="", limit=3):
        return None


class StubHotelsSerp:
    """Mirrors SerpApiHotelsClient (tier 2)."""

    def search_hotels(self, destination, checkin, checkout, adults, nights, nationality="", limit=3):
        return None


class StubHotelsBackup:
    """Mirrors StayApiHotelClient (tier 3)."""

    def search_hotels(self, destination, checkin, checkout, adults, nights, nationality="", limit=3):
        return None


class StubAttractionLinks:
    """Mirrors SerpApiSearchClient; None means fill_missing_booking_urls()
    falls back to the generic search-query URL.
    """

    def resolve_link(self, query):
        return None


class StubEsim:
    def get_bundle(self, destination, nights):
        return None  # no live bundle -> build_plan() falls back to estimate_sim_cost()


class StubMaps:
    def estimate_transfer_cost(self, origin, destination):
        return 40.0  # one-way; matches integrations.maps.MapsClient.FALLBACK_TRANSFER_COST


class StubExchange:
    """base==target -> 1.0, matching the real ExchangeRateClient's short
    circuit, so every existing USD-default test is unaffected. Anything else
    returns a distinguishable 0.9 for CurrencyConversionTests to check against.
    """

    def get_rate(self, base, target):
        rate = 1.0 if base == target else 0.9
        return ExchangeRate(base=base, target=target, rate=rate)


def make_clients():
    return SimpleNamespace(
        flights=StubFlights(),
        flights_backup=StubFlightsBackup(),
        activities=StubActivities(),
        activities_serp=StubActivitiesSerp(),
        visa=StubVisa(),
        esim=StubEsim(),
        hotels=StubHotels(),
        hotels_serp=StubHotelsSerp(),
        hotels_backup=StubHotelsBackup(),
        attraction_links=StubAttractionLinks(),
        maps=StubMaps(),
        exchange=StubExchange(),
    )


def make_inputs(**overrides):
    defaults = dict(
        departure="Dar es Salaam",
        destination="Istanbul, Türkiye",
        nationality="Tanzanian",
        adults=2,
        children=1,
        start_date=date(2027, 5, 10),
        end_date=date(2027, 5, 18),
        travel_style="balanced",
        hotel_preference="3–4 Star Hotel",
        currency="USD",
        budget=5000,
    )
    defaults.update(overrides)
    return TripInputs(**defaults)


class CalculateNightsTests(unittest.TestCase):
    def test_uses_date_difference(self):
        self.assertEqual(calculate_nights(date(2027, 5, 10), date(2027, 5, 18)), 8)

    def test_falls_back_to_default_when_dates_missing(self):
        self.assertEqual(calculate_nights(None, None), 8)

    def test_falls_back_when_end_before_start(self):
        self.assertEqual(calculate_nights(date(2027, 5, 18), date(2027, 5, 10)), 8)


class EstimateSimCostTests(unittest.TestCase):
    def test_asia_is_cheaper_than_africa_for_same_length(self):
        asia_cost, _ = estimate_sim_cost("Bangkok, Thailand", 8)
        africa_cost, _ = estimate_sim_cost("Nairobi, Kenya", 8)
        self.assertLess(asia_cost, africa_cost)

    def test_scales_with_trip_length_up_to_cap(self):
        short_cost, _ = estimate_sim_cost("Istanbul, Türkiye", 3)
        long_cost, _ = estimate_sim_cost("Istanbul, Türkiye", 10)
        self.assertLess(short_cost, long_cost)

    def test_caps_at_fifteen_billed_days(self):
        capped_cost, _ = estimate_sim_cost("Istanbul, Türkiye", 15)
        over_cap_cost, _ = estimate_sim_cost("Istanbul, Türkiye", 40)
        self.assertEqual(capped_cost, over_cap_cost)

    def test_unresolvable_destination_uses_default_rate(self):
        cost, detail = estimate_sim_cost("Some Made Up Place", 5)
        self.assertEqual(cost, round(3.0 * 5))
        self.assertIn("Standard", detail)


class BuildPlanTests(unittest.TestCase):
    def test_people_and_nights(self):
        plan = build_plan(make_inputs(), make_clients())
        self.assertEqual(plan.people, 3)
        self.assertEqual(plan.nights, 8)

    def test_ten_line_items_and_total_matches_sum(self):
        plan = build_plan(make_inputs(), make_clients())
        self.assertEqual(len(plan.items), 10)
        self.assertEqual(plan.total, sum(i.amount for i in plan.items))

    def test_remaining_is_budget_minus_total(self):
        plan = build_plan(make_inputs(), make_clients())
        self.assertEqual(plan.remaining, plan.inputs.budget - plan.total)

    def test_attractions_cost_excludes_optional_and_style_multiplier(self):
        plan = build_plan(make_inputs(travel_style="balanced"), make_clients())
        # 30 + 45 + 55 + 0 + 20 (Premium Excursion is optional=True, excluded)
        self.assertEqual(plan.attractions_cost, 150)

    def test_serpapi_attractions_is_tried_first_and_bypasses_opentripmap(self):
        clients = make_clients()
        clients.activities_serp.search_attractions = lambda destination: [
            Attraction("Egyptian Museum", 10.61, "Museum (Google).", booking_url="https://google.example/museum"),
        ]
        clients.activities.search_attractions = lambda destination: (_ for _ in ()).throw(
            AssertionError("OpenTripMap should not be called when SerpApi (tier 1) has attractions")
        )
        plan = build_plan(make_inputs(), clients)
        self.assertEqual(plan.attractions[0].name, "Egyptian Museum")
        self.assertEqual(plan.attractions[0].booking_url, "https://google.example/museum")

    def test_opentripmap_is_used_when_serpapi_has_nothing(self):
        plan = build_plan(make_inputs(), make_clients())
        # activities_serp (tier 1) returns None by default; StubActivities (tier 2) has data.
        self.assertEqual(plan.attractions[0].name, "Landmark A")

    def test_luxury_style_costs_more_than_budget_style(self):
        luxury = build_plan(make_inputs(travel_style="luxury"), make_clients())
        budget = build_plan(make_inputs(travel_style="budget"), make_clients())
        self.assertGreater(luxury.total, budget.total)

    def test_over_budget_flags_correctly(self):
        plan = build_plan(make_inputs(budget=100, travel_style="luxury"), make_clients())
        self.assertFalse(plan.within_budget)
        self.assertLess(plan.remaining, 0)

    def test_live_esim_bundle_is_used_over_static_estimate(self):
        clients = make_clients()
        clients.esim.get_bundle = lambda destination, nights: EsimBundle(
            name="esim_2GB_15D_TR_V2", description="2GB, 15 days", data_mb=2000,
            unlimited=False, duration_days=15, price=2.1,
        )
        plan = build_plan(make_inputs(), clients)
        self.assertEqual(plan.sim_cost, 2)
        self.assertIn("2GB", [i.detail for i in plan.items if i.label == "SIM/eSIM"][0])

    def test_no_live_bundle_falls_back_to_static_estimate(self):
        plan = build_plan(make_inputs(destination="Istanbul, Türkiye"), make_clients())
        expected_cost, _ = estimate_sim_cost("Istanbul, Türkiye", plan.nights)
        self.assertEqual(plan.sim_cost, expected_cost)

    def test_transfer_cost_is_double_the_one_way_maps_estimate(self):
        plan = build_plan(make_inputs(travel_style="balanced"), make_clients())
        # StubMaps returns 40.0 one-way; balanced style has multiplier 1.0.
        self.assertEqual(plan.transfer_cost, round(40.0 * 2 * 1.0))

    def test_food_and_transport_scale_with_the_actual_hotel_rate(self):
        cheap_clients = make_clients()
        cheap_clients.hotels.search_hotels = lambda *a, **kw: [
            HotelOffer("Cheap Hotel", "Centre", "3★", 40.0, 320.0, "")
        ]
        pricey_clients = make_clients()
        pricey_clients.hotels.search_hotels = lambda *a, **kw: [
            HotelOffer("Pricey Hotel", "Centre", "5★", 250.0, 2000.0, "")
        ]
        cheap_plan = build_plan(make_inputs(), cheap_clients)
        pricey_plan = build_plan(make_inputs(), pricey_clients)
        self.assertLess(cheap_plan.food_cost, pricey_plan.food_cost)
        self.assertLess(cheap_plan.transport_cost, pricey_plan.transport_cost)
        self.assertIn("cost-of-living", [i.detail for i in cheap_plan.items if i.label == "Food"][0])


class CostOfLivingIndexTests(unittest.TestCase):
    def test_baseline_rate_gives_index_of_one(self):
        hotels = [HotelOffer("H", "A", "4★", 100.0, 800.0, "")]
        self.assertEqual(cost_of_living_index(hotels), 1.0)

    def test_expensive_hotels_raise_the_index(self):
        hotels = [HotelOffer("H", "A", "5★", 200.0, 1600.0, "")]
        self.assertEqual(cost_of_living_index(hotels), 2.0)

    def test_cheap_hotels_lower_the_index(self):
        hotels = [HotelOffer("H", "A", "3★", 50.0, 400.0, "")]
        self.assertEqual(cost_of_living_index(hotels), 0.5)

    def test_averages_across_multiple_hotels(self):
        hotels = [
            HotelOffer("A", "X", "5★", 200.0, 1600.0, ""),
            HotelOffer("B", "X", "3★", 40.0, 320.0, ""),
        ]
        self.assertEqual(cost_of_living_index(hotels), 1.2)  # avg 120 / 100

    def test_clamps_extreme_rates(self):
        very_cheap = [HotelOffer("H", "A", "1★", 5.0, 40.0, "")]
        very_expensive = [HotelOffer("H", "A", "5★", 5000.0, 40000.0, "")]
        self.assertEqual(cost_of_living_index(very_cheap), COST_OF_LIVING_MIN)
        self.assertEqual(cost_of_living_index(very_expensive), COST_OF_LIVING_MAX)

    def test_no_hotels_defaults_to_one(self):
        self.assertEqual(cost_of_living_index([]), 1.0)


class EstimateHotelsTests(unittest.TestCase):
    def test_rates_are_region_calibrated(self):
        asia = estimate_hotels("Istanbul, Türkiye", 8)
        europe = estimate_hotels("Paris, France", 8)
        self.assertLess(asia[0].night, europe[0].night)

    def test_tiers_descend_and_total_matches_nights(self):
        hotels = estimate_hotels("Nairobi, Kenya", 5)
        self.assertGreater(hotels[0].night, hotels[1].night)
        self.assertGreater(hotels[1].night, hotels[2].night)
        self.assertEqual(hotels[0].total, round(hotels[0].night * 5, 2))

    def test_live_rates_are_preferred_over_the_estimate(self):
        clients = make_clients()
        clients.hotels.search_hotels = lambda *a, **kw: [
            HotelOffer("Sura Hagia Sophia Hotel", "Istanbul", "5★", 80.23, 641.84, "Sultanahmet"),
        ]
        plan = build_plan(make_inputs(), clients)
        self.assertEqual(plan.hotels[0].name, "Sura Hagia Sophia Hotel")
        self.assertEqual(plan.hotel_cost, round(80.23 * 8))

    def test_no_live_inventory_falls_back_to_generic_bands(self):
        plan = build_plan(make_inputs(destination="Zanzibar"), make_clients())
        self.assertIn("Mid-range hotel", plan.hotels[0].name)

    def test_serpapi_is_tried_first_and_bypasses_liteapi_and_stayapi(self):
        clients = make_clients()
        clients.hotels_serp.search_hotels = lambda *a, **kw: [
            HotelOffer("JW Marriott", "Istanbul", "5★", 319.0, 2554.0, "", booking_url="https://google.example/hotels"),
        ]
        clients.hotels.search_hotels = lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("LiteAPI should not be called when SerpApi (tier 1) has inventory")
        )
        clients.hotels_backup.search_hotels = lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("StayAPI should not be called when SerpApi (tier 1) has inventory")
        )
        plan = build_plan(make_inputs(), clients)
        self.assertEqual(plan.hotels[0].name, "JW Marriott")
        self.assertEqual(plan.hotels[0].booking_url, "https://google.example/hotels")

    def test_liteapi_is_used_when_serpapi_has_nothing(self):
        clients = make_clients()
        # tier 1 (StubHotelsSerp) already returns None by default.
        clients.hotels.search_hotels = lambda *a, **kw: [
            HotelOffer("Tier Two Hotel", "Istanbul", "5★", 80.0, 640.0, ""),
        ]
        clients.hotels_backup.search_hotels = lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("StayAPI should not be called when LiteAPI (tier 2) has inventory")
        )
        plan = build_plan(make_inputs(), clients)
        self.assertEqual(plan.hotels[0].name, "Tier Two Hotel")

    def test_stayapi_is_used_when_serpapi_and_liteapi_have_nothing(self):
        clients = make_clients()
        # tiers 1 (StubHotelsSerp) and 2 (StubHotels) already return None.
        clients.hotels_backup.search_hotels = lambda *a, **kw: [
            HotelOffer("Daf House", "Zanzibar", "Unrated", 25.93, 207.44, "Real StayAPI property"),
        ]
        plan = build_plan(make_inputs(destination="Zanzibar"), clients)
        self.assertEqual(plan.hotels[0].name, "Daf House")

    def test_labels_are_honest_tiers_not_invented_hotel_names(self):
        hotels = estimate_hotels("Tunisia", 8)
        # Must not fabricate listings like "Tunisia Central Hotel".
        for hotel in hotels:
            self.assertNotIn("Tunisia", hotel.name)
        self.assertIn("Mid-range", hotels[0].name)
        self.assertEqual(hotels[0].area, "Tunisia")


class EstimateFlightsTests(unittest.TestCase):
    def test_estimate_is_distance_based_and_labelled(self):
        short = estimate_flights("London", "Paris")[0]
        long_haul = estimate_flights("New York", "Mwanza")[0]
        self.assertLess(short.price, long_haul.price)
        self.assertEqual(short.label, "Estimate")
        self.assertEqual(short.airline, "Estimated fare")

    def test_route_uses_resolved_iata_codes(self):
        offer = estimate_flights("Dar es Salaam", "Istanbul, Türkiye")[0]
        self.assertEqual(offer.route, "DAR → IST → DAR")

    def test_country_destination_resolves_via_capital(self):
        offer = estimate_flights("Cairo", "Tunisia")[0]
        self.assertEqual(offer.route, "CAI → TUN → CAI")

    def test_unlocatable_route_returns_none(self):
        self.assertIsNone(estimate_flights("Atlantis", "Shangri-La"))

    def test_within_a_sane_band_of_observed_real_fares(self):
        # Observed live: CAI->TUN $455, DAR->IST $756. The estimator only has
        # to be in the right ballpark, not exact.
        for dep, dest, real in [("Cairo", "Tunisia", 455), ("Dar es Salaam", "Istanbul", 756)]:
            price = estimate_flights(dep, dest)[0].price
            self.assertGreater(price, real * 0.5)
            self.assertLess(price, real * 2.0)


class FlightFallbackTests(unittest.TestCase):
    def test_serpapi_is_tried_first_and_bypasses_travelpayouts(self):
        clients = make_clients()
        clients.flights_backup.search_flights = lambda *a, **kw: [
            FlightOffer("Emirates", "DAR → IST → DAR", "1 stop", "10 hrs", 877, "Cheapest", booking_url="https://google.example/flights")
        ]
        clients.flights.search_flights = lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("Travelpayouts should not be called when SerpApi (tier 1) has a fare")
        )
        plan = build_plan(make_inputs(), clients)
        self.assertEqual(plan.flights[0].airline, "Emirates")
        self.assertEqual(plan.flights[0].booking_url, "https://google.example/flights")

    def test_travelpayouts_is_used_when_serpapi_has_nothing(self):
        plan = build_plan(make_inputs(), make_clients())
        # tier 1 (StubFlightsBackup) returns None by default; StubFlights (tier 2) has a fare.
        self.assertEqual(plan.flights[0].airline, "Test Air")
        # 550/person * 3 people * 1.0 style multiplier
        self.assertEqual(plan.flight_cost, 1650)

    def test_no_cached_fare_falls_back_to_a_distance_estimate(self):
        clients = make_clients()
        clients.flights.search_flights = lambda *a, **kw: None
        plan = build_plan(make_inputs(), clients)
        # Tier 3: a real, route-specific estimate — not the fixture airline.
        self.assertEqual(plan.flights[0].airline, "Estimated fare")
        self.assertEqual(plan.flights[0].route, "DAR → IST → DAR")
        self.assertGreater(plan.flight_cost, 0)

    def test_unlocatable_route_falls_back_to_fixture_flights(self):
        clients = make_clients()
        clients.flights.search_flights = lambda *a, **kw: None
        plan = build_plan(make_inputs(departure="Atlantis", destination="Shangri-La"), clients)
        # Tier 4: nothing resolvable, so the illustrative fixtures stand in.
        self.assertEqual(plan.flights[0].airline, "Turkish Airlines")
        self.assertEqual(plan.flight_cost, 550 * 3)

    def test_flight_cost_scales_with_party_size(self):
        solo = build_plan(make_inputs(adults=1, children=0), make_clients())
        family = build_plan(make_inputs(adults=2, children=2), make_clients())
        self.assertEqual(family.flight_cost, solo.flight_cost * 4)


class CurrencyConversionTests(unittest.TestCase):
    def test_usd_is_unaffected_by_conversion(self):
        plan = build_plan(make_inputs(currency="USD"), make_clients())
        self.assertEqual(plan.exchange_rate, 1.0)

    def test_non_usd_currency_scales_every_cost(self):
        usd_plan = build_plan(make_inputs(currency="USD"), make_clients())
        eur_plan = build_plan(make_inputs(currency="EUR"), make_clients())
        self.assertEqual(eur_plan.exchange_rate, 0.9)
        # Clean single-multiplication components convert exactly.
        self.assertEqual(eur_plan.flight_cost, round(usd_plan.flight_cost * 0.9))
        self.assertEqual(eur_plan.visa_cost, round(usd_plan.visa_cost * 0.9))
        self.assertEqual(eur_plan.transfer_cost, round(usd_plan.transfer_cost * 0.9))

    def test_raw_offer_prices_are_converted_for_display(self):
        eur_plan = build_plan(make_inputs(currency="EUR"), make_clients())
        # StubFlights quotes 550 USD per person.
        self.assertEqual(eur_plan.flights[0].price, round(550 * 0.9, 2))


class OptimizeTests(unittest.TestCase):
    def test_within_budget_plan_is_unchanged(self):
        plan = build_plan(make_inputs(budget=10000), make_clients())
        self.assertTrue(plan.within_budget)
        optimized = optimize(plan, make_clients())
        self.assertIs(optimized, plan)

    def test_over_budget_plan_switches_to_cheapest_style(self):
        plan = build_plan(make_inputs(budget=100, travel_style="luxury"), make_clients())
        self.assertFalse(plan.within_budget)
        optimized = optimize(plan, make_clients())
        self.assertEqual(optimized.inputs.travel_style, "budget")
        self.assertLess(optimized.total, plan.total)


class FillMissingBookingUrlsTests(unittest.TestCase):
    def test_native_urls_are_kept_untouched(self):
        flights = [FlightOffer("Test Air", "DAR → IST", "1 stop", "10h", 550, booking_url="https://aviasales.example/x")]
        hotels = [HotelOffer("Hotel", "Centre", "4★", 80.0, 640.0, "", booking_url="https://booking.example/y")]
        attractions = [Attraction("Landmark", 10, "desc", booking_url="https://wikidata.example/z")]
        f, h, a = fill_missing_booking_urls(flights, hotels, attractions, "Istanbul", None, None)
        self.assertEqual(f[0].booking_url, "https://aviasales.example/x")
        self.assertEqual(h[0].booking_url, "https://booking.example/y")
        self.assertEqual(a[0].booking_url, "https://wikidata.example/z")

    def test_missing_urls_get_a_real_fallback(self):
        flights = [FlightOffer("Test Air", "DAR → IST", "1 stop", "10h", 550)]
        hotels = [HotelOffer("Hotel", "Centre", "4★", 80.0, 640.0, "")]
        attractions = [Attraction("Landmark", 10, "desc")]
        f, h, a = fill_missing_booking_urls(
            flights, hotels, attractions, "Istanbul", date(2027, 5, 10), date(2027, 5, 18)
        )
        self.assertTrue(f[0].booking_url.startswith("https://www.google.com/search?q="))
        self.assertIn("Test+Air", f[0].booking_url)
        self.assertTrue(h[0].booking_url.startswith("https://www.booking.com/searchresults.html?"))
        self.assertIn("ss=Istanbul", h[0].booking_url)
        self.assertIn("checkin=2027-05-10", h[0].booking_url)
        self.assertIn("checkout=2027-05-18", h[0].booking_url)
        self.assertTrue(a[0].booking_url.startswith("https://www.google.com/search?q="))
        self.assertIn("Landmark", a[0].booking_url)

    def test_build_plan_never_leaves_an_empty_booking_url(self):
        plan = build_plan(make_inputs(), make_clients())
        for flight in plan.flights:
            self.assertTrue(flight.booking_url)
        for hotel in plan.hotels:
            self.assertTrue(hotel.booking_url)
        for attraction in plan.attractions:
            self.assertTrue(attraction.booking_url)

    def test_attraction_links_client_result_is_preferred_over_the_generic_search_url(self):
        attractions = [Attraction("Hagia Sophia", 0, "desc")]
        client = SimpleNamespace(resolve_link=lambda query: "https://en.wikipedia.org/wiki/Hagia_Sophia")
        _, _, a = fill_missing_booking_urls(
            [], [], attractions, "Istanbul", None, None, attraction_links_client=client
        )
        self.assertEqual(a[0].booking_url, "https://en.wikipedia.org/wiki/Hagia_Sophia")

    def test_generic_search_url_used_when_attraction_links_client_finds_nothing(self):
        attractions = [Attraction("Some Obscure Spot", 0, "desc")]
        client = SimpleNamespace(resolve_link=lambda query: None)
        _, _, a = fill_missing_booking_urls(
            [], [], attractions, "Istanbul", None, None, attraction_links_client=client
        )
        self.assertTrue(a[0].booking_url.startswith("https://www.google.com/search?q="))

    def test_attraction_links_client_is_not_called_when_a_link_already_exists(self):
        # activities_serp's own "Top sights" link (or OpenTripMap's
        # wikidata/osm one) is real; spending a second SerpApi call to
        # double-check it would waste the scarce monthly quota for nothing.
        attractions = [Attraction("Hagia Sophia", 0, "desc", booking_url="https://www.wikidata.org/wiki/Q1")]
        client = SimpleNamespace(
            resolve_link=lambda query: (_ for _ in ()).throw(
                AssertionError("attraction_links_client should not be called when a link already exists")
            )
        )
        _, _, a = fill_missing_booking_urls(
            [], [], attractions, "Istanbul", None, None, attraction_links_client=client
        )
        self.assertEqual(a[0].booking_url, "https://www.wikidata.org/wiki/Q1")

    def test_existing_wikidata_link_kept_when_attraction_links_client_finds_nothing(self):
        attractions = [Attraction("Hagia Sophia", 0, "desc", booking_url="https://www.wikidata.org/wiki/Q1")]
        client = SimpleNamespace(resolve_link=lambda query: None)
        _, _, a = fill_missing_booking_urls(
            [], [], attractions, "Istanbul", None, None, attraction_links_client=client
        )
        self.assertEqual(a[0].booking_url, "https://www.wikidata.org/wiki/Q1")


class BuildItineraryTests(unittest.TestCase):
    def test_day_count_matches_nights_plus_one(self):
        plan = build_plan(make_inputs(), make_clients())
        days = build_itinerary(plan)
        self.assertEqual(len(days), plan.nights + 1)
        self.assertEqual(days[0].title, "Day 1 — Arrival")
        self.assertEqual(days[-1].title, f"Day {plan.nights + 1} — Departure")

    def test_middle_days_have_four_activities(self):
        plan = build_plan(make_inputs(), make_clients())
        days = build_itinerary(plan)
        for day in days[1:-1]:
            self.assertEqual(len(day.activities), 4)


if __name__ == "__main__":
    unittest.main()
