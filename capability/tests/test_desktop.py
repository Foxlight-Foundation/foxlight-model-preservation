"""Actual standalone local server; no Core socket, SDK or model execution."""

import asyncio

import httpx
import pytest

pytest.importorskip("foxlight_model_preservation")
from foxlight_preservation_library.desktop import DesktopClient
from foxlight_preservation_library.server import LibraryServer


@pytest.mark.parametrize(
    "error,expected",
    [
        (
            "complete_signed_companion_map_required",
            "complete_signed_companion_map_required",
        ),
        ("internal_failure_detail", "invalid_or_unsafe"),
    ],
)
async def test_companion_refusal_crosses_only_the_allowed_error_boundary(
    tmp_path,
    monkeypatch,
    error,
    expected,
):
    from foxlight_model_preservation.models import PreservationError

    from foxlight_preservation_library.client import CoreRefused

    client = DesktopClient(tmp_path / "owner")

    def refuse(*args):
        raise PreservationError(error)

    monkeypatch.setattr(client.owner, "control", refuse)
    try:
        with pytest.raises(CoreRefused, match=expected):
            await client.request("POST", "desktop-plan", {"kind": "receive"})
    finally:
        await client.close()


async def test_independent_owner_opens_empty_offline_and_keeps_ticket_boundary(
    tmp_path, monkeypatch
):
    client = DesktopClient(tmp_path / "owner")
    server = LibraryServer(None, client, asyncio.get_running_loop())
    server.start()
    try:
        async with httpx.AsyncClient(trust_env=False) as browser:
            response = await browser.get(server.url + "api/library?offset=0")
            assert response.status_code == 200
            assert response.json()["host_kind"] == "sovereign"
            assert response.json()["jobs"] == []
            path = server.url + "api/desktop-search"
            assert (await browser.post(path, json={})).status_code == 400
            response = await browser.post(
                path,
                json={"query": "model"},
                headers={"X-Preservation-Action": "operator"},
            )
            assert response.status_code == 200
            assert response.json()["total"] == 0
            for action, expected in (
                ("desktop-source-read", {"sources": []}),
                ("desktop-backup-read", {"destinations": []}),
                (
                    "desktop-sharing-read",
                    {"public_copies": [], "total": 0, "next_offset": None},
                ),
                (
                    "desktop-source-search",
                    {"results": [], "total": 0, "next_offset": None},
                ),
            ):
                path = server.url + "api/" + action
                assert (await browser.post(path, json={})).status_code == 400
                response = await browser.post(
                    path, json={}, headers={"X-Preservation-Action": "operator"}
                )
                assert response.status_code == 200
                assert response.json() == expected
                assert (
                    await browser.post(
                        path,
                        json={},
                        headers={
                            "X-Preservation-Action": "operator",
                            "Origin": "https://attacker.invalid",
                        },
                    )
                ).status_code == 403
            assert (
                await browser.get(
                    server.url + "api/library", headers={"Host": "attacker.invalid"}
                )
            ).status_code == 403
            assert (
                await browser.post(
                    path,
                    json={},
                    headers={
                        "Origin": "https://attacker.invalid",
                        "X-Preservation-Action": "operator",
                    },
                )
            ).status_code == 403
    finally:
        await asyncio.to_thread(server.close)
        await client.close()


async def test_interactive_folder_selection_can_take_longer_than_control_timeout(
    tmp_path, monkeypatch
):
    client = DesktopClient(tmp_path / "owner")

    async def delayed():
        await asyncio.sleep(13)
        return {"path": "/controlled/backup/Sovereign Backups"}

    monkeypatch.setattr(client, "_choose_folder", delayed)
    server = LibraryServer(None, client, asyncio.get_running_loop())
    server.start()
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=20) as browser:
            reply = await browser.post(
                server.url + "api/desktop-folder-choose",
                json={},
                headers={"X-Preservation-Action": "operator"},
            )
        assert reply.status_code == 200
        assert reply.json()["path"] == "/controlled/backup/Sovereign Backups"
    finally:
        await asyncio.to_thread(server.close)
        await client.close()


@pytest.mark.parametrize("stop", ["cancel_request", "close_application"])
async def test_cancelled_folder_picker_is_owned_and_terminated(
    tmp_path, monkeypatch, stop
):
    import sys

    import foxlight_preservation_library.desktop as desktop
    from foxlight_preservation_library.client import CoreRefused

    client = DesktopClient(tmp_path / "owner")
    original = asyncio.create_subprocess_exec
    started = asyncio.Event()
    children = []

    async def spawn(*args, **kwargs):
        process = await original(
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        children.append(process)
        started.set()
        return process

    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setattr(desktop.shutil, "which", lambda *a, **kw: "/usr/bin/zenity")
    monkeypatch.setattr(desktop.asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(client.request("POST", "desktop-folder-choose", {}))
    try:
        await asyncio.wait_for(started.wait(), 5)
        with pytest.raises(CoreRefused, match="folder_picker_busy"):
            await client.request("POST", "desktop-folder-choose", {})
        if stop == "cancel_request":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await client.close()
            await asyncio.gather(task, return_exceptions=True)
        assert children[0].returncode is not None
        assert client._picker is None
    finally:
        if stop != "close_application":
            await client.close()


async def test_public_review_metadata_is_separate_from_compact_controls(
    tmp_path, monkeypatch
):
    client = DesktopClient(tmp_path / "owner")
    document = b'{"synthetic_review":"' + b"x" * 80_000 + b'"}'
    identity = "a" * 64

    def selected(plan):
        assert plan == identity
        return document

    monkeypatch.setattr(client.owner, "publication_review", selected)
    server = LibraryServer(None, client, asyncio.get_running_loop())
    server.start()
    try:
        async with httpx.AsyncClient(trust_env=False) as browser:
            response = await browser.get(
                server.url + "api/desktop-public-review/" + identity
            )
            assert response.status_code == 200
            assert response.content == document
            assert (
                await browser.get(
                    server.url + "api/desktop-public-review/" + identity,
                    headers={"Origin": "https://attacker.invalid"},
                )
            ).status_code == 403
    finally:
        await asyncio.to_thread(server.close)
        await client.close()
