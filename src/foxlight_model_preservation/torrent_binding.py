"""Typed foreign boundary to the optional, maintained libtorrent binding."""

import os
import socket
from collections.abc import Callable
from importlib import import_module
from typing import Protocol, cast

from .models import PreservationError


class TorrentFiles(Protocol):
    """Native file inventory used only for explicitly selected public bytes."""

    def add_file(self, path: str, size: int) -> None: ...
    def num_files(self) -> int: ...
    def file_path(self, index: int) -> str: ...
    def file_size(self, index: int) -> int: ...


class TorrentCreator(Protocol):
    """Native metainfo builder; no networking is involved."""

    def add_url_seed(self, url: str) -> None: ...
    def set_creator(self, creator: str) -> None: ...
    def generate(self) -> dict[bytes, object]: ...


class TorrentCreatorFactory(Protocol):
    """Native constructor and its v1 compatibility flag."""

    v1_only: int

    def __call__(
        self, files: TorrentFiles, piece_size: int, flags: int
    ) -> TorrentCreator: ...


class TorrentHashes(Protocol):
    """Native routing hashes; these never authenticate a model signer."""

    v1: object

    def has_v1(self) -> bool: ...


class TorrentInfo(Protocol):
    """Parsed native metainfo, independently checked against the signed selection."""

    def files(self) -> TorrentFiles: ...
    def info_hashes(self) -> TorrentHashes: ...


class TorrentError(Protocol):
    """Native error code; messages and their possible local paths stay private."""

    def value(self) -> int: ...


class TorrentStatus(Protocol):
    """Bounded counters from a native torrent, without peer identities."""

    total_payload_upload: int
    total_payload_download: int
    num_peers: int
    is_seeding: bool
    errc: TorrentError


class TorrentHandle(Protocol):
    """One exclusively owned torrent in one sharing session."""

    def status(self) -> TorrentStatus: ...
    def pause(self) -> None: ...
    def set_upload_limit(self, limit: int) -> None: ...
    def set_max_connections(self, limit: int) -> None: ...
    def connect_peer(self, peer: tuple[str, int]) -> None: ...


class TorrentSession(Protocol):
    """One native session owned and stopped by the explicit sharing worker."""

    def add_torrent(self, parameters: dict[str, object]) -> TorrentHandle: ...
    def remove_torrent(self, handle: TorrentHandle) -> None: ...
    def pause(self) -> None: ...
    def listen_port(self) -> int: ...


class TorrentFlags(Protocol):
    """Native flags that prevent implicit downloads and discovery."""

    upload_mode: int
    disable_dht: int
    disable_lsd: int
    disable_pex: int


class TorrentEngine(Protocol):
    """The small binding surface this adapter qualifies and uses."""

    __version__: str
    create_torrent: TorrentCreatorFactory
    torrent_flags: TorrentFlags

    def file_storage(self) -> TorrentFiles: ...
    def set_piece_hashes(
        self, creator: TorrentCreator, path: str, callback: Callable[[int], None]
    ) -> None: ...
    def bencode(self, value: dict[bytes, object]) -> bytes: ...
    def bdecode(self, data: bytes) -> dict[bytes, object]: ...
    def torrent_info(self, value: dict[bytes, object]) -> TorrentInfo: ...
    def session(self, settings: dict[str, str | int | bool]) -> TorrentSession: ...


def torrent_engine() -> TorrentEngine:
    """Load the optional binding without starting a session or changing any network."""
    try:
        engine = cast(TorrentEngine, import_module("libtorrent"))
    except ImportError as error:
        raise PreservationError(
            "BitTorrent sharing needs the optional bittorrent dependency"
        ) from error
    if not engine.__version__.startswith("2.0."):
        raise PreservationError(
            "this adapter requires the qualified libtorrent 2.0 line"
        )
    return engine


def torrent_path(files: TorrentFiles, index: int) -> str:
    """A torrent file's path with ``/`` separators, whatever the platform.

    libtorrent reports paths with the platform's own separator (``\\`` on
    Windows); signed selections and manifests always use ``/``.
    """
    return files.file_path(index).replace(os.sep, "/")


def listen_interfaces(*hosts: str, port: int = 0) -> str:
    """libtorrent ``listen_interfaces`` with one port on every interface.

    libtorrent opens a socket per network adapter for ``0.0.0.0``; asked for
    port 0, each adapter gets a different port, and ``listen_port()`` reports
    only the first (on Windows, not even loopback). One free port chosen here
    keeps the reported, mapped and announced port right on every adapter.
    """
    chosen = port or _free_port()
    return ",".join(f"{host}:{chosen}" for host in hosts)


def _free_port() -> int:
    """A port free for both TCP and UDP (BitTorrent and uTP) right now."""
    for _attempt in range(32):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
            tcp.bind(("", 0))
            port = int(tcp.getsockname()[1])
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                    udp.bind(("", port))
            except OSError:
                continue
            return port
    raise PreservationError("no free port for BitTorrent")
