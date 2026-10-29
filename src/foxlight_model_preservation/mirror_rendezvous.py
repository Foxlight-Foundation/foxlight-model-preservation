"""A rendezvous nobody owns: mirrors announce themselves on the BitTorrent DHT.

Every mirror announces its HTTP port under one well-known key on the public
BitTorrent mainline DHT, which millions of independent nodes run. A client with
no contacts at all looks that key up, then asks each address for its signed
peers document. The DHT holds only addresses; it carries no claims, and every
card and every byte is still verified. A non-mirror at an announced address
just fails one request.

Announcing is part of the explicit mirror role. Joining the DHT shows this
computer's address to DHT nodes and routes a little DHT traffic for others.
Receiving never announces; looking up is a separate choice.
"""

from __future__ import annotations

import hashlib
import ipaddress
import time
from importlib import import_module
from typing import Protocol, cast

from .models import PreservationError
from .operations import OperationControl

RENDEZVOUS_KEY = hashlib.sha1(  # noqa: S324 - BitTorrent DHT keys are SHA-1
    b"sovereign.mirror-rendezvous.v1"
).digest()
MAX_ADDRESSES = 256
DHT_ALERTS = 1024 | 262144  # libtorrent dht_notification | dht_operation_notification


class _Alert(Protocol):
    def peers(self) -> list[tuple[str, int]]: ...


class _DhtSession(Protocol):
    def dht_announce(self, info_hash: object, port: int, flags: int) -> None: ...
    def dht_get_peers(self, info_hash: object) -> None: ...
    def add_dht_node(self, node: tuple[str, int]) -> None: ...
    def wait_for_alert(self, milliseconds: int) -> object: ...
    def pop_alerts(self) -> list[object]: ...
    def listen_port(self) -> int: ...
    def pause(self) -> None: ...
    def is_dht_running(self) -> bool: ...
    def apply_settings(self, settings: dict[str, str | int | bool]) -> None: ...


class _Binding(Protocol):
    __version__: str

    def session(self, settings: dict[str, str | int | bool]) -> _DhtSession: ...
    def sha1_hash(self, value: bytes) -> object: ...


class _Alerts(Protocol):
    dht_get_peers_reply_alert: type
    alert: object


def _binding() -> _Binding:
    try:
        engine = cast(_Binding, import_module("libtorrent"))
    except ImportError as error:
        raise PreservationError(
            "the DHT rendezvous needs the optional bittorrent dependency"
        ) from error
    if not engine.__version__.startswith("2.0."):
        raise PreservationError("the DHT rendezvous needs the libtorrent 2.0 line")
    return engine


class Rendezvous:
    """Own one DHT-only libtorrent session: no torrents, no peer connections.

    ``bootstrap`` replaces libtorrent's default public routers, for a lab
    network; ``interface`` is the address to listen on. Local service discovery,
    UPnP and NAT-PMP stay off: Sovereign never configures a router.
    """

    def __init__(
        self,
        *,
        bootstrap: tuple[tuple[str, int], ...] | None = None,
        interface: str = "0.0.0.0",  # noqa: S104 - the DHT is a public network
        lab: bool = False,
    ) -> None:
        engine = _binding()
        settings: dict[str, str | int | bool] = {
            "listen_interfaces": f"{interface}:0",
            "enable_dht": True,
            "enable_lsd": False,
            "enable_upnp": False,
            "enable_natpmp": False,
            "enable_incoming_utp": False,
            "enable_outgoing_utp": False,
            # DHT notifications and operations (lookup replies) only.
            "alert_mask": DHT_ALERTS,
        }
        if bootstrap is not None:
            settings["dht_bootstrap_nodes"] = ",".join(
                f"{host}:{port}" for host, port in bootstrap
            )
        if lab:
            # A loopback lab network needs local addresses to be routable.
            settings.update(
                {
                    "dht_restrict_routing_ips": False,
                    "dht_restrict_search_ips": False,
                    "dht_ignore_dark_internet": False,
                    "dht_enforce_node_id": False,
                    "dht_prefer_verified_node_ids": False,
                }
            )
        self._engine = engine
        self._key = engine.sha1_hash(RENDEZVOUS_KEY)
        self.session = engine.session(settings)
        for node in bootstrap or ():
            self.session.add_dht_node(node)

    @property
    def port(self) -> int:
        """This node's DHT port, for bootstrapping a lab network."""
        return self.session.listen_port()

    def add_node(self, node: tuple[str, int]) -> None:
        """Add a known DHT node (another mirror, or a lab peer)."""
        self.session.add_dht_node(node)

    def ready(
        self, *, seconds: float = 10.0, control: OperationControl | None = None
    ) -> bool:
        """Wait, within bounds, until the DHT has started.

        libtorrent starts the DHT only after resolving its bootstrap routers;
        requests made before that are silently discarded.
        """
        active = control or OperationControl()
        end = time.monotonic() + seconds
        while not self.session.is_dht_running():
            active.checkpoint()
            if time.monotonic() >= end:
                return False
            time.sleep(0.1)
        return True

    def announce(
        self,
        http_port: int,
        *,
        seconds: float = 10.0,
        control: OperationControl | None = None,
    ) -> bool:
        """Announce this mirror's HTTP port; DHT nodes record the address they see.

        DHT entries expire, so callers re-announce every 15 minutes or so.
        Returns False if the DHT did not start in time.
        """
        if not 1 <= http_port <= 65535:
            raise PreservationError("invalid mirror port")
        if not self.ready(seconds=seconds, control=control):
            return False
        self.session.dht_announce(self._key, http_port, 0)
        return True

    def lookup(
        self,
        *,
        seconds: float = 20.0,
        control: OperationControl | None = None,
    ) -> tuple[str, ...]:
        """Find announced mirror base URLs; none of them is trusted for anything."""
        active = control or OperationControl()
        reply = cast(_Alerts, self._engine).dht_get_peers_reply_alert
        found: dict[str, None] = {}
        end = time.monotonic() + seconds
        if not self.ready(seconds=seconds, control=active):
            return ()
        self.session.dht_get_peers(self._key)
        asked = time.monotonic()
        while time.monotonic() < end and len(found) < MAX_ADDRESSES:
            active.checkpoint()
            if not found and time.monotonic() - asked > 3:
                # A lookup issued while the routing table was still filling can
                # return nothing; ask again rather than wait for a lost reply.
                self.session.dht_get_peers(self._key)
                asked = time.monotonic()
            self.session.wait_for_alert(250)
            for alert in self.session.pop_alerts():
                if not isinstance(alert, reply):
                    continue
                for host, port in cast(_Alert, alert).peers():
                    url = _url(host, port)
                    if url is not None:
                        found[url] = None
            if found and time.monotonic() > end - seconds / 2:
                break
        return tuple(found)[:MAX_ADDRESSES]

    def close(self) -> None:
        """Stop participating in the DHT now, not when this object is collected.

        Pausing a libtorrent session does not stop its DHT, so the DHT itself
        is switched off.
        """
        self.session.apply_settings({"enable_dht": False})
        self.session.pause()


def _url(host: str, port: int) -> str | None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if not 1 <= port <= 65535 or address.is_unspecified or address.is_multicast:
        return None
    return (
        f"http://[{address}]:{port}"
        if address.version == 6
        else f"http://{address}:{port}"
    )


def rendezvous_available() -> bool:
    """Report whether the optional DHT binding can be loaded."""
    try:
        _binding()
    except PreservationError:
        return False
    return True
