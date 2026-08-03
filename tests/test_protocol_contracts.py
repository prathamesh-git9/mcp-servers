import json
from pathlib import Path

import pytest
from ats_jobs.server import create_server as create_ats_server
from grounded_cv.server import create_server as create_cv_server
from mcp import Client
from mcp_server_common import FailureCode, run_bounded
from outcome_ledger.server import create_server as create_ledger_server
from outcome_ledger.store import OutcomeLedgerStore
from repo_intel.server import create_server as create_repo_server
from web_research.server import create_server as create_web_server

ROOT = Path(__file__).parents[1]


@pytest.mark.asyncio
async def test_shared_boundary_converts_unexpected_exceptions() -> None:
    async def explode() -> None:
        raise RuntimeError("secret upstream details must never cross the boundary")

    outcome = await run_bounded(explode, timeout_seconds=1)

    assert outcome.failure is not None
    assert outcome.failure.code == FailureCode.INTERNAL_ERROR
    assert "secret upstream" not in outcome.failure.message


@pytest.mark.asyncio
async def test_manifest_matches_live_protocol_and_every_tool_has_output_schema(
    tmp_path: Path,
) -> None:
    manifest = json.loads((ROOT / "docs" / "manifest.json").read_text(encoding="utf-8"))
    factories = {
        "grounded-cv": create_cv_server,
        "repo-intel": create_repo_server,
        "web-research": create_web_server,
        "ats-jobs": create_ats_server,
        "outcome-ledger": lambda: create_ledger_server(
            OutcomeLedgerStore(tmp_path / "manifest-ledger.sqlite3")
        ),
    }

    assert set(manifest) == {
        "schema_version",
        "repository",
        "transport",
        "sdk",
        "servers",
    }
    assert manifest["schema_version"] == "1.0.0"
    assert manifest["transport"] == "stdio"
    assert {item["name"] for item in manifest["servers"]} == set(factories)

    for item in manifest["servers"]:
        assert set(item) == {
            "name",
            "description",
            "tools",
            "resources",
            "prompts",
            "install_command",
            "eval",
        }
        assert item["install_command"].endswith(f" {item['name']}")
        async with Client(factories[item["name"]]()) as client:
            tools = await client.list_tools()
            resources = await client.list_resources()
            templates = await client.list_resource_templates()
            prompts = await client.list_prompts()

        live_resources = {str(resource.uri) for resource in resources.resources}
        live_resources.update(
            str(template.uri_template) for template in templates.resource_templates
        )
        assert {tool["name"] for tool in item["tools"]} == {
            tool.name for tool in tools.tools
        }
        assert {resource["uri"] for resource in item["resources"]} == live_resources
        assert {prompt["name"] for prompt in item["prompts"]} == {
            prompt.name for prompt in prompts.prompts
        }
        assert all(tool.input_schema for tool in tools.tools)
        assert all(tool.output_schema for tool in tools.tools)
        assert all("status" in tool.output_schema["properties"] for tool in tools.tools)


def test_manifest_eval_matches_committed_readme_numbers() -> None:
    manifest = json.loads((ROOT / "docs" / "manifest.json").read_text(encoding="utf-8"))
    grounded = next(item for item in manifest["servers"] if item["name"] == "grounded-cv")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert grounded["eval"] == {
        "query_count": 10,
        "k": 5,
        "recall_at_k": 1.0,
        "mrr": 1.0,
        "ndcg_at_k": 0.992,
    }
    assert "recall@5 | **1.0000**" in readme
    assert "MRR | **1.0000**" in readme
    assert "nDCG@5 | **0.9920**" in readme
