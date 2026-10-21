"""Preserve future metadata without relaxing interpreted artifact contracts."""

import base64
import copy
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_cards_proofs import NOW, body, selected_member

from foxlight_model_preservation.assurance import (
    inspect_member_evidence,
    load_retained_card,
    retain_card,
)
from foxlight_model_preservation.cards import (
    card_body_schema,
    create_card,
    identity_json,
    load_card,
)
from foxlight_model_preservation.models import Manifest, Recipe, TrustProfile
from foxlight_model_preservation.registry_proof import verify_registry_proof
from foxlight_model_preservation.signing import digest, public_record
from foxlight_model_preservation.storage import pack, restore


def future_body():
    """Include additions at multiple depths and a future string vocabulary."""
    return {
        **body(),
        "future_capability": {"revision": 1, "strength": 1.0, "labels": ["α", None]},
        "tasks": ["TextGeneration", "FutureGeneration"],
        "runtime": {"future_scheduler": {"enabled": True}},
        "video": {"future_control": True, "modes": ["future_mode"]},
        "audio": {
            "kind": "future_audio",
            "voice_catalog": [
                {"id": "one", "name": "One", "future_voice_property": [1.0, 2]}
            ],
        },
    }


def test_future_metadata_is_retained_and_identity_bound():
    selected = future_body()
    card = create_card(selected)
    encoded = identity_json(card.model_dump(mode="json"))
    assert load_card(encoded).card == selected
    retained = retain_card(card)
    assert base64.b64decode(retained.card_base64) == encoded
    assert (
        load_retained_card(identity_json(retained.model_dump(mode="json"))).envelope
        == card
    )
    assert type(retained.envelope.card["future_capability"]["strength"]) is float
    altered = json.loads(encoded)
    altered["card"]["future_capability"]["revision"] = 2
    with pytest.raises(ValueError, match="identity"):
        load_card(identity_json(altered))


@pytest.mark.parametrize(
    "addition",
    [
        {"hidden_size": "16"},
        {"storage_size": {"in_bytes": "20"}},
        {"video": {"fps": "25"}},
        {"audio": {"kind": "future_audio", "sample_rates": ["48000"]}},
    ],
)
def test_known_structure_remains_validated(addition):
    with pytest.raises(ValueError, match="structural schema"):
        create_card({**future_body(), **addition})


@pytest.mark.parametrize(
    "name",
    ["is_custom", "qualification_only", "registry_card_id", "registry_future_trust"],
)
def test_receiver_projections_are_still_refused(name):
    with pytest.raises(ValueError, match="structural schema"):
        create_card({**future_body(), name: True})


@pytest.mark.parametrize("location", ["envelope", "artifact", "bundle", "file"])
def test_interpreted_contracts_stay_closed(location):
    member, _repository = selected_member()
    card = load_retained_card(identity_json(member.retained_card)).envelope
    raw = card.model_dump(mode="json")
    target = (
        raw
        if location == "envelope"
        else raw["artifact"]
        if location == "artifact"
        else raw["card"]["artifact_bundle"]
        if location == "bundle"
        else raw["card"]["artifact_bundle"]["files"][0]
    )
    target["future_required_interpretation"] = True
    with pytest.raises(ValueError):
        load_card(identity_json(raw))
    raw = card.model_dump(mode="json")
    raw["schema_version"] = 999
    with pytest.raises(ValueError):
        load_card(identity_json(raw))


def test_future_fields_survive_registry_proof_and_signed_capture_restore(tmp_path):
    content = b"x" * 20
    member, repository = selected_member(object_id="sha256:" + digest(content))
    selected = {**repository.card.card, **future_body()}
    card = create_card(selected)
    repository.card = card
    repository.path = f"v1/cards/{card.card_id}.json"
    repository.target = identity_json(card.model_dump(mode="json"))
    repository.refresh()
    proof = repository.proof()
    verified = verify_registry_proof(
        identity_json(proof.model_dump(mode="json")),
        repository.root.to_bytes(),
        card.card_id,
        reference_time=NOW,
    )
    assert verified.card.card == selected
    retained = retain_card(card, registry_proof=proof)
    raw_member = member.model_dump(mode="json")
    raw_member["artifact_identity"] = card.card_id
    raw_member["retained_card"] = retained.model_dump(mode="json")
    raw_member["files"] = [{"path": "model.bin"}]
    recipe = Recipe.model_validate_json(
        identity_json(
            {
                "schema_version": "foxlight.model-preservation.v1",
                "name": "Future metadata fixture",
                "root_member": "primary",
                "workloads": ["synthetic"],
                "runtime_requirements": [],
                "members": [raw_member],
            }
        )
    )
    source = tmp_path / "source"
    source.mkdir()
    (source / "model.bin").write_bytes(content)
    key = Ed25519PrivateKey.generate()
    trust = TrustProfile(
        schema_version="foxlight.model-preservation.trust.v1",
        keys=(public_record(key),),
        threshold=1,
        as_of=NOW.isoformat(),
    )
    store = tmp_path / "store"
    manifest_id = pack(recipe, {"primary": source}, store, (key,))
    destination = tmp_path / "restored"
    restored = restore(store, manifest_id, trust, destination)
    evidence = inspect_member_evidence(
        restored.members[0], trusted_root=repository.root.to_bytes(), reference_time=NOW
    )
    assert evidence.publication == "registry-fresh"
    assert evidence.file_coverage == "complete-sha256"
    recovered = load_retained_card(identity_json(restored.members[0].retained_card))
    assert recovered.card_base64 == retained.card_base64
    assert recovered.envelope.card == selected
    assert (destination / "members/primary/model.bin").read_bytes() == content
    # Future metadata never grants permission to change the signed file graph.
    changed_manifest = copy.deepcopy(restored.model_dump(mode="json"))
    changed_manifest["future_required_member"] = "other"
    with pytest.raises(ValueError):
        Manifest.model_validate_json(identity_json(changed_manifest))


def test_published_reader_schema_matches_runtime_and_keeps_writer_profile():
    from importlib.resources import files

    writer = json.loads(
        files("foxlight_model_preservation")
        .joinpath("resources", "skulk_model_card_v1.json")
        .read_bytes()
    )
    reader = card_body_schema()
    assert reader == json.loads(
        Path("docs/schemas/PreservedModelCardBodyV1.json").read_bytes()
    )
    assert writer["additionalProperties"] is False
    assert reader["additionalProperties"] is True
    for name in ("ArtifactBundleConfig", "ArtifactBundleFile"):
        assert reader["$defs"][name] == writer["$defs"][name]
