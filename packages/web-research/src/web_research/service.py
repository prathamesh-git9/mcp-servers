"""Robots-respecting search, fetch, and extraction service."""

from urllib.parse import urljoin, urlsplit

from mcp_server_common import Failure, FailureCode, ServiceError
from mcp_server_common.http import HttpGateway
from mcp_server_common.rate_limit import HostRateLimiter

from web_research.extract import extract_main_text, parse_search_results
from web_research.models import ExtractedPage, ResearchItem, SearchHit
from web_research.policy import RobotsChecker, UrlPolicy

_SEARCH_URL = "https://html.duckduckgo.com/html/"


class WebResearchService:
    def __init__(
        self,
        gateway: HttpGateway | None = None,
        *,
        url_policy: UrlPolicy | None = None,
        limiter: HostRateLimiter | None = None,
        robots: RobotsChecker | None = None,
    ) -> None:
        self.gateway = gateway or HttpGateway(timeout_seconds=8, max_bytes=2_000_000)
        self.url_policy = url_policy or UrlPolicy()
        self.limiter = limiter or HostRateLimiter(minimum_interval_seconds=0.6)
        self.robots = robots or RobotsChecker(self.gateway, self.limiter)

    async def search(self, query: str, *, limit: int = 5) -> list[SearchHit]:
        if not query.strip():
            raise ServiceError(
                Failure(
                    code=FailureCode.INVALID_INPUT,
                    message="query must not be empty",
                )
            )
        await self.url_policy.validate(_SEARCH_URL)
        allowed, _ = await self.robots.allowed(_SEARCH_URL)
        if not allowed:
            raise _robots_denied(_SEARCH_URL)
        await self.limiter.wait(urlsplit(_SEARCH_URL).hostname or "")
        payload = await self.gateway.get(_SEARCH_URL, params={"q": query})
        return parse_search_results(payload.text(), _SEARCH_URL)[:limit]

    async def fetch(
        self,
        url: str,
        *,
        allowed_hosts: list[str] | None = None,
        denied_hosts: list[str] | None = None,
    ) -> ExtractedPage:
        current = url
        robots_url = ""
        for _ in range(self.gateway.max_redirects + 1):
            host = await self.url_policy.validate(
                current, allowed_hosts=allowed_hosts, denied_hosts=denied_hosts
            )
            allowed, robots_url = await self.robots.allowed(current)
            if not allowed:
                raise _robots_denied(robots_url)
            await self.limiter.wait(host)
            payload = await self.gateway.get(current, follow_redirects=False)
            if payload.status_code in {301, 302, 303, 307, 308}:
                location = payload.headers.get("location")
                if not location:
                    raise _parse_failure(payload.url)
                current = urljoin(current, location)
                continue
            if "text/html" not in payload.content_type and "text/plain" not in (
                payload.content_type
            ):
                raise ServiceError(
                    Failure(
                        code=FailureCode.BLOCKED,
                        message="Only public HTML and plain-text pages can be extracted.",
                        source_url=payload.url,
                    )
                )
            title, text = extract_main_text(payload.text())
            return ExtractedPage(
                url=payload.url,
                title=title,
                text=text,
                word_count=len(text.split()),
                robots_url=robots_url,
            )
        raise ServiceError(
            Failure(
                code=FailureCode.BLOCKED,
                message="The page exceeded the configured redirect limit.",
            )
        )

    async def research(self, query: str, *, limit: int = 3) -> list[ResearchItem]:
        hits = await self.search(query, limit=limit)
        items: list[ResearchItem] = []
        for hit in hits:
            try:
                page = await self.fetch(hit.url)
            except ServiceError as exc:
                items.append(
                    ResearchItem(
                        search_hit=hit,
                        fetch_failure=exc.failure.code.value,
                    )
                )
            else:
                items.append(ResearchItem(search_hit=hit, page=page))
        return items


def _robots_denied(url: str) -> ServiceError:
    return ServiceError(
        Failure(
            code=FailureCode.BLOCKED,
            message="robots.txt denies this automated fetch.",
            retryable=False,
            source_url=url,
            details={"policy": "robots_denied"},
        )
    )


def _parse_failure(url: str) -> ServiceError:
    return ServiceError(
        Failure(
            code=FailureCode.PARSE_ERROR,
            message="A redirect response did not include a target.",
            source_url=url,
        )
    )
