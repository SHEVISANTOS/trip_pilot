import requests
from django.conf import settings

from integrations.base import BaseClient
from integrations.dataclasses import ExchangeRate

EXCHANGE_RATE_URL = "https://open.er-api.com/v6/latest"

# exchangerate.host (the source the original build prompt named) started
# requiring a paid access_key at some point after this project's spec was
# written and now 401s on every unauthenticated request — confirmed live,
# not assumed. open.er-api.com (exchangerate-api.com's free tier) is a
# genuinely free, no-key equivalent: same shape (a `rates` dict), covers
# every currency this app offers (USD/TZS/EUR/GBP).


class ExchangeRateClient(BaseClient):
    """No API key needed, so this is called directly rather than gated
    behind a settings check. Falls back to a 1:1 rate (clearly a
    placeholder, never presented as a real conversion) if the service is
    unreachable.
    """

    def get_rate(self, base: str, target: str) -> ExchangeRate:
        def fetch():
            if base == target:
                return ExchangeRate(base=base, target=target, rate=1.0)
            resp = requests.get(f"{EXCHANGE_RATE_URL}/{base}", timeout=10)
            resp.raise_for_status()
            rate = resp.json()["rates"][target]
            return ExchangeRate(base=base, target=target, rate=float(rate))

        fallback = ExchangeRate(base=base, target=target, rate=1.0)
        cache_key = self.make_cache_key("exchange_rate", base, target)
        return self.call("exchange_rate", fetch, fallback, cache_key, settings.CACHE_TTL_EXCHANGE_RATE)
