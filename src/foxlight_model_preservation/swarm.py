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
import time
from pathlib import Path
from typing import Any, cast

from . import safefs
from .models import PreservationError
from .operations import OperationControl
from .pirateface_find import SwarmTorrent
from .storage import CHUNK_SIZE
from .torrent_binding import torrent_engine

# A swarm that delivers nothing for this long is given up for this file.
STALL_SECONDS = 180
# Receive only. No peer is ever unchoked, and the fast extension's
# "allowed fast" pieces (which a choked peer may still request) are off, so
# nothing is uploaded to the swarm.
RECEIVE_ONLY: dict[str, str | int | bool] = {
    "unchoke_slots_limit": 0,
    "allowed_fast_set_size": 0,
}
# libtorrent can report a piece before its bytes reach the file; the check is
# retried briefly after flushing before a mismatch counts.
FLUSH_RETRIES = 10
# How long libtorrent may take to point a file at its destination.
RENAME_SECONDS = 10


def _hash(path: Path, control: OperationControl) -> str:
    value = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            control.checkpoint()
            value.update(chunk)
    return value.hexdigest()


def no_unchoke_bypass(session: Any) -> None:
    """Make "never unchoke" hold for every peer class.

    libtorrent always unchokes local-network peers (the "local" peer class
    ignores unchoke slots by default), so a receive-only session clears that
    on every class.
    """
    for name in (
        "global_peer_class_id",
        "tcp_peer_class_id",
        "local_peer_class_id",
    ):
        identifier = getattr(session, name)
        peer_class = dict(session.get_peer_class(identifier))
        if peer_class.get("ignore_unchoke_slots"):
            session.set_peer_class(
                identifier, {**peer_class, "ignore_unchoke_slots": False}
            )


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
        # Native indices count padding files too; never the filtered position.
        self._files = {item.path: item for item in swarm.files}
        self._wanted: set[int] = set()

    def has(self, path: str, size: int, sha256: str | None = None) -> bool:
        """Whether this torrent holds the file at that path and size."""
        item = self._files.get(path)
        return item is not None and item.size_bytes == size

    def _start(self) -> None:
        engine = torrent_engine()
        native = cast(Any, engine).torrent_info(engine.bdecode(self.swarm.data))
        if str(native.info_hashes().v1) != self.swarm.infohash:
            raise PreservationError("pirate_face_torrent_differs")
        safefs.make_dir(self.staging, parents=True, exist_ok=True)
        session = cast(Any, engine).session(
            {
                **(self._settings or {}),
                **RECEIVE_ONLY,
            }
            if self._settings is not None
            else {
                "listen_interfaces": "0.0.0.0:0,[::]:0",
                "enable_dht": True,
                "enable_lsd": False,
                "enable_upnp": False,
                "enable_natpmp": False,
                "alert_mask": 0,
                **RECEIVE_ONLY,
            }
        )
        # libtorrent always unchokes local-network peers ("local" peer class
        # ignores unchoke slots by default); no class may do so here.
        no_unchoke_bypass(session)
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
        """Download one file straight into ``destination`` and check its hash.

        libtorrent writes the file where the save keeps it, so the model is on
        disk once (no staged copy beside it). The session is closed before the
        save hashes every file again while moving it into the signed store.
        """
        item = self._files.get(path)
        if item is None:
            raise PreservationError("swarm_file_unknown")
        index, size = item.index, item.size_bytes
        if self._handle is None:
            self._start()
        handle: Any = self._handle
        destination.parent.mkdir(parents=True, exist_ok=True)
        if size == 0:
            destination.write_bytes(b"")
            return
        # Whatever was there (an origin or mirror attempt) is replaced.
        destination.unlink(missing_ok=True)
        handle.rename_file(index, str(destination))
        renamed_by = time.monotonic() + RENAME_SECONDS
        while handle.torrent_file().files().file_path(index) != str(destination):
            control.checkpoint()
            if time.monotonic() > renamed_by:
                raise PreservationError("swarm_unavailable")
            time.sleep(0.05)
        self._wanted.add(index)
        handle.file_priority(index, 4)
        handle.resume()
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
            if destination.is_file() and _hash(destination, control) == sha256:
                break
            if attempt + 1 == FLUSH_RETRIES:
                destination.unlink(missing_ok=True)
                raise PreservationError("swarm_file_mismatch")
            time.sleep(0.5)
        # Done with this file: pieces it shares with another wanted file go
        # to libtorrent's part file, not into it.
        handle.file_priority(index, 0)

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
