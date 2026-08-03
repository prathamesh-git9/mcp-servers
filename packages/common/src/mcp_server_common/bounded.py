"""Per-call deadlines with exception-to-value conversion."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Generic, TypeVar

from mcp_server_common.models import Failure, FailureCode
from mcp_server_common.redaction import safe_error_message

T = TypeVar("T")


class ServiceError(Exception):
    """Internal control flow that becomes a typed public failure at the tool boundary."""

    def __init__(self, failure: Failure) -> None:
        super().__init__(failure.code)
        self.failure = failure


@dataclass(frozen=True, slots=True)
class BoundedOutcome(Generic[T]):
    value: T | None
    failure: Failure | None
    duration_ms: int


async def run_bounded(
    operation: Callable[[], Awaitable[T]], *, timeout_seconds: float
) -> BoundedOutcome[T]:
    """Run one operation under an absolute deadline and return a typed outcome."""

    started = monotonic()
    try:
        async with asyncio.timeout(timeout_seconds):
            value = await operation()
    except TimeoutError:
        failure = Failure(
            code=FailureCode.TIMEOUT,
            message=f"Operation exceeded its {timeout_seconds:g}s deadline.",
            retryable=True,
        )
        return BoundedOutcome(None, failure, _elapsed_ms(started))
    except ServiceError as exc:
        return BoundedOutcome(None, exc.failure, _elapsed_ms(started))
    except Exception as exc:
        failure = Failure(
            code=FailureCode.INTERNAL_ERROR,
            message=safe_error_message(exc),
            retryable=False,
        )
        return BoundedOutcome(None, failure, _elapsed_ms(started))
    return BoundedOutcome(value, None, _elapsed_ms(started))


def _elapsed_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))
