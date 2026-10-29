"""Offline exact-selection inspection and complete retained-copy recovery kits.

The mirror's accepted signed records outlive source catalogs and application
import receipts. Metadata inspection authenticates only those bounded records;
export separately verifies all actual bytes without serving or importing trust.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter

from .archives import ArchiveManifest, archive_objects
from .kits import KitInspection, export_kit
from .mirror import selection_envelope, selection_manifest
from .mirror_models import HeldModel, MirrorState
from .mirror_state import MirrorLedger
from .models import Digest, PreservationError
from .operations import OperationControl
from .storage import (
    atomic_bytes,
    manifest_path,
    read_metadata,
    verify_object,
    writer_lock,
)

_IDENTITY = TypeAdapter(Digest)


def selection_status(ledger: MirrorLedger, manifest_id: str) -> HeldModel | None:
    """Look up exact local state independently of catalog or list pagination.

    None means no selection in an available, valid ledger. Unavailable, replaced
    or corrupt owner state raises instead of masquerading as an empty selection.
    The returned accepted authority is local operator state, not recipient trust.
    """
    identity = _IDENTITY.validate_python(manifest_id)
    return ledger.read().models.get(identity)


@dataclass(frozen=True)
class _SelectedCapture:
    held: HeldModel
    manifest: ArchiveManifest
    envelope: bytes
    store_identity: tuple[int, int]


def _capture(
    ledger: MirrorLedger, manifest_id: str, *, complete: bool = False
) -> _SelectedCapture:
    held = selection_status(ledger, manifest_id)
    if held is None:
        raise PreservationError("unknown mirror selection")
    if held.state == "removed":
        raise PreservationError("selected mirror copy was removed")
    if complete and held.state != "retained":
        raise PreservationError("kit export needs a retained mirror selection")
    with ledger.child("store") as store:
        info = os.fstat(store)
        envelope = selection_envelope(
            ledger, store, manifest_id, held, pending=not complete
        )
        manifest = selection_manifest(envelope, manifest_id, held)
    return _SelectedCapture(held, manifest, envelope, (info.st_dev, info.st_ino))


def inspect_selection(ledger: MirrorLedger, manifest_id: str) -> ArchiveManifest:
    """Authenticate retained signed metadata without opening any weight objects.

    All original card values, unknown extensions and required companion/notice
    members remain in this bounded original manifest. The accepted model signer,
    exact identity and every member's previously reviewed public terms verify.
    No object hashing, network, signing, runtime admission or state update occurs.
    A pinned pending local/source intent may supply metadata; removed selections
    cannot. Historical source selections without that original record refuse.
    """
    return _capture(ledger, manifest_id).manifest


def recover_selection(
    ledger: MirrorLedger,
    manifest_id: str,
    *,
    control: OperationControl | None = None,
) -> ArchiveManifest:
    """Recover one already selected complete closure offline after interruption.

    Require a stopped controller and the selection's original independent trust,
    exact identity and every member's public terms. Verify every actual object;
    missing/corrupt bytes refuse without a fetch, retry or deletion. A local
    or source write-ahead record may supply the exact original envelope if a crash
    preceded its publication. Removed selections require separate reintake.

    Successful pending promotion clears its old failure and restart grant, while
    keeping pin, block, accepted policy and resource accounting unchanged. An
    already retained selection is reverified without changing its receipt. Run
    this byte work in an owned cancellable job when embedding it in a host.
    """
    active = control or OperationControl()
    _IDENTITY.validate_python(manifest_id)
    with (
        active.operation("mirror_recover"),
        ledger.service_lock(),
        ledger.content_lock(),
    ):
        if (ledger.path / "prune.json").exists():
            raise PreservationError("finish the interrupted operator prune first")
        selected = _capture(ledger, manifest_id)
        files = archive_objects(selected.manifest)
        if sum(file.size for file in files) > selected.held.grant.max_bytes:
            raise PreservationError("selection exceeds its accepted byte limit")
        with ledger.child("store") as store, writer_lock(store):
            info = os.fstat(store)
            if (info.st_dev, info.st_ino) != selected.store_identity:
                raise PreservationError("mirror store directory identity changed")
            active.phase("checking")
            for file in files:
                verify_object(store, file, control=active)
            active.phase("publishing")
            # Model controls can change during hashing even with the service
            # stopped. Refuse changed authority rather than undoing that choice.
            if selection_status(ledger, manifest_id) != selected.held:
                raise PreservationError("mirror selection changed during recovery")
            try:
                existing = read_metadata(store, manifest_path(manifest_id))
            except FileNotFoundError:
                atomic_bytes(store, manifest_path(manifest_id), selected.envelope)
            else:
                if existing != selected.envelope:
                    raise PreservationError("original mirror wrapper changed")

            def promote(state: MirrorState) -> MirrorState:
                current = state.models.get(manifest_id)
                if current is None or current != selected.held:
                    raise PreservationError("mirror selection changed during recovery")
                return state.model_copy(
                    update={
                        "models": dict(
                            state.models,
                            **{
                                manifest_id: current.model_copy(
                                    update={
                                        "state": "retained",
                                        "error": None,
                                        "verified_at": int(time.time()),
                                    }
                                )
                            },
                        ),
                        "restart": None,
                    }
                )

            if selected.held.state == "pending":
                active.checkpoint()
                ledger.update(promote)
            active.mark_committed()
        return selected.manifest


def export_selection_kit(
    ledger: MirrorLedger,
    manifest_id: str,
    destination: Path,
    *,
    control: OperationControl | None = None,
) -> KitInspection:
    """Copy a selected complete closure into a new verified recovery kit offline.

    Preserve the exact original wrapper, every card/companion and the accepted
    public trust snapshot as candidate recipient keys. This does not enroll that
    snapshot for a recipient, alter mirror policy or require stopping serving.
    All objects are checked while streaming and publication cannot overwrite.
    Cancellation, concurrent removal or corrupt bytes may refuse preparation;
    no partial kit is published and retained copies are never deleted here.
    """
    selected = _capture(ledger, manifest_id, complete=True)
    return export_kit(
        ledger.path / "store",
        manifest_id,
        selected.held.grant.trust,
        destination,
        expected_envelope=selected.envelope,
        expected_store_identity=selected.store_identity,
        control=control,
    )
