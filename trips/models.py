from django.conf import settings
from django.db import models

TRAVEL_STYLE_CHOICES = [
    ("budget", "Budget"),
    ("balanced", "Comfortable / Balanced"),
    ("luxury", "Luxury"),
]

PURPOSE_CHOICES = [
    ("Holiday", "Holiday"),
    ("Business", "Business"),
    ("Family Visit", "Family Visit"),
    ("Honeymoon", "Honeymoon"),
]

# Stored value stays plain text (pricing.optimizer.CHEAPEST_HOTEL_PREFERENCE
# and existing TripRequest rows match on it) — only the display label shown
# in the dropdown uses ★ symbols.
ACCOMMODATION_CHOICES = [
    ("3–4 Star Hotel", "★★★–★★★★ Hotel"),
    ("Apartment", "Apartment"),
    ("Resort", "★★★★ Resort"),
    ("Luxury Hotel", "★★★★★ Luxury Hotel"),
]

CURRENCY_CHOICES = [
    ("USD", "USD ($)"),
    ("TZS", "TZS"),
    ("EUR", "EUR (€)"),
    ("GBP", "GBP (£)"),
]

CHECKLIST_ITEMS = [
    "Verify visa and passport validity",
    "Select and book flights",
    "Select and book hotel",
    "Arrange airport transfer",
    "Purchase travel insurance",
    "Arrange SIM/eSIM",
    "Book priority attractions",
    "Check-in online before departure",
    "Prepare emergency cash and cards",
]


class TripRequest(models.Model):
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="trip_requests"
    )
    departure = models.CharField(max_length=120)
    destination = models.CharField(max_length=120)
    nationality = models.CharField(max_length=80)
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES, default="Holiday")
    start_date = models.DateField()
    end_date = models.DateField()
    adults = models.PositiveSmallIntegerField(default=1)
    children = models.PositiveSmallIntegerField(default=0)
    travel_style = models.CharField(max_length=10, choices=TRAVEL_STYLE_CHOICES, default="balanced")
    hotel_preference = models.CharField(max_length=30, choices=ACCOMMODATION_CHOICES, default="3–4 Star Hotel")
    interests = models.CharField(max_length=200, blank=True)
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default="USD")
    budget = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.destination} ({self.created_at:%Y-%m-%d})"

    @property
    def people(self):
        return self.adults + self.children

    @property
    def is_multi_city(self):
        return self.legs.count() > 1


class TripLeg(models.Model):
    """One stop on the itinerary. Every TripRequest has at least one — for a
    single-destination trip (still the common case) that's just one leg
    mirroring the legacy destination/start_date/end_date/hotel_preference
    fields above, which stay in sync with leg 0 for every template, the PDF
    export, the dashboard list, etc. that already read them directly as a
    plain destination/date pair.
    """

    trip_request = models.ForeignKey(TripRequest, on_delete=models.CASCADE, related_name="legs")
    order = models.PositiveSmallIntegerField()
    city = models.CharField(max_length=120)
    arrival_date = models.DateField()
    departure_date = models.DateField()
    hotel_preference = models.CharField(max_length=30, choices=ACCOMMODATION_CHOICES, default="3–4 Star Hotel")

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.city} ({self.arrival_date} → {self.departure_date})"

    @property
    def nights(self):
        return max((self.departure_date - self.arrival_date).days, 1)


class BudgetBreakdown(models.Model):
    trip_request = models.OneToOneField(TripRequest, on_delete=models.CASCADE, related_name="budget_breakdown")
    items = models.JSONField(help_text="[{label, detail, amount}, ...]")
    visa = models.JSONField(help_text="{type, method, stay, cost, tag}")
    flights = models.JSONField(help_text="[{airline, route, stops, duration, price, label}, ...]")
    hotels = models.JSONField(help_text="[{name, area, rating, night, total, desc}, ...]")
    attractions = models.JSONField(help_text="[{name, cost, desc, optional}, ...]")
    legs = models.JSONField(
        default=list,
        help_text="[{city, nights, hotels, attractions, hotel_cost, ...}, ...] — per-destination breakdown",
    )
    nights = models.PositiveSmallIntegerField()
    people = models.PositiveSmallIntegerField()
    flight_cost = models.DecimalField(max_digits=10, decimal_places=2)
    hotel_cost = models.DecimalField(max_digits=10, decimal_places=2)
    transfer_cost = models.DecimalField(max_digits=10, decimal_places=2)
    transport_cost = models.DecimalField(max_digits=10, decimal_places=2)
    food_cost = models.DecimalField(max_digits=10, decimal_places=2)
    attractions_cost = models.DecimalField(max_digits=10, decimal_places=2)
    insurance_cost = models.DecimalField(max_digits=10, decimal_places=2)
    sim_cost = models.DecimalField(max_digits=10, decimal_places=2)
    emergency_cost = models.DecimalField(max_digits=10, decimal_places=2)
    visa_cost = models.DecimalField(max_digits=10, decimal_places=2)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    remaining = models.DecimalField(max_digits=10, decimal_places=2)
    over_budget = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Budget for {self.trip_request.destination}"


class SavedTrip(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_trips")
    trip_request = models.OneToOneField(TripRequest, on_delete=models.CASCADE, related_name="saved_trip")
    checklist = models.JSONField(default=list, help_text="[{label, done}, ...]")
    saved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-saved_at"]

    def __str__(self):
        return f"{self.user} → {self.trip_request.destination}"

    def save(self, *args, **kwargs):
        if not self.checklist:
            self.checklist = [{"label": label, "done": False} for label in CHECKLIST_ITEMS]
        super().save(*args, **kwargs)


class Itinerary(models.Model):
    trip_request = models.OneToOneField(TripRequest, on_delete=models.CASCADE, related_name="itinerary")

    def __str__(self):
        return f"Itinerary for {self.trip_request.destination}"


class ItineraryDay(models.Model):
    itinerary = models.ForeignKey(Itinerary, on_delete=models.CASCADE, related_name="days")
    day_number = models.PositiveSmallIntegerField()
    title = models.CharField(max_length=120)

    class Meta:
        ordering = ["day_number"]

    def __str__(self):
        return self.title


class ItineraryActivity(models.Model):
    day = models.ForeignKey(ItineraryDay, on_delete=models.CASCADE, related_name="activities")
    order = models.PositiveSmallIntegerField()
    time = models.CharField(max_length=20)
    title = models.CharField(max_length=160)
    cost_display = models.CharField(max_length=60)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.time} — {self.title}"
