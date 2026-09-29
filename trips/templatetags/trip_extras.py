from django import template

from pricing.budget import format_money
from trips.models import BED_CONFIGURATION_CHOICES

register = template.Library()

BED_CONFIGURATION_LABELS = dict(BED_CONFIGURATION_CHOICES)


@register.filter(name="money")
def money(amount, currency):
    return format_money(amount, currency)


@register.filter(name="bed_config_label")
def bed_config_label(value):
    """`legs` in results/PDF templates is a plain JSON dict (from
    BudgetBreakdown.legs), not a TripLeg instance — no get_FOO_display() to
    call, hence this lookup against the same choices list.
    """
    return BED_CONFIGURATION_LABELS.get(value, value)
