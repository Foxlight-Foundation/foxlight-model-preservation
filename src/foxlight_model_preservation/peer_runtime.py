"""Owned optional Kubo boundary: standard IPNS/DHT/UnixFS, no peer wire protocol."""

from __future__ import annotations

import http.client
import ipaddress
import json
import os
import re
import selectors
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import BinaryIO, Literal, Protocol, cast

from pydantic import Field, JsonValue

from .models import PreservationError, Record
from .operations import OperationControl
from .signing import canonical_bytes, digest, encode_record, strict_json
from .storage import atomic_bytes, directory, open_file, writer_lock

KUBO_VERSION = "0.43.1"
PROFILE = "preservation-peer.json"
PRIVATE_FILTERS = (
    "/ip4/0.0.0.0/ipcidr/8",
    "/ip4/10.0.0.0/ipcidr/8",
    "/ip4/100.64.0.0/ipcidr/10",
    "/ip4/127.0.0.0/ipcidr/8",
    "/ip4/169.254.0.0/ipcidr/16",
    "/ip4/172.16.0.0/ipcidr/12",
    "/ip4/192.168.0.0/ipcidr/16",
    "/ip4/198.18.0.0/ipcidr/15",
    "/ip4/224.0.0.0/ipcidr/4",
    "/ip6/::/ipcidr/128",
    "/ip6/::1/ipcidr/128",
    "/ip6/fc00::/ipcidr/7",
    "/ip6/fe80::/ipcidr/10",
    "/ip6/ff00::/ipcidr/8",
)


class _Codec(Protocol):
    name: str


class _CID(Protocol):
    version: int
    codec: _Codec
    hashfun: _Codec

    def encode(self, base: str) -> str: ...


class _CIDType(Protocol):
    def decode(self, value: str) -> _CID: ...


class _Formats(Protocol):
    CID: _CIDType


def content_id(value: str, *, name: bool = False) -> str:
    """Validate canonical public IPNS keys or SHA-256 UnixFS roots with multiformats."""
    if len(value) > 200 or not value:
        raise PreservationError("invalid peer content identity")
    try:
        formats = cast(_Formats, import_module("multiformats"))
    except ImportError as error:
        raise PreservationError(
            "peer commands require the optional peer dependency"
        ) from error
    try:
        cid = formats.CID.decode(value)
        base = "base36" if name else "base32"
        if cid.version != 1 or cid.encode(base) != value:
            raise ValueError("noncanonical CID")
        if name:
            if cid.codec.name != "libp2p-key":
                raise ValueError("IPNS needs a public-key CID")
        elif cid.codec.name not in {"dag-pb", "raw"} or cid.hashfun.name != "sha2-256":
            raise ValueError("unsupported content CID")
    except (ValueError, KeyError) as error:
        raise PreservationError("invalid canonical peer content identity") from error
    return value


def bootstrap_address(value: str, allow_private: bool) -> str:
    """Accept a bounded standard numeric TCP peer contact, never DNS or a URL."""
    match = re.fullmatch(
        r"/(ip4|ip6)/([^/]+)/tcp/([0-9]{1,5})/p2p/([1-9A-HJ-NP-Za-km-z]{20,128})", value
    )
    if match is None or not 1 <= int(match[3]) <= 65535:
        raise PreservationError("bootstrap needs a numeric IP/TCP/libp2p multiaddress")
    address = ipaddress.ip_address(match[2])
    if address.version != (4 if match[1] == "ip4" else 6):
        raise PreservationError("bootstrap address family mismatch")
    if not allow_private and not address.is_global:
        raise PreservationError("private peer contacts require separate permission")
    return value


class PeerProfile(Record):
    """Local profile and immutable configuration binding; never portable trust."""

    schema_version: Literal["foxlight.peer-runtime.v1"] = "foxlight.peer-runtime.v1"
    mode: Literal["receive", "mirror"]
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    allow_private_peers: bool = False
    kubo_version: Literal["0.43.1"] = KUBO_VERSION


def _environment(repo: Path) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "IPFS_PATH",
            "IPFS_API",
            "IPFS_GATEWAY",
            "IPFS_NS_MAP",
            "IPFS_HTTP_GATEWAY",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        }
    }
    return {**environment, "IPFS_PATH": str(repo.absolute()), "IPFS_FD_MAX": "1024"}


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def _command(
    binary: str,
    repo: Path,
    arguments: tuple[str, ...],
    *,
    maximum: int,
    control: OperationControl,
    deadline: float,
    sink: BinaryIO | None = None,
    charge: Callable[[int], None] | None = None,
) -> bytes:
    control.checkpoint()
    process = subprocess.Popen(  # noqa: S603 - fixed executable and separate arguments, never shell input
        [binary, *arguments],
        env=_environment(repo),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    output = bytearray()
    received = 0
    try:
        if process.stdout is None:
            raise PreservationError("peer command stdout unavailable")
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                control.checkpoint()
                if time.monotonic() >= deadline:
                    raise PreservationError("peer command deadline exceeded")
                if not selector.select(0.1):
                    continue
                chunk = os.read(
                    process.stdout.fileno(), min(65536, maximum - received + 1)
                )
                if not chunk:
                    break
                received += len(chunk)
                if charge is not None:
                    charge(len(chunk))
                if received > maximum:
                    raise PreservationError(
                        "peer command exceeds its output byte bound"
                    )
                if sink is None:
                    output.extend(chunk)
                else:
                    _ = sink.write(chunk)
            try:
                result = process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as error:
                raise PreservationError("peer command deadline exceeded") from error
            if result != 0:
                raise PreservationError("peer command refused or content unavailable")
        return bytes(output)
    finally:
        _stop(process)
        if process.stdout is not None:
            process.stdout.close()


def executable(value: str | None = None) -> str:
    """Select the explicit optional Kubo executable and verify the qualified version."""
    from .peer_install import default_runtime_directory, managed_executable

    selected = (
        value or managed_executable(default_runtime_directory()) or shutil.which("ipfs")
    )
    if selected is None:
        raise PreservationError("install public Kubo 0.43.1 for peer commands")
    selected = str(Path(selected).resolve(strict=True))
    version = (
        _command(
            selected,
            Path.cwd(),
            ("version", "--number"),
            maximum=100,
            control=OperationControl(),
            deadline=time.monotonic() + 10,
        )
        .decode()
        .strip()
    )
    if version != KUBO_VERSION:
        raise PreservationError("this peer adapter requires qualified Kubo 0.43.1")
    return selected


def _private(repo: Path) -> None:
    with directory(repo) as root:
        info = os.fstat(root)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PreservationError(
                "peer runtime requires a dedicated owner-only repository"
            )


def _configuration_digest(config: dict[str, JsonValue]) -> str:
    # Routing contacts are replaceable transport hints. All serving, privacy and
    # identity configuration remains bound. Updating contacts needs one atomic
    # config replacement, without a two-file commit or rotating publisher keys.
    return digest(canonical_bytes({**config, "Bootstrap": []}))


def _contacts(config: dict[str, JsonValue], private: bool) -> tuple[str, ...]:
    values = config.get("Bootstrap", [])
    if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
        raise PreservationError("invalid peer bootstrap contacts")
    contacts = tuple(bootstrap_address(str(v), private) for v in values)
    if len(contacts) > 8 or len(set(contacts)) != len(contacts):
        raise PreservationError("peer bootstrap contacts must be unique and bounded")
    return contacts


def read_profile(repo: Path) -> PeerProfile:
    """Verify that a known dedicated repo has not changed mode or critical config."""
    _private(repo)
    with directory(repo) as root, open_file(root, PROFILE) as stream:
        profile = PeerProfile.model_validate_json(stream.read(65537))
    with directory(repo) as root, open_file(root, "config") as stream:
        data = stream.read(256 * 1024 + 1)
    value = strict_json(data)
    if (
        len(data) > 256 * 1024
        or not isinstance(value, dict)
        or _configuration_digest(value) != profile.configuration_sha256
    ):
        raise PreservationError("peer configuration changed; no runtime was started")
    _contacts(value, profile.allow_private_peers)
    return profile


def _live_api(root: int) -> bool:
    """Report whether an API marker names a live listener; clear a dead one.

    Callers hold the repo writer lock, which every owned node holds for its whole
    lifetime. A loopback marker whose port refuses connections was therefore left
    by an owned node killed before Kubo removed it. Anything else is retained.
    """
    try:
        with open_file(root, "api") as stream:
            address = stream.read(513).decode(errors="replace").strip()
    except FileNotFoundError:
        return False
    match = re.fullmatch(r"/ip4/127\.0\.0\.1/tcp/([0-9]{1,5})", address)
    if match is None or not 0 < int(match.group(1)) <= 65535:
        return True
    try:
        with socket.create_connection(("127.0.0.1", int(match.group(1))), timeout=1):
            return True
    except ConnectionRefusedError:
        os.unlink("api", dir_fd=root)
        return False
    except OSError:
        return True


def set_bootstrap(repo: Path, contacts: tuple[str, ...]) -> tuple[str, ...]:
    """Atomically replace offline routing contacts without changing keys or trust."""
    with directory(repo) as root, writer_lock(root):
        profile = read_profile(repo)
        if _live_api(root):
            raise PreservationError("stop the owned peer before replacing contacts")
        with open_file(root, "config") as stream:
            config = cast(dict[str, JsonValue], strict_json(stream.read(256 * 1024)))
        config["Bootstrap"] = list(contacts)
        validated = _contacts(config, profile.allow_private_peers)
        atomic_bytes(root, "config", canonical_bytes(config))
    return validated


def initialize_peer(
    repo: Path,
    binary: str,
    *,
    mode: Literal["receive", "mirror"] = "receive",
    bootstrap: tuple[str, ...] = (),
    allow_private_peers: bool = False,
) -> PeerProfile:
    """Create a fresh dedicated offline repo; never adopt another Kubo installation."""
    if len(bootstrap) > 8 or len(set(bootstrap)) != len(bootstrap):
        raise PreservationError("peer bootstrap contacts must be unique and bounded")
    contacts = tuple(bootstrap_address(item, allow_private_peers) for item in bootstrap)
    repo.parent.resolve(strict=True)
    repo.mkdir(mode=0o700)
    _command(
        binary,
        repo,
        ("init", "--profile=server"),
        maximum=8192,
        control=OperationControl(),
        deadline=time.monotonic() + 20,
    )
    with directory(repo) as root:
        with open_file(root, "config") as stream:
            config = cast(dict[str, JsonValue], json.loads(stream.read(256 * 1024)))
        config.update(
            {
                # Rewriting Addresses drops the server profile's NoAnnounce, so
                # restate it: a private-scope-filtered node never advertises its
                # LAN/loopback addresses to the peers it contacts.
                "Addresses": {
                    "API": "/ip4/127.0.0.1/tcp/0",
                    "Gateway": [],
                    "Swarm": ["/ip4/0.0.0.0/tcp/0"],
                    "NoAnnounce": [] if allow_private_peers else list(PRIVATE_FILTERS),
                },
                # Relaying and reachability probing serve third parties; neither
                # receiving nor reviewed block sharing consents to that.
                "AutoNAT": {"ServiceMode": "disabled"},
                "AutoConf": {"Enabled": False},
                "DNS": {"Resolvers": {}},
                "AutoTLS": {"Enabled": False},
                "Bootstrap": list(contacts),
                "Discovery": {"MDNS": {"Enabled": False}},
                "Routing": {
                    "Type": "dhtclient" if mode == "receive" else "dhtserver",
                    "DelegatedRouters": [],
                },
                "Bitswap": {"ServerEnabled": mode == "mirror"},
                "Provide": {"Enabled": mode == "mirror", "Strategy": "pinned"},
                "HTTPRetrieval": {"Enabled": False},
                "Peering": {"Peers": []},
                "Ipns": {"DelegatedPublishers": [], "UsePubsub": False},
                "Swarm": {
                    "DisableNatPortMap": True,
                    "RelayService": {"Enabled": False},
                    "AddrFilters": [] if allow_private_peers else list(PRIVATE_FILTERS),
                    "ConnMgr": {"LowWater": 16, "HighWater": 32, "GracePeriod": "20s"},
                },
            }
        )
        encoded = canonical_bytes(config)
        atomic_bytes(root, "config", encoded)
        profile = PeerProfile(
            mode=mode,
            configuration_sha256=_configuration_digest(config),
            allow_private_peers=allow_private_peers,
        )
        atomic_bytes(root, PROFILE, encode_record(profile))
    return profile


class PeerClient:
    """Bounded local RPC CLI access to an explicitly running dedicated peer node."""

    def __init__(
        self,
        repo: Path,
        binary: str,
        *,
        allow_network: bool,
        control: OperationControl | None = None,
        seconds: float = 120,
    ):
        if not allow_network:
            raise PreservationError(
                "peer network participation needs explicit permission"
            )
        if not 0 < seconds <= 86400:
            raise PreservationError("peer deadline exceeds its bound")
        self.profile = read_profile(repo)
        with directory(repo) as root, open_file(root, "api") as stream:
            address = stream.read(513).decode().strip()
        if re.fullmatch(r"/ip4/127\.0\.0\.1/tcp/[0-9]{1,5}", address) is None:
            raise PreservationError("peer API must be the dedicated loopback listener")
        if not 1 <= int(address.rsplit("/", 1)[-1]) <= 65535:
            raise PreservationError("invalid owned peer API port")
        self.repo, self.binary, self.address = repo, binary, address
        self.control = control or OperationControl()
        self.deadline = time.monotonic() + seconds
        self.control.limit_deadline(self.deadline)
        self.exported_bytes = 0

    def command(self, *arguments: str, maximum: int = 65536) -> bytes:
        """Run one fixed adapter command with bounded stdout and an owned deadline."""
        return _command(
            self.binary,
            self.repo,
            ("--api", self.address, *arguments),
            maximum=maximum,
            control=self.control,
            deadline=self.deadline,
        )

    def publisher_name(self) -> str:
        """Read only the dedicated public self-key name from maintained Kubo."""
        output = self.command("key", "ls", "-l", "--ipns-base=base36", maximum=8192)
        for line in output.decode().splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[1] == "self":
                return content_id(fields[0], name=True)
        raise PreservationError("dedicated IPNS self-key unavailable")

    def wire_usage(self) -> int:
        """Read bounded native wire counters from the owned loopback API only."""
        self.control.checkpoint()
        port = int(self.address.rsplit("/", 1)[-1])
        if not 1 <= port <= 65535:
            raise PreservationError("invalid owned peer API port")
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request("POST", "/api/v0/stats/bw", body=b"")
            response = connection.getresponse()
            data = response.read(8193)
            if response.status != 200 or len(data) > 8192:
                raise PreservationError("peer counters unavailable")
            value = strict_json(data)
            total = value.get("TotalOut") if isinstance(value, dict) else None
            if not isinstance(total, int) or isinstance(total, bool) or total < 0:
                raise PreservationError("peer counter response invalid")
            return total
        finally:
            connection.close()

    def cat(self, path: str, *, maximum: int, sink: BinaryIO | None = None) -> bytes:
        """Receive selected content; native overhead has separate accounting."""

        if maximum <= 0:
            raise PreservationError("peer byte allowance exhausted")

        def charge(count: int) -> None:
            self.exported_bytes += count
            self.control.advance(received=count)

        return _command(
            self.binary,
            self.repo,
            ("--api", self.address, "cat", path),
            maximum=maximum,
            control=self.control,
            deadline=self.deadline,
            sink=sink,
            charge=charge,
        )


def serve_peer(
    repo: Path,
    binary: str,
    *,
    allow_network: bool,
    share_public: bool = False,
    seconds: int = 3600,
    max_upload_bytes: int = 1024**3,
    control: OperationControl | None = None,
    on_ready: Callable[[PeerClient], None] | None = None,
    observe_upload: Callable[[int], None] | None = None,
) -> dict[str, object]:
    """Run and stop only this finite node; receive mode never serves model blocks.

    Mirror upload counters are observed native wire counters, with in-flight
    overshoot. Stop always waits for the owned daemon, never a global process.
    """
    if (
        not allow_network
        or not 1 <= seconds <= 86400
        or not 1 <= max_upload_bytes <= 2**53 - 1
    ):
        raise PreservationError("peer network permission and finite bounds required")
    profile = read_profile(repo)
    if (profile.mode == "mirror") != share_public:
        raise PreservationError(
            "mirror profile needs separate explicit public sharing consent"
        )
    active = control or OperationControl()
    with directory(repo) as root, writer_lock(root):
        if _live_api(root):
            raise PreservationError(
                "existing peer API retained; stop its owner before launch"
            )
        active.checkpoint()
        process = subprocess.Popen(  # noqa: S603 - owned executable and fixed command
            [binary, "daemon", "--migrate=false"],
            env=_environment(repo),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=False,
        )
        try:
            started = time.monotonic()
            # Startup is bounded and refuses rather than adopting another API.
            while not (repo / "api").exists():
                active.checkpoint()
                if process.poll() is not None or time.monotonic() - started > 20:
                    raise PreservationError("owned peer runtime could not start")
                time.sleep(0.05)
            client = PeerClient(
                repo, binary, allow_network=True, control=active, seconds=seconds
            )
            while True:
                active.checkpoint()
                try:
                    client.wire_usage()
                    break
                except OSError:
                    if process.poll() is not None or time.monotonic() - started > 20:
                        raise PreservationError(
                            "owned peer API could not start"
                        ) from None
                    time.sleep(0.05)
            if on_ready is not None:
                active.checkpoint()
                on_ready(client)
                active.checkpoint()
            while process.poll() is None and time.monotonic() - started < seconds:
                active.checkpoint()
                if profile.mode == "mirror":
                    uploaded = client.wire_usage()
                    if observe_upload is not None:
                        observe_upload(uploaded)
                    if uploaded >= max_upload_bytes:
                        break
                time.sleep(0.2)
            if process.poll() is not None:
                raise PreservationError("owned native peer exited unexpectedly")
            return {
                "stopped": True,
                "mode": profile.mode,
                "serving_model_blocks": False,
                "reason": "owned session ended",
            }
        finally:
            _stop(process)


def run_peer(
    repo: Path,
    binary: str,
    *,
    allow_network: bool,
    share_public: bool = False,
    seconds: int = 3600,
    max_upload_bytes: int = 1024**3,
    control: OperationControl | None = None,
) -> dict[str, JsonValue]:
    """Supervise one finite native node with an EOF lifetime fence and owned group.

    A killed caller closes the worker pipe, which stops its actual Kubo process.
    Closing a requesting client does not control an independently running node.
    """
    if not allow_network:
        raise PreservationError("peer network participation needs explicit permission")
    profile = read_profile(repo)
    if (profile.mode == "mirror") != share_public:
        raise PreservationError("mirror profile needs separate public sharing consent")
    if not 1 <= seconds <= 86400 or not 1 <= max_upload_bytes <= 2**53 - 1:
        raise PreservationError("peer sessions require finite bounds")
    active = control or OperationControl()
    process = subprocess.Popen(  # noqa: S603 - fixed known worker, no shell
        [
            sys.executable,
            "-m",
            "foxlight_model_preservation.peer_runtime",
            "--worker",
            str(repo.absolute()),
            binary,
            str(seconds),
            str(max_upload_bytes),
            "share" if share_public else "receive",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    output = bytearray()
    try:
        if process.stdout is None:
            raise PreservationError("peer worker unavailable")
        deadline = time.monotonic() + seconds + 30
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                active.checkpoint()
                if time.monotonic() > deadline:
                    raise PreservationError("peer worker deadline exceeded")
                if not selector.select(0.1):
                    continue
                chunk = os.read(process.stdout.fileno(), 8192)
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > 8192:
                    raise PreservationError("peer worker response exceeds its bound")
        if process.wait(timeout=5) != 0:
            raise PreservationError("owned peer runtime failed or refused startup")
        value = strict_json(bytes(output))
        if not isinstance(value, dict):
            raise PreservationError("peer worker receipt invalid")
        return value
    finally:
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            pass
        # The group includes Kubo, so even an unexpectedly killed worker cannot
        # leave native networking running while its caller remains alive.
        for signum in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, signum)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        _stop(process)
        if process.stdout is not None:
            process.stdout.close()


def _worker() -> int:
    if len(sys.argv) != 7 or sys.argv[1] != "--worker":
        return 2
    active = OperationControl()

    def lifetime() -> None:
        _ = sys.stdin.buffer.read(1)
        active.cancel()

    threading.Thread(target=lifetime, daemon=True).start()
    try:
        result = serve_peer(
            Path(sys.argv[2]),
            sys.argv[3],
            allow_network=True,
            seconds=int(sys.argv[4]),
            max_upload_bytes=int(sys.argv[5]),
            share_public=sys.argv[6] == "share",
            control=active,
        )
        sys.stdout.write(json.dumps(result))
        return 0
    except (ValueError, OSError):
        return 1


if __name__ == "__main__":
    raise SystemExit(_worker())
