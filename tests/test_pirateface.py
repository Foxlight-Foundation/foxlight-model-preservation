"""Exact untrusted lookup, bounded metadata, and no implicit peer participation."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

import pytest
from test_cli_catalog import command

from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationCancelled, OperationControl
from foxlight_model_preservation.pirateface import (
    MAX_CANDIDATE_BYTES,
    lookup,
    read_candidate,
)
from foxlight_model_preservation.signing import digest, encode_record
from foxlight_model_preservation.transfer import TransferPolicy

HASH = "1" * 40
LOCAL = TransferPolicy(allow_http=True, allow_private=True, deadline_seconds=5)


def listing(**changes):
    return {
        "repoId": "test/weights",
        "revision": "main",
        "infohash": HASH,
        "magnet": "magnet:?xt=urn:btih:"
        + HASH
        + "&tr="
        + quote("http://127.0.0.1/private"),
        "files": [],
        "seeders": 17,
        **changes,
    }


@pytest.fixture
def service():
    state = {"body": json.dumps(listing()).encode(), "requests": [], "status": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"].append(("GET", self.path))
            self.send_response(state["status"])
            self.send_header("Content-Length", str(len(state["body"])))
            if state["status"] == 302:
                self.send_header("Location", "/must-not-follow")
            self.end_headers()
            try:
                self.wfile.write(state["body"])
            except (BrokenPipeError, ConnectionResetError):
                pass  # A bounded client may refuse before reading the test body.

        def do_POST(self):
            state["requests"].append(("POST", self.path))
            self.send_error(500)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    runner = threading.Thread(target=server.serve_forever, daemon=True)
    runner.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        runner.join(timeout=2)


def test_exact_lookup_only_records_untrusted_hints(service):
    source, state = service
    candidate = lookup("test/weights", source=source, policy=LOCAL)
    assert candidate.repo_id == "test/weights"
    assert candidate.revision_hint == "main"
    assert candidate.authority == "untrusted-candidate"
    assert candidate.magnet == "magnet:?xt=urn:btih:" + HASH
    assert candidate.response_sha256 == digest(state["body"])
    assert read_candidate(encode_record(candidate)) == candidate
    assert state["requests"] == [("GET", "/api/torrents?repo=test%2Fweights")]


@pytest.mark.parametrize(
    "changes",
    [
        {"repoId": "other/weights"},
        {"infohash": "bad"},
        {"revision": 5},
        {"revision": "main\n"},
        {"magnet": "magnet:?xt=urn:btih:" + "2" * 40},
        {"magnet": "magnet:?xt=urn:btih:" + HASH + "&xt=urn:btih:" + HASH},
        {"magnet": "https://evil.invalid"},
    ],
)
def test_lookup_refuses_substitution_and_ambiguous_metadata(service, changes):
    source, state = service
    state["body"] = json.dumps(listing(**changes)).encode()
    with pytest.raises(ValueError):
        lookup("test/weights", source=source, policy=LOCAL)
    assert len(state["requests"]) == 1


@pytest.mark.parametrize(
    "body",
    [
        b'{"repoId":"test/weights","repoId":"test/weights"}',
        b" " * (MAX_CANDIDATE_BYTES + 1),
    ],
)
def test_lookup_refuses_duplicate_and_oversized_json(service, body):
    source, state = service
    state["body"] = body
    with pytest.raises(ValueError):
        lookup("test/weights", source=source, policy=LOCAL)


def test_no_rescue_redirect_proxy_or_private_default(service, monkeypatch):
    source, state = service
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    for policy in (TransferPolicy(), TransferPolicy(allow_http=True)):
        with pytest.raises(PreservationError):
            lookup("test/weights", source=source, policy=policy)
    assert not state["requests"]
    for status in (404, 302, 206):
        state["status"] = status
        with pytest.raises(PreservationError):
            lookup("test/weights", source=source, policy=LOCAL)
    assert state["requests"] == [("GET", "/api/torrents?repo=test%2Fweights")] * 3


@pytest.mark.parametrize(
    "repo", ["../weights", "https://x/y", "a/b/c", "a/b?token=x", "a/b\n"]
)
def test_invalid_repository_fails_before_connect(service, repo):
    source, state = service
    with pytest.raises(PreservationError):
        lookup(repo, source=source, policy=LOCAL)
    assert not state["requests"]


def test_cancelled_lookup_starts_no_network(service):
    source, state = service
    control = OperationControl()
    control.cancel()
    with pytest.raises(OperationCancelled):
        lookup("test/weights", source=source, policy=LOCAL, control=control)
    assert not state["requests"]


def test_cancel_at_receipt_publication_never_reports_completion(service, tmp_path):
    source, _state = service
    events = []

    def cancel(progress):
        events.append(progress)
        if progress.stage == "publishing":
            control.cancel()

    control = OperationControl(cancel, interval_seconds=0)
    output = tmp_path / "not-published.json"
    with pytest.raises(OperationCancelled):
        lookup(
            "test/weights", source=source, policy=LOCAL, control=control, output=output
        )
    assert not output.exists()
    assert events[-1].stage == "cancelled"
    assert all(event.stage != "complete" for event in events)


def test_lookup_cli_is_standalone_and_never_replaces_candidate(service, tmp_path):
    source, state = service
    output = tmp_path / "candidate.json"
    arguments = (
        "pirateface",
        "lookup",
        "test/weights",
        "--source",
        source,
        "--allow-http",
        "--allow-private",
        "--output",
        str(output),
    )
    result = command(*arguments)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["authority"] == "untrusted-candidate"
    assert not receipt["sharing_started"]
    assert not receipt["peer_network_started"]
    original = output.read_bytes()
    result = command(*arguments)
    assert result.returncode == 1
    assert output.read_bytes() == original
