"""Portable public-model handoffs with offline metadata and explicit recipient trust."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, final
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from .models import (
    MAX_METADATA_BYTES,
    Digest,
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
    strict_json,
    verify_manifest,
)
from .storage import directory, manifest_path, read_metadata
from .transfer import DEFAULT_POLICY, TransferPolicy, fetch


def _mirrors(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for source in values:
        if len(source) > 2048 or any(
            ord(char) <= 32 or ord(char) == 127 or char == "\\" for char in source
        ):
            raise PreservationError("invitation mirror must be a bounded HTTP(S) base")
        try:
            parsed = urlsplit(source)
            port = parsed.port
        except ValueError as error:
            raise PreservationError("invalid invitation mirror") from error
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise PreservationError(
                "invitation mirrors cannot carry credentials, queries or local paths"
            )
        normalized.append(source.rstrip("/"))
    if len(set(normalized)) != len(normalized):
        raise PreservationError("duplicate invitation mirror")
    return tuple(normalized)


@final
class Invitation(Record):
    """Bounded metadata-only handoff; embedded keys are candidates, not authority.

    The original DSSE envelope binds the exact complete model closure. Mirror
    hints remain unsigned, untrusted transport data. Neither reading this record
    nor matching its self-signatures authenticates a publisher, performs network
    IO, grants registry authority, or authorizes runtime execution.
    """

    schema_version: Literal["foxlight.model-preservation.invitation.v1"]
    manifest_id: Digest
    envelope: Envelope
    candidate_trust: TrustProfile
    mirrors: tuple[str, ...] = Field(min_length=1, max_length=16)

    _mirror_bases = field_validator("mirrors")(_mirrors)

    @model_validator(mode="after")
    def validate_public_metadata(self) -> Invitation:
        """Authenticate candidate signatures, exact identity, audience and bounds."""
        if len(encode_record(self)) > MAX_METADATA_BYTES:
            raise PreservationError("invitation exceeds metadata allowance")
        manifest = self.manifest
        if any(member.intake.audience != "public" for member in manifest.members):
            raise PreservationError("invitations require an already public bundle")
        return self

    @property
    def manifest(self) -> Manifest:
        """Inspect candidate signatures without authenticating the owner."""
        return verify_manifest(
            encode_record(self.envelope), self.candidate_trust, self.manifest_id
        )


def read_invitation(data: bytes) -> Invitation:
    """Inspect a bounded invitation offline, without trusting its candidate owner.

    Duplicate/unknown fields, unknown versions, bad signatures/digests, private
    members and unsafe mirror syntax refuse before any DNS or filesystem fetch.
    Unknown descriptive card properties remain inside the original envelope.
    """
    try:
        return Invitation.model_validate_json(canonical_bytes(strict_json(data)))
    except ValueError as error:
        # Pydantic errors can include the complete peer-supplied input, including
        # accidentally pasted credentials. Keep CLI/control refusals bounded.
        raise PreservationError("invalid or unsafe public invitation") from error


def invitation_from_envelope(
    envelope: bytes, manifest_id: str, trust: TrustProfile, mirrors: tuple[str, ...]
) -> Invitation:
    """Prepare a public handoff from caller-authenticated metadata without IO.

    Keys become recipient candidates only. This checks signatures and public
    audience, not stored weight bytes, license grants, or mirror reachability.
    No invitation signing key, account, registry or hosted resolver is needed.
    """
    try:
        return Invitation(
            schema_version="foxlight.model-preservation.invitation.v1",
            manifest_id=manifest_id,
            envelope=Envelope.model_validate_json(
                canonical_bytes(strict_json(envelope))
            ),
            candidate_trust=trust,
            mirrors=mirrors,
        )
    except ValueError as error:
        raise PreservationError(
            "invalid or unsafe public invitation metadata"
        ) from error


def create_invitation(
    store: Path, manifest_id: str, trust: TrustProfile, mirrors: tuple[str, ...]
) -> Invitation:
    """Read only the selected public envelope and create an offline invitation.

    The explicit local caller owns the store path. Weight availability is not
    asserted and no listener or swarm starts. A valid bundle too large for an
    invitation stays intact and can still be transferred as a public recovery kit.
    """
    with directory(store) as root:
        envelope = read_metadata(root, manifest_path(manifest_id))
    return invitation_from_envelope(envelope, manifest_id, trust, mirrors)


def authenticate_invitation(invitation: Invitation, trust: TrustProfile) -> Manifest:
    """Authenticate the exact included manifest using independently supplied trust.

    This never adopts embedded candidate keys or mirror operators as trusted.
    Signature/key/threshold failure occurs offline, before model-byte requests.
    The returned public metadata does not establish current registry status or
    compatibility and authorizes no loading, repository code or public serving.
    """
    checked = read_invitation(encode_record(invitation))
    return verify_manifest(encode_record(checked.envelope), trust, checked.manifest_id)


def accept_invitation_keys(
    invitation: Invitation, key_ids: tuple[str, ...]
) -> TrustProfile:
    """Make a fresh exact-copy trust choice from independently authenticated IDs.

    Callers must authenticate these fingerprints and the intended threshold via
    a channel they trust. This is explicit first-contact acceptance, never global
    enrollment or a replacement for an existing revocation/threshold policy.
    The stable copy-scoped label permits an interrupted transfer's budget to
    resume without inventing a new trust time on every call.
    """
    checked = read_invitation(encode_record(invitation))
    chosen = set(key_ids)
    candidates = checked.candidate_trust
    if (
        not chosen
        or len(chosen) != len(key_ids)
        or not chosen.issubset(key.key_id for key in candidates.keys)
    ):
        raise PreservationError(
            "accept only independently authenticated invitation fingerprints"
        )
    try:
        trust = TrustProfile(
            schema_version="foxlight.model-preservation.trust.v1",
            keys=tuple(key for key in candidates.keys if key.key_id in chosen),
            threshold=candidates.threshold,
            revoked_keys=candidates.revoked_keys,
            as_of="explicit-invitation:" + checked.manifest_id,
        )
    except ValueError as error:
        raise PreservationError(
            "accepted fingerprints do not satisfy the candidate trust policy"
        ) from error
    authenticate_invitation(checked, trust)
    return trust


def fetch_invitation(
    invitation: Invitation,
    trust: TrustProfile,
    destination: Path,
    policy: TransferPolicy = DEFAULT_POLICY,
    *,
    control: OperationControl | None = None,
    operation_id: str | None = None,
) -> Manifest:
    """Receive the exact public copy with existing pinned-DNS/budget verification.

    Args:
        invitation: Reviewed metadata and bounded HTTP(S) transport hints.
        trust: Independently enrolled or explicitly fingerprint-accepted keys.
        destination: Caller-owned local receive store, never a hint from the peer.
        policy: Finite allowance/deadlines; HTTP/private addresses need opt-in.
        control: Optional cooperative progress/cancellation owner.
        operation_id: Retained identity for cumulative allowance across retries.

    Sender authentication fails before any source is contacted. The existing
    fetcher then reauthenticates the same manifest before weight requests and
    verifies every object before complete publication. It never seeds, restores,
    admits models or runs code. Direct connections expose the receiver's network
    address to mirrors; this provides integrity, not anonymity.
    """
    checked = read_invitation(encode_record(invitation))
    authenticate_invitation(checked, trust)
    return fetch(
        checked.manifest_id,
        trust,
        checked.mirrors,
        destination,
        policy,
        control=control,
        operation_id=operation_id,
    )
