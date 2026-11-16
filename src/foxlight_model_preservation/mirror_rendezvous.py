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
import threading
import time
from importlib import import_module
from typing import Protocol, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .models import PreservationError
from .operations import OperationControl

RENDEZVOUS_KEY = hashlib.sha1(  # noqa: S324 - BitTorrent DHT keys are SHA-1
    b"sovereign.mirror-rendezvous.v1"
).digest()
MAX_ADDRESSES = 256
MAX_POINTER_BYTES = 900  # BEP 44 values are at most 1,000 bytes bencoded
DHT_ALERTS = 1024 | 262144  # libtorrent dht_notification | dht_operation_notification
# libtorrent port_mapping_notification | status_notification (external address).
ROUTER_ALERTS = 4 | 64


class _Alert(Protocol):
    def peers(self) -> list[tuple[str, int]]: ...


class _DhtSession(Protocol):
    def dht_announce(self, info_hash: object, port: int, flags: int) -> None: ...
    def dht_get_peers(self, info_hash: object) -> None: ...
    def dht_put_mutable_item(
        self, secret: bytes, public: bytes, value: bytes, salt: bytes
    ) -> None: ...
    def dht_get_mutable_item(self, public: bytes, salt: bytes) -> None: ...
    def add_dht_node(self, node: tuple[str, int]) -> None: ...
    def wait_for_alert(self, milliseconds: int) -> object: ...
    def pop_alerts(self) -> list[object]: ...
    def listen_port(self) -> int: ...
    def pause(self) -> None: ...
    def is_dht_running(self) -> bool: ...
    def apply_settings(self, settings: dict[str, str | int | bool]) -> None: ...
    def add_port_mapping(
        self, protocol: object, external: int, local: int
    ) -> object: ...


class _Ports(Protocol):
    class portmap_protocol:  # noqa: N801 - libtorrent's own name
        tcp: object


class _PortAlert(Protocol):
    map_protocol: int
    external_port: int
    external_address: object

    def message(self) -> str: ...


class _ItemAlert(Protocol):
    item: object
    authoritative: bool


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
    network; ``interface`` is the address to listen on. Local service discovery
    stays off. ``open_port`` asks the home router (UPnP / NAT-PMP) to forward
    that TCP port to this computer, for the mirror's HTTP server; it is the
    owner's choice inside the sharing consent. Without it the router is never
    configured.
    """

    def __init__(
        self,
        *,
        bootstrap: tuple[tuple[str, int], ...] | None = None,
        interface: str = "0.0.0.0",  # noqa: S104 - the DHT is a public network
        lab: bool = False,
        open_port: int | None = None,
        dht: bool = True,
    ) -> None:
        engine = _binding()
        if open_port is not None and not 1 <= open_port <= 65535:
            raise PreservationError("invalid mirror port")
        settings: dict[str, str | int | bool] = {
            "listen_interfaces": f"{interface}:0",
            # Off only for a session that just opens the router port for an
            # owner who chose not to announce on the DHT.
            "enable_dht": dht,
            "enable_lsd": False,
            "enable_upnp": open_port is not None,
            "enable_natpmp": open_port is not None,
            "enable_incoming_utp": False,
            "enable_outgoing_utp": False,
            # DHT notifications and operations (lookup replies), and the
            # router's answers when a port is to be opened.
            "alert_mask": DHT_ALERTS | (ROUTER_ALERTS if open_port else 0),
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
        # libtorrent sessions are not safe for concurrent alert handling: every
        # native call goes through one lock.
        self._lock = threading.RLock()
        self._key = engine.sha1_hash(RENDEZVOUS_KEY)
        self.session = engine.session(settings)
        for node in bootstrap or ():
            self.session.add_dht_node(node)
        self.open_port = open_port
        self.dht = dht
        self.external_port: int | None = None
        self.external_address: str | None = None
        self.router_error: str | None = None
        if open_port is not None:
            protocol = cast(_Ports, engine).portmap_protocol.tcp
            try:
                self.session.add_port_mapping(protocol, open_port, open_port)
            except TypeError:
                # libtorrent 2.0's Python binding queues the mapping, then
                # cannot convert the mapping IDs it returns; the request stands.
                pass

    def router(self) -> dict[str, int | str | None]:
        """What the home router answered: the forwarded port and outside
        address once it agreed, or why it refused. All None until it answers
        (and when no port was asked for).

        Never waits: a DHT lookup can hold the session for many seconds, and
        it records router answers itself as they arrive, so while it runs the
        last known answer is returned at once.
        """
        if self._lock.acquire(blocking=False):
            try:
                for alert in self.session.pop_alerts():
                    self._note(alert)
            finally:
                self._lock.release()
        return {
            "external_port": self.external_port,
            "external_address": self.external_address,
            "error": self.router_error,
        }

    def _note(self, alert: object) -> None:
        """Remember a router answer about this mirror's port; ignore the rest."""
        if self.open_port is None:
            return
        name = type(alert).__name__
        if name == "portmap_alert":
            mapping = cast(_PortAlert, alert)
            # Only the mirror's own TCP mapping, which asks for the same port
            # outside. libtorrent also maps its own listener (TCP and UDP),
            # and the binding can't say which mapping is which, so a mirror
            # port the router moved elsewhere is reported as not forwarded
            # rather than risk publishing the listener's port.
            if int(mapping.map_protocol) == 1 and int(mapping.external_port) == (
                self.open_port
            ):
                self.external_port = int(mapping.external_port)
                self.router_error = None
        elif name == "portmap_error_alert":
            if self.external_port is None:
                self.router_error = str(cast(_PortAlert, alert).message())[:200]
        elif name == "external_ip_alert":
            self.external_address = str(cast(_PortAlert, alert).external_address)

    @property
    def port(self) -> int:
        """This node's DHT port, for bootstrapping a lab network."""
        return self.session.listen_port()

    def add_node(self, node: tuple[str, int]) -> None:
        """Add a known DHT node (another mirror, or a lab peer)."""
        with self._lock:
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
        while True:
            with self._lock:
                if self.session.is_dht_running():
                    break
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
        with self._lock:
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
        with self._lock:
            return self._lookup(found, end, seconds, reply, active)

    def _lookup(
        self,
        found: dict[str, None],
        end: float,
        seconds: float,
        reply: type,
        active: OperationControl,
    ) -> tuple[str, ...]:
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
                    self._note(alert)
                    continue
                for host, port in cast(_Alert, alert).peers():
                    url = _url(host, port)
                    if url is not None:
                        found[url] = None
            if found and time.monotonic() > end - seconds / 2:
                break
        return tuple(found)[:MAX_ADDRESSES]

    def put_pointer(
        self,
        secret: bytes,
        public: bytes,
        salt: bytes,
        value: bytes,
        *,
        seconds: float = 10.0,
        control: OperationControl | None = None,
    ) -> bool:
        """Store a signed mutable item (BEP 44) under ``public`` and ``salt``.

        libtorrent fetches the current item first and signs the new one with a
        higher sequence number. ``secret`` is the expanded 64-byte Ed25519
        secret it signs with. Returns False if the DHT did not start in time;
        the put itself completes in the background.
        """
        if len(secret) != 64 or len(public) != 32 or len(value) > MAX_POINTER_BYTES:
            raise PreservationError("invalid DHT pointer")
        if not self.ready(seconds=seconds, control=control):
            return False
        with self._lock:
            self.session.dht_put_mutable_item(secret, public, value, salt)
        return True

    def get_pointer(
        self,
        public: bytes,
        salt: bytes,
        *,
        seconds: float = 20.0,
        control: OperationControl | None = None,
    ) -> bytes | None:
        """The newest value stored under ``public`` and ``salt``, or None.

        libtorrent checks each item's signature against ``public`` before
        reporting it; the signature is checked again here, so nothing but the
        key's owner can set what this returns.
        """
        if len(public) != 32:
            raise PreservationError("invalid DHT pointer key")
        active = control or OperationControl()
        end = time.monotonic() + seconds
        if not self.ready(seconds=seconds, control=active):
            return None
        best: tuple[int, bytes] | None = None
        with self._lock:
            self.session.dht_get_mutable_item(public, salt)
            while time.monotonic() < end:
                active.checkpoint()
                self.session.wait_for_alert(250)
                done = False
                for alert in self.session.pop_alerts():
                    if type(alert).__name__ != "dht_mutable_item_alert":
                        self._note(alert)
                        continue
                    found = _verified_item(cast(_ItemAlert, alert), public, salt)
                    if found is not None and (best is None or found[0] > best[0]):
                        best = found
                    if bool(cast(_ItemAlert, alert).authoritative):
                        done = True
                if done:
                    break
        return None if best is None else best[1]

    def close(self) -> None:
        """Stop participating in the DHT now, not when this object is collected.

        Pausing a libtorrent session does not stop its DHT, so the DHT itself
        is switched off.
        """
        with self._lock:
            self.session.apply_settings({"enable_dht": False})
            self.session.pause()


def _bencoded(value: bytes) -> bytes:
    return str(len(value)).encode() + b":" + value


def _verified_item(
    alert: _ItemAlert, public: bytes, salt: bytes
) -> tuple[int, bytes] | None:
    """(sequence, value) of a mutable item whose signature ``public`` made."""
    try:
        item = alert.item
    except RuntimeError:
        return None  # an empty reply: nothing is stored there
    if not isinstance(item, dict):
        return None
    item = cast(dict[str, object], item)
    value, signature, seq = item.get("value"), item.get("signature"), item.get("seq")
    if item.get("key") != public or item.get("salt") != salt:
        return None
    if not isinstance(value, bytes) or not isinstance(signature, bytes):
        return None
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        return None
    signed = b"4:salt" + _bencoded(salt) + b"3:seqi%de1:v" % seq + _bencoded(value)
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(signature, signed)
    except (InvalidSignature, ValueError):
        return None
    return seq, value


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
