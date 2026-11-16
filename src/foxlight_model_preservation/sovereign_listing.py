"""A sharing computer's signed listing, published as a small torrent of its own.

A computer sharing from home usually can't be reached at a web address, so the
signed documents a reachable mirror serves over HTTP (what it holds, the
torrents it shares, its object coverage) and the signed manifests of the copies
it shares travel the way its files do: as one small torrent, seeded beside
them. Everything inside is signed (documents by the mirror key, manifests by
their publishers), so where the torrent comes from never matters.

People find the listing through a signed pointer in the BitTorrent DHT: a
mutable item (BEP 44) stored under the mirror key, which is Ed25519 as BEP 44
requires. Updating the listing updates the pointer; nobody else can.

Generations live in ``listing/`` in the mirror root, one folder per listing
(named by its v2 infohash), with ``current`` naming the one being shared. A
generation is never changed once written, so the folder a session is seeding
is never rewritten under it.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .models import PreservationError
from .storage import atomic_bytes, directory, read_metadata
from .torrent_binding import torrent_engine

LISTING_DIRECTORY = "listing"
LISTING_NAME = "sovereign-listing"
DOCUMENTS = ("holdings.json", "objects.json", "torrents.json")
# BEP 44 salt: one pointer per mirror key for this purpose and version.
LISTING_SALT = b"sovereign.listing.v1"
POINTER_PREFIX = b"sovereign-listing-v1:"
LISTING_PIECE = 256 * 1024
# Everything in one listing; manifests that don't fit are left out (the copy
# is still listed, but can only be saved from somewhere its manifest is).
MAX_LISTING_BYTES = 64 * 1024**2
# A listing that still describes what is shared is renewed this often, well
# within its signed documents' week-long lifetime.
RENEW_SECONDS = 24 * 3600
_V2 = re.compile(r"[0-9a-f]{64}")
_MANIFEST = re.compile(r"manifests/[0-9a-f]{64}\.json")


@dataclass(frozen=True)
class Listing:
    """One listing generation: its torrent and where its files are."""

    infohash: str  # v1, 40 hex characters
    infohash_v2: str  # 64 hex characters, what the DHT pointer names
    data: bytes
    folder: Path  # holds LISTING_NAME/


def listing_files(
    documents: dict[str, bytes], manifests: dict[str, bytes]
) -> dict[str, bytes]:
    """The files of one listing, within its size bound.

    ``documents`` are the mirror's signed documents by name; ``manifests`` are
    held copies' signed manifest envelopes by manifest ID. Documents always
    fit (each is bounded where it is issued); manifests are added in ID order
    until the bound is reached.
    """
    files: dict[str, bytes] = {}
    total = 0
    for name in DOCUMENTS:
        data = documents.get(name)
        if data is not None:
            files[name] = data
            total += len(data)
    if total > MAX_LISTING_BYTES:
        raise PreservationError("listing documents are too large")
    for manifest_id in sorted(manifests):
        data = manifests[manifest_id]
        path = f"manifests/{manifest_id}.json"
        if not _MANIFEST.fullmatch(path) or total + len(data) > MAX_LISTING_BYTES:
            continue
        files[path] = data
        total += len(data)
    return files


def _create(folder: Path, files: dict[str, bytes]) -> bytes:
    """Build the listing torrent over ``folder/LISTING_NAME``; deterministic."""
    engine = cast(Any, torrent_engine())
    storage = engine.file_storage()
    for path in sorted(files):
        storage.add_file(f"{LISTING_NAME}/{path}", len(files[path]))
    creator = engine.create_torrent(storage, LISTING_PIECE)  # hybrid v1 + v2
    engine.set_piece_hashes(creator, str(folder))
    return bytes(engine.bencode(creator.generate()))


def read_listing(data: bytes) -> tuple[Any, str, str]:
    """Check that ``data`` is a listing torrent: its name, piece size, paths and
    total size. Returns the parsed torrent and its v1 and v2 infohashes."""
    engine = cast(Any, torrent_engine())
    try:
        info = engine.torrent_info(engine.bdecode(data))
    except (RuntimeError, TypeError, ValueError) as error:
        raise PreservationError("not a listing torrent") from error
    hashes = info.info_hashes()
    if not hashes.has_v1() or not hashes.has_v2():
        raise PreservationError("a listing torrent must be hybrid v1 + v2")
    if info.name() != LISTING_NAME or info.piece_length() != LISTING_PIECE:
        raise PreservationError("not a listing torrent")
    storage = info.files()
    total = 0
    for index in range(storage.num_files()):
        flags = int(storage.file_flags(index))
        if flags & int(storage.flag_pad_file):
            continue
        if flags & int(storage.flag_symlink):
            raise PreservationError("unexpected link in a listing torrent")
        path = storage.file_path(index).replace(os.sep, "/")
        inner = path.removeprefix(LISTING_NAME + "/")
        if inner == path or not (inner in DOCUMENTS or _MANIFEST.fullmatch(inner)):
            raise PreservationError("unexpected file in a listing torrent")
        total += int(storage.file_size(index))
    if total > MAX_LISTING_BYTES:
        raise PreservationError("listing torrent is too large")
    return info, str(hashes.v1), str(hashes.v2)


def unchanged_listing(root: Path, subject: str, now: float | None = None) -> bool:
    """Whether the current listing already describes ``subject`` (what is
    shared) and is fresh enough that its signed documents needn't be renewed.

    The documents carry their own issue times, so rebuilding every round would
    give a new torrent, and a new pointer, each time nothing changed.
    """
    if current_listing(root) is None:
        return False
    try:
        recorded, published = (root / LISTING_DIRECTORY / "subject").read_text().split()
        age = (time.time() if now is None else now) - float(published)
    except (OSError, ValueError):
        return False
    return recorded == subject and 0 <= age < RENEW_SECONDS


_LOCKS: dict[Path, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(root: Path) -> threading.Lock:
    """One lock per mirror root: publishing, pruning and withdrawing a listing
    never overlap (two announcements could otherwise delete each other's
    current generation)."""
    key = root.resolve()
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def publish_listing(
    root: Path,
    files: dict[str, bytes],
    subject: str | None = None,
    now: float | None = None,
) -> Listing:
    """Write ``files`` as a listing generation in ``root/listing`` (unless an
    identical one is there), make it current, and drop older generations.
    ``subject`` records what it describes, for :func:`unchanged_listing`."""
    if not files:
        raise PreservationError("an empty listing is not published")
    with _lock(root):
        return _publish(root, files, subject, now)


def _publish(
    root: Path, files: dict[str, bytes], subject: str | None, now: float | None
) -> Listing:
    base = root / LISTING_DIRECTORY
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    build = base / ("build-" + secrets.token_hex(8))
    try:
        for path, data in files.items():
            target = build / LISTING_NAME / path
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_bytes(data)
        torrent = _create(build, files)
        _info, v1, v2 = read_listing(torrent)
        generation = base / v2
        if not generation.exists():
            (build / "listing.torrent").write_bytes(torrent)
            os.replace(build, generation)
    finally:
        shutil.rmtree(build, ignore_errors=True)
    _set_current(base, v2)
    _prune(base, v2)
    if subject is not None:
        with directory(base) as fd:
            stamp = int(time.time() if now is None else now)
            atomic_bytes(fd, "subject", f"{subject} {stamp}".encode())
    return Listing(v1, v2, torrent, generation)


def _set_current(base: Path, v2: str) -> None:
    """Make ``v2`` current, remembering the generation it replaces."""
    with directory(base) as root:
        try:
            before = read_metadata(root, "current").decode().strip()
        except (OSError, UnicodeDecodeError):
            before = ""
        if before != v2:
            if _V2.fullmatch(before):
                atomic_bytes(root, "previous", before.encode())
            atomic_bytes(root, "current", v2.encode())


def _prune(base: Path, current: str) -> None:
    """Drop every generation but the current one and the one it replaced."""
    try:
        previous = (base / "previous").read_text().strip()
    except (OSError, UnicodeDecodeError):
        previous = ""
    for entry in base.iterdir():
        if entry.name.startswith("build-") or (
            _V2.fullmatch(entry.name) and entry.name not in {current, previous}
        ):
            shutil.rmtree(entry, ignore_errors=True)


def current_listing(root: Path) -> Listing | None:
    """The listing generation being shared from ``root``, or None."""
    return _generation(root, "current")


def _generation(root: Path, which: str) -> Listing | None:
    base = root / LISTING_DIRECTORY
    try:
        with directory(base) as fd:
            v2 = read_metadata(fd, which).decode().strip()
    except (OSError, UnicodeDecodeError, PreservationError):
        return None
    if not _V2.fullmatch(v2):
        return None
    folder = base / v2
    try:
        data = (folder / "listing.torrent").read_bytes()
        _info, v1, found = read_listing(data)
    except (OSError, PreservationError):
        return None
    if found != v2:
        return None
    return Listing(v1, v2, data, folder)


def previous_listing(root: Path) -> Listing | None:
    """The generation the current one replaced, still shared so readers that
    found the older pointer can finish fetching it; None once pruned."""
    return _generation(root, "previous")


def dht_keys(key: Ed25519PrivateKey) -> tuple[bytes, bytes]:
    """The mirror key in the form libtorrent's BEP 44 signing takes.

    libtorrent keeps an Ed25519 secret key expanded (SHA-512 of the 32-byte
    seed, clamped), as the reference implementation does. Returns that
    64-byte secret and the 32-byte public key.
    """
    seed = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    expanded = bytearray(hashlib.sha512(seed).digest())
    expanded[0] &= 248
    expanded[31] &= 63
    expanded[31] |= 64
    return bytes(expanded), public


def pointer_value(infohash_v2: str) -> bytes:
    """The DHT pointer's value: which listing torrent is current."""
    if not _V2.fullmatch(infohash_v2):
        raise PreservationError("a listing pointer names a v2 infohash")
    return POINTER_PREFIX + infohash_v2.encode()


def read_pointer(value: object) -> str | None:
    """The v2 infohash a pointer names, or None for anything else."""
    if isinstance(value, str):
        value = value.encode("latin-1", errors="replace")
    if not isinstance(value, bytes) or not value.startswith(POINTER_PREFIX):
        return None
    infohash = value.removeprefix(POINTER_PREFIX).decode("ascii", errors="replace")
    return infohash if _V2.fullmatch(infohash) else None


def withdraw_listing(root: Path) -> None:
    """Stop offering any listing from ``root``: its sharing session drops it
    on its next tick, and the DHT pointer lapses once it is no longer renewed."""
    base = root / LISTING_DIRECTORY
    with _lock(root):
        if base.exists():
            shutil.rmtree(base, ignore_errors=True)


def listing_manifests(listing: Listing) -> frozenset[str]:
    """The copies a listing generation carries signed manifests for."""
    folder = listing.folder / LISTING_NAME / "manifests"
    try:
        names = os.listdir(folder)
    except OSError:
        return frozenset()
    return frozenset(
        name.removesuffix(".json")
        for name in names
        if _MANIFEST.fullmatch("manifests/" + name)
    )
