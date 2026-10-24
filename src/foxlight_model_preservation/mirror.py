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

from .discovery import follow_source, refresh_source, search_sources
from .mirror_models import HeldModel, MirrorState, SourceGrant
from .mirror_native import NativeMirrors
from .mirror_state import (
    HEADROOM,
    MirrorLedger,
    policy_id,
    restart_allowed,
    serving_hour,
)
from .models import Manifest, PreservationError
from .operations import OperationCancelled, OperationControl, failure_code
from .seed import SeedHandler, SeedServer
from .signing import digest, verify_manifest
from .storage import (
    manifest_path,
    object_path,
    observe_objects,
    open_file,
    parent_directory,
    read_metadata,
    unique_objects,
    verify_object,
    writer_lock,
)
from .transfer import TransferPolicy, fetch, inspect_manifest


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
        "retry": {"error": None, "state": "pending"},
    }
    if action not in changes:
        raise PreservationError("unsupported mirror model action")
    update = cast(dict[str, object], changes[action])
    return record_model(ledger, manifest_id, update)


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
    }:
        raise PreservationError("selection authority cannot be changed by a receipt")

    def apply(state: MirrorState) -> MirrorState:
        if manifest_id not in state.models:
            raise PreservationError("unknown retained mirror selection")
        models = dict(state.models)
        selected_changes = dict(changes)
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


def require_terms(manifest: Manifest, grant: SourceGrant) -> None:
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


def verify_retained(
    root: int, identity: str, held: HeldModel, control: OperationControl
) -> Manifest:
    """Verify original model trust, public terms and every retained object."""
    envelope = read_metadata(root, manifest_path(identity))
    if held.origin == "local" and digest(envelope) != held.envelope_sha256:
        raise PreservationError("local selection's original signed wrapper changed")
    manifest = verify_manifest(envelope, held.grant.trust, identity)
    require_terms(manifest, held.grant)
    for file in unique_objects(manifest):
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
                verify_retained(store, identity, held, control.fork())
        except OperationCancelled:
            raise
        except (ValueError, OSError):
            continue
        record_model(
            ledger, identity, {"state": "retained", "verified_at": int(time.time())}
        )


def reconcile(ledger: MirrorLedger, control: OperationControl) -> None:
    """Refresh followed sources, admit trusted public closures and retain old copies.

    Caller owns the service lock. Each attempted selection retains one immutable
    operation and conservative ingress reservation. Failures require explicit
    retry; source changes never delete bytes, pins or accepted model evidence.
    """
    recover_completed(ledger, control)
    state = ledger.read()
    refresh_error = None
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
    state = ledger.read()
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
            entry_id=location.entry_id,
            grant=grant,
            mirrors=mirrors,
            operation_id="m" + digest((state.salt + result.manifest).encode())[:63],
        )
        ledger.update(
            lambda current, identity=result.manifest, selected=held: current.model_copy(
                update={"models": dict(current.models, **{identity: selected})}
            )
        )
    fresh = {
        (r.manifest, x.source_id)
        for r in results
        for x in r.locations
        if x.freshness == "fresh"
    }
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
                # Reserve both bounded metadata passes before first contact.
                # Object credit is proportional to the authenticated closure.
                if not ledger.reserve(
                    HEADROOM + 4096,
                    ingress=True,
                    operation="meta-" + digest(held.operation_id.encode())[:59],
                ):
                    raise PreservationError("mirror ingress budget unavailable")
                manifest = inspect_manifest(
                    identity,
                    held.grant.trust,
                    held.mirrors,
                    _policy(held.grant),
                    control=control.fork(),
                )
                require_terms(manifest, held.grant)
                allowance = held.transfer_bytes
                if allowance is None:
                    allowance = min(
                        held.grant.max_bytes,
                        3 * sum(f.size for f in unique_objects(manifest))
                        + 16 * 1024**2,
                    )
                    record_model(ledger, identity, {"transfer_bytes": allowance})
                if not ledger.reserve(
                    allowance, ingress=True, operation=held.operation_id
                ):
                    raise PreservationError("mirror ingress budget unavailable")
                with ledger.child("store") as store:
                    missing, _reused, partial = observe_objects(
                        store,
                        unique_objects(manifest),
                        control.fork(),
                        include_partials=True,
                    )
                additional = sum(f.size for f in missing) - partial
                usage = ledger.usage()
                if usage + additional + HEADROOM > ledger.read().disk_bytes or (
                    shutil.disk_usage(ledger.path).free < additional + HEADROOM
                ):
                    raise PreservationError("mirror disk admission budget exhausted")
                fetch(
                    identity,
                    held.grant.trust,
                    held.mirrors,
                    ledger.path / "store",
                    _policy(held.grant, allowance),
                    control=control.fork(),
                    operation_id=held.operation_id,
                    expected_directory=ledger.read().identity["store"],
                )
            with ledger.child("store") as store:
                verify_retained(store, identity, held, control.fork())
            record_model(
                ledger,
                identity,
                {"state": "retained", "verified_at": int(time.time()), "error": None},
            )
        except OperationCancelled:
            raise
        except (ValueError, OSError) as error:
            record_model(
                ledger,
                identity,
                {"error": failure_code(error) + ": " + str(error)[:400]},
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
            if held.blocked or held.error or held.state != "retained":
                continue
            try:
                manifest = verify_retained(source, identity, held, control.fork())
                require_terms(manifest, held.grant)
                paths = {
                    object_path(f.sha256): f.size for f in unique_objects(manifest)
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
        self.allowed = {}
        self.owners: dict[str, set[str]] = {}
        self._slots = threading.BoundedSemaphore(4)
        self._connections: set[socket.socket] = set()
        self._connections_lock = threading.Lock()
        self._closing = False
        self.tls_context = None
        state = ledger.read()
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
            and held.state == "retained"
            for identity in self.owners.get(path, ())
        )

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
) -> dict[str, JsonValue]:
    """Own a persistent HTTP controller until stop, pause/leave policy or grant expiry.

    Manual runs require a fresh explicit public grant and expire within 24 hours.
    Service-manager runs require the separate exact-policy restart grant. Stop
    joins byte work and actual HTTP handlers; no retention is deleted or replayed.
    """
    if public == resume or not 1 <= seconds <= 86400:
        raise PreservationError(
            "choose explicit finite public run or separately granted resume"
        )
    active = control or OperationControl()
    stop = threading.Event()
    state = ledger.read()
    deadline = time.monotonic() + seconds if public else float("inf")
    pinned_policy = policy_id(state)

    def authorized(current: MirrorState) -> bool:
        return (
            not stop.is_set()
            and time.monotonic() < deadline
            and not current.paused
            and not current.left
            and policy_id(current) == pinned_policy
            and (not resume or restart_allowed(current))
        )

    if not authorized(state):
        raise PreservationError(
            "mirror is paused/left or lacks current restart authority"
        )
    with ledger.service_lock():
        if (ledger.path / "prune.json").exists():
            raise PreservationError(
                "finish the interrupted operator prune before serving"
            )
        server = MirrorServer(ledger, authorized)
        web = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.1}
        )
        error: list[BaseException] = []

        def worker() -> None:
            while not stop.is_set():
                try:
                    # Recover the completed intent before any source lookup.
                    recover_completed(ledger, active.fork())
                    # Retained copies remain useful even when all sources are gone.
                    server.allowed, server.owners = public_selection(
                        ledger, active.fork()
                    )
                    reconcile(ledger, active.fork())
                    server.allowed, server.owners = public_selection(
                        ledger, active.fork()
                    )
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
        native = NativeMirrors(ledger, authorized)
        web.start()
        background.start()
        try:
            if ready:
                ready(server.server_port)
            while time.monotonic() < deadline and not error:
                active.checkpoint()
                if not authorized(ledger.read()):
                    break
                native.tick()
                stop.wait(0.1)
        finally:
            stop.set()
            active.cancel()
            native.close()
            server.close_connections()
            server.shutdown()
            background.join()
            server.server_close()
            web.join()
        if error:
            raise error[0]
    return {"stopped": True, "retained": True, "network_started": True}
