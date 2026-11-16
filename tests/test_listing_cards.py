"""Listing cards: how people learn of computers that share with no address."""

from __future__ import annotations

import secrets
import threading
import time
from pathlib import Path

import pytest
from conftest import Capture
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from foxlight_model_preservation.mirror_cards import (
    build_peers,
    issue_card,
    issue_listing_card,
    read_listing_cards,
    read_peers,
    verify_card,
    verify_listing_card,
)
from foxlight_model_preservation.mirror_directory import MirrorDirectory
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.nostr import (
    LISTING_TOPIC,
    card_event,
    card_from_event,
)
from foxlight_model_preservation.seed import SeedServer
from foxlight_model_preservation.signing import public_record
from foxlight_model_preservation.transfer import TransferPolicy

HELD = frozenset({"a" * 64, "b" * 64})
LAB = TransferPolicy(allow_http=True, allow_private=True, timeout=3)


def test_a_listing_card_names_its_key_and_what_it_probably_holds() -> None:
    key = Ed25519PrivateKey.generate()
    card = verify_listing_card(issue_listing_card(key, HELD))
    assert card.mirror_key == public_record(key)
    assert card.covers("a" * 64) and card.covers("b" * 64)
    assert card.coverage_count == 2
    # It is not a mirror card, and a mirror card is not a listing card.
    with pytest.raises(PreservationError):
        verify_card(issue_listing_card(key, HELD))
    with pytest.raises(PreservationError):
        verify_listing_card(issue_card(key, ("https://m.example.org",), HELD))


def test_a_listing_card_expires_and_is_never_dated_ahead() -> None:
    key = Ed25519PrivateKey.generate()
    data = issue_listing_card(key, HELD, lifetime=60, now=1_000_000)
    assert verify_listing_card(data, now=1_000_030)
    with pytest.raises(PreservationError):
        verify_listing_card(data, now=1_000_060)
    with pytest.raises(PreservationError):
        verify_listing_card(data, now=1_000_000 - 3600)
    with pytest.raises(ValueError, match="at most 14 days"):
        issue_listing_card(key, HELD, lifetime=15 * 86400)


def test_a_peers_document_relays_listing_cards_that_older_apps_skip() -> None:
    mirror = issue_card(Ed25519PrivateKey.generate(), ("https://m.example.org",), HELD)
    listing = issue_listing_card(Ed25519PrivateKey.generate(), HELD)
    document = build_peers(mirror, (listing,))
    # What every app reads: only mirror cards (an older app sees just that).
    assert read_peers(document) == [mirror]
    # What this version also reads: the relayed listing card.
    assert read_listing_cards(document) == [listing]


def test_nostr_carries_listing_cards_under_their_own_topic() -> None:
    key = Ed25519PrivateKey.generate()
    secret = secrets.token_bytes(32)
    listing = issue_listing_card(key, HELD)
    event = card_event(listing, secret)
    tags = event["tags"]
    assert ["t", LISTING_TOPIC] in tags  # type: ignore[operator]
    assert ["d", "listing:" + public_record(key).key_id] in tags  # type: ignore[operator]
    assert card_from_event(event, topic=LISTING_TOPIC) == listing
    # Asked for mirror cards, an app never gets a listing card, nor the reverse.
    assert card_from_event(event) is None
    mirror = card_event(issue_card(key, ("https://m.example.org",), HELD), secret)
    assert card_from_event(mirror, topic=LISTING_TOPIC) is None


def test_the_directory_keeps_the_newest_live_card_per_key_and_relays_them(
    tmp_path: Path,
) -> None:
    store = MirrorDirectory(tmp_path / "directory")
    key = Ed25519PrivateKey.generate()
    now = int(time.time())
    older = issue_listing_card(key, HELD, now=now - 100)
    newer = issue_listing_card(key, frozenset({"c" * 64}), now=now - 50)
    other = issue_listing_card(Ed25519PrivateKey.generate(), HELD, now=now)
    lapsed = issue_listing_card(
        Ed25519PrivateKey.generate(), HELD, lifetime=60, now=now - 3600
    )
    assert store.ingest_listings([newer, older, other, lapsed, b"junk"], now=now) == 2
    assert set(store.listing_cards(now=now)) == {newer, other}
    # A card older than the one kept never replaces it.
    assert store.ingest_listings([older], now=now) == 0
    assert newer in store.listing_cards(now=now)
    # Relayed in peers documents after any mirror cards, within the limit.
    assert set(store.gossip(now=now)) == {newer, other}
    assert store.gossip(limit=1, now=now) in ((newer,), (other,))


def test_crawling_a_peers_document_learns_the_listing_cards_it_relays(
    tmp_path: Path, capture: Capture
) -> None:
    identity = capture.identity
    peers = tmp_path / "peers.json"
    server = SeedServer(("127.0.0.1", 0), capture.store, (identity,), capture.trust)
    server.documents = {"/.well-known/sovereign/peers.json": peers}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    listing = issue_listing_card(Ed25519PrivateKey.generate(), HELD)
    peers.write_bytes(
        build_peers(
            issue_card(
                Ed25519PrivateKey.generate(), (f"http://127.0.0.1:{port}",), {identity}
            ),
            (listing,),
        )
    )
    try:
        store = MirrorDirectory(tmp_path / "directory")
        store.crawl((f"http://127.0.0.1:{port}",), LAB)
        assert store.listing_cards() == [listing]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_a_computer_sharing_with_no_address_publishes_its_listing_card(
    tmp_path: Path, capture: Capture
) -> None:
    pytest.importorskip("libtorrent", reason="optional native BitTorrent extra")
    from test_local_backups import saved
    from test_local_community import enable, free_port, served, wait_for

    from foxlight_model_preservation.local_host.owner import LocalOwner
    from foxlight_model_preservation.sovereign_listing import current_listing

    port = free_port()
    owner = LocalOwner(tmp_path / "owner")
    try:
        saved(owner, tmp_path, capture)
        enable(owner, port)
        identity = wait_for(
            lambda: next(
                (
                    row["public_manifest_id"]
                    for row in owner.control("community-read", {})["copies"]
                    if row["status"] == "sharing"
                ),
                None,
            )
        )
        wait_for(lambda: served(port, identity) == 200)
        community = owner.community
        assert community.addresses() == ()

        def published() -> bytes | None:
            before = current_listing(community._mirror())
            community.announce()
            if before is None and current_listing(community._mirror()) is not None:
                # The announcement that published the listing carries its card.
                assert community.listing_card is not None
            return community.listing_card

        raw = wait_for(published, seconds=60)
        card = verify_listing_card(raw)
        assert card.mirror_key == public_record(community._mirror_key())
        assert card.covers(identity)
        # Its own peers document carries it (for a bucket, or any reader).
        document = (community._mirror() / "peers.json").read_bytes()
        assert read_listing_cards(document) == [raw]
        assert read_peers(document) == []  # no address, so no mirror card
    finally:
        owner.close()


def test_kept_listing_cards_stay_within_a_byte_budget_and_a_bad_file_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from foxlight_model_preservation import mirror_directory

    store = MirrorDirectory(tmp_path / "directory")
    now = int(time.time())
    cards = [
        issue_listing_card(Ed25519PrivateKey.generate(), HELD, now=now)
        for _ in range(6)
    ]
    one = len(cards[0])
    monkeypatch.setattr(mirror_directory, "MAX_LISTINGS_BYTES", one * 3)
    for index, card in enumerate(cards):
        store.ingest_listings([card], now=now + index)
    kept = store.listing_cards(now=now + 10)
    # The most recently seen fit; the file stays readable.
    assert 0 < len(kept) < len(cards) and kept[0] == cards[-1]
    assert (tmp_path / "directory" / "listings.json").stat().st_size < one * 4
    # A damaged file never stops discovery: it reads as empty and is rewritten.
    (tmp_path / "directory" / "listings.json").write_bytes(b"{not json")
    assert store.listing_cards(now=now) == []
    assert store.ingest_listings([cards[0]], now=now) == 1
    assert store.listing_cards(now=now) == [cards[0]]
