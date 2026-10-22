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
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit

from . import __version__
from .ledger import TransferBudget, receipt_path
from .models import (
    MAX_METADATA_BYTES,
    FileIdentity,
    Manifest,
    PreservationError,
    TrustProfile,
)
from .operations import (
    BudgetExceeded,
    DeadlineExceeded,
    OperationControl,
    OperationError,
    OperationPlan,
)
from .signing import canonical_bytes, digest, verify_manifest
from .storage import (
    CHUNK_SIZE,
    atomic_bytes,
    directory,
    file_identity,
    manifest_path,
    object_path,
    observe_objects,
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


class MirrorHTTPError(PreservationError):
    """Retain an HTTP status for callers implementing standard metadata protocols."""

    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(
            f"mirror returned HTTP {status_code}; redirects are not followed"
        )


class MirrorProtocolError(PreservationError):
    """An invalid or truncated HTTP response; callers may try another supplier."""


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
    headers = {
        "Accept-Encoding": "identity",
        "User-Agent": "model-preserve/" + __version__,
    }
    if offset:
        headers["Range"] = f"bytes={offset}-"
    path = parsed.path.rstrip("/") + "/" + relative
    try:
        connection.request("GET", path, headers=headers)
        received = connection.getresponse()
        if received.status not in {200, 206}:
            raise MirrorHTTPError(received.status)
        if received.getheader("Content-Encoding", "identity") != "identity":
            raise PreservationError("encoded mirror responses are not supported")
        yield received
    except http.client.HTTPException as error:
        raise MirrorProtocolError("mirror HTTP response framing failed") from error
    finally:
        connection.close()


def _is_remote(source: str) -> bool:
    return "://" in source


def _remaining_policy(policy: TransferPolicy, deadline: float) -> TransferPolicy:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DeadlineExceeded("transfer deadline exceeded")
    return replace(policy, timeout=min(policy.timeout, remaining))


def _metadata(
    source: str,
    relative: str,
    policy: TransferPolicy,
    deadline: float,
    control: OperationControl,
) -> bytes:
    control.checkpoint()
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
            control.checkpoint()
    if len(data) > MAX_METADATA_BYTES:
        raise PreservationError("metadata exceeds size limit")
    return data


def _bounded_copy(
    origin: BinaryIO | http.client.HTTPResponse,
    output: BinaryIO,
    count: int,
    deadline: float,
    budget: TransferBudget,
    control: OperationControl,
) -> None:
    remaining = count
    while remaining:
        if time.monotonic() > deadline:
            raise DeadlineExceeded("transfer deadline exceeded")
        control.checkpoint()
        if budget.remaining <= 0:
            raise BudgetExceeded("cumulative transfer budget exceeded")
        amount = min(65536, remaining, budget.remaining)
        budget.reserve(amount)
        try:
            chunk = (
                origin.read1(amount)
                if isinstance(origin, http.client.HTTPResponse)
                else origin.read(amount)
            )
        except BaseException:
            budget.abandon()
            raise
        if not chunk:
            raise PreservationError("mirror ended before the expected file length")
        budget.consume(len(chunk))
        control.advance(received=len(chunk))
        output.write(chunk)
        remaining -= len(chunk)
    # HTTP framing was checked against the exact signed size; filesystem input
    # has its full length checked separately. No unbudgeted overrun probe.


def _download(
    source: str,
    file: FileIdentity,
    target: int,
    policy: TransferPolicy,
    deadline: float,
    budget: TransferBudget,
    control: OperationControl,
) -> None:
    control.checkpoint()
    policy = _remaining_policy(policy, deadline)
    pending = ".partial/" + file.sha256
    with open_file(target, pending, write=True) as output:
        output.seek(0, os.SEEK_END)
        offset = output.tell()
        if offset == file.size:
            observed = file_identity(target, pending, file.size, control=control)
            if observed.sha256 == file.sha256:
                _publish_partial(target, pending, file, control)
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
                _bounded_copy(
                    origin, output, file.size - offset, deadline, budget, control
                )
                if os.fstat(origin.fileno()).st_size != file.size:
                    raise PreservationError(
                        "mirror object changed length during transfer"
                    )
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
                _bounded_copy(
                    received, output, file.size - offset, deadline, budget, control
                )
        output.flush()
        os.fsync(output.fileno())
    control.phase("checking")
    observed = file_identity(target, pending, control=control)
    if (observed.size, observed.sha256) != (file.size, file.sha256):
        with open_file(target, pending, write=True) as output:
            output.truncate(0)
        raise PreservationError("mirror object hash differs from signed identity")
    _publish_partial(target, pending, file, control)


def _publish_partial(
    target: int, pending: str, file: FileIdentity, control: OperationControl
) -> None:
    control.checkpoint()
    with parent_directory(target, pending) as (pending_parent, pending_name):
        with parent_directory(target, object_path(file.sha256), create=True) as (
            parent,
            name,
        ):
            os.replace(pending_name, name, src_dir_fd=pending_parent, dst_dir_fd=parent)
            os.fsync(parent)
    verify_object(target, file, control=control)


def request_digest(
    manifest_id: str,
    trust: TrustProfile,
    sources: tuple[str, ...],
    destination: Path,
    policy: TransferPolicy,
) -> str:
    """Bind exact content, trust, source order, destination and policy without leaks."""
    return digest(
        canonical_bytes(
            {
                "manifest": manifest_id,
                "trust": trust.model_dump(mode="json"),
                "sources": list(sources),
                "destination": str(destination.resolve()),
                "policy": asdict(policy),
            }
        )
    )


def _load_manifest(
    manifest_id: str,
    trust: TrustProfile,
    sources: tuple[str, ...],
    policy: TransferPolicy,
    deadline: float,
    control: OperationControl,
) -> tuple[Manifest, bytes]:
    policy.validate()
    if not sources or len(sources) > policy.max_sources:
        raise PreservationError("supply a bounded nonempty mirror list")
    relative = manifest_path(manifest_id)
    control.phase("inventory")
    failures: list[str] = []
    for index, source in enumerate(sources):
        try:
            envelope = _metadata(source, relative, policy, deadline, control)
            manifest = verify_manifest(envelope, trust, manifest_id)
            if sum(file.size for file in unique_objects(manifest)) > policy.max_bytes:
                raise BudgetExceeded(
                    "signed bundle exceeds the selected transfer budget"
                )
            control.checkpoint()
            return manifest, envelope
        except OperationError:
            raise
        except (OSError, ValueError, http.client.HTTPException) as error:
            failures.append(f"source {index + 1}: {type(error).__name__}")
    raise PreservationError(
        "no authenticated manifest available; " + "; ".join(failures)
    )


def fetch(
    manifest_id: str,
    trust: TrustProfile,
    sources: tuple[str, ...],
    destination: Path,
    policy: TransferPolicy = DEFAULT_POLICY,
    *,
    control: OperationControl | None = None,
    operation_id: str | None = None,
) -> Manifest:
    """Receive exact trusted content with optional control and persistent allowance.

    Reuse the same operation_id and immutable inputs after interruption. Local
    charges persist before object reads; a new ID is a new authorized allowance.
    No operation_id retains the original per-invocation accounting behavior.
    """
    with (control or OperationControl()).operation("fetch") as active:
        policy.validate()
        if operation_id is not None:
            receipt_path(operation_id)
        deadline = time.monotonic() + policy.deadline_seconds
        active.limit_deadline(deadline)
        manifest, envelope = _load_manifest(
            manifest_id, trust, sources, policy, deadline, active
        )
        files = unique_objects(manifest)
        active.checkpoint()
        with directory(destination, create=True) as target, writer_lock(target):
            pinned = os.fstat(target)
            binding = digest(
                canonical_bytes(
                    {
                        "request": request_digest(
                            manifest_id, trust, sources, destination, policy
                        ),
                        "device": str(pinned.st_dev),
                        "inode": str(pinned.st_ino),
                    }
                )
            )
            budget = TransferBudget(
                policy.max_bytes,
                root=target,
                operation_id=operation_id,
                request_digest=binding,
            )
            missing, _reused, partial = observe_objects(
                target, files, active, include_partials=True
            )
            additional = sum(file.size for file in missing) - partial
            if (
                missing
                and shutil.disk_usage(target).free < additional + 16 * CHUNK_SIZE
            ):
                raise PreservationError("destination has insufficient free space")
            for file in missing:
                complete = False
                for source in sources:
                    # A corrupt resumed prefix permits one clean retry from
                    # this supplier. Cancellation/deadlines/budgets never retry.
                    for _attempt in range(2):
                        active.phase("downloading")
                        try:
                            _download(
                                source, file, target, policy, deadline, budget, active
                            )
                            complete = True
                            break
                        except OperationError:
                            raise
                        except (OSError, ValueError, http.client.HTTPException):
                            continue
                    if complete:
                        break
                if not complete:
                    raise PreservationError(
                        "no mirror supplied a complete verified object"
                    )
            active.phase("checking")
            for file in files:
                verify_object(target, file, control=active)
            active.phase("publishing")
            atomic_bytes(target, manifest_path(manifest_id), envelope)
            active.mark_committed()
        return manifest


def plan_fetch(
    manifest_id: str,
    trust: TrustProfile,
    sources: tuple[str, ...],
    destination: Path,
    policy: TransferPolicy = DEFAULT_POLICY,
    *,
    control: OperationControl | None = None,
) -> OperationPlan:
    """Authenticate metadata and observe space without fetching weights or writing.

    Remote sources require bounded metadata requests. Partial bytes are only
    potential space reuse; their integrity is not established by this plan.
    """
    with (control or OperationControl()).operation("plan_fetch") as active:
        deadline = time.monotonic() + policy.deadline_seconds
        active.limit_deadline(deadline)
        manifest, envelope = _load_manifest(
            manifest_id, trust, sources, policy, deadline, active
        )
        files = unique_objects(manifest)
        reused = partial = 0
        if destination.exists() or destination.is_symlink():
            with directory(destination) as root:
                _missing, reused, partial = observe_objects(
                    root, files, active, include_partials=True
                )
        unique = sum(file.size for file in files)
        return OperationPlan(
            operation="fetch",
            manifest_id=manifest_id,
            request_digest=request_digest(
                manifest_id, trust, sources, destination, policy
            ),
            logical_bytes=manifest.total_bytes,
            unique_bytes=unique,
            reused_bytes=reused,
            partial_bytes=partial,
            additional_bytes=unique - reused - partial,
            metadata_bytes=len(envelope),
            reserve_bytes=16 * CHUNK_SIZE,
        )
