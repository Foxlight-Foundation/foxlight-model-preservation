"""Seeding a Pirate Face torrent from stored objects, over isolated loopback."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path

import pytest
from test_mirror import cycle, mirror

from foxlight_model_preservation.mirror_native import NativeMirrors, configure
from foxlight_model_preservation.mirror_state import MirrorLedger
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationControl
from foxlight_model_preservation.pirateface_find import SwarmEntry, parse_torrent
from foxlight_model_preservation.pirateface_seed import (
    TORRENTS_DIRECTORY,
    PirateFaceSeeder,
    refresh,
    seeded,
)
from foxlight_model_preservation.storage import object_path
from foxlight_model_preservation.torrent_binding import torrent_engine

pytest.importorskip("libtorrent", reason="optional native BitTorrent extra")

SETTINGS = {
    "listen_interfaces": "127.0.0.1:0",
    "enable_dht": False,
    "enable_lsd": False,
    "enable_upnp": False,
    "enable_natpmp": False,
    "enable_incoming_utp": False,
    "enable_outgoing_utp": False,
    "alert_mask": 0,
}


def _torrent(source: Path, names: list[str]):
    engine = torrent_engine()
    files = engine.file_storage()
    for name in names:
        files.add_file(source.name + "/" + name, (source / name).stat().st_size)
    creator = engine.create_torrent(files, 16384, engine.create_torrent.v1_only)
    engine.set_piece_hashes(creator, str(source.parent), lambda _piece: None)
    data = engine.bencode(creator.generate())
    infohash = str(engine.torrent_info(engine.bdecode(data)).info_hashes().v1)
    return parse_torrent(data, infohash, "test/" + source.name)


def _store(tmp_path: Path, contents: dict[str, bytes]) -> tuple[Path, list[str]]:
    store = tmp_path / "store"
    mapping = []
    for data in contents.values():
        sha256 = hashlib.sha256(data).hexdigest()
        target = store / object_path(sha256)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o444)
        mapping.append(sha256)
    return store, mapping


def _repository(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    source = tmp_path / "source" / "weights"
    source.mkdir(parents=True)
    contents = {
        "config.json": b'{"model": "test"}',
        "model.safetensors": os.urandom(300_000),
    }
    for name, data in contents.items():
        (source / name).write_bytes(data)
    return source, contents


def _leech(swarm, port: int, destination: Path):
    engine = torrent_engine()
    session = engine.session(SETTINGS)
    handle = session.add_torrent(
        {
            "ti": engine.torrent_info(engine.bdecode(swarm.data)),
            "save_path": str(destination),
            "flags": 0,
        }
    )
    handle.connect_peer(("127.0.0.1", port))
    return session, handle


def test_stored_objects_are_seeded_without_a_copy_or_a_write(tmp_path) -> None:
    source, contents = _repository(tmp_path)
    swarm = _torrent(source, list(contents))
    store, mapping = _store(tmp_path, contents)
    home = tmp_path / "home"
    home.mkdir()
    before = {path: path.stat().st_mtime_ns for path in store.rglob("*")}
    seeder = PirateFaceSeeder(
        swarm,
        store,
        mapping,
        home,
        verified=False,
        upload_rate=10 * 1024**2,
        max_seconds=60,
        stop_after_bytes=sum(map(len, contents.values())),
        settings=SETTINGS,
    )
    checked = threading.Event()
    sent: list[int] = []
    control = OperationControl()
    runner = threading.Thread(
        target=lambda: sent.append(seeder.run(control, verified=checked.set))
    )
    runner.start()
    try:
        assert checked.wait(20)
        session, handle = _leech(swarm, seeder.port, tmp_path / "leech")
        deadline = time.monotonic() + 30
        while not handle.status().is_seeding and time.monotonic() < deadline:
            time.sleep(0.05)
        assert handle.status().is_seeding
        for name, data in contents.items():
            assert (tmp_path / "leech" / "weights" / name).read_bytes() == data
        runner.join(30)
        assert sent and sent[0] >= sum(map(len, contents.values()))
    finally:
        control.cancel()
        runner.join(10)
        seeder.close()
    assert list(home.iterdir()) == []
    assert {path: path.stat().st_mtime_ns for path in store.rglob("*")} == before


def test_a_copy_whose_pieces_differ_is_never_seeded(tmp_path) -> None:
    source, contents = _repository(tmp_path)
    swarm = _torrent(source, list(contents))
    changed = dict(contents)
    changed["model.safetensors"] = b"\x00" + contents["model.safetensors"][1:]
    store, mapping = _store(tmp_path, changed)
    home = tmp_path / "home"
    home.mkdir()
    seeder = PirateFaceSeeder(
        swarm,
        store,
        mapping,
        home,
        verified=False,
        upload_rate=1024**2,
        max_seconds=60,
        stop_after_bytes=1,
        settings=SETTINGS,
    )
    try:
        with pytest.raises(PreservationError, match="pirate_face_copy_differs"):
            seeder.run(OperationControl())
    finally:
        seeder.close()
    target = store / object_path(mapping[1])
    assert target.read_bytes() == changed["model.safetensors"]


def test_mirror_looks_up_checks_and_lists_a_matching_torrent(
    tmp_path, capture, monkeypatch
) -> None:
    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    names = ["LICENSE", "model.bin"]
    swarm = _torrent(capture.sources["weights"], names)
    hashes = {
        name: hashlib.sha256(
            (capture.sources["weights"] / name).read_bytes()
        ).hexdigest()
        for name in names
    }
    entry = SwarmEntry(
        repository="test/weights",
        revision=None,
        on_hub=True,
        seeders=1,
        leechers=0,
        infohash=swarm.infohash,
        torrent_path=f"/api/torrents/{swarm.infohash}.torrent",
        hashes=tuple(sorted(hashes.items())),
    )
    lookups = []
    monkeypatch.setattr(
        "foxlight_model_preservation.pirateface_seed.lookup",
        lambda repository, **_kwargs: lookups.append(repository) or entry,
    )
    monkeypatch.setattr(
        "foxlight_model_preservation.pirateface_seed.torrent",
        lambda _entry, **_kwargs: swarm,
    )
    configure(ledger, pirate_face=True, max_active=1, session_seconds=1)
    manager = NativeMirrors(
        MirrorLedger(ledger.path),
        lambda _state: True,
        pirate_face_settings=SETTINGS,
    )
    manifest = capture.identity
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            manager.tick()
            record = ledger.read().pirate_face.get(manifest)
            if record is not None and record.verified and record.last_stopped:
                break
            time.sleep(0.05)
    finally:
        manager.close()
    record = ledger.read().pirate_face[manifest]
    assert lookups == ["test/weights"]
    assert record.verified and record.error is None and record.sessions >= 1
    assert record.objects == tuple(hashes.values())
    assert [item.infohash for item in seeded(ledger.read(), [manifest])] == [
        swarm.infohash
    ]
    # Nothing but the torrent file was written beside the stored objects.
    assert sorted(
        path.name for path in (ledger.path / TORRENTS_DIRECTORY).iterdir()
    ) == [swarm.infohash + ".torrent"]
    # The next lookup of the same listing keeps the check.
    assert refresh(ledger, manifest, "test/weights").verified
