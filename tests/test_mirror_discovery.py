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


def test_newest_card_wins_and_a_replacement_needs_its_own_spot_check(
    tmp_path: Path,
) -> None:
    key = Ed25519PrivateKey.generate()
    older = issue_card(key, ("https://old.example",), set(), now=NOW - 100)
    newer = issue_card(key, ("https://new.example",), set(), now=NOW)
    directory = MirrorDirectory(tmp_path / "d")
    directory.ingest((newer,), now=NOW)
    directory.ingest((older,), now=NOW)  # A replayed older card never wins.
    (seen,) = directory.read().mirrors.values()
    assert encode_record(seen.card) == newer
    # Mark it checked, then a renewed card resets that.
    from foxlight_model_preservation import mirror_directory as module

    directory._update(  # noqa: SLF001 - direct state seeding for the test
        lambda mirrors: mirrors.update(
            {k: v.model_copy(update={"checked": True}) for k, v in mirrors.items()}
        ),
        now=NOW,
    )
    renewed = issue_card(key, ("https://moved.example",), set(), now=NOW + 10)
    directory.ingest((renewed,), now=NOW + 10)
    (seen,) = directory.read().mirrors.values()
    assert not seen.checked and directory.gossip(now=NOW + 10) == ()
    assert module.MAX_CARD_BYTES > len(renewed)


def test_directory_reclaims_expired_mirrors_and_stays_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from foxlight_model_preservation import mirror_directory as module

    monkeypatch.setattr(module, "MAX_MIRRORS", 2)
    directory = MirrorDirectory(tmp_path / "d")
    stale = [
        issue_card(Ed25519PrivateKey.generate(), ("https://a.example",), set(), now=NOW)
        for _ in range(2)
    ]
    assert directory.ingest(stale, now=NOW) == 2
    later = NOW + 8 * 86400  # Both cards have expired by now.
    fresh = issue_card(
        Ed25519PrivateKey.generate(), ("https://b.example",), set(), now=later
    )
    assert directory.ingest((fresh,), now=later) == 1
    # A tight byte budget evicts the least useful entries instead of failing.
    monkeypatch.setattr(module, "MAX_DIRECTORY_BYTES", 6000)
    monkeypatch.setattr(module, "MAX_MIRRORS", 64)
    many = [
        issue_card(
            Ed25519PrivateKey.generate(), ("https://c.example",), set(), now=later
        )
        for _ in range(20)
    ]
    directory.ingest(many, now=later)
    raw = (tmp_path / "d" / "directory.json").read_bytes()
    assert len(raw) <= 6000 and directory.read().mirrors


def test_a_trickling_peers_document_cannot_outlast_the_discovery_deadline(
    tmp_path: Path,
) -> None:
    import socket as sockets

    listener = sockets.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    stop = threading.Event()

    def trickle() -> None:
        connection, _ = listener.accept()
        connection.recv(4096)
        connection.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Length: 1000000\r\n"
            b"Content-Type: application/json\r\n\r\n"
        )
        while not stop.is_set():
            try:
                connection.sendall(b" ")
            except OSError:
                break
            time.sleep(0.2)
        connection.close()

    thread = threading.Thread(target=trickle, daemon=True)
    thread.start()
    started = time.monotonic()
    try:
        directory = MirrorDirectory(tmp_path / "d")
        policy = TransferPolicy(
            allow_http=True, allow_private=True, timeout=1, deadline_seconds=2
        )
        assert directory.crawl((f"http://127.0.0.1:{port}",), policy) == 0
    finally:
        stop.set()
        listener.close()
    assert time.monotonic() - started < 6


def test_mirrors_are_scored_by_what_they_actually_delivered(
    tmp_path: Path, capture: Capture
) -> None:
    identity = capture.identity
    corrupt = tmp_path / "corrupt"
    shutil.copytree(capture.store, corrupt)
    weights = verify_store(capture.store, identity, capture.trust).members[0]
    with (
        mirror(corrupt, (identity,), capture, tmp_path / "x.json") as bad,
        mirror(capture.store, (identity,), capture, tmp_path / "y.json") as good,
    ):
        target = corrupt / object_path(weights.files[0].sha256)
        target.chmod(0o600)
        target.write_bytes(b"x" * weights.files[0].size)
        directory = MirrorDirectory(tmp_path / "d")
        directory.ingest(
            (
                issue_card(Ed25519PrivateKey.generate(), (bad,), {identity}),
                issue_card(Ed25519PrivateKey.generate(), (good,), {identity}),
            )
        )
        fetch_discovered(
            identity,
            capture.trust,
            tmp_path / "received",
            directory,
            LAB,
            extra_sources=(bad, good),
        )
        scores = {
            verify_card(encode_record(seen.card)).addresses[0]: seen
            for seen in directory.read().mirrors.values()
        }
        assert scores[bad].failures >= 1 and scores[bad].successes == 0
        assert scores[good].successes >= 1 and scores[good].failures == 0


def test_spot_check_results_merge_with_cards_learned_meanwhile(
    tmp_path: Path, capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from foxlight_model_preservation import mirror_directory as module

    identity = capture.identity
    directory = MirrorDirectory(tmp_path / "d")
    with mirror(capture.store, (identity,), capture, tmp_path / "p.json") as honest:
        directory.ingest(
            (issue_card(Ed25519PrivateKey.generate(), (honest,), {identity}),)
        )
        learned = issue_card(
            Ed25519PrivateKey.generate(), ("https://learned.example",), set()
        )
        real = module._bounded_read  # noqa: SLF001

        def read_and_learn(*args: object, **kwargs: object) -> bytes:
            # A download learns a new card while the spot-check is in flight.
            directory.ingest((learned,))
            return real(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(module, "_bounded_read", read_and_learn)
        assert directory.spot_check({identity}, LAB) == 1
    cards = {encode_record(seen.card) for seen in directory.read().mirrors.values()}
    assert learned in cards and len(cards) == 2
