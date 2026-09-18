from django.conf import settings
from django.db import models

from trips.models import CURRENCY_CHOICES


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    nationality = models.CharField(max_length=80, blank=True)
    preferred_currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default="USD")

    def __str__(self):
        return f"Profile: {self.user}"
