"""A rendezvous nobody owns: find a first mirror with no contacts at all."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from conftest import Capture
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from foxlight_model_preservation.mirror_cards import build_peers, issue_card
from foxlight_model_preservation.mirror_directory import (
    MirrorDirectory,
    fetch_discovered,
)
from foxlight_model_preservation.seed import SeedServer
from foxlight_model_preservation.storage import verify_store
from foxlight_model_preservation.transfer import TransferPolicy

pytest.importorskip("libtorrent", reason="optional native BitTorrent extra")

from foxlight_model_preservation.mirror_rendezvous import (  # noqa: E402
    Rendezvous,
    _url,
)

LAB = TransferPolicy(allow_http=True, allow_private=True, timeout=3)


def test_addresses_from_the_dht_are_only_plain_candidate_urls() -> None:
    assert _url("203.0.113.5", 8743) == "http://203.0.113.5:8743"
    assert _url("2001:db8::1", 80) == "http://[2001:db8::1]:80"
    for host, port in (("not-an-ip", 80), ("0.0.0.0", 80), ("224.0.0.1", 80)):  # noqa: S104
        assert _url(host, port) is None
    assert _url("203.0.113.5", 0) is None


def test_fresh_client_with_no_contacts_finds_a_mirror_through_the_dht(
    tmp_path: Path, capture: Capture
) -> None:
    identity = capture.identity
    peers = tmp_path / "peers.json"
    server = SeedServer(("127.0.0.1", 0), capture.store, (identity,), capture.trust)
    server.documents = {"/.well-known/sovereign/peers.json": peers}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    peers.write_bytes(
        build_peers(
            issue_card(
                Ed25519PrivateKey.generate(), (f"http://127.0.0.1:{port}",), {identity}
            ),
            (),
        )
    )
    router = Rendezvous(interface="127.0.0.1", bootstrap=(), lab=True)
    nodes: list[Rendezvous] = [router]
    try:
        boot = (("127.0.0.1", router.port),)
        mirror = Rendezvous(interface="127.0.0.1", bootstrap=boot, lab=True)
        squatter = Rendezvous(interface="127.0.0.1", bootstrap=boot, lab=True)
        client = Rendezvous(interface="127.0.0.1", bootstrap=boot, lab=True)
        nodes += [mirror, squatter, client]
        router.add_node(("127.0.0.1", mirror.port))
        time.sleep(2)
        mirror.announce(port)
        # Anyone can announce anything; a dead address costs one request.
        squatter.announce(9)
        time.sleep(2)
        found: tuple[str, ...] = ()
        end = time.monotonic() + 30
        while f"http://127.0.0.1:{port}" not in found and time.monotonic() < end:
            found = client.lookup(seconds=5)
        assert f"http://127.0.0.1:{port}" in found

        directory = MirrorDirectory(tmp_path / "client")
        assert not directory.read().mirrors
        assert directory.crawl(found, LAB) == 1
        received = tmp_path / "received"
        manifest = fetch_discovered(identity, capture.trust, received, directory, LAB)
        assert manifest == verify_store(received, identity, capture.trust)
    finally:
        for node in nodes:
            node.close()
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_reachable_app_mirror_announces_itself_on_the_dht(
    tmp_path: Path, capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_local_backups import saved
    from test_local_community import free_port, wait_for

    from foxlight_model_preservation.local_host import community as module
    from foxlight_model_preservation.local_host.community import DEFAULT_LICENSES
    from foxlight_model_preservation.local_host.owner import LocalOwner
    from foxlight_model_preservation.operations import OperationControl

    router = Rendezvous(interface="127.0.0.1", bootstrap=(), lab=True)
    boot = (("127.0.0.1", router.port),)
    monkeypatch.setattr(
        module,
        "Rendezvous",
        lambda: Rendezvous(interface="127.0.0.1", bootstrap=boot, lab=True),
    )
    client = Rendezvous(interface="127.0.0.1", bootstrap=boot, lab=True)
    port = free_port()
    owner = LocalOwner(tmp_path / "owner")
    try:
        saved(owner, tmp_path, capture)
        owner.control(
            "community-configure",
            {
                "enabled": True,
                "consent": True,
                "port": port,
                "public_url": f"http://127.0.0.1:{port}",
                "licenses": [*DEFAULT_LICENSES, "test-only"],
            },
        )
        wait_for(lambda: owner.control("community-read", {})["sharing_count"] == 1)
        url = f"http://127.0.0.1:{port}"
        found: tuple[str, ...] = ()
        end = time.monotonic() + 40
        while url not in found and time.monotonic() < end:
            owner.community.gossip(OperationControl())
            found = client.lookup(seconds=5)
        assert url in found
    finally:
        owner.close()
        client.close()
        router.close()
