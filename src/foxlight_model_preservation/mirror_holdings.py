"""Signed holdings: the models a mirror keeps, so Find can search them.

A mirror card's coverage and ``objects.json`` are Bloom filters: they confirm a
model a client already knows about, but cannot be listed or searched. This
document, served at ``/.well-known/sovereign/holdings.json`` and signed by the
same mirror key, names each held copy (its manifest, name, origin, format,
size, licence and uses), so a model stays findable after its original host
removes it. It is a separate document because mirror cards are strict.

A list grants nothing. Names and origins are claims; a save still verifies
every byte by hash and labels whether the origin could be confirmed.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Literal, Self

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import Field, model_validator

from .mirror_cards import CLOCK_SKEW, MAX_CARD_SECONDS
from .models import (
    MAX_METADATA_BYTES,
    Digest,
    Manifest,
    ManifestMember,
    PreservationError,
    Record,
    TrustedKey,
    TrustProfile,
)
from .pmf import load_pmf
from .pmf_models import PMF_TYPE, PMFManifest
from .signing import (
    BUNDLE_TYPE,
    Envelope,
    canonical_bytes,
    decode_base64,
    digest,
    encode_record,
    public_record,
    sign_record,
    strict_json,
    verify_payload,
)
from .storage import manifest_path

HOLDINGS_TYPE = "application/vnd.sovereign.mirror-holdings.v1+json"
HOLDINGS_PATH = ".well-known/sovereign/holdings.json"
# About 2,000 compact entries; a larger list drops entries rather than fail.
MAX_HOLDINGS_BYTES = 1024 * 1024
MAX_ENTRIES = 2000
# A licence named only as a pointer to the original card is no licence name.
_NO_LICENCE = {"see-original-card", "unknown", "other", "none"}


class Holding(Record):
    """One held copy; every field is the mirror's claim, verified on save."""

    manifest: Digest
    name: str = Field(min_length=1, max_length=200)
    origin_repository: str | None = Field(default=None, min_length=3, max_length=200)
    origin_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    format: str = Field(min_length=1, max_length=40)
    size_bytes: int = Field(ge=0)
    file_count: int = Field(ge=1, le=100_000)
    licence: str | None = Field(default=None, min_length=1, max_length=120)
    workloads: tuple[str, ...] = Field(default=(), max_length=16)


class MirrorHoldings(Record):
    """The copies one mirror keeps; never a grant of trust."""

    schema_version: Literal["sovereign.mirror-holdings.v1"] = (
        "sovereign.mirror-holdings.v1"
    )
    mirror_key: TrustedKey
    entries: tuple[Holding, ...] = Field(max_length=MAX_ENTRIES)
    issued_at: int = Field(ge=0)
    expires_at: int = Field(ge=0)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        """Short-lived like a card; each copy listed once."""
        if not self.issued_at < self.expires_at <= self.issued_at + MAX_CARD_SECONDS:
            raise PreservationError("holdings lifetime must be at most 14 days")
        manifests = [entry.manifest for entry in self.entries]
        if len(set(manifests)) != len(manifests):
            raise PreservationError("holdings list a copy twice")
        return self


def holding(manifest_id: str, manifest: PMFManifest | Manifest) -> Holding:
    """Describe a held copy from its own manifest (PMF or the earlier format)."""
    root = next(
        member
        for member in manifest.members
        if member.member_id == manifest.root_member
    )
    if isinstance(root, ManifestMember):
        repository, revision = root.origin_repository, root.origin_revision
    else:
        origin = root.origin
        repository = origin.repository if origin is not None else None
        revision = origin.revision if origin is not None else None
    licence = root.intake.license_id.strip().lower()
    return Holding(
        manifest=manifest_id,
        name=manifest.name[:200],
        origin_repository=repository[:200]
        if repository and len(repository) >= 3
        else None,
        origin_revision=revision
        if revision is not None and re.fullmatch(r"[0-9a-f]{40}", revision)
        else None,
        format=root.format[:40],
        size_bytes=sum(
            file.size for member in manifest.members for file in member.files
        ),
        file_count=sum(len(member.files) for member in manifest.members),
        licence=None if licence in _NO_LICENCE or not licence else licence[:120],
        workloads=tuple(item[:60] for item in manifest.workloads[:16] if item),
    )


def issue_holdings(
    key: Ed25519PrivateKey,
    entries: Iterable[Holding],
    *,
    lifetime: int = 7 * 86400,
    now: int | None = None,
) -> bytes:
    """Sign this mirror's holdings, keeping the list within its byte budget."""
    instant = int(time.time()) if now is None else now
    unique: dict[str, Holding] = {}
    for entry in entries:
        unique.setdefault(entry.manifest, entry)
    selected = sorted(unique.values(), key=lambda item: item.manifest)[:MAX_ENTRIES]
    while True:
        document = MirrorHoldings(
            mirror_key=public_record(key),
            entries=tuple(selected),
            issued_at=instant,
            expires_at=instant + lifetime,
        )
        envelope = encode_record(sign_record(document, (key,), HOLDINGS_TYPE))
        if len(envelope) <= MAX_HOLDINGS_BYTES:
            return envelope
        # A list that outgrows its budget drops entries rather than fail the
        # announce; the copies are still served and covered by objects.json.
        selected = selected[: len(selected) * 9 // 10]


def verify_holdings(
    data: bytes, mirror_key: TrustedKey, *, now: int | None = None
) -> MirrorHoldings:
    """Accept holdings only when the card's own mirror key signed them."""
    if len(data) > MAX_HOLDINGS_BYTES:
        raise PreservationError("holdings exceed size limit")
    envelope = Envelope.model_validate_json(
        canonical_bytes(strict_json(data, maximum_bytes=MAX_HOLDINGS_BYTES))
    )
    if envelope.payloadType != HOLDINGS_TYPE:
        raise PreservationError("not mirror holdings")
    payload = decode_base64(envelope.payload)
    claimed = MirrorHoldings.model_validate_json(
        canonical_bytes(strict_json(payload, maximum_bytes=MAX_HOLDINGS_BYTES))
    )
    if claimed.mirror_key != mirror_key:
        raise PreservationError("holdings name another mirror key")
    verify_payload(
        data,
        TrustProfile(
            schema_version="foxlight.model-preservation.trust.v1",
            keys=(mirror_key,),
            threshold=1,
            as_of="mirror-holdings-self-signature",
        ),
        digest(payload),
        HOLDINGS_TYPE,
    )
    instant = int(time.time()) if now is None else now
    if claimed.issued_at > instant + CLOCK_SKEW:
        raise PreservationError("holdings are issued in the future")
    if claimed.expires_at <= instant:
        raise PreservationError("holdings have expired")
    return claimed


def store_holdings(store: Path, manifests: Iterable[str]) -> list[Holding]:
    """Describe the copies a mirror store serves, from their own manifests.

    Each manifest's payload must hash to its identity, so an entry always
    describes the exact copy served; unreadable copies are left out.
    """
    entries: list[Holding] = []
    for manifest_id in sorted(set(manifests))[:MAX_ENTRIES]:
        try:
            raw = (store / manifest_path(manifest_id)).read_bytes()
            if len(raw) > MAX_METADATA_BYTES:
                continue
            envelope = Envelope.model_validate_json(
                canonical_bytes(strict_json(raw, maximum_bytes=MAX_METADATA_BYTES))
            )
            payload = decode_base64(envelope.payload)
            if digest(payload) != manifest_id:
                continue
            if envelope.payloadType == PMF_TYPE:
                described = holding(manifest_id, load_pmf(payload))
            elif envelope.payloadType == BUNDLE_TYPE:
                described = holding(
                    manifest_id,
                    Manifest.model_validate_json(canonical_bytes(strict_json(payload))),
                )
            else:
                continue
            entries.append(described)
        except (OSError, ValueError, PreservationError):
            continue
    return entries
