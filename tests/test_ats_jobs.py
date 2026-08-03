import json
from pathlib import Path

import pytest
from ats_jobs.providers import ADAPTERS
from ats_jobs.server import create_server
from ats_jobs.service import AtsJobsService
from mcp import Client

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ats"


@pytest.mark.parametrize(
    "fixture_path",
    sorted(FIXTURE_DIR.glob("*.json")),
    ids=lambda path: path.stem,
)
def test_detects_and_parses_every_provider_from_fixture(fixture_path: Path) -> None:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    service = AtsJobsService()

    board = service.detect_board(fixture["board_url"])
    roles = ADAPTERS[board.provider].parse(board, fixture["payload"])

    assert board.provider.value == fixture["expected_provider"]
    assert roles[0].id == fixture["expected_id"]
    assert roles[0].title
    assert roles[0].url.startswith("https://")
    assert roles[0].source_url == board.api_url


@pytest.mark.asyncio
async def test_ats_protocol_surfaces_and_typed_detection_failure() -> None:
    async with Client(create_server()) as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
        prompts = await client.list_prompts()
        detected = await client.call_tool(
            "detect_board", {"url": "https://jobs.ashbyhq.com/acme"}
        )
        unknown = await client.call_tool(
            "detect_board", {"url": "https://careers.example.com/jobs"}
        )

    assert {tool.name for tool in tools.tools} == {
        "detect_board",
        "list_roles",
        "get_role",
    }
    assert {str(resource.uri) for resource in resources.resources} == {
        "ats://providers",
        "ats://policy",
    }
    assert {prompt.name for prompt in prompts.prompts} == {"find-open-roles"}
    assert detected.structured_content["board"]["provider"] == "ashby"
    assert unknown.structured_content["status"] == "error"
    assert unknown.structured_content["failure"]["code"] == "invalid_input"
