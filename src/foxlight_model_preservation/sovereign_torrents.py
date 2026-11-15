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
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .models import PreservationError
from .operations import OperationControl
from .torrent_binding import torrent_engine

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
    scratch: Path,
    *,
    control: OperationControl | None = None,
) -> FileTorrent:
    """Build one stored file's torrent by reading it in place.

    The file is reached through a link in ``scratch`` named as the torrent
    says, so nothing is copied; ``scratch`` must be on a filesystem that can
    hold symbolic links (the app's own data folder, not an exFAT drive).
    Reading reports its progress as hashed bytes and stops on cancellation.
    """
    active = control or OperationControl()
    size = os.stat(source).st_size
    name = torrent_name(sha256, path)
    length = piece_length(size)
    engine = cast(Any, torrent_engine())
    files = engine.file_storage()
    files.add_file(name, size)
    creator = engine.create_torrent(files, length)  # default: hybrid v1 + v2
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
