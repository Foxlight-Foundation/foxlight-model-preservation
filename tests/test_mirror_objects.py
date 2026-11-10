"""Signed object coverage: which file hashes a mirror serves (phase 2)."""

from __future__ import annotations

import http.client
import random
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from conftest import Capture
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_catalog_mirror_fallback import _origin_down, _save, _seeds, _store
from test_local_owner import accepted_catalog

from foxlight_model_preservation.local_host.owner import LocalOwner
from foxlight_model_preservation.mirror_cards import build_peers, issue_card
from foxlight_model_preservation.mirror_directory import MirrorDirectory
from foxlight_model_preservation.mirror_objects import (
    OBJECTS_PATH,
    issue_objects,
    served_hashes,
    verify_objects,
)
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.seed import SeedServer
from foxlight_model_preservation.signing import digest, public_record
from foxlight_model_preservation.storage import object_path, verify_store
from foxlight_model_preservation.transfer import TransferPolicy

LAB = TransferPolicy(allow_http=True, allow_private=True, timeout=3)


def test_coverage_is_self_signed_short_lived_and_names_its_key() -> None:
    key = Ed25519PrivateKey.generate()
    held = {digest(str(index).encode()) for index in range(500)}
    document = issue_objects(key, held)
    claims = verify_objects(document, public_record(key))
    assert all(claims.covers(sha) for sha in held)
    others = [digest(f"other {index}".encode()) for index in range(2000)]
    assert sum(claims.covers(sha) for sha in others) < 60  # about one percent
    with pytest.raises(PreservationError, match="another mirror key"):
        verify_objects(document, public_record(Ed25519PrivateKey.generate()))
    expired = issue_objects(key, held, lifetime=60, now=int(time.time()) - 3600)
    with pytest.raises(PreservationError, match="expired"):
        verify_objects(expired, public_record(key))


def test_served_hashes_lists_only_object_names(tmp_path: Path) -> None:
    store = _store(tmp_path / "store", b"bytes")
    (store / "objects/sha256/.partial").write_bytes(b"")
    assert served_hashes(store) == {digest(b"bytes")}
    assert served_hashes(tmp_path / "missing") == set()


def _serve(store: Path, capture: Capture, documents: dict[str, Path]):
    server = SeedServer(("127.0.0.1", 0), store, (capture.identity,), capture.trust)
    server.documents = documents
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}"


def test_crawl_keeps_coverage_only_from_the_card_key(
    tmp_path: Path, capture: Capture
) -> None:
    store = tmp_path / "served"
    shutil.copytree(capture.store, store)
    weights = verify_store(store, capture.identity, capture.trust).members[0]
    sha = weights.files[0].sha256
    key = Ed25519PrivateKey.generate()
    peers, objects = tmp_path / "peers.json", tmp_path / "objects.json"
    server, thread, address = _serve(
        store,
        capture,
        {
            "/.well-known/sovereign/peers.json": peers,
            "/" + OBJECTS_PATH: objects,
        },
    )
    try:
        peers.write_bytes(
            build_peers(issue_card(key, (address,), {capture.identity}), ())
        )
        # Coverage signed by some other key is never kept.
        objects.write_bytes(issue_objects(Ed25519PrivateKey.generate(), {sha}))
        client = MirrorDirectory(tmp_path / "client", rng=random.Random(1))  # noqa: S311
        client.crawl((address,), LAB, expand=False)
        assert client.object_sources(sha, policy=LAB) == []
        objects.write_bytes(issue_objects(key, served_hashes(store)))
        client.crawl((address,), LAB, expand=False)
        assert client.object_sources(sha, policy=LAB) == [address]
        assert client.object_sources(digest(b"not held"), policy=LAB) == []
        # The default HTTPS-only policy never offers a plain-HTTP mirror.
        assert client.object_sources(sha) == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_head_reports_a_served_file_without_charging(
    tmp_path: Path, capture: Capture
) -> None:
    weights = verify_store(capture.store, capture.identity, capture.trust).members[0]
    item = weights.files[0]
    server, thread, address = _serve(capture.store, capture, {})
    try:
        before = server.reserved_upload_bytes
        parts = urlsplit(address)
        for path, status in (
            ("/" + object_path(item.sha256), 200),
            ("/" + object_path(digest(b"not held")), 404),
        ):
            connection = http.client.HTTPConnection(parts.hostname, parts.port)
            connection.request("HEAD", path)
            reply = connection.getresponse()
            assert reply.status == status
            if status == 200:
                assert reply.getheader("Content-Length") == str(item.size)
            connection.close()
        assert server.reserved_upload_bytes == before
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_a_save_asks_a_mirror_that_claims_the_file_before_seeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, repo, data = accepted_catalog(tmp_path, companion=False)
    _origin_down(monkeypatch)
    empty = tmp_path / "seed"
    empty.mkdir()
    _seeds(monkeypatch, empty)
    claimer = _store(tmp_path / "claimer", data)
    asked: list[str] = []
    outcomes: dict[str, bool] = {}
    monkeypatch.setattr(MirrorDirectory, "crawl", lambda *a, **k: 0)
    monkeypatch.setattr(
        MirrorDirectory,
        "object_sources",
        lambda self, sha, **k: asked.append(sha) or [str(claimer)],
    )
    monkeypatch.setattr(
        MirrorDirectory,
        "record_many",
        lambda self, results, **k: outcomes.update(results),
    )
    owner = LocalOwner(tmp_path / "owner", catalog=catalog)
    try:
        assert _save(owner, repo)["state"] == "succeeded"
    finally:
        owner.close()
    assert asked == [digest(data)]
    # The claiming mirror supplied it, so the seed was never asked.
    assert outcomes == {str(claimer): True}
