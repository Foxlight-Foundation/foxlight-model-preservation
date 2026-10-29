"""Decentralized discovery: signed mirror cards, gossip and untrusted sampling."""

from __future__ import annotations

import random
import shutil
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest
from conftest import Capture
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from foxlight_model_preservation.mirror_cards import (
    MirrorCard,
    build_peers,
    coverage_filter,
    issue_card,
    read_peers,
    verify_card,
)
from foxlight_model_preservation.mirror_directory import (
    MirrorDirectory,
    fetch_discovered,
)
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.seed import SeedServer
from foxlight_model_preservation.signing import Envelope, encode_record
from foxlight_model_preservation.storage import (
    manifest_path,
    object_path,
    verify_store,
)
from foxlight_model_preservation.transfer import TransferPolicy

LAB = TransferPolicy(allow_http=True, allow_private=True, timeout=3)
NOW = int(time.time())


def test_cards_are_self_certifying_short_lived_and_grant_nothing() -> None:
    key = Ed25519PrivateKey.generate()
    raw = issue_card(key, ("https://mirror.example/",), {"a" * 64}, now=NOW)
    card = verify_card(raw, now=NOW)
    assert card.addresses == ("https://mirror.example",)
    assert card.covers("a" * 64)
    # Changing anything in a signed card breaks its own signature.
    envelope = Envelope.model_validate_json(raw)
    forged = issue_card(
        Ed25519PrivateKey.generate(), ("https://evil.example",), {"a" * 64}, now=NOW
    )
    swapped = envelope.model_copy(
        update={"payload": Envelope.model_validate_json(forged).payload}
    )
    with pytest.raises(PreservationError):
        verify_card(encode_record(swapped), now=NOW)
    with pytest.raises(PreservationError, match="expired"):
        verify_card(raw, now=NOW + 8 * 86400)
    with pytest.raises(PreservationError, match="future"):
        verify_card(raw, now=NOW - 3600)
    with pytest.raises(ValueError):
        issue_card(key, ("https://m.example",), set(), lifetime=30 * 86400, now=NOW)
    for address in ("ftp://m.example", "https://u:p@m.example", "https://m.example/?q"):
        with pytest.raises(ValueError):
            issue_card(key, (address,), set(), now=NOW)


def test_coverage_filter_has_no_false_negatives_and_few_false_positives() -> None:
    held = {f"{index:064x}" for index in range(500)}
    coverage, count = coverage_filter(held)
    card = MirrorCard.model_validate(
        {
            "mirror_key": {
                "key_id": "ed25519:" + "0" * 64,
                "public_key": "A" * 43 + "=",
            },
            "addresses": ("https://m.example",),
            "coverage": coverage,
            "coverage_count": count,
            "issued_at": NOW,
            "expires_at": NOW + 60,
        }
    )
    assert all(card.covers(item) for item in held)
    others = [f"{index:064x}" for index in range(10_000, 12_000)]
    assert sum(card.covers(item) for item in others) < 2_000 * 0.03


def test_peers_document_keeps_only_cards_that_verify() -> None:
    good = issue_card(Ed25519PrivateKey.generate(), ("https://a.example",), set())
    other = issue_card(Ed25519PrivateKey.generate(), ("https://b.example",), set())
    stale = issue_card(
        Ed25519PrivateKey.generate(),
        ("https://c.example",),
        set(),
        now=NOW - 30 * 86400,
    )
    document = build_peers(good, (other, stale))
    assert read_peers(document) == [good, other]


@contextmanager
def mirror(
    store: Path, identities: tuple[str, ...], capture: Capture, peers: Path
) -> Generator[str]:
    server = SeedServer(("127.0.0.1", 0), store, identities, capture.trust)
    server.documents = {"/.well-known/sovereign/peers.json": peers}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_fresh_client_finds_and_fetches_through_gossip_without_any_seed_list(
    tmp_path: Path, capture: Capture
) -> None:
    """A lab network: A vouches for B, B for C. The client knows only A."""
    identity = capture.identity
    stores = {}
    for name in ("a", "b", "c"):
        stores[name] = tmp_path / name
        shutil.copytree(capture.store, stores[name])
    peers = {name: tmp_path / f"{name}-peers.json" for name in stores}
    keys = {name: Ed25519PrivateKey.generate() for name in stores}
    with (
        mirror(stores["a"], (identity,), capture, peers["a"]) as a,
        mirror(stores["b"], (identity,), capture, peers["b"]) as b,
        mirror(stores["c"], (identity,), capture, peers["c"]) as c,
    ):
        cards = {
            name: issue_card(keys[name], (address,), {identity})
            for name, address in (("a", a), ("b", b), ("c", c))
        }
        # Fake mirrors cost bounded attempts: one is dead, one serves bytes
        # that fail verification under the same signed identity.
        dead = issue_card(
            Ed25519PrivateKey.generate(), ("http://127.0.0.1:9",), {identity}
        )
        liar_store = tmp_path / "liar"
        shutil.copytree(capture.store, liar_store)
        weights = verify_store(capture.store, identity, capture.trust).members[0]
        with mirror(liar_store, (identity,), capture, tmp_path / "none.json") as liar:
            # After its server starts, the liar swaps in bytes of the same size.
            target = liar_store / object_path(weights.files[0].sha256)
            target.chmod(0o600)
            target.write_bytes(b"x" * weights.files[0].size)
            fake = issue_card(Ed25519PrivateKey.generate(), (liar,), {identity})
            peers["a"].write_bytes(build_peers(cards["a"], (cards["b"], dead, fake)))
            peers["b"].write_bytes(build_peers(cards["b"], (cards["c"],)))
            peers["c"].write_bytes(build_peers(cards["c"], ()))

            client = MirrorDirectory(tmp_path / "client", rng=random.Random(7))  # noqa: S311 - test sampling
            assert client.crawl((a,), LAB) == 5
            known = {
                address
                for seen in client.read().mirrors.values()
                for address in verify_card(encode_record(seen.card)).addresses
            }
            assert {a, b, c, liar} <= known
            # The seed itself goes away; the client's own directory remains.
            peers["a"].unlink()
            shutil.rmtree(stores["a"])
            received = tmp_path / "received"
            manifest = fetch_discovered(identity, capture.trust, received, client, LAB)
            assert manifest == verify_store(received, identity, capture.trust)
            scores = {
                verify_card(encode_record(seen.card)).addresses[0]: seen
                for seen in client.read().mirrors.values()
            }
            assert scores["http://127.0.0.1:9"].failures >= 1
            assert scores[a].failures >= 1
            assert scores[b].successes + scores[c].successes >= 1
            # Repeated failures drop a mirror from future candidates.
            for _ in range(3):
                client.record("http://127.0.0.1:9", delivered=False)
            assert "http://127.0.0.1:9" not in client.candidates(identity)


def test_spot_check_vouches_only_for_mirrors_that_serve_matching_bytes(
    tmp_path: Path, capture: Capture
) -> None:
    identity = capture.identity
    with mirror(capture.store, (identity,), capture, tmp_path / "peers.json") as honest:
        empty = tmp_path / "empty"
        shutil.copytree(capture.store, empty)
        with mirror(empty, (identity,), capture, tmp_path / "none.json") as hollow:
            # It claims the model but no longer holds its manifest.
            (empty / manifest_path(identity)).unlink()
            directory = MirrorDirectory(tmp_path / "checker")
            good = issue_card(Ed25519PrivateKey.generate(), (honest,), {identity})
            bad = issue_card(Ed25519PrivateKey.generate(), (hollow,), {identity})
            directory.ingest((good, bad))
            assert directory.spot_check({identity}, LAB) == 1
            assert directory.gossip() == (good,)
    assert (capture.store / manifest_path(identity)).is_file()
