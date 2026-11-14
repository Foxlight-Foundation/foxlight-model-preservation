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


@pytest.fixture(autouse=True)
def private_mirror_directory(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Keep every receive's private mirror directory out of the real home."""
    path = tmp_path_factory.mktemp("mirror-directory")
    monkeypatch.setenv("SOVEREIGN_MIRROR_DIRECTORY", str(path))
    monkeypatch.delenv("SOVEREIGN_SEED_MIRRORS", raising=False)
    return path


@pytest.fixture(autouse=True)
def instant_bucket_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bucket retries back off in real use; tests need not wait for them."""
    from foxlight_model_preservation.bucket import BucketClient

    monkeypatch.setattr(BucketClient, "retry_delay", 0.0)


@pytest.fixture(autouse=True)
def no_packaged_seed_mirrors(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may contact the real starter mirror the package ships.

    The environment switch reaches child processes too (CLI tests).
    """
    monkeypatch.setenv("SOVEREIGN_PACKAGED_SEED_MIRRORS", "off")


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


@pytest.fixture(autouse=True)
def no_origin_retry_pauses(monkeypatch):
    """Retry pauses between Hugging Face attempts are real seconds; tests skip them."""
    from foxlight_model_preservation.local_host import owner

    monkeypatch.setattr(owner, "_pause", lambda *args: None)
