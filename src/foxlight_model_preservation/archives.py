"""Format dispatch for authenticated archives; no runtime/card interpretation."""

from __future__ import annotations

from pathlib import Path

from .models import FileIdentity, Manifest, TrustProfile
from .operations import OperationControl
from .pmf import verify_pmf_manifest
from .pmf_models import PMF_TYPE, PMFManifest, pmf_objects
from .signing import Envelope, canonical_bytes, strict_json, verify_manifest
from .storage import (
    directory,
    manifest_path,
    read_metadata,
    unique_objects,
    verify_object,
)

ArchiveManifest = Manifest | PMFManifest


def verify_archive_manifest(
    envelope: bytes, trust: TrustProfile, expected_manifest_id: str
) -> ArchiveManifest:
    """Dispatch explicit signing domains, retaining exact identity and trust checks.

    Unknown domains are refused by the legacy verifier. Never retry a failed
    PMF verification as another format, or reinterpret a signed payload.
    """
    signed = Envelope.model_validate_json(canonical_bytes(strict_json(envelope)))
    if signed.payloadType == PMF_TYPE:
        return verify_pmf_manifest(envelope, trust, expected_manifest_id)
    return verify_manifest(envelope, trust, expected_manifest_id)


def archive_objects(manifest: ArchiveManifest) -> tuple[FileIdentity, ...]:
    """Return each exact object once, including PMF's opaque source documents."""
    if isinstance(manifest, PMFManifest):
        return tuple({file.sha256: file for file in pmf_objects(manifest)}.values())
    return unique_objects(manifest)


def verify_archive(
    store: Path,
    manifest_id: str,
    trust: TrustProfile,
    *,
    control: OperationControl | None = None,
) -> ArchiveManifest:
    """Authenticate a selected legacy or PMF archive and hash every actual object."""
    with (control or OperationControl()).operation("verify") as active:
        active.phase("checking")
        with directory(store) as source:
            manifest = verify_archive_manifest(
                read_metadata(source, manifest_path(manifest_id)), trust, manifest_id
            )
            for file in archive_objects(manifest):
                verify_object(source, file, control=active)
            return manifest
