"""Standalone draft PMF packing, byte verification and filesystem restoration.

This module deliberately imports no card, registry-proof or Skulk adapter code.
Profiles and their signed source records survive as exact opaque objects.
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .models import MAX_METADATA_BYTES, FileIdentity, PreservationError, TrustProfile
from .operations import OperationControl
from .pmf_models import PMF_TYPE, PMFManifest, pmf_objects, require_pmf_features
from .signing import (
    canonical_bytes,
    digest,
    encode_record,
    sign_record,
    strict_json,
    verify_payload,
)
from .storage import (
    CHUNK_SIZE,
    atomic_bytes,
    copy_exact,
    copy_verified_object,
    directory,
    file_identity,
    manifest_path,
    object_path,
    open_file,
    read_metadata,
    verify_object,
)


def load_pmf(data: bytes) -> PMFManifest:
    """Read a bounded unsigned descriptor and refuse required unknown semantics."""
    manifest = PMFManifest.model_validate_json(canonical_bytes(strict_json(data)))
    require_pmf_features(manifest)
    return manifest


def verify_pmf_manifest(
    envelope: bytes, trust: TrustProfile, expected_manifest_id: str
) -> PMFManifest:
    """Authenticate the exact PMF payload; this alone does not check its objects."""
    return load_pmf(verify_payload(envelope, trust, expected_manifest_id, PMF_TYPE))


def _unique_objects(manifest: PMFManifest) -> tuple[FileIdentity, ...]:
    return tuple({file.sha256: file for file in pmf_objects(manifest)}.values())


def check_pmf_space(
    target: int,
    manifest: PMFManifest,
    *,
    restoring: bool = False,
    linked_from: int | None = None,
) -> None:
    """Reserve space for objects or the larger materialized member/document tree.

    ``linked_from`` names the source store when its immutable objects will be
    hardlinked: on the same filesystem only the source documents need new
    space. Each object still checks free space itself if it falls back to a copy.
    """
    if (
        linked_from is not None
        and not restoring
        and (os.fstat(linked_from).st_dev == os.fstat(target).st_dev)
    ):
        required = sum(item.size for item in manifest.documents)
    else:
        required = (
            manifest.total_bytes
            if restoring
            else sum(file.size for file in _unique_objects(manifest))
        )
    if shutil.disk_usage(target).free < required + 16 * CHUNK_SIZE:
        raise PreservationError("insufficient space for complete PMF archive")


@contextmanager
def new_pmf_tree(destination: Path, control: OperationControl) -> Generator[int]:
    """Publish a complete new directory, refusing existing names and symlinks."""
    with directory(destination.parent) as parent:
        pending = ".pmf-" + uuid.uuid4().hex
        os.mkdir(pending, mode=0o700, dir_fd=parent)
        try:
            descriptor = os.open(
                pending, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
            )
            try:
                yield descriptor
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            control.phase("publishing")
            os.mkdir(destination.name, mode=0o700, dir_fd=parent)
            reservation = os.stat(
                destination.name, dir_fd=parent, follow_symlinks=False
            )
            try:
                os.rename(
                    pending, destination.name, src_dir_fd=parent, dst_dir_fd=parent
                )
            except BaseException:
                current = os.stat(
                    destination.name, dir_fd=parent, follow_symlinks=False
                )
                if (current.st_dev, current.st_ino) == (
                    reservation.st_dev,
                    reservation.st_ino,
                ):
                    os.rmdir(destination.name, dir_fd=parent)
                raise
            os.fsync(parent)
            control.mark_committed()
        finally:
            shutil.rmtree(pending, dir_fd=parent, ignore_errors=True)


def sign_pmf_manifest(
    manifest: PMFManifest, keys: tuple[Ed25519PrivateKey, ...]
) -> tuple[str, bytes]:
    """Validate and sign draft PMF; return payload identity and bounded envelope."""
    # Revalidate mutable nested values and the final base64 envelope's size.
    manifest = load_pmf(encode_record(manifest))
    envelope = encode_record(sign_record(manifest, keys, PMF_TYPE))
    if len(envelope) > MAX_METADATA_BYTES:
        raise PreservationError("signed PMF metadata exceeds size limit")
    return digest(encode_record(manifest)), envelope


def verify_pmf_contents(
    source: int, manifest_id: str, trust: TrustProfile, control: OperationControl
) -> tuple[PMFManifest, bytes]:
    """Verify from a pinned store handle inside a caller-owned worker operation."""
    control.phase("checking")
    envelope = read_metadata(source, manifest_path(manifest_id))
    manifest = verify_pmf_manifest(envelope, trust, manifest_id)
    for file in _unique_objects(manifest):
        verify_object(source, file, control=control)
    return manifest, envelope


def pack_pmf(
    manifest: PMFManifest,
    sources: Mapping[str, Path],
    documents: Mapping[str, Path],
    destination: Path,
    keys: tuple[Ed25519PrivateKey, ...],
    *,
    control: OperationControl | None = None,
) -> str:
    """Pack an exact descriptor and selected local files into a new signed PMF.

    The caller supplies file identities; every actual byte must match them.
    Origins, cards and ecosystem profiles are optional. Nothing is executed,
    served, fetched or enrolled as trusted by this operation.
    """
    manifest = load_pmf(encode_record(manifest))
    identity, envelope = sign_pmf_manifest(manifest, keys)
    if set(sources) != {member.member_id for member in manifest.members}:
        raise PreservationError("PMF requires exactly its selected member sources")
    if set(documents) != {item.document_id for item in manifest.documents}:
        raise PreservationError("PMF requires exactly its selected source documents")
    with (control or OperationControl()).operation("pack") as active:
        with new_pmf_tree(destination, active) as target:
            check_pmf_space(target, manifest)
            for member in manifest.members:
                with directory(sources[member.member_id]) as source:
                    for file in member.files:
                        copy_verified_object(
                            source,
                            file.path,
                            target,
                            file,
                            active,
                            verify_reused_source=True,
                        )
            for document in manifest.documents:
                path = documents[document.document_id]
                with directory(path.parent) as source:
                    copy_verified_object(
                        source,
                        path.name,
                        target,
                        FileIdentity(
                            path=path.name, size=document.size, sha256=document.sha256
                        ),
                        active,
                        verify_reused_source=True,
                    )
            atomic_bytes(target, manifest_path(identity), envelope)
    return identity


def verify_pmf(
    store: Path,
    manifest_id: str,
    trust: TrustProfile,
    *,
    control: OperationControl | None = None,
) -> PMFManifest:
    """Verify the pinned PMF owner capture and every file/source document object."""
    with (control or OperationControl()).operation("verify") as active:
        with directory(store) as source:
            return verify_pmf_contents(source, manifest_id, trust, active)[0]


def restore_pmf(
    store: Path,
    manifest_id: str,
    trust: TrustProfile,
    destination: Path,
    *,
    control: OperationControl | None = None,
) -> PMFManifest:
    """Restore members and opaque documents to a new tree, with no runtime import.

    Source records appear at documents/<document-id>; their original bytes are
    not parsed or normalized. The selected PMF envelope is saved alongside them.
    """
    with (control or OperationControl()).operation("restore") as active:
        with directory(store) as source:
            manifest, envelope = verify_pmf_contents(source, manifest_id, trust, active)
            with new_pmf_tree(destination, active) as target:
                check_pmf_space(target, manifest, restoring=True)
                paths = [
                    ("members/" + member.member_id + "/" + file.path, file)
                    for member in manifest.members
                    for file in member.files
                ] + [
                    ("documents/" + item.document_id, item)
                    for item in manifest.documents
                ]
                active.phase("copying")
                for path, file in paths:
                    with open_file(source, object_path(file.sha256)) as origin:
                        with open_file(target, path, write=True) as output:
                            copy_exact(origin, output, file.size, control=active)
                    copied = file_identity(target, path, file.size, control=active)
                    if copied.sha256 != file.sha256:
                        raise PreservationError(
                            "restored PMF object failed verification"
                        )
                atomic_bytes(target, ".pmf/envelope.json", envelope)
            return manifest
