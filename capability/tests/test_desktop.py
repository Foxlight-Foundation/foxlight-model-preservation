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
