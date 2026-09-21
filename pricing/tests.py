"""Unit tests for the budget engine and optimizer — pure Python, no DB or
network access. Run with: python manage.py test pricing
"""
import unittest
from datetime import date
from types import SimpleNamespace

from integrations.dataclasses import Attraction, EsimBundle, FlightOffer, HotelOffer, VisaInfo
from pricing.budget import TripInputs, build_plan, calculate_nights, estimate_sim_cost
from pricing.itinerary import build_itinerary
from pricing.optimizer import optimize


class StubAmadeus:
    def search_flights(self, origin, destination, adults):
        return [FlightOffer("Test Air", f"{origin} → {destination}", "1 stop", "10 hrs", 1650, "Recommended")]

    def search_hotels(self, destination, nights, adults):
        return [HotelOffer("Test Hotel", "Centre", "4★", 135, 1200, "Central.")]


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


class StubVisa:
    def get_visa_info(self, nationality, destination):
        return VisaInfo("Tourist entry requirement", "Verify before departure.", "Check stay length", 60, "VERIFY")


class StubEsim:
    def get_bundle(self, destination, nights):
        return None  # no live bundle -> build_plan() falls back to estimate_sim_cost()


def make_clients():
    return SimpleNamespace(
        amadeus=StubAmadeus(), activities=StubActivities(), visa=StubVisa(), esim=StubEsim()
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
