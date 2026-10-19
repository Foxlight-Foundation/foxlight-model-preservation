"""Isolated test-only captures and fresh ephemeral signing identities."""

from dataclasses import dataclass
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from foxlight_model_preservation.models import Recipe, TrustProfile
from foxlight_model_preservation.signing import public_record
from foxlight_model_preservation.storage import pack


@dataclass
class Capture:
    recipe: Recipe
    sources: dict[str, Path]
    key: Ed25519PrivateKey
    trust: TrustProfile
    store: Path
    identity: str


@pytest.fixture
def capture(tmp_path: Path) -> Capture:
    sources = {}
    members = []
    for name, role, dependencies in (
        ("weights", "primary", ["tokenizer"]),
        ("tokenizer", "tokenizer", []),
    ):
        source = tmp_path / name
        source.mkdir()
        (source / "LICENSE").write_text("Test fixture; no upstream model weights.\n")
        (source / "model.bin").write_bytes((name.encode() + b"\x00") * 1000)
        (source / "secret-not-selected").write_text("must never be exported")
        sources[name] = source
        members.append(
            {
                "member_id": name,
                "role": role,
                "format": "test-fixture",
                "origin_repository": "test/" + name,
                "origin_revision": "a" * 40,
                "artifact_identity": "test:" + name,
                "dependencies": dependencies,
                "retained_card": {"verification_class": "legacy"},
                "intake": {
                    "audience": "public",
                    "license_id": "test-only",
                    "terms_reference": "LICENSE",
                    "decision": "synthetic fixture",
                    "notice_paths": ["LICENSE"],
                },
                "files": [{"path": "model.bin"}, {"path": "LICENSE"}],
            }
        )
    import json

    recipe = Recipe.model_validate_json(
        json.dumps(
            {
                "schema_version": "foxlight.model-preservation.v1",
                "name": "fixture",
                "root_member": "weights",
                "workloads": ["test-only"],
                "runtime_requirements": ["not an inference fixture"],
                "members": members,
            }
        )
    )
    key = Ed25519PrivateKey.generate()
    trust = TrustProfile(
        schema_version="foxlight.model-preservation.trust.v1",
        keys=(public_record(key),),
        threshold=1,
        as_of="2026-10-19T00:00:00Z",
    )
    store = tmp_path / "store"
    identity = pack(recipe, sources, store, (key,))
    return Capture(recipe, sources, key, trust, store, identity)
