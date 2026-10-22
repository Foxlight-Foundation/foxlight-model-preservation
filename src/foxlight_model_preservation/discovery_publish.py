"""Explicit static-source publication using standard python-tuf metadata.

Callers retain their root and encrypted role keys outside the public tree. This
module publishes metadata only and never starts a listener or republishes model
weights, private cards, receiver policy or signing keys.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from securesystemslib.signer import CryptoSigner  # pyright: ignore[reportPrivateImportUsage]
from tuf.api.metadata import (
    Metadata,
    MetaFile,  # pyright: ignore[reportPrivateImportUsage]
    Root,  # pyright: ignore[reportPrivateImportUsage]
    Snapshot,  # pyright: ignore[reportPrivateImportUsage]
    TargetFile,  # pyright: ignore[reportPrivateImportUsage]
    Targets,  # pyright: ignore[reportPrivateImportUsage]
    Timestamp,  # pyright: ignore[reportPrivateImportUsage]
)

from .discovery import parse_discovery_role, validate_catalog_target
from .discovery_models import MAX_ROLE_BYTES, DiscoveryCatalogTarget
from .models import PreservationError
from .signing import digest
from .storage import (
    atomic_bytes,
    directory,
    open_file,
    parent_directory,
    read_metadata,
    writer_lock,
)


@dataclass(frozen=True)
class DiscoverySigningKeys:
    """Explicit targets/snapshot/timestamp authority; root custody stays separate."""

    targets: Ed25519PrivateKey
    snapshot: Ed25519PrivateKey
    timestamp: Ed25519PrivateKey


def _immutable(root: int, name: str, data: bytes) -> None:
    try:
        with open_file(root, name) as stream:
            previous = stream.read(len(data) + 1)
    except FileNotFoundError:
        atomic_bytes(root, name, data)
    else:
        if previous != data:
            raise PreservationError(
                "published discovery identity/version already exists"
            )


def publish_catalog(
    output: Path,
    trusted_root: bytes,
    catalog: bytes,
    profile: DiscoveryCatalogTarget,
    keys: DiscoverySigningKeys,
    *,
    version: int,
    expires: datetime,
) -> str:
    """Publish a reviewed catalog and monotonically versioned static TUF metadata.

    The supplied public root must authorize each signing key. Generate/rotate
    that root with stock TUF tooling; this function never fabricates root trust
    or requires root private keys for ordinary updates. Timestamp commits last.
    """
    if not 1 <= version <= 2**53 - 1:
        raise PreservationError("source publication needs a bounded positive version")
    now = datetime.now(UTC)
    if expires.tzinfo is None or not now < expires <= now + timedelta(days=365):
        raise PreservationError(
            "discovery expiry must be UTC-aware, future and within a year"
        )
    root_metadata = parse_discovery_role(trusted_root, Root)
    root = root_metadata.signed
    root.verify_delegate("root", root_metadata.signed_bytes, root_metadata.signatures)
    if root.is_expired(now):
        raise PreservationError("cannot publish with an expired discovery root")
    validate_catalog_target(catalog, profile)
    target = TargetFile.from_data("catalog.json", catalog, ["sha256"])
    target.unrecognized_fields["custom"] = profile.model_dump(mode="json")
    targets = Metadata(
        Targets(version=version, expires=expires, targets={"catalog.json": target})
    )
    targets.sign(CryptoSigner(keys.targets))
    targets_bytes = targets.to_bytes()
    snapshot = Metadata(
        Snapshot(
            version=version,
            expires=expires,
            meta={
                "targets.json": MetaFile.from_data(version, targets_bytes, ["sha256"]),
            },
        )
    )
    snapshot.sign(CryptoSigner(keys.snapshot))
    snapshot_bytes = snapshot.to_bytes()
    timestamp = Metadata(
        Timestamp(
            version=version,
            expires=expires,
            snapshot_meta=MetaFile.from_data(version, snapshot_bytes, ["sha256"]),
        )
    )
    timestamp.sign(CryptoSigner(keys.timestamp))
    timestamp_bytes = timestamp.to_bytes()
    for role, md in (
        ("targets", targets),
        ("snapshot", snapshot),
        ("timestamp", timestamp),
    ):
        if len(md.to_bytes()) > MAX_ROLE_BYTES:
            raise PreservationError("published discovery role exceeds the reader bound")
        root.verify_delegate(role, md.signed_bytes, md.signatures)
    with directory(output, create=True) as destination, writer_lock(destination):
        with parent_directory(destination, "metadata/unused", create=True) as (
            metadata_dir,
            _,
        ):
            published_roots = [
                int(match[1])
                for filename in os.listdir(metadata_dir)
                if (match := re.fullmatch(r"([1-9][0-9]{0,15})\.root\.json", filename))
            ]
        if published_roots:
            previous_root_version = max(published_roots)
            if root.version not in {previous_root_version, previous_root_version + 1}:
                raise PreservationError(
                    "published root versions must remain sequential"
                )
            if root.version == previous_root_version + 1:
                previous_root = parse_discovery_role(
                    read_metadata(
                        destination, f"metadata/{previous_root_version}.root.json"
                    ),
                    Root,
                )
                previous_root.signed.verify_delegate(
                    "root", root_metadata.signed_bytes, root_metadata.signatures
                )
        try:
            previous_bytes = read_metadata(destination, "metadata/timestamp.json")
        except FileNotFoundError:
            previous_bytes = None
        if previous_bytes is not None:
            previous = parse_discovery_role(previous_bytes, Timestamp)
            if version <= previous.signed.version:
                raise PreservationError(
                    "publication version must increase; never overwrite an old version"
                )
        _immutable(destination, f"metadata/{root.version}.root.json", trusted_root)
        _immutable(destination, f"targets/{digest(catalog)}.catalog.json", catalog)
        _immutable(destination, f"metadata/{version}.targets.json", targets_bytes)
        _immutable(destination, f"metadata/{version}.snapshot.json", snapshot_bytes)
        atomic_bytes(destination, "metadata/targets.json", targets_bytes)
        atomic_bytes(destination, "metadata/snapshot.json", snapshot_bytes)
        if not root.consistent_snapshot:
            atomic_bytes(destination, "targets/catalog.json", catalog)
        atomic_bytes(destination, "metadata/timestamp.json", timestamp_bytes)
    return profile.catalog_id
