"""Mirror presentation keeps immutable decisions, explicit grants and host authority."""

import asyncio
import json

import httpx
import pytest
from pydantic import JsonValue

from foxlight_preservation_library.client import CoreClient, CoreRefused
from foxlight_preservation_library.contracts import Settings
from foxlight_preservation_library.server import LibraryServer


def configuration() -> dict[str, JsonValue]:
    return {
        "name": "My public mirror",
        "disk_bytes": 1024**3,
        "ingress_daily": 1024**3,
        "egress_daily": 1024**3,
        "egress_monthly": 1024**3,
    }


async def test_mirror_reads_are_offline_and_retries_keep_the_same_decision() -> None:
    calls: list[tuple[str, str, dict[str, JsonValue]]] = []

    def core(request: httpx.Request) -> httpx.Response:
        body: dict[str, JsonValue] = (
            json.loads(request.content) if request.content else {}
        )
        calls.append((request.method, str(request.url), body))
        return httpx.Response(202 if request.method == "POST" else 200, json={})

    client = CoreClient(Settings(), "client", transport=httpx.MockTransport(core))
    app = LibraryServer(None, client, asyncio.get_running_loop())
    try:
        assert (
            len(
                {
                    client.owner_id,
                    client.sharing_owner_id,
                    client.discovery_owner_id,
                    client.mirror_owner_id,
                }
            )
            == 4
        )
        await app.dispatch("mirror-read", {"offset": 20})
        await app.dispatch("mirror-history", {"offset": 20})
        assert all(method == "GET" for method, _, _ in calls)
        assert calls[0][1].endswith("/preservation/mirror?offset=20")
        assert f"owner_id={client.mirror_owner_id}" in calls[1][1]
        decision: dict[str, JsonValue] = {
            "operation_id": "same-operation",
            "kind": "configure",
            "configuration": configuration(),
        }
        await app.dispatch("mirror-start", decision)
        await app.dispatch("mirror-start", decision)
        assert calls[-1] == calls[-2]
        body = calls[-1][2]
        assert body["owner_id"] == client.mirror_owner_id
        assert body["operation_id"] == "same-operation"
        assert body["public_participation"] is False and body["confirm_resume"] is False
        configured = body["configuration"]
        assert isinstance(configured, dict)
        assert all(configured[k] is False for k in ("http", "ipfs", "torrent"))
    finally:
        await client.close()


@pytest.mark.parametrize(
    "changed",
    [
        None,
        "copy_id",
        "manifest_id",
        "schema_version",
        "companion",
        "terms",
        "duplicate",
        "size",
    ],
)
async def test_public_copy_review_is_read_only_and_bound_to_the_exact_selection(
    changed: str | None,
) -> None:
    calls: list[tuple[str, str]] = []
    members: list[JsonValue] = [
        {
            "member_id": member,
            "role": "primary" if member == "primary" else "assistant",
            "repository": "example/fixture",
            "revision": "b" * 40,
            "format": "test",
            "size_bytes": 1,
            "file_count": 1,
            "license_id": member + "-terms",
            "terms_reference": "LICENSE",
            "notice_paths": ["LICENSE"],
        }
        for member in ("primary", "companion")
    ]
    review: dict[str, JsonValue] = {
        "schema_version": "skulk.preservation-public-review.v1",
        "copy_id": "selected-copy",
        "manifest_id": "a" * 64,
        "archive_format": "preservation-v1",
        "name": "Synthetic public selection",
        "size_bytes": 2,
        "workloads": ["fixture"],
        "runtime_requirements": [],
        "members": members,
        "future_extension": {"opaque": [1, 2, 3]},
    }
    companion = members[1]
    assert isinstance(companion, dict)
    if changed == "companion":
        members.pop()
    elif changed == "terms":
        companion.pop("terms_reference")
    elif changed == "duplicate":
        companion["member_id"] = "primary"
    elif changed == "size":
        review["size_bytes"] = 5
    elif changed is not None:
        review[changed] = "changed"

    def core(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json=review)

    client = CoreClient(Settings(), "client", transport=httpx.MockTransport(core))
    app = LibraryServer(None, client, asyncio.get_running_loop())
    try:
        selection: dict[str, JsonValue] = {
            "copy_id": "selected-copy",
            "manifest_id": "a" * 64,
            "member_count": 2,
        }
        if changed is None:
            assert await app.dispatch("mirror-copy-review", selection) == review
        else:
            with pytest.raises(CoreRefused, match="public_copy_review_changed"):
                await app.dispatch("mirror-copy-review", selection)
        assert calls == [("GET", "/preservation/public-copies/selected-copy/review")]
    finally:
        await client.close()


@pytest.mark.parametrize(
    "kind", ["retain", "run", "grant_resume", "revoke_resume", "model", "prune"]
)
async def test_distinct_mirror_decisions_reach_core_without_extra_authority(
    kind: str,
) -> None:
    requests: list[dict[str, JsonValue]] = []

    def core(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(202, json={})

    client = CoreClient(Settings(), "client", transport=httpx.MockTransport(core))
    app = LibraryServer(None, client, asyncio.get_running_loop())
    body: dict[str, JsonValue] = {
        "operation_id": "chosen",
        "kind": kind,
        "policy_id": "a" * 64,
    }
    if kind == "retain":
        body.update(copy_id="b" * 64, manifest_id="c" * 64, confirm_redistribution=True)
    elif kind == "run":
        body.update(public_participation=True, seconds=60)
    elif kind == "grant_resume":
        body.update(confirm_resume=True, resume_expires=2000000000)
    elif kind == "model":
        body.update(manifest_id="c" * 64, action="block")
    elif kind == "prune":
        body.update(manifest_id="c" * 64, confirm_delete_manifest="c" * 64)
    try:
        await app.dispatch("mirror-start", body)
        assert len(requests) == 1
        assert requests[0]["owner_id"] == client.mirror_owner_id
        assert requests[0]["kind"] == kind
        assert requests[0]["public_participation"] is (kind == "run")
        assert requests[0]["confirm_resume"] is (kind == "grant_resume")
    finally:
        await client.close()


@pytest.mark.parametrize(
    "addition",
    [
        {"owner_id": "injected"},
        {"path": "/outside"},
        {"kind": "run"},
        {"kind": "prune", "manifest_id": "c" * 64},
        {"kind": "retain", "copy_id": "b" * 64, "manifest_id": "c" * 64},
        {"kind": "grant_resume", "resume_expires": 2000000000},
        {"kind": "revoke_resume", "public_participation": True},
        {
            "kind": "model",
            "manifest_id": "c" * 64,
            "action": "block",
            "confirm_delete_manifest": "c" * 64,
        },
        {"kind": "run", "public_participation": True, "configuration": configuration()},
        {"kind": "prune", "manifest_id": "c" * 64, "confirm_delete_manifest": "d" * 64},
        {
            "kind": "configure",
            "configuration": {**configuration(), "executable": "/outside"},
        },
        {"kind": "configure", "configuration": {**configuration(), "ipfs": True}},
    ],
)
async def test_invalid_mirror_decisions_never_reach_core(
    addition: dict[str, JsonValue],
) -> None:
    def forbidden(_request: httpx.Request) -> httpx.Response:
        pytest.fail("invalid mirror decision reached Core")

    client = CoreClient(Settings(), "client", transport=httpx.MockTransport(forbidden))
    app = LibraryServer(None, client, asyncio.get_running_loop())
    body: dict[str, JsonValue] = {
        "operation_id": "chosen",
        "kind": "run",
        "policy_id": "a" * 64,
    }
    body.update(addition)
    try:
        with pytest.raises(ValueError):
            await app.dispatch("mirror-start", body)
    finally:
        await client.close()


async def test_mirror_control_can_stop_current_core_restart_but_not_foreign_jobs() -> (
    None
):
    calls: list[tuple[str, str]] = []
    kind = "run"
    current: str | None = "b" * 64
    observed_id = "b" * 64

    def core(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/preservation/mirror":
            return httpx.Response(200, json={"controller_job_id": current})
        return httpx.Response(
            200, json={"job_id": observed_id, "owner_id": "core-restart", "kind": kind}
        )

    client = CoreClient(Settings(), "client", transport=httpx.MockTransport(core))
    app = LibraryServer(None, client, asyncio.get_running_loop())
    try:
        await app.dispatch("mirror-observe", {"job_id": "b" * 64})
        assert all(method == "GET" for method, _ in calls)
        await app.dispatch("mirror-stop", {"job_id": "b" * 64})
        assert calls[-1] == ("DELETE", "/preservation/mirror/jobs/" + "b" * 64)
        before = len(calls)
        current = "c" * 64
        with pytest.raises(CoreRefused, match="job_owner_changed"):
            await app.dispatch("mirror-stop", {"job_id": "b" * 64})
        assert all(method == "GET" for method, _ in calls[before:])
        current, kind = "b" * 64, "retain"
        with pytest.raises(CoreRefused, match="job_owner_changed"):
            await app.dispatch("mirror-stop", {"job_id": "b" * 64})
        observed_id = "c" * 64
        with pytest.raises(CoreRefused, match="invalid_job_identity"):
            await app.dispatch("mirror-observe", {"job_id": "b" * 64})
    finally:
        await client.close()


async def test_mirror_http_gate_and_old_core_upgrade_refusal() -> None:
    calls = 0

    def core(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    client = CoreClient(Settings(), "client", transport=httpx.MockTransport(core))
    app = LibraryServer(None, client, asyncio.get_running_loop())
    app.start()
    try:
        async with httpx.AsyncClient(trust_env=False) as browser:
            for action in (
                "mirror-copy-review",
                "mirror-read",
                "mirror-history",
                "mirror-start",
                "mirror-observe",
                "mirror-stop",
            ):
                result = await browser.post(
                    app.url + "api/" + action,
                    json={},
                    headers={
                        "Origin": "https://foreign.example",
                        "X-Preservation-Action": "operator",
                    },
                )
                assert result.status_code == 403
            assert calls == 0
            result = await browser.post(
                app.url + "api/mirror-read",
                json={},
                headers={"X-Preservation-Action": "operator"},
            )
            assert result.status_code == 409 and result.json() == {
                "error": "core_upgrade_required"
            }
    finally:
        await asyncio.to_thread(app.close)
        await client.close()


async def test_lost_start_response_does_not_automatically_resubmit() -> None:
    calls = 0

    def unavailable(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("lost response", request=request)

    client = CoreClient(
        Settings(), "client", transport=httpx.MockTransport(unavailable)
    )
    app = LibraryServer(None, client, asyncio.get_running_loop())
    try:
        with pytest.raises(CoreRefused, match="host_unavailable"):
            await app.dispatch(
                "mirror-start",
                {
                    "kind": "run",
                    "operation_id": "permanent",
                    "policy_id": "a" * 64,
                    "public_participation": True,
                },
            )
        assert calls == 1
    finally:
        await client.close()
