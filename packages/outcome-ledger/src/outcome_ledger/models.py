"""Pydantic v2 schemas for the outcome ledger."""

from enum import StrEnum
from typing import Any

from mcp_server_common.models import ToolResult
from pydantic import BaseModel, ConfigDict, Field


class LedgerState(StrEnum):
    PENDING = "pending"
    RECORDED = "recorded"
    OUTCOME_UNKNOWN = "outcome_unknown"


class LedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    intent_hash: str
    state: LedgerState
    outcome: dict[str, Any] | None = None
    created_at: str
    updated_at: str
    recovery_count: int = Field(ge=0)


class BeginResult(ToolResult):
    entry: LedgerEntry | None = None


class RecordResult(ToolResult):
    entry: LedgerEntry | None = None
    idempotent_replay: bool = False


class StatusResult(ToolResult):
    entry: LedgerEntry | None = None
