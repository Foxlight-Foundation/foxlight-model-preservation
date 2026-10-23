"""Discovery bridge retains Core authority, owner isolation and explicit grants."""

import asyncio
import json

import httpx
import pytest
from pydantic import JsonValue

from foxlight_preservation_library.client import CoreClient, CoreRefused
from foxlight_preservation_library.contracts import Settings
from foxlight_preservation_library.server import LibraryServer


async def test_discovery_routes_keep_separate_owner_and_network_policies() -> None:
    calls: list[tuple[str, str, object]] = []
    observed_owner = "wrong"

    def core(request: httpx.Request) -> httpx.Response:
        calls.append(
            (
                request.method,
                str(request.url),
                json.loads(request.content) if request.content else {},
            )
        )
        return httpx.Response(200, json={"owner_id": observed_owner})

    client = CoreClient(Settings(), "client", transport=httpx.MockTransport(core))
    server = LibraryServer(None, client, asyncio.get_running_loop())
    try:
        assert (
            len({client.owner_id, client.sharing_owner_id, client.discovery_owner_id})
            == 3
        )
        await server.dispatch("discovery-read", {"offset": 4})
        assert (
            calls[-1][0] == "GET"
            and "owner_id=" + client.discovery_owner_id in calls[-1][1]
        )
        await server.dispatch(
            "discovery-search", {"query": "saved model", "supplier_offset": 4}
        )
        assert calls[-1][2] == {
            "query": "saved model",
            "offset": 0,
            "supplier_offset": 4,
        }
        await server.dispatch("source-review", {"upload_id": "a" * 32})
        await server.dispatch(
            "source-follow",
            {"review_id": "b" * 64, "confirmed": True, "allow_http": True},
        )
        assert calls[-1][2] == {
            "review_id": "b" * 64,
            "confirmed": True,
            "allow_http": True,
            "allow_private": False,
        }
        await server.dispatch(
            "discovery-start",
            {
                "kind": "model_metadata",
                "operation_id": "chosen",
                "source_id": "c" * 64,
                "entry_id": "saved",
                "manifest_id": "d" * 64,
                "accept_source_keys": True,
            },
        )
        body = calls[-1][2]
        assert isinstance(body, dict)
        assert (
            body["owner_id"] == client.discovery_owner_id
            and body["allow_http"] is False
            and body["allow_private"] is False
        )
        assert isinstance(body, dict) and not {
            "transport",
            "allow_peer_network",
            "peer_setup_id",
            "historical",
        }.intersection(body)
        before = len(calls)
        with pytest.raises(CoreRefused, match="job_owner_changed"):
            await server.dispatch("discovery-cancel", {"job_id": "e" * 64})
        assert len(calls) == before + 1 and calls[-1][0] == "GET"
        observed_owner = client.discovery_owner_id
        await server.dispatch("discovery-cancel", {"job_id": "e" * 64})
        assert calls[-1][0] == "POST" and calls[-1][1].endswith("/cancel")
        await server.dispatch("source-remove", {"source_id": "c" * 64})
        assert calls[-1][0] == "DELETE"
    finally:
        await client.close()


@pytest.mark.parametrize(
    "action,body",
    [
        (
            "discovery-start",
            {
                "kind": "refresh",
                "operation_id": "chosen",
                "source_id": "c" * 64,
                "owner_id": "injected",
            },
        ),
        (
            "discovery-start",
            {
                "kind": "refresh",
                "operation_id": "chosen",
                "source_id": "c" * 64,
                "cache_path": "/injected",
            },
        ),
        ("source-follow", {"review_id": "b" * 64, "confirmed": True, "model_keys": []}),
        ("source-review", {"upload_id": "../outside"}),
        ("source-remove", {"source_id": "https://unreviewed.example/"}),
        ("discovery-search", {"query": "x" * 501}),
        ("discovery-search", {"offset": 1001}),
        ("discovery-read", {"offset": -1}),
        ("discovery-cancel", {"job_id": "e" * 64, "owner_id": "injected"}),
    ],
)
async def test_invalid_discovery_input_never_reaches_core(
    action: str, body: dict[str, JsonValue]
) -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        pytest.fail("invalid discovery input reached Core")

    client = CoreClient(Settings(), "client", transport=httpx.MockTransport(forbidden))
    server = LibraryServer(None, client, asyncio.get_running_loop())
    try:
        with pytest.raises(ValueError):
            await server.dispatch(action, body)
    finally:
        await client.close()


async def test_discovery_http_origin_gate_and_missing_core_upgrade() -> None:
    calls = 0

    def core(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    client = CoreClient(Settings(), "client", transport=httpx.MockTransport(core))
    server = LibraryServer(None, client, asyncio.get_running_loop())
    server.start()
    try:
        async with httpx.AsyncClient(trust_env=False) as browser:
            for action in (
                "discovery-read",
                "discovery-search",
                "discovery-start",
                "discovery-observe",
                "discovery-cancel",
                "source-review",
                "source-follow",
                "source-remove",
            ):
                response = await browser.post(
                    server.url + "api/" + action,
                    json={},
                    headers={
                        "Origin": "https://untrusted.example",
                        "X-Preservation-Action": "operator",
                    },
                )
                assert response.status_code == 403
            assert calls == 0
            response = await browser.post(
                server.url + "api/discovery-read",
                json={},
                headers={"X-Preservation-Action": "operator"},
            )
            assert response.status_code == 409 and response.json() == {
                "error": "core_upgrade_required"
            }
    finally:
        await asyncio.to_thread(server.close)
        await client.close()


async def test_peer_controls_keep_settings_and_per_operation_consent_separate() -> None:
    calls: list[tuple[str, str, object]] = []

    def core(request: httpx.Request) -> httpx.Response:
        calls.append(
            (
                request.method,
                request.url.path,
                json.loads(request.content) if request.content else {},
            )
        )
        return httpx.Response(200, json={})

    client = CoreClient(Settings(), "client", transport=httpx.MockTransport(core))
    server = LibraryServer(None, client, asyncio.get_running_loop())
    try:
        await server.dispatch("peer-read", {})
        assert calls == [("GET", "/preservation/discovery/peer", {})]
        await server.dispatch(
            "peer-setup",
            {
                "bootstrap": ["/ip4/127.0.0.1/tcp/4001/p2p/example"],
                "allow_private": True,
                "confirmed": True,
            },
        )
        assert calls[-1][0] == "POST" and calls[-1][2] == {
            "bootstrap": ["/ip4/127.0.0.1/tcp/4001/p2p/example"],
            "allow_private": True,
            "confirmed": True,
        }
        await server.dispatch(
            "discovery-start",
            {
                "kind": "refresh",
                "operation_id": "peer",
                "source_id": "a" * 64,
                "transport": "peer",
                "allow_peer_network": True,
                "peer_setup_id": "b" * 64,
                "historical": True,
            },
        )
        assert (
            isinstance(calls[-1][2], dict)
            and calls[-1][2]["owner_id"] == client.discovery_owner_id
        )
        assert calls[-1][2]["historical"] is True
        await server.dispatch(
            "delivery-plan",
            {
                "kind": "receive",
                "location_id": "invitation",
                "resume_job_id": "c" * 64,
                "allow_peer_network": True,
                "peer_setup_id": "b" * 64,
            },
        )
        assert calls[-1][2] == {
            "kind": "receive",
            "location_id": "invitation",
            "resume_job_id": "c" * 64,
            "allow_peer_network": True,
            "peer_setup_id": "b" * 64,
        }
        before = len(calls)
        for action, body in (
            ("peer-read", {"binary": "/tmp/program"}),
            ("peer-setup", {"bootstrap": [], "confirmed": True}),
            (
                "peer-setup",
                {
                    "bootstrap": ["contact"],
                    "confirmed": True,
                    "repository": "/tmp/cache",
                },
            ),
        ):
            with pytest.raises(ValueError):
                await server.dispatch(action, body)
        assert len(calls) == before
    finally:
        await client.close()
