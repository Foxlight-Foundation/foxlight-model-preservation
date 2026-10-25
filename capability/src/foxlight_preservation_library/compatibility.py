"""Read Core API compatibility without enrolling archives or starting jobs."""

from __future__ import annotations

import json

import httpx
from pydantic import JsonValue

from .client import CoreClient, CoreRefused
from .contracts import LibraryCall

GROUPS: dict[str, tuple[tuple[str, str], ...]] = {
    "Library": (
        ("/preservation/library", "get"),
        ("/preservation/plans", "post"),
        ("/preservation/jobs", "post"),
    ),
    "Peer discovery": (
        ("/preservation/discovery/peer", "get"),
        ("/preservation/discovery/peer", "post"),
    ),
    "Peer software setup": (
        ("/preservation/discovery/peer/runtime", "get"),
        ("/preservation/discovery/peer/runtime/jobs", "post"),
        ("/preservation/discovery/peer/runtime/jobs/{job_id}", "delete"),
    ),
    "Source discovery": (
        ("/preservation/discovery/reviews", "post"),
        ("/preservation/discovery/sources", "post"),
        ("/preservation/discovery/sources/{source_id}", "delete"),
        ("/preservation/discovery", "get"),
        ("/preservation/discovery/search", "post"),
        ("/preservation/discovery/jobs", "post"),
        ("/preservation/discovery/jobs/{job_id}", "get"),
        ("/preservation/discovery/jobs/{job_id}/cancel", "post"),
    ),
    "Folder storage": (
        ("/preservation/setup/folder/review", "post"),
        ("/preservation/setup/folder", "post"),
        ("/preservation/setup/default", "post"),
    ),
    "Recovery kits": (
        ("/preservation/kit-uploads", "post"),
        ("/preservation/kits/{job_id}", "get"),
    ),
    "Portable handoffs": (("/preservation/public-copies/{copy_id}/handoff", "post"),),
    "Public copy review": (("/preservation/public-copies/{copy_id}/review", "get"),),
    "Persistent mirrors": (
        ("/preservation/mirror", "get"),
        ("/preservation/mirror/jobs", "get"),
        ("/preservation/mirror/jobs", "post"),
        ("/preservation/mirror/jobs/{job_id}", "get"),
        ("/preservation/mirror/jobs/{job_id}", "delete"),
    ),
    "Retained recovery": (
        ("/preservation/mirror/models/{manifest_id}", "get"),
        ("/preservation/mirror/models/{manifest_id}/review", "get"),
        ("/preservation/mirror/kits/{job_id}", "get"),
        ("/preservation/mirror/kits/{job_id}", "head"),
        ("/preservation/mirror/kits/{job_id}", "delete"),
    ),
    "Publishing and receiving": (
        ("/preservation/setup/publisher", "post"),
        ("/preservation/jobs/{job_id}/invitation", "get"),
        ("/preservation/jobs/{job_id}/resume", "post"),
    ),
}


async def check_core(client: CoreClient) -> dict[str, bool]:
    """Check the running owner and bounded OpenAPI surface, without mutation.

    This is API compatibility evidence, not job completion, filesystem or
    transport qualification. Version numbers are not used to infer support.
    """
    await client.library(LibraryCall())
    try:
        async with client.http.stream("GET", "/api/openapi.json") as response:
            if response.status_code != 200:
                raise CoreRefused("core_upgrade_required")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 4 * 1024 * 1024:
                    raise CoreRefused("host_response_too_large")
        value: JsonValue = json.loads(data)
    except httpx.HTTPError as error:
        raise CoreRefused("host_unavailable") from error
    except (ValueError, TypeError) as error:
        if isinstance(error, CoreRefused):
            raise
        raise CoreRefused("invalid_host_response") from error
    paths = value.get("paths") if isinstance(value, dict) else None
    if not isinstance(paths, dict):
        raise CoreRefused("invalid_host_response")
    report: dict[str, bool] = {}
    for name, required in GROUPS.items():
        supported = True
        for path, method in required:
            methods = paths.get(path)
            supported = supported and isinstance(methods, dict) and method in methods
        report[name] = supported
    # Existing paths alone cannot establish I2P support or safe Resume display.
    components = value.get("components") if isinstance(value, dict) else None
    schemas = components.get("schemas") if isinstance(components, dict) else None
    required_fields = {
        "SourceConfirmation": {"allow_i2p", "i2p_proxy", "i2p_endpoints"},
        "DiscoveryRequest": {"allow_i2p", "i2p_policy_id"},
        "ReceivingPolicy": {"allow_i2p", "i2p_proxy"},
        "InvitationPreview": {"receiving"},
    }
    supported_i2p = report["Source discovery"] and report["Publishing and receiving"]
    for name, fields in required_fields.items():
        schema = schemas.get(name) if isinstance(schemas, dict) else None
        properties = schema.get("properties") if isinstance(schema, dict) else None
        supported_i2p = (
            supported_i2p
            and isinstance(properties, dict)
            and fields.issubset(properties)
        )
    report["I2P preservation jobs"] = supported_i2p
    # Routes alone do not establish the authority and lifecycle contract.
    review_fields = {
        "PublicCopyReview": {
            "schema_version",
            "copy_id",
            "manifest_id",
            "archive_format",
            "name",
            "size_bytes",
            "workloads",
            "runtime_requirements",
            "members",
        },
        "PublicationMember": {
            "member_id",
            "role",
            "repository",
            "revision",
            "format",
            "size_bytes",
            "file_count",
            "license_id",
            "terms_reference",
            "notice_paths",
        },
    }
    mirror_fields = {
        "MirrorRequest": {
            "owner_id",
            "operation_id",
            "kind",
            "policy_id",
            "configuration",
            "copy_id",
            "manifest_id",
            "action",
            "confirm_redistribution",
            "confirm_reintake",
            "public_participation",
            "mode",
            "seconds",
            "resume_expires",
            "confirm_resume",
            "confirm_delete_manifest",
        },
        "MirrorConfiguration": {
            "name",
            "disk_bytes",
            "ingress_daily",
            "egress_daily",
            "egress_monthly",
            "http",
            "exposure",
            "port",
            "ipfs",
            "torrent",
            "peer_setup_id",
            "max_active",
            "session_seconds",
            "session_bytes",
            "upload_rate",
        },
        "MirrorPage": {
            "schema_version",
            "configured",
            "available",
            "policy_id",
            "configuration",
            "paused",
            "restart_expires",
            "restart_allowed",
            "controller_job_id",
            "activity",
            "models",
            "model_count",
            "next_offset",
        },
        "MirrorJob": {
            "job_id",
            "owner_id",
            "operation_id",
            "kind",
            "state",
            "activity",
        },
    }
    recovery_fields = {
        "MirrorSelection": {
            "schema_version",
            "manifest_id",
            "configured",
            "available",
            "policy_id",
            "model",
        },
        "MirrorReview": {
            "schema_version",
            "manifest_id",
            "archive_format",
            "name",
            "size_bytes",
            "workloads",
            "runtime_requirements",
            "members",
            "capture_key_ids",
            "capture_threshold",
        },
        "MirrorModel": {
            "manifest_id",
            "state",
            "pinned",
            "blocked",
            "name",
            "member_count",
            "size_bytes",
        },
        "MirrorKit": {"state", "manifest_id", "size_bytes", "sha256"},
        "MirrorKitRemoval": {"confirm_job_id"},
        "MirrorJob": {"kit", "kit_removed"},
    }
    for group, required in (
        ("Public copy review", review_fields),
        ("Persistent mirrors", mirror_fields),
        ("Retained recovery", recovery_fields),
    ):
        supported = report[group]
        for name, fields in required.items():
            schema = schemas.get(name) if isinstance(schemas, dict) else None
            properties = schema.get("properties") if isinstance(schema, dict) else None
            supported = (
                supported
                and isinstance(properties, dict)
                and fields.issubset(properties)
            )
        report[group] = supported
    for group, name, expected in (
        ("Persistent mirrors", "MirrorPage", "skulk.preservation-mirror.v1"),
        (
            "Retained recovery",
            "MirrorSelection",
            "skulk.preservation-mirror-selection.v1",
        ),
        ("Retained recovery", "MirrorReview", "skulk.preservation-mirror-review.v1"),
        (
            "Public copy review",
            "PublicCopyReview",
            "skulk.preservation-public-review.v1",
        ),
    ):
        schema = schemas.get(name) if isinstance(schemas, dict) else None
        properties = schema.get("properties") if isinstance(schema, dict) else None
        version = (
            properties.get("schema_version") if isinstance(properties, dict) else None
        )
        report[group] = (
            report[group]
            and isinstance(version, dict)
            and version.get("const") == expected
        )
    request_schema = schemas.get("MirrorRequest") if isinstance(schemas, dict) else None
    properties = (
        request_schema.get("properties") if isinstance(request_schema, dict) else None
    )
    kind = properties.get("kind") if isinstance(properties, dict) else None
    kinds = kind.get("enum") if isinstance(kind, dict) else None
    report["Retained recovery"] = (
        report["Retained recovery"]
        and report["Persistent mirrors"]
        and report["Public copy review"]
        and isinstance(kinds, list)
        and "prepare_kit" in kinds
    )
    return report
