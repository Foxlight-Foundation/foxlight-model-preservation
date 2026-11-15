"""Sovereign's own per-file torrents: the same file gives the same torrent."""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import pytest

from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationControl
from foxlight_model_preservation.sovereign_torrents import (
    MAX_PIECE,
    MIN_PIECE,
    FileSharer,
    TorrentCache,
    build_torrent,
    piece_length,
    read_torrent,
    torrent_name,
)
from foxlight_model_preservation.torrent_binding import torrent_engine

pytest.importorskip("libtorrent", reason="optional native BitTorrent extra")

LAB = {
    "listen_interfaces": "127.0.0.1:0",
    "enable_dht": False,
    "enable_lsd": False,
    "enable_upnp": False,
    "enable_natpmp": False,
    "enable_incoming_utp": False,
    "enable_outgoing_utp": False,
    "alert_mask": 0,
}


def _stored(folder: Path, data: bytes) -> tuple[Path, str]:
    sha256 = hashlib.sha256(data).hexdigest()
    path = folder / sha256
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path, sha256


def test_the_piece_size_and_name_depend_only_on_the_file() -> None:
    assert piece_length(0) == MIN_PIECE
    assert piece_length(MIN_PIECE * 2048) == MIN_PIECE
    assert piece_length(MIN_PIECE * 2048 + 1) == MIN_PIECE * 2
    assert piece_length(10**13) == MAX_PIECE
    sha = "a" * 64
    assert torrent_name(sha, "Model-Q4_K_M.GGUF") == sha + ".gguf"
    assert torrent_name(sha, "weights") == sha
    # An extension that isn't a plain one is left out rather than trusted.
    assert torrent_name(sha, "notes.tar gz") == sha
    with pytest.raises(PreservationError):
        torrent_name("not-a-hash", "model.gguf")


def test_two_computers_build_the_same_torrent_for_the_same_file(
    tmp_path: Path,
) -> None:
    data = os.urandom(3 * 1024 * 1024 + 7)
    first, sha256 = _stored(tmp_path / "one" / "objects", data)
    second, _ = _stored(tmp_path / "two" / "objects", data)
    built = build_torrent(first, sha256, "dir/model.gguf", tmp_path / "scratch-one")
    time.sleep(1.1)  # a creation time, if one leaked in, would differ
    again = build_torrent(second, sha256, "other/model.gguf", tmp_path / "scratch-two")
    assert (built.infohash, built.infohash_v2) == (again.infohash, again.infohash_v2)
    assert built.name == sha256 + ".gguf" and built.size == len(data)
    # The scratch links are gone; the stored files are untouched.
    assert not any((tmp_path / "scratch-one").iterdir())
    assert first.read_bytes() == data


def test_a_torrent_is_accepted_only_for_its_own_file(tmp_path: Path) -> None:
    data = os.urandom(600_000)
    stored, sha256 = _stored(tmp_path / "objects", data)
    built = build_torrent(stored, sha256, "model.bin", tmp_path / "scratch")
    assert read_torrent(built.data, sha256, len(data), "model.bin") == built
    with pytest.raises(PreservationError):
        read_torrent(built.data, sha256, len(data) + 1, "model.bin")
    with pytest.raises(PreservationError):
        read_torrent(built.data, "b" * 64, len(data), "model.bin")
    # A v1-only torrent of the same file is refused: v2 roots are required.
    engine = torrent_engine()
    files = engine.file_storage()
    files.add_file(built.name, len(data))
    creator = engine.create_torrent(
        files, piece_length(len(data)), engine.create_torrent.v1_only
    )
    (tmp_path / "v1").mkdir()
    (tmp_path / "v1" / built.name).write_bytes(data)
    engine.set_piece_hashes(creator, str(tmp_path / "v1"), lambda _piece: None)
    with pytest.raises(PreservationError, match="hybrid"):
        read_torrent(
            bytes(engine.bencode(creator.generate())), sha256, len(data), "model.bin"
        )


def test_building_stops_when_cancelled(tmp_path: Path) -> None:
    data = os.urandom(4 * 1024 * 1024)
    stored, sha256 = _stored(tmp_path / "objects", data)
    control = OperationControl()
    control.cancel()
    with pytest.raises(PreservationError):
        build_torrent(
            stored, sha256, "model.bin", tmp_path / "scratch", control=control
        )
    assert not any((tmp_path / "scratch").iterdir())


def test_the_cache_keeps_only_a_matching_torrent(tmp_path: Path) -> None:
    data = os.urandom(300_000)
    stored, sha256 = _stored(tmp_path / "objects", data)
    built = build_torrent(stored, sha256, "model.bin", tmp_path / "scratch")
    cache = TorrentCache(tmp_path / "torrents")
    assert cache.get(sha256, len(data), "model.bin") is None
    cache.put(built)
    assert cache.get(sha256, len(data), "model.bin") == built
    (tmp_path / "torrents" / (sha256 + ".torrent")).write_bytes(b"d4:junki0ee")
    assert cache.get(sha256, len(data), "model.bin") is None


def test_a_shared_file_reaches_a_downloader_and_is_never_written(
    tmp_path: Path,
) -> None:
    data = os.urandom(2 * 1024 * 1024 + 123)
    stored, sha256 = _stored(tmp_path / "objects", data)
    built = build_torrent(stored, sha256, "model.gguf", tmp_path / "scratch")
    before = os.stat(stored).st_mtime_ns
    sharer = FileSharer(tmp_path / "sharer", open_router=False, settings=LAB)
    engine = torrent_engine()
    downloader = engine.session(LAB)
    try:
        sharer.share(built, stored)
        sharer.share(built, stored)  # sharing twice is harmless
        assert sharer.shared() == {sha256}
        fetched = tmp_path / "fetched"
        handle = downloader.add_torrent(
            {
                "ti": engine.torrent_info(engine.bdecode(built.data)),
                "save_path": str(fetched),
            }
        )
        handle.connect_peer(("127.0.0.1", sharer.port()))
        end = time.monotonic() + 30
        while not handle.status().is_seeding and time.monotonic() < end:
            time.sleep(0.1)
        assert handle.status().is_seeding
        assert (fetched / built.name).read_bytes() == data
        end = time.monotonic() + 10
        while sharer.uploaded() < len(data) and time.monotonic() < end:
            time.sleep(0.1)
        assert sharer.uploaded() >= len(data)
        # Upload-only: the stored file was read, never written.
        assert os.stat(stored).st_mtime_ns == before
        assert stored.read_bytes() == data
        uploaded = sharer.uploaded()
        sharer.stop(sha256)
        assert sharer.shared() == frozenset()
        assert sharer.uploaded() >= uploaded  # counted after it stops, too
    finally:
        sharer.close()
        downloader.pause()


def test_a_mirror_shares_and_lists_every_file_of_a_held_copy(
    tmp_path: Path, capture
) -> None:
    from test_mirror import cycle, mirror

    from foxlight_model_preservation.mirror_native import NativeMirrors, configure
    from foxlight_model_preservation.mirror_state import MirrorLedger
    from foxlight_model_preservation.sovereign_torrents import held_files, listed
    from foxlight_model_preservation.storage import object_path

    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    configure(ledger, file_torrents=True)
    assert ledger.read().schema_version == "foxlight.mirror-state.v8"
    files = held_files(ledger.path / "store", capture.identity)
    assert files
    manager = NativeMirrors(
        MirrorLedger(ledger.path), lambda _state: True, file_settings=LAB
    )
    engine = torrent_engine()
    downloader = engine.session(LAB)
    try:
        end = time.monotonic() + 60
        while time.monotonic() < end:
            manager.tick()
            if manager.files and manager.files.status()["files"] == len(
                {sha for sha, _size, _path in files}
            ):
                break
            time.sleep(0.2)
        assert manager.files is not None
        assert manager.files.status()["files"] == len({sha for sha, _s, _p in files})
        entries = listed(ledger, {capture.identity})
        assert {entry.object for entry in entries} == {sha for sha, _s, _p in files}
        assert all(entry.source == "sovereign" for entry in entries)
        # A peer fetches one file through its torrent and gets the stored bytes.
        sha256, size, path = max(files, key=lambda item: item[1])
        torrent = manager.files.cache.get(sha256, size, path)
        assert torrent is not None
        fetched = tmp_path / "fetched"
        handle = downloader.add_torrent(
            {
                "ti": engine.torrent_info(engine.bdecode(torrent.data)),
                "save_path": str(fetched),
            }
        )
        assert manager.files.sharer is not None
        handle.connect_peer(("127.0.0.1", manager.files.sharer.port()))
        end = time.monotonic() + 30
        while not handle.status().is_seeding and time.monotonic() < end:
            time.sleep(0.1)
        stored = (ledger.path / "store" / object_path(sha256)).read_bytes()
        assert (fetched / torrent.name).read_bytes() == stored
        # Turning it off stops sharing and listing; built torrents are kept.
        configure(ledger, file_torrents=False)
        manager.tick()
        assert manager.files.status()["files"] == 0
        assert listed(ledger, {capture.identity}) == []
        assert manager.files.cache.get(sha256, size, path) is not None
    finally:
        manager.close()
        downloader.pause()
