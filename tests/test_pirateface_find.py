"""Pirate Face in Find: bounded search, exact entries, verified torrent files."""

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.pirateface_find import (
    lookup,
    match_files,
    parse_torrent,
    request_rescue,
    search,
    torrent,
)
from foxlight_model_preservation.transfer import TransferPolicy

LOCAL = TransferPolicy(allow_http=True, allow_private=True, deadline_seconds=5)
COMMIT = "1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0"


def bencode(value) -> bytes:
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, list):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    return (
        b"d"
        + b"".join(
            bencode(key) + bencode(value[key]) for key in sorted(value, key=str.encode)
        )
        + b"e"
    )


def make_torrent(files, *, url_list=None) -> tuple[bytes, str]:
    info = {
        "files": [{"length": size, "path": path.split("/")} for path, size in files],
        "name": "Qwen__Qwen-Test",
        "piece length": 16384,
        "pieces": b"\0" * 20,
    }
    top = {"announce": "udp://tracker.pirateface.co:6969/announce", "info": info}
    if url_list:
        top["url-list"] = url_list
    return bencode(top), hashlib.sha1(bencode(info)).hexdigest()


TORRENT, INFOHASH = make_torrent(
    [("config.json", 10), ("model.safetensors", 1000), ("crc32.txt", 5)],
    url_list=[f"https://pirateface.co/api/ws/Qwen/Qwen-Test/{COMMIT}/"],
)
SEARCH = (
    f"Qwen/Qwen-Test{' ' * 40}56 GB  118 seeds  magnet:?xt=urn:btih:{INFOHASH}"
    "&tr=udp%3A%2F%2Ftracker.pirateface.co%3A6969%2Fannounce"
    f"&ws=https%3A%2F%2Fpirateface.co%2Fapi%2Fws%2FQwen%2FQwen-Test%2F{COMMIT}%2F\n"
    f"someone/rescued{' ' * 30}1.5 GB  1 seed  magnet:?xt=urn:btih:{'2' * 40}\n"
    "not a result line\n"
)


@pytest.fixture
def service():
    state = {"requests": [], "entry": None}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, body):
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            state["requests"].append(("GET", self.path))
            if self.path.startswith("/s/"):
                return self._send(200, SEARCH.encode())
            if self.path.startswith("/api/torrents?"):
                entry = state["entry"]
                if entry is None:
                    return self._send(404, b"{}")
                return self._send(200, json.dumps(entry).encode())
            if self.path == f"/api/torrents/{INFOHASH}.torrent":
                return self._send(200, TORRENT)
            return self._send(404, b"")

        def do_POST(self):
            length = int(self.headers["Content-Length"])
            state["requests"].append(("POST", self.path, self.rfile.read(length)))
            self._send(200, b"{}")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    runner = threading.Thread(target=server.serve_forever, daemon=True)
    runner.start()
    try:
        yield server.server_port, state
    finally:
        server.shutdown()
        server.server_close()
        runner.join(timeout=2)


def test_search_parses_listed_torrents_and_their_pinned_commit(service):
    port, state = service
    results = search("qwen  test", source=f"http://127.0.0.1:{port}", policy=LOCAL)
    assert state["requests"] == [("GET", "/s/qwen+test")]
    assert [item.repository for item in results] == [
        "Qwen/Qwen-Test",
        "someone/rescued",
    ]
    first = results[0].view()
    assert first["seeds"] == 118 and first["size_bytes"] == 56 * 10**9
    assert first["revision"] == COMMIT and first["infohash"] == INFOHASH
    assert results[1].revision is None and results[1].seeds == 1
    assert search("   ", source=f"http://127.0.0.1:{port}", policy=LOCAL) == []


def test_lookup_reads_an_exact_entry_and_unlisted_models(service):
    port, state = service
    source = f"http://127.0.0.1:{port}"
    assert lookup("Qwen/Qwen-Test", source=source, policy=LOCAL) is None
    state["entry"] = {
        "repoId": "Qwen/Qwen-Test",
        "revision": COMMIT,
        "seeders": 3,
        "leechers": 1,
        "hf": False,
        "infohash": INFOHASH,
        "torrent": f"/api/torrents/{INFOHASH}.torrent",
        "files": [{"path": "model.safetensors", "sha256": "a" * 64}],
    }
    entry = lookup("Qwen/Qwen-Test", source=source, policy=LOCAL)
    assert entry is not None and entry.on_hub is False and entry.revision == COMMIT
    assert entry.hashes == (("model.safetensors", "a" * 64),)
    # Another repository's entry is never accepted for this one.
    state["entry"] = {**state["entry"], "repoId": "other/model"}
    with pytest.raises(PreservationError, match="pirate_face_response_invalid"):
        lookup("Qwen/Qwen-Test", source=source, policy=LOCAL)
    # Listed but no magnet served (e.g. terms to accept): not a source here.
    state["entry"] = {"repoId": "Qwen/Qwen-Test", "infohash": None}
    assert lookup("Qwen/Qwen-Test", source=source, policy=LOCAL) is None


def test_torrent_is_accepted_only_with_its_expected_infohash(service):
    port, state = service
    state["entry"] = {
        "repoId": "Qwen/Qwen-Test",
        "revision": "main",
        "infohash": INFOHASH,
        "torrent": f"/api/torrents/{INFOHASH}.torrent",
    }
    source = f"http://127.0.0.1:{port}"
    entry = lookup("Qwen/Qwen-Test", source=source, policy=LOCAL)
    assert entry is not None and entry.revision is None  # "main" is not pinned
    swarm = torrent(entry, source=source, policy=LOCAL)
    assert swarm.revision == COMMIT  # the web-seed names the exact commit
    assert [item.path for item in swarm.files] == [
        "config.json",
        "model.safetensors",
        "crc32.txt",
    ]
    assert match_files(swarm, [("model.safetensors", 1000), ("config.json", 11)]) == {
        "model.safetensors": "model.safetensors"
    }
    with pytest.raises(PreservationError, match="pirate_face_torrent_differs"):
        parse_torrent(TORRENT, "f" * 40, "Qwen/Qwen-Test")


@pytest.mark.parametrize(
    "files",
    [
        [("../escape", 1)],
        [("a//b", 1)],
        [("same", 1), ("same", 2)],
    ],
)
def test_unsafe_torrent_paths_are_refused(files):
    data, infohash = make_torrent(files)
    with pytest.raises(PreservationError, match="pirate_face_torrent_invalid"):
        parse_torrent(data, infohash, "Qwen/Qwen-Test")


def test_malformed_bencode_is_refused():
    for data in (b"d4:infoi1e", b"di1e1:ae", b"d1:b1:x1:a1:ye", b"i01e"):
        with pytest.raises(PreservationError):
            parse_torrent(data, "0" * 40, "Qwen/Qwen-Test")


def test_rescue_sends_only_the_repository(service):
    port, state = service
    request_rescue(
        "gone-org/model", host="127.0.0.1", port=port, policy=LOCAL, tls=False
    )
    assert state["requests"] == [
        ("POST", "/api/rescue", json.dumps({"repoId": "gone-org/model"}).encode())
    ]
