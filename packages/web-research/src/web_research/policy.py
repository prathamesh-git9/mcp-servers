"""SSRF, explicit-host, and robots.txt policy enforcement."""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from mcp_server_common import Failure, FailureCode, ServiceError
from mcp_server_common.cache import TTLCache
from mcp_server_common.http import HttpGateway
from mcp_server_common.rate_limit import HostRateLimiter

Resolver = Callable[[str], Awaitable[list[str]]]


class UrlPolicy:
    def __init__(self, resolver: Resolver | None = None) -> None:
        self._resolver = resolver or _resolve_host

    async def validate(
        self,
        url: str,
        *,
        allowed_hosts: list[str] | None = None,
        denied_hosts: list[str] | None = None,
    ) -> str:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise _blocked("Only absolute public HTTP(S) URLs are allowed.")
        if parts.username or parts.password:
            raise _blocked("URLs containing credentials are denied.")
        host = parts.hostname.casefold().rstrip(".")
        denied = {item.casefold().rstrip(".") for item in denied_hosts or []}
        allowed = {item.casefold().rstrip(".") for item in allowed_hosts or []}
        if host in denied or any(host.endswith(f".{item}") for item in denied):
            raise _blocked("The target host is on the explicit denylist.")
        if (
            allowed
            and host not in allowed
            and not any(host.endswith(f".{item}") for item in allowed)
        ):
            raise _blocked("The target host is outside the explicit allowlist.")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise _blocked("Local and internal hostnames are denied.")
        addresses = await self._resolver(host)
        if not addresses:
            raise _blocked("The target hostname did not resolve to a public address.")
        if any(not _is_public(address) for address in addresses):
            raise _blocked("Private, loopback, and reserved network targets are denied.")
        return host


class RobotsChecker:
    def __init__(
        self,
        gateway: HttpGateway,
        limiter: HostRateLimiter,
        *,
        user_agent: str = "prathamesh-mcp-servers",
    ) -> None:
        self.gateway = gateway
        self.limiter = limiter
        self.user_agent = user_agent
        self._cache: TTLCache[tuple[str, bool]] = TTLCache(ttl_seconds=900)

    async def allowed(self, url: str) -> tuple[bool, str]:
        parts = urlsplit(url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        cached = await self._cache.get(robots_url)
        if cached is not None:
            _, allowed = cached
            return allowed, robots_url
        await self.limiter.wait(parts.hostname or "")
        try:
            payload = await self.gateway.get(robots_url, follow_redirects=False)
        except ServiceError as exc:
            if exc.failure.code == FailureCode.NOT_FOUND:
                await self._cache.put(robots_url, ("", True))
                return True, robots_url
            raise _blocked(
                "robots.txt could not be verified; access failed closed."
            ) from exc
        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(payload.text().splitlines())
        allowed = parser.can_fetch(self.user_agent, url)
        await self._cache.put(robots_url, (payload.text(), allowed))
        return allowed, robots_url


async def _resolve_host(host: str) -> list[str]:
    try:
        records = await asyncio.to_thread(
            socket.getaddrinfo, host, None, type=socket.SOCK_STREAM
        )
    except OSError:
        return []
    return sorted({record[4][0] for record in records})


def _is_public(address: str) -> bool:
    try:
        value = ipaddress.ip_address(address)
    except ValueError:
        return False
    return value.is_global


def _blocked(message: str) -> ServiceError:
    return ServiceError(
        Failure(code=FailureCode.BLOCKED, message=message, retryable=False)
    )
