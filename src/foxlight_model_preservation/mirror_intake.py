"""Offline exact-copy admission into an independently operated public mirror."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from .archives import ArchiveManifest, archive_objects, verify_archive_manifest
from .mirror import record_model, require_terms, verify_retained
from .mirror_models import HeldModel, MirrorState, SourceGrant
from .mirror_state import HEADROOM, MirrorLedger
from .models import PreservationError
from .operations import OperationControl, failure_code
from .signing import digest
from .storage import (
    atomic_bytes,
    copy_verified_object,
    directory,
    manifest_path,
    object_path,
    observe_objects,
    read_metadata,
    writer_lock,
)


def retain_local(
    ledger: MirrorLedger,
    store: Path,
    manifest_id: str,
    grant: SourceGrant,
    *,
    control: OperationControl | None = None,
    confirm_reintake: bool = False,
    link_objects: bool = False,
) -> ArchiveManifest:
    """Copy one exact public selection offline without following a source or serving.

    The caller supplies independently accepted model keys, the exact manifest ID
    and reviewed terms for every member. Existing selection authority cannot be
    replaced. Re-admitting a removed selection needs separate confirmation and
    preserves its block/accounting. A new write-ahead selection invalidates
    restart consent; failed copies retain bytes and need explicit retry.
    Original envelope bytes are committed only after every object is copied and
    verified. No signing, network, runtime admission or deletion occurs.

    Public preservation-v1 and draft PMF archives are both retained natively:
    the signed payload type selects verification, and PMF source documents are
    copied and verified as objects. Private intake on any member still refuses.
    ``link_objects`` hardlinks the store's immutable objects on the same
    filesystem, so a mirror of a local archive needs no second copy of its bytes.
    """
    active = control or OperationControl()
    with active.operation("mirror_retain"), ledger.service_lock():
        if (ledger.path / "prune.json").exists():
            raise PreservationError("finish the interrupted operator prune first")
        with directory(store) as source:
            envelope = read_metadata(source, manifest_path(manifest_id))
            manifest = verify_archive_manifest(envelope, grant.trust, manifest_id)
            require_terms(manifest, grant)
            files = archive_objects(manifest)
            if sum(file.size for file in files) > grant.max_bytes:
                raise PreservationError(
                    "local selection exceeds the reviewed byte limit"
                )
            held = HeldModel(
                origin="local",
                envelope_sha256=digest(envelope),
                grant=grant,
                operation_id="l"
                + digest((ledger.read().salt + manifest_id).encode())[:63],
            )
            state = ledger.read()
            previous = state.models.get(manifest_id)
            if previous is not None:
                if (
                    previous.origin != "local"
                    or previous.grant != grant
                    or previous.envelope_sha256 != held.envelope_sha256
                ):
                    raise PreservationError(
                        "existing selection keeps its original authority"
                    )
                if previous.state == "removed" and not confirm_reintake:
                    raise PreservationError(
                        "removed local selection needs explicit reintake confirmation"
                    )
                if previous.error:
                    raise PreservationError(
                        "explicit mirror retry is required before local intake"
                    )
                held = (
                    previous.model_copy(
                        update={"state": "pending", "verified_at": None}
                    )
                    if previous.state == "removed"
                    else previous
                )
            elif len(state.models) >= 1000:
                raise PreservationError("mirror selection ledger at capacity")
            if confirm_reintake and (previous is None or previous.state != "removed"):
                raise PreservationError(
                    "reintake confirmation needs a removed local selection"
                )

            with ledger.child("store") as target, writer_lock(target):
                try:
                    existing_envelope = read_metadata(
                        target, manifest_path(manifest_id)
                    )
                except FileNotFoundError:
                    pass
                else:
                    if existing_envelope != envelope and previous is None:
                        raise PreservationError(
                            "retained original signed wrapper differs"
                        )
                    # An existing selection already pinned and reauthenticated
                    # this exact input wrapper above. An explicit retry may
                    # restore those original bytes after destination damage.
                missing, _reused, _partial = observe_objects(target, files, active)
                additional = sum(file.size for file in missing) + 2 * len(envelope)
                # Linked objects on the same filesystem consume no new space;
                # the mirror's own logical budget still counts their bytes.
                physical = (
                    2 * len(envelope)
                    if link_objects
                    and os.fstat(source).st_dev == os.fstat(target).st_dev
                    else additional
                )
                if (
                    ledger.usage() + additional + HEADROOM > state.disk_bytes
                    or shutil.disk_usage(target).free < physical + HEADROOM
                ):
                    raise PreservationError("mirror disk admission budget exhausted")
                active.checkpoint()
                try:
                    # Persist signed metadata before the selection and any object
                    # copy. A crash can leave unselected private metadata, never
                    # an admitted partial without its authenticated file table.
                    with ledger.child("scratch") as scratch:
                        atomic_bytes(
                            scratch, "local-intake/" + manifest_id + ".json", envelope
                        )
                    if previous is None or previous.state == "removed":

                        def select(current: MirrorState) -> MirrorState:
                            return current.model_copy(
                                update={
                                    "schema_version": current.schema_version
                                    if current.schema_version
                                    in {
                                        "foxlight.mirror-state.v4",
                                        "foxlight.mirror-state.v5",
                                    }
                                    else "foxlight.mirror-state.v3",
                                    "models": dict(
                                        current.models, **{manifest_id: held}
                                    ),
                                    "restart": None,
                                }
                            )

                        ledger.update(select)
                    for file in files:
                        copy_verified_object(
                            source,
                            object_path(file.sha256),
                            target,
                            file,
                            active,
                            verify_reused_source=True,
                            link=link_objects,
                        )
                    active.phase("publishing")
                    active.checkpoint()
                    # A local selection retains this original wrapper exactly;
                    # it never re-signs, normalizes or drops extra signatures.
                    atomic_bytes(target, manifest_path(manifest_id), envelope)
                    verify_retained(target, manifest_id, held, active)
                    record_model(
                        ledger,
                        manifest_id,
                        {
                            "state": "retained",
                            "verified_at": int(time.time()),
                            "error": None,
                        },
                    )
                    active.mark_committed()
                except (ValueError, OSError) as error:
                    selected = ledger.read().models.get(manifest_id)
                    if selected is not None and selected.state != "removed":
                        record_model(
                            ledger, manifest_id, {"error": failure_code(error)}
                        )
                    raise
    return manifest
