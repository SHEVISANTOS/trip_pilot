import hashlib
import logging
from typing import Callable, TypeVar

from django.core.cache import cache

from integrations.models import IntegrationCallLog

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Distinguishes "not in cache" from "cached value is None" (several clients
# legitimately cache a None result, e.g. "no flights found this route").
_CACHE_MISS = object()


class NotConfigured(Exception):
    """Raised by a client when it has no API key/credentials set."""


class BaseClient:
    """Every provider client wraps its live call through `call()`: on success
    the result is returned and logged; on any failure (not configured, network
    error, bad response) the exception is logged and `fallback` is returned
    instead, so the rest of the app never has to know a provider is down.

    Pass `cache_key`/`cache_ttl` to cache successful results (README section
    5's caching requirement) — a cache hit skips both the call and the log
    entry, so IntegrationCallLog/the health page reflects real network
    round-trips, not cache hits.
    """

    @staticmethod
    def make_cache_key(provider: str, *parts: object) -> str:
        """Cache keys built from free-text user input (destinations,
        nationalities, ...) need hashing — raw unicode/punctuation in a cache
        key is technically valid for Redis/locmem but not memcached, and an
        unbounded-length key from a long destination string is bad practice
        either way.
        """
        raw = "|".join(str(p) for p in parts)
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        return f"{provider}:{digest}"

    def call(
        self,
        provider: str,
        fn: Callable[[], T],
        fallback: T,
        cache_key: str | None = None,
        cache_ttl: int | None = None,
    ) -> T:
        if cache_key:
            cached = cache.get(cache_key, _CACHE_MISS)
            if cached is not _CACHE_MISS:
                return cached

        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 - any provider failure degrades to fallback
            logger.warning("%s call failed, using fallback: %s", provider, exc)
            IntegrationCallLog.objects.create(provider=provider, success=False, detail=str(exc))
            return fallback

        IntegrationCallLog.objects.create(provider=provider, success=True, detail="")
        if cache_key:
            cache.set(cache_key, result, cache_ttl)
        return result
