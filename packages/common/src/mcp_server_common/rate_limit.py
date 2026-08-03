"""Cooperative per-host request spacing."""

import asyncio
from time import monotonic


class HostRateLimiter:
    def __init__(self, *, minimum_interval_seconds: float = 0.5) -> None:
        self._minimum_interval = minimum_interval_seconds
        self._last_request: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def wait(self, host: str) -> None:
        normalized = host.casefold()
        async with self._guard:
            lock = self._locks.setdefault(normalized, asyncio.Lock())
        async with lock:
            elapsed = monotonic() - self._last_request.get(normalized, 0.0)
            remaining = self._minimum_interval - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)
            self._last_request[normalized] = monotonic()
