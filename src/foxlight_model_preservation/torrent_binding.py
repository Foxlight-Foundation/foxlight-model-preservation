"""Typed foreign boundary to the optional, maintained libtorrent binding."""

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


class TorrentInfo(Protocol):
    """Parsed native metainfo, independently checked against the signed selection."""

    def files(self) -> TorrentFiles: ...


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
