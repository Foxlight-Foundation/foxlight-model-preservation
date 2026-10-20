"""Offline, receiver-anchored TUF proofs for exact public model cards.

This bounded profile reads only top-level targets. It is not an update client:
it never discovers keys, follows URLs or changes an ordinary TUF client's state.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, TypeVar, final

from pydantic import Field, JsonValue
from tuf.api.exceptions import RepositoryError

# python-tuf documents these public re-exports but omits explicit typing aliases.
from tuf.api.metadata import (
    Metadata,
    Root,  # pyright: ignore[reportPrivateImportUsage]
    Snapshot,  # pyright: ignore[reportPrivateImportUsage]
    Targets,  # pyright: ignore[reportPrivateImportUsage]
    Timestamp,  # pyright: ignore[reportPrivateImportUsage]
)

from .cards import CardId, ModelCardEnvelope, identity_json, load_card
from .models import MAX_METADATA_BYTES, Digest, PreservationError, Record
from .signing import decode_base64, digest, encode_record, strict_json

ProofMode = Literal["fresh", "historical"]
EncodedMetadata = str
T = TypeVar("T", Root, Timestamp, Snapshot, Targets)


@final
class RegistryProof(Record):
    """Exact signed bytes plus a fingerprint, never an enrolled trust root."""

    schema_version: Literal["foxlight.registry-proof.v1"]
    anchor_sha256: Digest
    root_chain: tuple[EncodedMetadata, ...] = Field(default=(), max_length=32)
    timestamp: EncodedMetadata
    snapshot: EncodedMetadata
    targets: EncodedMetadata
    target_path: str = Field(min_length=1, max_length=200)
    target: EncodedMetadata


@final
class VersionFloor(Record):
    """Optional receiver-retained rollback floors, including historical recovery."""

    root: int = Field(default=1, ge=1)
    timestamp: int = Field(default=1, ge=1)
    snapshot: int = Field(default=1, ge=1)
    targets: int = Field(default=1, ge=1)


@final
class ProofReport(Record):
    """Card publication evidence, separate from file integrity and qualification."""

    card_id: CardId
    mode: ProofMode
    anchor_sha256: Digest
    target_path: str
    target_sha256: Digest
    versions: VersionFloor
    expired_roles: tuple[str, ...]
    provenance: Literal["foxlight", "agent", "community"] | None
    checked_at: str
    revocation_scope: Literal["supplied-root-chain-only"] = "supplied-root-chain-only"


@dataclass(frozen=True)
class VerifiedCard:
    """Return the verified exact card alongside the bounded evidence report."""

    card: ModelCardEnvelope
    report: ProofReport


def _bounded_json(data: bytes) -> JsonValue:
    value = strict_json(data)
    pending: list[tuple[JsonValue, int]] = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > 64 or count > 200_000:
            raise PreservationError("proof JSON exceeds structural limits")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    return value


def _metadata(data: bytes, kind: type[T]) -> Metadata[T]:
    value = _bounded_json(data)
    if not isinstance(value, dict):
        raise PreservationError("TUF metadata must be an object")
    signatures = value.get("signatures")
    signed = value.get("signed")
    if (
        not isinstance(signatures, list)
        or not 1 <= len(signatures) <= 64
        or not isinstance(signed, dict)
    ):
        raise PreservationError("invalid bounded TUF metadata")
    if signed.get("_type") == "root":
        keys = signed.get("keys")
        roles = signed.get("roles")
        if (
            not isinstance(keys, dict)
            or not 1 <= len(keys) <= 64
            or not isinstance(roles, dict)
            or set(roles) != {"root", "timestamp", "snapshot", "targets"}
        ):
            raise PreservationError("unsupported root profile")
        for key in keys.values():
            if not isinstance(key, dict) or (key.get("keytype"), key.get("scheme")) != (
                "ed25519",
                "ed25519",
            ):
                raise PreservationError("proof profile requires Ed25519 root keys")
        for role in roles.values():
            if not isinstance(role, dict):
                raise PreservationError("invalid root role")
            threshold, keyids = role.get("threshold"), role.get("keyids")
            if (
                type(threshold) is not int
                or not isinstance(keyids, list)
                or not 1 <= threshold <= len(keyids) <= 64
                or any(not isinstance(keyid, str) for keyid in keyids)
                or len(set(identity_json(keyid) for keyid in keyids)) != len(keyids)
                or any(keyid not in keys for keyid in keyids)
            ):
                raise PreservationError("invalid root threshold or key selection")
    if signed.get("_type") != kind.type:
        raise PreservationError("proof metadata has the wrong role")
    return Metadata[T].from_bytes(data)


def create_registry_proof(
    *,
    trusted_root: bytes,
    timestamp: bytes,
    snapshot: bytes,
    targets: bytes,
    target_path: str,
    target: bytes,
    root_chain: tuple[bytes, ...] = (),
) -> RegistryProof:
    """Package retained exact bytes; packaging alone does not verify a proof.

    The receiver must independently enroll the root and select a mode/card ID.
    No network access or trust-file mutation occurs here or during verification.
    """
    for data in (trusted_root, *root_chain, timestamp, snapshot, targets, target):
        _bounded_json(data)

    def encoded(data: bytes) -> str:
        return base64.b64encode(data).decode("ascii")

    proof = RegistryProof(
        schema_version="foxlight.registry-proof.v1",
        anchor_sha256=digest(trusted_root),
        root_chain=tuple(encoded(root) for root in root_chain),
        timestamp=encoded(timestamp),
        snapshot=encoded(snapshot),
        targets=encoded(targets),
        target_path=target_path,
        target=encoded(target),
    )
    if len(encode_record(proof)) > MAX_METADATA_BYTES:
        raise PreservationError("encoded proof exceeds metadata limit")
    return proof


def _selected_card(
    data: bytes, path: str, expected_card_id: str
) -> tuple[ModelCardEnvelope, Literal["foxlight", "agent", "community"] | None]:
    if path == f"v1/cards/{expected_card_id}.json":
        return load_card(data), None
    if path != "v1/catalog.json":
        raise PreservationError("unsupported proof target path")
    catalog = _bounded_json(data)
    if not isinstance(catalog, dict):
        raise PreservationError("invalid registry catalog")
    cards, metadata = catalog.get("cards"), catalog.get("card_metadata")
    if not isinstance(cards, list) or not isinstance(metadata, dict):
        raise PreservationError("catalog requires cards and provenance metadata")
    selected = [
        card
        for card in cards
        if isinstance(card, dict) and card.get("card_id") == expected_card_id
    ]
    if len(selected) != 1:
        raise PreservationError("proof catalog must contain exactly one selected card")
    evidence = metadata.get(expected_card_id)
    provenance = evidence.get("provenance") if isinstance(evidence, dict) else None
    if provenance not in {"foxlight", "agent", "community"}:
        raise PreservationError("catalog omits the selected card's provenance")
    # Narrow a JSON value without accepting arbitrary future provenance labels.
    tier: Literal["foxlight", "agent", "community"]
    if provenance == "foxlight":
        tier = "foxlight"
    elif provenance == "agent":
        tier = "agent"
    else:
        tier = "community"
    return load_card(identity_json(selected[0])), tier


def verify_registry_proof(
    data: bytes,
    trusted_root: bytes,
    expected_card_id: str,
    *,
    mode: ProofMode = "fresh",
    minimum_versions: VersionFloor | None = None,
    reference_time: datetime | None = None,
) -> VerifiedCard:
    """Verify exact card publication against receiver trust, without contacting hosts.

    Historical mode explicitly permits expired signed metadata. It neither
    establishes current freshness nor discovers later compromise/revocation.
    Supplied rollback floors still apply. A failed proof never downgrades itself
    to a community card. This function does not verify model file bytes.
    """
    if mode not in {"fresh", "historical"}:
        raise PreservationError("unsupported proof verification mode")
    proof = RegistryProof.model_validate_json(identity_json(_bounded_json(data)))
    if proof.anchor_sha256 != digest(trusted_root):
        raise PreservationError("proof does not match the independently enrolled root")
    now = reference_time or datetime.now(UTC)
    if now.tzinfo is None:
        raise PreservationError("proof reference time must include a timezone")
    try:
        return _verify(
            proof, trusted_root, expected_card_id, mode, minimum_versions, now
        )
    except (RepositoryError, ValueError, TypeError, KeyError) as error:
        if isinstance(error, PreservationError):
            raise
        raise PreservationError("registry proof verification failed") from error


def _verify(
    proof: RegistryProof,
    trusted_root: bytes,
    expected_card_id: str,
    mode: ProofMode,
    minimum_versions: VersionFloor | None,
    now: datetime,
) -> VerifiedCard:
    root_md = _metadata(trusted_root, Root)
    root = root_md.signed
    root.verify_delegate("root", root_md.signed_bytes, root_md.signatures)
    for encoded in proof.root_chain:
        successor = _metadata(decode_base64(encoded), Root)
        if successor.signed.version != root.version + 1:
            raise PreservationError("root rotations must be consecutive")
        root.verify_delegate("root", successor.signed_bytes, successor.signatures)
        successor.signed.verify_delegate(
            "root", successor.signed_bytes, successor.signatures
        )
        root = successor.signed
    ts_raw = decode_base64(proof.timestamp)
    snapshot_raw = decode_base64(proof.snapshot)
    targets_raw = decode_base64(proof.targets)
    target_raw = decode_base64(proof.target)
    ts_md, snapshot_md, targets_md = (
        _metadata(ts_raw, Timestamp),
        _metadata(snapshot_raw, Snapshot),
        _metadata(targets_raw, Targets),
    )
    for name, metadata in (
        ("timestamp", ts_md),
        ("snapshot", snapshot_md),
        ("targets", targets_md),
    ):
        root.verify_delegate(name, metadata.signed_bytes, metadata.signatures)
    ts, snapshot, targets = ts_md.signed, snapshot_md.signed, targets_md.signed
    snapshot_info = ts.snapshot_meta
    targets_info = snapshot.meta["targets.json"]
    for info, raw, version in (
        (snapshot_info, snapshot_raw, snapshot.version),
        (targets_info, targets_raw, targets.version),
    ):
        if (
            info.version != version
            or info.length is None
            or info.hashes is None
            or "sha256" not in info.hashes
        ):
            raise PreservationError("proof requires exact SHA-256 metadata bindings")
        info.verify_length_and_hashes(raw)
    target_info = targets.targets[proof.target_path]
    if "sha256" not in target_info.hashes:
        raise PreservationError("proof requires a SHA-256 target binding")
    target_info.verify_length_and_hashes(target_raw)
    versions = VersionFloor(
        root=root.version,
        timestamp=ts.version,
        snapshot=snapshot.version,
        targets=targets.version,
    )
    floors = minimum_versions or VersionFloor()
    if any(
        getattr(versions, role) < getattr(floors, role)
        for role in ("root", "timestamp", "snapshot", "targets")
    ):
        raise PreservationError("proof is below receiver-retained rollback floors")
    expired = tuple(
        name
        for name, signed in (
            ("root", root),
            ("timestamp", ts),
            ("snapshot", snapshot),
            ("targets", targets),
        )
        if signed.is_expired(now)
    )
    if mode == "fresh" and expired:
        raise PreservationError("registry proof has expired metadata")
    card, provenance = _selected_card(target_raw, proof.target_path, expected_card_id)
    if card.card_id != expected_card_id:
        raise PreservationError("registry proof contains a different card")
    return VerifiedCard(
        card,
        ProofReport(
            card_id=card.card_id,
            mode=mode,
            anchor_sha256=proof.anchor_sha256,
            target_path=proof.target_path,
            target_sha256=digest(target_raw),
            versions=versions,
            expired_roles=expired,
            provenance=provenance,
            checked_at=now.astimezone(UTC).isoformat(),
        ),
    )
