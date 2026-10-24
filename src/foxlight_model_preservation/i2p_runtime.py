"""Optional owned I2PSnark process with authenticated loopback RPC and EOF fencing."""

from __future__ import annotations

import http.client
import os
import re
import secrets
import socket
import subprocess
import time
from collections.abc import Generator
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from .i2p_models import I2PContact, I2PProfile, I2PRuntime, I2PStatus
from .models import PreservationError, canonical_path
from .operations import OperationControl
from .pmf import new_pmf_tree
from .signing import canonical_bytes, digest, encode_record, strict_json
from .storage import atomic_bytes, directory, file_identity, read_metadata, writer_lock

_JARS = {"snark.jar", "rpc.jar", "launcher.jar"}


def check_runtime(runtime: Path, expected: str) -> I2PRuntime:
    """Hash the exact selected optional software; start no client or router."""
    with directory(runtime) as root:
        encoded = read_metadata(root, "RUNTIME.json")
        if digest(encoded) != expected:
            raise PreservationError("I2P runtime identity differs")
        record = I2PRuntime.model_validate_json(canonical_bytes(strict_json(encoded)))
        launcher = (
            files("foxlight_model_preservation")
            .joinpath("resources/SnarkPilot.java")
            .read_bytes()
        )
        if record.launcher_sha256 != digest(launcher) or set(record.files) != _JARS:
            raise PreservationError("I2P runtime launcher or inventory is unsupported")
        for name, expected_hash in record.files.items():
            identity = file_identity(root, name)
            if identity.sha256 != expected_hash or identity.size > 128 * 1024**2:
                raise PreservationError("I2P runtime file identity differs")
        return record


def read_profile(profile: Path) -> I2PProfile:
    """Read a strict local profile without network effects or trust enrollment."""
    with directory(profile) as root:
        return I2PProfile.model_validate_json(
            canonical_bytes(strict_json(read_metadata(root, "profile.json")))
        )


def initialize_i2p(
    profile: Path,
    runtime: Path,
    expected_runtime_id: str,
    java: Path,
    *,
    router_port: int = 7654,
    upload_kib: int = 32,
    download_kib: int = 256,
) -> I2PProfile:
    """Initialize a new offline profile around explicitly selected optional software.

    The operator supplies an independently operated loopback I2CP router. No
    router is installed, reconfigured, started or adopted. Model inputs cannot
    choose software, Java flags, control endpoints or filesystem roots.
    """
    runtime, java = runtime.resolve(strict=True), java.resolve(strict=True)
    canonical_path(profile.absolute().as_posix().lstrip("/"))
    check_runtime(runtime, expected_runtime_id)
    with directory(java.parent) as parent:
        java_hash = file_identity(parent, java.name).sha256
    record = I2PProfile(
        runtime=str(runtime),
        runtime_id=expected_runtime_id,
        java=str(java),
        java_sha256=java_hash,
        router_port=router_port,
        upload_kib=upload_kib,
        download_kib=download_kib,
    )
    with OperationControl().operation("i2p_init") as active:
        with new_pmf_tree(profile, active) as target:
            atomic_bytes(target, "profile.json", encode_record(record))
    return record


class SnarkRPC:
    """A bounded private control client; never resolves a hostname or follows URLs."""

    def __init__(
        self,
        port: int,
        token: str,
        control: OperationControl,
        *,
        data_dir: Path | None = None,
    ):
        """Bind one owned numeric loopback endpoint and private authorization token."""
        if not 1 <= port <= 65535 or not re.fullmatch(r"[0-9a-f]{64}", token):
            raise PreservationError("invalid private I2P control endpoint")
        self.port, self._token, self._session, self._control = port, token, "", control
        self._data_dir = data_dir

    def call(
        self, method: str, arguments: dict[str, JsonValue] | None = None
    ) -> dict[str, JsonValue]:
        """Use only scoped supported methods; no global stop, removal or deletion."""
        if method not in {
            "session-get",
            "torrent-get",
            "torrent-add",
            "torrent-start",
            "torrent-stop",
        }:
            raise PreservationError("unsupported I2P control method")
        arguments = arguments or {}
        if method in {"torrent-start", "torrent-stop"} or (
            method == "torrent-get" and "ids" in arguments
        ):
            ids = arguments.get("ids")
            if (
                not isinstance(ids, list)
                or len(ids) != 1
                or not isinstance(ids[0], str)
                or not re.fullmatch(r"[0-9a-f]{40}", ids[0])
            ):
                raise PreservationError(
                    "I2P control requires one exact torrent infohash"
                )
        if method == "torrent-add" and (
            set(arguments) != {"metainfo", "download-dir", "paused"}
            or arguments.get("paused") is not True
            or self._data_dir is None
            or arguments.get("download-dir") != str(self._data_dir)
            or not isinstance(arguments.get("metainfo"), str)
        ):
            raise PreservationError(
                "I2P add requires paused metainfo in the owned directory"
            )
        body = canonical_bytes({"method": method, "arguments": arguments or {}})
        for _attempt in range(2):
            self._control.checkpoint()
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
            try:
                connection.request(
                    "POST",
                    "/transmission/rpc",
                    body,
                    headers={
                        "Authorization": "Bearer " + self._token,
                        "Content-Type": "application/json",
                        "X-Transmission-Session-Id": self._session,
                        "Accept-Encoding": "identity",
                    },
                )
                response = connection.getresponse()
                if response.status == 409:
                    session = response.getheader("X-Transmission-Session-Id", "")
                    if not 1 <= len(session) <= 256 or any(
                        ord(c) < 33 or ord(c) > 126 for c in session
                    ):
                        raise PreservationError("invalid I2P RPC session challenge")
                    self._session = session
                    continue
                if (
                    response.status != 200
                    or response.getheader("Content-Encoding", "identity") != "identity"
                ):
                    raise PreservationError(
                        "owned I2P control endpoint refused the request"
                    )
                data = response.read(1024**2 + 1)
                if len(data) > 1024**2:
                    raise PreservationError("I2P RPC response exceeds its bound")
                self._control.checkpoint()
                result = strict_json(data)
                if (
                    not isinstance(result, dict)
                    or result.get("result") != "success"
                    or not isinstance(result.get("arguments"), dict)
                ):
                    raise PreservationError("I2P control operation failed")
                return cast(dict[str, JsonValue], result["arguments"])
            except (OSError, http.client.HTTPException) as error:
                raise PreservationError(
                    "owned I2P control connection failed"
                ) from error
            finally:
                connection.close()
        raise PreservationError("I2P control session challenge did not complete")


def native_status(profile: Path) -> I2PStatus:
    """Read private native routing state; it is neither model nor privacy evidence."""
    with directory(profile / "node") as root:
        return I2PStatus.model_validate_json(
            canonical_bytes(strict_json(read_metadata(root, "status.json")))
        )


@contextmanager
def owned_snark(
    profile: Path,
    *,
    allow_i2p: bool,
    allow_sharing: bool,
    seconds: int,
    contacts: tuple[I2PContact, ...],
    control: OperationControl,
) -> Generator[SnarkRPC]:
    """Own one finite native session; controller EOF stops its actual Java process.

    Swarm receiving can upload, so both grants are required. The selected router
    is separately operated and untouched. Cached kits, DHT and job state remain.
    Native limits cover client lifetime/application rate, not router wire usage.
    """
    if not allow_i2p or not allow_sharing:
        raise PreservationError(
            "I2P participation and payload sharing need separate explicit grants"
        )
    if type(seconds) is not int or not 1 <= seconds <= 86400:
        raise PreservationError("I2P sharing requires a finite lifetime")
    record = read_profile(profile)
    runtime, java = Path(record.runtime), Path(record.java)
    check_runtime(runtime, record.runtime_id)
    with directory(java.parent) as root:
        if file_identity(root, java.name).sha256 != record.java_sha256:
            raise PreservationError("selected Java executable changed")
    control.checkpoint()
    canonical_path(profile.absolute().as_posix().lstrip("/"))
    node = profile.absolute() / "node"
    node.mkdir(mode=0o700, exist_ok=True)
    data = profile.absolute() / "downloads"
    data.mkdir(mode=0o700, exist_ok=True)
    with directory(data):
        pass
    with directory(node) as native_root, writer_lock(native_root):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(2)
                probe.connect(("127.0.0.1", record.router_port))
        except OSError as error:
            raise PreservationError(
                "I2P router unavailable; start the selected loopback I2CP router"
            ) from error
        token = secrets.token_hex(32)
        with directory(node) as local:
            atomic_bytes(local, "control.token", token.encode())
            atomic_bytes(
                local,
                "contacts.txt",
                "".join(f"{c.destination}:{c.port}\n" for c in contacts).encode(),
            )
            atomic_bytes(
                local,
                "i2psnark.config.d/i2psnark.config",
                (
                    f"i2psnark.dir={data}\ni2psnark.i2cpHost=127.0.0.1\ni2psnark.i2cpPort={record.router_port}\n"
                    f"i2psnark.upbw.max={record.upload_kib}\ni2psnark.downbw.max={record.download_kib}\n"
                    "i2psnark.autoStart=false\ni2psnark.useOpentrackers=false\ni2psnark.opentrackers=\n"
                    "i2psnark.enableDHT=true\ni2psnark.startupDelay=0\ni2psnark.filesPublic=false\n"
                ).encode(),
            )
            # A new private token binds readiness to this live invocation.
            for name in ("status.json", "status.pending"):
                try:
                    os.unlink(name, dir_fd=local)
                except FileNotFoundError:
                    pass
        environment = {
            k: v
            for k, v in os.environ.items()
            if k
            not in {
                "JAVA_TOOL_OPTIONS",
                "JDK_JAVA_OPTIONS",
                "_JAVA_OPTIONS",
                "CLASSPATH",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
            }
        }
        command = [
            str(java),
            "-Xmx512m",
            "-Djava.awt.headless=true",
            "-cp",
            os.pathsep.join(str(runtime / name) for name in sorted(_JARS)),
            "ai.foxlight.sovereign.i2p.SnarkPilot",
            str(seconds),
            "i2p-payload-sharing",
        ]
        process = subprocess.Popen(  # noqa: S603 - explicit pinned software, fixed arguments
            command,
            cwd=node,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            end = time.monotonic() + min(seconds, 20)
            while time.monotonic() < end:
                control.checkpoint()
                if process.poll() is not None:
                    raise PreservationError("owned I2P runtime failed during startup")
                try:
                    status = native_status(profile)
                    if status.pid != process.pid:
                        raise PreservationError("I2P native process identity differs")
                    client = SnarkRPC(status.rpc_port, token, control, data_dir=data)
                    session = client.call("session-get")
                    if session.get("i2p-version") != "2.14.0" or session.get(
                        "download-dir"
                    ) != str(data):
                        raise PreservationError(
                            "I2P native version or data directory differs"
                        )
                    break
                except (FileNotFoundError, ConnectionError):
                    time.sleep(0.05)
            else:
                raise PreservationError("owned I2P startup deadline exceeded")
            yield client
        finally:
            if process.stdin is not None:
                process.stdin.close()
            try:
                process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
