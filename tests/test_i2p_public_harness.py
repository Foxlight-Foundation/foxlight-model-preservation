"""Public qualification refuses absent grants and validates fixtures offline first."""

import base64
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from foxlight_model_preservation.discovery import (
    bootstrap_identity,
    follow_source,
    refresh_source_from_directory,
    search_sources,
    source_identity,
)
from foxlight_model_preservation.signing import encode_record
from foxlight_model_preservation.storage import manifest_path, restore

SCRIPT = Path(__file__).parents[1] / "scripts/qualify_i2p_http_public.py"


@pytest.mark.parametrize(
    "extra",
    [
        [],
        ["--allow-public-i2p", "--seconds", "1801"],
        ["--allow-public-i2p", "--kib", "129"],
    ],
)
def test_public_harness_refuses_before_files_or_tools(tmp_path, extra):
    target = tmp_path / "untouched"
    result = subprocess.run(  # noqa: S603 - fixed interpreter and synthetic paths
        [
            sys.executable,
            str(SCRIPT),
            "--directory",
            str(target),
            "--java-home",
            str(tmp_path / "absent-java"),
            "--i2p-source",
            str(tmp_path / "absent-source"),
            "--runtime",
            str(tmp_path / "absent-runtime"),
            *extra,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "bounded resources" in result.stderr
    assert not target.exists()


def test_generated_fixture_source_and_recovery_pass_without_network(tmp_path):
    spec = importlib.util.spec_from_file_location("public_qualification", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    identity, trust, original = module.fixture(tmp_path, 1)
    host = base64.b32encode(b"i" * 32).decode().rstrip("=").lower()
    bootstrap = module.publish(tmp_path, "http://" + host + ".b32.i2p", identity)
    cache = tmp_path / "cache"
    follow_source(cache, encode_record(bootstrap), bootstrap_identity(bootstrap))
    refresh_source_from_directory(
        cache, source_identity(bootstrap), tmp_path / "source"
    )
    assert search_sources(cache, "qualification")[0].manifest == identity
    restore(tmp_path / "model", identity, trust, tmp_path / "restored")
    assert (tmp_path / "model" / manifest_path(identity)).read_bytes() == original
    assert not (tmp_path / "weights").exists()
    assert (
        tmp_path / "restored/members/tokenizer/fixture.bin"
    ).read_bytes() == b"synthetic companion"


def observations_module():
    spec = importlib.util.spec_from_file_location("observed_qualification", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "fault, category, phase",
    [
        ("short", "short_body", "body_read"),
        ("range", "range_mismatch", "response_validation"),
        ("headers", "socket_timeout", "response_headers"),
        ("body", "socket_timeout", "body_read"),
    ],
)
def test_observed_fetch_preserves_verification_and_distinguishes_failures(
    capture, tmp_path, fault, category, phase
):
    import json
    import threading
    import time
    from urllib.parse import urlsplit

    from foxlight_model_preservation import transfer
    from foxlight_model_preservation.i2p_http import I2PHTTPProxy
    from foxlight_model_preservation.seed import SeedServer
    from foxlight_model_preservation.storage import object_path, verify_store
    from foxlight_model_preservation.transfer import I2PTransferPolicy

    module = observations_module()
    observations = module.HTTPObservations(tmp_path)
    file = max(
        verify_store(capture.store, capture.identity, capture.trust).members[0].files,
        key=lambda f: f.size,
    )
    object_url = "/" + object_path(file.sha256)
    triggered = threading.Event()

    class Proxy(observations.handler_class()):
        def do_GET(self):
            self.path = urlsplit(self.path).path.removeprefix("/PRIVATE_ROUTE_MARKER")
            if self.path == object_url and not triggered.is_set():
                triggered.set()
                if fault == "headers":
                    time.sleep(0.8)
                elif fault == "body":
                    self.send_response(206)
                    self.send_header("Content-Length", str(file.size - 500))
                    self.send_header(
                        "Content-Range", f"bytes 500-{file.size - 1}/{file.size}"
                    )
                    self.end_headers()
                    time.sleep(0.8)
                    return
                else:
                    self.send_response(206)
                    self.send_header("Content-Length", str(file.size - 500))
                    start = 499 if fault == "range" else 500
                    self.send_header(
                        "Content-Range", f"bytes {start}-{file.size - 1}/{file.size}"
                    )
                    self.end_headers()
                    if fault == "short":
                        self.wfile.write(
                            (capture.store / object_path(file.sha256)).read_bytes()[
                                500:821
                            ]
                        )
                    return
            super().do_GET()

    server = SeedServer(
        ("127.0.0.1", 0), capture.store, (capture.identity,), capture.trust
    )
    server.RequestHandlerClass = Proxy
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02))
    thread.start()
    target = tmp_path / "received"
    (target / ".partial").mkdir(parents=True)
    (target / ".partial" / file.sha256).write_bytes(
        (capture.store / object_path(file.sha256)).read_bytes()[:500]
    )
    original_download, original_response = transfer._download, transfer.response
    try:
        host = base64.b32encode(b"i" * 32).decode().rstrip("=").lower()
        with observations.client():
            value = transfer.fetch(
                capture.identity,
                capture.trust,
                (f"http://{host}.b32.i2p/PRIVATE_ROUTE_MARKER",),
                target,
                I2PTransferPolicy(
                    proxy=I2PHTTPProxy(port=server.server_port), timeout=0.2
                ),
            )
        assert value == verify_store(target, capture.identity, capture.trust)
        assert (target / manifest_path(capture.identity)).read_bytes() == (
            capture.store / manifest_path(capture.identity)
        ).read_bytes()
        assert transfer._download is original_download
        assert transfer.response is original_response
    finally:
        server.shutdown()
        thread.join(3)
        server.server_close()
        # Finish the finite injected slow handler before closing its event sink.
        time.sleep(0.85 if fault in {"headers", "body"} else 0)
        summary = observations.close()
    raw = (tmp_path / "transfer-events.jsonl").read_text()
    assert not any(
        secret in raw
        for secret in ("PRIVATE_ROUTE_MARKER", ".b32.i2p", "127.0.0.1", file.sha256)
    )
    events = [json.loads(line) for line in raw.splitlines()]
    errors = [item for item in events if item["event"] == "client_error"]
    assert len(errors) == 1
    assert errors[0]["category"] == category
    assert errors[0]["phase"] == phase
    assert summary["events_dropped"] == 0
    assert any(e["event"] == "client_request" and e["offset"] == 500 for e in events)
    assert any(e["event"] == "origin_end" and e["complete"] for e in events)


def test_observations_are_bounded_and_exception_values_are_never_recorded(tmp_path):
    import json

    observations = observations_module().HTTPObservations(tmp_path, limit=2)
    try:
        raise OSError("PRIVATE_EXCEPTION_VALUE http://secret.invalid/model")
    except OSError as error:
        observations.record("client_error", **observations.failure(error))
    observations.record("client_end", prefix=7)
    observations.record("client_end", prefix=9)
    summary = observations.close()
    raw = (tmp_path / "transfer-events.jsonl").read_text()
    assert "PRIVATE_EXCEPTION_VALUE" not in raw and "secret.invalid" not in raw
    assert len([json.loads(line) for line in raw.splitlines()]) == 2
    assert summary["events_dropped"] == 1
    assert (tmp_path / "transfer-events.jsonl").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("finish", [True, False])
def test_diagnostic_close_drains_origin_or_reports_incomplete_evidence(
    tmp_path, finish
):
    import json
    import threading
    import time

    observations = observations_module().HTTPObservations(tmp_path)
    observations.record("origin_start", request=1, expected_bytes=100)
    worker = None
    if finish:

        def complete():
            time.sleep(0.05)
            observations.record("origin_end", request=1, completed_write_bytes=100)

        worker = threading.Thread(target=complete)
        worker.start()
    summary = observations.close(wait_seconds=1 if finish else 0)
    if worker is not None:
        worker.join(2)
    assert summary["observed_origin_evidence_complete"] is finish
    assert summary["unfinished_origin_requests"] == ([] if finish else [1])
    events = [
        json.loads(s)
        for s in (tmp_path / "transfer-events.jsonl").read_text().splitlines()
    ]
    assert any(e["event"] == "origin_end" for e in events) is finish
    if not finish:
        observations.record("origin_end", request=1, completed_write_bytes=100)
        assert summary["observed_origin_evidence_complete"] is False


@pytest.mark.parametrize(
    "fault,category,phase",
    [
        ("headers", "socket_timeout", "response_headers"),
        ("body", "socket_timeout", "body_read"),
        ("status", "http_status", "response_status"),
        ("protocol", "http_protocol", "response_headers"),
    ],
)
def test_manifest_response_failures_remain_observable(
    capture, tmp_path, fault, category, phase
):
    import json
    import threading
    import time

    from foxlight_model_preservation import transfer
    from foxlight_model_preservation.i2p_http import I2PHTTPProxy
    from foxlight_model_preservation.models import PreservationError
    from foxlight_model_preservation.seed import SeedHandler, SeedServer
    from foxlight_model_preservation.transfer import I2PTransferPolicy

    observations = observations_module().HTTPObservations(tmp_path)

    class FailedManifest(SeedHandler):
        def do_GET(self):
            if fault == "headers":
                time.sleep(0.8)
                return
            if fault == "protocol":
                self.connection.sendall(b"PRIVATE_INVALID_STATUS\r\n\r\n")
                return
            self.send_response(503 if fault == "status" else 200)
            self.send_header("Content-Length", "0" if fault == "status" else "10")
            self.end_headers()
            if fault == "body":
                time.sleep(0.8)

    server = SeedServer(
        ("127.0.0.1", 0), capture.store, (capture.identity,), capture.trust
    )
    server.RequestHandlerClass = FailedManifest
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02))
    thread.start()
    original = transfer.response
    target = tmp_path / "not-published"
    try:
        host = base64.b32encode(b"i" * 32).decode().rstrip("=").lower()
        with (
            observations.client(),
            pytest.raises(PreservationError, match="no authenticated manifest"),
        ):
            transfer.fetch(
                capture.identity,
                capture.trust,
                (f"http://{host}.b32.i2p",),
                target,
                I2PTransferPolicy(
                    proxy=I2PHTTPProxy(port=server.server_port), timeout=0.2
                ),
            )
        assert transfer.response is original
        assert not target.exists()
    finally:
        server.shutdown()
        thread.join(3)
        server.server_close()
        summary = observations.close()
    raw = (tmp_path / "transfer-events.jsonl").read_text()
    assert "PRIVATE_INVALID_STATUS" not in raw and ".b32.i2p" not in raw
    errors = [
        json.loads(s)
        for s in raw.splitlines()
        if json.loads(s)["event"] == "client_error"
    ]
    assert len(errors) == 1
    assert errors[0]["kind"] == "manifest" and errors[0]["request"] == 1
    assert errors[0]["category"] == category and errors[0]["phase"] == phase
    assert summary["error_categories"][category] == 1
