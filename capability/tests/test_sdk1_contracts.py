"""Keep the actual old-SDK descriptor, authority and settings contracts intact."""

import json
from pathlib import Path

import pytest

pytest.importorskip("skulk_capability_sdk", reason="optional managed adapter")
from foxlight_preservation_library.contracts import Settings, build_manifest


def test_complete_contract_matches_original_sdk050() -> None:
    """Compare complete measured original SDK0.5.0/Pydantic2.14.1 declarations."""
    baseline = json.loads(
        (Path(__file__).parent / "fixtures/sdk050-managed-contracts.json").read_text()
    )
    original = baseline["manifest"]
    manifest = build_manifest(
        executable=original["executable"],
        executable_sha256=original["executable_sha256"],
    )
    current = json.loads(manifest.model_dump_json())
    assert current.pop("runtime_dependencies") == ["httpx", "pydantic", "pyyaml"]
    assert current == original
    assert json.loads(Settings().model_dump_json()) == baseline["settings"]
