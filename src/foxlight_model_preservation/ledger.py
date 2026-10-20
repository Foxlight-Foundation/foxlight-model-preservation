"""Local transfer accounting, durably reserved before object reads."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, final

from pydantic import Field, TypeAdapter, model_validator

from .models import MAX_TOTAL_BYTES, Digest, Identifier, Record
from .operations import BudgetExceeded, RequestChanged
from .signing import canonical_bytes, encode_record, strict_json
from .storage import CHUNK_SIZE, atomic_bytes, directory, read_metadata


@final
class TransferReceipt(Record):
    """Conservative object-byte charges; this is not proof of job completion."""

    schema_version: Literal["foxlight.model-preservation.transfer.v1"]
    operation_id: Identifier
    request_digest: Digest
    max_bytes: int = Field(gt=0, le=MAX_TOTAL_BYTES)
    charged_bytes: int = Field(ge=0, le=MAX_TOTAL_BYTES)

    @model_validator(mode="after")
    def bounded_charge(self) -> TransferReceipt:
        """Reject impossible local accounting rather than refilling its allowance."""
        if self.charged_bytes > self.max_bytes:
            raise ValueError("transfer charge exceeds its allowance")
        return self


def receipt_path(operation_id: str) -> str:
    """Constrain a caller-selected local operation ID to one safe metadata path."""
    TypeAdapter(Identifier).validate_python(operation_id, strict=True)
    return ".operations/" + operation_id + ".json"


def read_transfer_receipt(destination: Path, operation_id: str) -> TransferReceipt:
    """Read bounded local accounting without creating files or claiming completion."""
    with directory(destination) as root:
        receipt = TransferReceipt.model_validate_json(
            canonical_bytes(
                strict_json(read_metadata(root, receipt_path(operation_id)))
            )
        )
        if receipt.operation_id != operation_id:
            raise RequestChanged("receipt does not match its selected operation")
        return receipt


@final
class TransferBudget:
    """Fence reads against an allowance, optionally retaining charges across crashes.

    Persistent instances require the destination writer lock for their entire
    lifetime. A reservation is fsynced before reading. Unused reservation credit
    is reused within this invocation but never refunded after a restart.
    """

    def __init__(
        self,
        max_bytes: int,
        *,
        root: int | None = None,
        operation_id: str | None = None,
        request_digest: str | None = None,
    ) -> None:
        if not 0 < max_bytes <= MAX_TOTAL_BYTES:
            raise ValueError("max_bytes must be positive and bounded")
        self._root = root
        self._receipt: TransferReceipt | None = None
        self._credit = 0
        self._remaining = max_bytes
        if operation_id is None:
            return
        if root is None or request_digest is None:
            raise ValueError("persistent accounting requires a pinned request and root")
        path = receipt_path(operation_id)
        expected = TransferReceipt(
            schema_version="foxlight.model-preservation.transfer.v1",
            operation_id=operation_id,
            request_digest=request_digest,
            max_bytes=max_bytes,
            charged_bytes=0,
        )
        try:
            receipt = TransferReceipt.model_validate_json(
                canonical_bytes(strict_json(read_metadata(root, path)))
            )
        except FileNotFoundError:
            atomic_bytes(root, path, encode_record(expected))
            receipt = expected
        if receipt.model_copy(update={"charged_bytes": 0}) != expected:
            raise RequestChanged("operation ID belongs to different transfer inputs")
        self._receipt = receipt
        self._remaining = receipt.max_bytes - receipt.charged_bytes

    @property
    def remaining(self) -> int:
        """Return the remaining allowance for this invocation, including its credit."""
        return self._remaining

    def reserve(self, count: int) -> None:
        """Durably cover the next bounded read before it is issued."""
        if count < 0 or count > self._remaining:
            raise BudgetExceeded("cumulative transfer budget exceeded")
        receipt = self._receipt
        if receipt is None or count <= self._credit:
            return
        amount = min(
            max(CHUNK_SIZE, count - self._credit),
            receipt.max_bytes - receipt.charged_bytes,
        )
        updated = receipt.model_copy(
            update={"charged_bytes": receipt.charged_bytes + amount}
        )
        if self._root is None:
            raise ValueError("persistent budget lost its pinned root")
        atomic_bytes(
            self._root, receipt_path(receipt.operation_id), encode_record(updated)
        )
        self._receipt = updated
        self._credit += amount

    def consume(self, count: int) -> None:
        """Account for returned bytes after a reservation has covered the read."""
        if count < 0 or count > self._remaining:
            raise BudgetExceeded("cumulative transfer budget exceeded")
        if self._receipt is not None:
            if count > self._credit:
                raise BudgetExceeded("object read exceeded its durable reservation")
            self._credit -= count
        self._remaining -= count

    def abandon(self) -> None:
        """Forfeit uncertain credit after a read error before supplier fallback."""
        if self._receipt is not None:
            self._remaining -= self._credit
            self._credit = 0
