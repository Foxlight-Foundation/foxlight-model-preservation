"""Signed companion selection never loses a named file or invents file evidence."""

import copy

import pytest
from test_local_owner import accepted_catalog

from foxlight_model_preservation.assurance import load_retained_card
from foxlight_model_preservation.cards import create_card, identity_json
from foxlight_model_preservation.local_host.selection import (
    registry_file_map,
    registry_recipe,
)
from foxlight_model_preservation.models import PreservationError

DATA_SIZE = len(b"Synthetic model bytes, never executable.\n")


@pytest.mark.parametrize(
    "metadata,paths",
    [
        ({"vision": {"model_type": "llava"}}, ("model.bin",)),
        ({"audio": {"kind": "stt", "supports_streaming": True}}, ("model.bin",)),
        (
            {"vision": {"projector_file": "mmproj.gguf", "projector_size": DATA_SIZE}},
            ("model.bin", "mmproj.gguf"),
        ),
        (
            {
                "video": {
                    "modes": ["t2v"],
                    "companions": [
                        {
                            "kind": "duration_head",
                            "name": "duration",
                            "path": "duration.bin",
                            "size_bytes": DATA_SIZE,
                        }
                    ],
                }
            },
            ("model.bin", "duration.bin"),
        ),
        (
            {
                "runtime": {
                    "served_spec_draft_repo": "example/synthetic",
                    "served_spec_draft_revision": "a" * 40,
                    "served_spec_draft_file": "draft.gguf",
                }
            },
            ("model.bin", "draft.gguf"),
        ),
        (
            {
                "runtime": {
                    "served_spec_draft_revision": "a" * 40,
                    "served_spec_draft_file": "draft.gguf",
                }
            },
            ("model.bin", "draft.gguf"),
        ),
        (
            {
                "runtime": {
                    "mtp_sidecar_repo": "example/synthetic",
                    "mtp_sidecar_revision": "a" * 40,
                    "mtp_heads": True,
                }
            },
            ("model.bin", "mtp.safetensors", "config.json"),
        ),
    ],
)
def test_complete_named_companions_keep_the_exact_selection(tmp_path, metadata, paths):
    _catalog, repo, _data = accepted_catalog(
        tmp_path,
        companion=False,
        metadata=metadata,
        primary_paths=paths,
    )
    before = repo.card.model_dump_json()
    recipe = registry_recipe(repo.card, repo.proof())
    assert len(recipe.members) == 1
    assert set(paths).issubset(file.path for file in recipe.members[0].files)
    assert sum(file.size_bytes for file in registry_file_map(repo.card).values()) == (
        DATA_SIZE * len(paths)
    )
    assert (
        load_retained_card(identity_json(recipe.members[0].retained_card)).envelope
        == repo.card
    )
    assert repo.card.model_dump_json() == before


@pytest.mark.parametrize(
    "metadata,paths,error",
    [
        (
            {"runtime": {"served_spec_draft_repo": "example/synthetic"}},
            ("model.bin",),
            "complete_signed_companion_map_required",
        ),
        (
            {"vision": {"projector_file": "mmproj.gguf", "projector_size": DATA_SIZE}},
            ("model.bin",),
            "complete_signed_companion_map_required",
        ),
        (
            {
                "vision": {
                    "projector_file": "mmproj.gguf",
                    "projector_size": DATA_SIZE + 1,
                }
            },
            ("model.bin", "mmproj.gguf"),
            "complete_signed_companion_map_required",
        ),
        (
            {
                "vision": {
                    "processor_repo": "other/processor",
                    "processor_revision": "b" * 40,
                }
            },
            ("model.bin",),
            "companion_adapter_required",
        ),
        (
            {
                "vision": {
                    "weights_repo": "example/synthetic",
                    "weights_revision": "b" * 40,
                }
            },
            ("model.bin",),
            "companion_adapter_required",
        ),
        (
            {
                "video": {
                    "modes": ["t2v"],
                    "companions": [
                        {
                            "kind": "duration_head",
                            "name": "duration",
                            "path": "duration.bin",
                        }
                    ],
                }
            },
            ("model.bin",),
            "complete_signed_companion_map_required",
        ),
        (
            {
                "video": {
                    "modes": ["t2v"],
                    "companions": [
                        {
                            "kind": "duration_head",
                            "name": "duration",
                            "path": "duration.bin",
                            "repo": "other/model",
                            "revision": "b" * 40,
                        }
                    ],
                }
            },
            ("model.bin", "duration.bin"),
            "complete_signed_companion_map_required",
        ),
        (
            {
                "runtime": {
                    "served_spec_draft_repo": "example/synthetic",
                    "served_spec_draft_file": "draft.gguf",
                }
            },
            ("model.bin",),
            "complete_signed_companion_map_required",
        ),
        (
            {
                "runtime": {
                    "assistant_model_repo": "other/assistant",
                    "assistant_model_revision": "b" * 40,
                }
            },
            ("model.bin",),
            "companion_adapter_required",
        ),
        (
            {"runtime": {"future_required_repo": "other/model"}},
            ("model.bin",),
            "companion_adapter_required",
        ),
    ],
)
def test_missing_or_ambiguous_companions_are_not_downloadable(
    tmp_path,
    metadata,
    paths,
    error,
    monkeypatch,
):
    catalog, repo, _data = accepted_catalog(
        tmp_path,
        companion=False,
        metadata=metadata,
        primary_paths=paths,
    )
    monkeypatch.setattr(
        "socket.socket", lambda *args, **kwargs: pytest.fail("network while selecting")
    )
    with pytest.raises(PreservationError, match=error):
        registry_recipe(repo.card, repo.proof())
    assert catalog.search()["results"][0]["download_support"] == error


def test_explicit_external_video_file_uses_original_component_map(tmp_path):
    _catalog, repo, _data = accepted_catalog(
        tmp_path,
        metadata={
            "video": {
                "modes": ["t2v"],
                "companions": [
                    {
                        "kind": "embedding",
                        "name": "conditioning",
                        "path": "tokenizer.json",
                        "repo": "test/tokenizer",
                        "revision": "b" * 40,
                        "size_bytes": DATA_SIZE,
                    }
                ],
            }
        },
    )
    recipe = registry_recipe(repo.card, repo.proof())
    assert len(recipe.members) == 2
    assert recipe.members[0].dependencies == ("companion-1",)
    assert ("test/tokenizer", "b" * 40, "tokenizer.json") in registry_file_map(
        repo.card
    )


@pytest.mark.parametrize("repository", [{}, {"served_spec_draft_repo": None}])
def test_implicit_draft_repository_still_requires_the_selected_revision(
    tmp_path, repository
):
    _catalog, repo, _data = accepted_catalog(
        tmp_path,
        companion=False,
        metadata={
            "runtime": {
                **repository,
                "served_spec_draft_revision": "b" * 40,
                "served_spec_draft_file": "draft.gguf",
            }
        },
        primary_paths=("model.bin", "draft.gguf"),
    )
    with pytest.raises(PreservationError, match="companion_adapter_required"):
        registry_recipe(repo.card, repo.proof())


def test_conflicting_file_maps_cannot_choose_the_first_identity(tmp_path):
    _catalog, repo, _data = accepted_catalog(tmp_path)
    source = copy.deepcopy(repo.card.card)
    component = source["image"]["components"][0]
    component.update(
        repository=repo.card.artifact.repository, revision=repo.card.artifact.revision
    )
    component["entry_file"] = "model.bin"
    component["files"][0].update(path="model.bin", object_id="sha256:" + "0" * 64)
    with pytest.raises(PreservationError, match="conflicting companion"):
        registry_recipe(create_card(source), repo.proof())
