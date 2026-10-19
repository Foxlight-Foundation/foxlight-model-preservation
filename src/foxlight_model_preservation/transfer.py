"""Bounded receive-only downloads with pinned DNS, range resume and exact hashes."""

from __future__ import annotations

import http.client
import ipaddress
import os
import shutil
import socket
import ssl
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit

from .models import (
    MAX_METADATA_BYTES,
    FileIdentity,
    Manifest,
    PreservationError,
    TrustProfile,
)
from .signing import verify_manifest
from .storage import (
    CHUNK_SIZE,
    atomic_bytes,
    directory,
    file_identity,
    manifest_path,
    object_path,
    open_file,
    parent_directory,
    read_metadata,
    unique_objects,
    verify_object,
    writer_lock,
)


@dataclass(frozen=True)
class TransferPolicy:
    """Explicit bounds; local/private HTTP requires deliberate opt-in."""

    max_bytes: int = 20 * 1024**3
    timeout: float = 30.0
    deadline_seconds: float = 3600.0
    allow_private: bool = False
    allow_http: bool = False
    max_sources: int = 16

    def validate(self) -> None:
        """Reject policies that would accidentally remove transfer bounds."""
        if not (0 < self.max_bytes <= 2**53 - 1):
            raise PreservationError("max_bytes must be positive and bounded")
        if not (0 < self.timeout <= 300 and 0 < self.deadline_seconds <= 86400):
            raise PreservationError("timeouts must be positive and bounded")
        if not 1 <= self.max_sources <= 64:
            raise PreservationError("source limit must be between one and 64")


DEFAULT_POLICY = TransferPolicy()


@dataclass
class Budget:
    """Count actual object bytes across retries, rather than just final output."""

    remaining: int

    def consume(self, count: int) -> None:
        """Reject cumulative transfer beyond the explicitly selected limit."""
        if count > self.remaining:
            raise PreservationError("cumulative transfer budget exceeded")
        self.remaining -= count


class PinnedConnection(http.client.HTTPConnection):
    """Connect to a validated resolved address, preserving TLS hostname checks."""

    def __init__(self, host: str, port: int, policy: TransferPolicy, *, tls: bool):
        super().__init__(host, port, timeout=policy.timeout)
        self._policy = policy
        self._tls = tls

    def connect(self) -> None:
        """Resolve once and pin the actual socket, preventing DNS rebind bypass."""
        addresses = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        last_error: OSError | None = None
        for family, kind, protocol, _name, address in addresses:
            host = str(address[0]).split("%", 1)[0]
            ip = ipaddress.ip_address(host)
            if ip.is_multicast or ip.is_unspecified or ip.is_reserved:
                continue
            if not self._policy.allow_private and not ip.is_global:
                continue
            connection = socket.socket(family, kind, protocol)
            connection.settimeout(self._policy.timeout)
            try:
                connection.connect(address)
                self.sock = (
                    ssl.create_default_context().wrap_socket(
                        connection, server_hostname=self.host
                    )
                    if self._tls
                    else connection
                )
                return
            except OSError as error:
                connection.close()
                last_error = error
        raise PreservationError(
            "no permitted reachable address for mirror"
        ) from last_error


@contextmanager
def response(
    source: str, relative: str, policy: TransferPolicy, *, offset: int = 0
) -> Generator[http.client.HTTPResponse]:
    """Request fixed paths without credentials, redirects, proxies or telemetry."""
    parsed = urlsplit(source)
    if (
        parsed.scheme not in {"https", "http"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(char in source for char in "\r\n\\")
        or (parsed.scheme == "http" and not policy.allow_http)
    ):
        raise PreservationError(
            "mirror must be credential-free HTTPS or explicitly allowed HTTP"
        )
    connection = PinnedConnection(
        parsed.hostname,
        parsed.port or (443 if parsed.scheme == "https" else 80),
        policy,
        tls=parsed.scheme == "https",
    )
    headers = {"Accept-Encoding": "identity", "User-Agent": "model-preserve/0.1"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    path = parsed.path.rstrip("/") + "/" + relative
    try:
        connection.request("GET", path, headers=headers)
        received = connection.getresponse()
        if received.status not in {200, 206}:
            raise PreservationError(
                f"mirror returned HTTP {received.status}; redirects are not followed"
            )
        if received.getheader("Content-Encoding", "identity") != "identity":
            raise PreservationError("encoded mirror responses are not supported")
        yield received
    finally:
        connection.close()


def _is_remote(source: str) -> bool:
    return "://" in source


def _remaining_policy(policy: TransferPolicy, deadline: float) -> TransferPolicy:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PreservationError("transfer deadline exceeded")
    return replace(policy, timeout=min(policy.timeout, remaining))


def _metadata(
    source: str, relative: str, policy: TransferPolicy, deadline: float
) -> bytes:
    policy = _remaining_policy(policy, deadline)
    if not _is_remote(source):
        with directory(Path(source)) as root:
            return read_metadata(root, relative)
    with response(source, relative, policy) as received:
        if received.status != 200:
            raise PreservationError("partial metadata is not accepted")
        data = b""
        while len(data) <= MAX_METADATA_BYTES:
            _remaining_policy(policy, deadline)
            chunk = received.read1(min(65536, MAX_METADATA_BYTES + 1 - len(data)))
            if not chunk:
                break
            data += chunk
    if len(data) > MAX_METADATA_BYTES:
        raise PreservationError("metadata exceeds size limit")
    return data


def _bounded_copy(
    origin: BinaryIO | http.client.HTTPResponse,
    output: BinaryIO,
    count: int,
    deadline: float,
    budget: Budget,
) -> None:
    remaining = count
    while remaining:
        if time.monotonic() > deadline:
            raise PreservationError("transfer deadline exceeded")
        if budget.remaining <= 0:
            raise PreservationError("cumulative transfer budget exceeded")
        amount = min(65536, remaining, budget.remaining)
        chunk = (
            origin.read1(amount)
            if isinstance(origin, http.client.HTTPResponse)
            else origin.read(amount)
        )
        if not chunk:
            raise PreservationError("mirror ended before the expected file length")
        budget.consume(len(chunk))
        output.write(chunk)
        remaining -= len(chunk)
    if origin.read(1):
        budget.consume(1)
        raise PreservationError("mirror exceeded the expected file length")


def _download(
    source: str,
    file: FileIdentity,
    target: int,
    policy: TransferPolicy,
    deadline: float,
    budget: Budget,
) -> None:
    policy = _remaining_policy(policy, deadline)
    pending = ".partial/" + file.sha256
    with open_file(target, pending, write=True) as output:
        output.seek(0, os.SEEK_END)
        offset = output.tell()
        if offset == file.size:
            observed = file_identity(target, pending, file.size)
            if observed.sha256 == file.sha256:
                _publish_partial(target, pending, file)
                return
        if offset >= file.size:
            output.seek(0)
            output.truncate()
            offset = 0
        if not _is_remote(source):
            with (
                directory(Path(source)) as root,
                open_file(root, object_path(file.sha256)) as origin,
            ):
                if os.fstat(origin.fileno()).st_size != file.size:
                    raise PreservationError(
                        "mirror object length differs from signed size"
                    )
                origin.seek(offset)
                _bounded_copy(origin, output, file.size - offset, deadline, budget)
        else:
            with response(
                source, object_path(file.sha256), policy, offset=offset
            ) as received:
                if received.status == 200:
                    offset = 0
                    output.seek(0)
                    output.truncate()
                else:
                    expected = f"bytes {offset}-{file.size - 1}/{file.size}"
                    if received.getheader("Content-Range") != expected:
                        raise PreservationError(
                            "mirror returned an inconsistent content range"
                        )
                length = received.getheader("Content-Length")
                if (
                    length is None
                    or not length.isdecimal()
                    or int(length) != file.size - offset
                ):
                    raise PreservationError(
                        "mirror returned an inconsistent content length"
                    )
                _bounded_copy(received, output, file.size - offset, deadline, budget)
        output.flush()
        os.fsync(output.fileno())
    observed = file_identity(target, pending)
    if (observed.size, observed.sha256) != (file.size, file.sha256):
        with open_file(target, pending, write=True) as output:
            output.truncate(0)
        raise PreservationError("mirror object hash differs from signed identity")
    _publish_partial(target, pending, file)


def _publish_partial(target: int, pending: str, file: FileIdentity) -> None:
    with parent_directory(target, pending) as (pending_parent, pending_name):
        with parent_directory(target, object_path(file.sha256), create=True) as (
            parent,
            name,
        ):
            os.replace(pending_name, name, src_dir_fd=pending_parent, dst_dir_fd=parent)
            os.fsync(parent)
    verify_object(target, file)


def fetch(
    manifest_id: str,
    trust: TrustProfile,
    sources: tuple[str, ...],
    destination: Path,
    policy: TransferPolicy = DEFAULT_POLICY,
) -> Manifest:
    """Fetch an exact trusted bundle; publish only after full verification."""
    policy.validate()
    if not sources or len(sources) > policy.max_sources:
        raise PreservationError("supply a bounded nonempty mirror list")
    relative = manifest_path(manifest_id)
    deadline = time.monotonic() + policy.deadline_seconds
    manifest: Manifest | None = None
    budget = Budget(policy.max_bytes)
    envelope = b""
    failures: list[str] = []
    for index, source in enumerate(sources):
        try:
            envelope = _metadata(source, relative, policy, deadline)
            manifest = verify_manifest(envelope, trust, manifest_id)
            break
        except (OSError, ValueError, http.client.HTTPException) as error:
            failures.append(f"source {index + 1}: {type(error).__name__}")
    if manifest is None:
        raise PreservationError(
            "no authenticated manifest available; " + "; ".join(failures)
        )
    files = unique_objects(manifest)
    if sum(file.size for file in files) > policy.max_bytes:
        raise PreservationError("signed bundle exceeds the selected transfer budget")
    with directory(destination, create=True) as target, writer_lock(target):
        missing: list[FileIdentity] = []
        for file in files:
            try:
                verify_object(target, file)
            except (FileNotFoundError, PreservationError):
                missing.append(file)
        if (
            shutil.disk_usage(destination).free
            < sum(file.size for file in missing) + 16 * CHUNK_SIZE
        ):
            raise PreservationError("destination has insufficient free space")
        for file in missing:
            complete = False
            for source in sources:
                # A resumed prefix may have come from a corrupt peer: allow one
                # clean retry of the next supplier before rejecting that supplier.
                for _attempt in range(2):
                    if time.monotonic() > deadline:
                        raise PreservationError("transfer deadline exceeded")
                    try:
                        _download(source, file, target, policy, deadline, budget)
                        complete = True
                        break
                    except (OSError, ValueError, http.client.HTTPException):
                        continue
                if complete:
                    break
            if not complete:
                raise PreservationError("no mirror supplied a complete verified object")
        for file in files:
            verify_object(target, file)
        atomic_bytes(target, relative, envelope)
    return manifest
