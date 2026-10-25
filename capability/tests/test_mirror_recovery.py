"""Retained recovery survives client loss without replaying sharing or deletion."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from foxlight_preservation_library.client import CoreClient, CoreRefused
from foxlight_preservation_library.contracts import Settings
from foxlight_preservation_library.mirrors import prepared_mirror_kit
from foxlight_preservation_library.server import LibraryServer

MANIFEST, POLICY, JOB = "a" * 64, "b" * 64, "c" * 64


def client_for(handler: Callable[[httpx.Request], httpx.Response]) -> CoreClient:
    return CoreClient(
        Settings(), "stable-client", transport=httpx.MockTransport(handler)
    )


async def test_prepare_survives_lost_response_and_new_client_without_new_effects() -> (
    None
):
    submissions: list[dict[str, object]] = []
    lost = True

    def core(request: httpx.Request) -> httpx.Response:
        nonlocal lost
        body = json.loads(request.content)
        submissions.append(body)
        if lost:
            lost = False
            raise httpx.ReadError("reply lost", request=request)
        return httpx.Response(202, json={**body, "job_id": JOB, "state": "running"})

    for attempt in range(3):
        client = client_for(core)
        app = LibraryServer(None, client, asyncio.get_running_loop())
        try:
            if attempt == 0:
                with pytest.raises(CoreRefused, match="host_unavailable"):
                    await app.dispatch(
                        "mirror-prepare", {"manifest_id": MANIFEST, "policy_id": POLICY}
                    )
            else:
                receipt = await app.dispatch(
                    "mirror-prepare", {"manifest_id": MANIFEST, "policy_id": POLICY}
                )
                assert receipt["job_id"] == JOB
        finally:
            await client.close()
    assert submissions[0] == submissions[1] == submissions[2]
    assert submissions[0]["kind"] == "prepare_kit"
    assert submissions[0]["public_participation"] is False
    assert submissions[0]["confirm_resume"] is False
    assert submissions[0]["confirm_delete_manifest"] is None


@pytest.mark.parametrize("changed", [None, "owner_id", "manifest_id", "kind", "state"])
async def test_another_kit_requires_exact_own_terminal_receipt(
    changed: str | None,
) -> None:
    previous = {
        "job_id": JOB,
        "owner_id": "",
        "manifest_id": MANIFEST,
        "kind": "prepare_kit",
        "state": "succeeded",
    }
    starts: list[dict[str, object]] = []

    def core(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=previous)
        body = json.loads(request.content)
        starts.append(body)
        return httpx.Response(202, json={**body, "job_id": "d" * 64})

    client = client_for(core)
    previous["owner_id"] = client.mirror_owner_id
    if changed is not None:
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
                await app.dispatch("mirror-prepare", decision)
            assert not starts
        else:
            await app.dispatch("mirror-prepare", decision)
            await app.dispatch("mirror-prepare", decision)
            await app.dispatch(
                "mirror-prepare", {"manifest_id": MANIFEST, "policy_id": POLICY}
            )
            assert starts[0] == starts[1]
            assert starts[0]["operation_id"] != starts[2]["operation_id"]
    finally:
        await client.close()


async def test_latest_preparation_scans_past_first_page_without_effects() -> None:
    calls: list[str] = []

    def core(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        calls.append(str(request.url))
        owner = request.url.params["owner_id"]
        if request.url.params["offset"] == "0":
            return httpx.Response(
                200,
                json={
                    "jobs": [{"owner_id": owner, "kind": "model"}] * 20,
                    "next_offset": 20,
                },
            )
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "owner_id": owner,
                        "kind": "prepare_kit",
                        "manifest_id": MANIFEST,
                        "job_id": JOB,
                    }
                ],
                "next_offset": None,
            },
        )

    client = client_for(core)
    app = LibraryServer(None, client, asyncio.get_running_loop())
    try:
        result = await app.dispatch("mirror-kit-latest", {"manifest_id": MANIFEST})
        assert result["job"]["job_id"] == JOB
        assert len(calls) == 2 and calls[-1].endswith("offset=20")
    finally:
        await client.close()


@pytest.mark.parametrize("following", [0, -1, True, 4097, "20"])
async def test_latest_refuses_unreliable_paging(following: object) -> None:
    client = client_for(
        lambda _: httpx.Response(200, json={"jobs": [], "next_offset": following})
    )
    app = LibraryServer(None, client, asyncio.get_running_loop())
    try:
        with pytest.raises(CoreRefused, match="invalid_host_response"):
            await app.dispatch("mirror-kit-latest", {"manifest_id": MANIFEST})
    finally:
        await client.close()


async def test_review_preserves_unknown_fields_and_long_companion_role() -> None:
    member = {
        "member_id": "primary",
        "role": "future" * 15,
        "repository": "fixture/model",
        "revision": "d" * 40,
        "format": "fixture",
        "size_bytes": 20,
        "file_count": 2,
        "license_id": "MIT",
        "terms_reference": "LICENSE",
        "notice_paths": ["LICENSE"],
        "future": {"opaque": True},
    }
    review = {
        "schema_version": "skulk.preservation-mirror-review.v1",
        "manifest_id": MANIFEST,
        "archive_format": "preservation-v1",
        "name": "Still here",
        "size_bytes": 20,
        "workloads": [],
        "runtime_requirements": [],
        "members": [member],
        "capture_key_ids": ["e" * 64],
        "capture_threshold": 1,
        "future_card": {"preserve": [1, 2]},
    }
    paths: list[str] = []

    def core(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        paths.append(request.url.path)
        return httpx.Response(200, json=review)

    client = client_for(core)
    app = LibraryServer(None, client, asyncio.get_running_loop())
    try:
        assert (
            await app.dispatch(
                "mirror-retained-review", {"manifest_id": MANIFEST, "member_count": 1}
            )
            == review
        )
        review["capture_threshold"] = 2
        with pytest.raises(CoreRefused, match="mirror_selection_changed"):
            await app.dispatch("mirror-retained-review", {"manifest_id": MANIFEST})
        assert paths == [f"/preservation/mirror/models/{MANIFEST}/review"] * 2
    finally:
        await client.close()


async def test_exact_selection_refuses_unavailable_as_absent_and_wrong_identity() -> (
    None
):
    response = {
        "schema_version": "skulk.preservation-mirror-selection.v1",
        "manifest_id": MANIFEST,
        "configured": True,
        "available": False,
        "policy_id": POLICY,
        "model": None,
    }
    client = client_for(lambda _: httpx.Response(200, json=response))
    app = LibraryServer(None, client, asyncio.get_running_loop())
    try:
        assert (
            await app.dispatch("mirror-selection", {"manifest_id": MANIFEST})
            == response
        )
        response["manifest_id"] = "d" * 64
        with pytest.raises(CoreRefused, match="mirror_selection_changed"):
            await app.dispatch("mirror-selection", {"manifest_id": MANIFEST})
    finally:
        await client.close()


async def test_cleanup_confirms_only_the_exact_prepared_kit() -> None:
    calls: list[tuple[str, str]] = []

    def core(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return httpx.Response(
            200,
            json={
                "job_id": JOB,
                "owner_id": client.mirror_owner_id,
                "kind": "prepare_kit",
                "state": "succeeded",
                "manifest_id": MANIFEST,
            },
        )

    client = client_for(core)
    app = LibraryServer(None, client, asyncio.get_running_loop())
    try:
        with pytest.raises(ValueError):
            await app.dispatch(
                "mirror-kit-remove", {"job_id": JOB, "confirm_job_id": MANIFEST}
            )
        assert not calls
        await app.dispatch("mirror-kit-remove", {"job_id": JOB, "confirm_job_id": JOB})
        assert calls == [
            ("GET", f"/preservation/mirror/jobs/{JOB}"),
            ("DELETE", f"/preservation/mirror/kits/{JOB}"),
        ]
    finally:
        await client.close()


@pytest.mark.parametrize("changed", ["owner_id", "state", "kind", "kit_removed", "kit"])
async def test_only_owned_sealed_preparations_can_be_downloaded(changed: str) -> None:
    job: dict[str, object] = {
        "job_id": JOB,
        "owner_id": "",
        "kind": "prepare_kit",
        "state": "succeeded",
        "manifest_id": MANIFEST,
        "kit_removed": False,
        "kit": {
            "state": "prepared",
            "manifest_id": MANIFEST,
            "size_bytes": 20,
            "sha256": "e" * 64,
        },
    }
    client = client_for(lambda _: httpx.Response(200, json=job))
    job["owner_id"] = client.mirror_owner_id
    job[changed] = True if changed == "kit_removed" else "other"
    try:
        with pytest.raises(CoreRefused):
            await prepared_mirror_kit(client, JOB)
    finally:
        await client.close()


@pytest.mark.parametrize(
    "method,range_value,if_range,status,content_range,expected",
    [
        ("GET", None, None, 200, None, b"0123456789"),
        ("GET", "bytes=2-4", None, 206, "bytes 2-4/10", b"234"),
        ("GET", "bytes=-3", None, 206, "bytes 7-9/10", b"789"),
        ("GET", "bytes=2-", "matching", 206, "bytes 2-9/10", b"23456789"),
        ("GET", "bytes=2-4", "old", 200, None, b"0123456789"),
        ("HEAD", "bytes=2-4", None, 200, None, b""),
        ("GET", "bytes=99-", None, 416, "bytes */10", b""),
    ],
)
async def test_private_mirror_stream_preserves_ranges_and_seal(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    range_value: str | None,
    if_range: str | None,
    status: int,
    content_range: str | None,
    expected: bytes,
) -> None:
    etag = '"sha256-' + "e" * 64 + '"'
    job = {
        "job_id": JOB,
        "owner_id": "",
        "kind": "prepare_kit",
        "state": "succeeded",
        "manifest_id": MANIFEST,
        "kit_removed": False,
        "kit": {
            "state": "prepared",
            "manifest_id": MANIFEST,
            "size_bytes": 10,
            "sha256": "e" * 64,
        },
    }
    client = client_for(lambda _: httpx.Response(200, json=job))
    job["owner_id"] = client.mirror_owner_id
    transferred: list[httpx.Request] = []

    def artifact(request: httpx.Request) -> httpx.Response:
        transferred.append(request)
        assert request.url.path == f"/preservation/mirror/kits/{JOB}"
        assert request.headers["Accept-Encoding"] == "identity"
        headers = {
            "Content-Length": "10" if method == "HEAD" else str(len(expected)),
            "ETag": etag,
        }
        if content_range:
            headers["Content-Range"] = content_range
        return httpx.Response(
            status, headers=headers, stream=httpx.ByteStream(expected)
        )

    original = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(artifact)),
    )
    app = LibraryServer(None, client, asyncio.get_running_loop())
    app.start()
    try:
        headers = {}
        if range_value:
            headers["Range"] = range_value
        if if_range:
            headers["If-Range"] = etag if if_range == "matching" else if_range
        async with httpx.AsyncClient(trust_env=False) as browser:
            endpoint = app.url + f"api/mirror-kit/{JOB}"
            reply = await browser.request(method, endpoint, headers=headers)
            assert reply.status_code == status and reply.content == expected
            assert reply.headers.get("Content-Range") == content_range
            assert reply.headers["Cache-Control"] == "private, no-store"
            if status != 416:
                assert reply.headers["ETag"] == etag
                assert reply.headers["Accept-Ranges"] == "bytes"
            assert len(transferred) == 1
            if method == "HEAD":
                assert "Range" not in transferred[0].headers
            elif range_value:
                assert transferred[0].headers["Range"] == range_value
            if if_range:
                assert transferred[0].headers["If-Range"] == headers["If-Range"]
            reply = await browser.get(
                endpoint, headers={"Origin": "https://untrusted.example"}
            )
            assert reply.status_code == 403 and len(transferred) == 1
            reply = await browser.get(
                endpoint.replace(app.prefix, "/s/" + "0" * 64 + "/")
            )
            assert reply.status_code == 403 and len(transferred) == 1
    finally:
        await asyncio.to_thread(app.close)
        await client.close()


@pytest.mark.parametrize(
    "changed", ["etag", "length", "range", "ignored", "owner", "unsealed"]
)
async def test_bad_or_unowned_kit_is_refused_before_artifact_headers(
    monkeypatch: pytest.MonkeyPatch,
    changed: str,
) -> None:
    etag = '"sha256-' + "e" * 64 + '"'
    job = {
        "job_id": JOB,
        "owner_id": "",
        "kind": "prepare_kit",
        "state": "succeeded",
        "manifest_id": MANIFEST,
        "kit_removed": False,
        "kit": {
            "state": "prepared",
            "manifest_id": MANIFEST,
            "size_bytes": 10,
            "sha256": "e" * 64,
        },
    }
    client = client_for(lambda _: httpx.Response(200, json=job))
    job["owner_id"] = "other" if changed == "owner" else client.mirror_owner_id
    if changed == "unsealed":
        job["state"] = "running"
    transfers = 0

    def artifact(_: httpx.Request) -> httpx.Response:
        nonlocal transfers
        transfers += 1
        headers = {"Content-Length": "3", "ETag": etag, "Content-Range": "bytes 2-4/10"}
        if changed == "etag":
            headers["ETag"] = '"different"'
        elif changed == "length":
            headers["Content-Length"] = "4"
        elif changed == "range":
            headers["Content-Range"] = "bytes 3-5/10"
        elif changed == "ignored":
            headers = {"Content-Length": "10", "ETag": etag}
        return httpx.Response(
            200 if changed == "ignored" else 206,
            headers=headers,
            stream=httpx.ByteStream(b"bad"),
        )

    original = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(artifact)),
    )
    app = LibraryServer(None, client, asyncio.get_running_loop())
    app.start()
    try:
        async with httpx.AsyncClient(trust_env=False) as browser:
            reply = await browser.get(
                app.url + f"api/mirror-kit/{JOB}", headers={"Range": "bytes=2-4"}
            )
            assert reply.status_code == 409
            assert reply.json() == {"error": "kit_download_refused"}
            assert "Content-Disposition" not in reply.headers
            assert transfers == (0 if changed in {"owner", "unsealed"} else 1)
    finally:
        await asyncio.to_thread(app.close)
        await client.close()
