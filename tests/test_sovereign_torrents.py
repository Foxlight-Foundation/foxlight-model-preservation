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
    # The name is the content's hash alone: LICENSE and LICENSE.txt with the
    # same bytes are one torrent, one swarm.
    sha = "a" * 64
    assert torrent_name(sha) == sha
    with pytest.raises(PreservationError):
        torrent_name("not-a-hash")


def test_two_computers_build_the_same_torrent_for_the_same_file(
    tmp_path: Path,
) -> None:
    data = os.urandom(3 * 1024 * 1024 + 7)
    first, sha256 = _stored(tmp_path / "one" / "objects", data)
    second, _ = _stored(tmp_path / "two" / "objects", data)
    built = build_torrent(first, sha256, tmp_path / "scratch-one")
    time.sleep(1.1)  # a creation time, if one leaked in, would differ
    again = build_torrent(second, sha256, tmp_path / "scratch-two")
    assert (built.infohash, built.infohash_v2) == (again.infohash, again.infohash_v2)
    assert built.name == sha256 and built.size == len(data)
    # The scratch links are gone; the stored files are untouched.
    assert not any((tmp_path / "scratch-one").iterdir())
    assert first.read_bytes() == data


def test_a_torrent_is_accepted_only_for_its_own_file(tmp_path: Path) -> None:
    data = os.urandom(600_000)
    stored, sha256 = _stored(tmp_path / "objects", data)
    built = build_torrent(stored, sha256, tmp_path / "scratch")
    assert read_torrent(built.data, sha256, len(data)) == built
    with pytest.raises(PreservationError):
        read_torrent(built.data, sha256, len(data) + 1)
    with pytest.raises(PreservationError):
        read_torrent(built.data, "b" * 64, len(data))
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
        read_torrent(bytes(engine.bencode(creator.generate())), sha256, len(data))


def test_building_stops_when_cancelled(tmp_path: Path) -> None:
    data = os.urandom(4 * 1024 * 1024)
    stored, sha256 = _stored(tmp_path / "objects", data)
    control = OperationControl()
    control.cancel()
    with pytest.raises(PreservationError):
        build_torrent(stored, sha256, tmp_path / "scratch", control=control)
    assert not any((tmp_path / "scratch").iterdir())


def test_the_cache_keeps_only_a_matching_torrent(tmp_path: Path) -> None:
    data = os.urandom(300_000)
    stored, sha256 = _stored(tmp_path / "objects", data)
    built = build_torrent(stored, sha256, tmp_path / "scratch")
    cache = TorrentCache(tmp_path / "torrents")
    assert cache.get(sha256, len(data)) is None
    cache.put(built)
    assert cache.get(sha256, len(data)) == built
    (tmp_path / "torrents" / (sha256 + ".torrent")).write_bytes(b"d4:junki0ee")
    assert cache.get(sha256, len(data)) is None


def test_a_shared_file_reaches_a_downloader_and_is_never_written(
    tmp_path: Path,
) -> None:
    data = os.urandom(2 * 1024 * 1024 + 123)
    stored, sha256 = _stored(tmp_path / "objects", data)
    built = build_torrent(stored, sha256, tmp_path / "scratch")
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
        # libtorrent can report seeding before its disk writes land: wait for
        # the bytes themselves, not just the flag.
        received = fetched / built.name
        end = time.monotonic() + 30
        while time.monotonic() < end and not (
            handle.status().is_seeding
            and received.exists()
            and received.read_bytes() == data
        ):
            time.sleep(0.1)
        assert handle.status().is_seeding
        assert received.read_bytes() == data
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
        torrent = manager.files.cache.get(sha256, size)
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
        stored = (ledger.path / "store" / object_path(sha256)).read_bytes()
        # libtorrent can report seeding before its disk writes land: wait for
        # the bytes themselves, not just the flag.
        received = fetched / torrent.name
        end = time.monotonic() + 30
        while time.monotonic() < end and not (
            handle.status().is_seeding
            and received.exists()
            and received.read_bytes() == stored
        ):
            time.sleep(0.1)
        assert handle.status().is_seeding
        assert received.read_bytes() == stored
        # Turning it off stops sharing and listing; built torrents are kept.
        configure(ledger, file_torrents=False)
        manager.tick()
        assert manager.files.status()["files"] == 0
        assert listed(ledger, {capture.identity}) == []
        assert manager.files.cache.get(sha256, size) is not None
    finally:
        manager.close()
        downloader.pause()


class _Session:
    """Stands in for a sharing session whose uploads the test controls."""

    def __init__(self) -> None:
        self.sent = 0
        self.rate: int | None = None
        self.paused = False

    def uploaded(self) -> int:
        return self.sent

    def set_upload_rate(self, rate: int) -> None:
        self.rate = rate

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def close(self) -> None:
        self.closed = True


def _limited(tmp_path: Path, capture, daily: int | None, monthly: int | None):
    from test_mirror import cycle, mirror

    from foxlight_model_preservation.sovereign_torrents import FileSharing

    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    ledger.update(
        lambda state: state.model_copy(
            update={"egress_daily": daily, "egress_monthly": monthly}
        )
    )
    return ledger, FileSharing(ledger)


def test_without_upload_totals_sharing_is_never_slowed_and_is_counted(
    tmp_path: Path, capture
) -> None:
    ledger, sharing = _limited(tmp_path, capture, None, None)
    session = _Session()
    before = ledger.read().egress_day_used
    session.sent = 5_000_000
    sharing.spend(session)  # type: ignore[arg-type]
    assert session.rate == 0 and not session.paused
    assert ledger.read().egress_day_used == before + 5_000_000


def test_stopping_counts_every_byte_already_sent_even_once_serving_is_off(
    tmp_path: Path, capture
) -> None:
    ledger, sharing = _limited(tmp_path, capture, None, None)
    session = _Session()
    before = ledger.read().egress_day_used
    session.sent = 1_000_000
    sharing.spend(session)  # type: ignore[arg-type]
    # Small amounts are charged in batches, so this much is still owed...
    session.sent += 100
    sharing.spend(session)  # type: ignore[arg-type]
    assert ledger.read().egress_day_used == before + 1_000_000
    # ...and stopping, even after serving was paused, still counts it.
    ledger.update(lambda state: state.model_copy(update={"paused": True}))
    sharing.sharer = session  # type: ignore[assignment]
    sharing.stop()
    assert session.closed and sharing.sharer is None
    assert ledger.read().egress_day_used == before + 1_000_100
    assert ledger.read().egress_month_used >= 1_000_100


def test_a_small_upload_total_still_shares_and_stops_exactly_at_it(
    tmp_path: Path, capture
) -> None:
    from foxlight_model_preservation.sovereign_torrents import RESERVE_BYTES

    # A total far smaller than one reservation step still lets sharing start.
    total = 3 * 1024 * 1024
    ledger, sharing = _limited(tmp_path, capture, total, None)
    used = ledger.read().egress_day_used
    session = _Session()
    sharing.spend(session)  # type: ignore[arg-type]
    assert not session.paused
    assert ledger.read().egress_day_used == used + min(total - used, RESERVE_BYTES)
    # The rate is held to what is left per second: it can't overrun between checks.
    assert session.rate is not None and 0 < session.rate <= total
    session.sent = total - used  # everything allowed has gone out
    sharing.spend(session)  # type: ignore[arg-type]
    assert session.paused
    assert ledger.read().egress_day_used <= total


def test_credit_left_over_from_yesterday_never_adds_to_today(
    tmp_path: Path, capture
) -> None:
    import datetime as clock

    total = 3 * 1024 * 1024
    ledger, sharing = _limited(tmp_path, capture, total, None)
    session = _Session()
    noon = int(clock.datetime(2030, 1, 1, 12, tzinfo=clock.UTC).timestamp())
    sharing.spend(session, now=noon)  # type: ignore[arg-type]
    yesterday = sharing._charged
    assert yesterday > 0  # reserved, none of it sent
    tomorrow = noon + 86400
    sharing.spend(session, now=tomorrow)  # type: ignore[arg-type]
    # Only today's allowance is spendable: the unused credit didn't carry over.
    assert sharing._charged - session.sent <= total


def test_a_large_library_still_publishes_a_readable_torrent_list() -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from foxlight_model_preservation.pirateface_seed import (
        MAX_TORRENTS_BYTES,
        SeededTorrent,
        issue_torrents,
        verify_torrents,
    )
    from foxlight_model_preservation.signing import public_record

    key = Ed25519PrivateKey.generate()
    copies = [hashlib.sha256(f"copy {n}".encode()).hexdigest() for n in range(3)]
    entries = [
        SeededTorrent(manifest=copies[0], infohash="f" * 40),  # Pirate Face
        *(
            SeededTorrent(
                manifest=copy,
                infohash=hashlib.sha1(f"{copy} {n}".encode()).hexdigest(),  # noqa: S324
                source="sovereign",
                object=hashlib.sha256(f"{copy} file {n}".encode()).hexdigest(),
            )
            for copy in copies
            for n in range(500)
        ),
    ]
    document = issue_torrents(key, entries)
    assert len(document) <= MAX_TORRENTS_BYTES
    kept = verify_torrents(document, public_record(key)).entries
    # Whole copies are kept or dropped together, Pirate Face entries first.
    assert any(entry.source == "pirateface" for entry in kept)
    for copy in {entry.manifest for entry in kept if entry.source == "sovereign"}:
        assert (
            sum(1 for entry in kept if entry.manifest == copy and entry.object) == 500
        )


def test_a_pirate_face_entry_keeps_its_original_wire_shape() -> None:
    from foxlight_model_preservation.pirateface_seed import SeededTorrent

    entry = SeededTorrent(manifest="a" * 64, infohash="b" * 40)
    # Exactly the fields earlier readers accept: no "object" for Pirate Face.
    assert entry.model_dump(mode="json") == {
        "manifest": "a" * 64,
        "infohash": "b" * 40,
        "source": "pirateface",
    }
    own = SeededTorrent(
        manifest="a" * 64, infohash="b" * 40, source="sovereign", object="c" * 64
    )
    assert own.model_dump(mode="json")["object"] == "c" * 64


def test_a_mirror_may_serve_only_through_file_torrents(tmp_path: Path, capture) -> None:
    from test_mirror import cycle, mirror

    from foxlight_model_preservation.mirror import run_mirror
    from foxlight_model_preservation.mirror_native import configure

    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    ledger.update(lambda state: state.model_copy(update={"http_enabled": False}))
    configure(ledger, file_torrents=True)
    control = OperationControl()
    control.cancel()
    with pytest.raises(PreservationError) as caught:
        run_mirror(ledger, control=control)
    # It gets past the start-up check (then stops because it was cancelled).
    assert "serving transport" not in str(caught.value)


def test_a_file_is_received_by_magnet_and_checked(tmp_path: Path) -> None:
    from foxlight_model_preservation.sovereign_torrents import FileSwarmReceiver

    data = os.urandom(1_500_000)
    stored, sha256 = _stored(tmp_path / "objects", data)
    built = build_torrent(stored, sha256, tmp_path / "scratch")
    sharer = FileSharer(tmp_path / "sharer", open_router=False, settings=LAB)
    try:
        sharer.share(built, stored)
        receiver = FileSwarmReceiver(
            {sha256: (built.infohash, len(data))},
            tmp_path / "staging",
            settings=LAB,
            peers=(("127.0.0.1", sharer.port()),),
            stall_seconds=20,
        )
        try:
            assert receiver.has("weights/model.gguf", len(data), sha256)
            assert not receiver.has("weights/model.gguf", len(data) + 1, sha256)
            # Same path, other bytes (another member's file): not this torrent.
            assert not receiver.has("weights/model.gguf", len(data), "1" * 64)
            target = tmp_path / "work" / "model.gguf"
            receiver.fetch(
                "weights/model.gguf",
                sha256,
                target,
                OperationControl(),
                deadline=time.monotonic() + 30,
            )
            assert target.read_bytes() == data
            # A file the listing doesn't name is never fetched.
            with pytest.raises(PreservationError, match="swarm_file_unknown"):
                receiver.fetch(
                    "weights/model.gguf",
                    "0" * 64,
                    tmp_path / "work" / "other",
                    OperationControl(),
                    deadline=time.monotonic() + 5,
                )
        finally:
            receiver.close()
    finally:
        sharer.close()


def test_a_torrent_that_breaks_the_rules_is_refused_before_any_data(
    tmp_path: Path,
) -> None:
    """A listing pointing at some other torrent can't make a receiver download it."""
    from foxlight_model_preservation.sovereign_torrents import FileSwarmReceiver

    data = os.urandom(800_000)
    stored, sha256 = _stored(tmp_path / "objects", data)
    other = os.urandom(800_000)
    elsewhere, other_sha = _stored(tmp_path / "other", other)
    wrong = build_torrent(elsewhere, other_sha, tmp_path / "scratch")
    sharer = FileSharer(tmp_path / "sharer", open_router=False, settings=LAB)
    try:
        sharer.share(wrong, elsewhere)
        receiver = FileSwarmReceiver(
            {sha256: (wrong.infohash, len(data))},
            tmp_path / "staging",
            settings=LAB,
            peers=(("127.0.0.1", sharer.port()),),
            stall_seconds=20,
        )
        try:
            target = tmp_path / "work" / "model.bin"
            with pytest.raises(PreservationError, match="swarm_file_mismatch"):
                receiver.fetch(
                    "model.bin",
                    sha256,
                    target,
                    OperationControl(),
                    deadline=time.monotonic() + 30,
                )
            assert not target.exists()
            # Nothing of the refused torrent was downloaded anywhere.
            staged = [p for p in (tmp_path / "staging").rglob("*") if p.is_file()]
            assert all(p.stat().st_size == 0 for p in staged)
            # And receiving never uploads, local-network peers included.
            session = receiver._session
            for name in (
                "global_peer_class_id",
                "tcp_peer_class_id",
                "local_peer_class_id",
            ):
                assert not dict(session.get_peer_class(getattr(session, name))).get(
                    "ignore_unchoke_slots"
                )
        finally:
            receiver.close()
    finally:
        sharer.close()


def test_an_idle_mirror_never_rewrites_its_ledger(tmp_path: Path, capture) -> None:
    ledger, sharing = _limited(tmp_path, capture, None, None)
    session = _Session()
    record = ledger.path / "mirror.json"
    before = record.stat().st_mtime_ns, record.read_bytes()
    for _ in range(20):
        sharing.spend(session)  # type: ignore[arg-type]
    assert (record.stat().st_mtime_ns, record.read_bytes()) == before
    assert not session.paused


def test_a_file_falls_back_to_the_next_swarm_and_cancelling_stops_at_once(
    tmp_path: Path,
) -> None:
    from foxlight_model_preservation.operations import OperationCancelled
    from foxlight_model_preservation.sovereign_torrents import SwarmSources

    calls: list[str] = []

    class Swarm:
        def __init__(self, name: str, error: BaseException | None) -> None:
            self.name, self.error, self.closed = name, error, False

        def has(self, path: str, size: int, sha256: str | None = None) -> bool:
            return True

        def fetch(self, path, sha256, destination, control, *, deadline) -> None:  # type: ignore[no-untyped-def]
            calls.append(self.name)
            if self.error is not None:
                raise self.error
            destination.write_bytes(b"ok")

        def close(self) -> None:
            self.closed = True

    own = Swarm("sovereign", PreservationError("swarm_unavailable"))
    fallback = Swarm("pirateface", None)
    sources = SwarmSources([own, fallback])
    assert sources.has("model.bin", 2, "a" * 64)
    target = tmp_path / "model.bin"
    sources.fetch("model.bin", "a" * 64, target, OperationControl(), deadline=0)
    assert calls == ["sovereign", "pirateface"] and target.read_bytes() == b"ok"
    sources.close()
    assert own.closed and fallback.closed
    # Stop is never treated as "try the next one".
    calls.clear()
    stopped = SwarmSources([Swarm("sovereign", OperationCancelled()), fallback])
    with pytest.raises(OperationCancelled):
        stopped.fetch("model.bin", "a" * 64, target, OperationControl(), deadline=0)
    assert calls == ["sovereign"]
