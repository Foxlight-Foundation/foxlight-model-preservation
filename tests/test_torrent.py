"""Actual local peers, signed verification, explicit discovery and bounded stop."""

import socket
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

import pytest
from conftest import Capture

from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationCancelled, OperationControl
from foxlight_model_preservation.signing import digest
from foxlight_model_preservation.storage import pack, verify_store
from foxlight_model_preservation.torrent import (
    TorrentExport,
    TorrentSeeder,
    TorrentSeedPolicy,
    export_torrent,
)
from foxlight_model_preservation.torrent_binding import torrent_engine

pytest.importorskip("libtorrent", reason="optional native BitTorrent extra")


class QuietMirror(SimpleHTTPRequestHandler):
    """A test-only static mirror with no request-history output."""

    def log_message(self, format: str, *args: object) -> None:
        """Do not retain local test request history."""


def test_real_peer_receives_signed_public_files_then_listener_stops(
    capture: Capture, tmp_path: Path
) -> None:
    exported = tmp_path / "export"
    result = export_torrent(capture.store, capture.identity, capture.trust, exported)
    assert not result.public_peer_participation
    engine = torrent_engine()
    received = tmp_path / "received"
    received.mkdir()
    receiver = engine.session(
        {
            "listen_interfaces": "127.0.0.1:0",
            "enable_dht": False,
            "enable_lsd": False,
            "enable_upnp": False,
            "enable_natpmp": False,
            "enable_incoming_utp": False,
            "enable_outgoing_utp": False,
            "alert_mask": 0,
        }
    )
    metadata = (exported / (capture.identity + ".torrent")).read_bytes()
    handle = receiver.add_torrent(
        {
            "ti": engine.torrent_info(engine.bdecode(metadata)),
            "save_path": str(received),
            "flags": 0,
        }
    )
    seeder = TorrentSeeder(
        exported, result, capture.trust, TorrentSeedPolicy(max_seconds=30)
    )
    port = seeder.port
    try:
        deadline = time.monotonic() + 20
        while not seeder.observe().ready and time.monotonic() < deadline:
            time.sleep(0.05)
        assert seeder.observe().ready
        handle.connect_peer(("127.0.0.1", port))
        # Native piece completion can precede asynchronous filesystem writes.
        # The receiver is complete only when our independent byte verifier passes.
        restored = None
        while time.monotonic() < deadline:
            if handle.status().is_seeding:
                try:
                    restored = verify_store(
                        received / capture.identity, capture.identity, capture.trust
                    )
                except (FileNotFoundError, PreservationError):
                    pass
                else:
                    break
            time.sleep(0.05)
        assert restored is not None, (
            "peer did not publish a complete verified archive within the deadline"
        )
        assert (
            restored.total_bytes
            == verify_store(capture.store, capture.identity, capture.trust).total_bytes
        )
        assert seeder.observe().uploaded_payload_bytes > 0
    finally:
        seeder.close()
        receiver.remove_torrent(handle)
        receiver.pause()
        del handle
        del receiver
    assert seeder.observe().stopped
    assert seeder.port == 0
    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex(("127.0.0.1", port)) != 0
    assert verify_store(exported / capture.identity, capture.identity, capture.trust)


def test_duration_is_enforced_without_status_polling(
    capture: Capture, tmp_path: Path
) -> None:
    exported = tmp_path / "export"
    result = export_torrent(capture.store, capture.identity, capture.trust, exported)
    seeder = TorrentSeeder(
        exported, result, capture.trust, TorrentSeedPolicy(max_seconds=1)
    )
    try:
        deadline = time.monotonic() + 5
        while seeder.port and time.monotonic() < deadline:
            time.sleep(0.05)
        assert seeder.port == 0
        assert seeder.observe().reason == "duration"
    finally:
        seeder.close()
    assert seeder.observe().reason == "duration"


def test_cancel_during_verification_never_opens_a_session(
    capture: Capture, tmp_path: Path
) -> None:
    from foxlight_model_preservation.operations import Progress

    exported = tmp_path / "export"
    result = export_torrent(capture.store, capture.identity, capture.trust, exported)

    def cancel_check(progress: Progress) -> None:
        if progress.stage == "checking":
            control.cancel()

    control = OperationControl(cancel_check)
    with pytest.raises(OperationCancelled):
        TorrentSeeder(exported, result, capture.trust, control=control)


def test_cancel_stops_owned_session_without_status_polling(
    capture: Capture, tmp_path: Path
) -> None:
    exported = tmp_path / "export"
    result = export_torrent(capture.store, capture.identity, capture.trust, exported)
    control = OperationControl()
    seeder = TorrentSeeder(exported, result, capture.trust, control=control)
    try:
        control.cancel()
        deadline = time.monotonic() + 5
        while seeder.port and time.monotonic() < deadline:
            time.sleep(0.05)
        assert seeder.port == 0
        assert seeder.observe().stopped
    finally:
        seeder.close()


def test_standard_client_receives_from_static_web_seed_alone(
    capture: Capture, tmp_path: Path
) -> None:
    exported = tmp_path / "export"
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(QuietMirror, directory=str(exported))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    receiver = None
    handle = None
    try:
        base = f"http://127.0.0.1:{server.server_port}/"
        export_torrent(
            capture.store,
            capture.identity,
            capture.trust,
            exported,
            web_seeds=(base,),
            allow_local_http=True,
        )
        engine = torrent_engine()
        receiver = engine.session(
            {
                "listen_interfaces": "127.0.0.1:0",
                "enable_dht": False,
                "enable_lsd": False,
                "enable_upnp": False,
                "enable_natpmp": False,
                # Only the local test client may access a loopback HTTP web seed.
                "ssrf_mitigation": False,
                "alert_mask": 0,
            }
        )
        metadata = (exported / (capture.identity + ".torrent")).read_bytes()
        received = tmp_path / "received"
        received.mkdir()
        handle = receiver.add_torrent(
            {
                "ti": engine.torrent_info(engine.bdecode(metadata)),
                "save_path": str(received),
                "flags": 0,
            }
        )
        deadline = time.monotonic() + 20
        while not handle.status().is_seeding and time.monotonic() < deadline:
            time.sleep(0.05)
        assert handle.status().is_seeding
        assert verify_store(
            received / capture.identity, capture.identity, capture.trust
        )
    finally:
        if receiver is not None:
            if handle is not None:
                receiver.remove_torrent(handle)
            receiver.pause()
        del handle
        del receiver
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_export_never_accepts_private_intake(capture: Capture, tmp_path: Path) -> None:
    private_recipe = capture.recipe.model_copy(
        update={
            "members": tuple(
                member.model_copy(
                    update={
                        "intake": member.intake.model_copy(
                            update={"audience": "private"}
                        )
                    }
                )
                for member in capture.recipe.members
            )
        }
    )
    private = tmp_path / "private"
    identity = pack(private_recipe, capture.sources, private, (capture.key,))
    with pytest.raises(PreservationError, match="public intake"):
        export_torrent(private, identity, capture.trust, tmp_path / "export")
    assert not (tmp_path / "export").exists()


def test_public_listener_requires_an_explicit_public_swarm_choice(
    capture: Capture, tmp_path: Path
) -> None:
    exported = tmp_path / "export"
    result = export_torrent(capture.store, capture.identity, capture.trust, exported)
    with pytest.raises(PreservationError, match="explicit public participation"):
        TorrentSeeder(
            exported, result, capture.trust, TorrentSeedPolicy(listen_host="192.0.2.1")
        )


def test_changed_metainfo_and_forged_file_selection_are_refused(
    capture: Capture, tmp_path: Path
) -> None:
    exported = tmp_path / "export"
    result = export_torrent(capture.store, capture.identity, capture.trust, exported)
    path = exported / (capture.identity + ".torrent")
    engine = torrent_engine()
    data = engine.bdecode(path.read_bytes())
    info = cast(dict[bytes, object], data[b"info"])
    info[b"name"] = b"another-directory"
    changed = engine.bencode(data)
    path.write_bytes(changed)
    with pytest.raises(PreservationError, match="differs"):
        TorrentSeeder(exported, result, capture.trust)
    forged = result.model_copy(update={"metainfo_sha256": digest(changed)})
    with pytest.raises(PreservationError, match="signed public selection"):
        TorrentSeeder(exported, forged, capture.trust)


def test_export_is_repeatable_and_does_not_copy_local_receipts(
    capture: Capture, tmp_path: Path
) -> None:
    (capture.store / "private-file").write_text("private")
    receipts = []
    for index in range(2):
        destination = tmp_path / f"export-{index}"
        result = export_torrent(
            capture.store, capture.identity, capture.trust, destination
        )
        assert not (destination / capture.identity / "private-file").exists()
        assert (
            TorrentExport.model_validate_json(
                (destination / "torrent.json").read_bytes()
            )
            == result
        )
        receipts.append(result)
    assert receipts[0] == receipts[1]
    with pytest.raises(PreservationError, match="new destination"):
        export_torrent(
            capture.store, capture.identity, capture.trust, tmp_path / "export-0"
        )


def test_envelope_changed_after_authentication_cannot_publish_an_export(
    capture: Capture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import foxlight_model_preservation.torrent as torrent

    original = torrent.read_metadata

    def replace_envelope(root: int, path: str) -> bytes:
        (capture.store / path).write_bytes(b"{}")
        return original(root, path)

    monkeypatch.setattr(torrent, "read_metadata", replace_envelope)
    with pytest.raises(ValueError):
        export_torrent(
            capture.store, capture.identity, capture.trust, tmp_path / "export"
        )
    assert not (tmp_path / "export").exists()


def test_web_seed_credentials_and_non_https_default_are_refused(
    capture: Capture, tmp_path: Path
) -> None:
    for value in (
        "http://mirror.example/",
        "https://a:b@mirror.example/",
        "https://mirror.example/?token=secret",
    ):
        with pytest.raises(PreservationError, match="credential-free HTTPS"):
            export_torrent(
                capture.store,
                capture.identity,
                capture.trust,
                tmp_path / "export",
                web_seeds=(value,),
            )
    control = OperationControl()
    control.cancel()
    with pytest.raises(OperationCancelled):
        export_torrent(
            capture.store,
            capture.identity,
            capture.trust,
            tmp_path / "export",
            control=control,
        )


def test_torrent_cli_exports_then_stops_its_owned_listener(
    capture: Capture, tmp_path: Path
) -> None:
    import json

    from test_cli_catalog import command

    from foxlight_model_preservation.signing import encode_record

    trust_file = tmp_path / "trust.json"
    trust_file.write_bytes(encode_record(capture.trust))
    exported = tmp_path / "export"
    built = command(
        "torrent-export",
        capture.identity,
        "--store",
        str(capture.store),
        "--trust",
        str(trust_file),
        "--output",
        str(exported),
    )
    assert built.returncode == 0, built.stderr
    assert not json.loads(built.stdout)["public_peer_participation"]
    seeded = command(
        "torrent-seed",
        capture.identity,
        "--store",
        str(exported),
        "--trust",
        str(trust_file),
        "--duration",
        "1",
        "--progress",
    )
    assert seeded.returncode == 0, seeded.stderr
    lines = [json.loads(line) for line in seeded.stdout.splitlines()]
    assert not lines[0]["public_peer_participation"]
    assert not lines[0]["payload_threshold_is_hard_cap"]
    assert lines[-1]["stopped"]
    assert lines[-1]["reason"] == "duration"
    progress = [json.loads(line) for line in seeded.stderr.splitlines()]
    assert all(event["operation"] == "seed" for event in progress)
    assert progress[-1]["stage"] == "complete"
    assert sum(event["stage"] == "complete" for event in progress) == 1
    assert any(event["stage"] == "sharing" for event in progress)

    # A refusal after hashing must not have emitted successful job completion.
    metadata = exported / (capture.identity + ".torrent")
    metadata.write_bytes(metadata.read_bytes() + b"changed")
    refused = command(
        "torrent-seed",
        capture.identity,
        "--store",
        str(exported),
        "--trust",
        str(trust_file),
        "--duration",
        "1",
        "--progress",
    )
    assert refused.returncode == 1
    events = [json.loads(line) for line in refused.stderr.splitlines()]
    observed = [event for event in events if "stage" in event]
    assert observed[-1]["stage"] == "failed"
    assert all(event["stage"] != "complete" for event in observed)
