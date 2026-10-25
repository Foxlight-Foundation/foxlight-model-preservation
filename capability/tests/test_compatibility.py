"""Compatibility reads the actual surface rather than trusting a Core version."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import JsonValue

from foxlight_preservation_library.client import CoreClient, CoreRefused
from foxlight_preservation_library.compatibility import GROUPS, check_core
from foxlight_preservation_library.contracts import Settings


async def test_api_check_is_read_only_and_reports_missing_folder_routes() -> None:
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.method + " " + request.url.path)
        if request.url.path == "/preservation/library":
            return httpx.Response(200, json={"jobs": []})
        return httpx.Response(
            200,
            json={
                "info": {"version": "999.0.0"},
                "paths": {
                    path: {method: {}}
                    for name, pairs in GROUPS.items()
                    if name != "Folder storage"
                    for path, method in pairs
                },
            },
        )

    client = CoreClient(
        Settings(), "library-local", transport=httpx.MockTransport(respond)
    )
    try:
        report = await check_core(client)
        assert not report["Folder storage"] and report["Library"]
        assert requests == ["GET /preservation/library", "GET /api/openapi.json"]
    finally:
        await client.close()


@pytest.mark.parametrize(
    "missing", [None, "head", "kit_removed", "prepare_kit", "version"]
)
async def test_retained_recovery_requires_its_complete_new_contract(
    missing: str | None,
) -> None:
    value = json.loads(
        (Path(__file__).parent / "fixtures/core-mirror-contract-v1.json").read_text()
    )
    schemas = value["components"]["schemas"]
    if missing == "head":
        value["paths"]["/preservation/mirror/kits/{job_id}"].pop("head")
    elif missing == "kit_removed":
        schemas["MirrorJob"]["properties"].pop("kit_removed")
    elif missing == "prepare_kit":
        schemas["MirrorRequest"]["properties"]["kind"]["enum"].remove("prepare_kit")
    elif missing == "version":
        schemas["MirrorReview"]["properties"]["schema_version"]["const"] = "unknown"

    def core(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={"jobs": []} if request.url.path == "/preservation/library" else value,
        )

    client = CoreClient(Settings(), "local", transport=httpx.MockTransport(core))
    try:
        report = await check_core(client)
        assert report["Retained recovery"] is (missing is None)
        assert report["Persistent mirrors"] and report["Public copy review"]
    finally:
        await client.close()


async def test_compatibility_requires_a_real_available_store_owner() -> None:
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(
            503, json={"detail": "configure_model_store_on_this_host"}
        )

    client = CoreClient(
        Settings(), "library-local", transport=httpx.MockTransport(respond)
    )
    try:
        with pytest.raises(CoreRefused, match="configure_model_store_on_this_host"):
            await check_core(client)
        assert requests == ["/preservation/library"]
    finally:
        await client.close()


async def test_native_setup_support_is_reported_separately_without_acquisition() -> (
    None
):
    requests: list[str] = []
    paths: dict[str, dict[str, object]] = {}
    for name, pairs in GROUPS.items():
        if name == "Peer software setup":
            continue
        for path, method in pairs:
            paths.setdefault(path, {})[method] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.method + " " + request.url.path)
        return httpx.Response(
            200,
            json={"jobs": []}
            if request.url.path == "/preservation/library"
            else {"paths": paths, "info": {"version": "999.0.0"}},
        )

    client = CoreClient(
        Settings(), "library-local", transport=httpx.MockTransport(respond)
    )
    try:
        report = await check_core(client)
        assert report["Library"] and report["Peer discovery"]
        assert not report["Peer software setup"]
        assert requests == ["GET /preservation/library", "GET /api/openapi.json"]
    finally:
        await client.close()


@pytest.mark.parametrize(
    "schema,field,mirrors,review",
    [
        (None, None, True, True),
        ("route", None, True, False),
        ("MirrorRequest", "confirm_delete_manifest", False, True),
        ("MirrorRequest", "confirm_resume", False, True),
        ("PublicCopyReview", "members", True, False),
        ("PublicationMember", "notice_paths", True, False),
        ("MirrorJob", "owner_id", False, True),
        ("MirrorPage", "schema_version", False, True),
        ("PublicCopyReview", "schema_version", True, False),
    ],
)
async def test_actual_mirror_contract_keeps_stop_and_terms_support_distinct(
    schema: str | None, field: str | None, mirrors: bool, review: bool
) -> None:
    """Negotiate a generated Core fragment and refuse missing authority fields.

    An older owner without the new terms view must still expose its existing
    persistent-controller Stop contract. Neither check acquires software, starts
    a byte job or grants network permission.
    """
    value: JsonValue = json.loads(
        (Path(__file__).parent / "fixtures/core-mirror-contract-v1.json").read_text()
    )
    assert isinstance(value, dict)
    paths, components = value["paths"], value["components"]
    assert isinstance(paths, dict) and isinstance(components, dict)
    if schema == "route":
        paths.pop("/preservation/public-copies/{copy_id}/review")
    elif schema is not None:
        schemas = components["schemas"]
        assert isinstance(schemas, dict)
        model = schemas[schema]
        assert isinstance(model, dict)
        properties = model["properties"]
        assert isinstance(properties, dict) and field is not None
        if field == "schema_version":
            properties[field] = {"const": "unknown.future.version"}
        else:
            properties.pop(field)
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.method + " " + request.url.path)
        return httpx.Response(
            200,
            json={"jobs": []} if request.url.path == "/preservation/library" else value,
        )

    client = CoreClient(
        Settings(), "library-local", transport=httpx.MockTransport(respond)
    )
    try:
        report = await check_core(client)
        assert report["Persistent mirrors"] is mirrors
        assert report["Public copy review"] is review
        assert requests == ["GET /preservation/library", "GET /api/openapi.json"]
    finally:
        await client.close()
