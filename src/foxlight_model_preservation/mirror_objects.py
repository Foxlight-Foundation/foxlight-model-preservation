"""Signed object coverage: which file hashes a mirror serves.

A mirror card says which *manifests* a mirror probably holds. A save from the
registry catalog has only file hashes (the signed card pins each file's
SHA-256), so it can't use manifest coverage. This separate document, served at
``/.well-known/sovereign/objects.json`` and signed by the same mirror key,
lists the mirror's object hashes as a Bloom filter. It is a separate document
so clients that validate mirror cards strictly keep working.

Like a card it grants nothing: a false or stale claim costs one failed request,
because every object is still checked against the hash a client already trusts.
"""

from __future__ import annotations

import base64
import os
import re
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Literal, Self

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import Field, model_validator

from .mirror_cards import (
    CLOCK_SKEW,
    FILTER_HASHES,
    MAX_CARD_SECONDS,
    filter_positions,
)
from .models import Digest, PreservationError, Record, TrustedKey, TrustProfile
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

OBJECTS_TYPE = "application/vnd.sovereign.mirror-objects.v1+json"
OBJECTS_PATH = ".well-known/sovereign/objects.json"
# About 300,000 objects; small enough to fit a gossip round's reservation.
MAX_OBJECTS_BYTES = 512 * 1024
# The filter is base64 inside a base64 envelope (about 16/9 of its raw size),
# so 256 KiB of filter always fits; past ~210,000 objects the false-positive
# rate rises instead of the document outgrowing its budget.
MAX_FILTER_BYTES = 256 * 1024


def object_filter(hashes: Iterable[str]) -> tuple[str, int]:
    """Encode object SHA-256s as a Bloom filter (about one percent errors)."""
    unique = set(hashes)
    bits = min(MAX_FILTER_BYTES * 8, max(1024, (len(unique) * 10 + 7) // 8 * 8))
    array = bytearray(bits // 8)
    for sha256 in unique:
        for position in filter_positions(sha256, bits, FILTER_HASHES):
            array[position // 8] |= 1 << (position % 8)
    return base64.b64encode(bytes(array)).decode("ascii"), len(unique)


class MirrorObjects(Record):
    """The object hashes one mirror serves; never a grant of trust."""

    schema_version: Literal["sovereign.mirror-objects.v1"] = (
        "sovereign.mirror-objects.v1"
    )
    mirror_key: TrustedKey
    coverage: str = Field(min_length=4, max_length=MAX_OBJECTS_BYTES)
    coverage_count: int = Field(ge=0, le=100_000_000)
    issued_at: int = Field(ge=0)
    expires_at: int = Field(ge=0)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        """Short-lived like a card, with a well-formed filter."""
        if not self.issued_at < self.expires_at <= self.issued_at + MAX_CARD_SECONDS:
            raise PreservationError("object coverage lifetime must be at most 14 days")
        try:
            raw = base64.b64decode(self.coverage, validate=True)
        except ValueError as error:
            raise PreservationError("object coverage is not base64") from error
        if len(raw) < 128:
            raise PreservationError("object coverage filter is too small")
        return self

    def covers(self, sha256: Digest) -> bool:
        """Report whether this mirror probably serves an object (no false negatives)."""
        array = base64.b64decode(self.coverage)
        bits = len(array) * 8
        return all(
            array[position // 8] & (1 << (position % 8))
            for position in filter_positions(sha256, bits, FILTER_HASHES)
        )


def issue_objects(
    key: Ed25519PrivateKey,
    hashes: Iterable[str],
    *,
    lifetime: int = 7 * 86400,
    now: int | None = None,
) -> bytes:
    """Sign this mirror's object coverage with its mirror key."""
    instant = int(time.time()) if now is None else now
    coverage, count = object_filter(hashes)
    document = MirrorObjects(
        mirror_key=public_record(key),
        coverage=coverage,
        coverage_count=count,
        issued_at=instant,
        expires_at=instant + lifetime,
    )
    envelope = encode_record(sign_record(document, (key,), OBJECTS_TYPE))
    if len(envelope) > MAX_OBJECTS_BYTES:
        raise PreservationError("object coverage exceeds size limit")
    return envelope


def verify_objects(
    data: bytes, mirror_key: TrustedKey, *, now: int | None = None
) -> MirrorObjects:
    """Accept object coverage only when the card's own mirror key signed it."""
    if len(data) > MAX_OBJECTS_BYTES:
        raise PreservationError("object coverage exceeds size limit")
    envelope = Envelope.model_validate_json(
        canonical_bytes(strict_json(data, maximum_bytes=MAX_OBJECTS_BYTES))
    )
    if envelope.payloadType != OBJECTS_TYPE:
        raise PreservationError("not object coverage")
    payload = decode_base64(envelope.payload)
    claimed = MirrorObjects.model_validate_json(
        canonical_bytes(strict_json(payload, maximum_bytes=MAX_OBJECTS_BYTES))
    )
    if claimed.mirror_key != mirror_key:
        raise PreservationError("object coverage names another mirror key")
    verify_payload(
        data,
        TrustProfile(
            schema_version="foxlight.model-preservation.trust.v1",
            keys=(mirror_key,),
            threshold=1,
            as_of="mirror-objects-self-signature",
        ),
        digest(payload),
        OBJECTS_TYPE,
    )
    instant = int(time.time()) if now is None else now
    if claimed.issued_at > instant + CLOCK_SKEW:
        raise PreservationError("object coverage is issued in the future")
    if claimed.expires_at <= instant:
        raise PreservationError("object coverage has expired")
    return claimed


def served_hashes(store: Path) -> set[str]:
    """Object hashes present in a store or public tree (``objects/sha256/*``)."""
    try:
        names = os.listdir(store / "objects" / "sha256")
    except FileNotFoundError:
        return set()
    return {name for name in names if re.fullmatch(r"[0-9a-f]{64}", name)}
