"""Application service for deterministic coding-workflow operations."""

from pathlib import Path

from coding_workflows.checks import CommandRunner, run_configured_checks
from coding_workflows.diffing import propose_commit_message, review_diff
from coding_workflows.models import (
    ChangePlan,
    ChecksSummary,
    CommitProposal,
    DiffReview,
    FailureTriage,
    GateCategory,
    ToolchainReport,
)
from coding_workflows.planning import plan_change
from coding_workflows.toolchain import detect_toolchain, resolve_repository
from coding_workflows.triage import triage_failure


class CodingWorkflowService:
    """Keep local repository access injectable and separate from MCP transport."""

    def __init__(
        self,
        default_repository: str | Path | None = None,
        *,
        runner: CommandRunner | None = None,
    ) -> None:
        self.default_repository = Path(default_repository or Path.cwd())
        self.runner = runner

    def review_diff(self, diff_text: str, *, max_findings: int = 50) -> DiffReview:
        return review_diff(diff_text, max_findings=max_findings)

    def plan_change(
        self,
        request: str,
        *,
        repo_path: str | None = None,
        max_steps: int = 7,
    ) -> ChangePlan:
        repository = self._repository(repo_path) if repo_path else None
        return plan_change(
            request,
            repo_path=str(repository) if repository else None,
            max_steps=max_steps,
        )

    def detect_toolchain(self, repo_path: str | None = None) -> ToolchainReport:
        return detect_toolchain(self._repository(repo_path))

    async def run_checks(
        self,
        *,
        repo_path: str | None = None,
        categories: set[GateCategory] | None = None,
        timeout_seconds: float = 60,
    ) -> ChecksSummary:
        report = self.detect_toolchain(repo_path)
        return await run_configured_checks(
            report,
            categories=categories,
            timeout_seconds=timeout_seconds,
            runner=self.runner,
        )

    def triage_failure(
        self,
        failure_text: str,
        *,
        repo_path: str | None = None,
        max_candidates: int = 10,
    ) -> FailureTriage:
        return triage_failure(
            failure_text,
            str(self._repository(repo_path)),
            max_candidates=max_candidates,
        )

    def commit_message(self, staged_diff: str) -> CommitProposal:
        return propose_commit_message(staged_diff)

    def _repository(self, repo_path: str | None) -> Path:
        return resolve_repository(repo_path or self.default_repository)
