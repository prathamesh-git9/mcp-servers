from pathlib import Path

import pytest
from mcp import Client
from mcp_server_common import FailureCode, ServiceError
from outcome_ledger.models import LedgerState
from outcome_ledger.server import create_server
from outcome_ledger.store import OutcomeLedgerStore


def test_simulated_restart_marks_unrecorded_outcome_unknown(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    before_crash = OutcomeLedgerStore(database)
    begun = before_crash.begin(
        {"operation": "send", "recipient": "candidate@example.com"}
    )

    assert begun.state == LedgerState.PENDING

    after_restart = OutcomeLedgerStore(database)
    recovered = after_restart.status(begun.key)

    assert recovered.state == LedgerState.OUTCOME_UNKNOWN
    assert recovered.outcome is None
    assert recovered.recovery_count == 1

    recorded, replay = after_restart.record(
        begun.key, {"provider_id": "msg-101", "delivered": True}
    )
    final_restart = OutcomeLedgerStore(database)

    assert replay is False
    assert recorded.state == LedgerState.RECORDED
    assert final_restart.status(begun.key).outcome == {
        "provider_id": "msg-101",
        "delivered": True,
    }


def test_begin_and_record_are_idempotent_and_conflicts_do_not_overwrite(
    tmp_path: Path,
) -> None:
    store = OutcomeLedgerStore(tmp_path / "ledger.sqlite3")
    intent = {"operation": "create_ticket", "external_ref": "candidate-42"}
    first = store.begin(intent)
    second = store.begin({"external_ref": "candidate-42", "operation": "create_ticket"})
    _, replay = store.record(first.key, {"ticket_id": "T-42"})

    assert first.key == second.key
    assert replay is False
    assert store.record(first.key, {"ticket_id": "T-42"})[1] is True

    with pytest.raises(ServiceError) as conflict:
        store.record(first.key, {"ticket_id": "T-99"})

    assert conflict.value.failure.code == FailureCode.CONFLICT
    assert store.status(first.key).outcome == {"ticket_id": "T-42"}


@pytest.mark.asyncio
async def test_ledger_protocol_surfaces_and_never_raises_tool_errors(
    tmp_path: Path,
) -> None:
    server = create_server(OutcomeLedgerStore(tmp_path / "protocol.sqlite3"))
    async with Client(server) as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
        prompts = await client.list_prompts()
        begun = await client.call_tool(
            "begin", {"intent": {"operation": "notify", "reference": "abc"}}
        )
        key = begun.structured_content["entry"]["key"]
        missing = await client.call_tool("status", {"key": "intent_" + "0" * 64})
        first_record = await client.call_tool(
            "record", {"key": key, "outcome": {"message_id": "M-1"}}
        )
        replay = await client.call_tool(
            "record", {"key": key, "outcome": {"message_id": "M-1"}}
        )

    assert {tool.name for tool in tools.tools} == {"begin", "record", "status"}
    assert {str(resource.uri) for resource in resources.resources} == {
        "ledger://semantics",
        "ledger://durability",
    }
    assert {prompt.name for prompt in prompts.prompts} == {"perform-idempotently"}
    assert begun.structured_content["entry"]["state"] == "pending"
    assert missing.structured_content["failure"]["code"] == "not_found"
    assert first_record.structured_content["idempotent_replay"] is False
    assert replay.structured_content["idempotent_replay"] is True
