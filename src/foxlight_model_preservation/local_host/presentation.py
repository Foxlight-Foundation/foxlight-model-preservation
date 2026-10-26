"""Bounded descriptive views of existing cards; never a new trust decision.

The signed PMF and its opaque documents remain unchanged. These optional views
help people browse their copies without changing the versioned owner journal.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from ..assurance import load_retained_card
from ..cards import ModelCardEnvelope, identity_json, load_card
from ..models import MAX_METADATA_BYTES, PreservationError, TrustProfile
from ..pmf import verify_pmf_manifest
from ..pmf_models import PMFManifest
from ..registry_proof import RegistryProof
from ..signing import decode_base64, digest, strict_json
from ..storage import directory, manifest_path, object_path, open_file
from .model_capabilities import capability_details


def metadata_page(
    rows: Iterable[dict[str, JsonValue]],
    *,
    max_rows: int = 24,
    byte_limit: int = 58_000,
) -> list[dict[str, JsonValue]]:
    """Bound actual JSON wire bytes, leaving space for page metadata and cursors."""
    page: list[dict[str, JsonValue]] = []
    used = 0
    for row in rows:
        if len(page) >= max_rows:
            break
        size = len(json.dumps(row, separators=(",", ":"))) + 1
        if used + size > byte_limit:
            if not page:
                raise PreservationError("metadata_preview_too_large")
            break
        page.append(row)
        used += size
    return page


PURPOSE_WORDS = {
    "chat": "chat writing write email summarize summarise questions conversation text",
    "coding": "coding code programming software",
    "images": "images image pictures picture drawing draw art editing edit",
    "vision": (
        "vision understand describe photos photograph visual "
        "pictures image screenshot text questions"
    ),
    "speech": "speech voice audio transcription transcribe translation speak listening",
    "music": "music sound song songs",
    "video": "video videos animation animate movie",
    "search": "search embedding embeddings similarity retrieval documents",
    "reasoning": (
        "reasoning thinking solve problems problem solving logic mathematics math"
    ),
    "video-understanding": "understand video videos clips questions text visual",
}


def _text(value: JsonValue, limit: int = 600) -> str | None:
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


def _strings(value: JsonValue, *, max_items: int | None = 8) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item[:100] for item in value if isinstance(item, str)))[
        :max_items
    ]


def _bounded(summary: dict[str, JsonValue]) -> dict[str, JsonValue]:
    # Search returns up to 24 cards inside the existing 64 KiB control limit.
    # Excerpts may be omitted; the complete original metadata remains archived.
    result: dict[str, JsonValue] = {}
    for key in (
        "metadata_status",
        "metadata_source",
        "purposes",
        "feature_ids",
        "input_modalities",
        "output_modalities",
        "claim_sources",
        "artifact_notes",
        "attribution",
        "license_notice",
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
        "analysis_notes",
    ):
        value = summary.get(key)
        if len(json.dumps({**result, key: value}, separators=(",", ":"))) <= 1536:
            result[key] = value
    return result


def purpose_ids(tasks: list[str], capabilities: list[str]) -> list[str]:
    """Translate declared tasks to browsing categories, without rating usefulness."""
    return cast(
        list[str],
        capability_details(
            {
                "tasks": cast(list[JsonValue], tasks),
                "capabilities": cast(list[JsonValue], capabilities),
            }
        )["purposes"],
    )


def model_summary(
    body: dict[str, JsonValue],
    *,
    repository: str | None = None,
    artifact_format: str | None = None,
    quantization: str | None = None,
    claims: JsonValue = None,
    selected_files: Iterable[str] | None = None,
) -> dict[str, JsonValue]:
    """Describe existing model metadata without requiring a portable envelope.

    Hosts may project installed cards with mutable or unknown revisions. This
    pure, bounded view neither manufactures a card identity nor validates any
    claim. Original card bytes, signatures and runtime admission stay separate.
    """
    tasks, capabilities = (
        _strings(body.get("tasks"), max_items=None),
        _strings(body.get("capabilities"), max_items=None),
    )
    tasks = list(
        dict.fromkeys(tasks + _strings(body.get("pipeline_tag"), max_items=None))
    )
    context = body.get("context_length")
    license_info = body.get("license")
    license_fields = license_info if isinstance(license_info, dict) else {}
    return _bounded(
        {
            "description": _text(body.get("description")) or _text(body.get("summary")),
            "publisher": repository.split("/", 1)[0] if repository else None,
            "repository": repository,
            "base_model": _text(body.get("base_model"), 512),
            "format": artifact_format,
            "quantization": quantization,
            "workloads": cast(list[JsonValue], tasks[:8]),
            "capabilities": cast(list[JsonValue], capabilities[:8]),
            **capability_details(body, claims=claims, selected_files=selected_files),
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
            "metadata_source": "model-card-and-captured-claims"
            if claims
            else "model-card",
            "metadata_status": "available",
        }
    )


def card_summary(
    card: ModelCardEnvelope, *, metadata: dict[str, JsonValue] | None = None
) -> dict[str, JsonValue]:
    """Project publisher-provided metadata without fetching or rewriting its card."""
    return model_summary(
        card.card,
        repository=card.artifact.repository,
        artifact_format=card.artifact.format,
        quantization=card.artifact.quantization,
        claims=metadata.get("capability_claims") if metadata else None,
        selected_files=(file.path for file in card.artifact.bundle.files)
        if card.artifact.bundle
        else (),
    )


def captured_card_metadata(
    proof: RegistryProof | None, card: ModelCardEnvelope
) -> dict[str, JsonValue]:
    """Read a captured catalog's matching claims without granting registry trust.

    Callers authenticate their cache/archive separately. A retained proof is
    evidence to display, never an enrolled root or an execution permission.
    The selected original card must match; another card's claims cannot leak in.
    """
    if proof is None or proof.target_path != "v1/catalog.json":
        return {}
    try:
        value = strict_json(decode_base64(proof.target))
        if not isinstance(value, dict):
            return {}
        cards, metadata = value.get("cards"), value.get("card_metadata")
        if not isinstance(cards, list) or not isinstance(metadata, dict):
            return {}
        matching = [
            item
            for item in cards
            if isinstance(item, dict) and item.get("card_id") == card.card_id
        ]
        if len(matching) != 1 or load_card(identity_json(matching[0])) != card:
            return {}
        selected = metadata.get(card.card_id)
        return selected if isinstance(selected, dict) else {}
    except ValueError:
        return {}


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
        **capability_details({"tasks": cast(list[JsonValue], tasks)}),
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


def copy_metadata(
    store: Path, manifest_id: str, trust: TrustProfile
) -> tuple[dict[str, JsonValue], str]:
    """Read authenticated metadata only, never weights, network or executable code.

    Return a bounded display summary and complete searchable supported metadata.
    The latter stays inside the host cache, not its response or authority journal.
    Unknown profiles remain opaque. An unavailable or oversized optional card
    leaves common PMF metadata usable; it does not prevent recovery or grant trust.
    """
    with directory(store) as root:
        with open_file(root, manifest_path(manifest_id)) as stream:
            envelope = stream.read(512 * 1024 + 1)
        if len(envelope) > 512 * 1024:
            raise PreservationError("metadata_preview_too_large")
        manifest = verify_pmf_manifest(envelope, trust, manifest_id)
        summary = manifest_summary(manifest)
        searchable = manifest.model_dump_json()
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
                if document.size > MAX_METADATA_BYTES:
                    break
                with open_file(root, object_path(document.sha256)) as stream:
                    data = stream.read(MAX_METADATA_BYTES + 1)
                if len(data) != document.size or digest(data) != document.sha256:
                    break
                captured = load_retained_card(data)
                retained = captured.envelope
                metadata = captured_card_metadata(captured.registry_proof, retained)
                searchable += " " + identity_json(retained.card).decode()
                searchable += " " + identity_json(metadata).decode()
                projected = card_summary(retained, metadata=metadata)
                # A recognized ecosystem card must not erase common PMF claims.
                for field in (
                    "purposes",
                    "feature_ids",
                    "input_modalities",
                    "output_modalities",
                    "claim_sources",
                    "artifact_notes",
                ):
                    projected[field] = cast(
                        list[JsonValue],
                        list(
                            dict.fromkeys(
                                _strings(summary.get(field), max_items=None)
                                + _strings(projected.get(field), max_items=None)
                            )
                        ),
                    )
                projected["workloads"] = cast(
                    list[JsonValue],
                    list(
                        dict.fromkeys(
                            list(manifest.workloads)
                            + _strings(retained.card.get("tasks"), max_items=None)
                        )
                    )[:8],
                )
                projected["runtime_requirements"] = summary.get(
                    "runtime_requirements", []
                )
                projected["description"] = projected.get("description") or summary.get(
                    "description"
                )
                projected["license"] = projected.get("license") or summary.get(
                    "license"
                )
                return _bounded(projected), searchable
            except (ValueError, OSError):
                break
        return _bounded(summary), searchable


def copy_summary(
    store: Path, manifest_id: str, trust: TrustProfile
) -> dict[str, JsonValue]:
    """Return only the bounded descriptive view of authenticated archive metadata."""
    return copy_metadata(store, manifest_id, trust)[0]
