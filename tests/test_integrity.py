"""Trust, format and complete-closure failures must be closed by default."""

import base64
import json

import pytest
from conftest import Capture
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from foxlight_model_preservation.models import PreservationError, Recipe, canonical_path
from foxlight_model_preservation.signing import (
    BUNDLE_TYPE,
    authenticated_encoding,
    canonical_bytes,
    digest,
    encode_record,
    public_record,
    sign_manifest,
    strict_json,
    verify_manifest,
)
from foxlight_model_preservation.storage import manifest_path, object_path, verify_store


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/file",
        "../file",
        "a/../file",
        "a//file",
        "./file",
        "a\\file",
        "a/CON",
        "a/file.",
        "a/file ",
        "a:b",
        "e\u0301",
        "a\x00b",
    ],
)
def test_unsafe_paths(path: str) -> None:
    with pytest.raises(PreservationError):
        canonical_path(path)


def test_dsse_pae_and_direct_ed25519(capture: Capture) -> None:
    assert authenticated_encoding("test", b"hello") == b"DSSEv1 4 test 5 hello"
    manifest = verify_store(capture.store, capture.identity, capture.trust)
    envelope = sign_manifest(manifest, (capture.key,))
    payload = base64.b64decode(envelope.payload)
    signature = base64.b64decode(envelope.signatures[0].sig)
    capture.key.public_key().verify(
        signature,
        b"DSSEv1 "
        + str(len(BUNDLE_TYPE)).encode()
        + b" "
        + BUNDLE_TYPE.encode()
        + b" "
        + str(len(payload)).encode()
        + b" "
        + payload,
    )
    with pytest.raises(InvalidSignature):
        capture.key.public_key().verify(signature, payload)


@pytest.mark.parametrize("mutation", ["payload", "signature", "domain", "identity"])
def test_changed_envelope(capture: Capture, mutation: str) -> None:
    envelope = json.loads(
        (capture.store / manifest_path(capture.identity)).read_bytes()
    )
    expected = capture.identity
    if mutation == "payload":
        payload = json.loads(base64.b64decode(envelope["payload"]))
        payload["name"] = "changed"
        replacement = canonical_bytes(payload)
        envelope["payload"] = base64.b64encode(replacement).decode()
        expected = digest(replacement)  # Even replacing both hash and payload fails.
    elif mutation == "signature":
        envelope["signatures"][0]["sig"] = base64.b64encode(bytes(64)).decode()
    elif mutation == "domain":
        envelope["payloadType"] = "application/untrusted"
    else:
        expected = "0" * 64
    with pytest.raises(PreservationError):
        verify_manifest(json.dumps(envelope).encode(), capture.trust, expected)


def test_unknown_signer_and_duplicate_threshold(capture: Capture) -> None:
    manifest = verify_store(capture.store, capture.identity, capture.trust)
    stranger = Ed25519PrivateKey.generate()
    envelope = sign_manifest(manifest, (stranger,))
    with pytest.raises(PreservationError, match="threshold"):
        verify_manifest(encode_record(envelope), capture.trust, capture.identity)
    trust = capture.trust.model_copy(
        update={
            "keys": (public_record(capture.key), public_record(stranger)),
            "threshold": 2,
        }
    )
    duplicate = sign_manifest(manifest, (capture.key, capture.key))
    with pytest.raises(PreservationError, match="threshold"):
        verify_manifest(encode_record(duplicate), trust, capture.identity)
    valid = sign_manifest(manifest, (capture.key, stranger))
    assert verify_manifest(encode_record(valid), trust, capture.identity) == manifest
    revoked = trust.model_copy(
        update={"revoked_keys": (public_record(stranger).key_id,)}
    )
    with pytest.raises(PreservationError, match="threshold"):
        verify_manifest(encode_record(valid), revoked, capture.identity)


def test_altered_object_rejected(capture: Capture) -> None:
    manifest = verify_store(capture.store, capture.identity, capture.trust)
    target = capture.store / object_path(manifest.members[0].files[0].sha256)
    target.write_bytes(b"changed")
    with pytest.raises(PreservationError):
        verify_store(capture.store, capture.identity, capture.trust)


@pytest.mark.parametrize("bad_json", [b'{"x":1,"x":2}', b'{"x":NaN}', b"[]x"])
def test_strict_json(bad_json: bytes) -> None:
    with pytest.raises(PreservationError):
        strict_json(bad_json)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "cycle",
        "unreachable",
        "notice",
        "collision",
        "parent",
        "directory-case",
    ],
)
def test_invalid_closure(capture: Capture, mutation: str) -> None:
    value = capture.recipe.model_dump(mode="json")
    members = value["members"]
    if mutation == "missing":
        members.pop()
    elif mutation == "cycle":
        members[1]["dependencies"] = ["weights"]
    elif mutation == "unreachable":
        members[0]["dependencies"] = []
    elif mutation == "notice":
        members[0]["files"] = [{"path": "model.bin"}]
    elif mutation == "collision":
        members[0]["files"].append({"path": "MODEL.bin"})
    elif mutation == "parent":
        members[0]["files"].append({"path": "model.bin/other"})
    else:
        members[0]["files"] += [{"path": "a/x"}, {"path": "A/y"}]
    with pytest.raises(ValidationError):
        Recipe.model_validate_json(json.dumps(value))
