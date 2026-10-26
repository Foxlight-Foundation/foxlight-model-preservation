"""Capability descriptions retain direction, evidence boundaries and originals."""

import json

import pytest

from foxlight_model_preservation.local_host.model_capabilities import capability_details
from foxlight_model_preservation.local_host.presentation import model_summary


def claim(
    identifier,
    *,
    status="observed",
    scope="model",
    source="upstream_structured",
    inputs=(),
    outputs=(),
    details=None,
):
    return {
        "capability_id": identifier,
        "status": status,
        "scope": scope,
        "source": source,
        "confidence": 1.0,
        "input_modalities": list(inputs),
        "output_modalities": list(outputs),
        "details": details or {},
    }


@pytest.mark.parametrize(
    "task", ["ImageTextToText", "image-text-to-text", "image_text_to_text"]
)
def test_portable_multimodal_task_exposes_text_and_image_understanding(task):
    details = capability_details({"tasks": [task]})
    assert details["purposes"] == ["chat", "vision"]
    assert set(details["input_modalities"]) == {"text", "image"}
    assert details["output_modalities"] == ["text"]
    assert "image-generate" not in details["feature_ids"]


def test_qwen_style_catalog_claims_reveal_vision_without_changing_the_card():
    body = {
        "tasks": ["TextGeneration"],
        "capabilities": ["text", "thinking"],
        "future_extension": {"preserve": True},
    }
    captured = [
        claim("text.generate"),
        claim("text.reason"),
        claim(
            "vision.understand",
            scope="artifact",
            status="complete",
            source="artifact_manifest",
            details={"companion_files": ["mmproj.gguf"]},
        ),
    ]
    before = json.dumps([body, captured])
    details = model_summary(body, claims=captured, selected_files=("model.gguf",))
    assert set(details["purposes"]) == {"chat", "vision", "reasoning"}
    assert set(details["input_modalities"]) == {"text", "image"}
    assert details["output_modalities"] == ["text"]
    assert details["artifact_notes"] == ["vision-companion-unlisted"]
    assert details["claim_sources"] == ["upstream_structured", "artifact_manifest"]
    assert len(json.dumps(details, separators=(",", ":"))) <= 1536
    assert json.dumps([body, captured]) == before
    assert "card_id" not in details and "trust" not in details


@pytest.mark.parametrize(
    ("inputs", "outputs", "expected", "absent"),
    [
        (("image", "text"), ("text",), "vision", "image-generate"),
        (("image", "text"), ("video",), "video-generate", "vision"),
        (("video", "text"), ("text",), "video-understand", "video-generate"),
        (("text",), ("image",), "image-generate", "vision"),
        (("audio",), ("text",), "audio-understand", "speech-transcribe"),
    ],
)
def test_new_namespaced_claim_uses_explicit_direction_without_guessing(
    inputs, outputs, expected, absent
):
    details = capability_details(
        {}, claims=[claim("future.example", inputs=inputs, outputs=outputs)]
    )
    assert expected in details["feature_ids"]
    assert absent not in details["feature_ids"]


def test_image_input_does_not_turn_a_video_generator_into_an_image_reader():
    details = capability_details({"tasks": ["ImageToVideo"]})
    assert details["purposes"] == ["video"]
    assert details["input_modalities"] == ["text", "image"]
    assert details["output_modalities"] == ["video"]


def test_joint_video_audio_output_does_not_claim_a_standalone_music_generator():
    details = capability_details(
        {},
        claims=[
            claim(
                "video.generate", inputs=("text", "image"), outputs=("video", "audio")
            )
        ],
    )
    assert details["purposes"] == ["video"]
    assert details["output_modalities"] == ["video", "audio"]
    assert "sound-generate" not in details["feature_ids"]


def test_reference_image_input_does_not_by_itself_promise_an_editing_mode():
    details = capability_details(
        {},
        claims=[claim("image.generate", inputs=("text", "image"), outputs=("image",))],
    )
    assert details["feature_ids"] == ["image-generate"]
    assert details["input_modalities"] == ["text", "image"]


def test_editing_claim_does_not_promise_text_only_image_generation():
    details = capability_details(
        {}, claims=[claim("image.edit", inputs=("image", "text"), outputs=("image",))]
    )
    assert details["feature_ids"] == ["image-edit"]


def test_text_modality_does_not_turn_an_embedding_model_into_chat():
    details = capability_details({"tasks": ["TextEmbedding"], "capabilities": ["text"]})
    assert details["purposes"] == ["search"]
    assert details["output_modalities"] == ["embedding"]


@pytest.mark.parametrize("status", ["unknown", "incomplete", "future-status"])
def test_nonpositive_claim_cannot_add_a_capability(status):
    details = capability_details(
        {}, claims=[claim("vision.understand", status=status, scope="artifact")]
    )
    assert details["purposes"] == []
    assert details["feature_ids"] == []
    assert details["artifact_notes"] == (
        ["artifact-incomplete"] if status == "incomplete" else []
    )


def test_incomplete_variant_keeps_model_capability_visible_with_its_limitation():
    details = capability_details(
        {},
        claims=[
            claim("vision.understand"),
            claim("vision.understand", scope="artifact", status="incomplete"),
        ],
    )
    assert details["purposes"] == ["vision"]
    assert details["artifact_notes"] == ["artifact-incomplete"]


def test_automated_claim_keeps_attribution_and_unknown_scope_stays_opaque():
    captured = [
        claim(
            "multimodal.image.understanding",
            source="agent_analysis",
            status="claimed",
            inputs=("image",),
            outputs=("text",),
            details={
                "description": "An automated interpretation, not a capability test."
            },
        ),
        claim("video.understand", scope="future-scope"),
    ]
    before = json.dumps(captured)
    details = capability_details({}, claims=captured)
    assert details["purposes"] == []
    assert details["input_modalities"] == []
    assert details["claim_sources"] == ["agent_analysis"]
    assert details["analysis_notes"] == [
        "An automated interpretation, not a capability test."
    ]
    assert json.dumps(captured) == before


def test_image_generator_is_not_reclassified_by_automated_component_notes():
    details = capability_details(
        {"tasks": ["TextToImage"]},
        claims=[
            claim(
                "text.encoder.component",
                source="agent_analysis",
                inputs=("image", "text"),
                outputs=("text",),
            )
        ],
    )
    assert details["purposes"] == ["images"]
    assert details["input_modalities"] == ["text"]
    assert details["output_modalities"] == ["image"]


def test_portable_pipeline_and_modality_declarations_need_no_skulk_profile():
    pipeline = model_summary({"pipeline_tag": "image-text-to-text"})
    assert pipeline["purposes"] == ["chat", "vision"]
    direction = model_summary(
        {"input_modalities": ["image"], "output_modalities": ["text"]}
    )
    assert direction["purposes"] == ["vision"]


def test_model_names_never_supply_capability_evidence():
    details = model_summary(
        {"model_id": "example/Vision-Reasoning-Image-Video-Model"},
        repository="example/Vision-Model",
    )
    assert details["purposes"] == []
    assert details["input_modalities"] == []


def test_selected_projector_presence_is_a_file_map_fact_not_a_trust_or_runtime_grant():
    claims = [
        claim(
            "vision.understand",
            status="complete",
            scope="artifact",
            source="artifact_manifest",
            details={"companion_files": ["mmproj.gguf"]},
        )
    ]
    details = capability_details(
        {}, claims=claims, selected_files=("model.gguf", "mmproj.gguf")
    )
    assert details["artifact_notes"] == []
    assert not any(
        key in details for key in ("verified", "qualified", "trust", "ready")
    )
    unknown = capability_details({}, claims=claims, selected_files=())
    assert set(unknown["artifact_notes"]) == {
        "vision-companion-unlisted",
        "vision-files-unconfirmed",
    }


@pytest.mark.parametrize(
    ("limits", "expected"),
    [
        ({"max_images": 2, "max_videos": 0, "max_audio_clips": 0}, ["text", "image"]),
        ({"max_videos": 1, "max_audio_clips": 1}, ["text", "video", "audio"]),
        ({"max_images": False, "max_videos": -1}, ["text"]),
        (None, ["text"]),
    ],
)
def test_reference_video_lists_only_explicitly_supported_reference_types(
    limits, expected
):
    details = capability_details(
        {"tasks": ["ReferenceToVideo"], "video": {"reference_limits": limits}}
    )
    assert details["input_modalities"] == expected
    assert details["purposes"] == ["video"]


@pytest.mark.parametrize(
    ("task", "inputs", "outputs", "feature", "absent"),
    [
        ("ImageToImage", ["image", "text"], ["image"], "image-edit", "image-generate"),
        ("TextToSpeech", ["text"], ["audio"], "speech-synthesize", "sound-generate"),
    ],
)
def test_explicit_card_direction_keeps_known_task_distinctions(
    task, inputs, outputs, feature, absent
):
    details = capability_details(
        {"tasks": [task], "input_modalities": inputs, "output_modalities": outputs}
    )
    assert feature in details["feature_ids"]
    assert absent not in details["feature_ids"]


def test_reference_video_respects_explicit_portable_modalities_without_skulk_limits():
    details = capability_details(
        {
            "tasks": ["ReferenceToVideo"],
            "input_modalities": ["image"],
            "output_modalities": ["video"],
        }
    )
    assert details["input_modalities"] == ["image"]
    assert details["output_modalities"] == ["video"]


def test_direction_derived_vision_keeps_unlisted_companion_warning():
    details = capability_details(
        {},
        claims=[
            claim(
                "future.example",
                inputs=("image",),
                outputs=("text",),
                details={"companion_files": ["mmproj.gguf"]},
            )
        ],
        selected_files=("model.gguf",),
    )
    assert details["feature_ids"] == ["vision"]
    assert details["artifact_notes"] == ["vision-companion-unlisted"]


@pytest.mark.parametrize("modes", [["t2va"], ["fl2va"], ["t2va", "fl2va", "ref2va"]])
def test_video_soundtrack_claim_is_not_a_standalone_music_task(modes):
    details = capability_details(
        {"tasks": ["TextToVideo"]},
        claims=[
            claim("video.generate", outputs=("video",)),
            claim("audio.generate", outputs=("audio",), details={"video_modes": modes}),
        ],
    )
    assert details["purposes"] == ["video"]
    assert details["output_modalities"] == ["video", "audio"]
    assert "sound-generate" not in details["feature_ids"]


def test_explicit_separate_music_task_is_kept_alongside_joint_video_modes():
    details = capability_details(
        {"tasks": ["TextToVideo", "TextToMusic"]},
        claims=[
            claim(
                "audio.generate", outputs=("audio",), details={"video_modes": ["t2va"]}
            )
        ],
    )
    assert details["purposes"] == ["music", "video"]
    assert "sound-generate" in details["feature_ids"]


@pytest.mark.parametrize(
    "selected,expected",
    [
        (("model.gguf",), ["vision-companion-unlisted"]),
        (("model.gguf", "mmproj.gguf"), []),
    ],
)
def test_direct_card_projector_declaration_is_compared_to_selected_files(
    selected, expected
):
    details = capability_details(
        {"vision": {"projector_file": "mmproj.gguf"}}, selected_files=selected
    )
    assert details["artifact_notes"] == expected
