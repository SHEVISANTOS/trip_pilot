from django.db import models


class IntegrationCallLog(models.Model):
    PROVIDER_CHOICES = [
        ("travelpayouts", "Travelpayouts — Flights"),
        ("opentripmap", "OpenTripMap — Attractions"),
        ("visa", "Visa guidance"),
        ("exchange_rate", "Exchange rate"),
        ("maps", "Maps / transfer estimate"),
        ("esim", "eSIM Go — Data bundles"),
        ("hotels", "LiteAPI — Hotels"),
    ]

    provider = models.CharField(max_length=32, choices=PROVIDER_CHOICES)
    called_at = models.DateTimeField(auto_now_add=True)
    success = models.BooleanField()
    detail = models.TextField(blank=True)

    class Meta:
        ordering = ["-called_at"]

    def __str__(self):
        state = "ok" if self.success else "fallback"
        return f"{self.get_provider_display()} — {state} @ {self.called_at:%Y-%m-%d %H:%M}"
