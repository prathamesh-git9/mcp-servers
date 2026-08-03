"""Stable response envelopes shared by every server."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FailureCode(StrEnum):
    """Machine-readable failure categories exposed to MCP clients."""

    INVALID_INPUT = "invalid_input"
    TIMEOUT = "timeout"
    BLOCKED = "blocked"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    UPSTREAM_ERROR = "upstream_error"
    PARSE_ERROR = "parse_error"
    CONFIGURATION_ERROR = "configuration_error"
    CONFLICT = "conflict"
    INTERNAL_ERROR = "internal_error"


class Failure(BaseModel):
    """A safe failure value; messages must not contain fetched content or secrets."""

    model_config = ConfigDict(extra="forbid")

    code: FailureCode
    message: str = Field(min_length=1, max_length=300)
    retryable: bool = False
    source_url: str | None = None
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class SourceRef(BaseModel):
    """A public source attached to returned intelligence."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)
    label: str = Field(min_length=1, max_length=200)


class ToolResult(BaseModel):
    """Common MCP tool result metadata."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "error"]
    failure: Failure | None = None
    duration_ms: int = Field(ge=0)
    sources: list[SourceRef] = Field(default_factory=list)
    content_is_untrusted: bool = False

    @model_validator(mode="after")
    def failure_matches_status(self) -> "ToolResult":
        if self.status == "error" and self.failure is None:
            raise ValueError("error results require failure")
        if self.status == "ok" and self.failure is not None:
            raise ValueError("successful results cannot contain failure")
        return self


JsonObject = dict[str, Any]
