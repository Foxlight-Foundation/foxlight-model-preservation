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


@pytest.mark.parametrize(
    ("action", "method", "suffix"),
    [
        ("work-preview", "GET", "temporary-files"),
        ("reclaim-work", "POST", "reclaim-work"),
    ],
)
async def test_sharing_cleanup_checks_custody_before_read_or_clear(
    action: str, method: str, suffix: str
) -> None:
    seen: list[str] = []
    owner = "unrelated"

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
            await server.dispatch(action, {"job_id": "a" * 64})
        assert len(seen) == 1
        owner = client.sharing_owner_id
        async with httpx.AsyncClient(trust_env=False) as browser:
            response = await browser.post(
                server.url + "api/" + action,
                json={"job_id": "a" * 64},
                headers={"X-Preservation-Action": "operator"},
            )
            assert response.status_code == 200
        assert seen[-1] == method + " /preservation/jobs/" + "a" * 64 + "/" + suffix
        assert not any(path.endswith("/jobs") for path in seen)
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


async def test_screen_serves_exact_local_assets_with_explicit_font_types() -> None:
    """Every built file is allowlisted, packaged, bounded and served locally."""
    import re
    import tomllib
    from importlib.resources import files
    from importlib.resources.abc import Traversable
    from pathlib import Path

    from foxlight_preservation_library.server import STATIC_ASSET_LIMIT, STATIC_ASSETS

    static = files("foxlight_preservation_library").joinpath("static")
    built: set[str] = set()

    def walk(node: Traversable, prefix: str = "") -> None:
        for child in node.iterdir():
            if child.name.startswith("."):
                continue
            if child.is_dir():
                walk(child, prefix + child.name + "/")
            else:
                built.add(prefix + child.name)

    walk(static)
    assert built == set(STATIC_ASSETS)
    manifest = tomllib.loads(
        (Path(__file__).parents[1] / "capability.toml").read_text(encoding="utf-8")
    )
    assert set(manifest["data_files"]) == {
        "foxlight_preservation_library/static/" + name for name in STATIC_ASSETS
    }
    css = static.joinpath("assets/index.css").read_text(encoding="utf-8")
    page = static.joinpath("index.html").read_text(encoding="utf-8")
    referenced = {
        "assets/" + item.strip("'\"").removeprefix("./")
        for item in re.findall(r"url\(([^)]+)\)", css)
    } | {
        item.removeprefix("./")
        for item in re.findall(r'(?:src|href)="(\./[^"]+)"', page)
    }
    assert {name for name in STATIC_ASSETS if name.endswith(".woff2")} <= referenced
    assert referenced <= set(STATIC_ASSETS)
    assert "@import" not in css and "//" not in "".join(
        re.findall(r"url\(([^)]+)\)", css)
    )

    client = CoreClient(
        Settings(),
        "library-local",
        transport=httpx.MockTransport(lambda request: httpx.Response(404)),
    )
    server = LibraryServer(None, client, asyncio.get_running_loop())
    server.start()
    try:
        async with httpx.AsyncClient(trust_env=False) as browser:
            for name, declared in STATIC_ASSETS.items():
                reply = await browser.get(server.url + name)
                assert reply.status_code == 200, name
                assert 0 < len(reply.content) <= STATIC_ASSET_LIMIT
                assert "default-src 'self'" in reply.headers["content-security-policy"]
                if declared is not None:
                    assert reply.headers["content-type"] == declared
            for font in [name for name in STATIC_ASSETS if name.endswith(".woff2")]:
                reply = await browser.get(server.url + font)
                assert reply.headers["content-type"] == "font/woff2"
                assert reply.content[:4] == b"wOF2"
            assert (
                await browser.get(server.url + "assets/other.woff2")
            ).status_code == 404
            assert (await browser.get(server.url + "assets/")).status_code == 404
    finally:
        await asyncio.to_thread(server.close)
        await client.close()
