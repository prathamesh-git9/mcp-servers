"""Pydantic v2 schemas for ATS discovery."""

from enum import StrEnum

from mcp_server_common.models import ToolResult
from pydantic import BaseModel, ConfigDict, Field


class Provider(StrEnum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    WORKABLE = "workable"
    SMARTRECRUITERS = "smartrecruiters"
    RECRUITEE = "recruitee"


class BoardRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Provider
    token: str
    board_url: str
    api_url: str


class JobRole(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    provider: Provider
    company: str
    title: str
    location: str | None = None
    department: str | None = None
    employment_type: str | None = None
    workplace_type: str | None = None
    description: str | None = None
    url: str
    apply_url: str | None = None
    posted_at: str | None = None
    source_url: str


class DetectBoardResult(ToolResult):
    board: BoardRef | None = None


class RoleListResult(ToolResult):
    board: BoardRef | None = None
    roles: list[JobRole] = Field(default_factory=list)


class RoleDetailResult(ToolResult):
    board: BoardRef | None = None
    role: JobRole | None = None
