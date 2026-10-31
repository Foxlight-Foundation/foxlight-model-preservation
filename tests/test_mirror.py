"""Persistent authority, publisher-loss service and explicit deletion."""

from __future__ import annotations

import http.client
import json
import queue
import shutil
import threading
import time
from pathlib import Path

import pytest
from conftest import Capture
from test_discovery import Publisher

from foxlight_model_preservation.mirror import (
    follow,
    model_action,
    plan,
    public_selection,
    reconcile,
    run_mirror,
)
from foxlight_model_preservation.mirror_intake import retain_local
from foxlight_model_preservation.mirror_models import SourceGrant, Terms
from foxlight_model_preservation.mirror_prune import prune
from foxlight_model_preservation.mirror_state import (
    MirrorLedger,
    grant_restart,
    policy_id,
    restart_allowed,
)
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationControl
from foxlight_model_preservation.signing import encode_record
from foxlight_model_preservation.storage import (
    manifest_path,
    object_path,
    pack,
    verify_store,
)
from foxlight_model_preservation.transfer import TransferPolicy, fetch


def mirror(tmp_path: Path, capture: Capture) -> tuple[MirrorLedger, Publisher]:
    publisher = Publisher(tmp_path / "publisher", capture)
    ledger = MirrorLedger.initialize(
        tmp_path / "mirror",
        name="Fixture mirror",
        disk_bytes=2 * 1024**3,
        ingress_daily=10 * 1024**3,
        egress_daily=1024**3,
        egress_monthly=10 * 1024**3,
        port=0,
    )
    bootstrap = publisher.bootstrap()
    from foxlight_model_preservation.discovery import bootstrap_identity

    follow(
        ledger,
        encode_record(bootstrap),
        bootstrap_identity(bootstrap),
        SourceGrant(
            trust=capture.trust,
            terms=(Terms(license_id="test-only", terms_reference="LICENSE"),),
            allow_local=True,
            max_bytes=10**6,
        ),
    )
    return ledger, publisher


def cycle(ledger: MirrorLedger) -> None:
    with ledger.service_lock():
        reconcile(ledger, OperationControl())


def test_follow_plan_are_offline_and_model_keys_independent(
    tmp_path, capture, monkeypatch
):
    ledger, publisher = mirror(tmp_path, capture)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("offline action connected")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    result = plan(ledger)
    assert not result["network_started"]
    assert not ledger.read().models
    assert not restart_allowed(ledger.read())
    # Enrollment did not fetch even the source catalog.
    assert not list((ledger.path / "store").rglob("*.json"))
    assert publisher.initial


def test_response_reservations_survive_restart_rollover_and_rollback(tmp_path):
    now = int(time.time())
    if time.gmtime(now + 86400).tm_mon != time.gmtime(now).tm_mon:
        # On a month's last day "tomorrow" would also reset the monthly
        # window; start two days later so tomorrow stays in the same month.
        now += 2 * 86400
    ledger = MirrorLedger.initialize(
        tmp_path / "mirror",
        name="Ledger",
        disk_bytes=1024**3,
        ingress_daily=100,
        egress_daily=100,
        egress_monthly=150,
    )
    assert ledger.reserve(90, now=now)
    assert not MirrorLedger(ledger.path).reserve(11, now=now)
    assert not ledger.reserve(1, now=now - 100)
    # A forward UTC day resets only the daily window, never the month balance.
    tomorrow = now + 86400
    assert ledger.reserve(50, now=tomorrow)
    assert not ledger.reserve(11, now=tomorrow)
    assert not ledger.reserve(1, now=now)
    assert ledger.read().egress_month_used == 140
    operation = "a" * 64
    assert ledger.reserve(80, ingress=True, operation=operation, now=tomorrow)
    assert MirrorLedger(ledger.path).reserve(
        80, ingress=True, operation=operation, now=tomorrow
    )
    assert ledger.read().ingress_used == 80
    assert not ledger.reserve(21, ingress=True, now=tomorrow)


def test_wrong_terms_refuse_before_weights_and_retry_does_not_refill(
    tmp_path, capture, monkeypatch
):
    ledger, _ = mirror(tmp_path, capture)
    source = next(iter(ledger.read().sources))
    original = ledger.read().sources[source]
    ledger.update(
        lambda s: s.model_copy(
            update={
                "sources": {
                    source: original.model_copy(
                        update={
                            "terms": (
                                Terms(license_id="other", terms_reference="LICENSE"),
                            )
                        }
                    )
                }
            }
        )
    )
    downloads = []
    monkeypatch.setattr(
        "foxlight_model_preservation.transfer._download",
        lambda *a, **kw: downloads.append(a),
    )
    cycle(ledger)
    state = ledger.read()
    held = state.models[capture.identity]
    assert held.error and held.state == "pending" and not downloads
    used = state.ingress_used
    model_action(ledger, capture.identity, "retry")
    cycle(ledger)
    # Only the source refresh adds its fixed metadata reservation on retry.
    assert ledger.read().ingress_used == used + 32 * 1024**2
    assert not downloads


def test_untrusted_capture_never_becomes_retained(tmp_path, capture):
    ledger, _ = mirror(tmp_path, capture)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from foxlight_model_preservation.signing import public_record

    source = next(iter(ledger.read().sources))
    old = ledger.read().sources[source]
    trust = old.trust.model_copy(
        update={"keys": (public_record(Ed25519PrivateKey.generate()),)}
    )
    ledger.update(
        lambda s: s.model_copy(
            update={"sources": {source: old.model_copy(update={"trust": trust})}}
        )
    )
    cycle(ledger)
    assert ledger.read().models[capture.identity].error
    assert not (ledger.path / "store" / manifest_path(capture.identity)).exists()


def test_retained_service_survives_publisher_loss_and_pause_preserves_bytes(
    tmp_path, capture
):
    ledger, publisher = mirror(tmp_path, capture)
    cycle(ledger)
    held = ledger.read().models[capture.identity]
    assert held.state == "retained" and held.error is None
    original = (ledger.path / "store" / manifest_path(capture.identity)).read_bytes()
    shutil.rmtree(publisher.path)
    shutil.rmtree(capture.store)
    grant_restart(ledger, int(time.time()) + 600)
    ready = queue.Queue()
    result = []

    def run():
        result.append(run_mirror(ledger, resume=True, ready=ready.put))

    thread = threading.Thread(target=run)
    thread.start()
    port = ready.get(timeout=10)
    try:
        end = time.monotonic() + 15
        while True:
            client = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            client.request("GET", "/" + manifest_path(capture.identity))
            reply = client.getresponse()
            body = reply.read()
            client.close()
            if reply.status == 200:
                break
            assert time.monotonic() < end
            time.sleep(0.05)
        assert body == original
        received = tmp_path / "received"
        value = fetch(
            capture.identity,
            capture.trust,
            (f"http://127.0.0.1:{port}",),
            received,
            TransferPolicy(max_bytes=10**6, allow_http=True, allow_private=True),
        )
        assert value == verify_store(
            ledger.path / "store", capture.identity, capture.trust
        )
        assert (received / manifest_path(capture.identity)).read_bytes() == original
        model_action(ledger, capture.identity, "block")
        client = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        client.request("GET", "/" + manifest_path(capture.identity))
        reply = client.getresponse()
        reply.read()
        client.close()
        assert reply.status == 503
        ledger.update(lambda s: s.model_copy(update={"paused": True}))
        thread.join(10)
        assert not thread.is_alive()
        assert result[0]["stopped"]
        assert (
            verify_store(ledger.path / "store", capture.identity, capture.trust)
            == value
        )
        assert MirrorLedger(ledger.path).read().egress_month_used > 0
        with pytest.raises(PreservationError, match="restart authority"):
            run_mirror(ledger, resume=True)
    finally:
        if thread.is_alive():
            ledger.update(lambda s: s.model_copy(update={"paused": True}))
            thread.join(35)


def test_restart_requires_separate_current_unexpired_grant(tmp_path, capture):
    ledger, _ = mirror(tmp_path, capture)
    before = policy_id(ledger.read())
    with pytest.raises(PreservationError, match="restart authority"):
        run_mirror(ledger, resume=True)
    grant_restart(ledger, int(time.time()) + 600)
    assert restart_allowed(ledger.read())
    ledger.update(lambda s: s.model_copy(update={"egress_daily": 1}))
    assert policy_id(ledger.read()) != before and not restart_allowed(ledger.read())
    grant_restart(ledger, int(time.time()) + 600)
    assert not restart_allowed(ledger.read(), now=int(time.time()) + 700)
    ledger.update(lambda s: s.model_copy(update={"restart": None}))
    assert not restart_allowed(ledger.read())


def test_pin_block_prune_protects_shared_objects_and_refuses_active_owner(
    tmp_path, capture
):
    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    other = pack(
        capture.recipe.model_copy(update={"name": "Second copy"}),
        capture.sources,
        capture.store,
        (capture.key,),
    )
    retain_local(
        ledger,
        capture.store,
        other,
        SourceGrant(
            trust=capture.trust,
            terms=(Terms(license_id="test-only", terms_reference="LICENSE"),),
        ),
    )
    model_action(ledger, capture.identity, "block")
    model_action(ledger, capture.identity, "pin")
    with pytest.raises(PreservationError, match="unpinned"):
        prune(ledger, capture.identity, capture.identity)
    model_action(ledger, capture.identity, "unpin")
    with (
        ledger.service_lock(),
        pytest.raises(PreservationError, match="stop the active"),
    ):
        prune(ledger, capture.identity, capture.identity)
    with pytest.raises(PreservationError, match="confirm"):
        prune(ledger, capture.identity, "b" * 64)
    result = prune(ledger, capture.identity, capture.identity)
    assert result["removed"] and result["unshared_objects_selected"] == 0
    assert not (ledger.path / "store" / manifest_path(capture.identity)).exists()
    verify_store(ledger.path / "store", other, capture.trust)
    assert ledger.read().models[capture.identity].state == "removed"
    cycle(ledger)
    assert not (ledger.path / "store" / manifest_path(capture.identity)).exists()


def test_corruption_withdraws_serving_without_deleting_retention(tmp_path, capture):
    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    with ledger.service_lock():
        allowed, _ = public_selection(ledger, OperationControl())
        assert "/" + manifest_path(capture.identity) in allowed
        value = verify_store(ledger.path / "store", capture.identity, capture.trust)
        path = ledger.path / "store" / object_path(value.members[0].files[0].sha256)
        path.write_bytes(b"corrupted")
        allowed, _ = public_selection(ledger, OperationControl())
        assert "/" + manifest_path(capture.identity) not in allowed
    assert path.exists() and ledger.read().models[capture.identity].error


def test_replaced_root_and_symlink_refuse_without_recreating(tmp_path, capture):
    ledger, _ = mirror(tmp_path, capture)
    saved = ledger.path.with_name("old")
    ledger.path.rename(saved)
    ledger.path.mkdir(mode=0o700)
    shutil.copyfile(saved / "mirror.json", ledger.path / "mirror.json")
    with pytest.raises((OSError, ValueError)):
        MirrorLedger(ledger.path)
    assert not (ledger.path / "store").exists()
    shutil.rmtree(ledger.path)
    ledger.path.symlink_to(saved, target_is_directory=True)
    with pytest.raises(OSError):
        MirrorLedger(ledger.path)


def test_serving_window_uses_utc_and_does_not_reset_budget(tmp_path):
    from foxlight_model_preservation.mirror_state import serving_hour

    ledger = MirrorLedger.initialize(
        tmp_path / "mirror",
        name="Window",
        disk_bytes=1024**3,
        ingress_daily=100,
        egress_daily=100,
        egress_monthly=150,
        serve_start=23,
        serve_end=2,
    )
    assert serving_hour(ledger.read(), 23) and serving_hour(ledger.read(), 1)
    assert not serving_hour(ledger.read(), 2) and not serving_hour(ledger.read(), 22)


def test_interrupted_prune_is_explicitly_resumable_and_blocks_service(
    tmp_path, capture, monkeypatch
):
    import os

    import foxlight_model_preservation.mirror_prune as deletion

    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    model_action(ledger, capture.identity, "block")
    store_inode = (ledger.path / "store").stat().st_ino
    original = deletion._unlink

    def interrupt(root, path):
        original(root, path)
        if os.fstat(root).st_ino == store_inode and path == manifest_path(
            capture.identity
        ):
            raise OSError("injected crash after manifest retirement")

    monkeypatch.setattr(deletion, "_unlink", interrupt)
    with pytest.raises(OSError, match="injected crash"):
        prune(ledger, capture.identity, capture.identity)
    assert (ledger.path / "prune.json").exists()
    with pytest.raises(PreservationError, match="interrupted operator prune"):
        run_mirror(ledger, public=True)
    monkeypatch.setattr(deletion, "_unlink", original)
    result = prune(ledger, capture.identity, capture.identity)
    assert result["removed"] and result["unshared_objects_selected"] > 0
    assert not (ledger.path / "prune.json").exists()
    assert not list((ledger.path / "store/objects/sha256").iterdir())


def test_source_replacement_and_expiry_keep_retained_copy_and_pin(tmp_path, capture):
    from datetime import UTC, datetime, timedelta

    ledger, publisher = mirror(tmp_path, capture)
    cycle(ledger)
    model_action(ledger, capture.identity, "pin")
    original = (ledger.path / "store" / manifest_path(capture.identity)).read_bytes()
    # A new signed catalog no longer lists the old exact manifest. Its fake new
    # manifest cannot authenticate, while the old retained copy remains usable.
    publisher.publish(version=2, manifest="b" * 64)
    cycle(ledger)
    assert ledger.read().models[capture.identity].pinned
    assert (
        ledger.path / "store" / manifest_path(capture.identity)
    ).read_bytes() == original
    publisher.publish(version=3, expires=datetime.now(UTC) - timedelta(seconds=1))
    cycle(ledger)
    assert ledger.read().last_error
    verify_store(ledger.path / "store", capture.identity, capture.trust)
    assert ledger.read().models[capture.identity].pinned


def test_private_member_and_changed_terms_never_fetch_objects(
    tmp_path, capture, monkeypatch
):
    from foxlight_model_preservation.storage import pack

    changed = capture.recipe.model_copy(
        update={
            "members": tuple(
                m.model_copy(
                    update={
                        "intake": m.intake.model_copy(update={"audience": "private"})
                    }
                )
                if i == 0
                else m
                for i, m in enumerate(capture.recipe.members)
            )
        }
    )
    identity = pack(changed, capture.sources, capture.store, (capture.key,))
    ledger, publisher = mirror(tmp_path, capture)
    publisher.publish(version=2, manifest=identity)
    downloads = []
    monkeypatch.setattr(
        "foxlight_model_preservation.transfer._download",
        lambda *a, **kw: downloads.append(a),
    )
    cycle(ledger)
    assert ledger.read().models[identity].error and not downloads


def test_disk_pressure_does_not_evict_retained_or_refill_ingress(tmp_path, capture):
    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    model_action(ledger, capture.identity, "pin")
    before = (ledger.path / "store" / manifest_path(capture.identity)).read_bytes()
    ledger.update(lambda s: s.model_copy(update={"disk_bytes": 256 * 1024**2}))
    with pytest.raises(PreservationError, match="disk admission"):
        cycle(ledger)
    assert ledger.read().models[capture.identity].pinned
    assert (
        ledger.path / "store" / manifest_path(capture.identity)
    ).read_bytes() == before


def test_failed_budget_commit_sends_no_reserved_response(tmp_path, monkeypatch):
    ledger = MirrorLedger.initialize(
        tmp_path / "mirror",
        name="Commit",
        disk_bytes=1024**3,
        ingress_daily=100,
        egress_daily=100,
        egress_monthly=150,
    )

    def fail(*a, **kw):
        raise OSError("injected ledger fsync failure")

    monkeypatch.setattr("foxlight_model_preservation.mirror_state.atomic_bytes", fail)
    with pytest.raises(OSError):
        ledger.reserve(50)
    assert MirrorLedger(ledger.path).read().egress_month_used == 0


def test_completed_intent_recovers_without_publisher_or_registry(tmp_path, capture):
    from foxlight_model_preservation.mirror import recover_completed

    ledger, publisher = mirror(tmp_path, capture)
    cycle(ledger)
    held = ledger.read().models[capture.identity]
    ledger.update(
        lambda s: s.model_copy(
            update={
                "models": {
                    capture.identity: held.model_copy(
                        update={"state": "pending", "verified_at": None}
                    )
                }
            }
        )
    )
    shutil.rmtree(publisher.path)
    shutil.rmtree(capture.store)
    recover_completed(ledger, OperationControl())
    assert ledger.read().models[capture.identity].state == "retained"
    with ledger.service_lock():
        assert ledger.running()
        allowed, _ = public_selection(ledger, OperationControl())
        assert "/" + manifest_path(capture.identity) in allowed
    assert not ledger.running()


def test_cli_kill_releases_owner_and_preserves_grants_budgets_and_copies(
    tmp_path, capture
):
    import selectors
    import subprocess
    import sys

    ledger, _ = mirror(tmp_path, capture)

    def start():
        p = subprocess.Popen(  # noqa: S603 - isolated test CLI, no shell
            [
                sys.executable,
                "-m",
                "foxlight_model_preservation.cli",
                "mirror",
                "run",
                "--dir",
                str(ledger.path),
                "--public",
                "--seconds",
                "120",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert p.stdout
        selector = selectors.DefaultSelector()
        selector.register(p.stdout, selectors.EVENT_READ)
        try:
            assert selector.select(10), "CLI readiness deadline"
            ready = json.loads(p.stdout.readline())
            assert ready["ready"]
        except BaseException:
            p.kill()
            p.wait(timeout=5)
            p.stdout.close()
            if p.stderr:
                p.stderr.close()
            raise
        finally:
            selector.close()
        return p, ready["port"]

    process, port = start()
    try:
        end = time.monotonic() + 15
        while True:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            connection.request("GET", "/" + manifest_path(capture.identity))
            response = connection.getresponse()
            body = response.read()
            connection.close()
            if response.status == 200:
                break
            assert time.monotonic() < end
            time.sleep(0.05)
        assert (
            body
            == (ledger.path / "store" / manifest_path(capture.identity)).read_bytes()
        )
        process.kill()
        process.wait(timeout=5)
        assert not ledger.running()
        reserved = MirrorLedger(ledger.path).read().egress_month_used
        assert reserved > 0
        denied = subprocess.run(  # noqa: S603 - isolated test CLI, no shell
            [
                sys.executable,
                "-m",
                "foxlight_model_preservation.cli",
                "mirror",
                "run",
                "--dir",
                str(ledger.path),
                "--resume",
            ],
            capture_output=True,
            timeout=10,
        )
        assert denied.returncode == 1 and not ledger.running()
        assert MirrorLedger(ledger.path).read().egress_month_used == reserved
        verify_store(ledger.path / "store", capture.identity, capture.trust)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()


def test_fetch_rechecks_enrolled_directory_before_any_write(
    tmp_path, capture, monkeypatch
):
    import foxlight_model_preservation.transfer as transport

    ledger, _ = mirror(tmp_path, capture)
    enrolled = ledger.read().identity["store"]
    store = ledger.path / "store"
    original = transport._load_manifest

    def replace_directory(*args, **kwargs):
        result = original(*args, **kwargs)
        store.rename(ledger.path / "former-store")
        store.mkdir(mode=0o700)
        return result

    monkeypatch.setattr(transport, "_load_manifest", replace_directory)
    with pytest.raises(PreservationError, match="identity changed"):
        fetch(
            capture.identity,
            capture.trust,
            (str(capture.store),),
            store,
            TransferPolicy(max_bytes=10**6),
            expected_directory=enrolled,
        )
    assert list(store.iterdir()) == []


def test_full_selection_ledger_continues_existing_pending_work(
    tmp_path, capture, monkeypatch
):
    import foxlight_model_preservation.mirror as controller
    from foxlight_model_preservation.discovery_models import SearchResult

    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    held = ledger.read().models[capture.identity]
    models = {
        f"{i:064x}": held.model_copy(update={"state": "removed", "blocked": True})
        for i in range(999)
    }
    models[capture.identity] = held.model_copy(update={"state": "pending"})
    ledger.update(lambda s: s.model_copy(update={"models": models}))
    (ledger.path / "store" / manifest_path(capture.identity)).unlink()
    search = controller.search_sources

    def added(*args, **kwargs):
        actual = search(*args, **kwargs)
        return (*actual, SearchResult(manifest="f" * 64, locations=actual[0].locations))

    monkeypatch.setattr(controller, "search_sources", added)
    cycle(ledger)
    state = ledger.read()
    assert len(state.models) == 1000
    assert state.models[capture.identity].state == "retained"
    assert state.last_error == "selection ledger at capacity; new intake paused"
    verify_store(ledger.path / "store", capture.identity, capture.trust)


def test_pause_interrupts_slow_incomplete_headers_before_handler_join(
    tmp_path, capture
):
    import socket

    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    ready = queue.Queue()
    result = []
    thread = threading.Thread(
        target=lambda: result.append(
            run_mirror(ledger, public=True, seconds=120, ready=ready.put)
        )
    )
    thread.start()
    port = ready.get(timeout=10)
    client = socket.create_connection(("127.0.0.1", port), timeout=2)
    try:
        client.sendall(b"GET / HTTP/1.1\r\nHost: slow\r\nX-Incomplete: ")
        time.sleep(0.05)
        ledger.update(lambda s: s.model_copy(update={"paused": True}))
        thread.join(2)
        assert not thread.is_alive() and result[0]["stopped"]
        assert not ledger.running()
    finally:
        client.close()
        if thread.is_alive():
            thread.join(35)
