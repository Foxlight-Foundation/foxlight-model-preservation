"""I2P-only HTTP boundary, authenticated updates and complete receive-only recovery."""

from __future__ import annotations

import base64
import json
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from test_cli_catalog import command
from test_discovery import Publisher

from foxlight_model_preservation.discovery import (
    bootstrap_identity,
    export_source,
    follow_source,
    refresh_source,
    refresh_source_i2p,
    search_sources,
    source_identity,
)
from foxlight_model_preservation.i2p_http import (
    I2PHTTPProxy,
    i2p_http_endpoint,
    i2p_http_response,
)
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationCancelled, OperationControl
from foxlight_model_preservation.signing import encode_record
from foxlight_model_preservation.storage import manifest_path, restore
from foxlight_model_preservation.transfer import (
    I2PTransferPolicy,
    MirrorHTTPError,
    TransferPolicy,
    fetch,
    response,
)

# Use a full 32-byte destination hash, not a DNS/address-book name.
HOST = base64.b32encode(b"i" * 32).decode().rstrip("=").lower() + ".b32.i2p"
BASE = "http://" + HOST


@contextmanager
def proxy_server(root: Path, *, status=200, headers=(), body=None, host="127.0.0.1"):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append((self.command, self.path, dict(self.headers)))
            path = root / urlsplit(self.path).path.lstrip("/")
            code = status
            data = body
            if data is None:
                if path.is_file():
                    data = path.read_bytes()
                else:
                    code, data = 404, b"not found"
            self.send_response(code)
            for key, value in headers:
                self.send_header(key, value)
            if not any(
                key.lower() in {"content-length", "transfer-encoding"}
                for key, _ in headers
            ):
                self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

    class IPv6Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6

    server_type = IPv6Server if host == "::1" else ThreadingHTTPServer
    server = server_type((host, 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield I2PHTTPProxy(host, server.server_port), requests
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


def no_dns(*args, **kwargs):
    raise AssertionError("protected request attempted DNS")


@pytest.mark.parametrize("suffix", ["", "/archive", ":8080/archive-v1/"])
def test_canonical_i2p_destination(suffix):
    assert i2p_http_endpoint(BASE + suffix) == BASE + suffix


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com",
        "http://example.com",
        "http://127.0.0.1",
        "http://router.i2p",
        BASE + "?i2paddresshelper=untrusted",
        BASE + "#fragment",
        BASE.replace("http://", "http://user:pass@"),
        BASE + "/../escape",
        BASE + "/%2e%2e",
        BASE + "/bad//path",
        BASE + "/a\\b",
        BASE.upper(),
        BASE + ".",
        BASE + ":65536",
        BASE + ":0",
        "http://" + "b" * 52 + ".b32.i2p",
    ],
)
def test_unsafe_or_public_routes_refuse_without_sockets(value, monkeypatch):
    monkeypatch.setattr(socket, "socket", no_dns)
    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    with pytest.raises(ValueError):
        with i2p_http_response(value, "metadata/timestamp.json", I2PHTTPProxy(), 1):
            pytest.fail("unsafe request connected")


@pytest.mark.parametrize(
    "host,port",
    [
        ("localhost", 4444),
        ("192.0.2.1", 4444),
        ("0.0.0.0", 4444),  # noqa: S104 - deliberately rejected proxy input
        ("127.0.0.1", 0),
        ("127.0.0.1", True),
    ],
)
def test_invalid_proxy_refuses(host, port):
    with pytest.raises(PreservationError):
        I2PHTTPProxy(host, port).validate()


@pytest.mark.parametrize(
    "name", [BASE, BASE + ".", "http://hidden.onion", BASE + ".alt"]
)
def test_direct_transport_refuses_private_network_names_before_dns(name, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    with pytest.raises(PreservationError, match="explicit transport"):
        with response(name, "metadata/timestamp.json", TransferPolicy(allow_http=True)):
            pytest.fail("direct private-network request connected")


def test_authenticated_source_update_search_and_offline_cache(
    capture, tmp_path, monkeypatch
):
    publisher = Publisher(tmp_path / "publisher", capture)
    bootstrap = publisher.bootstrap(BASE)
    cache = tmp_path / "cache"
    identity = source_identity(bootstrap)
    follow_source(cache, encode_record(bootstrap), bootstrap_identity(bootstrap))
    with proxy_server(publisher.path) as (proxy, requests):
        monkeypatch.setattr(socket, "getaddrinfo", no_dns)
        first = refresh_source_i2p(cache, identity, proxy=proxy, allow_i2p=True)
        assert first.freshness == "fresh"
        result = search_sources(cache, "offline")
        assert result[0].manifest == capture.identity
        assert (
            result[0].locations[0].description.candidate_bundle_trust == capture.trust
        )
        assert requests
        for method, target, headers in requests:
            assert method == "GET" and target.startswith(BASE + "/")
            assert headers["Host"] == HOST
            assert set(k.lower() for k in headers) == {
                "host",
                "accept-encoding",
                "connection",
            }
        accepted = export_source(cache, identity)
        # Authenticated rollback must retain last-good searchable evidence.
        publisher.publish(version=2)
        refresh_source_i2p(cache, identity, proxy=proxy, allow_i2p=True)
        publisher.publish(version=1)
        with pytest.raises(PreservationError, match="retained last accepted"):
            refresh_source_i2p(cache, identity, proxy=proxy, allow_i2p=True)
    before = len(requests)
    assert search_sources(cache, "offline")[0].manifest == capture.identity
    fresh_cache = tmp_path / "offline-cache"
    follow_source(fresh_cache, accepted, bootstrap_identity(bootstrap))
    assert search_sources(fresh_cache, "offline")[0].manifest == capture.identity
    assert len(requests) == before
    # Ordinary refresh cannot accidentally resolve the private name directly.
    with pytest.raises(PreservationError):
        refresh_source(cache, identity)


def test_original_source_identity_can_use_explicit_protected_mirror(capture, tmp_path):
    publisher = Publisher(tmp_path / "publisher", capture)
    bootstrap = publisher.bootstrap("https://original.example")
    cache = tmp_path / "cache"
    follow_source(cache, encode_record(bootstrap), bootstrap_identity(bootstrap))
    with proxy_server(publisher.path) as (proxy, _):
        status = refresh_source_i2p(
            cache,
            source_identity(bootstrap),
            proxy=proxy,
            endpoints=(BASE,),
            allow_i2p=True,
        )
    assert status.source_id == source_identity(bootstrap)
    exported = json.loads(export_source(cache, status.source_id))
    assert exported["bootstrap"]["endpoints"] == ["https://original.example"]


def test_missing_grant_or_mixed_endpoints_start_no_network(
    capture, tmp_path, monkeypatch
):
    publisher = Publisher(tmp_path / "publisher", capture)
    bootstrap = publisher.bootstrap(BASE, "https://original.example")
    cache = tmp_path / "cache"
    follow_source(cache, encode_record(bootstrap), bootstrap_identity(bootstrap))
    monkeypatch.setattr(socket, "socket", no_dns)
    with pytest.raises(PreservationError, match="participation"):
        refresh_source_i2p(cache, source_identity(bootstrap))
    with pytest.raises(PreservationError, match="b32"):
        refresh_source_i2p(cache, source_identity(bootstrap), allow_i2p=True)


def test_receive_checks_original_envelope_and_every_companion(
    capture, tmp_path, monkeypatch
):
    with proxy_server(capture.store) as (proxy, requests):
        monkeypatch.setattr(socket, "getaddrinfo", no_dns)
        target = tmp_path / "received"
        manifest = fetch(
            capture.identity,
            capture.trust,
            (BASE,),
            target,
            I2PTransferPolicy(proxy=proxy),
        )
        assert (target / manifest_path(capture.identity)).read_bytes() == (
            capture.store / manifest_path(capture.identity)
        ).read_bytes()
        restore(target, capture.identity, capture.trust, tmp_path / "restored")
        for member in manifest.members:
            for file in member.files:
                assert (
                    tmp_path / "restored" / "members" / member.member_id / file.path
                ).read_bytes() == (
                    capture.sources[member.member_id] / file.path
                ).read_bytes()
        assert all(method == "GET" for method, _, _ in requests)


@pytest.mark.parametrize("status", [301, 302, 307, 403])
def test_redirect_and_proxy_failure_never_fall_back(tmp_path, status, monkeypatch):
    with proxy_server(
        tmp_path,
        status=status,
        headers=(("Location", "https://example.com/leak"),),
        body=b"",
    ) as (proxy, requests):
        monkeypatch.setattr(socket, "getaddrinfo", no_dns)
        with pytest.raises(MirrorHTTPError):
            with response(
                BASE, "metadata/timestamp.json", I2PTransferPolicy(proxy=proxy)
            ):
                pytest.fail("redirect accepted")
        assert len(requests) == 1


@pytest.mark.parametrize(
    "headers",
    [
        (("Content-Length", "0"), ("Content-Length", "0")),
        (("Content-Length", "0"), ("Transfer-Encoding", "chunked")),
        (("Content-Encoding", "gzip"),),
    ],
)
def test_ambiguous_or_encoded_responses_refuse(tmp_path, headers):
    with proxy_server(tmp_path, headers=headers, body=b"") as (proxy, _):
        with pytest.raises(PreservationError, match="framing"):
            with response(
                BASE, "metadata/timestamp.json", I2PTransferPolicy(proxy=proxy)
            ):
                pytest.fail("ambiguous response accepted")


def test_cancel_precedes_any_source_request(capture, tmp_path, monkeypatch):
    publisher = Publisher(tmp_path / "publisher", capture)
    bootstrap = publisher.bootstrap(BASE)
    cache = tmp_path / "cache"
    follow_source(cache, encode_record(bootstrap), bootstrap_identity(bootstrap))
    control = OperationControl()
    control.cancel()
    monkeypatch.setattr(socket, "socket", no_dns)
    with pytest.raises(OperationCancelled):
        refresh_source_i2p(
            cache, source_identity(bootstrap), allow_i2p=True, control=control
        )


def test_receive_refuses_bad_signer_before_object_requests(capture, tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from foxlight_model_preservation.signing import public_record

    wrong = capture.trust.model_copy(
        update={"keys": (public_record(Ed25519PrivateKey.generate()),)}
    )
    with proxy_server(capture.store) as (proxy, requests):
        with pytest.raises(PreservationError):
            fetch(
                capture.identity,
                wrong,
                (BASE,),
                tmp_path / "bad",
                I2PTransferPolicy(proxy=proxy),
            )
    assert len(requests) == 1
    assert "/objects/" not in requests[0][1]
    assert not (tmp_path / "bad").exists()


def test_cli_i2p_consent_and_complete_receive(capture, tmp_path):
    trust = tmp_path / "trust.json"
    trust.write_bytes(encode_record(capture.trust))
    target = tmp_path / "received"
    with proxy_server(capture.store) as (proxy, requests):
        args = (
            "i2p-http",
            "fetch",
            capture.identity,
            "--mirror",
            BASE,
            "--trust",
            str(trust),
            "--store",
            str(target),
            "--proxy-port",
            str(proxy.port),
        )
        refused = command(*args)
        assert refused.returncode == 1 and not requests and not target.exists()
        received = command(*args, "--allow-i2p")
        assert received.returncode == 0, received.stderr
        report = json.loads(received.stdout)
        assert report["transport"] == "i2p-http" and report["payload_uploads"] is False
        assert report["verification"] == "complete"


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_numeric_proxy_ipv4_and_ipv6_without_dns(tmp_path, monkeypatch, host):
    if host == "::1" and not socket.has_ipv6:
        pytest.skip("IPv6 unavailable on this host")
    try:
        server = proxy_server(tmp_path, host=host, body=b"verified fixture")
        with server as (proxy, requests):
            monkeypatch.setattr(socket, "getaddrinfo", no_dns)
            with response(
                BASE, "objects/sha256/fixture", I2PTransferPolicy(proxy=proxy), offset=3
            ) as incoming:
                assert incoming.read() == b"verified fixture"
            assert requests[0][2]["Range"] == "bytes=3-"
    except OSError as error:
        if host == "::1" and error.errno in {47, 49, 97, 99}:
            pytest.skip("IPv6 loopback unavailable on this host")
        raise


def test_failed_proxy_retains_authenticated_cache_and_never_uses_direct_hint(
    capture, tmp_path, monkeypatch
):
    publisher = Publisher(tmp_path / "publisher", capture)
    bootstrap = publisher.bootstrap("https://original.example")
    cache = tmp_path / "cache"
    follow_source(
        cache,
        encode_record(bootstrap),
        bootstrap_identity(bootstrap),
        allow_http=True,
        allow_private=True,
    )
    identity = source_identity(bootstrap)
    with proxy_server(publisher.path) as (proxy, _):
        refresh_source_i2p(
            cache, identity, proxy=proxy, endpoints=(BASE,), allow_i2p=True
        )
    before = export_source(cache, identity)
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    with pytest.raises(PreservationError, match="retained last accepted"):
        refresh_source_i2p(
            cache,
            identity,
            proxy=I2PHTTPProxy(port=port),
            endpoints=(BASE,),
            allow_i2p=True,
        )
    assert export_source(cache, identity) == before
    assert search_sources(cache, "offline")[0].manifest == capture.identity


def test_source_refresh_cli_uses_independent_followed_root(capture, tmp_path):
    publisher = Publisher(tmp_path / "publisher", capture)
    bootstrap = publisher.bootstrap(BASE)
    cache = tmp_path / "cache"
    follow_source(cache, encode_record(bootstrap), bootstrap_identity(bootstrap))
    with proxy_server(publisher.path) as (proxy, requests):
        result = command(
            "i2p-http",
            "source-refresh",
            "--cache",
            str(cache),
            "--source",
            source_identity(bootstrap),
            "--proxy-port",
            str(proxy.port),
            "--allow-i2p",
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["source"]["freshness"] == "fresh"
        count = len(requests)
        result = command("source-search", "offline", "--cache", str(cache))
        assert result.returncode == 0
        assert json.loads(result.stdout)["results"][0]["manifest"] == capture.identity
        assert len(requests) == count


def test_cli_followed_source_fetch_needs_no_manual_mirror_url(capture, tmp_path):
    publisher = Publisher(tmp_path / "publisher", capture)
    publisher.mirrors = (BASE + "/store",)
    publisher.publish()
    bootstrap = publisher.bootstrap(BASE + "/publisher")
    cache = tmp_path / "cache"
    follow_source(cache, encode_record(bootstrap), bootstrap_identity(bootstrap))
    trust = tmp_path / "trust.json"
    trust.write_bytes(encode_record(capture.trust))
    with proxy_server(tmp_path) as (proxy, _):
        refresh_source_i2p(
            cache, source_identity(bootstrap), proxy=proxy, allow_i2p=True
        )
        result = command(
            "i2p-http",
            "source-fetch",
            "--cache",
            str(cache),
            "--source",
            source_identity(bootstrap),
            "--entry",
            "small_text",
            "--expected-manifest-id",
            capture.identity,
            "--trust",
            str(trust),
            "--store",
            str(tmp_path / "from-source"),
            "--proxy-port",
            str(proxy.port),
            "--allow-i2p",
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["manifest"] == capture.identity


@contextmanager
def interrupted_range_proxy(capture, *, stall=False):
    from foxlight_model_preservation.storage import object_path, verify_store

    file = (
        verify_store(capture.store, capture.identity, capture.trust).members[0].files[0]
    )
    selected = "/" + object_path(file.sha256)
    offsets = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = urlsplit(self.path).path
            data = (capture.store / path.lstrip("/")).read_bytes()
            offset = int(self.headers["Range"][6:-1]) if "Range" in self.headers else 0
            self.send_response(206 if offset else 200)
            self.send_header("Content-Length", str(len(data) - offset))
            if offset:
                self.send_header(
                    "Content-Range", f"bytes {offset}-{len(data) - 1}/{len(data)}"
                )
            self.end_headers()
            if path == selected:
                offsets.append(offset)
                amount = 0 if stall else 1500 if len(offsets) <= 3 else len(data)
                data = data[offset : offset + amount]
            self.wfile.write(data)
            self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield I2PHTTPProxy(port=server.server_port), offsets
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


def test_i2p_progressive_range_resume_survives_multiple_interruptions(
    capture, tmp_path
):
    from foxlight_model_preservation.storage import verify_store

    with interrupted_range_proxy(capture) as (proxy, offsets):
        fetch(
            capture.identity,
            capture.trust,
            (BASE,),
            tmp_path / "received",
            I2PTransferPolicy(proxy=proxy),
        )
    assert offsets == [0, 1500, 3000, 4500]
    assert verify_store(
        tmp_path / "received", capture.identity, capture.trust
    ) == verify_store(capture.store, capture.identity, capture.trust)


def test_i2p_resume_stops_after_two_attempts_without_progress(capture, tmp_path):
    with interrupted_range_proxy(capture, stall=True) as (proxy, offsets):
        with pytest.raises(PreservationError, match="no mirror"):
            fetch(
                capture.identity,
                capture.trust,
                (BASE,),
                tmp_path / "received",
                I2PTransferPolicy(proxy=proxy),
            )
    assert offsets == [0, 0]
    assert not (tmp_path / "received" / manifest_path(capture.identity)).exists()


def test_i2p_resume_cancellation_keeps_partial_and_starts_no_retry(capture, tmp_path):
    from foxlight_model_preservation.storage import object_path, verify_store

    file = (
        verify_store(capture.store, capture.identity, capture.trust).members[0].files[0]
    )
    partial = tmp_path / "received/.partial"
    partial.mkdir(parents=True)
    previous = (capture.store / object_path(file.sha256)).read_bytes()[:500]
    (partial / file.sha256).write_bytes(previous)
    control = OperationControl(
        lambda progress: control.cancel() if progress.bytes_received else None,
        interval_seconds=0,
    )
    with interrupted_range_proxy(capture) as (proxy, offsets):
        with pytest.raises(OperationCancelled):
            fetch(
                capture.identity,
                capture.trust,
                (BASE,),
                tmp_path / "received",
                I2PTransferPolicy(proxy=proxy),
                control=control,
            )
    assert offsets == [500]
    assert (partial / file.sha256).read_bytes() == previous
    assert not (tmp_path / "received" / manifest_path(capture.identity)).exists()
