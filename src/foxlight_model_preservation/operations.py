"""Bounded observations and cooperative control for one worker invocation."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from threading import Event
from typing import ClassVar, Literal, final

from pydantic import Field

from .models import Digest, PreservationError, Record
from .signing import digest, encode_record

Operation = Literal[
    "bucket_publish",
    "pack",
    "verify",
    "restore",
    "fetch",
    "plan_pack",
    "plan_restore",
    "plan_fetch",
    "seed",
    "kit_export",
    "kit_import",
    "pirateface_lookup",
    "pirateface_import",
    "i2p_init",
    "i2p_export",
    "i2p_receive",
    "i2p_share",
    "mirror_retain",
    "mirror_recover",
]
Stage = Literal[
    "starting",
    "inventory",
    "checking",
    "copying",
    "downloading",
    "publishing",
    "sharing",
    "complete",
    "cancelled",
    "failed",
]
ErrorCode = Literal[
    "cancelled",
    "budget_exceeded",
    "deadline_exceeded",
    "request_changed",
    "invalid_or_unsafe",
    "storage_error",
    "operation_failed",
]


class OperationError(PreservationError):
    """A structured worker failure which supplier fallback must never swallow."""

    code: ClassVar[ErrorCode] = "operation_failed"


class OperationCancelled(OperationError):
    """The worker stopped at a checkpoint before publishing completion."""

    code: ClassVar[ErrorCode] = "cancelled"


class BudgetExceeded(OperationError):
    """The selected cumulative object-transfer allowance was exhausted."""

    code: ClassVar[ErrorCode] = "budget_exceeded"


class DeadlineExceeded(OperationError):
    """The bounded invocation reached its transfer deadline."""

    code: ClassVar[ErrorCode] = "deadline_exceeded"


class RequestChanged(OperationError):
    """An operation identity was reused with different immutable inputs."""

    code: ClassVar[ErrorCode] = "request_changed"


def failure_code(error: BaseException) -> ErrorCode:
    """Classify failures without exposing paths, URLs, secrets or exception text."""
    if isinstance(error, OperationError):
        return error.code
    if isinstance(error, ValueError):
        return "invalid_or_unsafe"
    if isinstance(error, OSError):
        return "storage_error"
    return "operation_failed"


@final
class Progress(Record):
    """One bounded snapshot; byte counters are cumulative work, not percentages."""

    operation: Operation
    stage: Stage
    bytes_hashed: int = Field(default=0, ge=0)
    bytes_copied: int = Field(default=0, ge=0)
    bytes_received: int = Field(default=0, ge=0)
    bytes_reused: int = Field(default=0, ge=0)
    committed: bool = False
    error_code: ErrorCode | None = None
    observer_failed: bool = False


@final
class OperationPlan(Record):
    """Read-only observations requiring execution-time revalidation and authority."""

    operation: Literal["pack", "restore", "fetch"]
    manifest_id: Digest
    request_digest: Digest
    logical_bytes: int = Field(ge=0)
    unique_bytes: int = Field(ge=0)
    reused_bytes: int = Field(ge=0)
    partial_bytes: int = Field(ge=0)
    additional_bytes: int = Field(ge=0)
    metadata_bytes: int = Field(ge=0)
    reserve_bytes: int = Field(ge=0)

    @property
    def plan_id(self) -> str:
        """Identify this exact observation without granting execution permission."""
        return digest(encode_record(self))


@final
class OperationControl:
    """Single-worker control; only cancel() is intended for concurrent callers.

    Observers run synchronously and must be short. An observer exception disables
    observation and sets observer_failed; it cannot change publication outcome.
    Each invocation needs a fresh control, including a retry of a durable job.
    """

    def __init__(
        self,
        observer: Callable[[Progress], None] | None = None,
        *,
        interval_seconds: float = 0.1,
    ) -> None:
        if not math.isfinite(interval_seconds) or interval_seconds < 0:
            raise ValueError("progress interval must be finite and nonnegative")
        self._cancelled = Event()
        self._observer = observer
        self._interval = interval_seconds
        self._last_report = 0.0
        self._used = False
        self._deadline: float | None = None
        self._snapshot = Progress(operation="verify", stage="starting")

    @property
    def progress(self) -> Progress:
        """Return the latest immutable snapshot without retaining an event history."""
        return self._snapshot

    def cancel(self) -> None:
        """Request cooperative stop; the worker's terminal outcome confirms it."""
        self._cancelled.set()

    def fork(self) -> OperationControl:
        """Return a fresh control for one nested invocation of a retrying worker.

        The child shares this control's stop request, deadline and observer, so a
        cancellation of either stops both. Use one per attempt; never reuse it.
        """
        child = OperationControl(self._observer, interval_seconds=self._interval)
        child._cancelled = self._cancelled
        child._deadline = self._deadline
        return child

    def limit_deadline(self, deadline: float) -> None:
        """Apply an absolute monotonic deadline without extending an existing one."""
        if not math.isfinite(deadline):
            raise ValueError("deadline must be finite")
        self._deadline = min(self._deadline or deadline, deadline)

    def checkpoint(self) -> None:
        """Stop before the next effect; an already committed result wins the race."""
        if self._cancelled.is_set() and not self._snapshot.committed:
            raise OperationCancelled("operation cancelled")
        if (
            self._deadline is not None
            and time.monotonic() > self._deadline
            and not self._snapshot.committed
        ):
            raise DeadlineExceeded("transfer deadline exceeded")

    def _report(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if self._observer is not None and (
            force or now - self._last_report >= self._interval
        ):
            self._last_report = now
            try:
                self._observer(self._snapshot)
            except Exception:
                self._observer = None
                self._snapshot = self._snapshot.model_copy(
                    update={"observer_failed": True}
                )

    def phase(self, stage: Stage) -> None:
        """Report a phase boundary and honor cancellation before continuing."""
        self.checkpoint()
        self._snapshot = self._snapshot.model_copy(update={"stage": stage})
        self._report(force=True)
        self.checkpoint()

    def advance(
        self, *, hashed: int = 0, copied: int = 0, received: int = 0, reused: int = 0
    ) -> None:
        """Count bounded I/O work and check cancellation even when reports throttle."""
        if min(hashed, copied, received, reused) < 0:
            raise ValueError("progress increments must be nonnegative")
        current = self._snapshot
        self._snapshot = current.model_copy(
            update={
                "bytes_hashed": current.bytes_hashed + hashed,
                "bytes_copied": current.bytes_copied + copied,
                "bytes_received": current.bytes_received + received,
                "bytes_reused": current.bytes_reused + reused,
            }
        )
        self._report()
        self.checkpoint()

    def mark_committed(self) -> None:
        """Record successful publication without rechecking a late cancellation."""
        self._snapshot = self._snapshot.model_copy(update={"committed": True})

    @contextmanager
    def operation(self, kind: Operation) -> Generator[OperationControl]:
        """Own one invocation and emit exactly one final snapshot on exit."""
        if self._used:
            raise ValueError("use a fresh control for each invocation")
        self._used = True
        self._snapshot = self._snapshot.model_copy(update={"operation": kind})
        try:
            self.phase("starting")
            yield self
            self.checkpoint()
        except BaseException as error:
            self._snapshot = self._snapshot.model_copy(
                update={
                    "stage": "cancelled"
                    if isinstance(error, OperationCancelled)
                    else "failed",
                    "error_code": failure_code(error),
                }
            )
            self._report(force=True)
            raise
        else:
            self._snapshot = self._snapshot.model_copy(update={"stage": "complete"})
            self._report(force=True)
