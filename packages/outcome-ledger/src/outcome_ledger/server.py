"""MCP stdio server for crash-aware idempotency state."""

import asyncio
import json
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp_server_common import run_bounded
from pydantic import Field

from outcome_ledger.models import BeginResult, RecordResult, StatusResult
from outcome_ledger.store import OutcomeLedgerStore

CALL_TIMEOUT_SECONDS = 5.0


def create_server(store: OutcomeLedgerStore | None = None) -> MCPServer:
    ledger = store or OutcomeLedgerStore()
    server = MCPServer(
        "outcome-ledger",
        description="Crash-aware durable idempotency state for external effects.",
        version="0.1.0",
    )

    @server.tool(structured_output=True)
    async def begin(
        intent: Annotated[dict[str, Any], Field(min_length=1)],
    ) -> BeginResult:
        """Durably begin an intent and return its deterministic idempotency key."""

        outcome = await run_bounded(
            lambda: asyncio.to_thread(ledger.begin, intent),
            timeout_seconds=CALL_TIMEOUT_SECONDS,
        )
        if outcome.failure:
            return BeginResult(
                status="error",
                failure=outcome.failure,
                duration_ms=outcome.duration_ms,
            )
        return BeginResult(
            status="ok",
            duration_ms=outcome.duration_ms,
            entry=outcome.value,
        )

    @server.tool(structured_output=True)
    async def record(
        key: Annotated[str, Field(pattern=r"^intent_[a-f0-9]{64}$")],
        outcome: Annotated[dict[str, Any], Field(min_length=1)],
    ) -> RecordResult:
        """Record a proven outcome; replay safely and reject conflicts."""

        bounded = await run_bounded(
            lambda: asyncio.to_thread(ledger.record, key, outcome),
            timeout_seconds=CALL_TIMEOUT_SECONDS,
        )
        if bounded.failure:
            return RecordResult(
                status="error",
                failure=bounded.failure,
                duration_ms=bounded.duration_ms,
            )
        entry, replay = bounded.value or (None, False)
        return RecordResult(
            status="ok",
            duration_ms=bounded.duration_ms,
            entry=entry,
            idempotent_replay=replay,
        )

    @server.tool(structured_output=True)
    async def status(
        key: Annotated[str, Field(pattern=r"^intent_[a-f0-9]{64}$")],
    ) -> StatusResult:
        """Return pending, recorded, or the honest post-restart outcome_unknown state."""

        outcome = await run_bounded(
            lambda: asyncio.to_thread(ledger.status, key),
            timeout_seconds=CALL_TIMEOUT_SECONDS,
        )
        if outcome.failure:
            return StatusResult(
                status="error",
                failure=outcome.failure,
                duration_ms=outcome.duration_ms,
            )
        return StatusResult(
            status="ok",
            duration_ms=outcome.duration_ms,
            entry=outcome.value,
        )

    @server.resource(
        "ledger://semantics",
        name="outcome-ledger-semantics",
        description="State-machine semantics and caller obligations.",
        mime_type="application/json",
    )
    def semantics_resource() -> str:
        return json.dumps(
            {
                "states": {
                    "pending": "begin committed in this process",
                    "recorded": "the exact outcome is durably known",
                    "outcome_unknown": (
                        "a restart occurred before an outcome was recorded"
                    ),
                },
                "unknown_rule": "reconcile externally; never blindly repeat the effect",
            }
        )

    @server.resource(
        "ledger://durability",
        name="outcome-ledger-durability",
        description="SQLite transaction and recovery guarantees.",
        mime_type="application/json",
    )
    def durability_resource() -> str:
        return json.dumps(
            {
                "journal_mode": "WAL",
                "synchronous": "FULL",
                "transactions": "BEGIN IMMEDIATE",
                "stable_key": "sha256 of canonical intent JSON",
            }
        )

    @server.prompt(
        name="perform-idempotently",
        description="Plan a side effect around the outcome ledger state machine.",
    )
    def perform_idempotently(intent_description: str) -> str:
        return (
            f"For this external effect: {intent_description}, call begin first and use "
            "its "
            "key with the destination. Record only a proven outcome. If status is "
            "outcome_unknown, reconcile with the destination and do not retry blindly."
        )

    return server


def main() -> None:
    create_server().run("stdio")


if __name__ == "__main__":
    main()
