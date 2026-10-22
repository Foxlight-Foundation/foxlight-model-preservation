"""RFC 8785 payload identities and Ed25519 DSSE signatures with explicit trust."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import Field, JsonValue

from .models import (
    MAX_METADATA_BYTES,
    Catalog,
    KeyId,
    Manifest,
    PreservationError,
    Record,
    TrustedKey,
    TrustProfile,
)

CATALOG_TYPE = "application/vnd.foxlight.model-preservation.catalog.v1+json"
BUNDLE_TYPE = "application/vnd.foxlight.model-preservation.v1+json"


class Signature(Record):
    """One Ed25519 signature in the standard DSSE envelope."""

    keyid: KeyId
    sig: str = Field(min_length=80, max_length=100)


class Envelope(Record):
    """Bounded standard DSSE envelope; keys are never inferred from this object."""

    payloadType: str = Field(min_length=1, max_length=200)
    payload: str = Field(max_length=MAX_METADATA_BYTES * 2)
    signatures: tuple[Signature, ...] = Field(min_length=1, max_length=32)


def canonical_bytes(value: JsonValue) -> bytes:
    """Encode a JSON value using RFC 8785 rather than a bespoke signing encoding."""
    return rfc8785.dumps(value)


def _unique_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise PreservationError("duplicate JSON object key")
        result[key] = value
    return result


def strict_json(data: bytes, *, maximum_bytes: int = MAX_METADATA_BYTES) -> JsonValue:
    """Reject oversize/ambiguous JSON under an explicit bounded parser limit.

    Signed formats retain the ordinary 8 MiB default. The discovery cache may
    explicitly select up to 32 MiB without changing any signed format's limits.
    """
    if not 1 <= maximum_bytes <= 4 * MAX_METADATA_BYTES:
        raise PreservationError("unsupported bounded JSON parser limit")
    if len(data) > maximum_bytes:
        raise PreservationError("metadata size exceeds limit")
    try:
        value = cast(JsonValue, json.loads(data, object_pairs_hook=_unique_object))
        canonical_bytes(value)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise PreservationError("invalid bounded JSON metadata") from error
    return value


def encode_record(record: Record) -> bytes:
    """Serialize strict metadata deterministically for storage and signing."""
    return canonical_bytes(cast(JsonValue, record.model_dump(mode="json")))


def digest(data: bytes) -> str:
    """Return the portable SHA-256 identity of exact bytes."""
    return hashlib.sha256(data).hexdigest()


def decode_base64(value: str) -> bytes:
    """Decode the standard or URL-safe base64 encodings permitted by DSSE."""
    try:
        return base64.b64decode(value, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as error:
        raise PreservationError("invalid base64") from error


def key_identity(public: bytes) -> str:
    """Identify Ed25519 keys by their complete raw-public-key SHA-256."""
    return "ed25519:" + digest(public)


def authenticated_encoding(payload_type: str, payload: bytes) -> bytes:
    """Apply the standard DSSE v1 pre-authentication encoding (PAE)."""
    kind = payload_type.encode("utf-8")
    return b"DSSEv1 %d " % len(kind) + kind + b" %d " % len(payload) + payload


def public_record(key: Ed25519PrivateKey) -> TrustedKey:
    """Export only the public part of an owner key for independent enrollment."""
    raw = key.public_key().public_bytes_raw()
    return TrustedKey(
        key_id=key_identity(raw), public_key=base64.b64encode(raw).decode("ascii")
    )


def create_key(path: Path, password: bytes | None) -> TrustProfile:
    """Create an owner PEM with private permissions and public trust data."""
    key = Ed25519PrivateKey.generate()
    encryption = (
        serialization.BestAvailableEncryption(password)
        if password
        else serialization.NoEncryption()
    )
    data = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption
    )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return TrustProfile(
        schema_version="foxlight.model-preservation.trust.v1",
        keys=(public_record(key),),
        threshold=1,
        as_of=datetime.now(UTC).isoformat(),
    )


def load_key(path: Path, password: bytes | None) -> Ed25519PrivateKey:
    """Load a locally supplied Ed25519 PEM without exposing key bytes in output."""
    if path.stat().st_mode & 0o077:
        raise PreservationError("private key must not be group/world accessible")
    key = serialization.load_pem_private_key(path.read_bytes(), password=password)
    if not isinstance(key, Ed25519PrivateKey):
        raise PreservationError("only Ed25519 keys are supported")
    return key


def sign_manifest(manifest: Manifest, keys: tuple[Ed25519PrivateKey, ...]) -> Envelope:
    """Sign canonical manifest bytes with domain-bound Ed25519 DSSE signatures."""
    return sign_record(manifest, keys, BUNDLE_TYPE)


def sign_record(
    record: Record, keys: tuple[Ed25519PrivateKey, ...], payload_type: str
) -> Envelope:
    """Sign canonical records in an explicit DSSE payload domain."""
    payload = encode_record(record)
    message = authenticated_encoding(payload_type, payload)
    signatures = tuple(
        Signature(
            keyid=public_record(key).key_id,
            sig=base64.b64encode(key.sign(message)).decode("ascii"),
        )
        for key in keys
    )
    return Envelope(
        payloadType=payload_type,
        payload=base64.b64encode(payload).decode("ascii"),
        signatures=signatures,
    )


def load_trust(data: bytes) -> TrustProfile:
    """Validate independent trust input, including key IDs matching raw public keys."""
    trust = TrustProfile.model_validate_json(canonical_bytes(strict_json(data)))
    for key in trust.keys:
        raw = decode_base64(key.public_key)
        if len(raw) != 32 or key_identity(raw) != key.key_id:
            raise PreservationError("trust key fingerprint mismatch")
    return trust


def verify_manifest(data: bytes, trust: TrustProfile, expected_digest: str) -> Manifest:
    """Authenticate the exact selected payload before parsing its model semantics."""
    return Manifest.model_validate_json(
        verify_payload(data, trust, expected_digest, BUNDLE_TYPE)
    )


def verify_catalog(data: bytes, trust: TrustProfile, expected_digest: str) -> Catalog:
    """Authenticate a pinned catalog separately from each selected model manifest."""
    return Catalog.model_validate_json(
        verify_payload(data, trust, expected_digest, CATALOG_TYPE)
    )


def verify_payload(
    data: bytes, trust: TrustProfile, expected_digest: str, payload_type: str
) -> bytes:
    """Verify domain, exact payload, and independent unique-key threshold."""
    envelope = Envelope.model_validate_json(canonical_bytes(strict_json(data)))
    if envelope.payloadType != payload_type:
        raise PreservationError("unsupported signed payload type")
    payload = decode_base64(envelope.payload)
    if digest(payload) != expected_digest:
        raise PreservationError("manifest does not match the selected identity")
    message = authenticated_encoding(payload_type, payload)
    accepted: set[str] = set()
    for key in trust.keys:
        raw = decode_base64(key.public_key)
        if len(raw) != 32 or key_identity(raw) != key.key_id:
            raise PreservationError("trust key fingerprint mismatch")
        if key.key_id in trust.revoked_keys:
            continue
        for signature in envelope.signatures:
            if signature.keyid != key.key_id:
                continue
            try:
                Ed25519PublicKey.from_public_bytes(raw).verify(
                    decode_base64(signature.sig), message
                )
            except InvalidSignature:
                continue
            accepted.add(key.key_id)
    if len(accepted) < trust.threshold:
        raise PreservationError("trusted signature threshold not met")
    value = strict_json(payload)
    if canonical_bytes(value) != payload:
        raise PreservationError("signed manifest is not RFC 8785 canonical JSON")
    return payload
