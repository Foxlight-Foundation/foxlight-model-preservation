"""Compatibility reads the actual surface rather than trusting a Core version."""

from __future__ import annotations

import httpx
import pytest

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
