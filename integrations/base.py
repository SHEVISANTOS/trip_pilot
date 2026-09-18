import logging
from typing import Callable, TypeVar

from integrations.models import IntegrationCallLog

logger = logging.getLogger(__name__)

T = TypeVar("T")


class NotConfigured(Exception):
    """Raised by a client when it has no API key/credentials set."""


class BaseClient:
    """Every provider client wraps its live call through `call()`: on success
    the result is returned and logged; on any failure (not configured, network
    error, bad response) the exception is logged and `fallback` is returned
    instead, so the rest of the app never has to know a provider is down.
    """

    def call(self, provider: str, fn: Callable[[], T], fallback: T) -> T:
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 - any provider failure degrades to fallback
            logger.warning("%s call failed, using fallback: %s", provider, exc)
            IntegrationCallLog.objects.create(provider=provider, success=False, detail=str(exc))
            return fallback
        IntegrationCallLog.objects.create(provider=provider, success=True, detail="")
        return result
