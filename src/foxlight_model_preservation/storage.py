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
from .signing import digest, encode_record, sign_manifest, verify_manifest

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
    root: int, path: str, expected_size: int | None = None
) -> FileIdentity:
    """Hash a stable source snapshot and reject mutation during the read."""
    with open_file(root, path) as stream:
        before = os.fstat(stream.fileno())
        if expected_size is not None and before.st_size != expected_size:
            raise PreservationError("file length differs from signed size")
        hasher = hashlib.sha256()
        size = 0
        while chunk := stream.read(min(CHUNK_SIZE, before.st_size + 1 - size)):
            hasher.update(chunk)
            size += len(chunk)
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


def verify_object(root: int, file: FileIdentity) -> None:
    """Verify actual bytes, including length, before a complete-generation claim."""
    observed = file_identity(root, object_path(file.sha256), file.size)
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


def copy_exact(origin: BinaryIO, output: BinaryIO, count: int) -> None:
    """Copy exactly the declared size, rejecting truncated or growing files."""
    remaining = count
    while remaining:
        chunk = origin.read(min(CHUNK_SIZE, remaining))
        if not chunk:
            raise PreservationError("source ended before the declared length")
        output.write(chunk)
        remaining -= len(chunk)
    if origin.read(1):
        raise PreservationError("source exceeds the declared length")


def _copy_object(
    source: int, source_path: str, target: int, file: FileIdentity
) -> None:
    try:
        verify_object(target, file)
        return
    except (FileNotFoundError, PreservationError):
        pass
    # Verified content needs no copy; missing or corrupt content needs a full
    # temporary replacement while any old object remains in place.
    if shutil.disk_usage(target).free < file.size + 16 * CHUNK_SIZE:
        raise PreservationError("destination has insufficient free space")
    pending = ".pending/" + uuid.uuid4().hex
    with (
        open_file(source, source_path) as origin,
        open_file(target, pending, write=True) as output,
    ):
        copy_exact(origin, output, file.size)
        output.flush()
        os.fsync(output.fileno())
    observed = file_identity(target, pending)
    if (observed.size, observed.sha256) != (file.size, file.sha256):
        raise PreservationError("source changed between inventory and copy")
    with parent_directory(target, pending) as (pending_parent, pending_name):
        with parent_directory(target, object_path(file.sha256), create=True) as (
            parent,
            name,
        ):
            os.replace(pending_name, name, src_dir_fd=pending_parent, dst_dir_fd=parent)
            os.fsync(parent)
    verify_object(target, file)


def pack(
    recipe: Recipe,
    sources: dict[str, Path],
    destination: Path,
    keys: tuple[Ed25519PrivateKey, ...],
) -> str:
    """Capture selected complete members and publish the signed manifest last."""
    if set(sources) != {member.member_id for member in recipe.members}:
        raise PreservationError("provide exactly one directory per member")
    for source in sources.values():
        if destination.resolve().is_relative_to(source.resolve()):
            raise PreservationError("destination must not be inside a captured source")
    members: list[ManifestMember] = []
    with directory(destination, create=True) as target, writer_lock(target):
        for member in recipe.members:
            identities: list[FileIdentity] = []
            with directory(sources[member.member_id]) as source:
                for selected in member.files:
                    identity = file_identity(source, selected.path)
                    if (
                        selected.expected_sha256 is not None
                        and identity.sha256 != selected.expected_sha256
                    ):
                        raise PreservationError(
                            "source does not match its expected hash"
                        )
                    _copy_object(source, selected.path, target, identity)
                    identities.append(identity)
            metadata = member.model_dump(exclude={"files"})
            members.append(ManifestMember(**metadata, files=tuple(identities)))
        metadata = recipe.model_dump(exclude={"members"})
        manifest = Manifest(**metadata, members=tuple(members))
        envelope = encode_record(sign_manifest(manifest, keys))
        if len(envelope) > MAX_METADATA_BYTES:
            raise PreservationError("signed envelope exceeds metadata size limit")
        manifest_id = digest(encode_record(manifest))
        for file in unique_objects(manifest):
            verify_object(target, file)
        atomic_bytes(target, manifest_path(manifest_id), envelope)
    return manifest_id


def verify_store(destination: Path, manifest_id: str, trust: TrustProfile) -> Manifest:
    """Verify the selected signed generation and every actual required object."""
    with directory(destination) as root:
        manifest = verify_manifest(
            read_metadata(root, manifest_path(manifest_id)), trust, manifest_id
        )
        for file in unique_objects(manifest):
            verify_object(root, file)
    return manifest


def restore(
    store: Path, manifest_id: str, trust: TrustProfile, destination: Path
) -> Manifest:
    """Materialize a verified member layout into a new directory; never overwrite."""
    if destination.exists() or destination.is_symlink():
        raise PreservationError("restore destination already exists")
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    pending = parent / (".restore-" + uuid.uuid4().hex)
    manifest = verify_store(store, manifest_id, trust)
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
                        copy_exact(origin, output, file.size)
                        output.flush()
                        os.fsync(output.fileno())
                    copied = file_identity(target, relative)
                    if (copied.size, copied.sha256) != (file.size, file.sha256):
                        raise PreservationError("restored file failed verification")
            atomic_bytes(
                target,
                ".preservation/envelope.json",
                read_metadata(source, manifest_path(manifest_id)),
            )
        # Reserve the name exclusively, then atomically replace our empty
        # reservation with the complete tree. Readers never see a partial tree.
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
