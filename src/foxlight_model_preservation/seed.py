"""Opt-in, bounded read-only serving of explicitly selected verified bundles."""

from __future__ import annotations

import http.server
import os
import re
import socket
import socketserver
import ssl
import threading
import time
from pathlib import Path
from typing import BinaryIO, cast

from .archives import archive_objects, verify_archive
from .models import PreservationError, TrustProfile
from .storage import (
    CHUNK_SIZE,
    directory,
    manifest_path,
    object_path,
    open_file,
    read_metadata,
)

MAX_DOCUMENT_BYTES = 1024 * 1024


class SeedServer(http.server.ThreadingHTTPServer):
    """Serve only selected bundle paths, with bounded threads and total upload."""

    daemon_threads = True
    budget_status = 429

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
        self.documents: dict[str, Path] = {}
        self.allowed: dict[str, int] = {}
        for manifest_id in bundles:
            manifest = verify_archive(store, manifest_id, trust)
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
            for file in archive_objects(manifest):
                self.allowed["/" + object_path(file.sha256)] = file.size
        self._remaining = max_upload_bytes
        self._upload_limit = max_upload_bytes
        self._budget_lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(concurrency)
        super().__init__(address, SeedHandler)

    def server_bind(self) -> None:
        """Bind the requested address without an incidental reverse-DNS lookup.

        Called by server construction with no arguments. Returns None after the
        socket is bound and its numeric address/actual port are recorded. A
        numeric listener works without DNS; an explicitly supplied hostname may
        still need resolution for the bind itself. No advertised public hostname
        or reachability is inferred from the local address.
        """
        socketserver.TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])

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

    def charge_document(self, count: int) -> bool:
        """Charge a small public document against the same upload budget."""
        return self.charge(count)

    def charge(self, count: int, path: str | None = None) -> bool:
        """Reserve actual response bytes globally before they leave the process."""
        with self._budget_lock:
            if count > self._remaining:
                return False
            self._remaining -= count
            return True

    def can_send(self, path: str) -> bool:
        """Allow a chunk; persistent mirrors also recheck local policy."""
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
        document = self.seed.documents.get(self.path)
        if document is not None:
            self._document(document)
            return
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
                if not self.seed.charge(count, self.path):
                    if self.seed.budget_status == 503:
                        self.send_response(503)
                        self.send_header("Content-Length", "0")
                        self.send_header("Retry-After", "60")
                        self.end_headers()
                    else:
                        self.send_error(self.seed.budget_status)
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

    def _document(self, path: Path) -> None:
        """Serve one small public JSON document that its owner rewrites atomically."""
        try:
            with directory(path.parent) as root, open_file(root, path.name) as stream:
                data = stream.read(MAX_DOCUMENT_BYTES + 1)
        except OSError:
            self.send_error(404)
            return
        if len(data) > MAX_DOCUMENT_BYTES or not self.seed.charge_document(len(data)):
            self.send_error(503)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "max-age=300")
        self.end_headers()
        self.wfile.write(data)

    def _send(self, stream: BinaryIO, offset: int, count: int) -> None:
        stream.seek(offset)
        remaining = count
        while remaining:
            if not self.seed.can_send(self.path):
                self.close_connection = True
                return
            chunk = stream.read(min(remaining, CHUNK_SIZE))
            if not chunk:
                self.close_connection = True
                return
            # SocketWriter uses sendall: its timeout covers the entire write,
            # even when the receiver keeps making progress. Bound network writes
            # separately from disk reads so a slow route need not drain 1 MiB
            # within one timeout. Keep the original socket/concurrency limits.
            view = memoryview(chunk)
            next_check = time.monotonic() + 0.1
            for start in range(0, len(view), 16 * 1024):
                if time.monotonic() >= next_check:
                    if not self.seed.can_send(self.path):
                        self.close_connection = True
                        return
                    next_check = time.monotonic() + 0.1
                self.wfile.write(view[start : start + 16 * 1024])
            remaining -= len(chunk)
