"""Bind retained public cards to captures without upgrading owner statements."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, final

from pydantic import Field, model_validator

from .cards import CardId, ModelCardEnvelope, identity_json
from .models import ManifestMember, PreservationError, Record
from .registry_proof import (
    ProofMode,
    ProofReport,
    RegistryProof,
    VersionFloor,
    verify_registry_proof,
)
from .signing import encode_record, strict_json


@final
class RetainedCard(Record):
    """Optional typed profile inside the existing manifest retained_card field."""

    schema_version: Literal["foxlight.retained-card.v1"]
    envelope: ModelCardEnvelope
    registry_proof: RegistryProof | None = None


@final
class MemberEvidence(Record):
    """Metadata assurance only; callers must also verify the actual stored bytes."""

    member_id: str
    card_id: CardId
    publication: Literal["owner-only", "registry-fresh", "registry-historical"]
    file_coverage: Literal["complete-sha256", "partial-or-missing"]
    unbound_paths: tuple[str, ...] = Field(max_length=100_000)
    proof: ProofReport | None
    qualification: Literal["not-assessed"] = "not-assessed"

    @model_validator(mode="after")
    def complete_coverage(self) -> MemberEvidence:
        """Keep a report's coverage label consistent with its explicit gaps."""
        if (not self.unbound_paths) != (self.file_coverage == "complete-sha256"):
            raise PreservationError("file evidence coverage disagrees with its gaps")
        return self


def load_retained_card(data: bytes) -> RetainedCard:
    """Read bounded card/proof data, without implicitly trusting its signing keys."""
    return RetainedCard.model_validate_json(identity_json(strict_json(data)))


def inspect_member_evidence(
    member: ManifestMember,
    *,
    trusted_root: bytes | None = None,
    mode: ProofMode = "fresh",
    minimum_versions: VersionFloor | None = None,
    reference_time: datetime | None = None,
) -> MemberEvidence:
    """Check card/selection agreement and separately report publication/file evidence.

    A retained proof requires independent receiver trust. Invalid proofs and
    contradictory file identities fail; they never silently downgrade. Missing
    SHA-256 evidence remains an explicit gap even if the card was published by
    a trusted registry. The owning bundle's signature and actual bytes must be
    verified separately, before using this metadata report for an import.
    """
    if member.retained_card is None:
        raise PreservationError("member has no public retained-card profile")
    retained = load_retained_card(identity_json(member.retained_card))
    card, proof_report = retained.envelope, None
    publication: Literal["owner-only", "registry-fresh", "registry-historical"]
    publication = "owner-only"
    if retained.registry_proof is not None:
        if trusted_root is None:
            raise PreservationError("retained registry proof needs an enrolled root")
        verified = verify_registry_proof(
            encode_record(retained.registry_proof),
            trusted_root,
            card.card_id,
            mode=mode,
            minimum_versions=minimum_versions,
            reference_time=reference_time,
        )
        if verified.card != card:
            raise PreservationError("retained card differs from its registry proof")
        proof_report = verified.report
        publication = "registry-fresh" if mode == "fresh" else "registry-historical"
    artifact = card.artifact
    bundle = artifact.bundle
    allowed_identities = {card.card_id}
    if bundle is not None:
        allowed_identities.add(bundle.bundle_id)
    if (
        member.origin_repository != artifact.repository
        or member.origin_revision != artifact.revision
        or member.artifact_identity not in allowed_identities
        or member.format != artifact.format
        or member.loader_root != (bundle.root if bundle is not None else None)
    ):
        raise PreservationError("capture selection differs from its retained card")
    declared = {file.path: file for file in bundle.files} if bundle else {}
    actual = {file.path: file for file in member.files}
    if not set(declared).issubset(actual):
        raise PreservationError("capture omits files selected by its retained card")
    unbound: list[str] = []
    for path, file in actual.items():
        expected = declared.get(path)
        if expected is not None and file.size != expected.size_bytes:
            raise PreservationError("capture size differs from retained card evidence")
        if (
            expected is None
            or expected.object_id is None
            or not expected.object_id.startswith("sha256:")
        ):
            unbound.append(path)
        elif expected.object_id != "sha256:" + file.sha256:
            raise PreservationError("capture hash differs from retained card evidence")
    return MemberEvidence(
        member_id=member.member_id,
        card_id=card.card_id,
        publication=publication,
        file_coverage="partial-or-missing" if unbound else "complete-sha256",
        unbound_paths=tuple(sorted(unbound)),
        proof=proof_report,
    )
