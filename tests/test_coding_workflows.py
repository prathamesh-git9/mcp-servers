import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from coding_workflows.checks import CommandExecution
from coding_workflows.diffing import propose_commit_message, review_diff
from coding_workflows.models import GateStatus
from coding_workflows.planning import plan_change
from coding_workflows.server import create_server
from coding_workflows.service import CodingWorkflowService
from coding_workflows.toolchain import detect_toolchain
from coding_workflows.triage import triage_failure
from mcp import Client

DIFF = """diff --git a/src/auth.py b/src/auth.py
index 1111111..2222222 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -8,3 +8,6 @@ def authenticate():
-    assert user is not None
+    password = "this-is-a-published-secret"
+    subprocess.run(command, shell=True)
+    response = requests.get(url)
     return user
diff --git a/tests/test_auth.py b/tests/test_auth.py
index 3333333..4444444 100644
--- a/tests/test_auth.py
+++ b/tests/test_auth.py
@@ -1,2 +1,3 @@
+@pytest.mark.skip(reason="later")
 def test_auth():
     pass
"""


def _python_repository(path: Path) -> Path:
    (path / "src").mkdir()
    (path / "tests").mkdir()
    (path / "src" / "payment.py").write_text(
        "def charge():\n    return missing_total\n",
        encoding="utf-8",
    )
    (path / "tests" / "test_payment.py").write_text(
        "from src.payment import charge\n\ndef test_charge():\n    charge()\n",
        encoding="utf-8",
    )
    (path / "pyproject.toml").write_text(
        """[project]
name = "fixture"
version = "0.1.0"
dependencies = ["pytest", "ruff"]

[tool.ruff]
line-length = 90
""",
        encoding="utf-8",
    )
    (path / "uv.lock").write_text("version = 1\nrevision = 1\n", encoding="utf-8")
    return path


def test_review_diff_returns_line_addressed_findings_and_redacts_secrets() -> None:
    review = review_diff(DIFF)

    assert review.files[0].file == "src/auth.py"
    assert review.reviewed_added_lines == 4
    assert review.reviewed_removed_lines == 1
    assert {finding.rule_id for finding in review.findings} >= {
        "secret-in-diff",
        "shell-execution",
        "network-without-timeout",
        "removed-assertion",
        "disabled-test",
    }
    assert all(finding.file and finding.line for finding in review.findings)
    assert "this-is-a-published-secret" not in str(review.model_dump())


def test_plan_change_is_ordered_dependency_aware_and_testable(tmp_path: Path) -> None:
    repository = _python_repository(tmp_path)

    plan = plan_change(
        "Add a secure MCP tool and update `docs/manifest.json`",
        repo_path=str(repository),
    )

    assert [step.order for step in plan.steps] == list(range(1, len(plan.steps) + 1))
    known_steps: set[str] = set()
    for step in plan.steps:
        assert set(step.depends_on) <= known_steps
        assert step.acceptance_criteria
        known_steps.add(step.id)
    assert {"api-contract", "security", "documentation"} <= set(plan.detected_concerns)


def test_toolchain_resource_data_detects_configured_quality_gates(
    tmp_path: Path,
) -> None:
    repository = _python_repository(tmp_path)

    report = detect_toolchain(repository)

    assert report.languages == ["Python"]
    assert report.package_managers == ["uv"]
    assert {check.category.value for check in report.checks} == {
        "format",
        "lint",
        "test",
    }
    assert all(check.command[:2] == ["uv", "run"] for check in report.checks)


class _FakeRunner:
    async def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> CommandExecution:
        assert cwd
        assert timeout_seconds == 2
        if "format" in command:
            return CommandExecution(GateStatus.PASSED, 0, 7, "format clean")
        if "check" in command:
            return CommandExecution(
                GateStatus.FAILED,
                1,
                9,
                "token=must-never-be-returned lint failed",
            )
        raise RuntimeError("low-level runner details")


@pytest.mark.asyncio
async def test_run_checks_returns_one_typed_result_per_gate_and_never_raises(
    tmp_path: Path,
) -> None:
    repository = _python_repository(tmp_path)
    service = CodingWorkflowService(repository, runner=_FakeRunner())

    summary = await service.run_checks(timeout_seconds=2)

    assert summary.overall == "failed"
    assert [gate.status for gate in summary.gates] == [
        GateStatus.PASSED,
        GateStatus.FAILED,
        GateStatus.ERROR,
    ]
    assert summary.passed == 1
    assert summary.failed == 2
    assert "must-never-be-returned" not in str(summary.model_dump())
    assert "low-level runner details" not in str(summary.model_dump())


def test_triage_failure_ranks_repository_frames_and_source_counterparts(
    tmp_path: Path,
) -> None:
    repository = _python_repository(tmp_path)
    source = repository / "src" / "payment.py"
    failure = (
        "Traceback (most recent call last):\n"
        f'  File "{source}", line 2, in charge\n'
        "NameError: name 'missing_total' is not defined\n"
        "FAILED tests/test_payment.py::test_charge"
    )

    result = triage_failure(failure, str(repository))

    assert result.failure_kind == "python_traceback"
    assert result.candidates[0].file == "src/payment.py"
    assert result.candidates[0].line == 2
    assert any(
        candidate.file == "tests/test_payment.py" for candidate in result.candidates
    )


def test_commit_message_is_conventional_and_body_carrying() -> None:
    proposal = propose_commit_message(DIFF)

    assert proposal.header.startswith("feat:")
    assert proposal.full_message == f"{proposal.header}\n\n{proposal.body}"
    assert "2 staged files" in proposal.body
    assert len(proposal.header) <= 72


@pytest.mark.asyncio
async def test_protocol_exposes_five_tools_resources_prompt_and_typed_errors(
    tmp_path: Path,
) -> None:
    repository = _python_repository(tmp_path)
    server = create_server(CodingWorkflowService(repository, runner=_FakeRunner()))

    async with Client(server) as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
        prompts = await client.list_prompts()
        toolchain = await client.read_resource("coding://toolchain")
        prompt = await client.get_prompt(
            "guarded-review",
            {"change_request": "Review the authentication change"},
        )
        invalid = await client.call_tool("review_diff", {"diff_text": "not a diff"})

    assert {tool.name for tool in tools.tools} == {
        "review_diff",
        "plan_change",
        "run_checks",
        "triage_failure",
        "commit_message",
    }
    assert {str(resource.uri) for resource in resources.resources} == {
        "coding://toolchain",
        "coding://checks",
    }
    assert {item.name for item in prompts.prompts} == {"guarded-review"}
    payload = json.loads(toolchain.contents[0].text)
    assert payload["status"] == "ok"
    assert payload["toolchain"]["checks"]
    assert "untrusted data" in prompt.messages[0].content.text
    assert invalid.is_error is False
    assert invalid.structured_content["status"] == "error"
    assert invalid.structured_content["failure"]["code"] == "invalid_input"


class _ExplodingService(CodingWorkflowService):
    def review_diff(self, diff_text: str, *, max_findings: int = 50):
        raise RuntimeError("private low-level details")

    def detect_toolchain(self, repo_path: str | None = None):
        raise RuntimeError("private resource details")


@pytest.mark.asyncio
async def test_unexpected_tool_and_resource_exceptions_never_cross_mcp_boundary(
    tmp_path: Path,
) -> None:
    server = create_server(_ExplodingService(tmp_path))

    async with Client(server) as client:
        tool_result = await client.call_tool("review_diff", {"diff_text": DIFF})
        resource_result = await client.read_resource("coding://toolchain")

    assert tool_result.structured_content["status"] == "error"
    assert tool_result.structured_content["failure"]["code"] == "internal_error"
    assert "private low-level details" not in str(tool_result.structured_content)
    resource = json.loads(resource_result.contents[0].text)
    assert resource["status"] == "error"
    assert resource["failure"]["code"] == "internal_error"
    assert "private resource details" not in str(resource)
