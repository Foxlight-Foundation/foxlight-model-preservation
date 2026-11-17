"""Announcement for a standalone mirror: its card, peers, listing and gossip.

An announcing mirror signs a short-lived mirror card with its own mirror key
(never a model or source key), naming its declared public URL and a coverage
filter of the verified public selections it serves. It serves that card and
the peers it has itself spot-checked at ``/.well-known/sovereign/peers.json``.
Publishing the card to Nostr relays is on by default for a mirror with a
public URL (it publishes that address anyway); announcing on the BitTorrent
DHT is a separate opt-in. A mirror sharing its files over BitTorrent also
publishes its listing and a listing card, carried in its peers document and
on the same relays. This is not anonymous: the mirror's address and the
models it probably holds become public, and every peer it contacts sees its IP.
Cards grant no trust; every byte is still verified against chosen model keys.
"""

from __future__ import annotations

import contextlib
import os
import secrets
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .discovery import list_sources, search_sources
from .mirror_cards import MAX_PEERS_BYTES, base_url, build_peers, issue_card
from .mirror_directory import MirrorDirectory
from .mirror_holdings import MAX_HOLDINGS_BYTES, issue_holdings, store_holdings
from .mirror_listing import (
    LISTING_DOCUMENTS,
    mirror_listing_card,
    publish_mirror_listing,
)
from .mirror_models import AnnouncePolicy
from .mirror_objects import MAX_OBJECTS_BYTES, issue_objects, served_hashes
from .mirror_rendezvous import Rendezvous, rendezvous_available
from .mirror_state import MirrorLedger
from .models import MAX_METADATA_BYTES, PreservationError
from .nostr import DEFAULT_RELAYS, LISTING_TOPIC, find_cards, publish_card
from .operations import OperationControl
from .pirateface_seed import issue_torrents, seeded
from .signing import create_key, load_key, public_record
from .sovereign_torrents import listed
from .storage import atomic_bytes, read_metadata
from .transfer import TransferPolicy

# Conservative per-round ingress charge for peers documents, relay replies and
# spot-check manifests. Gossip is skipped when the daily budget cannot cover it.
# 64 MiB for peers, coverage, spot checks and relays, plus each crawled
# mirror's holdings list (CRAWL_DOCUMENTS of up to 1 MiB).
GOSSIP_RESERVATION = 72 * 1024**2
# Every read in a round is capped so their bodies fit that reservation:
# peers documents, spot-check manifests (two addresses each) and relay frames.
# The remainder covers HTTP response headers; DHT datagrams are not counted.
CRAWL_DOCUMENTS = 8
SPOT_CHECKS = 2
RELAY_FIND_BYTES = 10 * 1024**2
# Listing cards (computers sharing over BitTorrent), read from the same relays.
LISTING_FIND_BYTES = 2 * 1024**2
RELAY_PUBLISH_BYTES = 6 * 1024**2
ROUND_BODY_BYTES = (
    CRAWL_DOCUMENTS * (MAX_PEERS_BYTES + 1)
    # Each crawled mirror's signed object coverage is read beside its peers.
    + CRAWL_DOCUMENTS * (MAX_OBJECTS_BYTES + 1)
    # ... and its signed holdings list.
    + CRAWL_DOCUMENTS * (MAX_HOLDINGS_BYTES + 1)
    + SPOT_CHECKS * 2 * (MAX_METADATA_BYTES + 1)
    + RELAY_FIND_BYTES
    + LISTING_FIND_BYTES
    + RELAY_PUBLISH_BYTES
)
MAX_SEEDS = 32
KEY_FILE = "mirror-key.pem"
NOSTR_FILE = "nostr-key"
DIRECTORY = "mirror-directory"
DISCLOSURE = (
    "Announcing publishes this mirror's public URL and a filter of the models it "
    "probably holds. Nostr publishing copies that card to third-party relays, "
    "which see this server's address (the card publishes it anyway); DHT "
    "announcing tells the public BitTorrent DHT this address and port. "
    "This is not anonymous: anyone can see and contact the address."
)


def relays_for(policy: AnnouncePolicy) -> tuple[str, ...]:
    """The relays this mirror posts to and reads from: none when Nostr is off."""
    if not policy.nostr:
        return ()
    return policy.relays or DEFAULT_RELAYS


def mirror_key(ledger: MirrorLedger) -> Ed25519PrivateKey:
    """Load or create this mirror's own owner-only card key in its root."""
    path = ledger.path / KEY_FILE
    if not path.exists():
        with ledger.root():
            create_key(path, None)
    return load_key(path, None)


def mirror_key_id(ledger: MirrorLedger) -> str:
    """Return the public fingerprint of this mirror's card key."""
    return public_record(mirror_key(ledger)).key_id


def dht_port(policy: AnnouncePolicy) -> int | None:
    """The public port a DHT announcement could name, or None to stay off it.

    The DHT records only the observed address and a port, so it can express a
    plain-HTTP root URL only. An HTTPS URL behind a reverse proxy relies on
    cards, gossip and (optionally) relays instead.
    """
    if not policy.dht:
        return None
    parsed = urlsplit(policy.public_url)
    if parsed.scheme != "http" or parsed.path not in {"", "/"}:
        return None
    return parsed.port or 80


def catalog_mirrors(ledger: MirrorLedger) -> tuple[str, ...]:
    """Public HTTP(S) mirror hints named by followed catalogs, as gossip seeds."""
    followed = set(ledger.read().sources)
    seeds: dict[str, None] = {}
    for result in search_sources(ledger.path / "discovery", "", limit=1000):
        for location in result.locations:
            if location.source_id not in followed:
                continue
            for hint in location.mirrors:
                try:
                    seeds[base_url(hint)] = None
                except PreservationError:
                    continue
    return tuple(seeds)[:MAX_SEEDS]


class Announcer:
    """Own one announcing mirror's directory, card and optional rendezvous."""

    def __init__(self, ledger: MirrorLedger, *, lists: bool = True) -> None:
        """``lists`` is False when an embedding app publishes the listing for
        this mirror's files with its own key; this announcer then leaves it."""
        self.ledger = ledger
        self.lists = lists
        self.directory = MirrorDirectory(ledger.path / DIRECTORY)
        self.rendezvous: Rendezvous | None = None
        # The listing card for the listing this mirror shares, when it shares
        # its files over BitTorrent.
        self.listing_card: bytes | None = None

    def _nostr_key(self) -> bytes:
        with self.ledger.root() as root:
            try:
                secret = read_metadata(root, NOSTR_FILE)
            except FileNotFoundError:
                atomic_bytes(root, NOSTR_FILE, secrets.token_bytes(32))
                secret = read_metadata(root, NOSTR_FILE)
        if len(secret) != 32:
            raise PreservationError("mirror nostr key is invalid")
        return secret

    def _documents(self, serving: frozenset[str]) -> dict[str, bytes | None]:
        """The signed object coverage, holdings and torrents documents for
        ``serving``. Each is optional: a problem with one never stops the card
        renewing or the listing being shared."""
        documents: dict[str, bytes | None] = dict.fromkeys(LISTING_DOCUMENTS)
        key = mirror_key(self.ledger)
        # Signed by the same key: lets catalog saves find files by hash.
        with contextlib.suppress(PreservationError, OSError):
            documents["objects.json"] = issue_objects(
                key, served_hashes(self.ledger.path / "public")
            )
        # What this mirror keeps, by name, so Find can search it.
        with contextlib.suppress(PreservationError, OSError):
            documents["holdings.json"] = issue_holdings(
                key, store_holdings(self.ledger.path / "public", serving)
            )
        # The torrents it shares (Sovereign's own per-file ones and Pirate
        # Face ones), so their swarms stay findable from mirrors.
        with contextlib.suppress(PreservationError, OSError, ValueError):
            documents["torrents.json"] = issue_torrents(
                key,
                [
                    *seeded(self.ledger.read(), serving),
                    *listed(self.ledger, serving),
                ],
            )
        return documents

    def share_listing(self, serving: frozenset[str]) -> None:
        """Keep the listing in step for a mirror with no public URL.

        A mirror sharing its files over BitTorrent lists them under its key
        (the listing its session shares, and the DHT pointer to it) whether
        or not it announces an address; without one there is no card, so the
        listing card is not issued. Withdrawn when files aren't shared; kept
        as it is while nothing is served.
        """
        self.listing_card = None
        if not self.lists:
            return
        native = self.ledger.read().native_policy
        if native is None or not native.file_torrents:
            publish_mirror_listing(self.ledger.path, None, serving)
        elif serving:
            publish_mirror_listing(self.ledger.path, self._documents(serving), serving)

    def publish(self, serving: frozenset[str]) -> bytes | None:
        """Write the peers document: this mirror's fresh card, then checked peers.

        No card is issued while nothing is served; peers are still listed.
        A mirror sharing its files over BitTorrent also publishes its listing
        and a listing card for it, carried in the peers document.
        """
        state = self.ledger.read()
        policy = state.announce
        if policy is None:
            return None
        catalogs = tuple(
            sorted(
                {
                    status.catalog_id
                    for status in list_sources(self.ledger.path / "discovery")
                    if status.source_id in state.sources
                    and status.catalog_id is not None
                    and status.freshness == "fresh"
                }
            )
        )[:64]
        card = (
            issue_card(
                mirror_key(self.ledger),
                (policy.public_url,),
                serving,
                catalogs=catalogs,
                contact=policy.contact,
            )
            if serving
            else None
        )
        native = state.native_policy
        sharing_files = self.lists and native is not None and native.file_torrents
        documents: dict[str, bytes | None] = (
            self._documents(serving)
            if card is not None
            else dict.fromkeys(LISTING_DOCUMENTS)
        )
        # The listing its BitTorrent session shares, then its listing card.
        # Withdrawn when files aren't shared; kept as it is while nothing is
        # served, so a passing gap doesn't rebuild it.
        if not self.lists:
            pass  # the embedding app publishes it
        elif not sharing_files:
            publish_mirror_listing(self.ledger.path, None, serving)
        elif card is not None:
            publish_mirror_listing(self.ledger.path, documents, serving)
        self.listing_card = (
            mirror_listing_card(self.ledger.path, mirror_key(self.ledger))
            if sharing_files and card is not None
            else None
        )
        own = (self.listing_card,) if self.listing_card is not None else ()
        document = build_peers(card, (*own, *self.directory.gossip()))
        with self.ledger.root() as root:
            atomic_bytes(root, "peers.json", document)
            for name, data in documents.items():
                if data is not None:
                    with contextlib.suppress(OSError):
                        atomic_bytes(root, name, data)
        return card

    def round(self, serving: frozenset[str], control: OperationControl) -> bool:
        """One bounded gossip round; return False when the budget skipped it.

        Learns mirrors from followed catalogs' hints, the optional DHT and
        optional relays, confirms they serve bytes they claim, then republishes.
        Only public routes are crawled. No model is fetched or trusted here.
        """
        state = self.ledger.read()
        policy = state.announce
        if policy is None:
            self.close()
            return False
        if not self.ledger.reserve(GOSSIP_RESERVATION, ingress=True):
            self.publish(serving)
            return False
        crawl_policy = TransferPolicy(allow_http=True, timeout=10, deadline_seconds=120)
        relay_policy = TransferPolicy(timeout=10, deadline_seconds=120)
        seeds = catalog_mirrors(self.ledger)
        port = dht_port(policy)
        if port is not None and rendezvous_available():
            if self.rendezvous is None:
                self.rendezvous = Rendezvous()
            self.rendezvous.announce(port, seconds=20, control=control)
            seeds = seeds + self.rendezvous.lookup(seconds=20, control=control)
        else:
            self.close()
        relays = relays_for(policy)
        if relays:
            self.directory.ingest(
                find_cards(
                    relays,
                    relay_policy,
                    control=control,
                    max_bytes=RELAY_FIND_BYTES,
                )
            )
            # Relayed onward in this mirror's peers document, so people who
            # never ask a relay still learn of computers with no address.
            self.directory.ingest_listings(
                find_cards(
                    relays,
                    relay_policy,
                    control=control,
                    max_bytes=LISTING_FIND_BYTES,
                    topic=LISTING_TOPIC,
                )
            )
        self.directory.crawl(
            seeds, crawl_policy, max_documents=CRAWL_DOCUMENTS, control=control
        )
        self.directory.spot_check(
            serving, crawl_policy, control=control, max_checks=SPOT_CHECKS
        )
        card = self.publish(serving)
        if card is not None and relays:
            publish_card(
                card,
                self._nostr_key(),
                relays,
                relay_policy,
                control=control,
                max_bytes=RELAY_PUBLISH_BYTES // 2,
            )
            if self.listing_card is not None:
                # Relays carry it to people who never reach this mirror.
                publish_card(
                    self.listing_card,
                    self._nostr_key(),
                    relays,
                    relay_policy,
                    control=control,
                    max_bytes=RELAY_PUBLISH_BYTES // 2,
                )
        return True

    def close(self) -> None:
        """Leave the DHT if this mirror joined it."""
        if self.rendezvous is not None:
            self.rendezvous.close()
            self.rendezvous = None


def remove_peers(ledger: MirrorLedger) -> None:
    """Withdraw this mirror's published peers document (its card lapses)."""
    with ledger.root() as root:
        try:
            os.unlink("peers.json", dir_fd=root)
            os.fsync(root)
        except FileNotFoundError:
            pass
