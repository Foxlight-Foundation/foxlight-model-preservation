"""Bounded descriptive views of existing cards; never a new trust decision.

The signed PMF and its opaque documents remain unchanged. These optional views
help people browse their copies without changing the versioned owner journal.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from ..assurance import load_retained_card
from ..cards import ModelCardEnvelope
from ..models import TrustProfile
from ..pmf import verify_pmf_manifest
from ..pmf_models import PMFManifest
from ..signing import digest
from ..storage import directory, manifest_path, object_path, open_file, read_metadata

PURPOSE_WORDS = {
    "chat": "chat writing write email summarize summarise questions conversation text",
    "coding": "coding code programming software",
    "images": "images image pictures picture drawing draw art editing edit",
    "vision": "vision understand describe photos photograph visual",
    "speech": "speech voice audio transcription transcribe translation speak listening",
    "music": "music sound song songs",
    "video": "video videos animation animate movie",
    "search": "search embedding embeddings similarity retrieval documents",
}
_PURPOSES = {
    "chat": {"textgeneration", "text"},
    "coding": {"code", "codegeneration"},
    "images": {"texttoimage", "imagetoimage", "imagegen", "imageedit"},
    "vision": {"vision", "imagetotext", "visualquestionanswering"},
    "speech": {"speechtotext", "speechtranslation", "texttospeech", "stt", "tts"},
    "music": {"texttomusic", "texttoaudio"},
    "video": {"texttovideo", "imagetovideo", "referencetovideo", "videogen"},
    "search": {"textembedding", "embedding", "featureextraction", "reranking"},
}


def _text(value: JsonValue, limit: int = 600) -> str | None:
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


def _strings(value: JsonValue) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item[:100] for item in value if isinstance(item, str)))[
        :8
    ]


def _bounded(summary: dict[str, JsonValue]) -> dict[str, JsonValue]:
    # Search returns up to 24 cards inside the existing 64 KiB control limit.
    # Excerpts may be omitted; the complete original metadata remains archived.
    result: dict[str, JsonValue] = {}
    for key in (
        "metadata_status",
        "metadata_source",
        "attribution",
        "license_notice",
        "purposes",
        "workloads",
        "format",
        "quantization",
        "description",
        "publisher",
        "repository",
        "base_model",
        "context_length",
        "languages",
        "license",
        "capabilities",
        "runtime_requirements",
    ):
        value = summary.get(key)
        if len(json.dumps({**result, key: value}, separators=(",", ":"))) <= 1536:
            result[key] = value
    return result


def purpose_ids(tasks: list[str], capabilities: list[str]) -> list[str]:
    """Translate declared tasks to browsing categories, without rating usefulness."""
    tokens = {
        re.sub(r"[^a-z0-9]", "", item.casefold()) for item in tasks + capabilities
    }
    return [purpose for purpose, names in _PURPOSES.items() if tokens & names]


def card_summary(card: ModelCardEnvelope) -> dict[str, JsonValue]:
    """Project publisher-provided metadata without fetching or rewriting its card."""
    body = card.card
    tasks, capabilities = (
        _strings(body.get("tasks")),
        _strings(body.get("capabilities")),
    )
    context = body.get("context_length")
    license_info = body.get("license")
    license_fields = license_info if isinstance(license_info, dict) else {}
    return _bounded(
        {
            "description": _text(body.get("description")) or _text(body.get("summary")),
            "publisher": card.artifact.repository.split("/", 1)[0],
            "repository": card.artifact.repository,
            "base_model": _text(body.get("base_model"), 512),
            "format": card.artifact.format,
            "quantization": card.artifact.quantization,
            "workloads": cast(list[JsonValue], tasks),
            "capabilities": cast(list[JsonValue], capabilities),
            "purposes": cast(list[JsonValue], purpose_ids(tasks, capabilities)),
            "context_length": context
            if type(context) is int and 0 < context < 2**53
            else None,
            "languages": cast(
                list[JsonValue], _strings(body.get("languages") or body.get("language"))
            ),
            "license": _text(license_fields.get("spdx_id"))
            or _text(license_fields.get("name"))
            or _text(license_info)
            or _text(body.get("license_id")),
            "attribution": _text(license_fields.get("display_name")),
            "license_notice": _text(license_fields.get("notice")),
            "runtime_requirements": [],
            "metadata_source": "model-card",
            "metadata_status": "available",
        }
    )


def search_words(summary: dict[str, JsonValue]) -> str:
    """Keep everyday search local and tied to declared capabilities."""
    purposes = summary.get("purposes")
    return (
        " ".join(PURPOSE_WORDS.get(str(item), "") for item in purposes)
        if isinstance(purposes, list)
        else ""
    )


def manifest_summary(manifest: PMFManifest) -> dict[str, JsonValue]:
    """Describe common PMF metadata even when no ecosystem profile is understood."""
    member = next(
        item for item in manifest.members if item.member_id == manifest.root_member
    )
    tasks = list(manifest.workloads)
    repository = member.origin.repository if member.origin else None
    license_id = member.intake.license_id
    summary: dict[str, JsonValue] = {
        "description": _text(manifest.annotations.get("description"))
        or _text(manifest.annotations.get("summary")),
        "publisher": repository.split("/", 1)[0] if repository else None,
        "repository": repository,
        "base_model": None,
        "format": member.format,
        "quantization": None,
        "workloads": cast(list[JsonValue], tasks[:32]),
        "capabilities": [],
        "purposes": cast(list[JsonValue], purpose_ids(tasks, [])),
        "context_length": None,
        "languages": [],
        "license": None if license_id == "see-original-card" else license_id,
        "runtime_requirements": cast(
            list[JsonValue], list(manifest.runtime_requirements[:16])
        ),
        "metadata_source": "portable-archive",
        "metadata_status": "available",
    }
    return _bounded(summary)


def copy_summary(
    store: Path, manifest_id: str, trust: TrustProfile
) -> dict[str, JsonValue]:
    """Read authenticated metadata only, never weights, network or executable code.

    Unknown profiles remain opaque. An unavailable or oversized optional card
    leaves common PMF metadata usable; it does not prevent recovery or grant trust.
    """
    with directory(store) as root:
        manifest = verify_pmf_manifest(
            read_metadata(root, manifest_path(manifest_id)), trust, manifest_id
        )
        summary = manifest_summary(manifest)
        for profile in manifest.profiles:
            if (
                profile.profile_id != "skulk.retained-card.v1"
                or profile.member_id != manifest.root_member
            ):
                continue
            summary["metadata_status"] = "partial"
            try:
                document = next(
                    item
                    for item in manifest.documents
                    if item.document_id == profile.documents[0]
                )
                if document.size > 512 * 1024:
                    break
                with open_file(root, object_path(document.sha256)) as stream:
                    data = stream.read(512 * 1024 + 1)
                if len(data) != document.size or digest(data) != document.sha256:
                    break
                projected = card_summary(load_retained_card(data).envelope)
                projected["runtime_requirements"] = summary.get(
                    "runtime_requirements", []
                )
                projected["description"] = projected.get("description") or summary.get(
                    "description"
                )
                projected["license"] = projected.get("license") or summary.get(
                    "license"
                )
                return _bounded(projected)
            except (ValueError, OSError):
                break
        return _bounded(summary)
