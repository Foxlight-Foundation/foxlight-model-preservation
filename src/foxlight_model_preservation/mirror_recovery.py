"""Offline exact-selection inspection and complete retained-copy recovery kits.

The mirror's accepted signed records outlive source catalogs and application
import receipts. Metadata inspection authenticates only those bounded records;
export separately verifies all actual bytes without serving or importing trust.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter

from .kits import KitInspection, export_kit
from .mirror import selection_manifest
from .mirror_models import HeldModel
from .mirror_state import MirrorLedger
from .models import Digest, Manifest, PreservationError
from .operations import OperationControl
from .storage import manifest_path, read_metadata

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
    manifest: Manifest
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
        try:
            envelope = read_metadata(store, manifest_path(manifest_id))
        except FileNotFoundError:
            if complete or held.origin != "local":
                raise
            # A pending local intake commits its authenticated private intent
            # before objects or the complete generation exist. This is useful
            # review metadata, never proof that its payload is available.
            with ledger.child("scratch") as scratch:
                envelope = read_metadata(
                    scratch, "local-intake/" + manifest_id + ".json"
                )
        manifest = selection_manifest(envelope, manifest_id, held)
    return _SelectedCapture(held, manifest, envelope, (info.st_dev, info.st_ino))


def inspect_selection(ledger: MirrorLedger, manifest_id: str) -> Manifest:
    """Authenticate retained signed metadata without opening any weight objects.

    All original card values, unknown extensions and required companion/notice
    members remain in this bounded original manifest. The accepted model signer,
    exact identity and every member's previously reviewed public terms verify.
    No object hashing, network, signing, runtime admission or state update occurs.
    A pending local intent may supply metadata; removed selections cannot.
    """
    return _capture(ledger, manifest_id).manifest


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
