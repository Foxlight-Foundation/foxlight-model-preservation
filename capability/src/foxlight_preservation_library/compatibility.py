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
    return report
