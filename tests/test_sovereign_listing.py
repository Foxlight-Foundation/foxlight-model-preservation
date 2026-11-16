"""A sharing computer's signed listing travels as a torrent, found by a DHT pointer."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from foxlight_model_preservation.models import PreservationError

pytest.importorskip("libtorrent", reason="optional native BitTorrent extra")

from foxlight_model_preservation import sovereign_listing  # noqa: E402
from foxlight_model_preservation.mirror_rendezvous import (  # noqa: E402
    Rendezvous,
    _verified_item,
)
from foxlight_model_preservation.sovereign_listing import (  # noqa: E402
    LISTING_SALT,
    current_listing,
    dht_keys,
    listing_files,
    pointer_value,
    publish_listing,
    read_listing,
    read_pointer,
)
from foxlight_model_preservation.sovereign_torrents import build_torrent  # noqa: E402

DOCUMENTS = {"holdings.json": b'{"holdings":[]}', "torrents.json": b'{"t":1}'}
MANIFESTS = {"a" * 64: b'{"manifest":"a"}', "b" * 64: b'{"manifest":"b"}'}


def test_the_same_listing_gives_the_same_torrent_on_every_computer(
    tmp_path: Path,
) -> None:
    files = listing_files(DOCUMENTS, MANIFESTS)
    assert sorted(files) == [
        "holdings.json",
        f"manifests/{'a' * 64}.json",
        f"manifests/{'b' * 64}.json",
        "torrents.json",
    ]
    first = publish_listing(tmp_path / "one", files)
    second = publish_listing(tmp_path / "two", dict(reversed(files.items())))
    assert (first.infohash, first.infohash_v2) == (second.infohash, second.infohash_v2)
    # The files are there to seed, exactly as listed.
    inner = first.folder / "sovereign-listing"
    assert (inner / "torrents.json").read_bytes() == b'{"t":1}'
    assert current_listing(tmp_path / "one") == first


def test_manifests_beyond_the_bound_are_left_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sovereign_listing, "MAX_LISTING_BYTES", 45)
    files = listing_files(DOCUMENTS, MANIFESTS)
    # The documents always fit; one manifest still does, the other doesn't.
    assert sorted(files) == [
        "holdings.json",
        f"manifests/{'a' * 64}.json",
        "torrents.json",
    ]
    monkeypatch.setattr(sovereign_listing, "MAX_LISTING_BYTES", 10)
    with pytest.raises(PreservationError):
        listing_files(DOCUMENTS, MANIFESTS)


def test_a_new_listing_keeps_the_one_it_replaced_and_drops_older_ones(
    tmp_path: Path,
) -> None:
    generations = [
        publish_listing(tmp_path, {**DOCUMENTS, "objects.json": str(index).encode()})
        for index in range(3)
    ]
    kept = {entry.name for entry in (tmp_path / "listing").iterdir() if entry.is_dir()}
    assert kept == {generations[1].infohash_v2, generations[2].infohash_v2}
    assert current_listing(tmp_path) == generations[2]
    # Publishing the same content again changes nothing.
    assert (
        publish_listing(tmp_path, {**DOCUMENTS, "objects.json": b"2"}) == generations[2]
    )
    assert current_listing(tmp_path) == generations[2]


def test_only_listing_torrents_are_read_as_listings(tmp_path: Path) -> None:
    stored = tmp_path / "file"
    stored.write_bytes(b"model bytes")
    import hashlib

    other = build_torrent(stored, hashlib.sha256(b"model bytes").hexdigest(), tmp_path)
    with pytest.raises(PreservationError):
        read_listing(other.data)
    with pytest.raises(PreservationError):
        read_listing(b"not a torrent")
    # A listing torrent with any other file in it is refused.
    from foxlight_model_preservation import sovereign_listing as module

    with pytest.raises(PreservationError):
        read_listing(
            module._create(_folder(tmp_path, "notes.txt"), {"notes.txt": b"x"})
        )


def _folder(root: Path, name: str) -> Path:
    folder = root / "foreign"
    (folder / "sovereign-listing").mkdir(parents=True)
    (folder / "sovereign-listing" / name).write_bytes(b"x")
    return folder


def test_pointer_values_name_only_a_v2_infohash() -> None:
    v2 = "c" * 64
    assert read_pointer(pointer_value(v2)) == v2
    for value in (b"", b"sovereign-listing-v1:" + b"c" * 63, b"other:" + b"c" * 64, 5):
        assert read_pointer(value) is None
    with pytest.raises(PreservationError):
        pointer_value("C" * 64)


def test_a_pointer_signed_by_the_mirror_key_is_found_and_updated_through_the_dht() -> (
    None
):
    key = Ed25519PrivateKey.generate()
    secret, public = dht_keys(key)
    router = Rendezvous(interface="127.0.0.1", bootstrap=(), lab=True)
    boot = (("127.0.0.1", router.port),)
    nodes = [
        Rendezvous(interface="127.0.0.1", bootstrap=boot, lab=True) for _ in range(5)
    ]
    publisher, reader = nodes[0], nodes[-1]
    try:
        for value in (pointer_value("1" * 64), pointer_value("2" * 64)):
            found = None
            end = time.monotonic() + 60
            while found != value and time.monotonic() < end:
                assert publisher.put_pointer(secret, public, LISTING_SALT, value)
                time.sleep(1)
                found = reader.get_pointer(public, LISTING_SALT, seconds=5)
            assert found == value
        # Nobody else's key finds it, and another salt is another pointer.
        stranger = dht_keys(Ed25519PrivateKey.generate())[1]
        assert reader.get_pointer(stranger, LISTING_SALT, seconds=2) is None
        assert reader.get_pointer(public, b"other", seconds=2) is None
    finally:
        for node in (router, *nodes):
            node.close()


class _Alert:
    def __init__(self, item: dict[str, object]) -> None:
        self.item = item
        self.authoritative = True


def test_an_item_is_accepted_only_with_the_owners_signature() -> None:
    key = Ed25519PrivateKey.generate()
    _secret, public = dht_keys(key)
    value = pointer_value("d" * 64)
    signed = b"4:salt%d:%s3:seqi7e1:v%d:%s" % (
        len(LISTING_SALT),
        LISTING_SALT,
        len(value),
        value,
    )
    item: dict[str, object] = {
        "key": public,
        "salt": LISTING_SALT,
        "seq": 7,
        "value": value,
        "signature": key.sign(signed),
    }
    assert _verified_item(_Alert(item), public, LISTING_SALT) == (7, value)  # type: ignore[arg-type]
    for change in (
        {"value": pointer_value("e" * 64)},
        {"seq": 8},
        {"salt": b"other"},
        {"signature": Ed25519PrivateKey.generate().sign(signed)},
    ):
        assert _verified_item(_Alert({**item, **change}), public, LISTING_SALT) is None  # type: ignore[arg-type]


def test_a_sharing_computer_with_no_address_can_be_found_through_its_listing(
    tmp_path: Path, capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No address, no card: the DHT pointer leads to a listing torrent, fetched
    from the sharing session, whose documents the mirror key signed."""
    from test_local_backups import saved
    from test_local_community import enable, served, wait_for
    from test_local_community import free_port as free

    from foxlight_model_preservation import sovereign_torrents
    from foxlight_model_preservation.local_host.owner import LocalOwner
    from foxlight_model_preservation.mirror_holdings import verify_holdings
    from foxlight_model_preservation.operations import OperationControl
    from foxlight_model_preservation.signing import public_record
    from foxlight_model_preservation.torrent_binding import torrent_engine

    router = Rendezvous(interface="127.0.0.1", bootstrap=(), lab=True)
    boot = (("127.0.0.1", router.port),)
    sharers: list[sovereign_torrents.FileSharer] = []
    # The real sharing session (tests otherwise keep it off the DHT), joined to
    # a loopback DHT instead of the public one, and never to a router.
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

    class Recorded(real):  # type: ignore[misc, valid-type]
        def __init__(self, home, *, open_router, upload_rate=0, settings=None):  # type: ignore[no-untyped-def]
            super().__init__(
                home,
                open_router=False,
                upload_rate=upload_rate,
                settings={**(settings or {}), **lab},
            )
            self.session.add_dht_node(("127.0.0.1", router.port))
            sharers.append(self)

    monkeypatch.setattr(sovereign_torrents, "FileSharer", Recorded)
    reader = Rendezvous(interface="127.0.0.1", bootstrap=boot, lab=True)
    engine = torrent_engine()
    downloader = engine.session(
        {
            "listen_interfaces": "127.0.0.1:0",
            "enable_dht": False,
            "enable_lsd": False,
            "enable_upnp": False,
            "enable_natpmp": False,
            "alert_mask": 0,
        }
    )
    port = free()
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
        # Nothing reaches this computer: no card is published, only the listing.
        assert owner.community.addresses() == ()
        key = owner.community._mirror_key()
        _secret, public = dht_keys(key)
        found = None
        end = time.monotonic() + 90
        while found is None and time.monotonic() < end:
            owner.community.gossip(OperationControl())
            value = reader.get_pointer(public, LISTING_SALT, seconds=5)
            found = read_pointer(value)
        assert found is not None
        listing = wait_for(
            lambda: current_listing(owner.community._mirror()), seconds=10
        )
        assert listing.infohash_v2 == found
        # Another round with nothing new shared keeps the same listing.
        owner.community.gossip(OperationControl())
        assert current_listing(owner.community._mirror()) == listing
        # The sharing session seeds exactly that listing.
        wait_for(lambda: any(sharer.listing() == found for sharer in sharers), 60)
        sharer = next(sharer for sharer in sharers if sharer.listing() == found)
        params = engine.parse_magnet_uri(f"magnet:?xt=urn:btmh:1220{found}")
        params.save_path = str(tmp_path / "fetched")
        handle = downloader.add_torrent(params)
        handle.connect_peer(("127.0.0.1", sharer.port()))
        wait_for(lambda: handle.status().is_seeding, 60)
        inner = tmp_path / "fetched" / "sovereign-listing"
        holdings = verify_holdings(
            (inner / "holdings.json").read_bytes(), public_record(key)
        )
        assert [entry.manifest for entry in holdings.entries] == [identity]
        assert (inner / "manifests" / f"{identity}.json").exists()
        assert (inner / "torrents.json").exists()
    finally:
        owner.close()
        reader.close()
        router.close()
        downloader.pause()


def test_a_listing_is_rebuilt_only_when_what_it_describes_changes_or_ages(
    tmp_path: Path,
) -> None:
    from foxlight_model_preservation.sovereign_listing import (
        RENEW_SECONDS,
        unchanged_listing,
    )

    assert not unchanged_listing(tmp_path, "subject")
    publish_listing(tmp_path, dict(DOCUMENTS), "subject", now=1_000_000)
    assert unchanged_listing(tmp_path, "subject", now=1_000_000 + 60)
    # Something else shared, or documents due for renewal: rebuild.
    assert not unchanged_listing(tmp_path, "other", now=1_000_000 + 60)
    assert not unchanged_listing(tmp_path, "subject", now=1_000_000 + RENEW_SECONDS)
    # A listing whose files are gone is never treated as current.
    (tmp_path / "listing" / "current").unlink()
    assert not unchanged_listing(tmp_path, "subject", now=1_000_000 + 60)
