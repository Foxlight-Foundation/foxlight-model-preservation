"""Sovereign's own torrents: one per stored file, the same on every computer.

People save different selections of a model (one quantization or another), so
a torrent per saved copy would split every model into many small swarms. A
torrent per file doesn't: it is built from that file alone, by fixed rules, so
everyone who holds the file joins one swarm for it, whatever else they saved.

The rules (they decide the infohash, so changing them splits every swarm):

- the torrent's name is the file's SHA-256 (hex) plus its lowercase extension;
- the piece size depends only on the file's size (:func:`piece_length`);
- BitTorrent hybrid v1 + v2 (BEP 52), one file, no padding;
- nothing else enters the info dictionary. Trackers, web seeds, a creator or a
  creation date never do (they would not change the infohash anyway).

Trust never comes from a torrent: a receiver checks every file against a
SHA-256 it already trusts. Building reads the stored file in place through a
link, and sharing maps the torrent onto that same file, so no second copy is
written. Sharing is upload-only: stored files are never written.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import tempfile
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from .models import PreservationError
from .operations import OperationCancelled, OperationControl
from .storage import object_path
from .torrent_binding import torrent_engine

if TYPE_CHECKING:
    from .mirror_models import MirrorState
    from .mirror_state import MirrorLedger
    from .pirateface_seed import SeededTorrent

# Where a mirror keeps its built torrents and its sharing session's scratch.
FILES_DIRECTORY = "scratch/native/files"
# Upload allowance reserved ahead of the bytes actually sent, like other egress.
RESERVE_BYTES = 256 * 1024**2

MIN_PIECE = 256 * 1024
MAX_PIECE = 16 * 1024**2
# Pieces per file the size rule aims for: few enough to keep metadata small.
TARGET_PIECES = 2048
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SUFFIX = re.compile(r"\.[a-z0-9]{1,12}")


def piece_length(size: int) -> int:
    """The piece size for a file of ``size`` bytes: a power of two from 256 KiB
    to 16 MiB, the smallest that keeps the file within about 2,048 pieces."""
    if size < 0:
        raise PreservationError("file size must not be negative")
    length = MIN_PIECE
    while length < MAX_PIECE and size > length * TARGET_PIECES:
        length *= 2
    return length


def torrent_name(sha256: str, path: str) -> str:
    """The torrent's name: the file's SHA-256 plus its lowercase extension."""
    if not _SHA256.fullmatch(sha256):
        raise PreservationError("a file torrent needs the file's SHA-256")
    suffix = Path(path).suffix.lower()
    return sha256 + (suffix if _SUFFIX.fullmatch(suffix) else "")


@dataclass(frozen=True)
class FileTorrent:
    """One file's torrent: its bytes, and the infohashes peers know it by."""

    sha256: str
    size: int
    name: str
    infohash: str  # v1, 40 hex characters (what the DHT and most clients use)
    infohash_v2: str  # 64 hex characters
    data: bytes


def _parse(data: bytes) -> tuple[Any, str, str]:
    engine = cast(Any, torrent_engine())
    info = engine.torrent_info(engine.bdecode(data))
    hashes = info.info_hashes()
    if not hashes.has_v1() or not hashes.has_v2():
        raise PreservationError("a file torrent must be hybrid v1 + v2")
    return info, str(hashes.v1), str(hashes.v2)


def read_torrent(data: bytes, sha256: str, size: int, path: str) -> FileTorrent:
    """Check that ``data`` is exactly the torrent these rules give this file."""
    info, v1, v2 = _parse(data)
    files = info.files()
    name = torrent_name(sha256, path)
    if (
        files.num_files() != 1
        or files.file_size(0) != size
        or info.name() != name
        or info.piece_length() != piece_length(size)
    ):
        raise PreservationError("not this file's torrent")
    return FileTorrent(sha256, size, name, v1, v2, data)


def build_torrent(
    source: Path,
    sha256: str,
    path: str,
    scratch: Path | None = None,
    *,
    control: OperationControl | None = None,
) -> FileTorrent:
    """Build one stored file's torrent by reading it in place.

    The file is reached through a symbolic link named as the torrent says, so
    nothing is copied. The link lives in ``scratch`` (default: the system's
    temporary folder), which must be able to hold symbolic links even when the
    store can't (an exFAT library drive). Reading reports its progress as
    hashed bytes and stops on cancellation.
    """
    active = control or OperationControl()
    size = os.stat(source).st_size
    name = torrent_name(sha256, path)
    length = piece_length(size)
    engine = cast(Any, torrent_engine())
    files = engine.file_storage()
    files.add_file(name, size)
    creator = engine.create_torrent(files, length)  # default: hybrid v1 + v2
    if scratch is None:
        folder = Path(tempfile.mkdtemp(prefix="sovereign-torrent-"))
    else:
        folder = scratch / ("build-" + secrets.token_hex(8))
        folder.mkdir(parents=True, mode=0o700)
    try:
        os.symlink(source.resolve(), folder / name)
        stopped: list[BaseException] = []

        def progress(_piece: int) -> None:
            if stopped:
                return
            try:
                active.advance(hashed=length)
            except BaseException as error:  # noqa: BLE001 - re-raised below
                stopped.append(error)
                raise

        engine.set_piece_hashes(creator, str(folder), progress)
        if stopped:
            raise stopped[0]
        active.checkpoint()
        data = bytes(engine.bencode(creator.generate()))
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return read_torrent(data, sha256, size, path)


class TorrentCache:
    """Built torrents kept by file SHA-256, so each file is read once."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def get(self, sha256: str, size: int, path: str) -> FileTorrent | None:
        """The kept torrent for this file, if one was built and still matches."""
        try:
            data = (self.path / (sha256 + ".torrent")).read_bytes()
            return read_torrent(data, sha256, size, path)
        except (FileNotFoundError, PreservationError, RuntimeError, ValueError):
            return None

    def put(self, torrent: FileTorrent) -> None:
        """Keep a built torrent (written whole, then renamed into place)."""
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        pending = self.path / (torrent.sha256 + ".torrent." + secrets.token_hex(4))
        pending.write_bytes(torrent.data)
        os.replace(pending, self.path / (torrent.sha256 + ".torrent"))


class FileSharer:
    """One upload-only BitTorrent session sharing stored files.

    Each torrent is mapped onto the stored file itself and added in seed and
    upload mode: libtorrent checks a piece the first time a peer asks for it,
    and never writes. ``open_router`` lets libtorrent ask the home router to
    forward its port (the owner's sharing choice). ``upload_rate`` is bytes
    per second; 0 means no limit (the default).
    """

    def __init__(
        self,
        home: Path,
        *,
        open_router: bool,
        upload_rate: int = 0,
        settings: dict[str, str | int | bool] | None = None,
    ) -> None:
        if upload_rate < 0:
            raise PreservationError("upload rate must not be negative")
        engine = cast(Any, torrent_engine())
        self._engine = engine
        self._home = home
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self._handles: dict[str, Any] = {}
        # Payload bytes uploaded by files no longer shared in this session.
        self._earlier = 0
        self.session = engine.session(
            {
                "listen_interfaces": "0.0.0.0:0,[::]:0",
                "enable_dht": True,
                "enable_lsd": False,
                "enable_upnp": open_router,
                "enable_natpmp": open_router,
                "alert_mask": 0,
                **(settings or {}),
                "upload_rate_limit": upload_rate,
                "download_rate_limit": 1,
            }
        )

    def port(self) -> int:
        """The listening port, for status and tests."""
        with self._lock:
            return int(self.session.listen_port())

    def share(self, torrent: FileTorrent, stored: Path) -> None:
        """Share ``stored`` (the file itself) as ``torrent``; already shared is fine."""
        with self._lock:
            if torrent.sha256 in self._handles:
                return
            info, _v1, _v2 = _parse(torrent.data)
            flags = self._engine.torrent_flags
            self._handles[torrent.sha256] = self.session.add_torrent(
                {
                    "ti": info,
                    "save_path": str(self._home),
                    "renamed_files": {0: str(stored.resolve())},
                    "flags": flags.upload_mode | flags.seed_mode,
                }
            )

    def stop(self, sha256: str) -> None:
        """Stop sharing one file; its stored bytes are untouched."""
        with self._lock:
            handle = self._handles.pop(sha256, None)
            if handle is not None:
                self._earlier += int(handle.status().total_payload_upload)
                self.session.remove_torrent(handle)

    def shared(self) -> frozenset[str]:
        """SHA-256s of the files this session shares."""
        with self._lock:
            return frozenset(self._handles)

    def uploaded(self) -> int:
        """Model bytes uploaded since this session began (for upload totals)."""
        with self._lock:
            return self._earlier + sum(
                int(handle.status().total_payload_upload)
                for handle in self._handles.values()
            )

    def pause(self) -> None:
        """Stop uploading until resumed (an upload total was reached)."""
        with self._lock:
            self.session.pause()

    def resume(self) -> None:
        """Upload again."""
        with self._lock:
            self.session.resume()

    def connect(self, peer: tuple[str, int]) -> None:
        """Connect every shared file to a known peer (a lab network, or tests)."""
        with self._lock:
            for handle in self._handles.values():
                handle.connect_peer(peer)

    def close(self) -> None:
        """Stop sharing everything now, not when this object is collected."""
        with self._lock:
            for handle in self._handles.values():
                self.session.remove_torrent(handle)
            self._handles.clear()
            self.session.pause()


def held_files(store: Path, manifest: str) -> tuple[tuple[str, int, str], ...]:
    """Every file of one held copy: (SHA-256, size, path), from its manifest."""
    from .mirror_holdings import held_manifest

    document = held_manifest(store, manifest)
    if document is None:
        return ()
    return tuple(
        (file.sha256, file.size, file.path)
        for member in document.members
        for file in member.files
    )


def listed(ledger: MirrorLedger, held: Iterable[str]) -> list[SeededTorrent]:
    """The Sovereign torrents a mirror shares, for its signed torrents list.

    A copy is listed once every one of its files has a built torrent, one entry
    per file. Nothing is listed while per-file sharing is off.
    """
    from .pirateface_seed import SeededTorrent

    policy = ledger.read().native_policy
    if policy is None or not policy.file_torrents:
        return []
    cache = TorrentCache(ledger.path / FILES_DIRECTORY / "torrents")
    store = ledger.path / "store"
    entries: list[SeededTorrent] = []
    for manifest in sorted(held):
        files = held_files(store, manifest)
        built = [cache.get(sha256, size, path) for sha256, size, path in files]
        if files and all(built):
            entries.extend(
                SeededTorrent(
                    manifest=manifest,
                    infohash=torrent.infohash,
                    source="sovereign",
                    object=torrent.sha256,
                )
                for torrent in built
                if torrent is not None
            )
    return entries


class FileSharing:
    """Keep one mirror's per-file sharing in step with what it may serve.

    Each tick: work out every file of every copy it may serve, share the ones
    whose torrent is built, stop the rest, and build one missing torrent at a
    time in the background (reading the stored file once). Upload is reserved
    from the mirror's egress allowance in steps ahead of what is sent; when the
    owner's daily or monthly total is reached, sharing pauses until it frees.
    """

    def __init__(
        self,
        ledger: MirrorLedger,
        *,
        settings: dict[str, str | int | bool] | None = None,
    ) -> None:
        self.ledger = ledger
        self.settings = settings
        self.home = ledger.path / FILES_DIRECTORY
        self.cache = TorrentCache(self.home / "torrents")
        self.sharer: FileSharer | None = None
        self.paused = False
        self.failed: dict[str, str] = {}
        self._reserved = 0
        self._router = False
        self._build: threading.Thread | None = None
        self._control = OperationControl()

    def tick(self, state: MirrorState, allowed: Callable[[str], bool]) -> None:
        """Bring sharing in line with the policy and the copies it may serve."""
        policy = state.native_policy
        if policy is None or not policy.file_torrents:
            self.stop()
            return
        store = self.ledger.path / "store"
        wanted: dict[str, tuple[int, str]] = {}
        for manifest in sorted(state.models):
            if allowed(manifest):
                for sha256, size, path in held_files(store, manifest):
                    wanted.setdefault(sha256, (size, path))
        if self.sharer is not None and self._router != policy.map_ports:
            self.stop()  # the router choice changed: start a fresh session
        if self.sharer is None:
            self.sharer = FileSharer(
                self.home / "session",
                open_router=policy.map_ports,
                settings=self.settings,
            )
            self._router = policy.map_ports
            self._reserved = 0
            self.paused = False
        sharer = self.sharer
        self._spend(sharer)
        for sha256 in sharer.shared() - wanted.keys():
            sharer.stop(sha256)
        missing: list[tuple[str, int, str]] = []
        for sha256, (size, path) in wanted.items():
            if sha256 in sharer.shared() or sha256 in self.failed:
                continue
            torrent = self.cache.get(sha256, size, path)
            if torrent is not None:
                sharer.share(torrent, store / object_path(sha256))
            else:
                missing.append((sha256, size, path))
        if missing and (self._build is None or not self._build.is_alive()):
            sha256, _size, path = missing[0]
            self._build = threading.Thread(
                target=self._make,
                args=(store / object_path(sha256), sha256, path),
                daemon=True,
            )
            self._build.start()

    def _spend(self, sharer: FileSharer) -> None:
        """Reserve upload ahead of what is sent; pause when none is left."""
        if sharer.uploaded() + RESERVE_BYTES // 2 < self._reserved:
            return
        if self.ledger.reserve(RESERVE_BYTES):
            self._reserved += RESERVE_BYTES
            if self.paused:
                sharer.resume()
                self.paused = False
        elif not self.paused:
            sharer.pause()
            self.paused = True

    def _make(self, stored: Path, sha256: str, path: str) -> None:
        try:
            self.cache.put(build_torrent(stored, sha256, path, control=self._control))
        except OperationCancelled:
            pass
        except (PreservationError, OSError, RuntimeError, ValueError) as error:
            # One unreadable file never stops the others; it isn't retried
            # until the mirror restarts.
            self.failed[sha256] = type(error).__name__

    def status(self) -> dict[str, int | bool]:
        """How many files are shared, and whether an upload total paused them."""
        return {
            "files": len(self.sharer.shared()) if self.sharer else 0,
            "uploaded_bytes": self.sharer.uploaded() if self.sharer else 0,
            "paused": self.paused,
        }

    def stop(self) -> None:
        """Stop sharing (the stored files and built torrents are kept)."""
        if self.sharer is not None:
            self.sharer.close()
            self.sharer = None

    def close(self) -> None:
        """Stop sharing and any torrent being built."""
        self._control.cancel()
        if self._build is not None:
            self._build.join(timeout=30)
        self.stop()
