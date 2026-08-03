import httpx2
import pytest
from mcp import Client
from mcp_server_common.http import HttpGateway
from mcp_server_common.rate_limit import HostRateLimiter
from web_research.policy import RobotsChecker, UrlPolicy
from web_research.server import create_server
from web_research.service import WebResearchService


async def _public_resolver(host: str) -> list[str]:
    return ["93.184.216.34"]


def _web_response(request: httpx2.Request) -> httpx2.Response:
    if request.url.path == "/robots.txt":
        return httpx2.Response(
            200,
            text="User-agent: *\nDisallow: /private\nAllow: /public\n",
            headers={"content-type": "text/plain"},
        )
    return httpx2.Response(
        200,
        text="<html><title>Allowed</title><main><p>Public evidence.</p></main></html>",
        headers={"content-type": "text/html"},
    )


def _redirect_response(request: httpx2.Request) -> httpx2.Response:
    if request.url.path == "/robots.txt":
        return httpx2.Response(
            200,
            text="User-agent: *\nAllow: /\n",
            headers={"content-type": "text/plain"},
        )
    return httpx2.Response(302, headers={"location": "http://127.0.0.1/private"})


def _service() -> WebResearchService:
    gateway = HttpGateway(
        transport=httpx2.MockTransport(_web_response),
        max_redirects=2,
    )
    limiter = HostRateLimiter(minimum_interval_seconds=0)
    policy = UrlPolicy(resolver=_public_resolver)
    robots = RobotsChecker(gateway, limiter)
    return WebResearchService(
        gateway,
        url_policy=policy,
        limiter=limiter,
        robots=robots,
    )


@pytest.mark.asyncio
async def test_robots_denial_is_a_typed_tool_failure() -> None:
    async with Client(create_server(_service())) as client:
        result = await client.call_tool(
            "fetch_page", {"url": "https://example.com/private/report"}
        )

    assert result.is_error is False
    assert result.structured_content["status"] == "error"
    assert result.structured_content["failure"]["code"] == "blocked"
    assert result.structured_content["failure"]["details"]["policy"] == ("robots_denied")


@pytest.mark.asyncio
async def test_allowlist_and_protocol_surfaces() -> None:
    async with Client(create_server(_service())) as client:
        resources = await client.list_resources()
        prompts = await client.list_prompts()
        allowed = await client.call_tool(
            "fetch_page",
            {
                "url": "https://example.com/public/report",
                "allowed_hosts": ["example.com"],
            },
        )
        denied = await client.call_tool(
            "fetch_page",
            {
                "url": "https://example.com/public/report",
                "allowed_hosts": ["another.example"],
            },
        )
        robots_denied = await client.call_tool(
            "fetch_page", {"url": "https://example.com/private/report"}
        )

    assert {str(resource.uri) for resource in resources.resources} == {
        "research://policy",
        "research://trust",
    }
    assert {prompt.name for prompt in prompts.prompts} == {"source-backed-research"}
    assert allowed.structured_content["page"]["text"] == "Allowed\nPublic evidence."
    assert denied.structured_content["failure"]["code"] == "blocked"
    assert robots_denied.structured_content["failure"]["details"]["policy"] == (
        "robots_denied"
    )


@pytest.mark.asyncio
async def test_redirect_to_private_network_is_revalidated_and_denied() -> None:
    gateway = HttpGateway(transport=httpx2.MockTransport(_redirect_response))
    limiter = HostRateLimiter(minimum_interval_seconds=0)
    policy = UrlPolicy(resolver=_public_resolver)
    service = WebResearchService(
        gateway,
        url_policy=policy,
        limiter=limiter,
        robots=RobotsChecker(gateway, limiter, url_policy=policy),
    )
    async with Client(create_server(service)) as client:
        result = await client.call_tool(
            "fetch_page", {"url": "https://example.com/redirect"}
        )

    assert result.structured_content["status"] == "error"
    assert result.structured_content["failure"]["code"] == "blocked"
