"""Cloud storage for "Help keep models available": check first, then keep in sync."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import Capture
from test_bucket import KEYS, LOCAL, bucket
from test_local_backups import saved
from test_local_community import enable, free_port, wait_for

from foxlight_model_preservation.archives import verify_archive_contents
from foxlight_model_preservation.bucket import BucketCredentials
from foxlight_model_preservation.local_host import community as module
from foxlight_model_preservation.local_host.owner import LocalOwner
from foxlight_model_preservation.mirror_cards import read_peers, verify_card
from foxlight_model_preservation.mirror_directory import (
    MirrorDirectory,
    fetch_discovered,
)
from foxlight_model_preservation.mirror_state import MirrorLedger
from foxlight_model_preservation.operations import OperationControl
from foxlight_model_preservation.storage import manifest_path


@pytest.fixture
def keychain(monkeypatch: pytest.MonkeyPatch) -> dict[str, BucketCredentials]:
    """Stand in for the operating system keychain."""
    stored: dict[str, BucketCredentials] = {}

    def save(name: str, credentials: BucketCredentials) -> None:
        stored[name] = credentials

    def load(name: str) -> BucketCredentials:
        if name not in stored:
            raise module.PreservationError("bucket_credentials_missing")
        return stored[name]

    monkeypatch.setattr(module, "save_credentials", save)
    monkeypatch.setattr(module, "load_credentials", load)
    monkeypatch.setattr(
        module, "delete_credentials", lambda name: stored.pop(name, None)
    )
    return stored


def details(endpoint: str, public_url: str, secret: str = KEYS.secret_key) -> dict:
    return {
        "provider": "other",
        "bucket": "models",
        "region": "local-1",
        "endpoint": endpoint,
        "public_url": public_url,
        "access_key": KEYS.access_key,
        "secret_key": secret,
    }


def addresses(client) -> tuple[str, str]:
    return client.target.endpoint, client.target.public_url


def test_a_bucket_is_saved_only_after_it_passes_the_check(
    tmp_path: Path, keychain: dict[str, BucketCredentials]
) -> None:
    owner = LocalOwner(tmp_path / "owner")
    owner.community.bucket_policy = LOCAL
    try:
        with bucket() as (server, client):
            endpoint, public_url = addresses(client)
            wrong = owner.control(
                "community-bucket-connect", details(endpoint, public_url, "wrong")
            )
            assert wrong["status"] == "upload" and wrong["bucket"] is None
            server.public = False
            private = owner.control(
                "community-bucket-connect", details(endpoint, public_url)
            )
            assert private["status"] == "public" and private["bucket"] is None
            assert keychain == {}
            server.public = True
            ready = owner.control(
                "community-bucket-connect", details(endpoint, public_url)
            )
            assert ready["status"] == "ready"
            assert ready["bucket"]["public_url"] == public_url
            # The check cleans up after itself.
            assert server.objects == {}
        # Keys live only in the keychain, never in the app's files or replies.
        (name,) = keychain
        assert keychain[name] == KEYS
        saved_state = (owner.home / "sharing/community/bucket.json").read_text()
        assert KEYS.secret_key not in saved_state
        assert json.loads(saved_state)["target"]["key_name"] == name
        assert KEYS.secret_key not in json.dumps(ready)
        assert KEYS.access_key not in json.dumps(ready)
        providers = {item["id"] for item in ready["bucket_providers"]}
        assert {"r2", "b2", "s3", "wasabi", "other"} <= providers
    finally:
        owner.close()


def test_unusable_bucket_details_are_refused_plainly(
    tmp_path: Path, keychain: dict[str, BucketCredentials]
) -> None:
    owner = LocalOwner(tmp_path / "owner")
    try:
        for body in (
            {**details("http://storage.example", "https://pub.example")},
            {
                "provider": "r2",
                "bucket": "models",
                "access_key": "a",
                "secret_key": "b",
            },
            {**details("http://127.0.0.1:1", "http://127.0.0.1:1"), "extra": "x"},
            {**details("http://127.0.0.1:1", "http://127.0.0.1:1"), "provider": "x"},
        ):
            with pytest.raises(module.PreservationError) as raised:
                owner.control("community-bucket-connect", body)
            assert KEYS.secret_key not in str(raised.value)
        assert keychain == {} and owner.community.bucket() is None
    finally:
        owner.close()


def test_a_home_mirror_behind_a_router_is_found_through_its_bucket(
    tmp_path: Path, capture: Capture, keychain: dict[str, BucketCredentials]
) -> None:
    owner = LocalOwner(tmp_path / "owner")
    owner.community.bucket_policy = LOCAL
    try:
        saved(owner, tmp_path, capture)
        if owner.community.reachability()["status"] == "public_address":
            pytest.skip("this test host has a public address")
        with bucket() as (server, client):
            enable(owner, free_port())
            endpoint, public_url = addresses(client)
            ready = owner.control(
                "community-bucket-connect", details(endpoint, public_url)
            )
            assert ready["status"] == "ready"
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
            # The next round uploads the model, manifest last, then announces.
            wait_for(
                lambda: (
                    (owner.control("community-read", {})["bucket"] or {}).get(
                        "model_count"
                    )
                    == 1
                )
            )
            assert manifest_path(identity) in server.objects
            wait_for(lambda: ".well-known/sovereign/peers.json" in server.objects)
            cards = read_peers(server.objects[".well-known/sovereign/peers.json"])
            card = verify_card(cards[0])
            # Only the bucket can be reached, so the card points only there.
            assert card.addresses == (public_url,)
            assert card.covers(identity)
            # A stranger who knows only the bucket's address discovers it from
            # the bucket's own peers document, then downloads with no computer
            # attached, verifying against the network's public identity.
            stranger = MirrorDirectory(tmp_path / "stranger")
            stranger.crawl((public_url,), LOCAL)
            assert public_url in stranger.candidates(identity)
            trust = (
                MirrorLedger(owner.home / "sharing/community/mirror")
                .read()
                .models[identity]
                .grant.trust
            )
            received = tmp_path / "recipient"
            manifest = fetch_discovered(identity, trust, received, stranger, LOCAL)
            assert manifest == verify_archive_contents(
                received, identity, trust, OperationControl()
            )

            # Turning sharing off withdraws the model from the bucket as well.
            owner.control("community-configure", {"enabled": False})
            wait_for(lambda: owner.community.bucket().synced == ())
            assert manifest_path(identity) not in server.objects
    finally:
        owner.close()


def test_disconnecting_forgets_the_keys_and_leaves_the_bucket_alone(
    tmp_path: Path, keychain: dict[str, BucketCredentials]
) -> None:
    owner = LocalOwner(tmp_path / "owner")
    owner.community.bucket_policy = LOCAL
    try:
        with bucket() as (server, client):
            endpoint, public_url = addresses(client)
            owner.control("community-bucket-connect", details(endpoint, public_url))
            server.objects["objects/kept"] = b"kept"
            gone = owner.control("community-bucket-disconnect", {})
            assert gone["status"] == "disconnected" and gone["bucket"] is None
            assert keychain == {}
            assert server.objects == {"objects/kept": b"kept"}
            # Disconnecting twice is harmless.
            owner.control("community-bucket-disconnect", {})
    finally:
        owner.close()


def test_a_sync_never_writes_over_a_bucket_connected_meanwhile(
    tmp_path: Path, keychain: dict[str, BucketCredentials]
) -> None:
    owner = LocalOwner(tmp_path / "owner")
    owner.community.bucket_policy = LOCAL
    try:
        with bucket() as (_, client):
            endpoint, public_url = addresses(client)
            owner.control("community-bucket-connect", details(endpoint, public_url))
            old = owner.community.bucket()
            assert old is not None
            owner.control("community-bucket-connect", details(endpoint, public_url))
            new = owner.community.bucket()
            assert new is not None and new.target.key_name != old.target.key_name
            # Replacing a bucket forgets the old keys.
            assert set(keychain) == {new.target.key_name}
            stale = old.model_copy(update={"last_synced": 1})
            assert not owner.community._save_bucket(stale)
            assert owner.community.bucket() == new
    finally:
        owner.close()


def _shared(owner: LocalOwner, server, client) -> str:
    """Turn sharing on with a connected bucket; return the shared identity."""
    endpoint, public_url = addresses(client)
    assert (
        owner.control("community-bucket-connect", details(endpoint, public_url))[
            "status"
        ]
        == "ready"
    )
    return wait_for(
        lambda: next(
            (
                row["public_manifest_id"]
                for row in owner.control("community-read", {})["copies"]
                if row["status"] == "sharing"
            ),
            None,
        )
    )


def test_bucket_uploads_count_against_the_owners_upload_limits(
    tmp_path: Path, capture: Capture, keychain: dict[str, BucketCredentials]
) -> None:
    owner = LocalOwner(tmp_path / "owner")
    owner.community.bucket_policy = LOCAL
    try:
        saved(owner, tmp_path, capture)
        with bucket() as (server, client):
            enable(owner, free_port())
            # Far less than the model: nothing may be sent beyond it.
            owner.control("community-configure", {"daily_upload_bytes": 64})
            _shared(owner, server, client)
            view = wait_for(
                lambda: (
                    (owner.control("community-read", {})["bucket"] or {}).get(
                        "last_error"
                    )
                    == "bucket_upload_limit_reached"
                    and owner.control("community-read", {})
                )
            )
            assert view["bucket"]["model_count"] == 0
            assert not any(key.startswith("objects/") for key in server.objects)
            # A larger allowance lets the next round finish the upload, and the
            # bytes sent show up in the same usage counters as serving.
            owner.control("community-configure", {"daily_upload_bytes": 2**30})
            done = wait_for(
                lambda: (
                    (owner.control("community-read", {})["bucket"] or {}).get(
                        "model_count"
                    )
                    == 1
                    and owner.control("community-read", {})
                )
            )
            assert done["uploaded_today_bytes"] > 0
    finally:
        owner.close()


def test_a_withdrawal_the_bucket_refused_is_retried_while_sharing_is_off(
    tmp_path: Path,
    capture: Capture,
    keychain: dict[str, BucketCredentials],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(LocalOwner, "_withdraw_retry", 0.1)
    owner = LocalOwner(tmp_path / "owner")
    owner.community.bucket_policy = LOCAL
    try:
        saved(owner, tmp_path, capture)
        with bucket() as (server, client):
            enable(owner, free_port())
            identity = _shared(owner, server, client)
            wait_for(lambda: manifest_path(identity) in server.objects)
            wait_for(lambda: owner.community.bucket().synced == (identity,))
            server.fail_deletes = 3
            owner.control("community-configure", {"enabled": False})
            wait_for(lambda: owner.community.bucket().synced == ())
            assert manifest_path(identity) not in server.objects
            assert server.fail_deletes == 0
    finally:
        owner.close()


def test_a_withdrawal_left_over_from_a_previous_run_resumes_at_start(
    tmp_path: Path,
    keychain: dict[str, BucketCredentials],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = "b" * 64
    monkeypatch.setattr(module.CommunityMirror, "bucket_policy", LOCAL)
    monkeypatch.setattr(LocalOwner, "_withdraw_retry", 0.1)
    owner = LocalOwner(tmp_path / "owner")
    try:
        with bucket() as (server, client):
            endpoint, public_url = addresses(client)
            owner.control("community-bucket-connect", details(endpoint, public_url))
            state = owner.community.bucket()
            assert state is not None
            assert owner.community._save_bucket(
                state.model_copy(update={"synced": (stale,)})
            )
            server.objects[manifest_path(stale)] = b"{}"
            owner.close()
            again = LocalOwner(tmp_path / "owner")
            try:
                wait_for(lambda: again.community.bucket().synced == ())
                assert manifest_path(stale) not in server.objects
            finally:
                again.close()
    finally:
        owner.close()


def test_a_failing_upload_never_keeps_a_withdrawn_model_public(
    tmp_path: Path,
    keychain: dict[str, BucketCredentials],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from foxlight_model_preservation.operations import OperationControl

    wanted, unwanted = "a" * 64, "b" * 64
    owner = LocalOwner(tmp_path / "owner")
    owner.community.bucket_policy = LOCAL
    try:
        with bucket() as (server, client):
            endpoint, public_url = addresses(client)
            owner.control("community-bucket-connect", details(endpoint, public_url))
            state = owner.community.bucket()
            assert state is not None
            owner.community._save_bucket(
                state.model_copy(update={"synced": (unwanted,)})
            )
            server.objects[manifest_path(unwanted)] = b"{}"

            class Ledger:
                def __init__(self, path: Path) -> None:
                    pass

                def read(self):
                    grant = SimpleNamespace(trust=None)
                    return SimpleNamespace(
                        models={wanted: SimpleNamespace(grant=grant)}
                    )

                def reserve(self, count: int) -> bool:
                    return True

            def failing(*args, **kwargs):
                raise module.PreservationError("bucket_request_failed")

            monkeypatch.setattr(module, "MirrorLedger", Ledger)
            monkeypatch.setattr(module, "publish_to_bucket", failing)
            monkeypatch.setattr(owner.community, "held", lambda: frozenset({wanted}))
            monkeypatch.setattr(owner.community, "consented", lambda policy=None: True)
            result = owner.community.sync_bucket(OperationControl())
            assert result is not None
            assert manifest_path(unwanted) not in server.objects
            assert result.synced == ()
            assert result.last_error == "bucket_request_failed"
    finally:
        owner.close()


def test_an_upload_whose_outcome_was_lost_is_still_withdrawn(
    tmp_path: Path,
    keychain: dict[str, BucketCredentials],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from foxlight_model_preservation.operations import OperationControl

    wanted = "a" * 64
    owner = LocalOwner(tmp_path / "owner")
    owner.community.bucket_policy = LOCAL
    try:
        with bucket() as (server, client):
            endpoint, public_url = addresses(client)
            owner.control("community-bucket-connect", details(endpoint, public_url))

            class Ledger:
                def __init__(self, path: Path) -> None:
                    pass

                def read(self):
                    grant = SimpleNamespace(trust=None)
                    return SimpleNamespace(
                        models={wanted: SimpleNamespace(grant=grant)}
                    )

                def reserve(self, count: int) -> bool:
                    return True

            def lost_reply(store, identity, trust, target, **kwargs):
                # The bucket stored the manifest, but its reply never arrived.
                server.objects[manifest_path(identity)] = b"{}"
                raise ConnectionResetError("reply lost")

            sharing = {"on": True}
            monkeypatch.setattr(module, "MirrorLedger", Ledger)
            monkeypatch.setattr(module, "publish_to_bucket", lost_reply)
            monkeypatch.setattr(owner.community, "held", lambda: frozenset({wanted}))
            monkeypatch.setattr(
                owner.community, "consented", lambda policy=None: sharing["on"]
            )
            failed = owner.community.sync_bucket(OperationControl())
            assert failed is not None
            assert failed.synced == () and failed.pending == (wanted,)
            # Turning sharing off withdraws it although it never counted as synced.
            sharing["on"] = False
            withdrawn = owner.community.sync_bucket(OperationControl())
            assert withdrawn is not None and withdrawn.pending == ()
            assert manifest_path(wanted) not in server.objects
    finally:
        owner.close()
