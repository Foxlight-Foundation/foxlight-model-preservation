"""Portable metadata stays bounded, operator-only and independent of the SDK."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from foxlight_preservation_library.client import CoreClient, CoreRefused
from foxlight_preservation_library.contracts import Settings
from foxlight_preservation_library.server import LibraryServer


async def test_peer_plan_keeps_grants_separate_and_binds_sharing_owner() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def core(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, json.loads(request.content or b"{}")))
        return httpx.Response(200, json={"kind": "peer_share"})

    client = CoreClient(Settings(), "local", transport=httpx.MockTransport(core))
    server = LibraryServer(None, client, asyncio.get_running_loop())
    try:
        await server.dispatch(
            "sharing-plan",
            {"kind": "http_share", "location_id": "public-kit"},
        )
        assert "allow_peer_network" not in calls[-1][1]
        assert "peer_setup_id" not in calls[-1][1]
        await server.dispatch(
            "sharing-plan",
            {
                "kind": "peer_share",
                "location_id": "public-kit",
                "allow_peer_network": True,
                "peer_setup_id": "d" * 64,
            },
        )
        assert calls[-1][1]["allow_peer_network"] is True
        assert calls[-1][1]["peer_setup_id"] == "d" * 64
        await server.dispatch(
            "start",
            {
                "plan_id": "a" * 64,
                "operation_id": "share",
                "public_participation": True,
            },
        )
        assert calls[-1][1]["owner_id"] == client.sharing_owner_id
        assert "allow_repository_code" not in calls[-1][1]
    finally:
        await client.close()


async def test_active_or_stopped_share_resolves_its_plan_without_outcome_fields() -> (
    None
):
    calls: list[str] = []
    owner = "other"
    plan: dict[str, object] = {
        "kind": "peer_share",
        "copy_id": "b" * 64,
        "manifest_id": "c" * 64,
    }

    def core(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={"owner_id": owner, "kind": "peer_share", "plan_id": "a" * 64}
            if "/jobs/" in request.url.path
            else plan,
        )

    client = CoreClient(Settings(), "local", transport=httpx.MockTransport(core))
    server = LibraryServer(None, client, asyncio.get_running_loop())
    try:
        with pytest.raises(CoreRefused, match="job_owner_changed"):
            await server.dispatch("sharing-reference", {"job_id": "d" * 64})
        assert len(calls) == 1
        owner = client.sharing_owner_id
        assert await server.dispatch("sharing-reference", {"job_id": "d" * 64}) == {
            "copy_id": "b" * 64,
            "manifest_id": "c" * 64,
        }
        assert calls[-1] == "/preservation/plans/" + "a" * 64
        plan["copy_id"] = "../../private"
        with pytest.raises(CoreRefused, match="public_metadata_refused"):
            await server.dispatch("sharing-reference", {"job_id": "d" * 64})
        before = len(calls)
        with pytest.raises(ValueError):
            await server.dispatch("sharing-reference", {"job_id": "../escape"})
        assert len(calls) == before
    finally:
        await client.close()


async def test_handoff_download_is_fixed_bounded_and_rejects_cross_origin() -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    content = b'{"public":"' + b"x" * 80000 + b'"}'
    status = 200

    def core(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, json.loads(request.content)))
        return httpx.Response(status, content=content)

    client = CoreClient(Settings(), "local", transport=httpx.MockTransport(core))
    server = LibraryServer(None, client, asyncio.get_running_loop())
    server.start()
    endpoint = server.url + "api/handoff/" + "b" * 64
    headers = {"X-Preservation-Action": "operator"}
    try:
        async with httpx.AsyncClient(trust_env=False) as browser:
            for body in (
                {"mirrors": ["https://models.example/"]},
                {"sharing_job_id": "d" * 64},
            ):
                reply = await browser.post(endpoint, json=body, headers=headers)
                assert reply.status_code == 200 and reply.content == content
                assert calls[-1] == (
                    "/preservation/public-copies/" + "b" * 64 + "/handoff",
                    body,
                )
            before = len(calls)
            reply = await browser.post(
                endpoint,
                json={"sharing_job_id": "d" * 64},
                headers={**headers, "Origin": "https://untrusted.example"},
            )
            assert reply.status_code == 403
            for body in (
                {},
                {"mirrors": []},
                {"mirrors": [], "sharing_job_id": "d" * 64},
                {"path": "/private"},
            ):
                reply = await browser.post(endpoint, json=body, headers=headers)
                assert reply.status_code == 400
            assert len(calls) == before
            status = 404
            reply = await browser.post(
                endpoint, json={"sharing_job_id": "d" * 64}, headers=headers
            )
            assert (
                reply.status_code == 409
                and reply.json()["error"] == "core_upgrade_required"
            )
            status, content = 200, b"x" * (8 * 1024**2 + 1)
            reply = await browser.post(
                endpoint, json={"sharing_job_id": "d" * 64}, headers=headers
            )
            assert (
                reply.status_code == 409
                and reply.json()["error"] == "public_metadata_too_large"
            )
    finally:
        await asyncio.to_thread(server.close)
        await client.close()
