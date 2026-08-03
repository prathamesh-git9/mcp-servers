"""Durable SQLite state transitions for honest idempotency reporting."""

import hashlib
import json
import os
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp_server_common import Failure, FailureCode, ServiceError

from outcome_ledger.models import LedgerEntry, LedgerState

_MAX_JSON_BYTES = 65_536
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
}


class OutcomeLedgerStore:
    """One durable ledger; opening it is the simulated process-restart boundary."""

    def __init__(self, database_path: Path | str | None = None) -> None:
        self.database_path = Path(database_path or default_database_path()).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_and_recover()

    def begin(self, intent: dict[str, Any]) -> LedgerEntry:
        intent_json = _canonical_json(intent, label="intent")
        intent_hash = hashlib.sha256(intent_json.encode()).hexdigest()
        key = f"intent_{intent_hash}"
        now = _now()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM ledger_entries WHERE key = ?", (key,)
            ).fetchone()
            if existing is not None:
                if existing["intent_hash"] != intent_hash:
                    raise _conflict("The idempotency key belongs to another intent.")
                return _entry(existing)
            connection.execute(
                """
                INSERT INTO ledger_entries (
                    key, intent_hash, intent_json, state, outcome_json,
                    created_at, updated_at, recovery_count
                ) VALUES (?, ?, ?, ?, NULL, ?, ?, 0)
                """,
                (
                    key,
                    intent_hash,
                    intent_json,
                    LedgerState.PENDING.value,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM ledger_entries WHERE key = ?", (key,)
            ).fetchone()
            return _entry(row)

    def record(self, key: str, outcome: dict[str, Any]) -> tuple[LedgerEntry, bool]:
        outcome_json = _canonical_json(outcome, label="outcome")
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM ledger_entries WHERE key = ?", (key,)
            ).fetchone()
            if existing is None:
                raise _not_found()
            if existing["state"] == LedgerState.RECORDED.value:
                if existing["outcome_json"] != outcome_json:
                    raise _conflict(
                        "A different outcome is already recorded for this key."
                    )
                return _entry(existing), True
            connection.execute(
                """
                UPDATE ledger_entries
                SET state = ?, outcome_json = ?, updated_at = ?
                WHERE key = ?
                """,
                (LedgerState.RECORDED.value, outcome_json, _now(), key),
            )
            row = connection.execute(
                "SELECT * FROM ledger_entries WHERE key = ?", (key,)
            ).fetchone()
            return _entry(row), False

    def status(self, key: str) -> LedgerEntry:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ledger_entries WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            raise _not_found()
        return _entry(row)

    def _initialize_and_recover(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ledger_entries (
                    key TEXT PRIMARY KEY,
                    intent_hash TEXT NOT NULL,
                    intent_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (
                        state IN ('pending', 'recorded', 'outcome_unknown')
                    ),
                    outcome_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    recovery_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    UPDATE ledger_entries
                    SET state = 'outcome_unknown',
                        updated_at = ?,
                        recovery_count = recovery_count + 1
                    WHERE state = 'pending'
                    """,
                    (_now(),),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.database_path,
            timeout=3,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 3000")
        connection.execute("PRAGMA synchronous = FULL")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise


def default_database_path() -> Path:
    configured = os.getenv("OUTCOME_LEDGER_DB")
    if configured:
        return Path(configured)
    if os.name == "nt" and os.getenv("LOCALAPPDATA"):
        root = Path(os.environ["LOCALAPPDATA"])
    elif os.getenv("XDG_DATA_HOME"):
        root = Path(os.environ["XDG_DATA_HOME"])
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path.home() / ".local" / "share"
    return root / "prathamesh-mcp-servers" / "outcome-ledger.sqlite3"


def _canonical_json(value: dict[str, Any], *, label: str) -> str:
    if not value:
        raise ServiceError(
            Failure(
                code=FailureCode.INVALID_INPUT,
                message=f"{label} must contain at least one field.",
            )
        )
    sensitive = _find_sensitive_key(value)
    if sensitive:
        raise ServiceError(
            Failure(
                code=FailureCode.INVALID_INPUT,
                message=(
                    f"{label} contains a secret-like field; store a reference instead."
                ),
                details={"field": sensitive},
            )
        )
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ServiceError(
            Failure(
                code=FailureCode.INVALID_INPUT,
                message=f"{label} must be a valid JSON object.",
            )
        ) from exc
    if len(encoded.encode()) > _MAX_JSON_BYTES:
        raise ServiceError(
            Failure(
                code=FailureCode.INVALID_INPUT,
                message=f"{label} exceeds the {_MAX_JSON_BYTES}-byte limit.",
            )
        )
    return encoded


def _find_sensitive_key(value: object) -> str | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if normalized in _SENSITIVE_KEYS:
                return str(key)
            found = _find_sensitive_key(nested)
            if found:
                return found
    if isinstance(value, list):
        for nested in value:
            found = _find_sensitive_key(nested)
            if found:
                return found
    return None


def _entry(row: sqlite3.Row) -> LedgerEntry:
    return LedgerEntry(
        key=row["key"],
        intent_hash=row["intent_hash"],
        state=LedgerState(row["state"]),
        outcome=json.loads(row["outcome_json"]) if row["outcome_json"] else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        recovery_count=row["recovery_count"],
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _not_found() -> ServiceError:
    return ServiceError(
        Failure(
            code=FailureCode.NOT_FOUND,
            message="No ledger entry exists for this idempotency key.",
        )
    )


def _conflict(message: str) -> ServiceError:
    return ServiceError(
        Failure(code=FailureCode.CONFLICT, message=message, retryable=False)
    )
