"""Explicit, pinned native acquisition without a system installer or peer effects."""

from __future__ import annotations

import hashlib
import http.client
import os
import platform
import shutil
import stat
import tarfile
import tempfile
import time
from collections.abc import Generator
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urljoin, urlsplit

from pydantic import Field, TypeAdapter

from .models import PreservationError, Record
from .operations import OperationControl
from .peer_runtime import KUBO_VERSION
from .storage import CHUNK_SIZE, directory, file_identity, open_file, writer_lock
from .transfer import PinnedConnection, TransferPolicy

_ORIGINS = (
    "https://dist.ipfs.tech/kubo/v0.43.1/",
    "https://github.com/ipfs/kubo/releases/download/v0.43.1/",
)
_HOSTS = {"dist.ipfs.tech", "github.com", "release-assets.githubusercontent.com"}
_MAX_ARCHIVE = 128 * 1024**2
_MAX_EXECUTABLE = 256 * 1024**2


class _DownloadUnavailable(PreservationError):
    pass


class UnsupportedRuntimePlatform(PreservationError):
    """Managed acquisition has no pinned build; an existing native runtime may work."""


class RuntimeArtifact(Record):
    """Release-reviewed native identities; remote metadata cannot change these pins."""

    archive_sha512: str = Field(pattern=r"^[0-9a-f]{128}$")
    archive_bytes: int = Field(gt=0, le=_MAX_ARCHIVE)
    executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    executable_bytes: int = Field(gt=0, le=_MAX_EXECUTABLE)


class RuntimePlan(Record):
    """Offline installation review for the current host, independent of peer grants."""

    version: str
    target: str
    filename: str
    artifact: RuntimeArtifact
    download_urls: tuple[str, ...]
    network_requests: int = 0


def runtime_plan() -> RuntimePlan:
    """Select the fixed macOS/Linux ARM64 or AMD64 build without any network effects."""
    system = platform.system().lower()
    machine = {"aarch64": "arm64", "x86_64": "amd64"}.get(
        platform.machine().lower(), platform.machine().lower()
    )
    target = system + "-" + machine
    pins = TypeAdapter(dict[str, RuntimeArtifact]).validate_json(
        files("foxlight_model_preservation")
        .joinpath("resources/kubo_v0_43_1.json")
        .read_bytes()
    )
    if target not in pins:
        raise UnsupportedRuntimePlatform(
            "managed peer runtime supports macOS/Linux ARM64 and AMD64"
        )
    filename = f"kubo_v{KUBO_VERSION}_{target}.tar.gz"
    return RuntimePlan(
        version=KUBO_VERSION,
        target=target,
        filename=filename,
        artifact=pins[target],
        download_urls=tuple(origin + filename for origin in _ORIGINS),
    )


def default_runtime_directory() -> Path:
    """Return private per-user storage; no PATH or system-directory mutation is used."""
    return Path.home() / ".local/state/foxlight-preservation/runtime"


def _generation(plan: RuntimePlan) -> str:
    return f"kubo-{plan.version}-{plan.target}"


def managed_executable(root: Path) -> str | None:
    """Rehash managed executable bytes against packaged pins before returning a path.

    Missing installations or unsupported managed platforms return None.
    Corruption, non-private storage, links and
    incomplete generations refuse rather than falling back to another executable.
    This function never executes the binary or starts a native daemon.
    """
    try:
        plan = runtime_plan()
    except UnsupportedRuntimePlatform:
        return None
    if not root.exists():
        if root.is_symlink():
            raise PreservationError("managed runtime directory must not be a link")
        return None
    with directory(root) as descriptor:
        _private(descriptor)
        generation = _generation(plan)
        try:
            observed = file_identity(
                descriptor, generation + "/ipfs", plan.artifact.executable_bytes
            )
        except FileNotFoundError:
            if (root / generation).exists() or (root / generation).is_symlink():
                raise PreservationError(
                    "managed runtime generation is incomplete"
                ) from None
            return None
        if observed.sha256 != plan.artifact.executable_sha256:
            raise PreservationError("managed runtime executable checksum mismatch")
        with open_file(descriptor, generation + "/ipfs") as stream:
            info = os.fstat(stream.fileno())
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise PreservationError(
                    "managed runtime executable permissions changed"
                )
    return str(root.absolute() / generation / "ipfs")


def _private(descriptor: int) -> None:
    info = os.fstat(descriptor)
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PreservationError(
            "managed runtime storage must be private and owned by this user"
        )


@contextmanager
def _response(url: str, end: float) -> Generator[http.client.HTTPResponse]:
    for _ in range(4):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in _HOSTS
            or parsed.port not in {None, 443}
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise PreservationError(
                "native download redirected outside approved HTTPS origins"
            )
        remaining = end - time.monotonic()
        if remaining <= 0:
            raise PreservationError("native download deadline exceeded")
        connection = PinnedConnection(
            parsed.hostname, 443, TransferPolicy(timeout=min(10, remaining)), tls=True
        )
        try:
            path = parsed.path + ("?" + parsed.query if parsed.query else "")
            try:
                connection.request("GET", path, headers={"Accept-Encoding": "identity"})
                received = connection.getresponse()
            except (OSError, http.client.HTTPException, PreservationError) as error:
                raise _DownloadUnavailable(
                    "native download origin is unavailable"
                ) from error
            if received.status in {301, 302, 303, 307, 308}:
                location = received.getheader("Location")
                if location is None:
                    raise PreservationError(
                        "native download redirect has no destination"
                    )
                url = urljoin(url, location)
                continue
            if (
                received.status != 200
                or received.getheader("Content-Encoding", "identity") != "identity"
            ):
                raise _DownloadUnavailable(
                    "native download requires a complete unencoded HTTPS response"
                )
            yield received
            return
        finally:
            connection.close()
    raise PreservationError("native download redirect limit exceeded")


def _copy_archive(
    source: BinaryIO | http.client.HTTPResponse,
    destination: BinaryIO,
    plan: RuntimePlan,
    control: OperationControl,
    end: float,
) -> None:
    checksum = hashlib.sha512()
    count = 0
    while True:
        control.checkpoint()
        if time.monotonic() >= end:
            raise PreservationError("native acquisition deadline exceeded")
        chunk = source.read(min(CHUNK_SIZE, plan.artifact.archive_bytes + 1 - count))
        if not chunk:
            break
        count += len(chunk)
        if count > plan.artifact.archive_bytes:
            raise PreservationError("native archive exceeds pinned byte length")
        checksum.update(chunk)
        _ = destination.write(chunk)
        control.advance(copied=len(chunk))
    if (
        count != plan.artifact.archive_bytes
        or checksum.hexdigest() != plan.artifact.archive_sha512
    ):
        raise PreservationError("native archive length or checksum mismatch")
    destination.flush()
    os.fsync(destination.fileno())


def install_runtime(
    root: Path,
    *,
    allow_download: bool = False,
    archive: Path | None = None,
    control: OperationControl | None = None,
) -> str:
    """Install one authenticated native build atomically into private local storage.

    Network acquisition needs explicit allow_download; an offline archive uses
    identical embedded pins. Imported model/source files cannot select URLs,
    checksums or executables. No installation script or extracted binary runs.
    Cancellation/failure publishes no executable generation; reruns reuse only
    a fully verified existing generation. No system packages, PATH, profiles,
    sharing, peer connections or model trust are changed.
    """
    active = control or OperationControl()
    active.checkpoint()
    plan = runtime_plan()
    if archive is None and not allow_download:
        raise PreservationError(
            "native runtime download needs explicit permission or an offline archive"
        )
    with directory(root, create=True) as descriptor, writer_lock(descriptor):
        _private(descriptor)
        existing = managed_executable(root)
        if existing is not None:
            return existing
        if (
            shutil.disk_usage(root).free
            < plan.artifact.archive_bytes
            + plan.artifact.executable_bytes
            + 16 * 1024**2
        ):
            raise PreservationError(
                "insufficient free space for native runtime staging"
            )
        end = time.monotonic() + 300
        active.limit_deadline(end)
        with tempfile.TemporaryDirectory(
            prefix=".native-pending-", dir=root
        ) as temporary:
            stage = Path(temporary)
            compressed = stage / "archive.tar.gz"
            with compressed.open("x+b") as destination:
                if archive is not None:
                    with (
                        directory(archive.absolute().parent) as parent,
                        open_file(parent, archive.name) as source,
                    ):
                        _copy_archive(source, destination, plan, active, end)
                else:
                    # Availability errors can use the other fixed official origin.
                    # A completed but invalid archive refuses immediately.
                    for index, url in enumerate(plan.download_urls):
                        active.checkpoint()
                        try:
                            with _response(url, end) as remote:
                                _copy_archive(remote, destination, plan, active, end)
                            break
                        except (
                            OSError,
                            http.client.HTTPException,
                            _DownloadUnavailable,
                        ):
                            if index == len(plan.download_urls) - 1:
                                raise
                            _ = destination.seek(0)
                            _ = destination.truncate()
            publication = stage / "generation"
            publication.mkdir(mode=0o700)
            _extract(compressed, publication, plan, active)
            active.checkpoint()
            with directory(publication) as generation:
                os.fsync(generation)
            active.checkpoint()
            os.rename(publication, _generation(plan), dst_dir_fd=descriptor)
            os.fsync(descriptor)
            active.mark_committed()
    result = managed_executable(root)
    if result is None:
        raise PreservationError("native runtime publication failed")
    return result


def _extract(
    archive: Path, destination: Path, plan: RuntimePlan, control: OperationControl
) -> None:
    # Never invoke tar extraction, installers, hooks or archive-supplied paths.
    # Copy only the regular executable after authenticating the complete archive.
    seen = False
    with tarfile.open(archive, mode="r:gz") as bundle:
        for index, entry in enumerate(bundle):
            control.checkpoint()
            if index > 32:
                raise PreservationError("native archive has too many entries")
            if entry.name != "kubo/ipfs":
                continue
            if (
                seen
                or not entry.isfile()
                or entry.size != plan.artifact.executable_bytes
            ):
                raise PreservationError("native archive executable is invalid")
            seen = True
            source = bundle.extractfile(entry)
            if source is None:
                raise PreservationError("native archive executable is missing")
            checksum = hashlib.sha256()
            with source, (destination / "ipfs").open("xb") as output:
                while chunk := source.read(CHUNK_SIZE):
                    control.checkpoint()
                    checksum.update(chunk)
                    _ = output.write(chunk)
                if checksum.hexdigest() != plan.artifact.executable_sha256:
                    raise PreservationError("native executable checksum mismatch")
                os.fchmod(output.fileno(), 0o700)
                output.flush()
                os.fsync(output.fileno())
    if not seen:
        raise PreservationError("native archive executable is missing")
    notice = (
        files("foxlight_model_preservation")
        .joinpath("resources/kubo-NOTICE.txt")
        .read_bytes()
    )
    with (destination / "NOTICE.txt").open("xb") as output:
        _ = output.write(notice)
        output.flush()
        os.fsync(output.fileno())
