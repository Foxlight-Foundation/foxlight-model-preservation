"""Self-contained, streaming recovery kits with no executable archive features.

The kit's public keys are candidates, never an independent trust anchor. A
receiver must supply enrolled trust or explicitly accept the displayed kit key.
The original signed manifest controls every object length and content identity.
"""

from __future__ import annotations

import base64
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

from .models import (
    MAX_METADATA_BYTES,
    Manifest,
    PreservationError,
    Record,
    TrustProfile,
)
from .operations import OperationControl
from .signing import (
    Envelope,
    canonical_bytes,
    encode_record,
    load_trust,
    strict_json,
    verify_manifest,
)
from .storage import (
    CHUNK_SIZE,
    atomic_bytes,
    directory,
    manifest_path,
    object_path,
    open_file,
    read_metadata,
    unique_objects,
)

MAGIC = b"FOXLIGHT-RECOVERY-KIT\x00\x01\r\n"


class KitInspection(Record):
    """Self-verified metadata, before independently trusting its candidate keys."""

    manifest_id: str
    trust: TrustProfile
    manifest: Manifest
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
    if stream.read(len(MAGIC)) != MAGIC:
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
    identity = hashlib.sha256(
        base64.b64decode(signed.payload, validate=True)
    ).hexdigest()
    manifest = verify_manifest(envelope, trust, identity)
    expected = (
        len(MAGIC)
        + 16
        + trust_length
        + envelope_length
        + sum(file.size for file in unique_objects(manifest))
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
    control: OperationControl | None = None,
) -> KitInspection:
    """Write only the selected signed closure and public trust to a new kit.

    Private keys, other archive generations and local authorization are never
    selected. Each object is hashed while copying; incomplete output stays hidden.
    """
    active = control or OperationControl()
    with (
        active.operation("kit_export"),
        directory(store) as source,
        directory(destination.parent) as parent,
    ):
        envelope = read_metadata(source, manifest_path(manifest_id))
        manifest = verify_manifest(envelope, trust, manifest_id)
        trust_bytes = encode_record(trust)
        if len(trust_bytes) > MAX_METADATA_BYTES:
            raise PreservationError("kit trust exceeds metadata limit")
        required = (
            len(MAGIC)
            + 16
            + len(trust_bytes)
            + len(envelope)
            + sum(file.size for file in unique_objects(manifest))
        )
        if shutil.disk_usage(parent).free < required + 16 * CHUNK_SIZE:
            raise PreservationError("insufficient space for recovery kit")
        pending = ".kit-" + uuid.uuid4().hex
        try:
            with open_file(parent, pending, write=True) as output:
                output.write(
                    MAGIC + struct.pack(">QQ", len(trust_bytes), len(envelope))
                )
                output.write(trust_bytes)
                output.write(envelope)
                for file in sorted(
                    unique_objects(manifest), key=lambda item: item.sha256
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
    control: OperationControl | None = None,
) -> KitInspection:
    """Verify a complete kit into a new portable store, then publish atomically.

    Selecting a local kit and deliberately accepting its candidate key grants
    capture verification only, never registry authority or code execution.
    Independently enrolled trust is preferable when it remains available.
    """
    active = control or OperationControl()
    with (
        active.operation("kit_import"),
        _input(path) as stream,
        directory(destination.parent) as parent,
    ):
        info, envelope = _header(stream)
        if info.manifest_id != expected_manifest_id:
            raise PreservationError("selected kit manifest changed")
        if trust is None:
            if not accept_kit_key:
                raise PreservationError(
                    "independent trust or explicit kit-key acceptance required"
                )
            trust = info.trust
        _ = verify_manifest(envelope, trust, expected_manifest_id)
        if (
            shutil.disk_usage(parent).free
            < sum(file.size for file in unique_objects(info.manifest)) + 16 * CHUNK_SIZE
        ):
            raise PreservationError("insufficient space for recovery kit")
        pending = ".kit-import-" + uuid.uuid4().hex
        os.mkdir(pending, mode=0o700, dir_fd=parent)
        try:
            with directory(destination.parent / pending) as target:
                for file in sorted(
                    unique_objects(info.manifest), key=lambda item: item.sha256
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
