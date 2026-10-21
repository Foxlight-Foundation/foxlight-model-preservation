"""Public loopback UI behavior independent of managed SDK control calls."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from foxlight_preservation_library.client import CoreClient, CoreRefused
from foxlight_preservation_library.contracts import Settings
from foxlight_preservation_library.server import LibraryServer


async def test_public_screen_is_ticket_scoped_and_cannot_be_rebound() -> None:
    requests: list[str] = []

    def core(request: httpx.Request) -> httpx.Response:
        requests.append(request.method + " " + request.url.path)
        return httpx.Response(
            200, json={"jobs": [], "locations": [], "models": [], "copies": []}
        )

    client = CoreClient(
        Settings(), "library-local", transport=httpx.MockTransport(core)
    )
    server = LibraryServer(None, client, asyncio.get_running_loop())
    server.start()
    try:
        async with httpx.AsyncClient(trust_env=False) as browser:
            page = await browser.get(server.url)
            assert page.status_code == 200 and "Model Preservation" in page.text
            assert page.headers["referrer-policy"] == "no-referrer"
            assert page.headers["cache-control"] == "no-store"
            assert (await browser.get(server.url + "api/library")).status_code == 200
            assert requests == ["GET /preservation/library"]
            assert (
                await browser.get(server.url, headers={"Host": "untrusted.example"})
            ).status_code == 403
            assert (
                await browser.get(
                    server.url, headers={"Origin": "https://untrusted.example"}
                )
            ).status_code == 403
            assert (
                await browser.get(server.url.replace(server.prefix, "/"))
            ).status_code == 403
        assert not requests[1:]
    finally:
        await asyncio.to_thread(server.close)
        await client.close()


async def test_cleanup_checks_job_owner_and_never_starts_a_restore() -> None:
    seen: list[str] = []
    owner = "somebody-else"

    def core(request: httpx.Request) -> httpx.Response:
        seen.append(request.method + " " + request.url.path)
        return httpx.Response(200, json={"owner_id": owner})

    client = CoreClient(
        Settings(), "library-local", transport=httpx.MockTransport(core)
    )
    server = LibraryServer(None, client, asyncio.get_running_loop())
    server.start()
    try:
        with pytest.raises(CoreRefused, match="job_owner_changed"):
            await server.dispatch("reclaim", {"job_id": "a" * 64})
        assert len(seen) == 1
        owner = "library-local"
        async with httpx.AsyncClient(trust_env=False) as browser:
            response = await browser.post(
                server.url + "api/reclaim",
                json={"job_id": "a" * 64},
                headers={"X-Preservation-Action": "operator"},
            )
            assert response.status_code == 200
        assert seen[-1] == "POST /preservation/jobs/" + "a" * 64 + "/reclaim"
        assert not any(path.endswith("/jobs") for path in seen)
    finally:
        await asyncio.to_thread(server.close)
        await client.close()


async def test_sharing_uses_separate_owner_and_never_starts_during_review() -> None:
    import json

    from foxlight_preservation_library.contracts import LibraryCall

    selected = {"plan_id": "a" * 64, "kind": "mirror_import"}
    submissions: list[dict[str, object]] = []
    calls: list[str] = []
    sharing_owner = ""

    def core(request: httpx.Request) -> httpx.Response:
        calls.append(request.method + " " + request.url.path)
        if request.url.path.startswith("/preservation/plans"):
            return httpx.Response(200, json=selected)
        if request.method == "POST" and request.url.path == "/preservation/jobs":
            submissions.append(json.loads(request.content))
        if request.url.path == "/preservation/library":
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {"owner_id": sharing_owner},
                        {"owner_id": "library-local"},
                        {"owner_id": "unrelated"},
                    ]
                },
            )
        return httpx.Response(200, json={"owner_id": sharing_owner})

    client = CoreClient(
        Settings(), "library-local", transport=httpx.MockTransport(core)
    )
    sharing_owner = client.sharing_owner_id
    server = LibraryServer(None, client, asyncio.get_running_loop())
    server.start()
    try:
        async with httpx.AsyncClient(trust_env=False) as browser:
            headers = {"X-Preservation-Action": "operator"}
            response = await browser.post(
                server.url + "api/sharing-plan",
                json={
                    "kind": "mirror_import",
                    "location_id": "public-kit",
                    "upload_id": "1" * 32,
                },
                headers=headers,
            )
            assert response.status_code == 200 and not submissions
            response = await browser.post(
                server.url + "api/start",
                json={
                    "plan_id": selected["plan_id"],
                    "operation_id": "import",
                    "accept_owner_capture": True,
                },
                headers=headers,
            )
            assert (
                response.status_code == 200
                and submissions[0]["owner_id"] == sharing_owner
            )
            assert "public_participation" not in submissions[0]
            selected["kind"] = "http_share"
            response = await browser.post(
                server.url + "api/start",
                json={
                    "plan_id": selected["plan_id"],
                    "operation_id": "share",
                    "public_participation": True,
                },
                headers=headers,
            )
            assert (
                response.status_code == 200
                and submissions[1]["public_participation"] is True
            )
            response = await browser.post(
                server.url + "api/sharing-plan",
                json={"kind": "recover", "location_id": "kit", "owner_id": "intruder"},
                headers=headers,
            )
            assert response.status_code != 200
        assert (await client.library(LibraryCall())).report["jobs"] == [
            {"owner_id": sharing_owner},
            {"owner_id": "library-local"},
        ]
        await client.cancel_job("b" * 64)
        assert calls[-1].endswith("/cancel")
    finally:
        await asyncio.to_thread(server.close)
        await client.close()


async def test_local_screen_does_not_require_reverse_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket

    def forbidden_lookup(name: str = "") -> str:
        pytest.fail("the local Library screen must not require reverse DNS")

    monkeypatch.setattr(socket, "getfqdn", forbidden_lookup)
    client = CoreClient(
        Settings(),
        "library-local",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )
    server = LibraryServer(None, client, asyncio.get_running_loop())
    server.start()
    try:
        async with httpx.AsyncClient(trust_env=False) as browser:
            page = await browser.get(server.url)
            assert page.status_code == 200 and "Model Preservation" in page.text
    finally:
        await asyncio.to_thread(server.close)
        await client.close()
