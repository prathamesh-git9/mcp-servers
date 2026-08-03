"""Bounded, shell-free execution of repository quality gates."""

import asyncio
import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Protocol

from mcp_server_common.redaction import redact

from coding_workflows.models import (
    ChecksSummary,
    GateCategory,
    GateResult,
    GateStatus,
    ToolchainReport,
)

_MAX_OUTPUT_CHARACTERS = 12_000
_ENVIRONMENT_ALLOWLIST = {
    "APPDATA",
    "COMSPEC",
    "HOME",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "UV_CACHE_DIR",
    "VIRTUAL_ENV",
    "WINDIR",
}


@dataclass(frozen=True, slots=True)
class CommandExecution:
    """Raw command outcome returned by an injectable process runner."""

    status: GateStatus
    exit_code: int | None
    duration_ms: int
    output: str


class CommandRunner(Protocol):
    """Narrow command boundary used by the offline test suite."""

    async def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> CommandExecution: ...


class SubprocessRunner:
    """Execute an argv list directly; never invoke a command shell."""

    async def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> CommandExecution:
        started = monotonic()
        environment = _safe_environment()
        executable = command[0]
        if shutil.which(executable, path=environment.get("PATH")) is None:
            return CommandExecution(
                status=GateStatus.UNAVAILABLE,
                exit_code=None,
                duration_ms=_elapsed_ms(started),
                output=f"Required executable is unavailable: {executable}",
            )

        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                env=environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            async with asyncio.timeout(timeout_seconds):
                raw_output, _ = await process.communicate()
        except TimeoutError:
            if process is not None:
                process.kill()
                await process.wait()
            return CommandExecution(
                status=GateStatus.TIMED_OUT,
                exit_code=None,
                duration_ms=_elapsed_ms(started),
                output=f"Gate exceeded its {timeout_seconds:g}s deadline.",
            )
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            raise
        except (OSError, ValueError):
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            return CommandExecution(
                status=GateStatus.ERROR,
                exit_code=None,
                duration_ms=_elapsed_ms(started),
                output="The quality gate could not be started safely.",
            )

        exit_code = process.returncode
        return CommandExecution(
            status=GateStatus.PASSED if exit_code == 0 else GateStatus.FAILED,
            exit_code=exit_code,
            duration_ms=_elapsed_ms(started),
            output=_safe_output(raw_output.decode("utf-8", errors="replace")),
        )


async def run_configured_checks(
    report: ToolchainReport,
    *,
    categories: set[GateCategory] | None = None,
    timeout_seconds: float = 60,
    runner: CommandRunner | None = None,
) -> ChecksSummary:
    """Run detected gates in order and convert every gate failure into data."""

    repository = Path(report.repository)
    selected = [
        check
        for check in report.checks
        if categories is None or check.category in categories
    ]
    process_runner = runner or SubprocessRunner()
    gates: list[GateResult] = []
    for check in selected:
        try:
            execution = await process_runner.run(
                check.command,
                cwd=repository,
                timeout_seconds=timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            execution = CommandExecution(
                status=GateStatus.ERROR,
                exit_code=None,
                duration_ms=0,
                output="The quality gate failed safely inside the runner boundary.",
            )
        if (
            check.pass_condition == "exit_zero_and_empty_output"
            and execution.status == GateStatus.PASSED
            and execution.output.strip()
        ):
            execution = CommandExecution(
                status=GateStatus.FAILED,
                exit_code=execution.exit_code,
                duration_ms=execution.duration_ms,
                output=execution.output,
            )
        gates.append(
            GateResult(
                name=check.name,
                category=check.category,
                command=check.command,
                status=execution.status,
                exit_code=execution.exit_code,
                duration_ms=execution.duration_ms,
                output=_safe_output(execution.output),
            )
        )

    passed = sum(gate.status == GateStatus.PASSED for gate in gates)
    failed = len(gates) - passed
    return ChecksSummary(
        repository=report.repository,
        overall="passed" if gates and failed == 0 else "failed",
        gates=gates,
        passed=passed,
        failed=failed,
    )


def _safe_environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if key in _ENVIRONMENT_ALLOWLIST
    }
    environment.update({"CI": "true", "NO_COLOR": "1", "PYTHONUNBUFFERED": "1"})
    return environment


def _safe_output(value: str) -> str:
    cleaned = redact(value).replace("\x00", "")
    if len(cleaned) <= _MAX_OUTPUT_CHARACTERS:
        return cleaned
    omitted = len(cleaned) - _MAX_OUTPUT_CHARACTERS
    return f"{cleaned[:_MAX_OUTPUT_CHARACTERS]}\n... {omitted} characters omitted"


def _elapsed_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))
