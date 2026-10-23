"""CLI, ordinary static publication and failed-update security boundaries."""

import base64
import json
from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from securesystemslib.signer import CryptoSigner
from test_cli_catalog import command
from test_discovery import Publisher, follow, mirror
from tuf.api.exceptions import RepositoryError
from tuf.api.metadata import Metadata, MetaFile, Root

from foxlight_model_preservation import discovery
from foxlight_model_preservation.discovery import (
    bootstrap_identity,
    export_source,
    fetch_selection,
    follow_source,
    list_sources,
    refresh_source,
    search_sources,
    source_identity,
)
from foxlight_model_preservation.discovery_cli import DiscoveryArguments, run_command
from foxlight_model_preservation.discovery_models import (
    DiscoveryCacheBundle,
    DiscoveryCatalogTarget,
)
from foxlight_model_preservation.discovery_publish import (
    DiscoverySigningKeys,
    publish_catalog,
)
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationCancelled, OperationControl
from foxlight_model_preservation.signing import encode_record


@pytest.mark.parametrize("consistent_snapshot", [True, False])
def test_publication_with_independent_role_keys_and_no_listener(
    tmp_path, capture, consistent_snapshot
):
    keys = {
        role: Ed25519PrivateKey.generate()
        for role in ("root", "timestamp", "snapshot", "targets")
    }
    root = Metadata(
        Root(
            expires=datetime.now(UTC) + timedelta(days=30),
            consistent_snapshot=consistent_snapshot,
        )
    )
    for role, key in keys.items():
        root.signed.add_key(CryptoSigner(key).public_key, role)
    root.sign(CryptoSigner(keys["root"]))
    fixture = Publisher(tmp_path / "input", capture)
    profile = DiscoveryCatalogTarget.model_validate_json(
        json.dumps(fixture.targets.signed.targets["catalog.json"].custom)
    )
    public = tmp_path / "static"
    role_keys = DiscoverySigningKeys(
        keys["targets"], keys["snapshot"], keys["timestamp"]
    )
    expires = datetime.now(UTC) + timedelta(days=1)
    publish_catalog(
        public,
        root.to_bytes(),
        fixture.envelope,
        profile,
        role_keys,
        version=1,
        expires=expires,
    )
    bootstrap = fixture.bootstrap(str(public)).model_copy(
        update={"root": base64.b64encode(root.to_bytes()).decode()}
    )
    cache = tmp_path / "consumer"
    source = follow(cache, bootstrap, allow_local=True)
    assert refresh_source(cache, source.source_id).freshness == "fresh"
    assert search_sources(cache, "offline")
    before = (public / "metadata/timestamp.json").read_bytes()
    with pytest.raises(PreservationError, match="version must increase"):
        publish_catalog(
            public,
            root.to_bytes(),
            fixture.envelope,
            profile,
            role_keys,
            version=1,
            expires=expires,
        )
    assert (public / "metadata/timestamp.json").read_bytes() == before
    wrong = DiscoverySigningKeys(keys["root"], keys["snapshot"], keys["timestamp"])
    with pytest.raises(RepositoryError):
        publish_catalog(
            tmp_path / "bad",
            root.to_bytes(),
            fixture.envelope,
            profile,
            wrong,
            version=2,
            expires=expires,
        )
    assert not (tmp_path / "bad").exists()
    assert not list(public.rglob("*.pem"))
    if not consistent_snapshot:
        committed = {
            str(path.relative_to(public)): path.read_bytes()
            for path in public.rglob("*")
            if path.is_file()
        }
        fixture.name = "A changed catalog"
        fixture.publish(2)
        changed = DiscoveryCatalogTarget.model_validate_json(
            json.dumps(fixture.targets.signed.targets["catalog.json"].custom)
        )
        successor = discovery.parse_discovery_role(root.to_bytes(), Root)
        successor.signed.version = 2
        successor.signed.consistent_snapshot = True
        successor.sign(CryptoSigner(keys["root"]))
        for update_root in (root.to_bytes(), successor.to_bytes()):
            with pytest.raises(PreservationError, match="updates require consistent"):
                publish_catalog(
                    public,
                    update_root,
                    fixture.envelope,
                    changed,
                    role_keys,
                    version=2,
                    expires=expires,
                )
            assert {
                str(path.relative_to(public)): path.read_bytes()
                for path in public.rglob("*")
                if path.is_file()
            } == committed
        assert refresh_source(cache, source.source_id).freshness == "fresh"


def test_installed_command_flow_and_source_failures_have_nonzero_exit(
    tmp_path, capture
):
    publisher = Publisher(tmp_path / "publisher", capture)
    cache, bootstrap_path = tmp_path / "cache", tmp_path / "source.json"
    root_path = tmp_path / "root.json"
    root_path.write_bytes(publisher.initial)
    created = command(
        "source-bootstrap",
        "--root",
        str(root_path),
        "--name",
        "Local source",
        "--endpoint",
        str(publisher.path),
        "--output",
        str(bootstrap_path),
    )
    assert created.returncode == 0, created.stderr
    identity = json.loads(created.stdout)
    inspected = command("source-inspect", "--source-file", str(bootstrap_path))
    assert inspected.returncode == 0
    followed = command(
        "source-follow",
        "--cache",
        str(cache),
        "--source-file",
        str(bootstrap_path),
        "--expected-bootstrap-id",
        identity["bootstrap_id"],
        "--allow-local",
    )
    assert followed.returncode == 0, followed.stderr
    source = identity["source_id"]
    refollow = (
        "source-follow",
        "--cache",
        str(cache),
        "--source-file",
        str(bootstrap_path),
        "--expected-bootstrap-id",
        identity["bootstrap_id"],
    )
    # Re-following without policy flags keeps the existing local grant.
    assert command(*refollow).returncode == 0
    refreshed = command("source-refresh", "--cache", str(cache), "--source", source)
    assert refreshed.returncode == 0, refreshed.stderr
    # An explicit --no-allow-local revokes it; --allow-local restores it.
    assert command(*refollow, "--no-allow-local").returncode == 0
    revoked = command("source-refresh", "--cache", str(cache), "--source", source)
    assert revoked.returncode != 0
    assert command(*refollow, "--allow-local").returncode == 0
    searched = command("source-search", "writing", "--cache", str(cache))
    data = json.loads(searched.stdout)
    assert (
        data["network_requests"] == 0
        and data["results"][0]["manifest"] == capture.identity
    )
    trust = tmp_path / "independent-bundle-trust.json"
    trust.write_bytes(encode_record(capture.trust))
    fetched = command(
        "source-fetch",
        "--cache",
        str(cache),
        "--source",
        source,
        "--entry",
        "small_text",
        "--expected-manifest-id",
        capture.identity,
        "--bundle-trust",
        str(trust),
        "--allow-local",
        "--store",
        str(tmp_path / "fetched"),
    )
    assert fetched.returncode == 0, fetched.stderr
    exported = tmp_path / "offline-source.json"
    assert (
        command(
            "source-export",
            "--cache",
            str(cache),
            "--source",
            source,
            "--output",
            str(exported),
        ).returncode
        == 0
    )
    publisher.publish(2, wrong_catalog_key=True)
    failed = command("source-refresh", "--cache", str(cache), "--source", source)
    assert failed.returncode == 1 and source in json.loads(failed.stdout)["failures"]
    assert command("source-search", "writing", "--cache", str(cache)).returncode == 0
    assert (
        command("source-remove", "--cache", str(cache), "--source", source).returncode
        == 0
    )
    assert not json.loads(
        command("source-search", "writing", "--cache", str(cache)).stdout
    )["results"]


def test_cli_publisher_uses_protected_role_keys(tmp_path, capture):
    fixture = Publisher(tmp_path / "input", capture)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    key = private / "roles.pem"
    # Explicit local-test key; no checked-in or real operator key material.
    crypto_key = fixture.tuf_crypto_key
    key.write_bytes(
        crypto_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key.chmod(0o600)
    root, catalog, trust = [
        tmp_path / name for name in ("root.json", "catalog.json", "catalog-trust.json")
    ]
    root.write_bytes(fixture.initial)
    catalog.write_bytes(fixture.envelope)
    trust.write_bytes(encode_record(fixture.catalog_trust))
    profile = fixture.targets.signed.targets["catalog.json"].custom
    output = tmp_path / "public"
    result = command(
        "source-publish",
        "--root",
        str(root),
        "--index",
        str(catalog),
        "--catalog-id",
        profile["catalog_id"],
        "--catalog-trust",
        str(trust),
        "--targets-key",
        str(key),
        "--snapshot-key",
        str(key),
        "--timestamp-key",
        str(key),
        "--unencrypted",
        "--version",
        "1",
        "--output",
        str(output),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["serving_started"] is False
    bootstrap = fixture.bootstrap(str(output))
    cache = tmp_path / "cache"
    source = follow(cache, bootstrap, allow_local=True)
    refresh_source(cache, source.source_id)
    # Plain v1 catalogs need no enrichment; missing descriptions stay absent.
    assert search_sources(cache, "small_text")[0].locations[0].description is None


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://user:secret@example.invalid",
        "https://example.invalid/feed?q=secret",
        "file:///etc",
        "relative/source",
        "https://example.invalid/#fragment",
        "https://example.invalid/\r\nHeader: value",
    ],
)
def test_unsafe_bootstrap_endpoints_refuse_before_effects(tmp_path, capture, endpoint):
    publisher = Publisher(tmp_path / "publisher", capture)
    with pytest.raises(ValueError):
        publisher.bootstrap(endpoint)


def test_http_metadata_budget_stops_before_model_reads(tmp_path, capture, monkeypatch):
    publisher = Publisher(tmp_path / "publisher", capture)
    cache = tmp_path / "cache"
    original = discovery._Budget
    monkeypatch.setattr(discovery, "_Budget", partial(original, bytes_left=200))
    with mirror(publisher.path) as (endpoint, requests):
        source = follow(
            cache, publisher.bootstrap(endpoint), allow_http=True, allow_private=True
        )
        with pytest.raises(PreservationError):
            refresh_source(cache, source.source_id)
        assert not search_sources(cache, "")
        assert all("objects/" not in request for request in requests)


def test_batch_cancel_after_one_source_commit_stops_the_next(
    tmp_path, capture, monkeypatch
):
    first = Publisher(tmp_path / "first", capture)
    second = Publisher(tmp_path / "second", capture)
    cache = tmp_path / "cache"
    for publisher in (first, second):
        follow(cache, publisher.bootstrap(), allow_local=True)
    from foxlight_model_preservation import discovery_cli

    original = discovery_cli.refresh_source
    completed = []
    parent = OperationControl()

    def refresh(*args, **kwargs):
        result = original(*args, **kwargs)
        completed.append(result.source_id)
        parent.cancel()
        return result

    monkeypatch.setattr(discovery_cli, "refresh_source", refresh)
    arguments = DiscoveryArguments(command="source-refresh", cache=cache)
    with pytest.raises(OperationCancelled):
        run_command(arguments, parent)
    assert len(completed) == 1
    assert sum(status.catalog_id is not None for status in list_sources(cache)) == 1


def test_valid_new_root_and_poisoned_target_keep_historical_cache(tmp_path, capture):
    publisher = Publisher(tmp_path / "publisher", capture)
    cache = tmp_path / "cache"
    bootstrap = publisher.bootstrap()
    source = follow(cache, bootstrap, allow_local=True)
    refresh_source(cache, source.source_id)
    publisher.rotate()
    publisher.publish(2, wrong_catalog_key=True)
    with pytest.raises(PreservationError):
        refresh_source(cache, source.source_id)
    status = list_sources(cache)[0]
    assert status.root_version == 2 and status.freshness == "superseded-root"
    historical = export_source(cache, source.source_id)
    follow_source(tmp_path / "offline", historical, bootstrap_identity(bootstrap))
    assert list_sources(tmp_path / "offline")[0].freshness == "superseded-root"
    assert search_sources(tmp_path / "offline", "first")


def test_threshold_root_requires_all_signatures_and_rotation_endorsements(
    tmp_path, capture
):
    publisher = Publisher(tmp_path / "publisher", capture)
    second = CryptoSigner.generate_ed25519()
    publisher.root.signed.add_key(second.public_key, "root")
    publisher.root.signed.roles["root"].threshold = 2
    publisher.root.sign(publisher.tuf_key)
    publisher.initial = publisher.root.to_bytes()
    with pytest.raises(RepositoryError):
        bootstrap_identity(publisher.bootstrap())
    publisher.root.sign(second, append=True)
    publisher.initial = publisher.root.to_bytes()
    (publisher.path / "metadata/1.root.json").write_bytes(publisher.initial)
    source = follow(tmp_path / "cache", publisher.bootstrap(), allow_local=True)
    refresh_source(tmp_path / "cache", source.source_id)
    publisher.rotate()  # Only one of the two old roots has endorsed this yet.
    publisher.publish(2)
    with pytest.raises(PreservationError):
        refresh_source(tmp_path / "cache", source.source_id)
    publisher.root.sign(second, append=True)
    (publisher.path / "metadata/2.root.json").write_bytes(publisher.root.to_bytes())
    assert refresh_source(tmp_path / "cache", source.source_id).root_version == 2


def test_healthy_source_stays_browsable_when_other_update_is_poisoned(
    tmp_path, capture
):
    first = Publisher(tmp_path / "first", capture)
    second = Publisher(tmp_path / "second", capture)
    cache = tmp_path / "cache"
    for publisher in (first, second):
        source = follow(cache, publisher.bootstrap(), allow_local=True)
        refresh_source(cache, source.source_id)
    second.publish(2, wrong_catalog_key=True)
    result = run_command(
        DiscoveryArguments(command="source-refresh", cache=cache), OperationControl()
    )
    assert set(result["failures"]) == {source_identity(second.bootstrap())}
    assert len(search_sources(cache, "first")[0].locations) == 2


def test_local_model_hints_have_their_own_opt_in(tmp_path, capture):
    publisher = Publisher(tmp_path / "publisher", capture)
    cache = tmp_path / "cache"
    source = follow(cache, publisher.bootstrap(), allow_local=True)
    refresh_source(cache, source.source_id)
    with pytest.raises(PreservationError, match="separate explicit"):
        fetch_selection(
            cache,
            source.source_id,
            "small_text",
            capture.identity,
            capture.trust,
            tmp_path / "denied",
        )
    assert not (tmp_path / "denied").exists()
    fetch_selection(
        cache,
        source.source_id,
        "small_text",
        capture.identity,
        capture.trust,
        tmp_path / "allowed",
        allow_local=True,
    )


def test_expiry_and_signature_failures_are_distinct_and_never_fresh(tmp_path, capture):
    publisher = Publisher(tmp_path / "publisher", capture)
    cache = tmp_path / "cache"
    source = follow(cache, publisher.bootstrap(), allow_local=True)
    refresh_source(cache, source.source_id)
    publisher.publish(2, expires=datetime.now(UTC) - timedelta(hours=1))
    with pytest.raises(PreservationError):
        refresh_source(cache, source.source_id)
    status = list_sources(cache)[0]
    assert status.freshness == "retained" and "expired" in status.last_error
    publisher.publish(2)
    publisher.timestamp.sign(CryptoSigner.generate_ed25519())
    (publisher.path / "metadata/timestamp.json").write_bytes(
        publisher.timestamp.to_bytes()
    )
    with pytest.raises(PreservationError):
        refresh_source(cache, source.source_id)
    assert "signature" in list_sources(cache)[0].last_error
    assert search_sources(cache, "first")[0].locations[0].freshness == "retained"


def test_source_metadata_redirect_is_not_followed(tmp_path, capture):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    publisher = Publisher(tmp_path / "publisher", capture)
    with mirror(publisher.path) as (endpoint, requests):

        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", endpoint + self.path)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            source = follow(
                tmp_path / "cache",
                publisher.bootstrap(f"http://127.0.0.1:{server.server_port}"),
                allow_private=True,
                allow_http=True,
            )
            with pytest.raises(PreservationError):
                refresh_source(tmp_path / "cache", source.source_id)
            assert not requests
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


def test_cli_publisher_refuses_key_files_inside_public_output(tmp_path, capture):
    from foxlight_model_preservation.discovery_cli import _publish

    args = DiscoveryArguments(
        root=tmp_path / "root",
        output=tmp_path / "public",
        targets_key=tmp_path / "public/targets.pem",
        snapshot_key=tmp_path / "private/snapshot.pem",
        timestamp_key=tmp_path / "private/timestamp.pem",
    )
    with pytest.raises(PreservationError, match="outside public"):
        _publish(args)
    assert not args.output.exists()


@pytest.mark.parametrize("version", [None, "foxlight.discovery-source.v99"])
def test_missing_or_unknown_source_versions_refuse(tmp_path, capture, version):
    publisher = Publisher(tmp_path / "publisher", capture)
    record = publisher.bootstrap().model_dump(mode="json")
    record.pop("schema_version")
    if version is not None:
        record["schema_version"] = version
    from foxlight_model_preservation.discovery import read_source_document

    with pytest.raises(ValueError):
        read_source_document(json.dumps(record).encode())


def test_missing_catalog_profile_version_is_not_implicitly_v1(tmp_path, capture):
    publisher = Publisher(tmp_path / "publisher", capture)
    cache = tmp_path / "cache"
    source = follow(cache, publisher.bootstrap(), allow_local=True)
    refresh_source(cache, source.source_id)
    publisher.publish(2)
    publisher.targets.signed.targets["catalog.json"].custom.pop("schema_version")
    publisher.targets.sign(publisher.tuf_key)
    publisher.snapshot.signed.meta["targets.json"] = MetaFile.from_data(
        2, publisher.targets.to_bytes(), ["sha256"]
    )
    publisher.snapshot.sign(publisher.tuf_key)
    publisher.timestamp.signed.snapshot_meta = MetaFile.from_data(
        2, publisher.snapshot.to_bytes(), ["sha256"]
    )
    publisher.timestamp.sign(publisher.tuf_key)
    for role, metadata in (
        ("targets", publisher.targets),
        ("snapshot", publisher.snapshot),
        ("timestamp", publisher.timestamp),
    ):
        (publisher.path / f"metadata/2.{role}.json").write_bytes(metadata.to_bytes())
        (publisher.path / f"metadata/{role}.json").write_bytes(metadata.to_bytes())
    with pytest.raises(PreservationError):
        refresh_source(cache, source.source_id)
    assert search_sources(cache, "first")[0].locations[0].freshness == "retained"


def test_future_private_cache_version_is_preserved_not_reset(tmp_path, capture):
    publisher = Publisher(tmp_path / "publisher", capture)
    cache = tmp_path / "cache"
    bootstrap = publisher.bootstrap()
    source = follow(cache, bootstrap, allow_local=True)
    state_file = cache / (source.source_id + ".json")
    state = json.loads(state_file.read_bytes())
    state["schema_version"] = "foxlight.discovery-state.v99"
    state_file.write_text(json.dumps(state))
    before = state_file.read_bytes()
    with pytest.raises(PreservationError, match="cache version"):
        follow(cache, bootstrap, allow_local=True)
    assert state_file.read_bytes() == before


def test_large_authenticated_cache_round_trips_above_ordinary_metadata_bound(
    tmp_path, capture
):
    publisher = Publisher(tmp_path / "publisher", capture)
    cache = tmp_path / "initial"
    source = follow(cache, publisher.bootstrap(), allow_local=True)
    refresh_source(cache, source.source_id)
    bundle = DiscoveryCacheBundle.model_validate_json(
        export_source(cache, source.source_id)
    )
    roots = []
    for version in range(2, 102):
        metadata = Publisher.make_root(version, publisher.tuf_key)
        metadata.signed.unrecognized_fields["padding"] = "p" * 63000
        metadata.sign(publisher.tuf_key)
        data = metadata.to_bytes()
        assert len(data) <= 64 * 1024
        roots.append(base64.b64encode(data).decode())
    bundle = bundle.model_copy(update={"roots": tuple(roots)})
    data = encode_record(bundle)
    assert 8 * 1024 * 1024 < len(data) < 32 * 1024 * 1024
    large = tmp_path / "large-cache"
    followed = follow_source(large, data, bootstrap_identity(bundle.bootstrap))
    assert followed.root_version == 101
    assert list_sources(large)[0].freshness == "superseded-root"
    assert search_sources(large, "first")
    exported = export_source(large, source.source_id)
    follow_source(
        tmp_path / "another-consumer", exported, bootstrap_identity(bundle.bootstrap)
    )
    assert search_sources(tmp_path / "another-consumer", "writing")


def test_truncated_chunked_metadata_fails_over_without_cli_traceback(tmp_path, capture):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    publisher = Publisher(tmp_path / "publisher", capture)
    with mirror(publisher.path) as (healthy, healthy_requests):

        class Truncated(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self.wfile.write(b"100\r\npartial")
                self.wfile.flush()
                self.close_connection = True

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Truncated)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            bootstrap = publisher.bootstrap(
                f"http://127.0.0.1:{server.server_port}", healthy
            )
            cache = tmp_path / "cache"
            source = follow(cache, bootstrap, allow_private=True, allow_http=True)
            result = command(
                "source-refresh", "--cache", str(cache), "--source", source.source_id
            )
            assert result.returncode == 0, result.stderr
            assert "Traceback" not in result.stderr and healthy_requests
            assert search_sources(cache, "first")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


@pytest.mark.parametrize("successor_state", ["accepted", "observed", "incoming"])
def test_import_cannot_restore_retired_root_authority(
    tmp_path, capture, successor_state
):
    """Higher role versions cannot revive keys revoked by a known successor root."""
    publisher = Publisher(tmp_path / "publisher", capture)
    bootstrap = publisher.bootstrap()
    retired_signer = publisher.tuf_key
    cache = tmp_path / "cache"
    source = follow(cache, bootstrap, allow_local=True)
    refresh_source(cache, source.source_id)
    publisher.rotate()
    successor = publisher.root.to_bytes()
    publisher.publish(2, wrong_catalog_key=successor_state != "accepted")
    if successor_state == "accepted":
        refresh_source(cache, source.source_id)
    elif successor_state == "observed":
        with pytest.raises(PreservationError):
            refresh_source(cache, source.source_id)
    before = export_source(cache, source.source_id)
    # A compromised old online signer can create internally consistent, higher
    # role versions. Keeping the real successor chain does not restore its grant.
    publisher.tuf_key = retired_signer
    publisher.name = "Forged with retired authority"
    publisher.publish(3)
    forged = DiscoveryCacheBundle(
        bootstrap=bootstrap,
        roots=(base64.b64encode(successor).decode(),),
        metadata={
            role: base64.b64encode(metadata.to_bytes()).decode()
            for role, metadata in (
                ("timestamp", publisher.timestamp),
                ("snapshot", publisher.snapshot),
                ("targets", publisher.targets),
            )
        },
        accepted_root_version=1,
        catalog=base64.b64encode(publisher.envelope).decode(),
    )
    with pytest.raises(PreservationError, match="root authority"):
        follow_source(cache, encode_record(forged), bootstrap_identity(bootstrap))
    assert export_source(cache, source.source_id) == before
    assert not search_sources(cache, "forged")


def test_static_root_rotation_keeps_committed_role_authority(
    tmp_path, capture, monkeypatch
):
    """Stage overlapping online keys before revocation, preserving each generation."""
    publisher = Publisher(tmp_path / "producer", capture)
    profile = DiscoveryCatalogTarget.model_validate_json(
        json.dumps(publisher.targets.signed.targets["catalog.json"].custom)
    )
    public = tmp_path / "static"
    old_keys = DiscoverySigningKeys(*(publisher.tuf_crypto_key for _ in range(3)))
    new_private = Ed25519PrivateKey.generate()
    new_signer = CryptoSigner(new_private)
    new_keys = DiscoverySigningKeys(*(new_private for _ in range(3)))
    expires = datetime.now(UTC) + timedelta(days=1)

    def publish(root, keys, version):
        return publish_catalog(
            public,
            root.to_bytes(),
            publisher.envelope,
            profile,
            keys,
            version=version,
            expires=expires,
        )

    def contents():
        return {
            str(path.relative_to(public)): path.read_bytes()
            for path in public.rglob("*")
            if path.is_file()
        }

    publish(publisher.root, old_keys, 1)
    overlap = Metadata(Root(version=2, expires=expires))
    overlap.signed.add_key(new_signer.public_key, "root")
    for role in ("timestamp", "snapshot", "targets"):
        overlap.signed.add_key(publisher.tuf_key.public_key, role)
        overlap.signed.add_key(new_signer.public_key, role)
    overlap.sign(new_signer)
    overlap.sign(publisher.tuf_key, append=True)
    before = contents()
    # New role signatures must remain readable by clients on the old root.
    with pytest.raises(RepositoryError):
        publish(overlap, new_keys, 2)
    assert contents() == before
    from foxlight_model_preservation import discovery_publish

    write = discovery_publish.atomic_bytes

    def crash_before_timestamp(root, name, data):
        if name == "metadata/timestamp.json":
            raise OSError("synthetic publication interruption")
        write(root, name, data)

    with monkeypatch.context() as patch:
        patch.setattr(discovery_publish, "atomic_bytes", crash_before_timestamp)
        with pytest.raises(OSError, match="synthetic"):
            publish(overlap, old_keys, 2)
    # The new root is already visible, but the committed old timestamp and
    # versioned closure must still authenticate through a real cold updater.
    interrupted_cache = tmp_path / "interrupted-consumer"
    interrupted_source = follow(
        interrupted_cache, publisher.bootstrap(str(public)), allow_local=True
    )
    interrupted_status = refresh_source(interrupted_cache, interrupted_source.source_id)
    assert (
        interrupted_status.root_version == 2 and interrupted_status.freshness == "fresh"
    )
    publish(overlap, old_keys, 2)
    revoked = Publisher.make_root(3, new_signer)
    before = contents()
    # Conversely, the new root must still authenticate the committed old roles
    # until the timestamp switch. Move current signatures before revoking a key.
    with pytest.raises(RepositoryError):
        publish(revoked, new_keys, 3)
    assert contents() == before
    publish(overlap, new_keys, 3)
    publish(revoked, new_keys, 4)
    bootstrap = publisher.bootstrap(str(public))
    source = follow(tmp_path / "cache", bootstrap, allow_local=True)
    assert refresh_source(tmp_path / "cache", source.source_id).root_version == 3
