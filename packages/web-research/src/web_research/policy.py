"""SSRF, explicit-host, and robots.txt policy enforcement."""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlsplit
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
        try:
            ipaddress.ip_address(host)
        except ValueError:
            addresses = await self._resolver(host)
        else:
            addresses = [host]
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
        url_policy: UrlPolicy | None = None,
    ) -> None:
        self.gateway = gateway
        self.limiter = limiter
        self.user_agent = user_agent
        self.url_policy = url_policy
        self._cache: TTLCache[str] = TTLCache(ttl_seconds=900)

    async def allowed(self, url: str) -> tuple[bool, str]:
        parts = urlsplit(url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        cached = await self._cache.get(robots_url)
        if cached is not None:
            return _robots_allows(robots_url, cached, self.user_agent, url), robots_url
        current = robots_url
        payload = None
        for _ in range(self.gateway.max_redirects + 1):
            if self.url_policy is not None:
                host = await self.url_policy.validate(current)
            else:
                host = urlsplit(current).hostname or ""
            await self.limiter.wait(host)
            try:
                candidate = await self.gateway.get(current, follow_redirects=False)
            except ServiceError as exc:
                if exc.failure.code == FailureCode.NOT_FOUND:
                    await self._cache.put(robots_url, "")
                    return True, robots_url
                raise _blocked(
                    "robots.txt could not be verified; access failed closed."
                ) from exc
            if candidate.status_code in {301, 302, 303, 307, 308}:
                location = candidate.headers.get("location")
                if not location:
                    raise _blocked("robots.txt redirected without a verifiable target.")
                current = urljoin(current, location)
                continue
            payload = candidate
            break
        if payload is None:
            raise _blocked("robots.txt exceeded the configured redirect limit.")
        rules = payload.text()
        allowed = _robots_allows(current, rules, self.user_agent, url)
        await self._cache.put(robots_url, rules)
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


def _robots_allows(robots_url: str, rules: str, user_agent: str, target_url: str) -> bool:
    parser = RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(rules.splitlines())
    return parser.can_fetch(user_agent, target_url)


def _blocked(message: str) -> ServiceError:
    return ServiceError(
        Failure(code=FailureCode.BLOCKED, message=message, retryable=False)
    )
