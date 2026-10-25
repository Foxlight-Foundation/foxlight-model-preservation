"""Self-contained, streaming recovery kits with no executable archive features.

The kit's public keys are candidates, never an independent trust anchor. A
receiver must supply enrolled trust or explicitly accept the displayed kit key.
The original signed manifest controls every object length and content identity.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import struct
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from .archives import ArchiveManifest, archive_objects, verify_archive_manifest
from .models import (
    MAX_METADATA_BYTES,
    PreservationError,
    Record,
    TrustProfile,
)
from .operations import OperationControl
from .pmf_models import PMFManifest
from .signing import (
    Envelope,
    canonical_bytes,
    decode_base64,
    encode_record,
    load_trust,
    strict_json,
)
from .storage import (
    CHUNK_SIZE,
    atomic_bytes,
    directory,
    manifest_path,
    object_path,
    open_file,
    read_metadata,
)

MAGIC = b"FOXLIGHT-RECOVERY-KIT\x00\x01\r\n"
PMF_MAGIC = b"FOXLIGHT-RECOVERY-KIT\x00\x02\r\n"


class KitInspection(Record):
    """Self-verified metadata, before independently trusting its candidate keys."""

    manifest_id: str
    trust: TrustProfile
    manifest: ArchiveManifest
    kit_bytes: int


@contextmanager
def _input(path: Path) -> Generator[BinaryIO]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise PreservationError("kit must be a regular file")
        stream = os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise
    with stream:
        yield stream


def _header(stream: BinaryIO) -> tuple[KitInspection, bytes]:
    magic = stream.read(len(MAGIC))
    if magic not in {MAGIC, PMF_MAGIC}:
        raise PreservationError("unsupported recovery kit format")
    lengths = stream.read(16)
    if len(lengths) != 16:
        raise PreservationError("truncated recovery kit header")
    trust_length, envelope_length = struct.unpack(">QQ", lengths)
    if (
        not 0 < trust_length <= MAX_METADATA_BYTES
        or not 0 < envelope_length <= MAX_METADATA_BYTES
    ):
        raise PreservationError("kit metadata exceeds limits")
    trust_bytes = stream.read(trust_length)
    envelope = stream.read(envelope_length)
    if len(trust_bytes) != trust_length or len(envelope) != envelope_length:
        raise PreservationError("truncated kit metadata")
    trust = load_trust(trust_bytes)
    # Recover the identity only from the authenticated DSSE payload, then run
    # normal signature/schema/closure checks against that exact identity.
    signed = Envelope.model_validate_json(canonical_bytes(strict_json(envelope)))
    identity = hashlib.sha256(decode_base64(signed.payload)).hexdigest()
    manifest = verify_archive_manifest(envelope, trust, identity)
    if (magic == PMF_MAGIC) != isinstance(manifest, PMFManifest):
        raise PreservationError("kit version differs from its signed archive format")
    expected = (
        len(MAGIC)
        + 16
        + trust_length
        + envelope_length
        + sum(file.size for file in archive_objects(manifest))
    )
    if os.fstat(stream.fileno()).st_size != expected:
        raise PreservationError("kit length differs from the signed closure")
    return KitInspection(
        manifest_id=identity, trust=trust, manifest=manifest, kit_bytes=expected
    ), envelope


def inspect_kit(path: Path) -> KitInspection:
    """Read bounded metadata; candidate signatures do not establish key trust.

    This does not read weights. Use import_kit to verify every actual object.
    """
    with _input(path) as stream:
        return _header(stream)[0]


def export_kit(
    store: Path,
    manifest_id: str,
    trust: TrustProfile,
    destination: Path,
    *,
    expected_envelope: bytes | None = None,
    expected_store_identity: tuple[int, int] | None = None,
    control: OperationControl | None = None,
) -> KitInspection:
    """Write only the selected signed closure and public trust to a new kit.

    Private keys, other archive generations and local authorization are never
    selected. Each object is hashed while copying; incomplete output stays hidden.
    An enrolled owner can additionally pin the source directory's device/inode;
    replacement then refuses before any source metadata or object is read.
    """
    active = control or OperationControl()
    with (
        active.operation("kit_export"),
        directory(store) as source,
        directory(destination.parent) as parent,
    ):
        if expected_store_identity is not None:
            info = os.fstat(source)
            if (info.st_dev, info.st_ino) != expected_store_identity:
                raise PreservationError("kit source directory identity changed")
        envelope = read_metadata(source, manifest_path(manifest_id))
        if expected_envelope is not None and envelope != expected_envelope:
            raise PreservationError(
                "source differs from the selected original envelope"
            )
        manifest = verify_archive_manifest(envelope, trust, manifest_id)
        magic = PMF_MAGIC if isinstance(manifest, PMFManifest) else MAGIC
        trust_bytes = encode_record(trust)
        if len(trust_bytes) > MAX_METADATA_BYTES:
            raise PreservationError("kit trust exceeds metadata limit")
        required = (
            len(MAGIC)
            + 16
            + len(trust_bytes)
            + len(envelope)
            + sum(file.size for file in archive_objects(manifest))
        )
        if shutil.disk_usage(parent).free < required + 16 * CHUNK_SIZE:
            raise PreservationError("insufficient space for recovery kit")
        pending = ".kit-" + uuid.uuid4().hex
        try:
            with open_file(parent, pending, write=True) as output:
                output.write(
                    magic + struct.pack(">QQ", len(trust_bytes), len(envelope))
                )
                output.write(trust_bytes)
                output.write(envelope)
                for file in sorted(
                    archive_objects(manifest), key=lambda item: item.sha256
                ):
                    with open_file(source, object_path(file.sha256)) as origin:
                        _copy(
                            origin,
                            output,
                            file.size,
                            file.sha256,
                            active,
                            check_eof=True,
                        )
                output.flush()
                os.fsync(output.fileno())
                size = output.tell()
            active.phase("publishing")
            # Hard-link publication cannot replace an existing file or symlink.
            os.link(
                pending,
                destination.name,
                src_dir_fd=parent,
                dst_dir_fd=parent,
                follow_symlinks=False,
            )
            os.fsync(parent)
            active.mark_committed()
        finally:
            os.unlink(pending, dir_fd=parent)
    return KitInspection(
        manifest_id=manifest_id, trust=trust, manifest=manifest, kit_bytes=size
    )


def _copy(
    origin: BinaryIO,
    output: BinaryIO,
    size: int,
    expected: str,
    control: OperationControl,
    *,
    check_eof: bool = False,
) -> None:
    hasher = hashlib.sha256()
    remaining = size
    while remaining:
        control.checkpoint()
        chunk = origin.read(min(CHUNK_SIZE, remaining))
        if not chunk:
            raise PreservationError("kit object truncated")
        output.write(chunk)
        hasher.update(chunk)
        remaining -= len(chunk)
        control.advance(copied=len(chunk), hashed=len(chunk))
    if hasher.hexdigest() != expected or (check_eof and origin.read(1)):
        raise PreservationError("kit object failed integrity verification")


def import_kit(
    path: Path,
    destination: Path,
    *,
    trust: TrustProfile | None = None,
    expected_manifest_id: str,
    accept_kit_key: bool = False,
    expected_envelope: bytes | None = None,
    control: OperationControl | None = None,
) -> KitInspection:
    """Verify a complete kit into a new portable store, then publish atomically.

    Selecting a local kit and deliberately accepting its candidate key grants
    capture verification only, never registry authority or code execution.
    Independently enrolled trust is preferable when it remains available.
    An optional expected_envelope additionally pins its exact original wrapper,
    including retained signatures and whitespace, before reading model bytes.
    """
    active = control or OperationControl()
    with (
        active.operation("kit_import"),
        _input(path) as stream,
        directory(destination.parent) as parent,
    ):
        info, envelope = _header(stream)
        if expected_envelope is not None and envelope != expected_envelope:
            raise PreservationError("kit differs from the selected original envelope")
        if info.manifest_id != expected_manifest_id:
            raise PreservationError("selected kit manifest changed")
        if trust is None:
            if not accept_kit_key:
                raise PreservationError(
                    "independent trust or explicit kit-key acceptance required"
                )
            trust = info.trust
        _ = verify_archive_manifest(envelope, trust, expected_manifest_id)
        if (
            shutil.disk_usage(parent).free
            < sum(file.size for file in archive_objects(info.manifest))
            + 16 * CHUNK_SIZE
        ):
            raise PreservationError("insufficient space for recovery kit")
        pending = ".kit-import-" + uuid.uuid4().hex
        os.mkdir(pending, mode=0o700, dir_fd=parent)
        try:
            with directory(destination.parent / pending) as target:
                for file in sorted(
                    archive_objects(info.manifest), key=lambda item: item.sha256
                ):
                    with open_file(
                        target, object_path(file.sha256), write=True
                    ) as output:
                        _copy(stream, output, file.size, file.sha256, active)
                if stream.read(1):
                    raise PreservationError("unexpected trailing kit bytes")
                atomic_bytes(target, manifest_path(info.manifest_id), envelope)
                atomic_bytes(target, "kit-trust.json", encode_record(trust))
            active.phase("publishing")
            os.mkdir(destination.name, mode=0o700, dir_fd=parent)
            try:
                os.rename(
                    pending, destination.name, src_dir_fd=parent, dst_dir_fd=parent
                )
            except BaseException:
                os.rmdir(destination.name, dir_fd=parent)
                raise
            os.fsync(parent)
            active.mark_committed()
        finally:
            shutil.rmtree(destination.parent / pending, ignore_errors=True)
    return info


def read_kit_document(
    path: Path, document_id: str, *, trust: TrustProfile, expected_manifest_id: str
) -> bytes:
    """Read one authenticated bounded PMF record without reading model weights.

    This metadata-only adapter helper seeks over signed object lengths. It
    rechecks the selected identity and independent trust and hashes the record;
    it does not establish that any skipped weight object is intact.
    """
    with _input(path) as stream:
        info, envelope = _header(stream)
        if info.manifest_id != expected_manifest_id:
            raise PreservationError("selected kit manifest changed")
        manifest = verify_archive_manifest(envelope, trust, expected_manifest_id)
        if not isinstance(manifest, PMFManifest):
            raise PreservationError("source documents require a PMF kit")
        document = next(
            (item for item in manifest.documents if item.document_id == document_id),
            None,
        )
        if document is None:
            raise PreservationError("PMF source document missing")
        for file in sorted(archive_objects(manifest), key=lambda item: item.sha256):
            if file.sha256 == document.sha256:
                data = stream.read(document.size)
                if (
                    len(data) != document.size
                    or hashlib.sha256(data).hexdigest() != document.sha256
                ):
                    raise PreservationError("PMF source document failed verification")
                return data
            stream.seek(file.size, os.SEEK_CUR)
        raise PreservationError("PMF source document missing")
