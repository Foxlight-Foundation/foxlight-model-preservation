"""Peer hints cannot replace TUF/model authority, local grants or complete bytes."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_discovery import Publisher, follow

from foxlight_model_preservation.discovery import (
    export_source,
    list_sources,
    refresh_source,
    search_sources,
    source_identity,
)
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationCancelled, OperationControl
from foxlight_model_preservation.peer import (
    PeerSource,
    follow_peer_source,
    inspect_peer_source,
    peer_source_identity,
    refresh_peer_source,
)
from foxlight_model_preservation.peer_runtime import (
    PeerProfile,
    _command,
    bootstrap_address,
    content_id,
    read_profile,
)
from foxlight_model_preservation.signing import canonical_bytes, digest, encode_record
from foxlight_model_preservation.storage import atomic_bytes, directory

CID = "bafkreigh2akiscaildc6tvep3jhk5xb3wpymuc3g63wy2bmy3z2bl3rn44"
NAME = "k51qzi5uqu5dl0ppxz7s1b5ou99zdf5ridzo2lvjti880d6t0tqyamyhwglmxf"


@pytest.fixture(autouse=True)
def peer_extra():
    pytest.importorskip("multiformats")


class Transport:
    """Only the metadata transport is controlled; TUF and model crypto are real."""

    def __init__(
        self, snapshot: bytes, *, name: str = NAME, resolved: str = "/ipfs/" + CID
    ):
        self.snapshot = snapshot
        self.resolved = resolved
        self.name = name
        self.control = OperationControl()
        self.exported_bytes = 0
        self.paths = []
        self.profile = PeerProfile(mode="receive", configuration_sha256="a" * 64)

    def command(self, *args, maximum=65536):
        self.paths.append(args)
        return self.resolved.encode()

    def cat(self, path, *, maximum, sink=None):
        self.paths.append(path)
        self.exported_bytes += len(self.snapshot)
        if len(self.snapshot) > maximum:
            raise PreservationError("too large")
        return self.snapshot


def setup(tmp_path, capture):
    producer = Publisher(tmp_path / "publisher", capture)
    bootstrap = producer.bootstrap(str(producer.path))
    cache = tmp_path / "producer-cache"
    source = follow(cache, bootstrap, allow_local=True)
    refresh_source(cache, source.source_id)
    record = PeerSource(source=bootstrap, ipns=NAME, snapshots=(CID,))
    receiver = tmp_path / "receiver"
    follow_peer_source(receiver, encode_record(record), peer_source_identity(record))
    return producer, cache, source.source_id, record, receiver


def test_offline_enrollment_and_ordinary_search_need_no_runtime(tmp_path, capture):
    producer, cache, source, record, receiver = setup(tmp_path, capture)
    inspected = inspect_peer_source(encode_record(record))
    assert source_identity(inspected.source) == source
    assert list_sources(receiver)[0].freshness == "empty"
    transport = Transport(export_source(cache, source))
    assert refresh_peer_source(receiver, source, transport).freshness == "fresh"
    assert search_sources(receiver, "offline")[0].manifest == capture.identity
    assert all("removed" not in str(path) for path in transport.paths)
    exported = json.loads(export_source(receiver, source))
    assert exported["bootstrap"] == record.source.model_dump(mode="json")
    assert not exported.get("allow_local")
    # The trusted source's local endpoint is still ungranted after peer update.
    with pytest.raises(PreservationError):
        refresh_source(receiver, source)
    assert search_sources(receiver, "offline")
    with pytest.raises(PreservationError, match="independently"):
        follow_peer_source(tmp_path / "wrong", encode_record(record), "f" * 64)
    assert not (tmp_path / "wrong").exists()


def test_peer_snapshot_cannot_substitute_another_root_or_rollback(tmp_path, capture):
    producer, cache, source, _record, receiver = setup(tmp_path, capture)
    first = export_source(cache, source)
    refresh_peer_source(receiver, source, Transport(first))
    other = Publisher(tmp_path / "other", capture)
    other_cache = tmp_path / "other-cache"
    other_source = follow(
        other_cache, other.bootstrap(str(other.path)), allow_local=True
    )
    refresh_source(other_cache, other_source.source_id)
    with pytest.raises(PreservationError, match="acceptable"):
        refresh_peer_source(
            receiver,
            source,
            Transport(export_source(other_cache, other_source.source_id)),
        )
    producer.publish(2)
    refresh_source(cache, source)
    refresh_peer_source(receiver, source, Transport(export_source(cache, source)))
    with pytest.raises(PreservationError):
        refresh_peer_source(receiver, source, Transport(first))
    assert list_sources(receiver)[0].catalog_id == list_sources(cache)[0].catalog_id
    # An attacker-controlled IPNS name remains unable to replace TUF authority.
    with pytest.raises(PreservationError):
        refresh_peer_source(receiver, source, Transport(first), historical=True)


def test_expired_snapshot_requires_explicit_pinned_historical_import(tmp_path, capture):
    producer, cache, source, record, receiver = setup(tmp_path, capture)
    from datetime import UTC, datetime, timedelta

    from foxlight_model_preservation.discovery_models import DiscoveryCacheBundle

    producer.publish(2, expires=datetime.now(UTC) - timedelta(days=1))
    snapshot = encode_record(
        DiscoveryCacheBundle(
            bootstrap=record.source,
            roots=(),
            accepted_root_version=1,
            metadata={
                role: base64.b64encode(getattr(producer, role).to_bytes()).decode()
                for role in ("timestamp", "snapshot", "targets")
            },
            catalog=base64.b64encode(producer.envelope).decode(),
        )
    )
    with pytest.raises(PreservationError):
        refresh_peer_source(receiver, source, Transport(snapshot))
    assert list_sources(receiver)[0].catalog_id is None
    imported = refresh_peer_source(
        receiver, source, Transport(snapshot), historical=True
    )
    assert imported.freshness == "stale"
    assert imported.accepted_at is None
    assert search_sources(receiver, "offline")
    no_pins = record.model_copy(update={"snapshots": ()})
    follow_peer_source(receiver, encode_record(no_pins), peer_source_identity(no_pins))
    with pytest.raises(PreservationError, match="pinned snapshot"):
        refresh_peer_source(receiver, source, Transport(snapshot), historical=True)


@pytest.mark.parametrize(
    "value",
    [
        CID.upper(),
        "https://gateway.example/ipfs/" + CID,
        CID + "/source.json",
        "../escape",
        "bafk",
        "",
    ],
)
def test_unsafe_peer_content_references_refuse(value):
    with pytest.raises(PreservationError):
        content_id(value)


@pytest.mark.parametrize(
    "value",
    [
        "example.org",
        "/dns/example.org/tcp/1/p2p/" + "1" * 40,
        "/ip4/127.0.0.1/tcp/0/p2p/" + "1" * 40,
        "/ip4/10.0.0.1/tcp/4001/p2p/" + "1" * 40,
    ],
)
def test_bootstrap_needs_explicit_private_permission(value):
    with pytest.raises(PreservationError):
        bootstrap_address(value, False)
    assert bootstrap_address("/ip4/127.0.0.1/tcp/4001/p2p/" + "1" * 40, True)


def test_native_response_limits_cancel_and_config_binding(tmp_path):
    script = tmp_path / "output.py"
    script.write_text('print("x"*1000)')
    import time

    with pytest.raises(PreservationError, match="byte bound"):
        _command(
            sys.executable,
            tmp_path,
            (str(script),),
            maximum=10,
            control=OperationControl(),
            deadline=time.monotonic() + 5,
        )
    active = OperationControl()
    active.cancel()
    with pytest.raises(OperationCancelled):
        _command(
            sys.executable,
            tmp_path,
            (str(script),),
            maximum=10000,
            control=active,
            deadline=time.monotonic() + 5,
        )
    root = tmp_path / "repo"
    root.mkdir(mode=0o700)
    profile = PeerProfile(
        mode="receive", configuration_sha256=digest(b'{"Bootstrap":[]}')
    )
    with directory(root) as descriptor:
        atomic_bytes(descriptor, "config", b"{}")
        atomic_bytes(descriptor, "preservation-peer.json", encode_record(profile))
    assert read_profile(root).mode == "receive"
    (root / "config").write_text('{"Bitswap":{"ServerEnabled":true}}')
    with pytest.raises(PreservationError, match="configuration changed"):
        read_profile(root)


def test_pinned_snapshot_file_version_and_keys_stay_separate(tmp_path, capture):
    _producer, _cache, _source, record, _receiver = setup(tmp_path, capture)
    changed = json.loads(encode_record(record))
    changed["allow_peer_network"] = True
    with pytest.raises(ValueError):
        inspect_peer_source(json.dumps(changed).encode())
    changed = json.loads(encode_record(record))
    changed["schema_version"] = "foxlight.peer-source.v9"
    with pytest.raises(ValueError):
        inspect_peer_source(json.dumps(changed).encode())
    assert inspect_peer_source(encode_record(record)).source == record.source
    with pytest.raises(PreservationError, match="256 KiB"):
        inspect_peer_source(b"x" * (256 * 1024 + 1))


def test_no_peer_command_makes_optional_runtime_a_default_dependency():
    # Ordinary help/list/verification must retain their original dependency closure.
    completed = subprocess.run(
        [sys.executable, "-m", "foxlight_model_preservation.cli", "--help"],
        capture_output=True,
    )
    assert completed.returncode == 0 and b"peer-refresh" in completed.stdout


def test_bootstrap_replacement_is_atomic_offline_and_preserves_identity(tmp_path):
    from foxlight_model_preservation.peer_runtime import set_bootstrap

    repo = tmp_path / "repo"
    repo.mkdir(mode=0o700)
    config = {"Bootstrap": [], "Identity": {"PeerID": "retained"}}
    profile = PeerProfile(
        mode="receive",
        configuration_sha256=digest(canonical_bytes(config)),
        allow_private_peers=True,
    )
    with directory(repo) as root:
        atomic_bytes(root, "config", canonical_bytes(config))
        atomic_bytes(root, "preservation-peer.json", encode_record(profile))
    contact = "/ip4/127.0.0.1/tcp/4001/p2p/" + "1" * 40
    assert set_bootstrap(repo, (contact,)) == (contact,)
    assert read_profile(repo) == profile
    assert json.loads((repo / "config").read_bytes())["Identity"] == config["Identity"]
    previous = (repo / "config").read_bytes()
    with pytest.raises(PreservationError):
        set_bootstrap(repo, ("/dns/attacker.invalid/tcp/4001",))
    assert (repo / "config").read_bytes() == previous
    import socket

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        (repo / "api").write_text(f"/ip4/127.0.0.1/tcp/{port}")
        with pytest.raises(PreservationError, match="stop"):
            set_bootstrap(repo, ())
        assert (repo / "config").read_bytes() == previous
    # The listener is gone: under the writer lock that marker is provably stale.
    assert set_bootstrap(repo, ()) == ()
    assert not (repo / "api").exists()
    (repo / "api").write_text("/unix/elsewhere")
    with pytest.raises(PreservationError, match="stop"):
        set_bootstrap(repo, ())
    assert (repo / "api").exists()


def test_model_trust_refuses_before_any_weight_read(tmp_path, capture):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from foxlight_model_preservation.peer import fetch_peer_model
    from foxlight_model_preservation.signing import public_record
    from foxlight_model_preservation.storage import manifest_path

    producer, cache, source, _record, receiver = setup(tmp_path, capture)
    producer.mirrors = ("ipfs://" + CID,)
    producer.publish(2)
    refresh_source(cache, source)
    refresh_peer_source(receiver, source, Transport(export_source(cache, source)))
    transport = Transport(
        (capture.store / manifest_path(capture.identity)).read_bytes()
    )
    with pytest.raises(PreservationError):
        fetch_peer_model(
            receiver,
            source,
            "small_text",
            capture.identity,
            capture.trust.model_copy(
                update={"keys": (public_record(Ed25519PrivateKey.generate()),)}
            ),
            tmp_path / "received",
            transport,
        )
    assert len(transport.paths) == 1
    assert "manifests/" in transport.paths[0]
    assert not (tmp_path / "received").exists()


def test_owned_command_deadline_stops_silent_native_child(tmp_path):
    import time

    script = tmp_path / "silent.py"
    script.write_text("import time\ntime.sleep(60)\n")
    started = time.monotonic()
    with pytest.raises(PreservationError, match="deadline"):
        _command(
            sys.executable,
            tmp_path,
            (str(script),),
            maximum=10,
            control=OperationControl(),
            deadline=started + 0.3,
        )
    assert time.monotonic() - started < 5


def test_published_peer_schema_matches_installed_model():
    from importlib.resources import files

    resource = files("foxlight_model_preservation").joinpath(
        "resources/peer_source_v1.json"
    )
    assert json.loads(resource.read_bytes()) == PeerSource.model_json_schema()


SECOND_CID = "bafkreiaw4p5cuelyea6zzd33mpjjx4aykemkpn6mkofqprdvgxbh54rjwu"


class ModelTransport(Transport):
    """Serve exact store paths under each CID; selected CIDs corrupt objects."""

    def __init__(self, store, *, corrupt=frozenset()):
        super().__init__(b"")
        self.store = store
        self.corrupt = corrupt

    def cat(self, path, *, maximum, sink=None):
        self.paths.append(path)
        self.control.checkpoint()
        _, _, cid, relative = path.split("/", 3)
        data = (self.store / relative).read_bytes()
        if cid in self.corrupt and relative.startswith("objects/"):
            data = bytes([data[0] ^ 1]) + data[1:]
        if len(data) > maximum:
            raise PreservationError("too large")
        self.exported_bytes += len(data)
        if sink is None:
            return data
        sink.write(data)
        return b""


def model_source(tmp_path, capture):
    producer, cache, source, _record, receiver = setup(tmp_path, capture)
    producer.mirrors = ("ipfs://" + CID, "ipfs://" + SECOND_CID)
    producer.publish(2)
    refresh_source(cache, source)
    refresh_peer_source(receiver, source, Transport(export_source(cache, source)))
    return receiver, source


def test_corrupt_first_peer_root_fails_over_to_the_next(tmp_path, capture):
    from foxlight_model_preservation.peer import fetch_peer_model
    from foxlight_model_preservation.storage import verify_store

    receiver, source = model_source(tmp_path, capture)
    transport = ModelTransport(capture.store, corrupt=frozenset({CID}))
    manifest = fetch_peer_model(
        receiver,
        source,
        "small_text",
        capture.identity,
        capture.trust,
        tmp_path / "received",
        transport,
    )
    assert manifest.name == "fixture"
    assert any(SECOND_CID in path for path in transport.paths)
    verify_store(tmp_path / "received", capture.identity, capture.trust)


def test_cancelled_peer_receive_stops_instead_of_failing_over(tmp_path, capture):
    from foxlight_model_preservation.peer import fetch_peer_model

    receiver, source = model_source(tmp_path, capture)
    transport = ModelTransport(capture.store, corrupt=frozenset({CID}))
    transport.control.cancel()
    with pytest.raises(OperationCancelled):
        fetch_peer_model(
            receiver,
            source,
            "small_text",
            capture.identity,
            capture.trust,
            tmp_path / "received",
            transport,
        )
    assert not any(SECOND_CID in path for path in transport.paths)
    assert not (tmp_path / "received").exists()


@pytest.mark.parametrize("allow_private", [False, True])
@pytest.mark.parametrize("mode", ["receive", "mirror"])
def test_owned_runtime_never_relays_or_announces_private_addresses(
    tmp_path, mode, allow_private
):
    from foxlight_model_preservation.peer_runtime import (
        PRIVATE_FILTERS,
        initialize_peer,
    )

    fake = tmp_path / "ipfs"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, os\n"
        "config = {'Identity': {'PeerID': 'fixture'},\n"
        "          'Addresses': {'NoAnnounce': ['/ip4/10.0.0.0/ipcidr/8']},\n"
        "          'Swarm': {'RelayService': {'Enabled': True}}}\n"
        "with open(os.path.join(os.environ['IPFS_PATH'], 'config'), 'w') as f:\n"
        "    json.dump(config, f)\n"
    )
    fake.chmod(0o700)
    repo = tmp_path / "repo"
    initialize_peer(repo, str(fake), mode=mode, allow_private_peers=allow_private)
    config = json.loads((repo / "config").read_bytes())
    assert config["Swarm"]["RelayService"] == {"Enabled": False}
    assert config["AutoNAT"] == {"ServiceMode": "disabled"}
    expected = [] if allow_private else list(PRIVATE_FILTERS)
    assert config["Addresses"]["NoAnnounce"] == expected
    assert config["Identity"] == {"PeerID": "fixture"}


def test_peer_receive_stages_on_the_store_volume_and_resumes(
    tmp_path, capture, monkeypatch
):
    import tempfile

    from foxlight_model_preservation import peer
    from foxlight_model_preservation.peer import fetch_peer_model
    from foxlight_model_preservation.storage import verify_store

    receiver, source = model_source(tmp_path, capture)
    system_temp = tmp_path / "system-temp"
    system_temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(system_temp))
    store = tmp_path / "received"

    class Interrupted(ModelTransport):
        """Abort like a killed process after the first object reaches the store."""

        def cat(self, path, *, maximum, sink=None):
            if sink is not None and any(
                item.stat().st_size for item in (store / ".partial").glob("*")
            ):
                raise KeyboardInterrupt
            temp_bytes = sum(
                item.stat().st_size for item in system_temp.rglob("*") if item.is_file()
            )
            # Weights never pass through system temporary storage.
            assert temp_bytes < 64 * 1024
            return super().cat(path, maximum=maximum, sink=sink)

    def receive(transport):
        return fetch_peer_model(
            receiver,
            source,
            "small_text",
            capture.identity,
            capture.trust,
            store,
            transport,
        )

    with pytest.raises(KeyboardInterrupt):
        receive(Interrupted(capture.store))
    staged = [
        item.name for item in (store / ".partial").iterdir() if item.stat().st_size
    ]
    assert len(staged) == 1
    transport = ModelTransport(capture.store)
    assert receive(transport).name == "fixture"
    # The verified partial from the interrupted attempt was not received again.
    assert not any(path.endswith(staged[0]) for path in transport.paths)
    verify_store(store, capture.identity, capture.trust)

    other = tmp_path / "full"
    full = SimpleNamespace(total=1, used=1, free=0)
    monkeypatch.setattr(peer.shutil, "disk_usage", lambda _path: full)
    transport = ModelTransport(capture.store)
    with pytest.raises(PreservationError, match="no peer supplied"):
        fetch_peer_model(
            receiver,
            source,
            "small_text",
            capture.identity,
            capture.trust,
            other,
            transport,
        )
    assert not any("/objects/" in path for path in transport.paths)


def test_peer_publish_stages_beside_the_store_and_clears_orphans(
    tmp_path, capture, monkeypatch
):
    import tempfile

    from foxlight_model_preservation.peer import publish_peer_model

    system_temp = tmp_path / "system-temp"
    system_temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(system_temp))
    orphan = capture.store / ".peer-export" / "stage-orphan"
    (orphan / "objects").mkdir(parents=True)
    (orphan / "objects" / "leftover").write_bytes(b"x" * 1024)
    added = []

    class Mirror(Transport):
        def __init__(self):
            super().__init__(b"")
            self.profile = PeerProfile(mode="mirror", configuration_sha256="a" * 64)

        def command(self, *args, maximum=65536):
            stage = Path(args[-1])
            assert stage.parent == capture.store / ".peer-export"
            assert not orphan.exists()
            assert not list(system_temp.iterdir())
            added.append(sorted(str(p.relative_to(stage)) for p in stage.rglob("*")))
            return (CID + "\n").encode()

    result = publish_peer_model(
        capture.store, capture.identity, capture.trust, Mirror(), public_model=True
    )
    assert result["cid"] == CID
    assert any(name.startswith("manifests/") for name in added[0])
    assert list((capture.store / ".peer-export").iterdir()) == [
        capture.store / ".peer-export" / ".write.lock"
    ]


def test_peer_source_rejects_duplicate_json_keys(tmp_path, capture):
    *_, record, _receiver = setup(tmp_path, capture)
    data = encode_record(record)
    duplicated = data[:-1] + b',"ipns":"' + NAME.encode() + b'"}'
    assert inspect_peer_source(data) == record
    with pytest.raises(PreservationError, match="invalid bounded JSON"):
        inspect_peer_source(duplicated)


def test_peer_follow_and_historical_refresh_keep_receiver_grants(tmp_path, capture):
    from test_discovery import follow as follow_plain

    _producer, cache, source, record, _receiver = setup(tmp_path, capture)
    receiver = tmp_path / "granted"
    follow_plain(receiver, record.source, allow_local=True, allow_http=True)
    follow_peer_source(receiver, encode_record(record), peer_source_identity(record))
    status = refresh_peer_source(
        receiver, source, Transport(export_source(cache, source)), historical=True
    )
    assert status.source_id == source
    # The operator's local grant still works for an ordinary refresh.
    assert refresh_source(receiver, source).freshness == "fresh"
