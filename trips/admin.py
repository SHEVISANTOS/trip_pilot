from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from trips.models import BudgetBreakdown, Itinerary, ItineraryActivity, ItineraryDay, SavedTrip, TripRequest


class OverBudgetFilter(admin.SimpleListFilter):
    title = "budget status"
    parameter_name = "budget_status"

    def lookups(self, request, model_admin):
        return [("over", "Over budget"), ("under", "Within budget")]

    def queryset(self, request, queryset):
        if self.value() == "over":
            return queryset.filter(budget_breakdown__over_budget=True)
        if self.value() == "under":
            return queryset.filter(budget_breakdown__over_budget=False)
        return queryset


@admin.register(TripRequest)
class TripRequestAdmin(ModelAdmin):
    list_display = ("destination", "created_by", "budget", "currency", "travel_style", "created_at", "budget_status")
    list_filter = ("travel_style", "currency", OverBudgetFilter)
    search_fields = ("destination", "departure", "nationality")
    date_hierarchy = "created_at"

    @admin.display(description="Status")
    def budget_status(self, obj):
        breakdown = getattr(obj, "budget_breakdown", None)
        if not breakdown:
            return "—"
        return "OVER BUDGET" if breakdown.over_budget else "WITHIN BUDGET"


@admin.register(BudgetBreakdown)
class BudgetBreakdownAdmin(ModelAdmin):
    list_display = ("trip_request", "total", "remaining", "over_budget", "updated_at")
    list_filter = ("over_budget",)


@admin.register(SavedTrip)
class SavedTripAdmin(ModelAdmin):
    list_display = ("user", "trip_request", "saved_at")
    list_filter = ("saved_at",)
    autocomplete_fields = ["user"]


class ItineraryDayInline(TabularInline):
    model = ItineraryDay
    extra = 0
    fields = ("day_number", "title")


@admin.register(Itinerary)
class ItineraryAdmin(ModelAdmin):
    list_display = ("trip_request",)
    inlines = [ItineraryDayInline]


class ItineraryActivityInline(TabularInline):
    model = ItineraryActivity
    extra = 0
    fields = ("order", "time", "title", "cost_display")


@admin.register(ItineraryDay)
class ItineraryDayAdmin(ModelAdmin):
    list_display = ("itinerary", "day_number", "title")
    list_filter = ("itinerary",)
    inlines = [ItineraryActivityInline]
