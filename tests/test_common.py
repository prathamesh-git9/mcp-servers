import asyncio

import pytest
from mcp_server_common import FailureCode, run_bounded
from mcp_server_common.redaction import redact, safe_source_url


@pytest.mark.asyncio
async def test_deadline_returns_typed_timeout() -> None:
    async def slow() -> str:
        await asyncio.sleep(0.05)
        return "late"

    outcome = await run_bounded(slow, timeout_seconds=0.001)

    assert outcome.value is None
    assert outcome.failure is not None
    assert outcome.failure.code == FailureCode.TIMEOUT


def test_redaction_and_source_url_strip_secrets() -> None:
    assert "ghp_" not in redact("token=ghp_abcdefghijklmnopqrstuvwxyz")
    assert safe_source_url("https://user:secret@example.com/path?q=token#x") == (
        "https://example.com/path"
    )
