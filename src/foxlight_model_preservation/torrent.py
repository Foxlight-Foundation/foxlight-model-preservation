"""Explicit public torrent export and seed-only sessions using libtorrent."""

from __future__ import annotations

import ipaddress
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Literal, final
from urllib.parse import urlsplit

from pydantic import Field

from .models import (
    MAX_METADATA_BYTES,
    Digest,
    Manifest,
    PreservationError,
    Record,
    TrustProfile,
)
from .operations import (
    DeadlineExceeded,
    OperationCancelled,
    OperationControl,
    RequestChanged,
)
from .signing import digest, encode_record, verify_manifest
from .storage import (
    CHUNK_SIZE,
    atomic_bytes,
    copy_verified_object,
    directory,
    manifest_path,
    object_path,
    read_metadata,
    unique_objects,
    verify_store,
    verify_store_contents,
    writer_lock,
)
from .torrent_binding import TorrentHandle, TorrentSession, torrent_engine


@final
class TorrentExport(Record):
    """Exact public export receipt; contains neither local paths nor private trust."""

    manifest_id: Digest
    metainfo_sha256: Digest
    file_count: int = Field(ge=1, le=100_001)
    payload_bytes: int = Field(ge=1)
    public_peer_participation: Literal[False] = False


@final
class TorrentSeedPolicy(Record):
    """Explicit session limits; payload stop threshold permits in-flight overshoot."""

    public_swarm: bool = False
    listen_host: str = "127.0.0.1"
    listen_port: int = Field(default=0, ge=0, le=65535)
    upload_rate_bytes: int = Field(default=1024 * 1024, ge=1024, le=100 * 1024**2)
    max_connections: int = Field(default=8, ge=1, le=64)
    max_seconds: int = Field(default=3600, ge=1, le=86400)
    stop_after_payload_bytes: int = Field(default=1024**3, ge=1, le=2**53 - 1)


@final
class TorrentObservation(Record):
    """Safe session observation; no IPs, peer IDs, filenames or request history."""

    ready: bool = False
    uploaded_payload_bytes: int = Field(default=0, ge=0)
    connected_peers: int = Field(default=0, ge=0)
    stopped: bool = False
    reason: Literal["stopped", "duration", "payload_threshold"] | None = None


def _public_manifest(
    store: Path,
    manifest_id: str,
    trust: TrustProfile,
    control: OperationControl | None = None,
) -> Manifest:
    manifest = (
        verify_store_contents(store, manifest_id, trust, control)
        if control is not None
        else verify_store(store, manifest_id, trust)
    )
    if any(member.intake.audience != "public" for member in manifest.members):
        raise PreservationError(
            "BitTorrent exports require public intake for every member"
        )
    return manifest


def _files(manifest: Manifest, envelope: bytes, manifest_id: str) -> dict[str, int]:
    return {
        manifest_id + "/" + manifest_path(manifest_id): len(envelope),
        **{
            manifest_id + "/" + object_path(file.sha256): file.size
            for file in unique_objects(manifest)
        },
    }


def _web_seed(value: str, allow_local_http: bool) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ({"https", "http"} if allow_local_http else {"https"})
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise PreservationError("web seeds need credential-free HTTPS base URLs")
    return value.rstrip("/") + "/"


def export_torrent(
    store: Path,
    manifest_id: str,
    trust: TrustProfile,
    destination: Path,
    *,
    web_seeds: tuple[str, ...] = (),
    allow_local_http: bool = False,
    control: OperationControl | None = None,
) -> TorrentExport:
    """Verify public data and export standard torrent files without networking."""
    with (control or OperationControl()).operation("pack") as active:
        return _export_torrent(
            store,
            manifest_id,
            trust,
            destination,
            web_seeds=web_seeds,
            allow_local_http=allow_local_http,
            control=active,
        )


def _export_torrent(
    store: Path,
    manifest_id: str,
    trust: TrustProfile,
    destination: Path,
    *,
    web_seeds: tuple[str, ...] = (),
    allow_local_http: bool = False,
    control: OperationControl,
) -> TorrentExport:
    """Create a fresh public-only tree and standard metainfo; start no network.

    The exported directory contains ``<manifest-id>/`` (an ordinary mirror root),
    ``<manifest-id>.torrent`` and this safe receipt. Web-seed base URLs host its
    parent, so clients request ``<base>/<manifest-id>/objects/...``. Ordinary
    clients may receive it, but must independently verify the signed manifest
    and actual bytes before loading a model. Transport hashes are not authority.
    """
    if len(web_seeds) > 16 or len(set(web_seeds)) != len(web_seeds):
        raise PreservationError("web seeds must be unique and bounded")
    seeds = tuple(_web_seed(value, allow_local_http) for value in web_seeds)
    if destination.exists() or destination.is_symlink():
        raise PreservationError("torrent export requires a new destination")
    if destination.resolve().is_relative_to(store.resolve()):
        raise PreservationError("torrent export must be outside the source archive")
    manifest = _public_manifest(store, manifest_id, trust, control)
    engine = torrent_engine()
    with directory(store) as origin:
        envelope = read_metadata(origin, manifest_path(manifest_id))
    # This exact read enters the export. A source writer or corrupt filesystem
    # can replace its envelope after the earlier complete-store verification.
    verify_manifest(envelope, trust, manifest_id)
    parent = destination.parent.resolve(strict=True)
    with directory(parent) as parent_fd:
        parent_identity = os.fstat(parent_fd)
        pending = Path(tempfile.mkdtemp(prefix=".torrent-export-", dir=parent))
        try:
            wire_root = pending / manifest_id
            active = control
            with (
                directory(store) as origin,
                directory(wire_root, create=True) as target,
                writer_lock(target),
            ):
                for file in unique_objects(manifest):
                    copy_verified_object(
                        origin, object_path(file.sha256), target, file, active
                    )
                atomic_bytes(target, manifest_path(manifest_id), envelope)
            inventory = _files(manifest, envelope, manifest_id)
            native_files = engine.file_storage()
            for path, size in sorted(inventory.items()):
                native_files.add_file(path, size)
            creator = engine.create_torrent(
                native_files, CHUNK_SIZE, engine.create_torrent.v1_only
            )
            creator.set_creator("Foxlight model-preservation")
            for value in seeds:
                creator.add_url_seed(value)
            active.phase("checking")
            engine.set_piece_hashes(
                creator, str(pending), lambda _piece: active.checkpoint()
            )
            metadata = creator.generate()
            metadata.pop(b"creation date", None)
            encoded = engine.bencode(metadata)
            if len(encoded) > MAX_METADATA_BYTES:
                raise PreservationError("torrent metainfo exceeds the metadata bound")
            result = TorrentExport(
                manifest_id=manifest_id,
                metainfo_sha256=digest(encoded),
                file_count=len(inventory),
                payload_bytes=sum(inventory.values()),
            )
            with directory(pending) as root:
                atomic_bytes(root, manifest_id + ".torrent", encoded)
                atomic_bytes(root, "torrent.json", encode_record(result))
            active.phase("publishing")
            current = parent.stat()
            if (current.st_dev, current.st_ino) != (
                parent_identity.st_dev,
                parent_identity.st_ino,
            ):
                raise PreservationError("torrent export parent changed")
            os.mkdir(destination.name, mode=0o700, dir_fd=parent_fd)
            try:
                os.rename(
                    pending.name,
                    destination.name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
            except BaseException:
                os.rmdir(destination.name, dir_fd=parent_fd)
                raise
            os.fsync(parent_fd)
            active.mark_committed()
            return result
        finally:
            if pending.exists():
                shutil.rmtree(pending)


@final
class TorrentSeeder:
    """Own a seed-only session and stop its listener before confirming shutdown."""

    def __init__(
        self,
        export_root: Path,
        expected: TorrentExport,
        trust: TrustProfile,
        policy: TorrentSeedPolicy | None = None,
        *,
        control: OperationControl | None = None,
    ) -> None:
        policy = policy or TorrentSeedPolicy()
        active = control or OperationControl()
        host = ipaddress.ip_address(policy.listen_host)
        if not policy.public_swarm and not host.is_loopback:
            raise PreservationError(
                "non-loopback torrent binding needs explicit public participation"
            )
        # The caller owns any generic progress scope for the complete sharing
        # lifetime. Do not emit a terminal verification event before listening.
        manifest = _public_manifest(
            export_root / expected.manifest_id, expected.manifest_id, trust, active
        )
        with directory(export_root) as root:
            metadata = read_metadata(root, expected.manifest_id + ".torrent")
        if digest(metadata) != expected.metainfo_sha256:
            raise RequestChanged("torrent metainfo differs from its selected export")
        with directory(export_root / expected.manifest_id) as root:
            envelope = read_metadata(root, manifest_path(expected.manifest_id))
        engine = torrent_engine()
        decoded = engine.bdecode(metadata)
        # A seed never needs supplier URLs or trackers from a transport file.
        # Public discovery is controlled only by the explicitly selected policy.
        for field in (
            b"url-list",
            b"httpseeds",
            b"announce",
            b"announce-list",
            b"nodes",
        ):
            decoded.pop(field, None)
        native = engine.torrent_info(decoded)
        files = native.files()
        inventory = _files(manifest, envelope, expected.manifest_id)
        if (
            files.num_files() != len(inventory)
            or {
                files.file_path(index): files.file_size(index)
                for index in range(files.num_files())
            }
            != inventory
        ):
            raise PreservationError(
                "torrent files differ from the signed public selection"
            )
        self.policy = policy
        self._session: TorrentSession | None = None
        self._handle: TorrentHandle | None = None
        self._started = time.monotonic()
        self._latest = TorrentObservation()
        self._mutex = threading.RLock()
        self._stop = threading.Event()
        self._failed = False
        self._monitor: threading.Thread | None = None
        self._control = active
        address = f"[{host}]" if host.version == 6 else str(host)
        active.checkpoint()
        session = engine.session(
            {
                "listen_interfaces": f"{address}:{policy.listen_port}",
                "enable_dht": policy.public_swarm,
                "enable_lsd": False,
                "enable_upnp": False,
                "enable_natpmp": False,
                "enable_incoming_utp": False,
                "enable_outgoing_utp": False,
                "upload_rate_limit": policy.upload_rate_bytes,
                "connections_limit": policy.max_connections,
                "alert_mask": 0,
            }
        )
        try:
            flags = engine.torrent_flags.upload_mode | engine.torrent_flags.disable_lsd
            if not policy.public_swarm:
                flags |= (
                    engine.torrent_flags.disable_dht | engine.torrent_flags.disable_pex
                )
            handle = session.add_torrent(
                {"ti": native, "save_path": str(export_root), "flags": flags}
            )
            handle.set_upload_limit(policy.upload_rate_bytes)
            handle.set_max_connections(policy.max_connections)
            self._session = session
            self._handle = handle
            self._monitor = threading.Thread(
                target=self._watch, name="preservation-torrent", daemon=True
            )
            self._monitor.start()
        except BaseException:
            session.pause()
            raise

    @property
    def port(self) -> int:
        """Actual owned listener port, or zero after the session is closed."""
        with self._mutex:
            return self._session.listen_port() if self._session is not None else 0

    def _watch(self) -> None:
        while not self._stop.wait(0.1):
            try:
                self._control.checkpoint()
                self.observe()
            except OperationCancelled:
                self.close()
                return
            except DeadlineExceeded:
                self.close("duration")
                return
            except Exception:
                with self._mutex:
                    self._failed = True
                self.close()
                return

    def observe(self) -> TorrentObservation:
        """Read safe counters; an owned monitor enforces limits without reads."""
        with self._mutex:
            if self._failed:
                raise PreservationError("the seed-only BitTorrent worker failed")
            if self._handle is None:
                return self._latest
            status = self._handle.status()
            if status.errc.value() or status.total_payload_download:
                self._failed = True
                self.close()
                raise PreservationError("the seed-only BitTorrent worker failed")
            self._latest = TorrentObservation(
                ready=status.is_seeding,
                uploaded_payload_bytes=status.total_payload_upload,
                connected_peers=status.num_peers,
            )
            if time.monotonic() - self._started >= self.policy.max_seconds:
                self.close("duration")
            elif status.total_payload_upload >= self.policy.stop_after_payload_bytes:
                self.close("payload_threshold")
            return self._latest

    def close(
        self,
        reason: Literal["stopped", "duration", "payload_threshold"] = "stopped",
    ) -> None:
        """Stop only this owned session and retain its final safe observation."""
        with self._mutex:
            if self._session is None and self._latest.stopped:
                return
            session, handle = self._session, self._handle
            self._session, self._handle = None, None
            if session is not None:
                try:
                    if handle is not None:
                        status = handle.status()
                        self._latest = self._latest.model_copy(
                            update={
                                "uploaded_payload_bytes": status.total_payload_upload
                            }
                        )
                        handle.pause()
                        session.remove_torrent(handle)
                finally:
                    session.pause()
            # Release native references before confirming stop. No global process
            # signal or file deletion is used, and only this session is stopped.
            del handle
            del session
            self._latest = self._latest.model_copy(
                update={
                    "ready": False,
                    "stopped": True,
                    "reason": reason,
                    "connected_peers": 0,
                }
            )
            self._stop.set()
