"""Pydantic v2 schemas for repository intelligence."""

from mcp_server_common.models import ToolResult
from pydantic import BaseModel, ConfigDict, Field


class RepositorySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: str
    name: str
    description: str | None = None
    url: str
    default_branch: str
    stars: int = Field(ge=0)
    forks: int = Field(ge=0)
    open_issues: int = Field(ge=0)
    language: str | None = None
    topics: list[str] = Field(default_factory=list)
    archived: bool = False
    updated_at: str | None = None


class RepositoryListResult(ToolResult):
    owner: str
    repositories: list[RepositorySummary] = Field(default_factory=list)


class WorkflowSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: str
    conclusion: str | None = None
    branch: str | None = None
    url: str
    created_at: str | None = None


class RepositoryDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: RepositorySummary
    languages: dict[str, int] = Field(default_factory=dict)
    readme_excerpt: str | None = None
    latest_ci: WorkflowSnapshot | None = None


class RepositoryDetailResult(ToolResult):
    detail: RepositoryDetail | None = None


class CodeMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    repository: str
    url: str
    score: float = Field(ge=0)
    text_matches: list[str] = Field(default_factory=list)


class RepositorySearchResult(ToolResult):
    query: str
    matches: list[CodeMatch] = Field(default_factory=list)


class ActivityItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    title: str
    state: str | None = None
    author: str | None = None
    created_at: str | None = None
    url: str


class ActivityResult(ToolResult):
    repository: str
    activity: list[ActivityItem] = Field(default_factory=list)
