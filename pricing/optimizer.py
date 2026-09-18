"""Port of app.js's optimizeBtn handler: when a plan is over budget, drop to
the cheapest travel style + hotel tier and recompute. If already within
budget there's nothing to optimize, so the plan is returned unchanged.
"""
from dataclasses import replace

from pricing.budget import BudgetPlan, build_plan

CHEAPEST_STYLE = "budget"
CHEAPEST_HOTEL_PREFERENCE = "3–4 Star Hotel"


def optimize(plan: BudgetPlan, clients) -> BudgetPlan:
    if plan.within_budget:
        return plan
    cheaper_inputs = replace(
        plan.inputs, travel_style=CHEAPEST_STYLE, hotel_preference=CHEAPEST_HOTEL_PREFERENCE
    )
    return build_plan(cheaper_inputs, clients)
