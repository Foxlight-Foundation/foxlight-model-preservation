"""Private control authentication, software pinning and refusal before execution."""

import hashlib
import json
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files

import pytest

from foxlight_model_preservation.i2p_models import I2PRuntime
from foxlight_model_preservation.i2p_runtime import (
    SnarkRPC,
    check_runtime,
    initialize_i2p,
    owned_snark,
)
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationControl
from foxlight_model_preservation.signing import digest, encode_record


@contextmanager
def rpc_server(replies):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(
                (
                    dict(self.headers),
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
            )
            status, headers, body = replies.pop(0)
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        yield server.server_port, requests
    finally:
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()


def test_control_requires_bearer_and_bounded_transmission_challenge(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://invalid.invalid:1")
    replies = [
        (409, {"X-Transmission-Session-Id": "challenge"}, b""),
        (200, {}, b'{"result":"success","arguments":{"ok":true}}'),
    ]
    with rpc_server(replies) as (port, requests):
        client = SnarkRPC(port, "a" * 64, OperationControl())
        assert client.call("session-get") == {"ok": True}
    assert len(requests) == 2
    assert all(r[0]["Authorization"] == "Bearer " + "a" * 64 for r in requests)
    assert requests[1][0]["X-Transmission-Session-Id"] == "challenge"


@pytest.mark.parametrize(
    "status,headers,body",
    [
        (401, {}, b""),
        (409, {}, b""),
        (302, {"Location": "http://invalid.invalid/"}, b""),
        (200, {"Content-Encoding": "gzip"}, b"bad"),
        (200, {}, b'{"result":"success","result":"error","arguments":{}}'),
        (
            200,
            {},
            b'{"result":"success","arguments":{},"unused":"' + b"x" * 1024**2 + b'"}',
        ),
    ],
)
def test_untrusted_control_replies_fail_closed(status, headers, body):
    with rpc_server([(status, headers, body)]) as (port, _requests):
        with pytest.raises(ValueError):
            SnarkRPC(port, "b" * 64, OperationControl()).call("session-get")


@pytest.fixture
def pinned_runtime(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    jars = {}
    for name in ("snark.jar", "rpc.jar", "launcher.jar"):
        data = ("synthetic-" + name).encode()
        (runtime / name).write_bytes(data)
        jars[name] = hashlib.sha256(data).hexdigest()
    record = I2PRuntime(
        schema_version="foxlight.i2p-runtime.v1",
        i2p_version="2.14.0",
        i2p_revision="3c98503c5f14efe7bdd783528ba7f621f12d8081",
        rpc_revision="9fa6d9bc601f9ea6cfd18ce1e365bee989067665",
        launcher_sha256=digest(
            files("foxlight_model_preservation")
            .joinpath("resources/SnarkPilot.java")
            .read_bytes()
        ),
        rpc_patched_sha256="c" * 64,
        files=jars,
    )
    data = encode_record(record)
    (runtime / "RUNTIME.json").write_bytes(data)
    java = tmp_path / "java"
    java.write_bytes(b"synthetic executable; never execute")
    return runtime, digest(data), java


def test_init_is_offline_and_software_tampering_refuses(
    pinned_runtime, tmp_path, monkeypatch
):
    runtime, identity, java = pinned_runtime
    profile = tmp_path / "profile"
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    initialize_i2p(profile, runtime, identity, java, router_port=port)
    assert not (profile / "node").exists()

    def forbidden(*args, **kwargs):
        raise AssertionError("unavailable or changed software reached execution")

    import foxlight_model_preservation.i2p_runtime as native

    monkeypatch.setattr(native.subprocess, "Popen", forbidden)
    with pytest.raises(PreservationError, match="router unavailable"):
        with owned_snark(
            profile,
            allow_i2p=True,
            allow_sharing=True,
            seconds=10,
            contacts=(),
            control=OperationControl(),
        ):
            pass
    (runtime / "snark.jar").write_bytes(b"changed")
    with pytest.raises(PreservationError, match="identity differs"):
        check_runtime(runtime, identity)


def test_changed_java_refuses_before_native_effects(pinned_runtime, tmp_path):
    runtime, identity, java = pinned_runtime
    profile = tmp_path / "profile"
    initialize_i2p(profile, runtime, identity, java)
    java.write_bytes(b"changed")
    with pytest.raises(PreservationError, match="executable changed"):
        with owned_snark(
            profile,
            allow_i2p=True,
            allow_sharing=True,
            seconds=10,
            contacts=(),
            control=OperationControl(),
        ):
            pass
    assert not (profile / "node").exists()
