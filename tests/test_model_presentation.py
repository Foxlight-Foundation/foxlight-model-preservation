"""Human descriptions come from preserved metadata and grant no authority."""

import json

import pytest
from test_cards_proofs import body
from test_local_owner import accepted_catalog, finish, submit

from foxlight_model_preservation.cards import create_card, identity_json
from foxlight_model_preservation.local_host.owner import LocalOwner
from foxlight_model_preservation.local_host.presentation import (
    card_summary,
    copy_summary,
)
from foxlight_model_preservation.models import PreservationError, TrustProfile
from foxlight_model_preservation.storage import directory, manifest_path, open_file


def test_everyday_find_is_local_and_filters_declared_purpose(tmp_path, monkeypatch):
    catalog, _, _ = accepted_catalog(tmp_path)
    monkeypatch.setattr(
        "socket.socket", lambda *a, **k: pytest.fail("metadata search used network")
    )
    found = catalog.search("writing email", purpose="chat")
    assert found["total"] == 1
    assert found["results"][0]["details"]["purposes"] == ["chat"]
    assert catalog.search(purpose="video")["total"] == 0
    owner = LocalOwner(tmp_path / "owner", catalog=catalog)
    try:
        for invalid in ("invented", [], None, True):
            with pytest.raises(PreservationError, match="invalid_control"):
                owner.control("search", {"purpose": invalid})
    finally:
        owner.close()


@pytest.mark.parametrize(
    "description",
    [
        "Synthetic writing model for recovery tests; these are not real weights.",
        "🌱" * 4000,
    ],
)
def test_model_details_survive_restart_without_journal_or_object_changes(
    tmp_path, monkeypatch, description
):
    catalog, repo, data = accepted_catalog(
        tmp_path,
        metadata={
            "description": description,
            "license": {
                "name": "Apache License 2.0",
                "spdx_id": "Apache-2.0",
                "display_name": "Synthetic Model",
                "notice": "Retain the included license.",
            },
            "languages": ["en"],
            "context_length": 8192,
        },
    )

    def download(repository, revision, file, destination, control, **kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    monkeypatch.setattr(
        "foxlight_model_preservation.local_host.owner.download_file", download
    )
    home = tmp_path / "owner"
    owner = LocalOwner(home, catalog=catalog)
    result = owner.control("search", {})
    plan = owner.control(
        "plan",
        {
            "kind": "receive",
            "catalog_id": result["catalog_id"],
            "card_id": repo.card.card_id,
        },
    )
    assert finish(owner, submit(owner, plan))["state"] == "succeeded"
    saved = owner.read()["copies"][0]
    journal = (home / "journal.json").read_bytes()
    store = home / "copies" / saved["copy_id"]
    objects = {p.name: p.read_bytes() for p in (store / "objects/sha256").iterdir()}
    opened = []

    def metadata_only(root, path, **kwargs):
        opened.append(path)
        from foxlight_model_preservation.signing import digest

        assert not path.endswith(digest(data)), "browsing read model weights"
        return open_file(root, path, **kwargs)

    monkeypatch.setattr(
        "foxlight_model_preservation.local_host.presentation.open_file", metadata_only
    )
    details = copy_summary(
        store,
        saved["manifest_id"],
        TrustProfile.model_validate_json(identity_json(saved["trust"])),
    )
    if len(description) < 600:
        assert details["description"] == description
    else:
        assert details.get("description") is None
        assert repo.card.card["description"] == description
    assert details["context_length"] == 8192
    assert details["license"] == "Apache-2.0"
    assert details["attribution"] == "Synthetic Model"
    assert details["languages"] == ["en"]
    assert opened
    assert (home / "journal.json").read_bytes() == journal
    assert {
        p.name: p.read_bytes() for p in (store / "objects/sha256").iterdir()
    } == objects
    assert "details" not in json.loads(journal)["copies"][0]
    owner.close()
    restarted = LocalOwner(home)
    try:
        assert restarted.read()["copies"][0]["details"] == details
        assert restarted.read()["copies"][0]["manifest_id"] == saved["manifest_id"]
    finally:
        restarted.close()


def test_generic_pmf_description_needs_no_skulk_profile(tmp_path):
    import base64
    from pathlib import Path

    vector = json.loads(
        (Path(__file__).parents[1] / "examples/conformance/pmf-v0.1.json").read_bytes()
    )
    store = tmp_path / "store"
    (store / "manifests").mkdir(parents=True)
    (store / "manifests" / (vector["manifest_id"] + ".json")).write_bytes(
        base64.b64decode(vector["envelope_base64"])
    )
    details = copy_summary(
        store,
        vector["manifest_id"],
        TrustProfile.model_validate_json(identity_json(vector["trust"])),
    )
    assert details["description"] == vector["manifest"]["annotations"]["description"]
    assert details["metadata_source"] == "portable-archive"
    assert not details["purposes"]
    with directory(store) as root:
        with open_file(root, manifest_path(vector["manifest_id"])) as stream:
            assert stream.read() == base64.b64decode(vector["envelope_base64"])


def test_descriptive_projection_is_bounded_and_does_not_rewrite_the_card():
    value = body()
    value.update(
        {
            "description": "🌱" * 4000,
            "languages": ["x" * 500] * 100,
            "future_properties": {"arbitrary": [1, {"retained": True}]},
        }
    )
    card = create_card(value)
    original = identity_json(card.model_dump(mode="json"))
    assert len(identity_json(card_summary(card))) <= 1536
    assert len(json.dumps(card_summary(card), separators=(",", ":"))) <= 1536
    assert identity_json(card.model_dump(mode="json")) == original
    assert card.card["future_properties"] == value["future_properties"]


def test_large_descriptions_paginate_inside_the_existing_control_budget(tmp_path):
    from foxlight_model_preservation.cards import ArtifactFile, bundle_identity
    from foxlight_model_preservation.signing import digest, encode_record
    from foxlight_model_preservation.storage import atomic_bytes

    catalog, repo, data = accepted_catalog(tmp_path, companion=False)
    long_repository = "publisher" * 25 + "/" + "long-model-name" * 18
    filename = "模型" * 100 + ".gguf"
    files = (
        ArtifactFile(
            path=filename, size_bytes=len(data), object_id="sha256:" + digest(data)
        ),
    )
    cards = []
    for index in range(30):
        value = {
            **repo.card.card,
            "model_id": long_repository,
            "gguf_file": filename,
            "description": "🌱" * 600,
            "base_model": "base" * 100,
            "future_variant": index,
            "artifact_bundle": {
                "bundle_id": bundle_identity(files),
                "download_size": len(data),
                "files": [item.model_dump(mode="json") for item in files],
            },
        }
        cards.append(create_card(value))
    repo.card = cards[0]
    repo.target = identity_json(
        {
            "cards": [card.model_dump(mode="json") for card in cards],
            "card_metadata": {
                card.card_id: {"provenance": "community"} for card in cards
            },
        }
    )
    repo.refresh()
    with directory(catalog.cache) as root:
        atomic_bytes(root, "catalogs/" + digest(repo.target) + ".json", repo.target)
        atomic_bytes(
            root, "proofs/" + digest(repo.target) + ".json", encode_record(repo.proof())
        )
        atomic_bytes(
            root, "accepted.json", identity_json({"catalog_id": digest(repo.target)})
        )
    offset, seen, pages = 0, [], []
    while True:
        result = catalog.search(offset=offset)
        assert len(json.dumps(result, separators=(",", ":")).encode()) <= 65536
        assert result["results"]
        pages.append(len(result["results"]))
        seen.extend(row["card_id"] for row in result["results"])
        if result["next_offset"] is None:
            break
        assert result["next_offset"] > offset
        offset = result["next_offset"]
    assert len(seen) == len(set(seen)) == len(cards)
    assert pages[0] < 24
