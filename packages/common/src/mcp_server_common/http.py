"""Bounded public HTTP access with response-size and redirect controls."""

from dataclasses import dataclass
from typing import Any

import httpx2

from mcp_server_common.bounded import ServiceError
from mcp_server_common.models import Failure, FailureCode
from mcp_server_common.redaction import safe_source_url


@dataclass(frozen=True, slots=True)
class HttpPayload:
    url: str
    status_code: int
    content_type: str
    body: bytes

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class HttpGateway:
    """A short-lived async client suitable for injection with MockTransport in tests."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 8,
        max_bytes: int = 2_000_000,
        max_redirects: int = 3,
        transport: httpx2.AsyncBaseTransport | None = None,
        user_agent: str = "prathamesh-mcp-servers/0.1 (+public-research)",
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.transport = transport
        self.user_agent = user_agent

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str | int] | None = None,
    ) -> HttpPayload:
        request_headers = {"Accept": "application/json, text/html;q=0.9, */*;q=0.5"}
        request_headers["User-Agent"] = self.user_agent
        if headers:
            request_headers.update(headers)
        try:
            async with httpx2.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=True,
                max_redirects=self.max_redirects,
                transport=self.transport,
                trust_env=False,
            ) as client:
                response = await client.get(url, headers=request_headers, params=params)
        except httpx2.TimeoutException as exc:
            raise ServiceError(
                Failure(
                    code=FailureCode.TIMEOUT,
                    message="The upstream request reached its deadline.",
                    retryable=True,
                    source_url=safe_source_url(url),
                )
            ) from exc
        except httpx2.HTTPError as exc:
            raise ServiceError(
                Failure(
                    code=FailureCode.UPSTREAM_ERROR,
                    message="The public upstream could not be reached.",
                    retryable=True,
                    source_url=safe_source_url(url),
                )
            ) from exc

        public_url = safe_source_url(str(response.url))
        if response.status_code == 404:
            raise ServiceError(
                Failure(
                    code=FailureCode.NOT_FOUND,
                    message="The requested public record was not found.",
                    source_url=public_url,
                )
            )
        if response.status_code == 429:
            raise ServiceError(
                Failure(
                    code=FailureCode.RATE_LIMITED,
                    message="The public upstream rate limit was reached.",
                    retryable=True,
                    source_url=public_url,
                )
            )
        if response.status_code >= 400:
            raise ServiceError(
                Failure(
                    code=FailureCode.UPSTREAM_ERROR,
                    message=f"The public upstream returned HTTP {response.status_code}.",
                    retryable=response.status_code >= 500,
                    source_url=public_url,
                )
            )
        if len(response.content) > self.max_bytes:
            raise ServiceError(
                Failure(
                    code=FailureCode.BLOCKED,
                    message="The response exceeded the configured size limit.",
                    source_url=public_url,
                    details={"max_bytes": self.max_bytes},
                )
            )
        return HttpPayload(
            url=public_url,
            status_code=response.status_code,
            content_type=response.headers.get("content-type", ""),
            body=response.content,
        )

    async def get_json(self, url: str, **kwargs: Any) -> tuple[str, Any]:
        payload = await self.get(url, **kwargs)
        try:
            return payload.url, httpx2.Response(
                status_code=payload.status_code,
                content=payload.body,
            ).json()
        except ValueError as exc:
            raise ServiceError(
                Failure(
                    code=FailureCode.PARSE_ERROR,
                    message="The public upstream returned invalid JSON.",
                    source_url=payload.url,
                )
            ) from exc
