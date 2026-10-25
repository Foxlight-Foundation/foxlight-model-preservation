"""Check a real installed independent desktop profile with external sockets refused.

Run with the new environment's Python and -I, outside its package source paths.
This checks installation, local ownership and ticket/Origin controls, not model
runtime support, a native UI, public privacy or complete model recovery.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import json
import socket
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import httpx
from foxlight_preservation_library.desktop import DesktopClient
from foxlight_preservation_library.preview import PUBLIC_REPOSITORY, _library_source
from foxlight_preservation_library.server import LibraryServer


async def qualify() -> dict[str, object]:
    """Exercise installed wheel code and local HTTP without an ambient Core."""
    for name in ("skulk", "skulk_capability_sdk"):
        if importlib.util.find_spec(name) is not None:
            raise RuntimeError("independent profile contains " + name)
    record = json.loads((Path(sys.prefix) / "sovereign-install.json").read_text())
    if record["requirements_profile"] != "requirements-desktop.lock":
        raise RuntimeError("independent desktop installation receipt required")
    if _library_source() != (
        "git+"
        + PUBLIC_REPOSITORY
        + "@"
        + record["source_revision"]
        + "#subdirectory=capability"
    ):
        raise RuntimeError("Core preview cannot reuse retained software evidence")
    expected = [record, *record["components_installed"]]
    versions = {
        item["project"]: importlib.metadata.version(item["project"])
        for item in expected
    }
    if any(versions[item["project"]] != item["version"] for item in expected):
        raise RuntimeError("installed component version changed")
    with TemporaryDirectory(prefix="sovereign-install-check-") as temporary:
        client = DesktopClient(Path(temporary) / "owner")
        server = LibraryServer(None, client, asyncio.get_running_loop())
        server.start()
        address = urlsplit(server.url)
        connect, resolve = socket.socket.connect, socket.getaddrinfo

        def local_connect(connection: socket.socket, endpoint: object) -> None:
            if not isinstance(endpoint, tuple) or endpoint[:2] != (
                address.hostname,
                address.port,
            ):
                raise RuntimeError("connection outside owned loopback surface")
            connect(connection, endpoint)

        def local_resolve(host: object, *args: object, **kwargs: object) -> object:
            if host not in (address.hostname, address.hostname.encode()):
                raise RuntimeError("lookup outside owned loopback surface")
            return resolve(host, *args, **kwargs)

        socket.socket.connect = local_connect
        socket.getaddrinfo = local_resolve
        try:
            async with httpx.AsyncClient(trust_env=False) as browser:
                response = await browser.get(server.url + "api/library?offset=0")
                response.raise_for_status()
                state = response.json()
                if state["host_kind"] != "sovereign" or state["jobs"]:
                    raise RuntimeError("fresh independent ownership unavailable")
                response = await browser.post(
                    server.url + "api/desktop-search",
                    json={"query": "offline"},
                    headers={"X-Preservation-Action": "operator"},
                )
                response.raise_for_status()
                if response.json()["total"] != 0:
                    raise RuntimeError("fresh owner acquired an unselected catalog")
                response = await browser.post(
                    server.url + "api/desktop-refresh",
                    json={},
                    headers={
                        "X-Preservation-Action": "operator",
                        "Origin": "https://untrusted.invalid",
                    },
                )
                if response.status_code != 403:
                    raise RuntimeError("foreign-Origin control was accepted")
                response = await browser.get(server.url)
                response.raise_for_status()
        finally:
            socket.socket.connect, socket.getaddrinfo = connect, resolve
            await asyncio.to_thread(server.close)
            await client.close()
    return {
        "source_revision": record["source_revision"],
        "installed_versions": versions,
        "offline_install": record["offline"],
        "independent_loopback_surface": True,
        "external_connections_and_lookups_refused": True,
        "foreign_origin_refused": True,
        "preview_retained_source_validated": True,
        "models_downloaded_or_executed": False,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(qualify()), sort_keys=True))
