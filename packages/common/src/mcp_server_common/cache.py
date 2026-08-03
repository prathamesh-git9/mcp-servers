"""A small in-process TTL cache used only for public, non-secret responses."""

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class _Entry(Generic[T]):
    value: T
    expires_at: float


class TTLCache(Generic[T]):
    def __init__(self, *, ttl_seconds: float = 300, max_entries: int = 256) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._entries: dict[str, _Entry[T]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> T | None:
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at <= monotonic():
                self._entries.pop(key, None)
                return None
            return entry.value

    async def put(self, key: str, value: T) -> None:
        async with self._lock:
            now = monotonic()
            expired = [
                key for key, item in self._entries.items() if item.expires_at <= now
            ]
            for expired_key in expired:
                self._entries.pop(expired_key, None)
            if len(self._entries) >= self._max_entries:
                oldest_key = min(
                    self._entries, key=lambda item: self._entries[item].expires_at
                )
                self._entries.pop(oldest_key, None)
            self._entries[key] = _Entry(value=value, expires_at=now + self._ttl_seconds)
