"""Real ticket-scoped setup controls preserve the governed copy boundary."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from foxlight_preservation_library.client import CoreClient, CoreRefused
from foxlight_preservation_library.contracts import CopyInput, Settings
from foxlight_preservation_library.server import LibraryServer


async def test_exact_operator_folder_controls_cross_the_real_local_listener() -> None:
    seen: list[tuple[str, object]] = []

    def core(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"location_id": "folder_abc"})

    client = CoreClient(
        Settings(), "library-local", transport=httpx.MockTransport(core)
    )
    server = LibraryServer(None, client, asyncio.get_running_loop())
    server.start()
    try:
        async with httpx.AsyncClient(trust_env=False) as browser:
            for action, body, target in (
                (
                    "folder-review",
                    {"path": "/mounted/private-models", "label": "My NAS"},
                    "setup/folder/review",
                ),
                (
                    "folder-setup",
                    {"review_id": "a" * 64, "confirmed": True},
                    "setup/folder",
                ),
                ("storage-default", {"location_id": "folder_abc"}, "setup/default"),
            ):
                reply = await browser.post(
                    server.url + "api/" + action,
                    json=body,
                    headers={"X-Preservation-Action": "operator"},
                )
                assert reply.status_code == 200
                assert seen[-1] == ("/preservation/" + target, body)
            for action, body in (
                (
                    "folder-setup",
                    {"review_id": "a" * 64, "confirmed": True, "path": "/another"},
                ),
                ("storage-default", {"location_id": "../outside"}),
                (
                    "folder-review",
                    {
                        "path": "/mounted/private-models",
                        "label": "My NAS",
                        "owner_id": "arbitrary",
                    },
                ),
            ):
                reply = await browser.post(
                    server.url + "api/" + action,
                    json=body,
                    headers={"X-Preservation-Action": "operator"},
                )
                assert reply.status_code == 409
            assert len(seen) == 3
            assert not any(path.endswith("/jobs") for path, _ in seen)
            assert "path" not in CopyInput.model_fields
    finally:
        await asyncio.to_thread(server.close)
        await client.close()


@pytest.mark.parametrize(
    "path", ["setup/folder/review", "setup/folder", "setup/default"]
)
async def test_older_core_storage_routes_explain_upgrade(path: str) -> None:
    client = CoreClient(
        Settings(),
        "library-local",
        transport=httpx.MockTransport(lambda _: httpx.Response(404)),
    )
    try:
        with pytest.raises(CoreRefused, match="core_upgrade_required"):
            await client.request("POST", path, {})
    finally:
        await client.close()


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"detail": "storage_unavailable"}, "storage_unavailable"),
        (
            {"error": {"message": "storage_unavailable", "type": "Conflict"}},
            "storage_unavailable",
        ),
        (
            {"error": {"message": "private path /operator/keys"}},
            "host_unavailable_or_refused",
        ),
    ],
)
async def test_actual_core_and_legacy_error_envelopes_keep_only_bounded_codes(
    body: dict[str, object], expected: str
) -> None:
    client = CoreClient(
        Settings(),
        "library-local",
        transport=httpx.MockTransport(lambda _: httpx.Response(409, json=body)),
    )
    try:
        with pytest.raises(CoreRefused, match=expected):
            await client.request("POST", "plans", {})
    finally:
        await client.close()
