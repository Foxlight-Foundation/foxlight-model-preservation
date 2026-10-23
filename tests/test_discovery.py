"""Fresh clients and signed/faulted sources, using only isolated synthetic data."""

from __future__ import annotations

import base64
import json
import socket
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest import Capture
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from securesystemslib.signer import CryptoSigner
from tuf.api.metadata import (
    Metadata,
    MetaFile,
    Root,
    Snapshot,
    TargetFile,
    Targets,
    Timestamp,
)

from foxlight_model_preservation.discovery import (
    bootstrap_identity,
    export_source,
    follow_source,
    inspect_source,
    list_sources,
    refresh_source,
    remove_source,
    search_sources,
    select_entry,
    source_identity,
)
from foxlight_model_preservation.discovery_models import (
    DiscoveryCacheBundle,
    DiscoveryCatalogTarget,
    DiscoveryDescription,
    SourceBootstrap,
)
from foxlight_model_preservation.models import (
    Catalog,
    CatalogEntry,
    PreservationError,
    TrustProfile,
)
from foxlight_model_preservation.operations import OperationCancelled, OperationControl
from foxlight_model_preservation.signing import (
    CATALOG_TYPE,
    digest,
    encode_record,
    public_record,
    sign_record,
)
from foxlight_model_preservation.storage import verify_store
from foxlight_model_preservation.transfer import TransferPolicy, fetch


class Publisher:
    """Stock python-tuf producer; root/catalog/bundle keys are distinct."""

    def __init__(self, root: Path, capture: Capture, *, name="First model"):
        self.path = root
        (root / "metadata").mkdir(parents=True)
        (root / "targets").mkdir()
        self.capture = capture
        self.tuf_crypto_key = Ed25519PrivateKey.generate()
        self.tuf_key = CryptoSigner(self.tuf_crypto_key)
        self.catalog_key = Ed25519PrivateKey.generate()
        self.catalog_trust = TrustProfile(
            schema_version="foxlight.model-preservation.trust.v1",
            keys=(public_record(self.catalog_key),),
            threshold=1,
            as_of=datetime.now(UTC).isoformat(),
        )
        self.root = self.make_root(1, self.tuf_key)
        self.initial = self.root.to_bytes()
        (root / "metadata/1.root.json").write_bytes(self.initial)
        self.mirrors = (str(self.capture.store),)
        self.name = name
        self.publish()

    @staticmethod
    def make_root(version, key):
        md = Metadata(
            Root(version=version, expires=datetime.now(UTC) + timedelta(days=365))
        )
        for role in ("root", "timestamp", "snapshot", "targets"):
            md.signed.add_key(key.public_key, role)
        md.sign(key)
        return md

    def rotate(self):
        self.tuf_crypto_key = Ed25519PrivateKey.generate()
        new_key = CryptoSigner(self.tuf_crypto_key)
        md = self.make_root(self.root.signed.version + 1, new_key)
        md.sign(self.tuf_key, append=True)
        (self.path / f"metadata/{md.signed.version}.root.json").write_bytes(
            md.to_bytes()
        )
        self.root, self.tuf_key = md, new_key

    def publish(
        self,
        version=1,
        *,
        expires=None,
        wrong_catalog_key=False,
        manifest=None,
        description_manifest=None,
    ):
        expires = expires or datetime.now(UTC) + timedelta(days=1)
        identity = manifest or self.capture.identity
        catalog = Catalog(
            schema_version="foxlight.model-preservation.catalog.v1",
            description="Local synthetic models",
            entries=(
                CatalogEntry(
                    entry_id="small_text",
                    manifest=identity,
                    mirrors=self.mirrors,
                ),
            ),
        )
        envelope = encode_record(
            sign_record(
                catalog,
                (
                    Ed25519PrivateKey.generate()
                    if wrong_catalog_key
                    else self.catalog_key,
                ),
                CATALOG_TYPE,
            )
        )
        custom = DiscoveryCatalogTarget(
            catalog_id=digest(encode_record(catalog)),
            catalog_trust=self.catalog_trust,
            descriptions=(
                DiscoveryDescription(
                    entry_id="small_text",
                    manifest=description_manifest or identity,
                    name=self.name,
                    summary="A compact assistant for offline writing",
                    workloads=("text.generate",),
                    formats=("test-fixture",),
                    candidate_bundle_trust=self.capture.trust,
                    annotations={"future": {"card-field": [1, 2, "retained"]}},
                ),
            ),
        )
        info = TargetFile.from_data("catalog.json", envelope, ["sha256"])
        info.unrecognized_fields["custom"] = custom.model_dump(mode="json")
        self.targets = Metadata(
            Targets(version=version, expires=expires, targets={"catalog.json": info})
        )
        self.targets.sign(self.tuf_key)
        self.snapshot = Metadata(
            Snapshot(
                version=version,
                expires=expires,
                meta={
                    "targets.json": MetaFile.from_data(
                        version, self.targets.to_bytes(), ["sha256"]
                    ),
                },
            )
        )
        self.snapshot.sign(self.tuf_key)
        self.timestamp = Metadata(
            Timestamp(
                version=version,
                expires=expires,
                snapshot_meta=MetaFile.from_data(
                    version, self.snapshot.to_bytes(), ["sha256"]
                ),
            )
        )
        self.timestamp.sign(self.tuf_key)
        for role, md in (
            ("timestamp", self.timestamp),
            ("snapshot", self.snapshot),
            ("targets", self.targets),
        ):
            (self.path / "metadata" / (role + ".json")).write_bytes(md.to_bytes())
            (self.path / "metadata" / f"{version}.{role}.json").write_bytes(
                md.to_bytes()
            )
        (self.path / "targets" / f"{digest(envelope)}.catalog.json").write_bytes(
            envelope
        )
        self.envelope = envelope

    def bootstrap(self, *endpoints):
        return SourceBootstrap(
            name="Independent test source",
            root=base64.b64encode(self.initial).decode(),
            endpoints=endpoints or (str(self.path),),
        )


@pytest.fixture
def publisher(tmp_path, capture):
    return Publisher(tmp_path / "publisher", capture)


def follow(cache, bootstrap, **kwargs):
    return follow_source(
        cache, encode_record(bootstrap), bootstrap_identity(bootstrap), **kwargs
    )


@contextmanager
def mirror(path):
    requests = []

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append(self.path)
            super().do_GET()

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(Handler, directory=str(path))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_empty_consumer_follow_refresh_search_exact_fetch(
    publisher, tmp_path, monkeypatch
):
    """No official service, warm cache, Core or SDK is needed for cold discovery."""
    real_resolve = socket.getaddrinfo
    connections = []

    def resolve(host, *args, **kwargs):
        connections.append(host)
        assert host == "127.0.0.1", "an official or unknown service was contacted"
        return real_resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    cache = tmp_path / "empty-client"
    with (
        mirror(publisher.path) as (endpoint, requests),
        mirror(publisher.capture.store) as (weights_endpoint, weight_requests),
    ):
        publisher.mirrors = ("http://127.0.0.1:1", weights_endpoint)
        publisher.publish()
        bootstrap = publisher.bootstrap("http://127.0.0.1:1", endpoint)
        report = inspect_source(encode_record(bootstrap))
        assert report["authority"].startswith("discovery-only")
        source = follow(cache, bootstrap, allow_private=True, allow_http=True)
        assert source.freshness == "empty" and not requests
        status = refresh_source(cache, source.source_id)
        assert status.freshness == "fresh" and status.last_error is None
        assert list_sources(cache)[0] == status
        found = search_sources(cache, "offline text.generate")
        assert len(found) == 1 and found[0].manifest == publisher.capture.identity
        assert (
            found[0].locations[0].description.annotations["future"]["card-field"][-1]
            == "retained"
        )
        assert all("metadata/" in path or "targets/" in path for path in requests)
        assert not any("objects/" in path or "manifests/" in path for path in requests)
        selected = select_entry(
            cache, source.source_id, "small_text", publisher.capture.identity
        )
        with pytest.raises(PreservationError):
            fetch(
                selected.manifest,
                publisher.catalog_trust,
                selected.mirrors,
                tmp_path / "wrong-trust",
                TransferPolicy(allow_private=True, allow_http=True),
            )
        target = tmp_path / "selected"
        fetched = fetch(
            selected.manifest,
            publisher.capture.trust,
            selected.mirrors,
            target,
            TransferPolicy(allow_private=True, allow_http=True),
        )
        assert len(fetched.members) == 2
        verify_store(target, selected.manifest, publisher.capture.trust)
        assert any("objects/sha256/" in path for path in weight_requests)
    assert connections and all(host == "127.0.0.1" for host in connections)
    # Searches and offline cache exchange do not resolve or contact any server.
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: pytest.fail("offline DNS")
    )
    assert search_sources(cache, "first model")[0].manifest == selected.manifest
    exported = export_source(cache, source.source_id)
    fresh = follow_source(
        tmp_path / "offline-new", exported, bootstrap_identity(bootstrap)
    )
    assert fresh.freshness == "imported"
    assert (
        search_sources(tmp_path / "offline-new", "writing")[0].manifest
        == selected.manifest
    )


def test_first_contact_requires_exact_independent_bootstrap(publisher, tmp_path):
    bootstrap = publisher.bootstrap()
    assert inspect_source(encode_record(bootstrap))[
        "bootstrap_id"
    ] == bootstrap_identity(bootstrap)
    with pytest.raises(PreservationError, match="independently selected"):
        follow_source(tmp_path / "cache", encode_record(bootstrap), "f" * 64)
    assert not (tmp_path / "cache").exists()


def test_endpoint_policy_is_receiver_only(publisher, tmp_path):
    with mirror(publisher.path) as (endpoint, requests):
        source = follow(tmp_path / "cache", publisher.bootstrap(endpoint))
        with pytest.raises(PreservationError, match="refresh failed"):
            refresh_source(tmp_path / "cache", source.source_id)
        assert not requests
    local = follow(tmp_path / "local", publisher.bootstrap())
    with pytest.raises(PreservationError, match="refresh failed"):
        refresh_source(tmp_path / "local", local.source_id)


def test_poisoned_new_target_keeps_last_good_and_update_floors(publisher, tmp_path):
    cache = tmp_path / "cache"
    source = follow(cache, publisher.bootstrap(), allow_local=True)
    refresh_source(cache, source.source_id)
    publisher.publish(2, wrong_catalog_key=True)
    with pytest.raises(PreservationError, match="retained last accepted"):
        refresh_source(cache, source.source_id)
    assert search_sources(cache, "first")[0].manifest == publisher.capture.identity
    assert list_sources(cache)[0].last_error
    # A target rejection must not let a later source roll timestamp 2 back to 1.
    publisher.publish(1)
    with pytest.raises(PreservationError):
        refresh_source(cache, source.source_id)
    publisher.name = "Recovered source update"
    publisher.publish(3)
    assert refresh_source(cache, source.source_id).last_error is None
    assert search_sources(cache, "recovered source")


def test_stock_tuf_rotation_and_offline_chain(publisher, tmp_path):
    bootstrap = publisher.bootstrap()
    cache = tmp_path / "cache"
    source = follow(cache, bootstrap, allow_local=True)
    refresh_source(cache, source.source_id)
    publisher.rotate()
    publisher.publish(2)
    status = refresh_source(cache, source.source_id)
    assert status.source_id == source.source_id and status.root_version == 2
    offline = tmp_path / "offline"
    result = follow_source(
        offline, export_source(cache, source.source_id), bootstrap_identity(bootstrap)
    )
    assert result.root_version == 2 and result.freshness == "imported"
    assert search_sources(offline, "assistant")


def test_remove_refollow_and_import_cannot_reset_versions(publisher, tmp_path):
    cache = tmp_path / "cache"
    bootstrap = publisher.bootstrap()
    source = follow(cache, bootstrap, allow_local=True)
    refresh_source(cache, source.source_id)
    first = export_source(cache, source.source_id)
    publisher.publish(2)
    refresh_source(cache, source.source_id)
    remove_source(cache, source.source_id)
    assert not search_sources(cache, "") and not list_sources(cache)[0].enabled
    with pytest.raises(PreservationError, match="roll back"):
        follow_source(cache, first, bootstrap_identity(bootstrap))
    follow(cache, bootstrap, allow_local=True)
    publisher.publish(1)
    with pytest.raises(PreservationError):
        refresh_source(cache, source.source_id)
    assert search_sources(cache, "first")


def test_expired_signed_offline_snapshot_is_browsable_but_never_fresh(
    publisher, tmp_path
):
    publisher.publish(expires=datetime.now(UTC) - timedelta(hours=1))
    bundle = DiscoveryCacheBundle(
        bootstrap=publisher.bootstrap(),
        roots=(),
        accepted_root_version=1,
        metadata={
            name: base64.b64encode(md.to_bytes()).decode()
            for name, md in (
                ("timestamp", publisher.timestamp),
                ("snapshot", publisher.snapshot),
                ("targets", publisher.targets),
            )
        },
        catalog=base64.b64encode(publisher.envelope).decode(),
    )
    cache = tmp_path / "cache"
    state = follow_source(
        cache,
        encode_record(bundle),
        bootstrap_identity(bundle.bootstrap),
        allow_local=True,
    )
    assert state.freshness == "stale" and state.accepted_at is None
    assert search_sources(cache, "first")[0].locations[0].freshness == "stale"
    with pytest.raises(PreservationError):
        refresh_source(cache, state.source_id)
    assert search_sources(cache, "first")


def test_exact_identity_groups_all_suppliers_and_retains_conflicts(publisher, tmp_path):
    second = Publisher(
        tmp_path / "other-publisher",
        publisher.capture,
        name="Contradictory alternate name",
    )
    cache = tmp_path / "cache"
    for operator in (publisher, second):
        source = follow(cache, operator.bootstrap(), allow_local=True)
        refresh_source(cache, source.source_id)
    results = search_sources(cache, "first model")
    assert len(results) == 1 and len(results[0].locations) == 2
    assert {loc.description.name for loc in results[0].locations} == {
        "First model",
        "Contradictory alternate name",
    }
    second.publish(2, manifest="a" * 64)
    refresh_source(cache, source_identity(second.bootstrap()))
    assert len(search_sources(cache, "")) == 2
    with pytest.raises(PreservationError, match="changed"):
        select_entry(
            cache,
            source_identity(second.bootstrap()),
            "small_text",
            publisher.capture.identity,
        )


def test_bad_description_does_not_mutate_cached_card_or_accept_update(
    publisher, tmp_path
):
    cache = tmp_path / "cache"
    source = follow(cache, publisher.bootstrap(), allow_local=True)
    refresh_source(cache, source.source_id)
    publisher.publish(2, description_manifest="c" * 64)
    with pytest.raises(PreservationError):
        refresh_source(cache, source.source_id)
    assert search_sources(cache, "first")[0].manifest == publisher.capture.identity
    verify_store(
        publisher.capture.store, publisher.capture.identity, publisher.capture.trust
    )


def test_corrupt_source_is_isolated_from_other_healthy_sources(publisher, tmp_path):
    second = Publisher(tmp_path / "other", publisher.capture)
    cache = tmp_path / "cache"
    for operator in (publisher, second):
        source = follow(cache, operator.bootstrap(), allow_local=True)
        refresh_source(cache, source.source_id)
    (cache / (source_identity(second.bootstrap()) + ".json")).write_text(
        '{"corrupt": true}'
    )
    assert len(search_sources(cache, "first")[0].locations) == 1
    assert {status.freshness for status in list_sources(cache)} == {"invalid", "fresh"}


def test_tampered_offline_bundle_is_never_enrolled(publisher, tmp_path):
    cache = tmp_path / "cache"
    source = follow(cache, publisher.bootstrap(), allow_local=True)
    refresh_source(cache, source.source_id)
    bundle = json.loads(export_source(cache, source.source_id))
    bundle["catalog"] = base64.b64encode(b"{}").decode()
    with pytest.raises((PreservationError, ValueError)):
        follow_source(
            tmp_path / "new",
            json.dumps(bundle).encode(),
            bootstrap_identity(publisher.bootstrap()),
        )
    assert not (tmp_path / "new").exists()


def test_cancelled_update_retains_usable_cache(publisher, tmp_path):
    cache = tmp_path / "cache"
    source = follow(cache, publisher.bootstrap(), allow_local=True)
    refresh_source(cache, source.source_id)
    control = OperationControl()
    control.cancel()
    with pytest.raises(OperationCancelled):
        refresh_source(cache, source.source_id, control=control)
    assert search_sources(cache, "first")


def test_cache_rejects_shared_directory_and_symlink_state(publisher, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir(mode=0o755)
    with pytest.raises(PreservationError, match="owner-only"):
        follow(cache, publisher.bootstrap())
    cache.chmod(0o700)
    source = follow(cache, publisher.bootstrap(), allow_local=True)
    state = cache / (source.source_id + ".json")
    preserved = tmp_path / "state-original"
    state.rename(preserved)
    state.symlink_to(preserved)
    with pytest.raises(OSError):
        refresh_source(cache, source.source_id)
    assert list_sources(cache)[0].freshness == "invalid"


def test_future_clock_attempt_is_recoverable_without_losing_floors(
    publisher, tmp_path, monkeypatch
):
    from foxlight_model_preservation import discovery
    from foxlight_model_preservation.discovery import accept_source_clock_rollback

    cache = tmp_path / "cache"
    bootstrap = publisher.bootstrap()
    source = follow(cache, bootstrap, allow_local=True)
    refresh_source(cache, source.source_id)
    publisher.publish(2)

    class Ahead(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(days=3650)

    monkeypatch.setattr(discovery, "datetime", Ahead)
    refresh_source(cache, source.source_id)
    monkeypatch.undo()
    publisher.publish(3)
    with pytest.raises(PreservationError, match="clock moved backwards"):
        refresh_source(cache, source.source_id)
    # Neither removal nor re-following clears the future attempt time.
    remove_source(cache, source.source_id)
    follow(cache, bootstrap, allow_local=True)
    with pytest.raises(PreservationError, match="clock moved backwards"):
        refresh_source(cache, source.source_id)
    accepted = accept_source_clock_rollback(cache, source.source_id)
    assert accepted.last_attempt_at is None
    assert refresh_source(cache, source.source_id).freshness == "fresh"
    # Floors survived: an older catalog version still cannot be replayed.
    publisher.publish(2)
    with pytest.raises(PreservationError):
        refresh_source(cache, source.source_id)


def test_refollow_without_flags_keeps_grants_and_flags_still_revoke(
    publisher, tmp_path
):
    cache = tmp_path / "cache"
    bootstrap = publisher.bootstrap()
    source = follow(cache, bootstrap, allow_local=True, allow_http=True)
    follow(cache, bootstrap)
    assert refresh_source(cache, source.source_id).freshness == "fresh"
    follow(cache, bootstrap, allow_local=False)
    with pytest.raises(PreservationError):
        refresh_source(cache, source.source_id)
