"""Mirrors seed matching Pirate Face torrents from their stored files."""

from __future__ import annotations

import hashlib
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_mirror import cycle, mirror

from foxlight_model_preservation.mirror_models import PirateFaceSeed
from foxlight_model_preservation.mirror_native import NativeMirrors, configure
from foxlight_model_preservation.mirror_state import MirrorLedger
from foxlight_model_preservation.models import FileIdentity, PreservationError
from foxlight_model_preservation.pirateface_find import (
    SwarmEntry,
    SwarmTorrent,
    TorrentFile,
)
from foxlight_model_preservation.pirateface_seed import (
    LISTED_SECONDS,
    TORRENTS_DIRECTORY,
    SeededTorrent,
    due,
    issue_torrents,
    match,
    origin,
    refresh,
    seeded,
    verify_torrents,
)
from foxlight_model_preservation.signing import public_record


@pytest.fixture(autouse=True)
def no_engine(monkeypatch):
    """Choices are recorded offline; libtorrent itself is tested separately."""
    monkeypatch.setattr(
        "foxlight_model_preservation.mirror_native.torrent_engine", lambda: None
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _swarm(*files: tuple[str, int], infohash: str = "a" * 40) -> SwarmTorrent:
    return SwarmTorrent(
        infohash=infohash,
        data=b"d4:infod4:name1:xee",
        name="x",
        files=tuple(
            TorrentFile(path=path, size_bytes=size, index=index * 2)
            for index, (path, size) in enumerate(files)
        ),
        revision=None,
    )


def test_match_uses_recorded_hashes_then_unique_path_and_size() -> None:
    weights, notes = b"w" * 10, b"n" * 3
    held = (
        FileIdentity(path="model.bin", size=10, sha256=_sha(weights)),
        FileIdentity(path="README.md", size=3, sha256=_sha(notes)),
    )
    swarm = _swarm(("model.bin", 10), ("README.md", 3))
    assert match(swarm, {"model.bin": _sha(weights)}, held) == (
        _sha(weights),
        _sha(notes),
    )
    # A recorded hash the mirror doesn't hold means another copy of the file.
    assert match(swarm, {"model.bin": "0" * 64}, held) is None
    # One file the mirror lacks means the torrent isn't this copy.
    assert match(_swarm(("model.bin", 10), ("extra.bin", 1)), {}, held) is None
    # The same path and size held twice, with different bytes, is ambiguous.
    twice = (*held, FileIdentity(path="model.bin", size=10, sha256="1" * 64))
    assert match(swarm, {}, twice) is None
    assert match(swarm, {"model.bin": _sha(weights)}, twice) is not None


def test_torrents_list_is_self_signed_short_lived_and_unique() -> None:
    key = Ed25519PrivateKey.generate()
    entry = SeededTorrent(manifest="b" * 64, infohash="c" * 40)
    document = issue_torrents(key, [entry, entry], now=1000)
    listed = verify_torrents(document, public_record(key), now=1001)
    assert listed.entries == (entry,)
    with pytest.raises(PreservationError, match="another mirror key"):
        verify_torrents(document, public_record(Ed25519PrivateKey.generate()), now=1001)
    with pytest.raises(PreservationError, match="expired"):
        verify_torrents(document, public_record(key), now=1000 + 8 * 86400)


def _entry(swarm: SwarmTorrent, hashes: dict[str, str]) -> SwarmEntry:
    return SwarmEntry(
        repository="test/weights",
        revision=None,
        on_hub=True,
        seeders=1,
        leechers=0,
        infohash=swarm.infohash,
        torrent_path=f"/api/torrents/{swarm.infohash}.torrent",
        hashes=tuple(sorted(hashes.items())),
    )


def _weights(capture) -> dict[str, bytes]:
    source = capture.sources["weights"]
    return {name: (source / name).read_bytes() for name in ("LICENSE", "model.bin")}


def _listing(monkeypatch, swarm: SwarmTorrent | None, hashes: dict[str, str]):
    calls = []

    def lookup(repository, **_kwargs):
        calls.append(repository)
        if isinstance(swarm, Exception):
            raise swarm
        return None if swarm is None else _entry(swarm, hashes)

    monkeypatch.setattr("foxlight_model_preservation.pirateface_seed.lookup", lookup)
    monkeypatch.setattr(
        "foxlight_model_preservation.pirateface_seed.torrent",
        lambda _entry, **_kwargs: swarm,
    )
    return calls


def test_refresh_records_a_match_and_keeps_a_verified_torrent(
    tmp_path, capture, monkeypatch
) -> None:
    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    configure(ledger, pirate_face=True)
    manifest = capture.identity
    assert origin(ledger, manifest) == "test/weights"
    files = _weights(capture)
    swarm = _swarm(*((name, len(data)) for name, data in files.items()))
    hashes = {name: _sha(data) for name, data in files.items()}
    _listing(monkeypatch, swarm, hashes)
    record = refresh(ledger, manifest, "test/weights")
    assert record.listed and record.objects == tuple(hashes.values())
    assert not record.verified
    assert (ledger.path / TORRENTS_DIRECTORY / (swarm.infohash + ".torrent")).exists()
    assert ledger.read().schema_version == "foxlight.mirror-state.v7"
    # Only a checked torrent is listed for others.
    assert seeded(ledger.read(), [manifest]) == []

    def verified(state):
        records = dict(state.pirate_face)
        records[manifest] = records[manifest].model_copy(update={"verified": True})
        return state.model_copy(update={"pirate_face": records})

    ledger.update(verified)
    assert seeded(ledger.read(), [manifest]) == [
        SeededTorrent(manifest=manifest, infohash=swarm.infohash)
    ]
    # Pirate Face down, or the model delisted: the verified torrent stays.
    _listing(monkeypatch, PreservationError("pirate_face_unavailable"), {})
    record = refresh(ledger, manifest, "test/weights")
    assert record.verified and record.lookup_error == "pirate_face_unavailable"
    _listing(monkeypatch, None, {})
    record = refresh(ledger, manifest, "test/weights")
    assert record.verified and not record.listed and record.objects
    # A different torrent replaces it and must be checked again.
    other = _swarm(
        *((name, len(data)) for name, data in files.items()), infohash="d" * 40
    )
    _listing(monkeypatch, other, hashes)
    record = refresh(ledger, manifest, "test/weights")
    assert record.infohash == "d" * 40 and not record.verified


def test_a_torrent_with_files_the_copy_lacks_is_never_kept(
    tmp_path, capture, monkeypatch
) -> None:
    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    configure(ledger, pirate_face=True)
    files = _weights(capture)
    swarm = _swarm(
        *((name, len(data)) for name, data in files.items()), ("extra.bin", 5)
    )
    _listing(monkeypatch, swarm, {})
    record = refresh(ledger, capture.identity, "test/weights")
    assert record.listed and record.objects == ()
    assert not (ledger.path / TORRENTS_DIRECTORY).exists()


def test_lookups_are_paced_by_what_they_found() -> None:
    now = 10 * LISTED_SECONDS
    assert due(None, now)
    listed = PirateFaceSeed(repository="a/b", checked_at=now - 3600, listed=True)
    assert not due(listed, now)
    assert due(listed.model_copy(update={"checked_at": now - LISTED_SECONDS}), now)
    unlisted = PirateFaceSeed(repository="a/b", checked_at=now - LISTED_SECONDS)
    assert not due(unlisted, now)
    failed = unlisted.model_copy(
        update={"checked_at": now - 3600, "lookup_error": "pirate_face_unavailable"}
    )
    assert due(failed, now)


def test_older_profiles_cannot_express_pirate_face_seeding(tmp_path, capture) -> None:
    ledger, _ = mirror(tmp_path, capture)
    configure(ledger, pirate_face=True)
    state = ledger.read()
    assert state.schema_version == "foxlight.mirror-state.v7"
    with pytest.raises(ValueError, match="requires state v7"):
        type(state).model_validate(
            {**state.model_dump(), "schema_version": "foxlight.mirror-state.v6"}
        )


def test_seeding_off_starts_no_lookup_or_session(tmp_path, capture, monkeypatch):
    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    configure(ledger, torrent=False, pirate_face=False)
    calls = _listing(monkeypatch, None, {})
    manager = NativeMirrors(MirrorLedger(ledger.path), lambda _state: True)
    try:
        manager.tick()
        time.sleep(0.2)
        manager.tick()
    finally:
        manager.close()
    assert calls == [] and manager.sessions == {}
    assert ledger.read().pirate_face == {}


def test_lookups_and_checks_never_change_the_restart_identity(tmp_path, capture):
    from foxlight_model_preservation.mirror_state import policy_id

    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    configure(ledger, pirate_face=True)
    before = policy_id(ledger.read())
    record = PirateFaceSeed(repository="test/weights", checked_at=1, listed=True)
    ledger.update(
        lambda state: state.model_copy(
            update={"pirate_face": {capture.identity: record}}
        )
    )
    assert policy_id(ledger.read()) == before
    # The choice itself is policy: turning it off is a new identity.
    configure(ledger, pirate_face=False)
    assert policy_id(ledger.read()) != before


def test_a_check_never_vouches_for_a_torrent_that_replaced_it(
    tmp_path, capture, monkeypatch
) -> None:
    from foxlight_model_preservation.pirateface_seed import record_seed

    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    configure(ledger, pirate_face=True)
    files = _weights(capture)
    hashes = {name: _sha(data) for name, data in files.items()}
    sizes = [(name, len(data)) for name, data in files.items()]
    _listing(monkeypatch, _swarm(*sizes), hashes)
    first = refresh(ledger, capture.identity, "test/weights")
    checked = (first.infohash, first.objects)
    # A lookup replaces the torrent while a session is checking the old one.
    _listing(monkeypatch, _swarm(*sizes, infohash="e" * 40), hashes)
    refresh(ledger, capture.identity, "test/weights")
    record_seed(ledger, capture.identity, checked=checked, verified=True)
    record_seed(ledger, capture.identity, checked=checked, error="refused")
    current = ledger.read().pirate_face[capture.identity]
    assert current.infohash == "e" * 40
    assert not current.verified and current.error is None
    assert seeded(ledger.read(), [capture.identity]) == []
