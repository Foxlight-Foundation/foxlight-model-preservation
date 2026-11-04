"""Standalone persistent HTTP mirror; retained bytes have no remote deletion path."""

from __future__ import annotations

import http.server
import os
import re
import shutil
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from .archives import ArchiveManifest, archive_objects, verify_archive_manifest
from .discovery import follow_source, list_sources, refresh_source, search_sources
from .discovery_models import MAX_SOURCES
from .mirror_announce import Announcer, remove_peers
from .mirror_cards import PEERS_PATH
from .mirror_models import (
    HeldModel,
    MirrorObservation,
    MirrorState,
    SourceGrant,
    SourceIntake,
)
from .mirror_native import NativeMirrors, PeerSessionRunner
from .mirror_state import (
    HEADROOM,
    MirrorLedger,
    policy_id,
    restart_allowed,
    serving_hour,
)
from .models import PreservationError
from .operations import OperationCancelled, OperationControl, failure_code
from .seed import SeedHandler, SeedServer
from .signing import (
    canonical_bytes,
    digest,
    encode_record,
    strict_json,
)
from .storage import (
    atomic_bytes,
    manifest_path,
    object_path,
    observe_objects,
    open_file,
    parent_directory,
    read_metadata,
    verify_object,
    writer_lock,
)
from .transfer import TransferPolicy, fetch, inspect_envelope

# Catalog states whose authenticated, unexpired listing is a current statement.
# An expired ("stale") catalog says nothing about withdrawal: that is availability.
CURRENT_CATALOGS = frozenset({"fresh", "retained", "imported"})


class MirrorBudgetDeferred(PreservationError):
    """A local ingress or disk budget refused intake for now; retried later.

    Deferral is not a model failure. Reservations already made stay charged
    (never refunded), and a later cycle retries once the budget can admit it.
    """


def derived_model_limit(disk_bytes: int) -> int:
    """Default per-model byte limit: whatever the disk budget could ever admit."""
    return max(1, disk_bytes - HEADROOM)


def follow(
    ledger: MirrorLedger,
    document: bytes,
    expected_bootstrap_id: str,
    grant: SourceGrant,
) -> str:
    """Follow an authenticated source offline with separately supplied model policy."""
    with ledger.service_lock():
        if ledger.usage() + HEADROOM > ledger.read().disk_bytes:
            raise PreservationError("mirror disk admission budget exhausted")
        status = follow_source(
            ledger.path / "discovery",
            document,
            expected_bootstrap_id,
            allow_http=grant.allow_http,
            allow_private=grant.allow_private,
            allow_local=grant.allow_local,
            expected_directory=ledger.read().identity["discovery"],
        )
        ledger.update(
            lambda state: state.model_copy(
                update={
                    "sources": dict(state.sources, **{status.source_id: grant}),
                    "schema_version": "foxlight.mirror-state.v5",
                    "restart": None,
                }
            )
        )
        return status.source_id


def plan(ledger: MirrorLedger) -> dict[str, JsonValue]:
    """Explain bounded locally cached candidates and retained policy without network."""
    state = ledger.read()
    candidates: list[JsonValue] = []
    for result in search_sources(ledger.path / "discovery", "", limit=1000):
        for location in result.locations:
            if location.source_id in state.sources:
                candidates.append(
                    {
                        "manifest": result.manifest,
                        "source_id": location.source_id,
                        "entry": location.entry_id,
                        "freshness": location.freshness,
                        "candidate_keys_are_not_accepted": True,
                    }
                )
    return {
        "policy_id": policy_id(state),
        "disk_bytes_used": ledger.usage(),
        "state": state.model_dump(mode="json"),
        "candidates": candidates,
        "candidate_limit": 1000,
        "controller_active": ledger.running(),
        "network_started": False,
    }


def model_action(ledger: MirrorLedger, manifest_id: str, action: str) -> MirrorState:
    """Change operator pin/serving/retry policy without deleting any retained bytes."""
    changes: dict[str, object] = {
        "pin": {"pinned": True},
        "unpin": {"pinned": False},
        "block": {"blocked": True},
        "unblock": {"blocked": False},
        "retry": {"error": None, "deferred": None, "state": "pending"},
        # Resume serving a withdrawn selection. A current catalog that still
        # omits it withdraws it again on the next cycle.
        "unwithdraw": {"withdrawn": False},
    }
    if action not in changes:
        raise PreservationError("unsupported mirror model action")
    update = cast(dict[str, object], changes[action])
    return record_model(ledger, manifest_id, update)


def configure_http(ledger: MirrorLedger, *, enabled: bool) -> MirrorState:
    """Choose HTTP serving offline, retaining copies and clearing restart consent.

    This neither binds a listener nor changes selected native transports. An
    active controller must be stopped first. The explicit v4 owner profile
    prevents an older mirror from silently ignoring a disabled HTTP transport.
    """
    with ledger.service_lock():
        return ledger.update(
            lambda state: state.model_copy(
                update={
                    "schema_version": "foxlight.mirror-state.v5"
                    if state.schema_version == "foxlight.mirror-state.v5"
                    else "foxlight.mirror-state.v4",
                    "http_enabled": enabled,
                    "restart": None,
                }
            )
        )


CONFIGURABLE = frozenset(
    {
        "name",
        "disk_bytes",
        "ingress_daily",
        "egress_daily",
        "egress_monthly",
        "bind",
        "port",
        "serve_start",
        "serve_end",
        "refresh_seconds",
        "announce",
    }
)


def configure_mirror(ledger: MirrorLedger, changes: dict[str, object]) -> MirrorState:
    """Change bind, port, budgets, name or announcement offline.

    Any effective change alters the mirror's policy identity, so it also clears
    the standing restart grant: unattended serving needs fresh consent for the
    new policy. Retained bytes are never touched. An active controller must be
    stopped first. Source limits that were derived from the old disk budget
    (``disk_bytes`` minus headroom) follow the new one; explicit limits stay.
    Clearing the announcement removes this mirror's published peers document.
    """
    if set(changes) - CONFIGURABLE:
        raise PreservationError("unsupported mirror configuration change")
    with ledger.service_lock():

        def apply(state: MirrorState) -> MirrorState:
            update: dict[str, object] = dict(changes)
            disk = changes.get("disk_bytes")
            if isinstance(disk, int) and disk != state.disk_bytes:
                old_limit = derived_model_limit(state.disk_bytes)
                update["sources"] = {
                    source: grant.model_copy(
                        update={"max_bytes": derived_model_limit(disk)}
                    )
                    if grant.max_bytes == old_limit
                    else grant
                    for source, grant in state.sources.items()
                }
            candidate = MirrorState.model_validate(
                state.model_copy(update=update).model_dump()
            )
            if policy_id(candidate) == policy_id(state):
                return state
            return candidate.model_copy(update={"restart": None})

        state = ledger.update(apply)
        if "announce" in changes and state.announce is None:
            remove_peers(ledger)
        return state


def retry_selection(
    ledger: MirrorLedger, manifest_id: str, *, adopt_source_grant: bool = False
) -> MirrorState:
    """Clear a failure for retry, optionally adopting the source's current grant.

    Re-following a source records new model keys/terms for future intake, but an
    existing selection keeps its original authority. ``adopt_source_grant``
    replaces it with the source's current grant after an operator re-follow. A
    pinned original wrapper must still authenticate under the adopted keys and
    terms; its private authority receipt is rewritten after the ledger commit
    and rewritten again by repeating this command after an interruption. A
    changed byte limit starts a new, separately charged ingress operation.
    """
    if not adopt_source_grant:
        return model_action(ledger, manifest_id, "retry")
    with ledger.service_lock():
        state = ledger.read()
        held = state.models.get(manifest_id)
        if held is None:
            raise PreservationError("unknown retained mirror selection")
        if held.origin != "source" or held.source_id is None:
            raise PreservationError("only source selections adopt a source grant")
        grant = state.sources.get(held.source_id)
        if grant is None:
            raise PreservationError("the selection's source is no longer followed")
        update: dict[str, object] = {
            "grant": grant,
            "error": None,
            "deferred": None,
            # A complete copy is re-verified under the adopted grant by every
            # public selection, so it need not wait for a fresh catalog.
            "state": "retained" if held.state == "retained" else "pending",
        }
        if grant.max_bytes != held.grant.max_bytes and held.transfer_bytes is not None:
            update["transfer_bytes"] = None
            update["operation_id"] = (
                "m"
                + digest(
                    (
                        state.salt
                        + manifest_id
                        + digest(canonical_bytes(grant.model_dump(mode="json")))
                    ).encode()
                )[:63]
            )
        adopted = HeldModel.model_validate(held.model_copy(update=update).model_dump())
        if held.envelope_sha256 is not None:
            selection_manifest(
                _pinned_envelope(ledger, manifest_id, held), manifest_id, adopted
            )

        def adopt(current: MirrorState) -> MirrorState:
            if current.models.get(manifest_id) != held:
                raise PreservationError("mirror selection changed during adoption")
            native = dict(current.native_models)
            if manifest_id in native:
                native[manifest_id] = native[manifest_id].model_copy(
                    update={"error": None}
                )
            return current.model_copy(
                update={
                    "models": dict(current.models, **{manifest_id: adopted}),
                    "native_models": native,
                }
            )

        state = ledger.update(adopt)
        if adopted.envelope_sha256 is not None:
            with ledger.child("scratch") as scratch:
                atomic_bytes(
                    scratch,
                    "source-intake/" + manifest_id + ".authority.json",
                    encode_record(_source_intake(manifest_id, adopted)),
                )
        return state


def _pinned_envelope(ledger: MirrorLedger, identity: str, held: HeldModel) -> bytes:
    """Read the exact pinned original wrapper, checked only by its digest."""
    try:
        with ledger.child("scratch") as scratch:
            envelope = read_metadata(scratch, "source-intake/" + identity + ".json")
    except FileNotFoundError:
        with ledger.child("store") as store:
            envelope = read_metadata(store, manifest_path(identity))
    if digest(envelope) != held.envelope_sha256:
        raise PreservationError("selection's original signed wrapper changed")
    return envelope


def record_model(
    ledger: MirrorLedger, manifest_id: str, changes: dict[str, object]
) -> MirrorState:
    """Update an existing selection receipt without changing accepted authority."""
    if set(changes) - {
        "pinned",
        "blocked",
        "state",
        "error",
        "verified_at",
        "transfer_bytes",
        "deferred",
        "withdrawn",
    }:
        raise PreservationError("selection authority cannot be changed by a receipt")

    def apply(state: MirrorState) -> MirrorState:
        if manifest_id not in state.models:
            raise PreservationError("unknown retained mirror selection")
        models = dict(state.models)
        selected_changes = dict(changes)
        if (
            changes.get("state") == "pending"
            and state.models[manifest_id].origin == "local"
            and state.models[manifest_id].state == "removed"
        ):
            raise PreservationError(
                "removed local selection needs mirror retain --confirm-reintake"
            )
        if (
            changes.get("state") == "pending"
            and (record := state.native_models.get(manifest_id)) is not None
            and record.error
            and state.models[manifest_id].state == "retained"
            and not state.models[manifest_id].error
        ):
            # Retrying native admission need not wait for a new catalog refresh
            # or make a complete healthy HTTP copy disappear.
            selected_changes["state"] = "retained"
        models[manifest_id] = HeldModel.model_validate(
            state.models[manifest_id].model_copy(update=selected_changes).model_dump()
        )
        native = dict(state.native_models)
        if changes.get("state") == "pending" and manifest_id in native:
            native[manifest_id] = native[manifest_id].model_copy(update={"error": None})
        return state.model_copy(update={"models": models, "native_models": native})

    return ledger.update(apply)


def require_terms(manifest: ArchiveManifest, grant: SourceGrant) -> None:
    """Require every member to match independently reviewed public intake terms."""
    accepted = {(item.license_id, item.terms_reference) for item in grant.terms}
    for member in manifest.members:
        if (
            member.intake.audience != "public"
            or (member.intake.license_id, member.intake.terms_reference) not in accepted
        ):
            raise PreservationError(
                "every member needs public intake and exact reviewed terms"
            )


def _policy(grant: SourceGrant, maximum: int | None = None) -> TransferPolicy:
    return TransferPolicy(
        max_bytes=maximum if maximum is not None else grant.max_bytes,
        allow_private=grant.allow_private,
        allow_http=grant.allow_http,
        deadline_seconds=120,
    )


def selection_manifest(
    envelope: bytes, identity: str, held: HeldModel
) -> ArchiveManifest:
    """Authenticate a selected wrapper and terms without claiming complete bytes."""
    if held.envelope_sha256 is not None and digest(envelope) != held.envelope_sha256:
        raise PreservationError("selection's original signed wrapper changed")
    manifest = verify_archive_manifest(envelope, held.grant.trust, identity)
    require_terms(manifest, held.grant)
    return manifest


def selection_envelope(
    ledger: MirrorLedger,
    store: int,
    identity: str,
    held: HeldModel,
    *,
    prefer_private: bool = False,
    pending: bool = True,
) -> bytes:
    """Read original bounded custody metadata, never infer it from bare objects.

    Pinned local/source intake metadata can outlive unfinished publication. Prune
    prefers that private original to protect exact shared-object references even
    if a complete wrapper is damaged. Complete-kit callers require the store.
    Every returned wrapper still authenticates against the selection's own grant.
    """
    if held.origin == "source" and held.envelope_sha256 is not None:
        with ledger.child("scratch") as scratch:
            raw = read_metadata(
                scratch, "source-intake/" + identity + ".authority.json"
            )
        strict_json(raw)
        if SourceIntake.model_validate_json(raw) != _source_intake(identity, held):
            raise PreservationError("original source intake authority changed")
    private = held.envelope_sha256 is not None and pending
    path = held.origin + "-intake/" + identity + ".json"
    if private and prefer_private:
        with ledger.child("scratch") as scratch:
            envelope = read_metadata(scratch, path)
    else:
        try:
            envelope = read_metadata(store, manifest_path(identity))
        except FileNotFoundError:
            if not private:
                raise
            with ledger.child("scratch") as scratch:
                envelope = read_metadata(scratch, path)
    selection_manifest(envelope, identity, held)
    return envelope


def _source_intake(identity: str, held: HeldModel) -> SourceIntake:
    return SourceIntake(
        manifest=identity,
        authority_sha256=digest(
            canonical_bytes(
                held.model_dump(
                    mode="json",
                    exclude={
                        "pinned",
                        "blocked",
                        "state",
                        "error",
                        "verified_at",
                        "deferred",
                        "withdrawn",
                    },
                )
            )
        ),
    )


def _retain_source_envelope(
    ledger: MirrorLedger, identity: str, held: HeldModel, envelope: bytes
) -> HeldModel:
    # The caller has authenticated the full original closure and admitted space
    # for both metadata copies before this write. Commit private metadata before
    # its owner-state pin; neither may publish a partial generation or serve it.
    selected = held.model_copy(update={"envelope_sha256": digest(envelope)})
    selection_manifest(envelope, identity, selected)
    with ledger.child("scratch") as scratch:
        atomic_bytes(scratch, "source-intake/" + identity + ".json", envelope)
        atomic_bytes(
            scratch,
            "source-intake/" + identity + ".authority.json",
            encode_record(_source_intake(identity, selected)),
        )

    def pin(state: MirrorState) -> MirrorState:
        if state.models.get(identity) != held:
            raise PreservationError("mirror selection changed before source intake")
        return state.model_copy(
            update={
                "schema_version": "foxlight.mirror-state.v5",
                "models": dict(state.models, **{identity: selected}),
                "restart": state.restart
                if state.schema_version == "foxlight.mirror-state.v5"
                else None,
            }
        )

    ledger.update(pin)
    return selected


def verify_retained(
    root: int,
    identity: str,
    held: HeldModel,
    control: OperationControl,
    *,
    ledger: MirrorLedger | None = None,
) -> ArchiveManifest:
    """Verify original model trust, public terms and every retained object."""
    if held.origin == "source" and held.envelope_sha256 is not None:
        if ledger is None:
            raise PreservationError("pinned source custody needs its owner ledger")
        envelope = selection_envelope(ledger, root, identity, held, pending=False)
    else:
        envelope = read_metadata(root, manifest_path(identity))
    manifest = selection_manifest(envelope, identity, held)
    for file in archive_objects(manifest):
        verify_object(root, file, control=control)
    return manifest


def recover_completed(ledger: MirrorLedger, control: OperationControl) -> None:
    """Promote an already selected complete local closure after an interrupted commit.

    This neither discovers a new model nor relaxes its original independent
    trust/intake/terms policy. Partial or corrupt bytes remain retained, unserved.
    """
    for identity, held in ledger.read().models.items():
        if held.state != "pending" or held.blocked or held.error:
            continue
        try:
            with ledger.child("store") as store:
                verify_retained(store, identity, held, control.fork(), ledger=ledger)
        except OperationCancelled:
            raise
        except (ValueError, OSError):
            continue
        record_model(
            ledger,
            identity,
            {"state": "retained", "verified_at": int(time.time()), "deferred": None},
        )


def _budget_may_admit(
    ledger: MirrorLedger, held: HeldModel, usage: Callable[[], int]
) -> bool:
    """Offline estimate whether a deferred selection could pass its budgets now.

    This only gates an automatic retry, never admission itself: every budget is
    still reserved durably before contact and checked again before bytes move.
    """
    state = ledger.read()
    today = time.strftime("%Y-%m-%d", time.gmtime())
    used = state.ingress_used if state.day == today else 0
    needed = 0 if held.envelope_sha256 is not None else HEADROOM + 4096
    if held.transfer_bytes is not None and (
        held.operation_id not in state.reserved_operations
    ):
        needed += held.transfer_bytes
    if used + needed > state.ingress_daily:
        return False
    return usage() + HEADROOM < state.disk_bytes


def withdrawn_selections(ledger: MirrorLedger) -> dict[str, bool]:
    """Decide, offline, which source selections current catalogs withdrew.

    Only current evidence decides. A selection is withdrawn when its own source
    has an authenticated, unexpired catalog that no longer lists it and no other
    followed source's current catalog lists it. It is listed (not withdrawn)
    when any current followed catalog lists it. Otherwise there is no decision
    and the caller keeps the existing receipt: a catalog that merely expires (or
    cannot refresh) is availability, neither withdrawal nor relisting, so it
    never withdraws a served copy and never resumes a withdrawn one. Local
    selections are never withdrawn.
    """
    state = ledger.read()
    cache = ledger.path / "discovery"
    current = {
        status.source_id
        for status in list_sources(cache)
        if status.source_id in state.sources
        and status.enabled
        and status.freshness in CURRENT_CATALOGS
    }
    listed = {
        result.manifest
        for result in search_sources(cache, "", limit=MAX_SOURCES * 1000)
        for location in result.locations
        if location.source_id in current
    }
    decisions: dict[str, bool] = {}
    for identity, held in state.models.items():
        if held.origin != "source" or held.state == "removed":
            continue
        if identity in listed:
            decisions[identity] = False
        elif held.source_id in current:
            decisions[identity] = True
    return decisions


def mark_withdrawn(ledger: MirrorLedger) -> MirrorState:
    """Record catalog withdrawal receipts; bytes stay retained per retention rules.

    A withdrawn selection stops being served on the next public selection and
    stays withdrawn, even if the withdrawing catalog later expires or vanishes,
    until a current followed catalog lists it again or the operator runs
    ``mirror unwithdraw``. This is the takedown path: a source withdraws an
    entry from its signed catalog.
    """
    decisions = withdrawn_selections(ledger)

    def apply(state: MirrorState) -> MirrorState:
        models = dict(state.models)
        for identity, withdrawn in decisions.items():
            held = models.get(identity)
            if held is not None and held.withdrawn != withdrawn:
                models[identity] = HeldModel.model_validate(
                    held.model_copy(update={"withdrawn": withdrawn}).model_dump()
                )
        return state.model_copy(update={"models": models})

    if all(
        (held := ledger.read().models.get(identity)) is None
        or held.withdrawn == withdrawn
        for identity, withdrawn in decisions.items()
    ):
        return ledger.read()
    return ledger.update(apply)


def reconcile(ledger: MirrorLedger, control: OperationControl) -> None:
    """Refresh followed sources, admit trusted public closures and retain old copies.

    Caller owns the service lock. Each attempted selection retains one immutable
    operation and conservative ingress reservation. Failures require explicit
    retry. Ingress/disk budget refusals are deferred instead and retried in a
    later cycle; reservations already made stay charged. Source changes never
    delete bytes, pins or accepted model evidence; a selection withdrawn from
    every current followed catalog is recorded and stops being served.
    """
    if (ledger.path / "prune.json").exists():
        raise PreservationError("finish the interrupted operator prune first")
    recover_completed(ledger, control)
    state = ledger.read()
    refresh_error = None
    try:
        for source in state.sources:
            control.checkpoint()
            if ledger.usage() + HEADROOM > state.disk_bytes:
                raise PreservationError("mirror disk admission budget exhausted")
            if not ledger.reserve(32 * 1024**2, ingress=True):
                raise PreservationError("mirror ingress budget unavailable")
            try:
                refresh_source(
                    ledger.path / "discovery",
                    source,
                    control=control.fork(),
                    stage_parent=ledger.path / "scratch",
                    expected_directory=ledger.read().identity["discovery"],
                )
            except OperationCancelled:
                raise
            except (ValueError, OSError) as failure:
                refresh_error = failure_code(failure) + ": " + str(failure)[:400]
    finally:
        # Record every authenticated withdrawal already accepted, even when a
        # later source's budget or cancellation stops this cycle: one source's
        # failure must not delay another source's takedown.
        mark_withdrawn(ledger)
    state = ledger.read()
    catalogs = {
        source.source_id: source.catalog_id
        for source in list_sources(ledger.path / "discovery")
        if source.freshness == "fresh" and source.catalog_id is not None
    }
    results = sorted(
        search_sources(ledger.path / "discovery", "", limit=1000),
        key=lambda item: digest((state.salt + item.manifest).encode()),
    )
    for result in results:
        control.checkpoint()
        state = ledger.read()
        if result.manifest in state.models:
            continue
        if len(state.models) >= 1000:
            refresh_error = "selection ledger at capacity; new intake paused"
            break
        location = next(
            (
                x
                for x in result.locations
                if x.source_id in state.sources and x.freshness == "fresh"
            ),
            None,
        )
        if location is None:
            continue
        catalog_id = catalogs.get(location.source_id)
        if catalog_id is None:
            continue
        grant = state.sources[location.source_id]
        mirrors = tuple(
            x
            for x in location.mirrors
            if "://" in x or (grant.allow_local and Path(x).is_absolute())
        )
        if not mirrors:
            continue
        held = HeldModel(
            source_id=location.source_id,
            catalog_id=catalog_id,
            entry_id=location.entry_id,
            grant=grant,
            mirrors=mirrors,
            operation_id="m" + digest((state.salt + result.manifest).encode())[:63],
        )
        ledger.update(
            lambda current, identity=result.manifest, selected=held: current.model_copy(
                update={
                    "schema_version": "foxlight.mirror-state.v5",
                    "models": dict(current.models, **{identity: selected}),
                    "restart": current.restart
                    if current.schema_version == "foxlight.mirror-state.v5"
                    else None,
                }
            )
        )
    fresh = {
        (r.manifest, x.source_id)
        for r in results
        for x in r.locations
        if x.freshness == "fresh"
    }
    measured: list[int] = []

    def measured_usage() -> int:
        # One inventory walk per cycle gates every deferred retry; admission
        # itself always measures again before any bytes move.
        if not measured:
            measured.append(ledger.usage())
        return measured[0]

    for identity, held in ledger.read().models.items():
        control.checkpoint()
        if held.blocked or held.state == "removed" or held.error:
            continue
        try:
            if held.state == "pending":
                if held.origin == "local":
                    # A local copy is retried only by the operator supplying it
                    # again. A catalog cannot acquire its trust or repair route.
                    continue
                if (identity, held.source_id) not in fresh:
                    continue
                if held.deferred is not None and not _budget_may_admit(
                    ledger, held, measured_usage
                ):
                    # Deferred intake waits offline: no contact, no new charge.
                    continue
                # Reserve bounded metadata before first contact. The authenticated
                # original is reused by fetch, not downloaded a second time.
                # Object credit is proportional to the authenticated closure.
                # A deferred retry may need to authenticate the wrapper again, so
                # it is charged afresh rather than reusing the first reservation.
                if not ledger.reserve(
                    HEADROOM + 4096,
                    ingress=True,
                    operation=None
                    if held.deferred is not None and held.envelope_sha256 is None
                    else "meta-" + digest(held.operation_id.encode())[:59],
                ):
                    raise MirrorBudgetDeferred("mirror ingress budget unavailable")
                needs_record = held.envelope_sha256 is None
                if needs_record:
                    envelope = inspect_envelope(
                        identity,
                        held.grant.trust,
                        held.mirrors,
                        _policy(held.grant),
                        control=control.fork(),
                    )
                else:
                    # Retry retains the original wrapper, even when a supplier
                    # serves another independently valid envelope for this ID.
                    try:
                        with ledger.child("store") as store:
                            envelope = selection_envelope(ledger, store, identity, held)
                    except FileNotFoundError:
                        # Explicit retry after physical deletion may reacquire
                        # metadata, but only its originally pinned bytes and
                        # unchanged independent authority. Never clear that pin.
                        envelope = inspect_envelope(
                            identity,
                            held.grant.trust,
                            held.mirrors,
                            _policy(held.grant),
                            control=control.fork(),
                        )
                        needs_record = True
                manifest = selection_manifest(envelope, identity, held)
                allowance = held.transfer_bytes
                if allowance is None:
                    allowance = min(
                        held.grant.max_bytes,
                        3 * sum(f.size for f in archive_objects(manifest))
                        + 16 * 1024**2,
                    )
                    held = record_model(
                        ledger, identity, {"transfer_bytes": allowance}
                    ).models[identity]
                if not ledger.reserve(
                    allowance, ingress=True, operation=held.operation_id
                ):
                    raise MirrorBudgetDeferred("mirror ingress budget unavailable")
                with ledger.child("store") as store:
                    missing, _reused, partial = observe_objects(
                        store,
                        archive_objects(manifest),
                        control.fork(),
                        include_partials=True,
                    )
                additional = (
                    sum(f.size for f in missing)
                    - partial
                    + 2 * len(envelope)
                    + 1024  # Bounded source-intake authority receipt.
                )
                usage = ledger.usage()
                if usage + additional + HEADROOM > ledger.read().disk_bytes or (
                    shutil.disk_usage(ledger.path).free < additional + HEADROOM
                ):
                    raise MirrorBudgetDeferred("mirror disk admission budget exhausted")
                if ledger.read().models.get(identity) != held:
                    raise PreservationError(
                        "mirror selection changed before source intake"
                    )
                control.checkpoint()
                if needs_record:
                    held = _retain_source_envelope(ledger, identity, held, envelope)
                fetch(
                    identity,
                    held.grant.trust,
                    held.mirrors,
                    ledger.path / "store",
                    _policy(held.grant, allowance),
                    control=control.fork(),
                    operation_id=held.operation_id,
                    expected_directory=ledger.read().identity["store"],
                    envelope=envelope,
                )
            with ledger.child("store") as store:
                verify_retained(store, identity, held, control.fork(), ledger=ledger)
            record_model(
                ledger,
                identity,
                {
                    "state": "retained",
                    "verified_at": int(time.time()),
                    "error": None,
                    "deferred": None,
                },
            )
        except OperationCancelled:
            raise
        except MirrorBudgetDeferred as deferral:
            # Budget refusals are not sticky: the next cycle retries this model.
            record_model(ledger, identity, {"deferred": str(deferral)[:400]})
        except (ValueError, OSError) as error:
            record_model(
                ledger,
                identity,
                {
                    "error": failure_code(error) + ": " + str(error)[:400],
                    "deferred": None,
                },
            )
    ledger.update(
        lambda current: current.model_copy(
            update={"last_cycle": int(time.time()), "last_error": refresh_error}
        )
    )


def _link(source: int, target: int, path: str) -> None:
    with (
        open_file(source, path) as stream,
        parent_directory(target, path, create=True) as (parent, name),
    ):
        info = os.fstat(stream.fileno())
        existing = None
        try:
            existing = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        if existing and (existing.st_dev, existing.st_ino) == (
            info.st_dev,
            info.st_ino,
        ):
            return
        temporary = ".link-" + os.urandom(16).hex()
        with parent_directory(source, path) as (original, original_name):
            os.link(
                original_name,
                temporary,
                src_dir_fd=original,
                dst_dir_fd=parent,
                follow_symlinks=False,
            )
        try:
            os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass


def public_selection(
    ledger: MirrorLedger, control: OperationControl
) -> tuple[dict[str, int], dict[str, set[str]]]:
    """Verify every admitted public closure and publish only allowlisted paths.

    Public hardlinks are an optimization, not an integrity boundary. Each model
    envelope appears last. Retiring public links never removes retained objects.
    """
    allowed: dict[str, int] = {}
    owners: dict[str, set[str]] = {}
    state = ledger.read()
    with (
        ledger.content_lock(),
        ledger.child("store") as source,
        writer_lock(source),
        ledger.child("public") as target,
    ):
        for identity, held in state.models.items():
            if held.blocked or held.error or held.withdrawn or held.state != "retained":
                continue
            try:
                manifest = verify_retained(
                    source, identity, held, control.fork(), ledger=ledger
                )
                require_terms(manifest, held.grant)
                paths = {
                    object_path(f.sha256): f.size for f in archive_objects(manifest)
                }
                paths[manifest_path(identity)] = len(
                    read_metadata(source, manifest_path(identity))
                )
                for path, size in paths.items():
                    control.checkpoint()
                    _link(source, target, path)
                    allowed["/" + path] = size
                    owners.setdefault("/" + path, set()).add(identity)
            except OperationCancelled:
                raise
            except (ValueError, OSError) as error:
                record_model(
                    ledger,
                    identity,
                    {"error": failure_code(error) + ": " + str(error)[:400]},
                )
        for prefix in ("manifests", "objects/sha256"):
            try:
                with parent_directory(target, prefix + "/placeholder") as (
                    parent,
                    _name,
                ):
                    for name in os.listdir(parent):
                        path = prefix + "/" + name
                        if "/" + path not in allowed:
                            # Only our exact content-addressed public files are retired.
                            if re.fullmatch(
                                r"[0-9a-f]{64}\.json"
                                if prefix == "manifests"
                                else r"[0-9a-f]{64}",
                                name,
                            ):
                                os.unlink(name, dir_fd=parent)
                    os.fsync(parent)
            except FileNotFoundError:
                pass
    return allowed, owners


class MirrorServer(SeedServer):
    """Reuse bounded seed HTTP/ranges with local policy and persistent accounting."""

    daemon_threads = False
    budget_status = 503

    def __init__(self, ledger: MirrorLedger, authorized: Callable[[MirrorState], bool]):
        self.ledger = ledger
        self.authorized = authorized
        self.store = ledger.path / "public"
        # A mirror's owner may publish its signed peers document here; it is
        # read on each request so gossip updates need no restart.
        self.documents = {"/" + PEERS_PATH: ledger.path / "peers.json"}
        self.allowed = {}
        self.owners: dict[str, set[str]] = {}
        self._slots = threading.BoundedSemaphore(4)
        self._connections: set[socket.socket] = set()
        self._connections_lock = threading.Lock()
        self._closing = False
        # TLS terminates in a reverse proxy (see deploy/mirror); the mirror
        # itself normally listens on loopback only.
        self.tls_context = None
        state = ledger.read()
        self.address_family = socket.AF_INET6 if ":" in state.bind else socket.AF_INET
        http.server.ThreadingHTTPServer.__init__(
            self, (state.bind, state.port), SeedHandler
        )

    def process_request(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: tuple[str, int],
    ) -> None:
        """Register accepted sockets before workers start; reject late arrivals."""
        if isinstance(request, socket.socket):
            with self._connections_lock:
                if self._closing:
                    self.shutdown_request(request)
                    return
                self._connections.add(request)
        try:
            super().process_request(request, client_address)
        finally:
            if isinstance(request, socket.socket) and request.fileno() < 0:
                with self._connections_lock:
                    self._connections.discard(request)

    def process_request_thread(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: tuple[str, int],
    ) -> None:
        """Retire socket ownership only after the actual handler finishes."""
        try:
            super().process_request_thread(request, client_address)
        finally:
            if isinstance(request, socket.socket):
                with self._connections_lock:
                    self._connections.discard(request)

    def close_connections(self) -> None:
        """Interrupt incomplete headers and slow transfers before joining workers."""
        with self._connections_lock:
            self._closing = True
            for connection in self._connections:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def handle_error(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: tuple[str, int],
    ) -> None:
        """Keep disconnect/parser failures out of remote-address request history."""

    def _permitted(self, state: MirrorState, path: str) -> bool:
        return self.authorized(state) and any(
            (held := state.models.get(identity)) is not None
            and not held.blocked
            and not held.error
            and not held.withdrawn
            and held.state == "retained"
            for identity in self.owners.get(path, ())
        )

    def charge_document(self, count: int) -> bool:
        """Charge a peers document while this mirror is authorized to serve at all."""
        try:
            return self.ledger.reserve(count, authorized=self.authorized)
        except (ValueError, OSError):
            return False

    def charge(self, count: int, path: str | None = None) -> bool:
        """Fsync full response reservation before sending; fail closed on contention."""
        try:
            return path is not None and self.ledger.reserve(
                count, authorized=lambda state: self._permitted(state, path)
            )
        except (ValueError, OSError):
            return False

    def can_send(self, path: str) -> bool:
        """Honor pause/block/revocation between chunks, retaining the full charge."""
        try:
            state = self.ledger.read()
            return (
                not state.paused
                and not state.left
                and self._permitted(state, path)
                and int(time.time()) >= state.clock_floor
                and serving_hour(state, time.gmtime().tm_hour)
            )
        except (ValueError, OSError):
            return False

    @property
    def reserved_upload_bytes(self) -> int:
        """Report this UTC month's durable reservations, not bytes delivered."""
        return self.ledger.read().egress_month_used


def run_mirror(
    ledger: MirrorLedger,
    *,
    resume: bool = False,
    public: bool = False,
    seconds: int = 3600,
    control: OperationControl | None = None,
    ready: Callable[[int], None] | None = None,
    prepared: Callable[[int | None], None] | None = None,
    observe: Callable[[MirrorObservation], None] | None = None,
    serving_selection: Callable[[tuple[str, ...]], None] | None = None,
    expected_policy_id: str | None = None,
    native_peer_runner: PeerSessionRunner | None = None,
) -> dict[str, JsonValue]:
    """Own selected mirror transports until stop, pause/leave or grant expiry.

    Manual runs require a fresh explicit public grant and expire within 24 hours.
    Service-manager runs require the separate exact-policy restart grant. Stop
    joins byte work and actual HTTP handlers; no retention is deleted or replayed.
    The ready callback reports only an enabled HTTP listener's actual port.
    Prepared reports controller startup with the owned HTTP port or None when
    disabled, not native publication, public reachability or a new serving grant.
    Optional synchronous observations report owned activity at most once per
    second plus a drained final status. A failing observer stops and drains the
    controller; observers must remain short and must not grant any authority.
    Serving_selection reports exact verified HTTP selections after reconciliation,
    never external reachability or independent custody. It must return promptly.
    Expected_policy_id optionally fences an embedding host's reviewed policy
    inside the service lock before any listener or native worker is opened.
    A trusted embedding host may select a native peer runner under its own
    dedicated process owner; ordinary callers retain the standalone supervisor.
    The adapter does not bypass profile, trust, upload or session permission.
    """
    if public == resume or not 1 <= seconds <= 86400:
        raise PreservationError(
            "choose explicit finite public run or separately granted resume"
        )
    active = control or OperationControl()
    stop = threading.Event()
    deadline = time.monotonic() + seconds if public else float("inf")

    def authorized(current: MirrorState) -> bool:
        return (
            not stop.is_set()
            and time.monotonic() < deadline
            and not current.paused
            and not current.left
            and policy_id(current) == pinned_policy
            and (not resume or restart_allowed(current))
        )

    with ledger.service_lock():
        state = ledger.read()
        if expected_policy_id is not None and policy_id(state) != expected_policy_id:
            raise PreservationError("mirror policy differs from the reviewed selection")
        if (ledger.path / "prune.json").exists():
            raise PreservationError(
                "finish the interrupted operator prune before serving"
            )
        if not state.http_enabled and not (
            state.native_policy
            and (state.native_policy.ipfs_binary or state.native_policy.torrent)
        ):
            raise PreservationError("select at least one mirror serving transport")
        if state.sources and state.schema_version != "foxlight.mirror-state.v5":
            if resume or state.paused or state.left:
                raise PreservationError(
                    "source custody upgrade needs explicit run or renewed restart grant"
                )
            state = ledger.update(
                lambda current: current.model_copy(
                    update={
                        "schema_version": "foxlight.mirror-state.v5",
                        "restart": None,
                    }
                )
            )
        pinned_policy = policy_id(state)
        if not authorized(state):
            raise PreservationError(
                "mirror is paused/left or lacks current restart authority"
            )
        server = MirrorServer(ledger, authorized) if state.http_enabled else None
        web = (
            threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1})
            if server is not None
            else None
        )
        error: list[BaseException] = []
        announcer = Announcer(ledger)

        def announce(serving: frozenset[str]) -> None:
            # Announcement is best effort and never stops retained service.
            if ledger.read().announce is None:
                return
            try:
                announcer.round(serving, active.fork())
            except OperationCancelled:
                raise
            except (ValueError, OSError) as failure:
                code = "announce_" + failure_code(failure)
                ledger.update(
                    lambda current: current.model_copy(update={"last_error": code})
                )

        def worker() -> None:
            while not stop.is_set():
                try:
                    # Recover the completed intent before any source lookup.
                    recover_completed(ledger, active.fork())
                    # Retained copies remain useful even when all sources are gone.
                    if server is not None:
                        server.allowed, server.owners = public_selection(
                            ledger, active.fork()
                        )
                    # A budget or source failure must not skip the steps below:
                    # withdrawals already recorded must leave the serving set,
                    # and the card must be renewed from verified retained copies.
                    failed: BaseException | None = None
                    try:
                        reconcile(ledger, active.fork())
                    except OperationCancelled:
                        raise
                    except (ValueError, OSError) as failure:
                        failed = failure
                    if server is not None:
                        server.allowed, server.owners = public_selection(
                            ledger, active.fork()
                        )
                        serving = frozenset(
                            identity
                            for identities in server.owners.values()
                            for identity in identities
                        )
                        if serving_selection is not None:
                            try:
                                serving_selection(tuple(sorted(serving)))
                            except Exception as failure:
                                error.append(failure)
                                return
                        announce(serving)
                    if failed is not None:
                        # Status records the reconcile failure, not the announce.
                        raise failed
                except OperationCancelled:
                    return
                except (ValueError, OSError) as failure:
                    try:
                        code = failure_code(failure)
                        ledger.update(
                            lambda current, code=code: current.model_copy(
                                update={"last_error": code}
                            )
                        )
                    except (ValueError, OSError):
                        error.append(failure)
                        return
                if stop.wait(ledger.read().refresh_seconds):
                    return

        background = threading.Thread(target=worker)
        native = NativeMirrors(ledger, authorized, peer_runner=native_peer_runner)
        if web is not None:
            web.start()
        background.start()
        try:
            if prepared:
                prepared(server.server_port if server is not None else None)
            if ready and server is not None:
                ready(server.server_port)
            next_observation = 0.0
            while time.monotonic() < deadline and not error:
                active.checkpoint()
                if not authorized(ledger.read()):
                    break
                native.tick()
                if observe is not None and time.monotonic() >= next_observation:
                    observe(
                        MirrorObservation(
                            running=True,
                            http_port=server.server_port
                            if server is not None
                            else None,
                            native_workers=sum(
                                session.thread.is_alive()
                                for session in native.sessions.values()
                            ),
                        )
                    )
                    next_observation = time.monotonic() + 1
                stop.wait(0.1)
        finally:
            stop.set()
            active.cancel()
            native.close()
            if server is not None:
                server.close_connections()
                server.shutdown()
            background.join()
            announcer.close()
            if server is not None:
                server.server_close()
            if web is not None:
                web.join()
            if observe is not None:
                observe(MirrorObservation(running=False, native_workers=0))
        if error:
            raise error[0]
    return {"stopped": True, "retained": True, "network_started": True}
