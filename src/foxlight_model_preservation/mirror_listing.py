"""The listing a mirror sharing files over BitTorrent publishes, and its card.

A mirror that shares its files in Sovereign's own per-file swarms also shares
a small listing torrent: its signed object coverage, holdings and torrents
documents plus the manifests of what it holds, under a DHT pointer signed with
its mirror key. A listing card, signed by the same key, names that key and a
filter of the copies listed, so people can find the mirror with no address at
all. The desktop app and server mirrors publish them the same way.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import JsonValue

from .cards import identity_json
from .mirror_cards import issue_listing_card
from .mirror_state import MirrorLedger
from .models import PreservationError
from .pirateface_seed import seeded
from .signing import digest
from .sovereign_listing import (
    current_listing,
    listing_files,
    listing_manifests,
    publish_listing,
    unchanged_listing,
    withdraw_listing,
)
from .sovereign_torrents import listed
from .storage import directory, manifest_path, read_metadata

LISTING_DOCUMENTS = ("objects.json", "holdings.json", "torrents.json")


def publish_mirror_listing(
    root: Path, documents: dict[str, bytes | None] | None, held: frozenset[str]
) -> None:
    """Write the listing the mirror at ``root`` shares, or withdraw it.

    ``documents`` are the mirror's signed documents by name (None withdraws
    the listing: files aren't shared). ``held`` are the copies it shares. An
    unchanged listing is not rebuilt. Never raises for a problem it can
    leave to the next round.
    """
    if documents is None:
        withdraw_listing(root)
        return
    present = {name: data for name, data in documents.items() if data is not None}
    if "holdings.json" not in present:
        return  # nothing worth listing could be described
    # The copies the listing can actually describe: a copy counts as shared a
    # moment before its manifest is readable in the public store.
    manifests: dict[str, bytes] = {}
    for manifest_id in sorted(held):
        with contextlib.suppress(OSError, PreservationError):
            with directory(root / "public") as public:
                manifests[manifest_id] = read_metadata(
                    public, manifest_path(manifest_id)
                )
    if not manifests:
        return  # nothing it could describe yet; a later round publishes it
    try:
        pool = MirrorLedger(root)
        entries: list[JsonValue] = [
            [entry.manifest, entry.infohash, entry.source, entry.object or ""]
            for entry in [*seeded(pool.read(), held), *listed(pool, held)]
        ]
    except (PreservationError, OSError, ValueError):
        return
    described: list[JsonValue] = list(sorted(manifests))
    subject = digest(identity_json([described, sorted(entries, key=str)]))
    if unchanged_listing(root, subject):
        return
    with contextlib.suppress(PreservationError, OSError, RuntimeError):
        publish_listing(root, listing_files(present, manifests), subject)


def mirror_listing_card(root: Path, key: Ed25519PrivateKey) -> bytes | None:
    """A fresh listing card for the listing ``root`` shares now, or None."""
    listing = current_listing(root)
    if listing is None:
        return None
    manifests = listing_manifests(listing)
    if not manifests:
        return None
    try:
        return issue_listing_card(key, manifests)
    except (PreservationError, OSError, ValueError):
        return None
