"""Descriptor-relative filesystem operations and verified immutable publication."""

from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import stat
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .models import (
    MAX_METADATA_BYTES,
    FileIdentity,
    Manifest,
    ManifestMember,
    PreservationError,
    Recipe,
    TrustProfile,
    canonical_path,
)
from .operations import OperationControl, OperationError, OperationPlan, RequestChanged
from .signing import (
    Envelope,
    canonical_bytes,
    digest,
    encode_record,
    sign_manifest,
    strict_json,
    verify_manifest,
)

CHUNK_SIZE = 1024 * 1024


def object_path(sha256: str) -> str:
    """Map a validated content digest to its portable mirror path."""
    _require_digest(sha256)
    return "objects/sha256/" + sha256


def manifest_path(manifest_id: str) -> str:
    """Map an exact manifest identity to its standard DSSE envelope path."""
    _require_digest(manifest_id)
    return "manifests/" + manifest_id + ".json"


def _require_digest(value: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise PreservationError("expected a complete lowercase SHA-256 identity")


@contextmanager
def directory(path: Path, *, create: bool = False) -> Generator[int]:
    """Pin an explicitly selected directory; nested paths never follow symlinks."""
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def parent_directory(
    root: int, path: str, *, create: bool = False
) -> Generator[tuple[int, str]]:
    """Walk canonical path components through pinned non-symlink directory handles."""
    parts = canonical_path(path).split("/")
    descriptor = os.dup(root)
    try:
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                except FileExistsError:
                    pass
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = child
        yield descriptor, parts[-1]
    finally:
        os.close(descriptor)


@contextmanager
def open_file(root: int, path: str, *, write: bool = False) -> Generator[BinaryIO]:
    """Open only a regular file under a pinned root, rejecting every symlink."""
    with parent_directory(root, path, create=write) as (parent, name):
        flags = (
            os.O_NOFOLLOW
            | os.O_NONBLOCK
            | (os.O_RDWR | os.O_CREAT if write else os.O_RDONLY)
        )
        descriptor = os.open(name, flags, 0o600, dir_fd=parent)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise PreservationError("only regular files can be preserved")
            stream = os.fdopen(descriptor, "r+b" if write else "rb")
        except BaseException:
            os.close(descriptor)
            raise
        with stream:
            yield stream
            if write:
                stream.flush()
                os.fsync(stream.fileno())
                os.fsync(parent)


@contextmanager
def writer_lock(root: int) -> Generator[None]:
    """Fence concurrent writers; kernel releases the lock after a process crash."""
    with open_file(root, ".write.lock", write=True) as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise PreservationError("another writer owns this destination") from error
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def publish_envelope(root: int, manifest_id: str, envelope: bytes) -> None:
    """Publish an authenticated envelope without discarding other signers.

    When the store already holds a parseable envelope for the identical payload,
    its signatures are retained after the new ones, so a re-signed capture or a
    fetch under a different trust profile cannot strip another holder's
    signatures. Unverifiable extra signatures are ignored by every verifier.
    Anything unparseable, oversized or for a different payload is replaced.
    """
    path = manifest_path(manifest_id)
    try:
        with open_file(root, path) as stream:
            existing = stream.read(MAX_METADATA_BYTES + 1)
        retained = Envelope.model_validate_json(canonical_bytes(strict_json(existing)))
    except (OSError, ValueError):
        retained = None
    if retained is not None:
        incoming = Envelope.model_validate_json(canonical_bytes(strict_json(envelope)))
        if (retained.payloadType, retained.payload) == (
            incoming.payloadType,
            incoming.payload,
        ):
            signatures = list(incoming.signatures)
            signatures.extend(
                item for item in retained.signatures if item not in signatures
            )
            if len(signatures) <= 32:
                merged = encode_record(
                    incoming.model_copy(update={"signatures": tuple(signatures)})
                )
                if len(merged) <= MAX_METADATA_BYTES:
                    envelope = merged
    atomic_bytes(root, path, envelope)


def atomic_bytes(root: int, path: str, data: bytes) -> None:
    """Durably publish exact bytes by same-directory replacement after fsync."""
    with parent_directory(root, path, create=True) as (parent, name):
        temporary = ".pending-" + uuid.uuid4().hex
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass


def read_metadata(root: int, path: str) -> bytes:
    """Read bounded regular metadata without trusting its reported file size."""
    with open_file(root, path) as stream:
        data = stream.read(MAX_METADATA_BYTES + 1)
    if len(data) > MAX_METADATA_BYTES:
        raise PreservationError("metadata size exceeds limit")
    return data


def file_identity(
    root: int,
    path: str,
    expected_size: int | None = None,
    *,
    control: OperationControl | None = None,
) -> FileIdentity:
    """Hash a stable source snapshot and reject mutation during the read."""
    if control is not None:
        control.checkpoint()
    with open_file(root, path) as stream:
        before = os.fstat(stream.fileno())
        if expected_size is not None and before.st_size != expected_size:
            raise PreservationError("file length differs from signed size")
        hasher = hashlib.sha256()
        size = 0
        while chunk := stream.read(min(CHUNK_SIZE, before.st_size + 1 - size)):
            hasher.update(chunk)
            size += len(chunk)
            if control is not None:
                control.advance(hashed=len(chunk))
        after = os.fstat(stream.fileno())
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ) or size != before.st_size:
        raise PreservationError("source file changed while being captured")
    return FileIdentity(path=path, size=size, sha256=hasher.hexdigest())


def verify_object(
    root: int, file: FileIdentity, *, control: OperationControl | None = None
) -> None:
    """Verify actual bytes, including length, before a complete-generation claim."""
    observed = file_identity(root, object_path(file.sha256), file.size, control=control)
    if observed.size != file.size or observed.sha256 != file.sha256:
        raise PreservationError("stored object failed integrity verification")


def unique_objects(manifest: Manifest) -> tuple[FileIdentity, ...]:
    """Deduplicate content while rejecting conflicting signed size declarations."""
    files: dict[str, FileIdentity] = {}
    for member in manifest.members:
        for file in member.files:
            previous = files.get(file.sha256)
            if previous is not None and previous.size != file.size:
                raise PreservationError("conflicting sizes for one content identity")
            files[file.sha256] = file
    return tuple(files.values())


def observe_objects(
    root: int,
    files: tuple[FileIdentity, ...],
    control: OperationControl,
    *,
    include_partials: bool = False,
) -> tuple[tuple[FileIdentity, ...], int, int]:
    """Read missing objects, verified reuse bytes and unverified partial bytes.

    The caller pins the root. This observation creates no files or locks and
    cannot reserve capacity or prove that a partial prefix is correct.
    """
    control.phase("checking")
    missing: list[FileIdentity] = []
    reused = 0
    partial = 0
    for file in files:
        try:
            verify_object(root, file, control=control)
        except OperationError:
            raise
        except (FileNotFoundError, PreservationError):
            missing.append(file)
            if include_partials:
                try:
                    with open_file(root, ".partial/" + file.sha256) as stream:
                        partial += min(os.fstat(stream.fileno()).st_size, file.size)
                except FileNotFoundError:
                    pass
        else:
            reused += file.size
            control.advance(reused=file.size)
    return tuple(missing), reused, partial


def plan_pack(
    recipe: Recipe,
    sources: dict[str, Path],
    destination: Path,
    *,
    control: OperationControl | None = None,
) -> OperationPlan:
    """Hash sources and observe reuse without signing or writing a store."""
    with (control or OperationControl()).operation("plan_pack") as active:
        for source in sources.values():
            if destination.resolve().is_relative_to(source.resolve()):
                raise PreservationError(
                    "destination must not be inside a captured source"
                )
        manifest = _capture_manifest(recipe, sources, active)
        files = unique_objects(manifest)
        reused = 0
        if destination.exists() or destination.is_symlink():
            with directory(destination) as root:
                _missing, reused, _partial = observe_objects(root, files, active)
        identity = digest(encode_record(manifest))
        request = digest(
            canonical_bytes(
                {
                    "operation": "pack",
                    "manifest": identity,
                    "sources": {
                        name: str(path.resolve()) for name, path in sources.items()
                    },
                    "destination": str(destination.resolve()),
                }
            )
        )
        unique = sum(file.size for file in files)
        return OperationPlan(
            operation="pack",
            manifest_id=identity,
            request_digest=request,
            logical_bytes=manifest.total_bytes,
            unique_bytes=unique,
            reused_bytes=reused,
            partial_bytes=0,
            additional_bytes=unique - reused,
            metadata_bytes=len(encode_record(manifest)),
            reserve_bytes=16 * CHUNK_SIZE,
        )


def plan_restore(
    store: Path,
    manifest_id: str,
    trust: TrustProfile,
    destination: Path,
    *,
    control: OperationControl | None = None,
) -> OperationPlan:
    """Verify a source and report copy space without creating an output tree."""
    with (control or OperationControl()).operation("plan_restore") as active:
        if destination.exists() or destination.is_symlink():
            raise PreservationError("restore destination already exists")
        manifest = verify_store_contents(store, manifest_id, trust, active)
        with directory(store) as root:
            metadata = read_metadata(root, manifest_path(manifest_id))
        request = digest(
            canonical_bytes(
                {
                    "operation": "restore",
                    "manifest": manifest_id,
                    "trust": trust.model_dump(mode="json"),
                    "source": str(store.resolve()),
                    "destination": str(destination.resolve()),
                }
            )
        )
        return OperationPlan(
            operation="restore",
            manifest_id=manifest_id,
            request_digest=request,
            logical_bytes=manifest.total_bytes,
            unique_bytes=sum(file.size for file in unique_objects(manifest)),
            reused_bytes=0,
            partial_bytes=0,
            additional_bytes=manifest.total_bytes,
            metadata_bytes=len(metadata),
            reserve_bytes=16 * CHUNK_SIZE,
        )


def copy_exact(
    origin: BinaryIO,
    output: BinaryIO,
    count: int,
    *,
    control: OperationControl | None = None,
) -> None:
    """Copy exactly the declared size, rejecting truncated or growing files."""
    remaining = count
    if control is not None:
        control.checkpoint()
    while remaining:
        chunk = origin.read(min(CHUNK_SIZE, remaining))
        if not chunk:
            raise PreservationError("source ended before the declared length")
        output.write(chunk)
        remaining -= len(chunk)
        if control is not None:
            control.advance(copied=len(chunk))
    if origin.read(1):
        raise PreservationError("source exceeds the declared length")


def copy_verified_object(
    source: int,
    source_path: str,
    target: int,
    file: FileIdentity,
    control: OperationControl,
    *,
    verify_reused_source: bool = False,
) -> None:
    """Copy a checked object; optionally verify each input even when deduplicated.

    A content-store caller can reuse an authenticated target object. A capture
    caller promising that every supplied local path matches must additionally
    request source verification on that reuse path.
    """
    control.phase("checking")
    try:
        verify_object(target, file, control=control)
    except OperationError:
        raise
    except (FileNotFoundError, PreservationError):
        pass
    else:
        if verify_reused_source:
            observed = file_identity(source, source_path, file.size, control=control)
            if observed.sha256 != file.sha256:
                raise PreservationError(
                    "deduplicated source failed integrity verification"
                )
        control.advance(reused=file.size)
        return
    # Verified content needs no copy; missing or corrupt content needs a full
    # temporary replacement while any old object remains in place.
    if shutil.disk_usage(target).free < file.size + 16 * CHUNK_SIZE:
        raise PreservationError("destination has insufficient free space")
    pending = ".pending/" + uuid.uuid4().hex
    control.phase("copying")
    try:
        with (
            open_file(source, source_path) as origin,
            open_file(target, pending, write=True) as output,
        ):
            copy_exact(origin, output, file.size, control=control)
        control.phase("checking")
        observed = file_identity(target, pending, control=control)
        if (observed.size, observed.sha256) != (file.size, file.sha256):
            raise PreservationError("source changed between inventory and copy")
        with parent_directory(target, pending) as (pending_parent, pending_name):
            with parent_directory(target, object_path(file.sha256), create=True) as (
                parent,
                name,
            ):
                control.checkpoint()
                os.replace(
                    pending_name, name, src_dir_fd=pending_parent, dst_dir_fd=parent
                )
                os.fsync(parent)
        verify_object(target, file, control=control)
    finally:
        with parent_directory(target, pending) as (parent, name):
            try:
                os.unlink(name, dir_fd=parent)
                os.fsync(parent)
            except FileNotFoundError:
                pass


def pack(
    recipe: Recipe,
    sources: dict[str, Path],
    destination: Path,
    keys: tuple[Ed25519PrivateKey, ...],
    *,
    control: OperationControl | None = None,
    expected_manifest_id: str | None = None,
) -> str:
    """Capture selected complete members and publish the signed manifest last."""
    with (control or OperationControl()).operation("pack") as active:
        manifest = _capture_manifest(recipe, sources, active)
        manifest_id = digest(encode_record(manifest))
        if expected_manifest_id is not None and expected_manifest_id != manifest_id:
            raise RequestChanged("source differs from the planned manifest")
        for source in sources.values():
            if destination.resolve().is_relative_to(source.resolve()):
                raise PreservationError(
                    "destination must not be inside a captured source"
                )
        envelope = encode_record(sign_manifest(manifest, keys))
        if len(envelope) > MAX_METADATA_BYTES:
            raise PreservationError("signed envelope exceeds metadata size limit")
        active.checkpoint()
        with directory(destination, create=True) as target, writer_lock(target):
            for member in manifest.members:
                with directory(sources[member.member_id]) as source:
                    for file in member.files:
                        copy_verified_object(source, file.path, target, file, active)
            active.phase("checking")
            for file in unique_objects(manifest):
                verify_object(target, file, control=active)
            active.phase("publishing")
            publish_envelope(target, manifest_id, envelope)
            active.mark_committed()
        return manifest_id


def _capture_manifest(
    recipe: Recipe, sources: dict[str, Path], control: OperationControl
) -> Manifest:
    control.phase("inventory")
    if set(sources) != {member.member_id for member in recipe.members}:
        raise PreservationError("provide exactly one directory per member")
    members: list[ManifestMember] = []
    for member in recipe.members:
        identities: list[FileIdentity] = []
        with directory(sources[member.member_id]) as source:
            for selected in member.files:
                identity = file_identity(source, selected.path, control=control)
                if (
                    selected.expected_sha256 is not None
                    and identity.sha256 != selected.expected_sha256
                ):
                    raise PreservationError("source does not match its expected hash")
                identities.append(identity)
        metadata = member.model_dump(exclude={"files"})
        members.append(ManifestMember(**metadata, files=tuple(identities)))
    metadata = recipe.model_dump(exclude={"members"})
    return Manifest(**metadata, members=tuple(members))


def verify_store(
    destination: Path,
    manifest_id: str,
    trust: TrustProfile,
    *,
    control: OperationControl | None = None,
) -> Manifest:
    """Verify the selected signed generation and every actual required object."""
    with (control or OperationControl()).operation("verify") as active:
        return verify_store_contents(destination, manifest_id, trust, active)


def verify_store_contents(
    destination: Path, manifest_id: str, trust: TrustProfile, control: OperationControl
) -> Manifest:
    """Verify inside a caller-owned operation without opening another invocation."""
    control.phase("checking")
    with directory(destination) as root:
        manifest = verify_manifest(
            read_metadata(root, manifest_path(manifest_id)), trust, manifest_id
        )
        for file in unique_objects(manifest):
            verify_object(root, file, control=control)
    return manifest


def restore(
    store: Path,
    manifest_id: str,
    trust: TrustProfile,
    destination: Path,
    *,
    control: OperationControl | None = None,
) -> Manifest:
    """Materialize a verified member layout into a new directory; never overwrite."""
    with (control or OperationControl()).operation("restore") as active:
        return _restore(store, manifest_id, trust, destination, active)


def _restore(
    store: Path,
    manifest_id: str,
    trust: TrustProfile,
    destination: Path,
    control: OperationControl,
) -> Manifest:
    if destination.exists() or destination.is_symlink():
        raise PreservationError("restore destination already exists")
    parent = destination.parent
    pending = parent / (".restore-" + uuid.uuid4().hex)
    manifest = verify_store_contents(store, manifest_id, trust, control)
    control.phase("copying")
    parent.mkdir(parents=True, exist_ok=True)
    pending.mkdir(mode=0o700)
    try:
        with directory(store) as source, directory(pending) as target:
            for member in manifest.members:
                for file in member.files:
                    relative = "members/" + member.member_id + "/" + file.path
                    with (
                        open_file(source, object_path(file.sha256)) as origin,
                        open_file(target, relative, write=True) as output,
                    ):
                        copy_exact(origin, output, file.size, control=control)
                        output.flush()
                        os.fsync(output.fileno())
                    copied = file_identity(target, relative, control=control)
                    if (copied.size, copied.sha256) != (file.size, file.sha256):
                        raise PreservationError("restored file failed verification")
            atomic_bytes(
                target,
                ".preservation/envelope.json",
                read_metadata(source, manifest_path(manifest_id)),
            )
        # Reserve the name exclusively, then atomically replace our empty
        # reservation with the complete tree. Readers never see a partial tree.
        control.phase("publishing")
        with directory(parent) as parent_descriptor:
            os.mkdir(destination.name, mode=0o700, dir_fd=parent_descriptor)
            reservation = os.stat(destination.name, dir_fd=parent_descriptor)
            try:
                os.rename(
                    pending.name,
                    destination.name,
                    src_dir_fd=parent_descriptor,
                    dst_dir_fd=parent_descriptor,
                )
                os.fsync(parent_descriptor)
                control.mark_committed()
            except BaseException:
                current = os.stat(destination.name, dir_fd=parent_descriptor)
                if (current.st_dev, current.st_ino) == (
                    reservation.st_dev,
                    reservation.st_ino,
                ):
                    try:
                        os.rmdir(destination.name, dir_fd=parent_descriptor)
                    except OSError:
                        pass
                raise
    except BaseException:
        shutil.rmtree(pending, ignore_errors=True)
        raise
    return manifest
