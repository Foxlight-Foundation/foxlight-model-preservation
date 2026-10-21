"""Opt-in, bounded read-only serving of explicitly selected verified bundles."""

from __future__ import annotations

import http.server
import os
import re
import socket
import ssl
import threading
from pathlib import Path
from typing import BinaryIO, cast

from .models import PreservationError, TrustProfile
from .storage import (
    CHUNK_SIZE,
    directory,
    manifest_path,
    object_path,
    open_file,
    read_metadata,
    unique_objects,
    verify_store,
)


class SeedServer(http.server.ThreadingHTTPServer):
    """Serve only selected bundle paths, with bounded threads and total upload."""

    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        store: Path,
        bundles: tuple[str, ...],
        trust: TrustProfile,
        *,
        public: bool = False,
        max_upload_bytes: int = 1024**3,
        concurrency: int = 4,
        tls_context: ssl.SSLContext | None = None,
    ):
        if not bundles or max_upload_bytes <= 0 or not 1 <= concurrency <= 32:
            raise PreservationError(
                "explicit bundles and positive bounded serving limits required"
            )
        self.tls_context = tls_context
        self.store = store
        self.allowed: dict[str, int] = {}
        for manifest_id in bundles:
            manifest = verify_store(store, manifest_id, trust)
            if public and any(
                member.intake.audience != "public" for member in manifest.members
            ):
                raise PreservationError(
                    "public seeding requires public intake for every member"
                )
            with directory(store) as root:
                self.allowed["/" + manifest_path(manifest_id)] = len(
                    read_metadata(root, manifest_path(manifest_id))
                )
            for file in unique_objects(manifest):
                self.allowed["/" + object_path(file.sha256)] = file.size
        self._remaining = max_upload_bytes
        self._upload_limit = max_upload_bytes
        self._budget_lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(concurrency)
        super().__init__(address, SeedHandler)

    def process_request(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: tuple[str, int],
    ) -> None:
        """Bound worker creation before spawning a thread, including slow clients."""
        if not isinstance(request, socket.socket):
            request[1].close()
            return
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: tuple[str, int],
    ) -> None:
        """Release serving capacity when each bounded request finishes."""
        try:
            if not isinstance(request, socket.socket):
                request[1].close()
                return
            request.settimeout(30)
            if self.tls_context is not None:
                # Handshake inside the bounded worker, never in the accept loop.
                request = self.tls_context.wrap_socket(request, server_side=True)
            super().process_request_thread(request, client_address)
        except OSError:
            if isinstance(request, socket.socket):
                self.shutdown_request(request)
        finally:
            self._slots.release()

    def charge(self, count: int) -> bool:
        """Reserve actual response bytes globally before they leave the process."""
        with self._budget_lock:
            if count > self._remaining:
                return False
            self._remaining -= count
            return True

    @property
    def reserved_upload_bytes(self) -> int:
        """Return response-body allowance reserved by this serving session.

        Reads are thread-safe and do not start serving or change the budget.
        Interrupted responses retain their whole reservation, so this is a
        conservative budget charge, not a count of bytes received by peers.
        HTTP/TLS headers and transport overhead are excluded.
        """
        with self._budget_lock:
            return self._upload_limit - self._remaining


class SeedHandler(http.server.BaseHTTPRequestHandler):
    """No directory listing, management, proxying, uploads, or request-history log."""

    @property
    def seed(self) -> SeedServer:
        """Narrow the server associated with this handler."""
        return cast(SeedServer, self.server)

    def setup(self) -> None:
        """Bound slow request reads and writes as well as accepted concurrency."""
        self.request.settimeout(30)
        super().setup()

    def log_message(self, format: str, *args: object) -> None:
        """Keep IP/model request history out of the default seed log."""

    def do_GET(self) -> None:
        """Serve only exact allowlisted regular files with single-range support."""
        size = self.seed.allowed.get(self.path)
        if size is None:
            self.send_error(404)
            return
        start, end = 0, size - 1
        requested = self.headers.get("Range")
        if requested is not None:
            matched = re.fullmatch(r"bytes=([0-9]+)-([0-9]*)", requested)
            if matched is None or len(requested) > 100:
                self.send_error(416)
                return
            start = int(matched[1])
            end = int(matched[2]) if matched[2] else end
            if not (0 <= start <= end < size):
                self.send_error(416)
                return
        try:
            with (
                directory(self.seed.store) as root,
                open_file(root, self.path[1:]) as stream,
            ):
                if os.fstat(stream.fileno()).st_size != size:
                    raise PreservationError("selected file changed")
                count = end - start + 1
                if not self.seed.charge(count):
                    self.send_error(429)
                    return
                self.send_response(206 if requested is not None else 200)
                self.send_header("Content-Length", str(count))
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "no-store")
                if requested is not None:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                self._send(stream, start, count)
        except (OSError, PreservationError):
            self.close_connection = True

    def _send(self, stream: BinaryIO, offset: int, count: int) -> None:
        stream.seek(offset)
        remaining = count
        while remaining:
            chunk = stream.read(min(remaining, CHUNK_SIZE))
            if not chunk:
                self.close_connection = True
                return
            self.wfile.write(chunk)
            remaining -= len(chunk)
