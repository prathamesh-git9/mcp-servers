import httpx2
import pytest
from mcp import Client
from mcp_server_common.http import HttpGateway
from repo_intel.server import create_server
from repo_intel.service import RepoIntelService


def _github_response(request: httpx2.Request) -> httpx2.Response:
    if request.url.path == "/users/prathamesh-git9/repos":
        return httpx2.Response(
            200,
            json=[
                {
                    "owner": {"login": "prathamesh-git9"},
                    "name": "mcp-servers",
                    "description": "Five MCP servers",
                    "html_url": "https://github.com/prathamesh-git9/mcp-servers",
                    "default_branch": "main",
                    "stargazers_count": 3,
                    "forks_count": 1,
                    "open_issues_count": 0,
                    "language": "Python",
                    "topics": ["mcp"],
                    "archived": False,
                    "updated_at": "2026-08-03T10:00:00Z",
                }
            ],
        )
    return httpx2.Response(404, json={"message": "not found"})


@pytest.mark.asyncio
async def test_repo_intel_is_read_only_and_structured() -> None:
    gateway = HttpGateway(transport=httpx2.MockTransport(_github_response))
    service = RepoIntelService(gateway, token="test-token-never-returned")
    async with Client(create_server(service)) as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
        prompts = await client.list_prompts()
        result = await client.call_tool(
            "list_repositories", {"owner": "prathamesh-git9", "limit": 5}
        )

    assert {tool.name for tool in tools.tools} == {
        "list_repositories",
        "repository_detail",
        "search_repository",
        "latest_activity",
    }
    assert {str(resource.uri) for resource in resources.resources} == {
        "github://capabilities",
        "github://safety",
    }
    assert {prompt.name for prompt in prompts.prompts} == {"investigate-repository"}
    assert result.is_error is False
    assert result.structured_content["repositories"][0]["language"] == "Python"
    assert "test-token" not in str(result.structured_content)
