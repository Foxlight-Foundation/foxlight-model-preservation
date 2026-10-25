"""Offline checks keep exact authority and survive lost replies/client replacement."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from foxlight_preservation_library.client import CoreClient, CoreRefused
from foxlight_preservation_library.compatibility import check_core
from foxlight_preservation_library.contracts import Settings
from foxlight_preservation_library.server import LibraryServer

MANIFEST, POLICY, JOB = "a" * 64, "b" * 64, "c" * 64


def client_for(handler: Callable[[httpx.Request], httpx.Response]) -> CoreClient:
    return CoreClient(
        Settings(), "stable-client", transport=httpx.MockTransport(handler)
    )


async def test_lost_recovery_reply_new_client_and_kit_have_separate_identities() -> (
    None
):
    submitted: list[dict[str, object]] = []
    lost = True

    def core(request: httpx.Request) -> httpx.Response:
        nonlocal lost
        assert request.method == "POST"
        body = json.loads(request.content)
        submitted.append(body)
        if lost:
            lost = False
            raise httpx.ReadError("reply lost", request=request)
        return httpx.Response(202, json={**body, "job_id": JOB, "state": "running"})

    for attempt in range(3):
        client = client_for(core)
        app = LibraryServer(None, client, asyncio.get_running_loop())
        try:
            decision = {"manifest_id": MANIFEST, "policy_id": POLICY}
            if attempt == 0:
                with pytest.raises(CoreRefused, match="host_unavailable"):
                    await app.dispatch("mirror-recover", decision)
            else:
                receipt = await app.dispatch("mirror-recover", decision)
                assert receipt["job_id"] == JOB
            if attempt == 2:
                await app.dispatch("mirror-prepare", decision)
        finally:
            await client.close()
    assert submitted[0] == submitted[1] == submitted[2]
    assert submitted[2]["operation_id"] != submitted[3]["operation_id"]
    assert submitted[0]["kind"] == "recover"
    assert submitted[0]["public_participation"] is False
    assert submitted[0]["confirm_redistribution"] is False
    assert submitted[0]["confirm_resume"] is False
    assert submitted[0]["confirm_delete_manifest"] is None
    assert submitted[0]["configuration"] is None


@pytest.mark.parametrize(
    "changed", [None, "owner_id", "manifest_id", "kind", "state", "job_id"]
)
async def test_retry_requires_exact_own_terminal_recovery(changed: str | None) -> None:
    previous = {
        "job_id": JOB,
        "owner_id": "",
        "manifest_id": MANIFEST,
        "kind": "recover",
        "state": "interrupted",
    }
    submitted: list[dict[str, object]] = []

    def core(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=previous)
        body = json.loads(request.content)
        submitted.append(body)
        return httpx.Response(202, json={**body, "job_id": "d" * 64})

    client = client_for(core)
    previous["owner_id"] = client.mirror_owner_id
    if changed:
        previous[changed] = "running" if changed == "state" else "other"
    app = LibraryServer(None, client, asyncio.get_running_loop())
    try:
        decision = {
            "manifest_id": MANIFEST,
            "policy_id": POLICY,
            "previous_job_id": JOB,
        }
        if changed:
            with pytest.raises(CoreRefused):
                await app.dispatch("mirror-recover", decision)
            assert not submitted
        else:
            await app.dispatch("mirror-recover", decision)
            await app.dispatch("mirror-recover", decision)
            await app.dispatch(
                "mirror-recover", {"manifest_id": MANIFEST, "policy_id": POLICY}
            )
            assert submitted[0] == submitted[1]
            assert submitted[0]["operation_id"] != submitted[2]["operation_id"]
    finally:
        await client.close()


@pytest.mark.parametrize(
    "changed", ["owner_id", "manifest_id", "kind", "operation_id", "job_id"]
)
async def test_changed_recovery_reply_is_refused(changed: str) -> None:
    def core(request: httpx.Request) -> httpx.Response:
        body = {**json.loads(request.content), "job_id": JOB}
        body[changed] = "other"
        return httpx.Response(202, json=body)

    client = client_for(core)
    try:
        with pytest.raises(CoreRefused, match="invalid_job_identity"):
            await LibraryServer(None, client, asyncio.get_running_loop()).dispatch(
                "mirror-recover", {"manifest_id": MANIFEST, "policy_id": POLICY}
            )
    finally:
        await client.close()


@pytest.mark.parametrize("extra", ["copy_id", "public_participation", "path", "trust"])
async def test_recovery_accepts_no_receiving_sharing_path_or_trust(extra: str) -> None:
    calls = 0

    def core(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    client = client_for(core)
    try:
        with pytest.raises(ValidationError):
            await LibraryServer(None, client, asyncio.get_running_loop()).dispatch(
                "mirror-recover",
                {"manifest_id": MANIFEST, "policy_id": POLICY, extra: "unrelated"},
            )
        assert calls == 0
    finally:
        await client.close()


async def test_real_http_bridge_latest_beyond_twenty_and_exact_check() -> None:
    methods: list[str] = []
    submissions: list[dict[str, object]] = []

    def core(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "POST":
            body = json.loads(request.content)
            submissions.append(body)
            return httpx.Response(202, json={**body, "job_id": JOB})
        owner = request.url.params["owner_id"]
        if request.url.params["offset"] == "0":
            return httpx.Response(
                200,
                json={
                    "jobs": [{"owner_id": owner, "kind": "prepare_kit"}] * 20,
                    "next_offset": 20,
                },
            )
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "owner_id": owner,
                        "kind": "recover",
                        "manifest_id": MANIFEST,
                        "job_id": JOB,
                    }
                ],
                "next_offset": None,
            },
        )

    client = client_for(core)
    app = LibraryServer(None, client, asyncio.get_running_loop())
    app.start()
    try:
        async with httpx.AsyncClient(trust_env=False) as browser:
            headers = {"X-Preservation-Action": "operator"}
            result = await browser.post(
                app.url + "api/mirror-recovery-latest",
                headers=headers,
                json={"manifest_id": MANIFEST},
            )
            assert result.status_code == 200 and result.json()["job"]["job_id"] == JOB
            assert methods == ["GET", "GET"]
            denied = await browser.post(
                app.url + "api/mirror-recover",
                json={"manifest_id": MANIFEST, "policy_id": POLICY},
            )
            assert denied.status_code == 400 and not submissions
            result = await browser.post(
                app.url + "api/mirror-recover",
                headers=headers,
                json={"manifest_id": MANIFEST, "policy_id": POLICY},
            )
            assert result.status_code == 200 and result.json()["job_id"] == JOB
            assert submissions[0]["kind"] == "recover"
    finally:
        await asyncio.to_thread(app.close)
        await client.close()


@pytest.mark.parametrize("missing", [None, "request", "job", "progress"])
async def test_pending_support_is_separate_from_retained_kit_support(
    missing: str | None,
) -> None:
    value = json.loads(
        (Path(__file__).parent / "fixtures/core-mirror-contract-v1.json").read_text()
    )
    schemas = value["components"]["schemas"]
    if missing in {"request", "job"}:
        schema = "MirrorRequest" if missing == "request" else "MirrorJob"
        schemas[schema]["properties"]["kind"]["enum"].remove("recover")
    elif missing == "progress":
        schemas["MirrorJob"]["properties"].pop("bytes_hashed")
    calls: list[str] = []

    def core(request: httpx.Request) -> httpx.Response:
        calls.append(request.method + " " + request.url.path)
        return httpx.Response(
            200,
            json={"jobs": []} if request.url.path == "/preservation/library" else value,
        )

    client = client_for(core)
    try:
        report = await check_core(client)
        assert report["Pending recovery"] is (missing is None)
        assert report["Retained recovery"] and report["Persistent mirrors"]
        assert calls == ["GET /preservation/library", "GET /api/openapi.json"]
    finally:
        await client.close()
