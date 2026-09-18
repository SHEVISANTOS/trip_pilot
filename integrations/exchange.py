import requests

from integrations.base import BaseClient
from integrations.dataclasses import ExchangeRate

EXCHANGE_RATE_URL = "https://api.exchangerate.host/latest"


class ExchangeRateClient(BaseClient):
    """exchangerate.host needs no API key, so this is called directly rather
    than gated behind a settings check. Falls back to a 1:1 rate (clearly a
    placeholder, never presented as a real conversion) if the service is
    unreachable.
    """

    def get_rate(self, base: str, target: str) -> ExchangeRate:
        def fetch():
            if base == target:
                return ExchangeRate(base=base, target=target, rate=1.0)
            resp = requests.get(EXCHANGE_RATE_URL, params={"base": base, "symbols": target}, timeout=10)
            resp.raise_for_status()
            rate = resp.json()["rates"][target]
            return ExchangeRate(base=base, target=target, rate=float(rate))

        fallback = ExchangeRate(base=base, target=target, rate=1.0)
        return self.call("exchange_rate", fetch, fallback)
