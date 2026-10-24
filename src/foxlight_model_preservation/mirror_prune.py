"""Explicit stopped-controller deletion with shared-object and operator-pin fences."""

from __future__ import annotations

import os
from typing import Literal

from pydantic import Field

from .mirror_models import HeldModel, NativeCustodyName
from .mirror_native import prune_native
from .mirror_state import MAX_STATE, MirrorLedger, encode_state
from .models import Digest, PreservationError, Record
from .signing import encode_record, strict_json, verify_manifest
from .storage import (
    atomic_bytes,
    manifest_path,
    object_path,
    open_file,
    parent_directory,
    read_metadata,
    unique_objects,
    writer_lock,
)


class PruneIntent(Record):
    """Durable operator-selected deletion; restart never finishes it implicitly."""

    schema_version: Literal["foxlight.mirror-prune.v1"] = "foxlight.mirror-prune.v1"
    manifest: Digest
    objects: tuple[Digest, ...] = Field(max_length=100000)
    native_identity: tuple[int, int] | None = None
    native_custody: NativeCustodyName | None = None
    pending_custody: tuple[NativeCustodyName, ...] = Field(default=(), max_length=256)


def _unlink(root: int, path: str) -> None:
    try:
        with parent_directory(root, path) as (parent, name):
            try:
                os.unlink(name, dir_fd=parent)
            except FileNotFoundError:
                pass
            os.fsync(parent)
    except FileNotFoundError:
        pass


def prune(ledger: MirrorLedger, manifest: str, confirmation: str) -> dict[str, object]:
    """Remove one explicitly named unpinned model only while the controller is stopped.

    A write-ahead intent preserves the exact selected object closure across a
    crash. Other signed manifests are re-read before deleting shared bytes.
    Unknown store manifests refuse deletion; partial work is never swept.
    """
    if confirmation != manifest:
        raise PreservationError(
            "confirm the exact manifest identity for physical deletion"
        )
    with ledger.service_lock(), ledger.root() as root, writer_lock(root):
        state = ledger.read()
        held = state.models.get(manifest)
        if held is None or held.pinned or not held.blocked:
            raise PreservationError(
                "select an unpinned blocked model before physical deletion"
            )
        with ledger.child("store") as store, writer_lock(store):
            try:
                with open_file(root, "prune.json") as stream:
                    raw = stream.read(MAX_STATE + 1)
                    strict_json(raw, maximum_bytes=MAX_STATE)
                    intent = PruneIntent.model_validate_json(raw)
                if intent.manifest != manifest:
                    raise PreservationError("finish the existing operator prune first")
            except FileNotFoundError:
                original = verify_manifest(
                    read_metadata(store, manifest_path(manifest)),
                    held.grant.trust,
                    manifest,
                )
                intent = PruneIntent(
                    manifest=manifest,
                    objects=tuple(f.sha256 for f in unique_objects(original)),
                    native_identity=state.native_models[manifest].identity
                    if manifest in state.native_models
                    else None,
                    native_custody=state.native_models[manifest].custody_name
                    if manifest in state.native_models
                    else None,
                    pending_custody=state.native_models[manifest].pending_custody
                    if manifest in state.native_models
                    else (),
                )
                atomic_bytes(root, "prune.json", encode_record(intent))
            references: set[str] = set()
            try:
                with parent_directory(store, "manifests/placeholder") as (
                    parent,
                    _name,
                ):
                    names = os.listdir(parent)
            except FileNotFoundError:
                names = []
            for name in names:
                if name == manifest + ".json":
                    continue
                identity = name.removesuffix(".json")
                other = state.models.get(identity)
                if other is None or not name.endswith(".json"):
                    raise PreservationError(
                        "unknown retained manifest; refusing shared-object deletion"
                    )
                value = verify_manifest(
                    read_metadata(store, manifest_path(identity)),
                    other.grant.trust,
                    identity,
                )
                references.update(file.sha256 for file in unique_objects(value))
            # Retire public routes before touching retained files. No readers can
            # exist: the service lock is held until every HTTP worker has drained.
            with ledger.child("public") as public:
                _unlink(public, manifest_path(manifest))
                for identity in intent.objects:
                    if identity not in references:
                        _unlink(public, object_path(identity))
            if intent.native_identity is not None or intent.pending_custody:
                prune_native(
                    ledger,
                    manifest,
                    intent.native_identity,
                    custody_name=intent.native_custody,
                    pending_custody=intent.pending_custody,
                )
            _unlink(store, manifest_path(manifest))
            removed = 0
            for identity in intent.objects:
                if identity not in references:
                    _unlink(store, object_path(identity))
                    removed += 1

            models = dict(ledger.read().models)
            current = models[manifest]
            models[manifest] = HeldModel.model_validate(
                current.model_copy(
                    update={"state": "removed", "error": None, "verified_at": None}
                ).model_dump()
            )
            native_models = dict(state.native_models)
            native_models.pop(manifest, None)
            final = state.model_copy(
                update={"models": models, "native_models": native_models}
            )
            atomic_bytes(root, "mirror.json", encode_state(final))
            _unlink(root, "prune.json")
    return {
        "manifest": manifest,
        "removed": True,
        "unshared_objects_selected": removed,
        "retained_other_models": True,
    }
