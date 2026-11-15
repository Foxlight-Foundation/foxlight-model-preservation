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
        lambda **kwargs: Rendezvous(
            interface="127.0.0.1", bootstrap=boot, lab=True, dht=kwargs.get("dht", True)
        ),
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
                "publish_nostr": False,
                "announce_disclosure": 1,
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


def test_closing_the_rendezvous_actually_stops_the_dht() -> None:
    node = Rendezvous(interface="127.0.0.1", bootstrap=(), lab=True)
    assert node.ready(seconds=10)
    node.close()
    end = time.monotonic() + 5
    while node.session.is_dht_running() and time.monotonic() < end:
        time.sleep(0.05)
    assert not node.session.is_dht_running()


def test_dht_announces_only_a_port_people_can_actually_reach(tmp_path: Path) -> None:
    from foxlight_model_preservation.local_host.community import CommunityMirror
    from foxlight_model_preservation.local_host.sharing import Sharing

    community = CommunityMirror(tmp_path, Sharing(tmp_path))
    # Only a consent given with the announcement disclosure announces at all.
    community.configure(
        {
            "enabled": True,
            "consent": True,
            "announce_disclosure": 1,
            "public_url": "http://mirror.example:18080",
        }
    )
    assert community.dht_port() == 18080  # The forwarded public port.
    community.configure({"public_url": "http://203.0.113.9/"})
    assert community.dht_port() == 80
    community.configure({"public_url": "https://mirror.example"})
    assert community.dht_port() is None  # IP plus port cannot express HTTPS.
    community.configure({"public_url": "http://mirror.example:18080"})
    community.configure({"announce_dht": False})
    assert community.dht_port() is None


def test_opting_out_leaves_the_dht_immediately(
    tmp_path: Path, capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_local_backups import saved
    from test_local_community import free_port, wait_for

    from foxlight_model_preservation.local_host import community as module
    from foxlight_model_preservation.local_host.community import DEFAULT_LICENSES
    from foxlight_model_preservation.local_host.owner import LocalOwner

    router = Rendezvous(interface="127.0.0.1", bootstrap=(), lab=True)
    boot = (("127.0.0.1", router.port),)
    monkeypatch.setattr(
        module,
        "Rendezvous",
        lambda **kwargs: Rendezvous(
            interface="127.0.0.1", bootstrap=boot, lab=True, dht=kwargs.get("dht", True)
        ),
    )
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
                "publish_nostr": False,
                "announce_disclosure": 1,
            },
        )
        wait_for(lambda: owner.community.rendezvous is not None, seconds=60)
        # Turning the DHT off while still sharing leaves it at once...
        owner.control("community-configure", {"announce_dht": False})
        wait_for(lambda: owner.community.rendezvous is None, seconds=30)
        owner.control("community-configure", {"announce_dht": True})
        wait_for(lambda: owner.community.rendezvous is not None, seconds=60)
        # ...and so does opting out of sharing altogether.
        owner.control("community-configure", {"enabled": False})
        wait_for(lambda: owner.community.rendezvous is None, seconds=30)
    finally:
        owner.close()
        router.close()


def test_the_router_answer_is_remembered_for_the_mirror_port_only() -> None:
    """What libtorrent reports from UPnP / NAT-PMP, without a real router."""
    node = Rendezvous(interface="127.0.0.1", bootstrap=(), lab=True, dht=False)
    try:
        assert node.router() == {
            "external_port": None,
            "external_address": None,
            "error": None,
        }
        node.open_port = 18080  # what the mirror asked to have forwarded

        def alert(name: str, **fields: object) -> object:
            return type(name, (), {"message": lambda self: "no router", **fields})()

        node._note(alert("portmap_error_alert"))
        assert node.router()["error"] == "no router"
        # The DHT's own UDP mapping isn't the mirror's port, nor is
        # libtorrent's TCP listener, nor the mirror's port moved elsewhere.
        node._note(alert("portmap_alert", map_protocol=2, external_port=18080))
        node._note(alert("portmap_alert", map_protocol=1, external_port=6881))
        node._note(alert("portmap_alert", map_protocol=1, external_port=41000))
        assert node.router()["external_port"] is None
        node._note(alert("portmap_alert", map_protocol=1, external_port=18080))
        node._note(alert("external_ip_alert", external_address="93.184.216.34"))
        assert node.router() == {
            "external_port": 18080,
            "external_address": "93.184.216.34",
            "error": None,
        }
        # A later failure doesn't undo a forward that worked.
        node._note(alert("portmap_error_alert"))
        assert node.router()["error"] is None
    finally:
        node.close()


class _Router:
    """Stands in for the rendezvous session: no DHT, no real router."""

    made: list[dict[str, object]] = []
    answer: dict[str, object] = {}
    announced: list[int] = []

    def __init__(self, **kwargs: object) -> None:
        self.open_port = kwargs.get("open_port")
        self.dht = kwargs.get("dht", True)
        _Router.made.append(kwargs)

    def router(self) -> dict[str, object]:
        empty = {"external_port": None, "external_address": None, "error": None}
        if not self.open_port:
            return empty
        return {**empty, "external_port": self.open_port, **_Router.answer}

    def announce(self, port: int, *args: object, **kwargs: object) -> bool:
        _Router.announced.append(port)
        return True

    def lookup(self, *args: object, **kwargs: object) -> tuple[str, ...]:
        return ()

    def close(self) -> None: ...


def _sharing_behind_a_router(
    tmp_path: Path, capture: Capture, monkeypatch: pytest.MonkeyPatch, **choice: object
):
    from test_local_backups import saved
    from test_local_community import free_port, wait_for

    from foxlight_model_preservation.local_host import community as module
    from foxlight_model_preservation.local_host.community import DEFAULT_LICENSES
    from foxlight_model_preservation.local_host.owner import LocalOwner

    class Private:
        """The route to the internet leaves from a home network address."""

        def __init__(self, *args: object) -> None: ...

        def __enter__(self) -> Private:
            return self

        def __exit__(self, *args: object) -> None: ...

        def connect(self, *args: object) -> None: ...

        def getsockname(self) -> tuple[str, int]:
            return ("192.168.1.20", 0)

    # Only the community module's route check sees the home network address.
    monkeypatch.setattr(
        module,
        "socket",
        type("Sockets", (), {"socket": Private, "AF_INET": 2, "SOCK_DGRAM": 2}),
    )
    monkeypatch.setattr(module, "Rendezvous", _Router)
    monkeypatch.setattr(module, "rendezvous_available", lambda: True)
    _Router.made.clear()
    _Router.announced.clear()
    owner = LocalOwner(tmp_path / "owner")
    saved(owner, tmp_path, capture)
    port = free_port()
    owner.control(
        "community-configure",
        {
            "enabled": True,
            "consent": True,
            "port": port,
            "licenses": [*DEFAULT_LICENSES, "test-only"],
            "publish_nostr": False,
            "announce_disclosure": 1,
            "open_router_port": True,
            **choice,
        },
    )
    wait_for(lambda: owner.control("community-read", {})["sharing_count"] == 1)
    return owner, port


def test_a_forwarded_port_makes_a_home_mirror_reachable(
    tmp_path: Path, capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from foxlight_model_preservation.operations import OperationControl

    _Router.answer = {"external_address": "93.184.216.34"}
    owner, port = _sharing_behind_a_router(tmp_path, capture, monkeypatch)
    try:
        _Router.announced.clear()
        owner.community.gossip(OperationControl())
        # Sharing asks the router to forward exactly the mirror's port, and the
        # same round announces it once the router agrees.
        assert any(made.get("open_port") == port for made in _Router.made)
        assert _Router.announced == [port]
        reach = owner.community.reachability()
        assert reach["status"] == "router_opened"
        assert reach["mirror_url"] == f"http://93.184.216.34:{port}"
        assert owner.community.addresses() == (f"http://93.184.216.34:{port}",)
        # The DHT announces the port people actually reach: the forwarded one.
        assert owner.community.dht_port() == port
    finally:
        owner.close()


def test_a_provider_shared_address_is_said_plainly_and_never_published(
    tmp_path: Path, capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from foxlight_model_preservation.operations import OperationControl

    # Carrier-grade NAT: the router forwarded, but its own outside address is
    # one the internet provider shares among customers.
    _Router.answer = {"external_address": "100.64.3.4"}
    owner, _port = _sharing_behind_a_router(tmp_path, capture, monkeypatch)
    try:
        owner.community.gossip(OperationControl())
        assert owner.community.reachability()["status"] == "carrier_nat"
        assert owner.community.addresses() == ()
        assert owner.community.dht_port() is None
    finally:
        owner.close()


def test_turning_router_forwarding_off_never_asks_the_router(
    tmp_path: Path, capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from foxlight_model_preservation.operations import OperationControl

    _Router.answer = {"external_address": "93.184.216.34"}
    owner, _port = _sharing_behind_a_router(
        tmp_path, capture, monkeypatch, open_router_port=False
    )
    try:
        owner.community.gossip(OperationControl())
        assert all(made.get("open_port") is None for made in _Router.made)
        reach = owner.community.reachability()
        assert reach["status"] == "private_network"
        assert reach["router"]["asked"] is False
        assert owner.community.addresses() == ()
    finally:
        owner.close()


def test_a_sharer_from_before_the_router_choice_is_never_forwarded(
    tmp_path: Path, capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Someone already sharing was told the router is never changed: until they
    choose it, upgrading must not forward anything."""
    from foxlight_model_preservation.operations import OperationControl

    _Router.answer = {"external_address": "93.184.216.34"}
    owner, _port = _sharing_behind_a_router(
        tmp_path, capture, monkeypatch, open_router_port=None
    )
    try:
        assert owner.community.policy().open_router_port is None
        owner.community.gossip(OperationControl())
        assert all(made.get("open_port") is None for made in _Router.made)
        assert owner.community.reachability()["status"] == "private_network"
        # Choosing it later is what turns it on.
        owner.control("community-configure", {"open_router_port": True})
        owner.community.gossip(OperationControl())
        assert owner.community.reachability()["status"] == "router_opened"
    finally:
        owner.close()


def test_reading_the_router_answer_never_waits_for_a_lookup() -> None:
    import threading

    node = Rendezvous(interface="127.0.0.1", bootstrap=(), lab=True, dht=False)
    try:
        node.open_port = 18080
        node.external_port = 18080
        held = threading.Event()
        release = threading.Event()

        def lookup() -> None:  # stands in for a long DHT lookup holding the lock
            with node._lock:
                held.set()
                release.wait(10)

        worker = threading.Thread(target=lookup)
        worker.start()
        held.wait(5)
        started = time.monotonic()
        assert node.router()["external_port"] == 18080  # the last known answer
        assert time.monotonic() - started < 1
        release.set()
        worker.join(5)
    finally:
        node.close()
