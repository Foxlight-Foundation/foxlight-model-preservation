"""Signed, expiring mirror cards and the peers document mirrors gossip.

A mirror card says where one mirror can be reached and which manifests it
probably holds. It is self-certifying: it is signed by the mirror key it names,
so nobody can forge another mirror's card. A card grants no trust at all.
Every byte fetched from any mirror is still verified against independently
chosen model keys, so a wrong or hostile card costs one failed connection and
can never substitute a model. That is what lets discovery be decentralized.
"""

from __future__ import annotations

import base64
import hashlib
import time
from typing import Literal, Self
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import Field, field_validator, model_validator

from .models import (
    MAX_METADATA_BYTES,
    Digest,
    PreservationError,
    Record,
    TrustedKey,
    TrustProfile,
)
from .signing import (
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

CARD_TYPE = "application/vnd.sovereign.mirror-card.v1+json"
PEERS_PATH = ".well-known/sovereign/peers.json"
MAX_CARD_SECONDS = 14 * 86400
CLOCK_SKEW = 300
MAX_PEERS = 64
MAX_PEERS_BYTES = 1024 * 1024
FILTER_HASHES = 7


def base_url(value: str) -> str:
    """Validate a credential-free HTTP(S) base URL without a trailing slash."""
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(char in value for char in "\r\n\\ ")
        or len(value) > 2048
    ):
        raise PreservationError("mirror address must be a plain HTTP(S) base URL")
    return value.rstrip("/")


def _positions(manifest_id: str, bits: int, hashes: int) -> list[int]:
    return [
        int.from_bytes(
            hashlib.sha256(f"{index}:{manifest_id}".encode()).digest()[:8], "big"
        )
        % bits
        for index in range(hashes)
    ]


def coverage_filter(manifest_ids: set[str] | frozenset[str]) -> tuple[str, int]:
    """Encode manifest IDs as a compact Bloom filter (about one percent errors).

    Returns the base64 bit array and the number of identities it contains. A
    false positive only costs a client one manifest request.
    """
    bits = max(1024, (len(manifest_ids) * 10 + 7) // 8 * 8)
    array = bytearray(bits // 8)
    for manifest_id in manifest_ids:
        for position in _positions(manifest_id, bits, FILTER_HASHES):
            array[position // 8] |= 1 << (position % 8)
    return base64.b64encode(bytes(array)).decode("ascii"), len(manifest_ids)


class MirrorCard(Record):
    """Where one mirror is and what it probably holds; never a grant of trust."""

    schema_version: Literal["sovereign.mirror-card.v1"] = "sovereign.mirror-card.v1"
    mirror_key: TrustedKey
    addresses: tuple[str, ...] = Field(min_length=1, max_length=8)
    coverage: str = Field(min_length=4, max_length=200_000)
    coverage_count: int = Field(ge=0, le=1_000_000)
    catalogs: tuple[Digest, ...] = Field(default=(), max_length=64)
    region: str | None = Field(default=None, min_length=1, max_length=64)
    contact: str | None = Field(default=None, min_length=1, max_length=200)
    issued_at: int = Field(ge=0)
    expires_at: int = Field(ge=0)

    @field_validator("addresses")
    @classmethod
    def plain_addresses(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Accept only credential-free HTTP(S) base URLs, once each."""
        result = tuple(dict.fromkeys(base_url(value) for value in values))
        return result

    @model_validator(mode="after")
    def bounded_lifetime(self) -> Self:
        """Cards are short-lived: a mirror that stops renewing simply lapses."""
        if not self.issued_at < self.expires_at <= self.issued_at + MAX_CARD_SECONDS:
            raise PreservationError("mirror card lifetime must be at most 14 days")
        try:
            raw = base64.b64decode(self.coverage, validate=True)
        except ValueError as error:
            raise PreservationError("mirror card coverage is not base64") from error
        if len(raw) < 128:
            raise PreservationError("mirror card coverage filter is too small")
        return self

    def covers(self, manifest_id: str) -> bool:
        """Report whether this mirror probably holds a manifest (no false negatives)."""
        array = base64.b64decode(self.coverage)
        bits = len(array) * 8
        return all(
            array[position // 8] & (1 << (position % 8))
            for position in _positions(manifest_id, bits, FILTER_HASHES)
        )


def issue_card(
    key: Ed25519PrivateKey,
    addresses: tuple[str, ...],
    manifest_ids: set[str] | frozenset[str],
    *,
    catalogs: tuple[str, ...] = (),
    region: str | None = None,
    contact: str | None = None,
    lifetime: int = 7 * 86400,
    now: int | None = None,
) -> bytes:
    """Sign a fresh card for this mirror with its own mirror key."""
    issued = int(time.time()) if now is None else now
    coverage, count = coverage_filter(manifest_ids)
    card = MirrorCard(
        mirror_key=public_record(key),
        addresses=addresses,
        coverage=coverage,
        coverage_count=count,
        catalogs=catalogs,
        region=region,
        contact=contact,
        issued_at=issued,
        expires_at=issued + lifetime,
    )
    envelope = encode_record(sign_record(card, (key,), CARD_TYPE))
    if len(envelope) > MAX_METADATA_BYTES:
        raise PreservationError("mirror card exceeds size limit")
    return envelope


def verify_card(data: bytes, *, now: int | None = None) -> MirrorCard:
    """Check a card's own signature and freshness; grant it nothing more."""
    envelope = Envelope.model_validate_json(canonical_bytes(strict_json(data)))
    if envelope.payloadType != CARD_TYPE:
        raise PreservationError("not a mirror card")
    payload = decode_base64(envelope.payload)
    claimed = MirrorCard.model_validate_json(canonical_bytes(strict_json(payload)))
    verify_payload(
        data,
        TrustProfile(
            schema_version="foxlight.model-preservation.trust.v1",
            keys=(claimed.mirror_key,),
            threshold=1,
            as_of="mirror-card-self-signature",
        ),
        digest(payload),
        CARD_TYPE,
    )
    instant = int(time.time()) if now is None else now
    if claimed.issued_at > instant + CLOCK_SKEW:
        raise PreservationError("mirror card is issued in the future")
    if claimed.expires_at <= instant:
        raise PreservationError("mirror card has expired")
    return claimed


class PeersDocument(Record):
    """What a mirror serves at /.well-known/sovereign/peers.json.

    ``self`` is the serving mirror's own card; ``peers`` are other mirrors'
    cards it has itself checked by fetching matching bytes. Clients still verify
    every card and every byte, so a dishonest list only wastes attempts.
    """

    schema_version: Literal["sovereign.peers.v1"] = "sovereign.peers.v1"
    self_card: Envelope | None = Field(default=None, alias="self")
    peers: tuple[Envelope, ...] = Field(default=(), max_length=MAX_PEERS)


def build_peers(self_card: bytes | None, peers: tuple[bytes, ...]) -> bytes:
    """Encode a bounded peers document from exact signed card envelopes."""
    document = PeersDocument.model_validate(
        {
            "schema_version": "sovereign.peers.v1",
            "self": Envelope.model_validate_json(self_card) if self_card else None,
            "peers": tuple(
                Envelope.model_validate_json(item) for item in peers[:MAX_PEERS]
            ),
        }
    )
    data = canonical_bytes(document.model_dump(mode="json", by_alias=True))
    if len(data) > MAX_PEERS_BYTES:
        raise PreservationError("peers document exceeds size limit")
    return data


def read_peers(data: bytes, *, now: int | None = None) -> list[bytes]:
    """Return every card in a peers document that verifies; skip the rest."""
    if len(data) > MAX_PEERS_BYTES:
        raise PreservationError("peers document exceeds size limit")
    document = PeersDocument.model_validate_json(
        canonical_bytes(strict_json(data, maximum_bytes=MAX_PEERS_BYTES))
    )
    cards = (
        (document.self_card,) if document.self_card is not None else ()
    ) + document.peers
    result: list[bytes] = []
    for envelope in cards:
        raw = encode_record(envelope)
        try:
            verify_card(raw, now=now)
        except (ValueError, PreservationError):
            continue
        result.append(raw)
    return result
