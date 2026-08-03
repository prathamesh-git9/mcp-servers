"""Pydantic v2 schemas for coding workflow tools."""

from enum import StrEnum
from typing import Literal

from mcp_server_common.models import ToolResult
from pydantic import BaseModel, ConfigDict, Field


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingCategory(StrEnum):
    SECURITY = "security"
    CORRECTNESS = "correctness"
    RELIABILITY = "reliability"
    PERFORMANCE = "performance"
    TESTING = "testing"
    MAINTAINABILITY = "maintainability"


class DiffFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    line: int | None = Field(default=None, ge=1)
    side: Literal["new", "old"]
    severity: Severity
    category: FindingCategory
    rationale: str
    evidence: str | None = None
    rule_id: str


class DiffFileSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    change_type: Literal["added", "modified", "deleted", "renamed"]
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)


class DiffReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[DiffFileSummary] = Field(default_factory=list)
    findings: list[DiffFinding] = Field(default_factory=list)
    finding_counts: dict[Severity, int] = Field(default_factory=dict)
    truncated: bool = False
    reviewed_added_lines: int = Field(ge=0)
    reviewed_removed_lines: int = Field(ge=0)


class ReviewDiffResult(ToolResult):
    review: DiffReview | None = None


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    order: int = Field(ge=1)
    title: str
    description: str
    depends_on: list[str] = Field(default_factory=list)
    likely_files: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(min_length=1)


class ChangePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: str
    repository: str | None = None
    detected_concerns: list[str] = Field(default_factory=list)
    steps: list[PlanStep] = Field(min_length=1)


class PlanChangeResult(ToolResult):
    plan: ChangePlan | None = None


class GateCategory(StrEnum):
    FORMAT = "format"
    LINT = "lint"
    TEST = "test"


class CheckCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    category: GateCategory
    command: list[str] = Field(min_length=1)
    source: str
    pass_condition: Literal["exit_zero", "exit_zero_and_empty_output"] = "exit_zero"


class ToolchainReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str
    languages: list[str] = Field(default_factory=list)
    package_managers: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    config_files: list[str] = Field(default_factory=list)
    checks: list[CheckCommand] = Field(default_factory=list)


class GateStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class GateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    category: GateCategory
    command: list[str]
    status: GateStatus
    exit_code: int | None = None
    duration_ms: int = Field(ge=0)
    output: str = ""


class ChecksSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str
    overall: Literal["passed", "failed"]
    gates: list[GateResult] = Field(default_factory=list)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)


class RunChecksResult(ToolResult):
    summary: ChecksSummary | None = None


class FileCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    line: int | None = Field(default=None, ge=1)
    score: float = Field(ge=0, le=1)
    reasons: list[str] = Field(min_length=1)
    matched_symbols: list[str] = Field(default_factory=list)


class FailureTriage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str
    failure_kind: str
    extracted_symbols: list[str] = Field(default_factory=list)
    candidates: list[FileCandidate] = Field(default_factory=list)


class TriageFailureResult(ToolResult):
    triage: FailureTriage | None = None


class CommitProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "feat", "fix", "docs", "test", "refactor", "perf", "build", "ci", "chore"
    ]
    scope: str | None = None
    subject: str
    header: str
    body: str
    breaking_change: bool = False
    full_message: str


class CommitMessageResult(ToolResult):
    proposal: CommitProposal | None = None
