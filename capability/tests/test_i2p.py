"""I2P controls preserve source/model separation and refuse direct/peer mixing."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pydantic import JsonValue

from foxlight_preservation_library.client import CoreClient
from foxlight_preservation_library.compatibility import GROUPS, check_core
from foxlight_preservation_library.contracts import Settings
from foxlight_preservation_library.server import LibraryServer


async def test_i2p_follow_metadata_and_resume_forward_exact_separate_grants() -> None:
    calls: list[dict[str, JsonValue]] = []

    def core(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={})

    client = CoreClient(Settings(), "local", transport=httpx.MockTransport(core))
    server = LibraryServer(None, client, asyncio.get_running_loop())
    proxy = {"host": "::1", "port": 4445}
    try:
        await server.dispatch(
            "source-follow",
            {
                "review_id": "a" * 64,
                "confirmed": True,
                "allow_i2p": True,
                "i2p_proxy": proxy,
                "i2p_endpoints": ["http://" + "a" * 52 + ".b32.i2p"],
            },
        )
        assert calls[-1]["i2p_proxy"] == proxy
        assert calls[-1]["allow_i2p"] is True
        assert calls[-1]["allow_http"] is calls[-1]["allow_private"] is False
        assert "accepted_key_ids" not in calls[-1]
        await server.dispatch(
            "discovery-start",
            {
                "operation_id": "metadata",
                "kind": "model_metadata",
                "source_id": "b" * 64,
                "entry_id": "selected",
                "manifest_id": "c" * 64,
                "transport": "i2p",
                "allow_i2p": True,
                "i2p_policy_id": "d" * 64,
                "accept_source_keys": True,
            },
        )
        assert calls[-1]["owner_id"] == client.discovery_owner_id
        assert calls[-1]["i2p_policy_id"] == "d" * 64
        assert "i2p_proxy" not in calls[-1]  # Resolved by Core, never replaced here.
        await server.dispatch(
            "delivery-plan",
            {
                "kind": "receive",
                "location_id": "invitation",
                "resume_job_id": "e" * 64,
                "allow_i2p": True,
                "i2p_proxy": proxy,
            },
        )
        assert calls[-1] == {
            "kind": "receive",
            "location_id": "invitation",
            "resume_job_id": "e" * 64,
            "allow_i2p": True,
            "i2p_proxy": proxy,
        }
        assert not {"download_limit_bytes", "allow_repository_code"} & calls[-1].keys()
    finally:
        await client.close()


@pytest.mark.parametrize(
    "action,body",
    [
        ("source-follow", {"review_id": "a" * 64, "confirmed": True}),
        (
            "discovery-start",
            {
                "operation_id": "refresh",
                "kind": "refresh",
                "source_id": "a" * 64,
                "transport": "i2p",
                "i2p_policy_id": "b" * 64,
            },
        ),
        ("delivery-plan", {"kind": "receive", "location_id": "invitation"}),
    ],
)
@pytest.mark.parametrize("invalid", ["http", "peer", "named-proxy", "missing-grant"])
async def test_i2p_invalid_grants_refuse_before_core(
    action: str, body: dict[str, JsonValue], invalid: str
) -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        pytest.fail("Invalid I2P grant reached Core")

    selection = {**body, "allow_i2p": True}
    if action != "discovery-start":
        selection["i2p_proxy"] = {"host": "127.0.0.1", "port": 4444}
    if invalid == "http":
        selection["allow_http"] = True
    elif invalid == "peer":
        selection["allow_peer_network"] = True
    elif invalid == "named-proxy":
        selection["i2p_proxy"] = {"host": "localhost", "port": 4444}
    else:
        selection["allow_i2p"] = False
    client = CoreClient(Settings(), "local", transport=httpx.MockTransport(forbidden))
    server = LibraryServer(None, client, asyncio.get_running_loop())
    try:
        with pytest.raises(ValueError):
            await server.dispatch(action, selection)
    finally:
        await client.close()


async def test_i2p_feature_requires_actual_retained_policy_schema() -> None:
    paths: dict[str, dict[str, object]] = {}
    for pairs in GROUPS.values():
        for path, method in pairs:
            paths.setdefault(path, {})[method] = {}
    fields = {
        "SourceConfirmation": {"allow_i2p", "i2p_proxy", "i2p_endpoints"},
        "DiscoveryRequest": {"allow_i2p", "i2p_policy_id"},
        "ReceivingPolicy": {"allow_i2p", "i2p_proxy"},
        "InvitationPreview": {"receiving"},
    }
    schemas = {
        name: {"properties": dict.fromkeys(names, {})} for name, names in fields.items()
    }

    def core(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={"jobs": []}
            if request.url.path == "/preservation/library"
            else {"paths": paths, "components": {"schemas": schemas}},
        )

    client = CoreClient(Settings(), "local", transport=httpx.MockTransport(core))
    try:
        assert (await check_core(client))["I2P preservation jobs"]
        schemas["InvitationPreview"]["properties"].clear()
        report = await check_core(client)
        assert report["Library"] and not report["I2P preservation jobs"]
    finally:
        await client.close()
