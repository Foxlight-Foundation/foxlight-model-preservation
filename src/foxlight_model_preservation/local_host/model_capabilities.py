"""Describe declared uses and input/output direction without granting authority.

These are captured claims, not inference qualification. Unknown identifiers stay
in the original card/catalog; they are never guessed from a model's name.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import cast

from pydantic import JsonValue

# Feature -> browsing category, typical declared inputs, typical declared outputs.
# Keep understanding separate from generation, even when both accept images/video.
_FEATURES: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "vision": ("vision", ("image",), ("text",)),
    "video-understand": ("video-understanding", ("video",), ("text",)),
    "reasoning": ("reasoning", ("text",), ("text",)),
    "coding": ("coding", ("text",), ("text",)),
    "text": ("chat", ("text",), ("text",)),
    "image-generate": ("images", ("text",), ("image",)),
    "image-edit": ("images", ("image", "text"), ("image",)),
    "speech-transcribe": ("speech", ("audio",), ("text",)),
    "speech-translate": ("speech", ("audio",), ("text",)),
    "speech-synthesize": ("speech", ("text",), ("audio",)),
    "audio-understand": ("speech", ("audio",), ("text",)),
    "sound-generate": ("music", ("text",), ("audio",)),
    "video-generate": ("video", ("text",), ("video",)),
    "embed": ("search", ("text",), ("embedding",)),
    "rerank": ("search", ("text",), ("scores",)),
}
_ALIASES = {
    "textgeneration": ("text",),
    "textgenerate": ("text",),
    "imagetexttotext": ("text", "vision"),
    "imagetotext": ("vision",),
    "vision": ("vision",),
    "visionunderstand": ("vision",),
    "visionunderstanding": ("vision",),
    "multimodalimageunderstanding": ("vision",),
    "visualquestionanswering": ("vision",),
    "videotexttotext": ("video-understand", "text"),
    "videototext": ("video-understand",),
    "videounderstanding": ("video-understand",),
    "videounderstand": ("video-understand",),
    "thinking": ("reasoning",),
    "reasoning": ("reasoning",),
    "textreason": ("reasoning",),
    "code": ("coding",),
    "codegeneration": ("coding",),
    "textcode": ("coding",),
    "texttoimage": ("image-generate",),
    "imagegen": ("image-generate",),
    "imagegenerate": ("image-generate",),
    "imagegeneration": ("image-generate",),
    "imagetoimage": ("image-edit",),
    "imageedit": ("image-edit",),
    "speechtotext": ("speech-transcribe",),
    "automaticspeechrecognition": ("speech-transcribe",),
    "audiospeechtranscribe": ("speech-transcribe",),
    "stt": ("speech-transcribe",),
    "speechtranslation": ("speech-translate",),
    "audiospeechtranslate": ("speech-translate",),
    "texttospeech": ("speech-synthesize",),
    "audiospeechsynthesize": ("speech-synthesize",),
    "tts": ("speech-synthesize",),
    "texttomusic": ("sound-generate",),
    "texttoaudio": ("sound-generate",),
    "musicgenerate": ("sound-generate",),
    "audiogenerate": ("sound-generate",),
    "texttovideo": ("video-generate",),
    "imagetovideo": ("video-generate",),
    "referencetovideo": ("video-generate",),
    "videogen": ("video-generate",),
    "videogenerate": ("video-generate",),
    "videogeneration": ("video-generate",),
    "textembedding": ("embed",),
    "textembed": ("embed",),
    "embedding": ("embed",),
    "featureextraction": ("embed",),
    "sentencesimilarity": ("embed",),
    "reranking": ("rerank",),
    "textrerank": ("rerank",),
}
_MODALITIES = ("text", "image", "video", "audio", "embedding", "scores")
_SOURCES = ("upstream_structured", "artifact_manifest", "agent_analysis")
_PURPOSE_ORDER = (
    "chat",
    "coding",
    "images",
    "vision",
    "speech",
    "music",
    "video",
    "search",
    "reasoning",
    "video-understanding",
)


def _strings(value: JsonValue) -> list[str]:
    if isinstance(value, str):
        return [value]
    return (
        [item for item in value if isinstance(item, str)]
        if isinstance(value, list)
        else []
    )


def _token(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def capability_details(
    body: dict[str, JsonValue],
    *,
    claims: JsonValue = None,
    selected_files: Iterable[str] | None = None,
) -> dict[str, JsonValue]:
    """Project known declarations and attributed claims without changing originals.

    A repository's companion inventory never establishes that a selected artifact
    includes those files. Neither a claim's confidence nor its ``complete`` label
    establishes verified file coverage, registry authority or runtime support.
    """
    tasks = _strings(body.get("tasks")) + _strings(body.get("pipeline_tag"))
    features: set[str] = set()
    inputs: set[str] = set()
    outputs: set[str] = set()
    sources: set[str] = set()
    notes: set[str] = set()
    analysis_notes: list[str] = []
    selected = set(selected_files) if selected_files is not None else None

    def add(
        names: Iterable[str],
        ins: list[str] | None = None,
        outs: list[str] | None = None,
    ) -> None:
        for name in names:
            features.add(name)
            inputs.update(ins or _FEATURES[name][1])
            outputs.update(outs or _FEATURES[name][2])

    def direction(ins: list[str], outs: list[str], names: Iterable[str] = ()) -> None:
        inputs.update(ins)
        outputs.update(outs)
        if "text" in outs:
            if "image" in ins:
                add(("vision",), ins, outs)
            if "video" in ins:
                add(("video-understand",), ins, outs)
            if "audio" in ins and not any(name.startswith("speech-") for name in names):
                add(("audio-understand",), ins, outs)
        if "image" in outs and not any(
            name in ("image-generate", "image-edit") for name in names
        ):
            add(("image-generate",), ins, outs)
        if "video" in outs:
            add(("video-generate",), ins, outs)
        if (
            "audio" in outs
            and "video" not in outs
            and "image" not in outs
            and not any(name.startswith("speech-") for name in names)
        ):
            add(("sound-generate",), ins, outs)

    declared_inputs = [
        item for item in _strings(body.get("input_modalities")) if item in _MODALITIES
    ]
    declared_outputs = [
        item for item in _strings(body.get("output_modalities")) if item in _MODALITIES
    ]
    video = body.get("video")
    limits = video.get("reference_limits") if isinstance(video, dict) else None
    reference_inputs = ["text"]
    if isinstance(limits, dict):
        for key, modality in (
            ("max_images", "image"),
            ("max_videos", "video"),
            ("max_audio_clips", "audio"),
        ):
            maximum = limits.get(key)
            if type(maximum) is int and maximum > 0:
                reference_inputs.append(modality)

    for declaration in tasks + _strings(body.get("capabilities")):
        normalized = _token(declaration)
        # "text" is also a coarse modality/compatibility flag on embedding and
        # other task-specific models. Do not turn it into chat against their tasks.
        if normalized == "text" and (
            not tasks
            or any(
                _token(task) in {"textgeneration", "imagetexttotext"} for task in tasks
            )
        ):
            add(("text",))
        else:
            task_inputs = declared_inputs or {
                "imagetovideo": ["image", "text"],
                "referencetovideo": reference_inputs,
            }.get(normalized)
            add(_ALIASES.get(normalized, ()), task_inputs, declared_outputs)
    vision = body.get("vision")
    if isinstance(vision, dict) and vision:
        add(("vision",))
        projector = vision.get("projector_file")
        if (
            isinstance(projector, str)
            and projector
            and selected is not None
            and projector not in selected
        ):
            notes.add("vision-companion-unlisted")
    direction(declared_inputs, declared_outputs, tuple(features))

    own_claims = body.get("capability_claims")
    captured = [
        *(own_claims if isinstance(own_claims, list) else []),
        *(claims if isinstance(claims, list) else []),
    ]
    for claim in captured:
        if not isinstance(claim, dict):
            continue
        status, source, scope = (
            claim.get("status"),
            claim.get("source"),
            claim.get("scope"),
        )
        identifier = claim.get("capability_id")
        if not isinstance(identifier, str) or scope not in ("model", "artifact"):
            continue
        if not isinstance(source, str) or source not in _SOURCES:
            continue
        if source == "agent_analysis":
            # These may describe an internal component, or simply be mistaken.
            # Keep them attributable, but do not promote guesses to primary
            # capabilities, input/output types or download completeness.
            if status in ("claimed", "observed", "complete"):
                sources.add(source)
                details = claim.get("details")
                description = (
                    details.get("description") if isinstance(details, dict) else None
                )
                if (
                    isinstance(description, str)
                    and description.strip()
                    and len(analysis_notes) < 2
                ):
                    analysis_notes.append(description.strip()[:240])
            continue
        names = _ALIASES.get(_token(identifier), ())
        if status == "incomplete" and scope == "artifact":
            notes.add("artifact-incomplete")
        if status not in ("claimed", "observed", "complete"):
            continue
        sources.add(source)
        ins = [
            item
            for item in _strings(claim.get("input_modalities"))
            if item in _MODALITIES
        ]
        outs = [
            item
            for item in _strings(claim.get("output_modalities"))
            if item in _MODALITIES
        ]
        details = claim.get("details")
        video_modes = (
            _strings(details.get("video_modes")) if isinstance(details, dict) else []
        )
        if (
            _token(identifier) == "audiogenerate"
            and video_modes
            and all(mode in ("t2va", "fl2va", "ref2va") for mode in video_modes)
        ):
            # These explicitly joint modes describe a video's soundtrack,
            # not a separate music/sound generation task.
            names = ("video-generate",)
            outs = list(dict.fromkeys([*outs, "video", "audio"]))
        add(names, ins, outs)
        # Explicit direction can describe a new namespaced capability. Input
        # image/video alone is not evidence of understanding or generation.
        if scope == "model" and source == "upstream_structured":
            direction(ins, outs, names)
        companions = (
            _strings(details.get("companion_files"))
            if isinstance(details, dict)
            else []
        )
        if (
            (
                "vision" in names
                or (
                    scope == "model"
                    and source == "upstream_structured"
                    and "image" in ins
                    and "text" in outs
                )
            )
            and companions
            and selected is not None
            and any(path not in selected for path in companions)
        ):
            notes.add("vision-companion-unlisted")

    if "vision" in features and selected == set():
        notes.add("vision-files-unconfirmed")
    categories = {_FEATURES[name][0] for name in features}
    return {
        "purposes": cast(
            list[JsonValue], [item for item in _PURPOSE_ORDER if item in categories]
        ),
        "feature_ids": cast(
            list[JsonValue], [item for item in _FEATURES if item in features]
        ),
        "input_modalities": cast(
            list[JsonValue], [item for item in _MODALITIES if item in inputs]
        ),
        "output_modalities": cast(
            list[JsonValue], [item for item in _MODALITIES if item in outputs]
        ),
        "claim_sources": cast(
            list[JsonValue], [item for item in _SOURCES if item in sources]
        ),
        "artifact_notes": cast(list[JsonValue], sorted(notes)),
        "analysis_notes": cast(list[JsonValue], analysis_notes),
    }
