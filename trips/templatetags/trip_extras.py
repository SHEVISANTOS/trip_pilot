from django import template

from pricing.budget import format_money

register = template.Library()


@register.filter(name="money")
def money(amount, currency):
    return format_money(amount, currency)
