"""Exact SDK contract translation without a private SDK owner or Skulk import."""

from __future__ import annotations

import json

import httpx
import pytest
from skulk_capability_sdk.operations import (
    OperationCall,
    OperationPlan,
    OperationStatus,
)

from foxlight_preservation_library.client import CoreClient, CoreRefused
from foxlight_preservation_library.contracts import (
    Settings,
    build_manifest,
    validate_manifest,
)


def test_manifest_and_loopback_control_boundary() -> None:
    manifest = build_manifest(executable="/bin/known", executable_sha256="a" * 64)
    validate_manifest(manifest)
    assert manifest.effect == "governed"
    assert len(manifest.descriptors) == 2
    with pytest.raises(ValueError):
        Settings(skulk_api_url="https://remote.example:443")
    with pytest.raises(ValueError):
        Settings(skulk_api_url="http://user:secret@127.0.0.1:52415")


async def test_sdk_review_start_status_and_immutable_binding() -> None:
    starts: list[dict[str, object]] = []
    plan = {
        "plan_id": "a" * 64,
        "kind": "preserve",
        "model_id": "example/base",
        "location_id": "device",
        "generation_id": "b" * 64,
        "copy_id": None,
        "member_count": 2,
        "size_bytes": 123,
        "expires_at": 9999999999,
    }
    job = {
        "job_id": "c" * 64,
        "operation_id": "sdk.operation:1",
        "owner_id": "owner",
        "plan_id": plan["plan_id"],
        "kind": "preserve",
        "model_id": "example/base",
        "state": "running",
        "stage": "copying",
        "created_at": 1,
        "updated_at": 2,
        "finished_at": None,
        "result": None,
        "error_code": None,
    }

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/preservation/plans"):
            return httpx.Response(200, json=plan)
        if request.method == "POST" and request.url.path == "/preservation/jobs":
            starts.append(json.loads(request.content))
        return httpx.Response(202 if request.method == "POST" else 200, json=job)

    client = CoreClient(Settings(), "owner", transport=httpx.MockTransport(answer))
    selection = {
        "kind": "preserve",
        "location_id": "device",
        "model_id": "example/base",
        "generation_id": "b" * 64,
    }
    try:
        reviewed = await client.operation(OperationCall(op="plan", input=selection))
        assert isinstance(reviewed, OperationPlan)
        started = await client.operation(
            OperationCall(
                op="start", operation_id="sdk.operation:1", input=reviewed.input
            )
        )
        assert isinstance(started, OperationStatus) and started.state == "running"
        assert starts == [
            {
                "plan_id": "a" * 64,
                "operation_id": "sdk.operation:1",
                "owner_id": "owner",
            }
        ]
        assert reviewed.input is not None
        changed = {**reviewed.input, "generation_id": "d" * 64}
        with pytest.raises(CoreRefused, match="reviewed_selection_changed"):
            await client.operation(
                OperationCall(op="start", operation_id="sdk.operation:1", input=changed)
            )
        assert len(starts) == 1
    finally:
        await client.close()


async def test_http_timeout_is_safe_and_never_retries_submission() -> None:
    calls = 0

    def unavailable(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("private implementation detail", request=request)

    client = CoreClient(Settings(), "owner", transport=httpx.MockTransport(unavailable))
    try:
        with pytest.raises(CoreRefused, match="^host_unavailable$"):
            await client.request("POST", "jobs", {"plan_id": "a" * 64})
        assert calls == 1
    finally:
        await client.close()


async def test_ui_activity_and_stop_are_bound_to_the_capability_owner() -> None:
    import asyncio

    from skulk_capability_sdk.contracts import Startup

    from foxlight_preservation_library.contracts import LibraryCall
    from foxlight_preservation_library.server import LibraryServer

    owned = {"job_id": "a" * 64, "owner_id": "owner", "state": "running"}
    foreign = {"job_id": "b" * 64, "owner_id": "another", "state": "running"}
    effects: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("library") or path.endswith("setup/device"):
            return httpx.Response(200, json={"jobs": [owned, foreign]})
        job = foreign if foreign["job_id"] in path else owned
        if request.method == "POST":
            effects.append(path)
        return httpx.Response(200, json=job)

    client = CoreClient(Settings(), "owner", transport=httpx.MockTransport(answer))
    startup = Startup(
        socket="/tmp/unused-test-socket",
        token="f" * 64,
        node_id="test",
        host_id="host",
        transport_id="transport",
        manifest=build_manifest(executable="/bin/known", executable_sha256="a" * 64),
    )
    screen = LibraryServer(startup, client, asyncio.get_running_loop())
    try:
        for call in (LibraryCall(), LibraryCall(action="setup_device")):
            report = await client.library(call)
            assert report.report["jobs"] == [owned]
        with pytest.raises(CoreRefused, match="job_owner_changed"):
            await screen.dispatch("cancel", {"job_id": foreign["job_id"]})
        assert effects == []
        assert await screen.dispatch("cancel", {"job_id": owned["job_id"]}) == owned
        assert effects == ["/preservation/jobs/" + owned["job_id"] + "/cancel"]
    finally:
        await client.close()
