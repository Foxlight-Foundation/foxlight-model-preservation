"""Find reads the listings of computers that share with no address."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from conftest import Capture

pytest.importorskip("libtorrent", reason="optional native BitTorrent extra")

from foxlight_model_preservation import hub, sovereign_listing, sovereign_torrents  # noqa: E402
from foxlight_model_preservation.local_host.owner import LocalOwner  # noqa: E402
from foxlight_model_preservation.mirror_cards import verify_listing_card  # noqa: E402
from foxlight_model_preservation.mirror_directory import MirrorDirectory  # noqa: E402
from foxlight_model_preservation.mirror_holdings import read_held_manifest  # noqa: E402
from foxlight_model_preservation.mirror_rendezvous import Rendezvous  # noqa: E402
from foxlight_model_preservation.models import PreservationError  # noqa: E402


def _sharing(tmp_path: Path, capture: Capture, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """An app sharing one copy with no address, on a loopback DHT."""
    from test_local_backups import saved
    from test_local_community import enable, free_port, served, wait_for

    router = Rendezvous(interface="127.0.0.1", bootstrap=(), lab=True)
    sharers: list[sovereign_torrents.FileSharer] = []
    real = sovereign_torrents.FileSharer.__mro__[1]
    lab = {
        "listen_interfaces": "127.0.0.1:0",
        "enable_lsd": False,
        "dht_bootstrap_nodes": f"127.0.0.1:{router.port}",
        "dht_restrict_routing_ips": False,
        "dht_restrict_search_ips": False,
        "dht_ignore_dark_internet": False,
        "dht_enforce_node_id": False,
        "dht_prefer_verified_node_ids": False,
    }

    class Lab(real):  # type: ignore[misc, valid-type]
        def __init__(self, home, *, open_router, upload_rate=0, settings=None):  # type: ignore[no-untyped-def]
            super().__init__(
                home,
                open_router=False,
                upload_rate=upload_rate,
                settings={**(settings or {}), **lab},
            )
            self.session.add_dht_node(("127.0.0.1", router.port))
            sharers.append(self)

    monkeypatch.setattr(sovereign_torrents, "FileSharer", Lab)
    owner = LocalOwner(tmp_path / "sharing")
    saved(owner, tmp_path, capture)
    port = free_port()
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

    def card() -> bytes | None:
        community.announce()
        return community.listing_card

    listing_card = wait_for(card, seconds=60)
    wait_for(lambda: any(sharer.listing() for sharer in sharers), 60)
    return owner, router, sharers, identity, listing_card


def _read(
    tmp_path: Path,
    router: Rendezvous,
    sharer,
    card: bytes,  # type: ignore[no-untyped-def]
) -> dict[str, bytes]:
    """Read a listing through the loopback DHT, straight from the sharer."""
    dht = Rendezvous(
        interface="127.0.0.1", bootstrap=(("127.0.0.1", router.port),), lab=True
    )
    reader = sovereign_listing.ListingReader(
        tmp_path / "reads", rendezvous=dht, peers=(("127.0.0.1", sharer.port()),)
    )
    import base64

    public = base64.b64decode(verify_listing_card(card).mirror_key.public_key)
    try:
        end = time.monotonic() + 90
        while time.monotonic() < end:
            files = reader.read(public, seconds=10)
            if files is not None:
                return files
        raise AssertionError("listing never read")
    finally:
        reader.close()
        dht.close()


def test_a_listing_read_from_a_computer_makes_its_copy_findable_and_savable(
    tmp_path: Path, capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, router, sharers, identity, card = _sharing(tmp_path, capture, monkeypatch)
    try:
        sharer = next(sharer for sharer in sharers if sharer.listing())
        files = _read(tmp_path, router, sharer, card)
        assert "holdings.json" in files and f"manifests/{identity}.json" in files
        store = MirrorDirectory(tmp_path / "directory")
        store.ingest_listings([card])
        due = store.listings_to_read(4)
        assert [item.mirror_key for item in due] == [
            verify_listing_card(card).mirror_key
        ]
        assert store.keep_listing(due[0], files)
        # Found by name, kept by one computer and no mirror.
        rows = store.held_models("")
        assert [row["manifest"] for row in rows] == [identity]
        assert (rows[0]["mirror_count"], rows[0]["computers"]) == (0, 1)
        assert store.holders(identity) == []
        # Its signed manifest is exactly the copy listed.
        manifest = store.listed_manifest(identity)
        assert manifest is not None
        read_held_manifest(identity, manifest)
        # Read just now: not asked again until it is due.
        assert store.listings_to_read(4) == []
    finally:
        owner.close()
        router.close()


def test_a_listing_keeps_only_what_its_key_signed(
    tmp_path: Path, capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, router, sharers, identity, card = _sharing(tmp_path, capture, monkeypatch)
    try:
        sharer = next(sharer for sharer in sharers if sharer.listing())
        files = _read(tmp_path, router, sharer, card)
        store = MirrorDirectory(tmp_path / "directory")
        store.ingest_listings([card])
        parsed = verify_listing_card(card)
        # Holdings signed by another key are refused outright.
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )

        from foxlight_model_preservation.mirror_holdings import issue_holdings

        forged = {
            **files,
            "holdings.json": issue_holdings(Ed25519PrivateKey.generate(), []),
        }
        assert not store.keep_listing(parsed, forged)
        # A manifest that isn't the copy it is named for is left out.
        swapped = {**files, f"manifests/{identity}.json": b'{"payloadType":"x"}'}
        assert store.keep_listing(parsed, swapped)
        assert store.listed_manifest(identity) is None
        assert store.held_models("")[0]["manifest"] == identity
    finally:
        owner.close()
        router.close()


def test_saving_a_copy_only_computers_keep_needs_swarm_downloads(
    tmp_path: Path, capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, router, sharers, identity, card = _sharing(tmp_path, capture, monkeypatch)

    def gone(*_a: object, **_k: object) -> object:
        raise PreservationError("hub_not_found")

    monkeypatch.setattr(hub, "snapshot", gone)
    receiver = LocalOwner(tmp_path / "receiving")
    try:
        sharer = next(sharer for sharer in sharers if sharer.listing())
        files = _read(tmp_path, router, sharer, card)
        store = receiver.community.directory
        store.ingest_listings([card])
        assert store.keep_listing(verify_listing_card(card), files)
        found = receiver.control("held-search", {"query": ""})
        assert [row["manifest"] for row in found["results"]] == [identity]
        body = {"kind": "receive", "origin": "mirror", "manifest": identity}
        with pytest.raises(PreservationError, match="held_copy_needs_swarm"):
            receiver.control("plan", body)
        receiver.control("pirate-face-swarm", {"enabled": True})
        plan = receiver.control("plan", body)
        assert plan["origin"] == "mirror" and plan["holders"] == []
        assert plan["source"].startswith("computers sharing it over BitTorrent")
        assert plan["details"]["computers"] == 1
    finally:
        receiver.close()
        owner.close()
        router.close()


def test_refreshing_reads_due_listings_only_with_swarm_downloads_on(
    tmp_path: Path, capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, router, sharers, identity, card = _sharing(tmp_path, capture, monkeypatch)
    receiver = LocalOwner(tmp_path / "receiving")
    dht = Rendezvous(
        interface="127.0.0.1", bootstrap=(("127.0.0.1", router.port),), lab=True
    )
    try:
        sharer = next(sharer for sharer in sharers if sharer.listing())
        monkeypatch.setattr(
            "foxlight_model_preservation.local_host.owner.load_seeds", lambda: ()
        )
        monkeypatch.setattr(
            receiver,
            "_listing_reader",
            lambda: sovereign_listing.ListingReader(
                tmp_path / "reads",
                rendezvous=dht,
                peers=(("127.0.0.1", sharer.port()),),
            ),
        )
        receiver.community.directory.ingest_listings([card])
        # Off: nothing is read, so nothing shows this computer's address.
        assert receiver.control("held-refresh", {})["listings"] == 0
        assert receiver.community.directory.listing_computers(identity) == 0
        receiver.control("pirate-face-swarm", {"enabled": True})
        end = time.monotonic() + 120
        read = 0
        while not read and time.monotonic() < end:
            read = receiver.control("held-refresh", {})["listings"]
            if not read:
                # A failed read waits its turn; let the test ask again.
                receiver.community.directory._tried.clear()
        assert read == 1
        assert receiver.community.directory.listing_computers(identity) == 1
    finally:
        dht.close()
        receiver.close()
        owner.close()
        router.close()
