"""Persistent native sessions retain operator custody across stop and publisher loss."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_mirror import cycle, mirror

from foxlight_model_preservation.handoffs import read_handoff
from foxlight_model_preservation.mirror import configure_http, model_action, run_mirror
from foxlight_model_preservation.mirror_native import (
    NativeMirrors,
    configure,
    export_reference,
)
from foxlight_model_preservation.mirror_prune import prune
from foxlight_model_preservation.mirror_state import (
    MirrorLedger,
    encode_state,
    grant_restart,
    policy_id,
    restart_allowed,
)
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.peer_invitations import (
    PeerInvitation,
    fetch_peer_invitation,
)
from foxlight_model_preservation.signing import public_record
from foxlight_model_preservation.storage import manifest_path, pack, verify_store
from foxlight_model_preservation.torrent_binding import torrent_engine


def ipfs_home(ledger, manifest):
    return (
        custody_home(ledger, manifest) / ledger.read().native_models[manifest].ipfs_repo
    )


def custody_home(ledger, manifest):
    record = ledger.read().native_models[manifest]
    return ledger.path / "scratch/native" / (record.custody_name or manifest)


def await_condition(check, timeout=25):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError("native condition deadline")


def test_invalid_configuration_can_be_corrected_without_custody_cleanup(
    tmp_path, capture
):
    ledger, _ = mirror(tmp_path, capture)
    with pytest.raises(ValueError):
        configure(ledger, max_active=0)
    assert not (ledger.path / "scratch/native").exists()
    assert ledger.read().native_policy is None
    configure(ledger, max_active=1)
    assert ledger.read().native_policy.max_active == 1


@pytest.mark.parametrize("foreign_contents", [False, True])
def test_interrupted_custody_creation_retries_and_only_prunes_empty_intents(
    tmp_path, capture, monkeypatch, foreign_contents
):
    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    monkeypatch.setattr(
        "foxlight_model_preservation.mirror_native.torrent_engine", lambda: None
    )
    started = threading.Event()
    monkeypatch.setattr(
        "foxlight_model_preservation.mirror_native._torrent",
        lambda *args: started.set(),
    )
    configure(ledger, torrent=True)
    native_identity = ledger.read().native_policy.identity
    original = os.fsync
    interrupted = False

    def failed_sync(fd):
        nonlocal interrupted
        info = os.fstat(fd)
        if not interrupted and (info.st_dev, info.st_ino) == native_identity:
            interrupted = True
            raise OSError("interrupted custody directory sync")
        return original(fd)

    monkeypatch.setattr(os, "fsync", failed_sync)
    manager = NativeMirrors(ledger, lambda s: True)
    try:
        manager.tick()
        await_condition(
            lambda: (
                bool(ledger.read().native_models.get(capture.identity))
                and bool(ledger.read().native_models[capture.identity].error)
            )
        )
        manager.close()
        failed = ledger.read().native_models[capture.identity]
        assert interrupted and failed.identity is None and not started.is_set()
        assert len(failed.pending_custody) == 1
        pending = ledger.path / "scratch/native" / failed.pending_custody[0]
        assert pending.is_dir() and not list(pending.iterdir())
        if foreign_contents:
            (pending / "operator-file").write_bytes(b"preserve unknown contents")
        model_action(ledger, capture.identity, "retry")
        manager = NativeMirrors(ledger, lambda s: True)
        manager.tick()
        await_condition(started.is_set)
        manager.close()
        active = custody_home(ledger, capture.identity)
        assert active != pending and pending.is_dir()
        assert not ledger.read().native_models[capture.identity].error
        model_action(ledger, capture.identity, "block")
        if foreign_contents:
            with pytest.raises(PreservationError, match="custody is not empty"):
                prune(ledger, capture.identity, capture.identity)
            assert (
                pending / "operator-file"
            ).read_bytes() == b"preserve unknown contents"
            assert active.is_dir()
            assert verify_store(ledger.path / "store", capture.identity, capture.trust)
        else:
            prune(ledger, capture.identity, capture.identity)
            assert not pending.exists() and not active.exists()
    finally:
        manager.close()


def test_one_slot_alternates_selected_transports_across_restart(
    tmp_path, capture, monkeypatch
):
    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    monkeypatch.setattr(
        "foxlight_model_preservation.mirror_native.torrent_engine", lambda: None
    )
    monkeypatch.setattr(
        "foxlight_model_preservation.mirror_native._binary_hash", lambda _: "a" * 64
    )
    monkeypatch.setattr(
        "foxlight_model_preservation.mirror_native.executable", lambda value: value
    )
    started = []
    monkeypatch.setattr(
        "foxlight_model_preservation.mirror_native._ipfs",
        lambda *args: started.append("ipfs"),
    )
    monkeypatch.setattr(
        "foxlight_model_preservation.mirror_native._torrent",
        lambda *args: started.append("torrent"),
    )
    configure(
        ledger,
        ipfs_binary=sys.executable,
        ipfs_sha256="a" * 64,
        torrent=True,
        max_active=1,
        session_bytes=100,
    )
    for _ in range(3):
        manager = NativeMirrors(MirrorLedger(ledger.path), lambda s: True)
        try:
            target = len(started) + 2

            def next_sessions(owner=manager, count=target):
                owner.tick()
                return len(started) >= count

            await_condition(next_sessions)
        finally:
            manager.close()
    assert set(started[:2]) == {"ipfs", "torrent"}
    counts = ledger.read().native_models[capture.identity].session_counts
    assert counts["ipfs"] >= 3 and counts["torrent"] >= 3
    assert abs(counts["ipfs"] - counts["torrent"]) <= 1


@pytest.mark.parametrize("mutation", ["file", "directory", "replacement"])
def test_inventory_handles_stage_retirement_and_refuses_replacement(
    tmp_path, capture, monkeypatch, mutation
):
    ledger, _ = mirror(tmp_path, capture)
    area = ledger.path / "store/.peer-export"
    area.mkdir(mode=0o700)
    stage = area / "stage-gone"
    stage.mkdir(mode=0o700)
    (stage / "payload").write_bytes(b"retained test bytes")
    original_stat, original_open = os.stat, os.open
    changed = False

    def stat_entry(name, *args, **kwargs):
        nonlocal changed
        if (
            mutation == "file"
            and name == "payload"
            and kwargs.get("dir_fd") is not None
            and not changed
        ):
            changed = True
            os.unlink(name, dir_fd=kwargs["dir_fd"])
        return original_stat(name, *args, **kwargs)

    def open_entry(name, *args, **kwargs):
        nonlocal changed
        if (
            mutation != "file"
            and name == "stage-gone"
            and kwargs.get("dir_fd") is not None
            and not changed
        ):
            changed = True
            if mutation == "directory":
                shutil.rmtree(name, dir_fd=kwargs["dir_fd"])
            else:
                os.rename(
                    name,
                    "kept-stage",
                    src_dir_fd=kwargs["dir_fd"],
                    dst_dir_fd=kwargs["dir_fd"],
                )
                os.mkdir(name, 0o700, dir_fd=kwargs["dir_fd"])
        return original_open(name, *args, **kwargs)

    monkeypatch.setattr(os, "stat", stat_entry)
    monkeypatch.setattr(os, "open", open_entry)
    if mutation == "replacement":
        with pytest.raises(PreservationError, match="directory changed"):
            ledger.usage()
        assert (area / "kept-stage/payload").read_bytes() == b"retained test bytes"
    else:
        assert ledger.usage() >= 0
    assert changed


def test_configure_is_offline_invalidates_resume_and_preserves_legacy_policy(
    tmp_path, capture, monkeypatch
):
    ledger, _ = mirror(tmp_path, capture)
    original = policy_id(ledger.read())
    grant_restart(ledger, int(time.time()) + 600)
    assert restart_allowed(MirrorLedger(ledger.path).read())
    raw = json.loads(encode_state(ledger.read()))
    assert "native_policy" not in raw and "native_models" not in raw
    assert policy_id(MirrorLedger(ledger.path).read()) == original

    def forbidden(*args, **kwargs):
        raise AssertionError("offline configuration connected")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    configure(ledger)  # Explicitly select no native transports.
    assert ledger.read().schema_version == "foxlight.mirror-state.v2"
    assert ledger.read().restart is None and not ledger.read().native_models
    with pytest.raises(PreservationError, match="restart"):
        run_mirror(ledger, resume=True)


def test_native_budget_is_spent_before_start_and_block_retains_custody(
    tmp_path, capture, monkeypatch
):
    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    monkeypatch.setattr(
        "foxlight_model_preservation.mirror_native.torrent_engine", lambda: None
    )
    started = []

    def transport(ledger, manifest, policy, control):
        assert ledger.read().egress_month_used >= policy.session_bytes
        started.append(manifest)
        while True:
            control.checkpoint()
            time.sleep(0.01)

    monkeypatch.setattr("foxlight_model_preservation.mirror_native._torrent", transport)
    configure(ledger, torrent=True, session_bytes=100)
    ledger.update(
        lambda s: s.model_copy(update={"egress_daily": 100, "egress_monthly": 100})
    )
    manager = NativeMirrors(ledger, lambda s: not s.paused and not s.left)
    try:
        manager.tick()
        await_condition(lambda: bool(started))
        model_action(ledger, capture.identity, "pin")
        model_action(ledger, capture.identity, "block")
        manager.tick()
    finally:
        manager.close()
    assert ledger.read().native_models[capture.identity].last_stopped
    assert verify_store(ledger.path / "store", capture.identity, capture.trust)
    assert custody_home(ledger, capture.identity).is_dir()
    model_action(ledger, capture.identity, "unblock")
    again = NativeMirrors(MirrorLedger(ledger.path), lambda s: True)
    try:
        again.tick()
        assert not again.sessions
    finally:
        again.close()
    assert len(started) == 1 and ledger.read().egress_month_used == 100


@pytest.mark.parametrize("intake", ["source", "local"])
def test_torrent_mirror_receives_stops_and_prunes_only_on_operator_request(
    tmp_path, capture, intake
):
    pytest.importorskip("libtorrent")
    if intake == "local":
        from test_mirror_intake import local_grant, local_mirror

        from foxlight_model_preservation.mirror_intake import retain_local

        ledger = local_mirror(tmp_path)
        retain_local(ledger, capture.store, capture.identity, local_grant(capture))
        shutil.rmtree(capture.store)
    else:
        ledger, _ = mirror(tmp_path, capture)
        cycle(ledger)
    configure(ledger, torrent=True, session_seconds=60)
    manager = NativeMirrors(ledger, lambda s: True)
    engine = torrent_engine()
    receiver = None
    handle = None
    try:
        manager.tick()
        await_condition(
            lambda: bool(
                ledger.read().native_models.get(capture.identity)
                and ledger.read().native_models[capture.identity].last_torrent_port
            )
        )
        record = ledger.read().native_models[capture.identity]
        assert not record.error
        metainfo = export_reference(ledger, capture.identity, "torrent")
        received = tmp_path / "received"
        received.mkdir()
        receiver = engine.session(
            {
                "listen_interfaces": "127.0.0.1:0",
                "enable_dht": False,
                "enable_lsd": False,
                "enable_upnp": False,
                "enable_natpmp": False,
                "alert_mask": 0,
            }
        )
        handle = receiver.add_torrent(
            {
                "ti": engine.torrent_info(engine.bdecode(metainfo)),
                "save_path": str(received),
                "flags": 0,
            }
        )
        handle.connect_peer(("127.0.0.1", record.last_torrent_port))

        selected_handle = handle

        def complete():
            if not selected_handle.status().is_seeding:
                return False
            try:
                return bool(
                    verify_store(
                        received / capture.identity, capture.identity, capture.trust
                    )
                )
            except (ValueError, OSError):
                return False

        await_condition(complete)
        assert (
            verify_store(received / capture.identity, capture.identity, capture.trust)
            .members[0]
            .retained_card
            == capture.recipe.members[0].retained_card
        )
        model_action(ledger, capture.identity, "block")
        manager.tick()
        manager.close()
        assert export_reference(ledger, capture.identity, "torrent") == metainfo
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", record.last_torrent_port)) != 0
        model_action(ledger, capture.identity, "pin")
        with pytest.raises(PreservationError):
            prune(ledger, capture.identity, capture.identity)
        model_action(ledger, capture.identity, "unpin")
        prune(ledger, capture.identity, capture.identity)
        assert not any(
            p.name.startswith(capture.identity)
            for p in (ledger.path / "scratch/native").iterdir()
        )
        assert verify_store(
            received / capture.identity, capture.identity, capture.trust
        )
    finally:
        manager.close()
        if receiver:
            if handle:
                receiver.remove_torrent(handle)
            receiver.pause()
        del handle
        del receiver


def native_binary():
    selected = os.environ.get("KUBO_BINARY")
    if not selected:
        pytest.skip("set KUBO_BINARY for actual isolated Kubo acceptance")
    return selected


@pytest.mark.parametrize("transport", ["torrent", "ipfs"])
def test_native_only_controller_receives_without_http_and_retains_after_stop(
    tmp_path, capture, monkeypatch, transport
):
    from test_mirror_intake import local_grant, local_mirror

    from foxlight_model_preservation.mirror_intake import retain_local

    binary = native_binary() if transport == "ipfs" else None
    if transport == "torrent":
        pytest.importorskip("libtorrent")
    else:
        pytest.importorskip("multiformats")
    ledger = local_mirror(tmp_path)
    original = (capture.store / manifest_path(capture.identity)).read_bytes()
    retain_local(ledger, capture.store, capture.identity, local_grant(capture))
    configure_http(ledger, enabled=False)
    configure(
        ledger,
        torrent=transport == "torrent",
        ipfs_binary=binary,
        ipfs_sha256=hashlib.sha256(Path(binary).read_bytes()).hexdigest()
        if binary
        else None,
        allow_private_peers=transport == "ipfs",
        session_seconds=120,
    )
    shutil.rmtree(capture.store)
    prepared = threading.Event()
    failures = []

    def forbidden(*args, **kwargs):
        raise AssertionError("native-only controller constructed HTTP")

    monkeypatch.setattr("foxlight_model_preservation.mirror.MirrorServer", forbidden)

    def run():
        try:
            run_mirror(
                ledger,
                public=True,
                seconds=120,
                ready=forbidden,
                prepared=lambda _port: prepared.set(),
            )
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    receiver = handle = None
    received = tmp_path / "native-only-recipient"
    try:
        assert prepared.wait(10)
        if transport == "torrent":
            engine = torrent_engine()
            await_condition(
                lambda: bool(
                    ledger.read().native_models.get(capture.identity)
                    and ledger.read().native_models[capture.identity].last_torrent_port
                )
            )
            record = ledger.read().native_models[capture.identity]
            metadata = export_reference(ledger, capture.identity, "torrent")
            received.mkdir()
            receiver = engine.session(
                {
                    "listen_interfaces": "127.0.0.1:0",
                    "enable_dht": False,
                    "enable_lsd": False,
                    "enable_upnp": False,
                    "enable_natpmp": False,
                    "alert_mask": 0,
                }
            )
            handle = receiver.add_torrent(
                {
                    "ti": engine.torrent_info(engine.bdecode(metadata)),
                    "save_path": str(received),
                    "flags": 0,
                }
            )
            handle.connect_peer(("127.0.0.1", record.last_torrent_port))
            selected_handle = handle
            await_condition(lambda: selected_handle.status().is_seeding)
            archive = received / capture.identity
        else:
            from test_peer_native import node

            await_condition(
                lambda: bool(
                    ledger.read().native_models.get(capture.identity)
                    and ledger.read().native_models[capture.identity].cid
                    and ledger.read().native_models[capture.identity].contacts
                )
            )
            record = ledger.read().native_models[capture.identity]
            handoff = read_handoff(export_reference(ledger, capture.identity, "peer"))
            with node(
                tmp_path, "native-only-receiver", binary, bootstrap=record.contacts
            ) as (_, client, _):
                assert isinstance(handoff.reference, PeerInvitation)
                fetch_peer_invitation(
                    handoff.reference,
                    capture.trust,
                    received,
                    client,
                    maximum_bytes=10**6,
                )
            archive = received
        assert verify_store(archive, capture.identity, capture.trust)
        assert (archive / manifest_path(capture.identity)).read_bytes() == original
        ledger.update(lambda state: state.model_copy(update={"paused": True}))
        thread.join(20)
        assert not thread.is_alive() and not failures and not ledger.running()
        assert verify_store(ledger.path / "store", capture.identity, capture.trust)
        assert (archive / manifest_path(capture.identity)).read_bytes() == original
    finally:
        ledger.update(lambda state: state.model_copy(update={"paused": True}))
        thread.join(30)
        if receiver is not None:
            if handle is not None:
                receiver.remove_torrent(handle)
            receiver.pause()
        del handle
        del receiver


def test_torrent_export_receipt_interruption_retries_without_deleting_copy(
    tmp_path, capture, monkeypatch
):
    pytest.importorskip("libtorrent")
    from foxlight_model_preservation import mirror_native as native

    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    configure(ledger, torrent=True, session_seconds=60)
    original = native.export_torrent
    exports = []

    def interrupted(store, manifest, trust, destination, **kwargs):
        result = original(store, manifest, trust, destination, **kwargs)
        exports.append(destination)
        if len(exports) == 1:
            raise OSError("export committed before receipt interruption")
        return result

    monkeypatch.setattr(native, "export_torrent", interrupted)
    manager = NativeMirrors(ledger, lambda s: True)
    try:
        manager.tick()
        await_condition(
            lambda: (
                bool(ledger.read().native_models.get(capture.identity))
                and bool(ledger.read().native_models[capture.identity].error)
            )
        )
        manager.close()
        assert ledger.read().native_models[capture.identity].torrent is None
        first_metainfo = (exports[0] / (capture.identity + ".torrent")).read_bytes()
        model_action(ledger, capture.identity, "retry")
        manager = NativeMirrors(ledger, lambda s: True)
        manager.tick()
        await_condition(
            lambda: bool(
                ledger.read().native_models[capture.identity].last_torrent_port
            )
        )
        record = ledger.read().native_models[capture.identity]
        assert not record.error and record.torrent
        assert exports[0] != exports[1]
        assert record.torrent_directory == exports[1].name
        assert export_reference(ledger, capture.identity, "torrent") == first_metainfo
        assert (exports[0] / (capture.identity + ".torrent")).read_bytes() == (
            first_metainfo
        )
        assert verify_store(
            exports[0] / capture.identity, capture.identity, capture.trust
        )
    finally:
        manager.close()


def test_interrupted_ipfs_init_retries_new_generation_and_retains_partial(
    tmp_path, capture, monkeypatch
):
    from foxlight_model_preservation import mirror_native as native

    binary = native_binary()
    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    configure(
        ledger,
        ipfs_binary=binary,
        ipfs_sha256=hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
        allow_private_peers=True,
        session_seconds=60,
    )
    original = native.initialize_peer
    attempts = []

    def interrupted(repo, *args, **kwargs):
        attempts.append(repo)
        if len(attempts) == 1:
            repo.mkdir(mode=0o700)
            (repo / "partial").write_bytes(b"unfinished initialization")
            raise OSError("interrupted initialization")
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(native, "initialize_peer", interrupted)
    manager = NativeMirrors(ledger, lambda s: True)
    try:
        manager.tick()
        await_condition(
            lambda: (
                bool(ledger.read().native_models.get(capture.identity))
                and bool(ledger.read().native_models[capture.identity].error)
            )
        )
        manager.close()
        assert ledger.read().native_models[capture.identity].ipfs_identity is None
        model_action(ledger, capture.identity, "retry")
        manager = NativeMirrors(ledger, lambda s: True)
        manager.tick()
        await_condition(lambda: bool(ledger.read().native_models[capture.identity].cid))
        record = ledger.read().native_models[capture.identity]
        assert not record.error and record.ipfs_identity
        assert attempts[0] != attempts[1] == ipfs_home(ledger, capture.identity)
        assert (attempts[0] / "partial").read_bytes() == b"unfinished initialization"
        assert verify_store(ledger.path / "store", capture.identity, capture.trust)
    finally:
        manager.close()


def test_queued_ipfs_session_starts_daemon_only_after_publication_capacity(
    tmp_path, capture, monkeypatch
):
    from foxlight_model_preservation import mirror_native as native

    binary = native_binary()
    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    configure(
        ledger,
        ipfs_binary=binary,
        ipfs_sha256=hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
        allow_private_peers=True,
        session_seconds=60,
    )
    started = threading.Event()
    original = native.run_peer

    def observed(*args, **kwargs):
        started.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(native, "run_peer", observed)
    manager = NativeMirrors(ledger, lambda s: True)
    native._publication_lock.acquire()
    try:
        manager.tick()
        await_condition(
            lambda: (
                bool(ledger.read().native_models.get(capture.identity))
                and bool(ledger.read().native_models[capture.identity].ipfs_identity)
            )
        )
        assert not started.is_set()
        assert not ledger.read().native_models[capture.identity].error
    finally:
        native._publication_lock.release()
    try:
        await_condition(lambda: bool(ledger.read().native_models[capture.identity].cid))
        assert started.is_set()
        assert not ledger.read().native_models[capture.identity].error
    finally:
        manager.close()


def test_native_verification_serializes_http_link_publication(
    tmp_path, capture, monkeypatch
):
    from foxlight_model_preservation import mirror as http

    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    monkeypatch.setattr(
        "foxlight_model_preservation.mirror_native.torrent_engine", lambda: None
    )
    monkeypatch.setattr(
        "foxlight_model_preservation.mirror_native._torrent", lambda *args: None
    )
    configure(ledger, torrent=True)
    hashing, release_hash, publishing, published = (threading.Event() for _ in range(4))
    original = http.verify_object
    failures = []

    def held_hash(*args, **kwargs):
        if threading.current_thread().name == "mirror-native-session":
            hashing.set()
            if not release_hash.wait(10):
                raise AssertionError("HTTP/native verification barrier deadline")
        return original(*args, **kwargs)

    def publish():
        from foxlight_model_preservation.operations import OperationControl

        publishing.set()
        try:
            http.public_selection(ledger, OperationControl())
        except Exception as error:
            failures.append(error)
        finally:
            published.set()

    monkeypatch.setattr(http, "verify_object", held_hash)
    manager = NativeMirrors(ledger, lambda s: True)
    worker = threading.Thread(target=publish)
    try:
        manager.tick()
        assert hashing.wait(10)
        worker.start()
        assert publishing.wait(10)
        assert not published.wait(0.05)
        release_hash.set()
        worker.join(10)
        assert published.is_set() and not failures
    finally:
        release_hash.set()
        if worker.ident is not None:
            worker.join()
        manager.close()
    assert not ledger.read().native_models[capture.identity].error
    assert not ledger.read().models[capture.identity].error


@pytest.mark.parametrize("intake", ["source", "local"])
def test_ipfs_mirror_restart_after_publisher_loss_and_independent_receive(
    tmp_path, capture, intake
):
    pytest.importorskip("multiformats")
    from test_peer_native import node

    binary = native_binary()
    if intake == "local":
        from test_mirror_intake import local_grant, local_mirror

        from foxlight_model_preservation.mirror_intake import retain_local

        ledger = local_mirror(tmp_path)
        retain_local(ledger, capture.store, capture.identity, local_grant(capture))
        publisher = None
    else:
        ledger, publisher = mirror(tmp_path, capture)
        cycle(ledger)
    configure(
        ledger,
        ipfs_binary=binary,
        ipfs_sha256=hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
        allow_private_peers=True,
        session_seconds=60,
    )
    manager = NativeMirrors(ledger, lambda s: True)
    try:
        manager.tick()
        await_condition(
            lambda: bool(
                ledger.read().native_models.get(capture.identity)
                and ledger.read().native_models[capture.identity].cid
            )
        )
        first = ledger.read().native_models[capture.identity]
        manager.close()
        if publisher is not None:
            shutil.rmtree(publisher.path)
        shutil.rmtree(capture.store)
        manager = NativeMirrors(MirrorLedger(ledger.path), lambda s: True)
        manager.tick()

        def refreshed():
            r = ledger.read().native_models[capture.identity]
            return r.contacts and not r.error and r.publications > first.publications

        await_condition(refreshed)
        reference = read_handoff(export_reference(ledger, capture.identity, "peer"))
        assert reference.reference.manifest_id == capture.identity
        record = ledger.read().native_models[capture.identity]
        assert record.cid == first.cid
        with node(tmp_path, "receiver", binary, bootstrap=record.contacts) as (
            _,
            client,
            _,
        ):
            received = tmp_path / "received"
            assert isinstance(reference.reference, PeerInvitation)
            fetch_peer_invitation(
                reference.reference,
                capture.trust,
                received,
                client,
                maximum_bytes=10**6,
            )
            assert (received / manifest_path(capture.identity)).read_bytes() == (
                ledger.path / "store" / manifest_path(capture.identity)
            ).read_bytes()
        model_action(ledger, capture.identity, "block")
        manager.tick()
        manager.close()
        assert ipfs_home(ledger, capture.identity).is_dir()
    finally:
        manager.close()


def test_crashed_mirror_controller_drains_actual_native_child(tmp_path, capture):
    pytest.importorskip("multiformats")
    binary = native_binary()
    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    configure(
        ledger,
        ipfs_binary=binary,
        ipfs_sha256=hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
        allow_private_peers=True,
        session_seconds=60,
    )
    process = subprocess.Popen(  # noqa: S603 - own interpreter and fixed CLI, no shell
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
            "60",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        await_condition(
            lambda: bool(
                ledger.read().native_models.get(capture.identity)
                and ledger.read().native_models[capture.identity].cid
            )
        )
        api = ipfs_home(ledger, capture.identity) / "api"
        port = int(api.read_text().strip().rsplit("/", 1)[-1])
        before = ledger.read().egress_month_used
        process.kill()
        process.wait(timeout=5)

        def closed():
            with socket.socket() as probe:
                return probe.connect_ex(("127.0.0.1", port)) != 0

        await_condition(closed, 12)
        assert not ledger.running() and ledger.read().egress_month_used >= before
        with pytest.raises(PreservationError, match="restart"):
            run_mirror(ledger, resume=True)
        assert ipfs_home(ledger, capture.identity).is_dir()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=12)


def test_substituted_native_custody_refuses_prune_and_retains_selected_store(
    tmp_path, capture, monkeypatch
):
    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    monkeypatch.setattr(
        "foxlight_model_preservation.mirror_native.torrent_engine", lambda: None
    )

    def transport(ledger, manifest, policy, control):
        while True:
            control.checkpoint()
            time.sleep(0.01)

    monkeypatch.setattr("foxlight_model_preservation.mirror_native._torrent", transport)
    configure(ledger, torrent=True)
    manager = NativeMirrors(ledger, lambda s: True)
    try:
        manager.tick()
        await_condition(lambda: bool(ledger.read().native_models))
    finally:
        manager.close()
    original = custody_home(ledger, capture.identity)
    original.rename(original.with_name("retained-original"))
    original.mkdir(mode=0o700)
    (original / "operator-file").write_bytes(b"keep")
    model_action(ledger, capture.identity, "block")
    with pytest.raises(PreservationError, match="custody changed"):
        prune(ledger, capture.identity, capture.identity)
    assert (original / "operator-file").read_bytes() == b"keep"
    assert verify_store(ledger.path / "store", capture.identity, capture.trust)
    assert (ledger.path / "prune.json").exists()


def test_wrong_model_trust_never_starts_native_transport(
    tmp_path, capture, monkeypatch
):
    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    monkeypatch.setattr(
        "foxlight_model_preservation.mirror_native.torrent_engine", lambda: None
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("untrusted model served")

    monkeypatch.setattr("foxlight_model_preservation.mirror_native._torrent", forbidden)
    configure(ledger, torrent=True)
    held = ledger.read().models[capture.identity]
    bad = held.grant.trust.model_copy(
        update={"keys": (public_record(Ed25519PrivateKey.generate()),)}
    )
    ledger.update(
        lambda s: s.model_copy(
            update={
                "models": {
                    capture.identity: held.model_copy(
                        update={"grant": held.grant.model_copy(update={"trust": bad})}
                    )
                }
            }
        )
    )
    manager = NativeMirrors(ledger, lambda s: True)
    try:
        manager.tick()
        await_condition(
            lambda: bool(
                ledger.read().native_models.get(capture.identity)
                and ledger.read().native_models[capture.identity].error
            )
        )
        manager.tick()
        assert not manager.failures
    finally:
        manager.close()
    assert ledger.read().native_models[capture.identity].identity is None


def test_native_disk_refusal_keeps_http_owner_and_operator_pin(
    tmp_path, capture, monkeypatch
):
    from foxlight_model_preservation.mirror_state import HEADROOM

    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    monkeypatch.setattr(
        "foxlight_model_preservation.mirror_native.torrent_engine", lambda: None
    )
    configure(ledger, torrent=True)
    ledger.update(lambda s: s.model_copy(update={"disk_bytes": HEADROOM + 1}))
    model_action(ledger, capture.identity, "pin")
    result = run_mirror(ledger, public=True, seconds=1)
    assert result["stopped"] and result["retained"]
    record = ledger.read().native_models[capture.identity]
    assert record.identity is None and "disk admission" in record.error
    assert ledger.read().models[capture.identity].pinned
    assert ledger.read().models[capture.identity].state == "retained"
    assert verify_store(ledger.path / "store", capture.identity, capture.trust)
    model_action(ledger, capture.identity, "retry")
    assert ledger.read().native_models[capture.identity].error is None
    assert ledger.read().models[capture.identity].state == "retained"


def test_ipfs_model_isolation_allows_block_without_deleting_another_copy(
    tmp_path, capture
):
    pytest.importorskip("multiformats")
    from foxlight_model_preservation.peer_runtime import PeerClient

    binary = native_binary()
    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    other = pack(
        capture.recipe.model_copy(update={"name": "second"}),
        capture.sources,
        ledger.path / "store",
        (capture.key,),
    )
    held = ledger.read().models[capture.identity]
    ledger.update(
        lambda s: s.model_copy(update={"models": dict(s.models, **{other: held})})
    )
    configure(
        ledger,
        ipfs_binary=binary,
        ipfs_sha256=hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
        allow_private_peers=True,
        session_seconds=60,
        max_active=2,
    )
    manager = NativeMirrors(ledger, lambda s: True)
    try:
        manager.tick()
        await_condition(
            lambda: (
                len(ledger.read().native_models) == 2
                and all(r.cid for r in ledger.read().native_models.values())
            )
        )
        first = ipfs_home(ledger, capture.identity)
        first_port = int((first / "api").read_text().strip().rsplit("/", 1)[-1])
        model_action(ledger, capture.identity, "block")
        manager.tick()

        def first_closed():
            with socket.socket() as probe:
                return probe.connect_ex(("127.0.0.1", first_port)) != 0

        await_condition(first_closed, 12)
        second = ipfs_home(ledger, other)
        assert PeerClient(second, binary, allow_network=True).wire_usage() >= 0
        assert first.is_dir() and second.is_dir()
        assert verify_store(ledger.path / "store", other, capture.trust)
    finally:
        manager.close()
