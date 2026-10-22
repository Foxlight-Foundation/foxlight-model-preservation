"""The retained public vector can be verified by independent implementations."""

import base64
import json
from pathlib import Path

import pytest

from foxlight_model_preservation.signing import (
    canonical_bytes,
    load_trust,
    verify_manifest,
)
from foxlight_model_preservation.storage import (
    manifest_path,
    object_path,
    restore,
    verify_store,
)


def test_public_vector(tmp_path: Path) -> None:
    vector = json.loads(Path("examples/conformance/v1.json").read_bytes())
    trust = load_trust(canonical_bytes(vector["trust"]))
    identity = vector["manifest"]
    envelope = canonical_bytes(vector["envelope"])
    manifest = verify_manifest(envelope, trust, identity)
    root = tmp_path / "store"
    for file_hash, data in vector["objects_base64"].items():
        output = root / object_path(file_hash)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(base64.b64decode(data))
    selected = root / manifest_path(identity)
    selected.parent.mkdir()
    selected.write_bytes(envelope)
    assert verify_store(root, identity, trust) == manifest
    restore(root, identity, trust, tmp_path / "restored")
    selected.write_bytes(envelope[:-1])
    with pytest.raises(ValueError):
        verify_store(root, identity, trust)


def test_public_invitation_vector_preserves_original_signed_identity() -> None:
    from foxlight_model_preservation.invitations import (
        authenticate_invitation,
        read_invitation,
    )

    original = json.loads(Path("examples/conformance/v1.json").read_bytes())
    invitation = read_invitation(
        Path("examples/conformance/invitation-v1.json").read_bytes()
    )
    # Use the original independent vector trust, not the invitation's candidates.
    trust = load_trust(canonical_bytes(original["trust"]))
    authenticated = authenticate_invitation(invitation, trust)
    assert authenticated == verify_manifest(
        canonical_bytes(original["envelope"]), trust, original["manifest"]
    )
    assert invitation.envelope.payload == original["envelope"]["payload"]
    assert invitation.manifest_id == original["manifest"]
