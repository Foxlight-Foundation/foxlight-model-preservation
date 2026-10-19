"""Real HTTP serving, range recovery and hostile mirror boundaries."""

import http.client
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest
from conftest import Capture

from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.seed import SeedServer
from foxlight_model_preservation.storage import manifest_path, object_path, verify_store
from foxlight_model_preservation.transfer import TransferPolicy, fetch, response


@contextmanager
def running_seed(capture: Capture, **kwargs):
    server = SeedServer(
        ("127.0.0.1", 0), capture.store, (capture.identity,), capture.trust, **kwargs
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, "http://127.0.0.1:" + str(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_corrupt_first_mirror_falls_back(capture: Capture, tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt"
    shutil.copytree(capture.store, corrupt)
    manifest = verify_store(capture.store, capture.identity, capture.trust)
    bad = corrupt / object_path(manifest.members[0].files[0].sha256)
    bad.write_bytes(b"x" * bad.stat().st_size)
    target = tmp_path / "download"
    result = fetch(
        capture.identity, capture.trust, (str(corrupt), str(capture.store)), target
    )
    assert result == manifest
    assert not (target / ".seed").exists()
    verify_store(target, capture.identity, capture.trust)


def test_http_range_resume_and_complete_partial(
    capture: Capture, tmp_path: Path
) -> None:
    manifest = verify_store(capture.store, capture.identity, capture.trust)
    target = tmp_path / "download"
    (target / ".partial").mkdir(parents=True)
    first, second = manifest.members[0].files
    for file, amount in ((first, 50), (second, second.size)):
        (target / ".partial" / file.sha256).write_bytes(
            (capture.store / object_path(file.sha256)).read_bytes()[:amount]
        )
    with running_seed(capture) as (_server, url):
        fetch(
            capture.identity,
            capture.trust,
            (url,),
            target,
            TransferPolicy(allow_http=True, allow_private=True),
        )
    verify_store(target, capture.identity, capture.trust)


def test_network_defaults_block_loopback(capture: Capture) -> None:
    with running_seed(capture) as (_server, url):
        with pytest.raises(PreservationError):
            with response(url, manifest_path(capture.identity), TransferPolicy()):
                pass
        with pytest.raises(PreservationError, match="permitted"):
            with response(
                url, manifest_path(capture.identity), TransferPolicy(allow_http=True)
            ):
                pass


def test_seed_allowlist_range_and_budget(capture: Capture) -> None:
    manifest = verify_store(capture.store, capture.identity, capture.trust)
    file = manifest.members[0].files[0]
    with running_seed(capture, max_upload_bytes=10) as (server, _url):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        connection.request(
            "GET", "/" + object_path(file.sha256), headers={"Range": "bytes=2-6"}
        )
        result = connection.getresponse()
        assert result.status == 206
        assert (
            result.read()
            == (capture.store / object_path(file.sha256)).read_bytes()[2:7]
        )
        for path, expected in (
            ("/../LICENSE", 404),
            ("/.write.lock", 404),
            ("/" + object_path(file.sha256), 429),
        ):
            connection.request("GET", path)
            result = connection.getresponse()
            assert result.status == expected
            result.read()
        connection.close()


def test_public_seed_requires_public_intake(capture: Capture, tmp_path: Path) -> None:
    from foxlight_model_preservation.storage import pack

    member = capture.recipe.members[0]
    private = member.model_copy(
        update={"intake": member.intake.model_copy(update={"audience": "private"})}
    )
    recipe = capture.recipe.model_copy(
        update={"members": (private, capture.recipe.members[1])}
    )
    store = tmp_path / "private"
    identity = pack(recipe, capture.sources, store, (capture.key,))
    with pytest.raises(PreservationError, match="public intake"):
        SeedServer(("127.0.0.1", 0), store, (identity,), capture.trust, public=True)


def test_budget_failure_publishes_no_completion(
    capture: Capture, tmp_path: Path
) -> None:
    target = tmp_path / "download"
    with pytest.raises(PreservationError, match="budget"):
        fetch(
            capture.identity,
            capture.trust,
            (str(capture.store),),
            target,
            TransferPolicy(max_bytes=1),
        )
    assert not (target / manifest_path(capture.identity)).exists()


def test_corrupt_resume_prefix_recovers(capture: Capture, tmp_path: Path) -> None:
    manifest = verify_store(capture.store, capture.identity, capture.trust)
    file = manifest.members[0].files[0]
    target = tmp_path / "download"
    (target / ".partial").mkdir(parents=True)
    (target / ".partial" / file.sha256).write_bytes(b"wrong prefix")
    fetch(capture.identity, capture.trust, (str(capture.store),), target)
    verify_store(target, capture.identity, capture.trust)


@pytest.mark.parametrize("mode", ["truncate", "ignore-range", "bad-range", "redirect"])
def test_hostile_http_responses(
    capture: Capture,
    tmp_path: Path,
    mode: str,
) -> None:
    import http.server

    manifest = verify_store(capture.store, capture.identity, capture.trust)
    first = manifest.members[0].files[0]
    requested_ranges = []
    calls = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            calls.append(self.path)
            relative = self.path[1:]
            if relative == manifest_path(capture.identity):
                data = (capture.store / relative).read_bytes()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if mode == "redirect":
                self.send_response(302)
                self.send_header("Location", "/must-not-be-followed")
                self.end_headers()
                return
            data = (capture.store / relative).read_bytes()
            requested = self.headers.get("Range")
            if relative == object_path(first.sha256):
                requested_ranges.append(requested)
            offset = (
                int(requested.removeprefix("bytes=").removesuffix("-"))
                if requested
                else 0
            )
            partial = offset > 0 and mode != "ignore-range"
            self.send_response(206 if partial else 200)
            if partial:
                actual = 0 if mode == "bad-range" else offset
                self.send_header(
                    "Content-Range", f"bytes {actual}-{len(data) - 1}/{len(data)}"
                )
            else:
                offset = 0
            data = data[offset:]
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if mode == "truncate" and len(requested_ranges) == 1:
                self.wfile.write(data[:50])
                self.close_connection = True
            else:
                self.wfile.write(data)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    target = tmp_path / "target"
    if mode in {"ignore-range", "bad-range"}:
        (target / ".partial").mkdir(parents=True)
        (target / ".partial" / first.sha256).write_bytes(
            (capture.store / object_path(first.sha256)).read_bytes()[:30]
        )
    try:
        url = "http://127.0.0.1:" + str(server.server_port)
        if mode in {"bad-range", "redirect"}:
            with pytest.raises(PreservationError):
                fetch(
                    capture.identity,
                    capture.trust,
                    (url,),
                    target,
                    TransferPolicy(allow_http=True, allow_private=True),
                )
            assert not (target / manifest_path(capture.identity)).exists()
            assert "/must-not-be-followed" not in calls
        else:
            fetch(
                capture.identity,
                capture.trust,
                (url,),
                target,
                TransferPolicy(allow_http=True, allow_private=True),
            )
            verify_store(target, capture.identity, capture.trust)
            assert any(value is not None for value in requested_ranges)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_cumulative_retry_budget(capture: Capture, tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt"
    shutil.copytree(capture.store, corrupt)
    manifest = verify_store(capture.store, capture.identity, capture.trust)
    file = manifest.members[0].files[0]
    (corrupt / object_path(file.sha256)).write_bytes(b"x" * file.size)
    target = tmp_path / "target"
    with pytest.raises(PreservationError):
        fetch(
            capture.identity,
            capture.trust,
            (str(corrupt), str(capture.store)),
            target,
            TransferPolicy(
                max_bytes=sum(f.size for m in manifest.members for f in m.files)
            ),
        )
    assert not (target / manifest_path(capture.identity)).exists()


def test_disk_capacity_preflight(capture: Capture, tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        "foxlight_model_preservation.transfer.shutil.disk_usage",
        lambda _: SimpleNamespace(free=0),
    )
    target = tmp_path / "target"
    with pytest.raises(PreservationError, match="free space"):
        fetch(capture.identity, capture.trust, (str(capture.store),), target)
    assert not (target / manifest_path(capture.identity)).exists()


def test_https_certificate_validation(capture: Capture, tmp_path: Path, monkeypatch):
    import ipaddress
    import ssl
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "local test")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    certificate, private = tmp_path / "cert.pem", tmp_path / "key.pem"
    certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    with running_seed(capture) as (server, _url):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, private)
        server.tls_context = context
        url = "https://127.0.0.1:" + str(server.server_port)
        with pytest.raises(PreservationError, match="no authenticated"):
            fetch(
                capture.identity,
                capture.trust,
                (url,),
                tmp_path / "untrusted",
                TransferPolicy(allow_private=True),
            )
        default_context = ssl.create_default_context
        monkeypatch.setattr(
            "foxlight_model_preservation.transfer.ssl.create_default_context",
            lambda: default_context(cafile=str(certificate)),
        )
        target = tmp_path / "trusted"
        fetch(
            capture.identity,
            capture.trust,
            (url,),
            target,
            TransferPolicy(allow_private=True),
        )
        verify_store(target, capture.identity, capture.trust)
