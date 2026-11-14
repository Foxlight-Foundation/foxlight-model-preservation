"""Receive selected files from a public BitTorrent swarm, verified by SHA-256.

Used for Pirate Face torrents after the person turned swarm downloads on (peers
in a public swarm can see this computer's address). One session serves one
save: files are requested as the save needs them, each is checked against its
expected SHA-256 before it is accepted, and the session ends with the save.

Receiving never starts sharing: the session never unchokes a peer (no upload
slots), so this computer downloads from seeders and web seeds without serving
anyone, and nothing is seeded after the save.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any, cast

from .models import PreservationError
from .operations import OperationControl
from .pirateface_find import SwarmTorrent
from .storage import CHUNK_SIZE
from .torrent_binding import torrent_engine

# A swarm that delivers nothing for this long is given up for this file.
STALL_SECONDS = 180
# libtorrent can report a piece before its bytes reach the file; the check is
# retried briefly after flushing before a mismatch counts.
FLUSH_RETRIES = 10


def _hash(path: Path, control: OperationControl) -> str:
    value = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            control.checkpoint()
            value.update(chunk)
    return value.hexdigest()


class SwarmReceiver:
    """One download-only session for one torrent, started on first use."""

    def __init__(
        self,
        swarm: SwarmTorrent,
        staging: Path,
        *,
        stall_seconds: float = STALL_SECONDS,
        settings: dict[str, str | int | bool] | None = None,
        peers: tuple[tuple[str, int], ...] = (),
    ) -> None:
        self.swarm = swarm
        self.staging = staging
        self.stall_seconds = stall_seconds
        self._settings = settings
        self._peers = peers
        self._session: Any = None
        self._handle: Any = None
        self._index = {item.path: index for index, item in enumerate(swarm.files)}
        self._wanted: set[int] = set()

    def has(self, path: str, size: int) -> bool:
        """Whether this torrent holds the file at that path and size."""
        index = self._index.get(path)
        return index is not None and self.swarm.files[index].size_bytes == size

    def _start(self) -> None:
        engine = torrent_engine()
        native = cast(Any, engine).torrent_info(engine.bdecode(self.swarm.data))
        if str(native.info_hashes().v1) != self.swarm.infohash:
            raise PreservationError("pirate_face_torrent_differs")
        os.makedirs(self.staging, mode=0o700, exist_ok=True)
        session = cast(Any, engine).session(
            self._settings
            or {
                "listen_interfaces": "0.0.0.0:0,[::]:0",
                "enable_dht": True,
                "enable_lsd": False,
                "enable_upnp": False,
                "enable_natpmp": False,
                # Download only: no peer is ever unchoked, so nothing uploads.
                "unchoke_slots_limit": 0,
                "alert_mask": 0,
            }
        )
        flags = cast(Any, engine).torrent_flags
        handle = session.add_torrent(
            {
                "ti": native,
                "save_path": str(self.staging),
                "flags": flags.default_dont_download | flags.disable_lsd,
            }
        )
        for peer in self._peers:
            handle.connect_peer(peer)
        self._session, self._handle = session, handle

    def fetch(
        self,
        path: str,
        sha256: str,
        destination: Path,
        control: OperationControl,
        *,
        deadline: float,
    ) -> None:
        """Download one file and move it to ``destination`` once its hash matches."""
        index = self._index.get(path)
        if index is None:
            raise PreservationError("swarm_file_unknown")
        size = self.swarm.files[index].size_bytes
        if self._handle is None:
            self._start()
        handle: Any = self._handle
        self._wanted.add(index)
        handle.file_priority(index, 4)
        handle.resume()
        received = self.staging / self.swarm.name / path
        last, progress_at = -1, time.monotonic()
        while True:
            control.checkpoint()
            done = handle.file_progress()[index]
            if done != last:
                control.advance(received=max(0, done - max(last, 0)))
                last, progress_at = done, time.monotonic()
            if done >= size:
                break
            now = time.monotonic()
            if now > deadline:
                raise PreservationError("swarm_deadline_exceeded")
            if now - progress_at > self.stall_seconds:
                raise PreservationError("swarm_unavailable")
            time.sleep(0.25)
        handle.flush_cache()
        for attempt in range(FLUSH_RETRIES):
            if size == 0 or (received.is_file() and _hash(received, control) == sha256):
                break
            if attempt + 1 == FLUSH_RETRIES:
                raise PreservationError("swarm_file_mismatch")
            time.sleep(0.5)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if size == 0:
            destination.write_bytes(b"")
        else:
            # libtorrent keeps the file open; a copy leaves its view intact.
            with open(received, "rb") as reader, open(destination, "wb") as writer:
                while chunk := reader.read(CHUNK_SIZE):
                    control.checkpoint()
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            if _hash(destination, control) != sha256:
                destination.unlink(missing_ok=True)
                raise PreservationError("swarm_file_mismatch")

    def close(self) -> None:
        """End the session; nothing keeps seeding after the save."""
        session, handle = self._session, self._handle
        self._session = self._handle = None
        if session is None:
            return
        try:
            session.remove_torrent(handle)
        finally:
            session.pause()
            del session
