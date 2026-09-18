import requests
from django.conf import settings

from integrations.base import BaseClient, NotConfigured
from integrations.dataclasses import VisaInfo
from integrations.fixtures import sample_data_for

SHERPA_URL = "https://api.joinsherpa.com/v2/trips"


class SherpaVisaClient(BaseClient):
    """Sherpa (Timatic-based, paid, industry standard) when configured;
    otherwise falls back to the illustrative fixture. (Travel-advisory.info
    is a coarse per-country-code advisory feed, not a Timatic-style visa
    lookup, and destinations here are free-text city/country strings with no
    geocoding step to resolve an ISO country code — so it isn't a safe
    substitute and is left out rather than wired up to silently return
    mismatched results.)
    """

    def get_visa_info(self, nationality: str, destination: str) -> VisaInfo:
        def fetch_sherpa():
            if not settings.SHERPA_API_KEY:
                raise NotConfigured("SHERPA_API_KEY not set")
            resp = requests.get(
                SHERPA_URL,
                headers={"Authorization": f"Bearer {settings.SHERPA_API_KEY}"},
                params={"nationality": nationality, "destination": destination},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return VisaInfo(
                type=data.get("visaType", "Tourist entry requirement"),
                method=data.get("guidance", "Verify official immigration requirements."),
                stay=data.get("maxStay", "Verify before booking"),
                cost=float(data.get("cost", 60)),
                tag=data.get("tag", "VERIFY"),
            )

        fallback = sample_data_for(destination).visa
        result = self.call("visa", fetch_sherpa, None)
        return result or fallback
