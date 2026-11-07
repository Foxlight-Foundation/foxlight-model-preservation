"""Cloud storage for "Help keep models available": check first, then keep in sync."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pytest
from conftest import Capture
from pydantic import ValidationError
from test_bucket import KEYS, LOCAL, bucket
from test_local_backups import saved
from test_local_community import enable, free_port, wait_for

from foxlight_model_preservation.archives import (
    verify_archive_contents,
    verify_archive_manifest,
)
from foxlight_model_preservation.bucket import (
    BucketCredentials,
    BucketTarget,
    r2_jurisdiction,
)
from foxlight_model_preservation.handoffs import read_handoff
from foxlight_model_preservation.invitations import (
    Invitation,
    accept_invitation_keys,
    authenticate_invitation,
    fetch_invitation,
)
from foxlight_model_preservation.local_host import community as module
from foxlight_model_preservation.local_host.owner import LocalOwner
from foxlight_model_preservation.mirror_cards import read_peers, verify_card
from foxlight_model_preservation.mirror_directory import (
    MirrorDirectory,
    fetch_discovered,
)
from foxlight_model_preservation.mirror_state import MirrorLedger
from foxlight_model_preservation.models import Record
from foxlight_model_preservation.operations import OperationControl
from foxlight_model_preservation.signing import canonical_bytes
from foxlight_model_preservation.storage import directory, manifest_path, read_metadata


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
        assert {"r2", "b2", "s3", "wasabi", "hetzner", "other"} <= providers
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
                ),
                seconds=120,
            )
            assert manifest_path(identity) in server.objects

            def bucket_card() -> bytes | None:
                # Announcing runs every round; call it too, so a slow runner
                # need not wait for the next one.
                owner.community.announce()
                document = server.objects.get(".well-known/sovereign/peers.json")
                return next(iter(read_peers(document)), None) if document else None

            card = verify_card(wait_for(bucket_card, seconds=60))
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
            # The panel counts it as still to withdraw, though never confirmed.
            summary = owner.community._bucket_summary()
            assert isinstance(summary, dict) and summary["withdraw_count"] == 1
            # Turning sharing off withdraws it although it never counted as synced.
            sharing["on"] = False
            withdrawn = owner.community.sync_bucket(OperationControl())
            assert withdrawn is not None and withdrawn.pending == ()
            assert manifest_path(wanted) not in server.objects
    finally:
        owner.close()


def test_withholding_a_model_cancels_a_bucket_upload_in_progress(
    tmp_path: Path, keychain: dict[str, BucketCredentials]
) -> None:
    from foxlight_model_preservation.operations import OperationControl

    owner = LocalOwner(tmp_path / "owner")
    owner.community.bucket_policy = LOCAL
    try:
        with bucket() as (_, client):
            endpoint, public_url = addresses(client)
            owner.control("community-bucket-connect", details(endpoint, public_url))
            a, b = "a" * 64, "b" * 64
            for before, after, cancelled in (
                # A model joining never restarts a long upload.
                (frozenset({a}), frozenset({a, b}), False),
                # A model leaving cancels it, so the withdrawal goes first.
                (frozenset({a, b}), frozenset({a}), True),
            ):
                control = OperationControl()
                owner._gossip_control = control
                owner._gossip_wake.clear()
                owner._bucket_selection_changed(before, after)
                assert control._cancelled.is_set() is cancelled
                assert owner._gossip_wake.is_set()
            owner._gossip_control = None
    finally:
        owner.close()


def test_one_refused_withdrawal_never_keeps_the_others_public(
    tmp_path: Path, keychain: dict[str, BucketCredentials]
) -> None:
    from foxlight_model_preservation.operations import OperationControl

    stuck, other = "a" * 64, "b" * 64
    owner = LocalOwner(tmp_path / "owner")
    owner.community.bucket_policy = LOCAL
    try:
        with bucket() as (server, client):
            endpoint, public_url = addresses(client)
            owner.control("community-bucket-connect", details(endpoint, public_url))
            state = owner.community.bucket()
            assert state is not None
            owner.community._save_bucket(
                state.model_copy(update={"synced": (stuck, other)})
            )
            for identity in (stuck, other):
                server.objects[manifest_path(identity)] = b"{}"
            # A retention rule, say, forbids deleting one manifest.
            server.locked.add(manifest_path(stuck))
            result = owner.community.sync_bucket(OperationControl())
            assert result is not None
            assert manifest_path(other) not in server.objects
            assert result.synced == (stuck,) and result.last_error is not None
            server.locked.clear()
            result = owner.community.sync_bucket(OperationControl())
            assert result is not None and result.synced == ()
            assert manifest_path(stuck) not in server.objects
    finally:
        owner.close()


# What the bucket serves, and what a recipient needs ---------------------------


def test_the_bucket_lists_its_models_and_exports_a_handoff_for_each(
    tmp_path: Path, capture: Capture, keychain: dict[str, BucketCredentials]
) -> None:
    owner = LocalOwner(tmp_path / "owner")
    owner.community.bucket_policy = LOCAL
    try:
        saved(owner, tmp_path, capture)
        with bucket() as (server, client):
            enable(owner, free_port())
            identity = _shared(owner, server, client)
            wait_for(
                lambda: (
                    (owner.control("community-read", {})["bucket"] or {}).get(
                        "model_count"
                    )
                    == 1
                ),
                seconds=120,
            )
            public_url = client.target.public_url
            requests_before = len(server.order)
            contents = owner.control("community-bucket-contents", {})
            # Read from the app's own records: no request reached the bucket.
            assert len(server.order) == requests_before
            assert contents["total"] == 1 and contents["public_url"] == public_url
            (row,) = contents["models"]
            held = (
                MirrorLedger(owner.home / "sharing/community/mirror")
                .read()
                .models[identity]
            )
            trust = held.grant.trust
            with directory(owner.home / "sharing/community/mirror/store") as root:
                manifest = verify_archive_manifest(
                    read_metadata(root, manifest_path(identity)), trust, identity
                )
            assert row == {
                "manifest_id": identity,
                "name": manifest.name,
                "size_bytes": manifest.total_bytes,
                "synced_at": owner.community.bucket().synced_at[identity],
                "keys": [key.key_id for key in trust.keys],
            }
            assert (
                owner.control("community-bucket-contents", {"offset": 1})["models"]
                == []
            )

            document = owner.bucket_handoff(identity)
            # The ordinary one-file handoff: an unchanged signed invitation
            # whose only mirror is the bucket's public address.
            handoff = read_handoff(document)
            invitation = handoff.reference
            assert isinstance(invitation, Invitation)
            assert handoff.contacts == ()
            assert invitation.manifest_id == identity
            assert invitation.mirrors == (public_url,)
            assert invitation.candidate_trust == trust
            assert authenticate_invitation(invitation, trust) == manifest
            # Public metadata only: no keys, keychain names or local paths.
            text = document.decode()
            for private in (
                KEYS.secret_key,
                KEYS.access_key,
                owner.community.bucket().target.key_name,
                str(tmp_path),
                "PRIVATE KEY",
            ):
                assert private not in text

            # A recipient who accepts the signing key downloads straight from
            # the bucket and verifies every byte.
            accepted = accept_invitation_keys(
                invitation, tuple(key.key_id for key in trust.keys)
            )
            received = tmp_path / "recipient"
            assert fetch_invitation(invitation, accepted, received, LOCAL) == manifest
            # The recipient's app opens the same file offline.
            other = LocalOwner(tmp_path / "other")
            try:
                preview = other.sources.review_handoff(document)
                assert preview["manifest_id"] == identity
                assert preview["mirrors"] == [public_url]
            finally:
                other.close()

            for missing in ("f" * 64, "not-an-id"):
                with pytest.raises(module.PreservationError):
                    owner.bucket_handoff(missing)
            with pytest.raises(module.PreservationError, match="invalid_control"):
                owner.control("community-bucket-contents", {"offset": -1})
            with pytest.raises(module.PreservationError):
                owner.control("community-bucket-contents", {"other": 1})
    finally:
        owner.close()


def test_a_model_not_confirmed_in_the_bucket_has_no_handoff(
    tmp_path: Path, keychain: dict[str, BucketCredentials]
) -> None:
    owner = LocalOwner(tmp_path / "owner")
    owner.community.bucket_policy = LOCAL
    try:
        assert owner.control("community-bucket-contents", {})["models"] == []
        with pytest.raises(module.PreservationError, match="bucket_model_unavailable"):
            owner.bucket_handoff("a" * 64)
        with bucket() as (_server, client):
            endpoint, public_url = addresses(client)
            owner.control("community-bucket-connect", details(endpoint, public_url))
        state = owner.community.bucket()
        owner.community._save_bucket(state.model_copy(update={"pending": ("a" * 64,)}))
        # Pending uploads may be public, but were never confirmed complete.
        with pytest.raises(module.PreservationError, match="bucket_model_unavailable"):
            owner.bucket_handoff("a" * 64)
    finally:
        owner.close()


# Stored bucket.json compatibility --------------------------------------------

# Exactly what the previous release wrote for a bucket with one synced model.
_V1_TARGET = {
    "schema_version": "sovereign.bucket-target.v1",
    "provider": "r2",
    "endpoint": "https://" + "0" * 32 + ".r2.cloudflarestorage.com",
    "region": "auto",
    "bucket": "models",
    "path_style": True,
    "public_url": "https://mirror.example.org",
    "key_name": "community-0123456789abcdef",
}
_V1_STATE = {
    "schema_version": "sovereign.community-bucket.v1",
    "target": _V1_TARGET,
    "synced": ["b" * 64],
    "pending": [],
    "last_error": None,
    "last_synced": 1700000000,
}


class _ReleasedBucketState(Record):
    """The bucket state as the previous release reads it (strict, v1 only)."""

    schema_version: Literal["sovereign.community-bucket.v1"]
    target: BucketTarget
    synced: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()
    last_error: str | None = None
    last_synced: int | None = None


def test_a_bucket_saved_by_the_previous_release_still_reads_and_stays_v1(
    tmp_path: Path,
) -> None:
    owner = LocalOwner(tmp_path / "owner")
    try:
        path = owner.home / "sharing/community/bucket.json"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        released = canonical_bytes(_V1_STATE)
        path.write_bytes(released)
        state = owner.community.bucket()
        assert state is not None and state.synced == ("b" * 64,)
        assert state.synced_at == {}
        summary = owner.control("community-read", {})["bucket"]
        assert summary["jurisdiction"] is None and summary["model_count"] == 1
        # Saving it again without new information keeps the released bytes.
        assert module.encode_bucket(state) == released
        assert owner.community._save_bucket(state)
        assert path.read_bytes() == released
        _ReleasedBucketState.model_validate_json(path.read_bytes())
        # The model's upload time is unknown; the list says so plainly.
        (row,) = owner.control("community-bucket-contents", {})["models"]
        assert row["synced_at"] is None and row["name"] is None
    finally:
        owner.close()


def test_upload_times_are_version_2_which_older_releases_refuse(
    tmp_path: Path,
) -> None:
    state = module.BucketState.model_validate_json(canonical_bytes(_V1_STATE))
    timed = module.BucketState.model_validate(
        {
            **dict(state),
            "schema_version": "sovereign.community-bucket.v2",
            "synced_at": {"b" * 64: 1700000001},
        }
    )
    encoded = module.encode_bucket(timed)
    assert json.loads(encoded)["schema_version"] == "sovereign.community-bucket.v2"
    assert module.BucketState.model_validate_json(encoded) == timed
    # An older release refuses the new version outright instead of guessing.
    with pytest.raises(ValidationError):
        _ReleasedBucketState.model_validate_json(encoded)
    # Version 1 cannot carry upload times, and times name only synced models.
    for bad in (
        {**_V1_STATE, "synced_at": {"b" * 64: 1}},
        {
            **_V1_STATE,
            "schema_version": "sovereign.community-bucket.v2",
            "synced_at": {"c" * 64: 1},
        },
        {**_V1_STATE, "unknown": True},
        {**_V1_STATE, "schema_version": "sovereign.community-bucket.v3"},
    ):
        with pytest.raises(ValueError):
            module.BucketState.model_validate_json(canonical_bytes(bad))
    # A jurisdiction lives in the endpoint, so the target itself is unchanged.
    eu = {
        **_V1_TARGET,
        "endpoint": "https://" + "0" * 32 + ".eu.r2.cloudflarestorage.com",
    }
    target = BucketTarget.model_validate_json(canonical_bytes(eu))
    assert r2_jurisdiction(target) == "eu"
    _ReleasedBucketState.model_validate_json(
        canonical_bytes({**_V1_STATE, "target": eu})
    )


def test_connecting_passes_the_new_choices_and_refuses_unsafe_ones(
    tmp_path: Path, keychain: dict[str, BucketCredentials]
) -> None:
    owner = LocalOwner(tmp_path / "owner")
    try:
        base = {"bucket": "models", "access_key": "a", "secret_key": "b"}
        for body, code in (
            (
                {
                    **base,
                    "provider": "s3",
                    "region": "us-east-1",
                    "public_url": "http://d1234abcd.cloudfront.net",
                },
                "bucket_public_url_invalid",
            ),
            (
                {
                    **base,
                    "provider": "s3",
                    "region": "us-east-1",
                    "public_url": "https://user:pw@d1234abcd.cloudfront.net",
                },
                "bucket_public_url_invalid",
            ),
            (
                {
                    **base,
                    "provider": "r2",
                    "account_id": "0" * 32,
                    "public_url": "https://mirror.example.org",
                    "jurisdiction": "apac",
                },
                "bucket_details_invalid",
            ),
            (
                {
                    **base,
                    "provider": "b2",
                    "region": "us-west-004",
                    "jurisdiction": "eu",
                },
                "bucket_details_invalid",
            ),
        ):
            with pytest.raises(module.PreservationError, match=code) as raised:
                owner.control("community-bucket-connect", body)
            assert "user:pw" not in str(raised.value)
        assert keychain == {} and owner.community.bucket() is None
        providers = {
            item["id"]: item
            for item in owner.control("community-read", {})["bucket_providers"]
        }
        assert providers["s3"]["optional"] == ["public_url"]
        assert providers["r2"]["optional"] == ["jurisdiction"]
        assert [
            choice["value"] for choice in providers["r2"]["choices"]["jurisdiction"]
        ] == ["", "eu", "fedramp", "us"]
        assert (
            providers["other"]["optional"] == [] and providers["other"]["choices"] == {}
        )
    finally:
        owner.close()


def test_a_cloudfront_address_is_what_the_owner_sees_and_recipients_get(
    tmp_path: Path,
    keychain: dict[str, BucketCredentials],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An S3 bucket behind a CDN: uploads to S3, everything public names the CDN."""
    seen: list[tuple[str, str]] = []

    def check(client, _policy):
        seen.append((client.target.endpoint, client.target.public_url))
        return {"status": "ready", "detail": ""}

    monkeypatch.setattr(module, "check_bucket", check)
    owner = LocalOwner(tmp_path / "owner")
    try:
        ready = owner.control(
            "community-bucket-connect",
            {
                "provider": "s3",
                "region": "eu-central-1",
                "bucket": "sovereign-mirror-example",
                "public_url": "https://d1234abcd.cloudfront.net",
                "access_key": "AKIAEXAMPLE",
                "secret_key": "secret",
            },
        )
        assert ready["status"] == "ready"
        assert seen == [
            (
                "https://s3.eu-central-1.amazonaws.com",
                "https://d1234abcd.cloudfront.net",
            )
        ]
        assert ready["bucket"]["public_url"] == "https://d1234abcd.cloudfront.net"
        saved_state = owner.community.bucket()
        assert saved_state.target.provider == "s3"
        assert saved_state.target.public_url == "https://d1234abcd.cloudfront.net"
        # Nothing new to store: older releases still read this bucket.
        _ReleasedBucketState.model_validate_json(
            (owner.home / "sharing/community/bucket.json").read_bytes()
        )
        r2 = owner.control(
            "community-bucket-connect",
            {
                "provider": "r2",
                "account_id": "0" * 32,
                "jurisdiction": "eu",
                "bucket": "models",
                "public_url": "https://mirror.example.org",
                "access_key": "a",
                "secret_key": "b",
            },
        )
        assert seen[-1][0] == "https://" + "0" * 32 + ".eu.r2.cloudflarestorage.com"
        assert r2["bucket"]["jurisdiction"] == "eu"
        # The replaced bucket's keys are forgotten.
        assert len(keychain) == 1
    finally:
        owner.close()
