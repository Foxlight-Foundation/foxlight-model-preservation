"""Bounded publisher/recipient bridge, independent of the managed SDK."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pydantic import JsonValue

from foxlight_preservation_library.client import CoreClient, CoreRefused
from foxlight_preservation_library.contracts import Settings
from foxlight_preservation_library.server import LibraryServer


async def test_delivery_start_and_resume_bind_the_separate_original_owner() -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []
    selected: dict[str, object] = {"kind": "publish"}
    owner = "unrelated"

    def core(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        calls.append((request.method, request.url.path, body))
        if "/plans/" in request.url.path:
            return httpx.Response(200, json=selected)
        if request.method == "GET" and "/jobs/" in request.url.path:
            return httpx.Response(200, json={"owner_id": owner, "kind": "receive"})
        return httpx.Response(200, json={"job_id": "b" * 64})

    client = CoreClient(Settings(), "local", transport=httpx.MockTransport(core))
    server = LibraryServer(None, client, asyncio.get_running_loop())
    submission = {"plan_id": "a" * 64, "operation_id": "fresh-action"}
    try:
        for kind in ("publish", "receive"):
            selected["kind"] = kind
            await server.dispatch("start", submission)
            assert calls[-1][2]["owner_id"] == client.sharing_owner_id
        selected["resume_job_id"] = "b" * 64
        with pytest.raises(CoreRefused, match="job_owner_changed"):
            await server.dispatch("resume-receive", {**submission, "job_id": "b" * 64})
        assert not calls[-1][1].endswith("/resume")
        owner = client.sharing_owner_id
        await server.dispatch("resume-receive", {**submission, "job_id": "b" * 64})
        assert calls[-1] == (
            "POST",
            "/preservation/jobs/" + "b" * 64 + "/resume",
            {**submission, "owner_id": owner},
        )
        await server.dispatch(
            "delivery-plan",
            {"kind": "receive", "location_id": "invitation", "resume_job_id": "b" * 64},
        )
        assert calls[-1][2] == {
            "kind": "receive",
            "location_id": "invitation",
            "resume_job_id": "b" * 64,
        }
        assert "download_limit_bytes" not in calls[-1][2]
        with pytest.raises(ValueError):
            await server.dispatch(
                "delivery-plan",
                {
                    "kind": "receive",
                    "location_id": "invitation",
                    "owner_id": "injected",
                },
            )
        with pytest.raises(CoreRefused):
            await server.dispatch("start", {**submission, "owner_id": "injected"})
    finally:
        await client.close()


async def test_public_metadata_uses_a_separate_bounded_plane_and_origin_guard() -> None:
    calls: list[tuple[str, str]] = []
    content = b'{"public":"' + b"x" * 80000 + b'"}'

    def core(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return httpx.Response(
            200, content=content, headers={"Content-Type": "application/json"}
        )

    client = CoreClient(Settings(), "local", transport=httpx.MockTransport(core))
    server = LibraryServer(None, client, asyncio.get_running_loop())
    server.start()
    try:
        async with httpx.AsyncClient(trust_env=False) as browser:
            metadata = await browser.get(server.url + "api/manifest/" + "a" * 64)
            assert metadata.status_code == 200 and metadata.content == content
            exported = await browser.post(
                server.url + "api/invitation/" + "b" * 64,
                json={"mirrors": ["https://example.org/"]},
                headers={"X-Preservation-Action": "operator"},
            )
            assert exported.status_code == 200 and exported.content == content
            assert calls == [
                ("GET", "/preservation/plans/" + "a" * 64 + "/public-manifest"),
                ("POST", "/preservation/public-copies/" + "b" * 64 + "/invitation"),
            ]
            before = len(calls)
            for action in (
                "publisher-setup",
                "publication-source",
                "invitation-inspect",
                "receive-preview",
                "delivery-plan",
                "resume-receive",
                "invitation/" + "b" * 64,
            ):
                refused = await browser.post(
                    server.url + "api/" + action,
                    json={},
                    headers={
                        "X-Preservation-Action": "operator",
                        "Origin": "https://untrusted.example",
                    },
                )
                assert refused.status_code == 403
            assert len(calls) == before
            content = b"x" * (8 * 1024**2 + 1)
            refused = await browser.get(server.url + "api/manifest/" + "a" * 64)
            assert refused.status_code == 409 and refused.json() == {
                "error": "public_metadata_too_large"
            }
    finally:
        await asyncio.to_thread(server.close)
        await client.close()


@pytest.mark.parametrize("purpose", [{}, [], 12, None, "unrecognized"])
async def test_unknown_metadata_purpose_refuses_before_core(purpose: JsonValue) -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        pytest.fail("invalid purpose reached Core")

    client = CoreClient(Settings(), "local", transport=httpx.MockTransport(forbidden))
    server = LibraryServer(None, client, asyncio.get_running_loop())
    try:
        with pytest.raises(CoreRefused, match="invalid_upload"):
            await server.dispatch(
                "upload-create", {"size_bytes": 1, "purpose": purpose}
            )
    finally:
        await client.close()
