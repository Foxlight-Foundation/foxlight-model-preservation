"""Real stock Kubo mesh: no official service, HTTP mirror, Skulk or private SDK."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager

import pytest
from test_discovery import Publisher, follow

from foxlight_model_preservation.discovery import (
    export_source,
    follow_source,
    list_sources,
    refresh_source,
    search_sources,
)
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.peer import (
    PeerSource,
    fetch_peer_model,
    follow_peer_source,
    peer_source_identity,
    publish_peer_model,
    publish_peer_source,
    refresh_peer_source,
)
from foxlight_model_preservation.peer_runtime import (
    PeerClient,
    executable,
    initialize_peer,
)
from foxlight_model_preservation.signing import encode_record
from foxlight_model_preservation.storage import (
    directory,
    manifest_path,
    read_metadata,
    restore,
    verify_store,
)


@pytest.fixture
def native_binary():
    pytest.importorskip("multiformats")
    selected = os.environ.get("KUBO_BINARY")
    if not selected:
        pytest.skip("set KUBO_BINARY to checksum-verified stock Kubo 0.43.1")
    return executable(selected)


@contextmanager
def node(tmp_path, name, binary, *, mode="receive", bootstrap=()):
    repo = tmp_path / name
    initialize_peer(
        repo, binary, mode=mode, bootstrap=bootstrap, allow_private_peers=True
    )
    arguments = [
        sys.executable,
        "-m",
        "foxlight_model_preservation.cli",
        "peer-run",
        "--repo",
        str(repo),
        "--ipfs",
        binary,
        "--allow-peer-network",
        "--seconds",
        "300",
    ]
    if mode == "mirror":
        arguments.append("--share-public")
    process = subprocess.Popen(  # noqa: S603 - own Python CLI and separate arguments
        arguments,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        end = time.monotonic() + 25
        while time.monotonic() < end:
            if process.poll() is not None:
                pytest.fail(
                    "native peer startup failed: " + repr(process.communicate())
                )
            if (repo / "api").exists():
                try:
                    client = PeerClient(repo, binary, allow_network=True, seconds=90)
                    identity = json.loads(client.command("id"))
                    address = next(
                        a
                        for a in identity["Addresses"]
                        if a.startswith("/ip4/127.0.0.1/")
                    )
                    break
                except (OSError, ValueError):
                    pass
            time.sleep(0.05)
        else:
            pytest.fail("native peer startup deadline")
        yield process, client, address
    finally:
        if process.poll() is None:
            process.send_signal(2)
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()
        end = time.monotonic() + 10
        while (repo / "api").exists() and time.monotonic() < end:
            time.sleep(0.05)
        assert not (repo / "api").exists(), (
            "owned native daemon remained after caller stop"
        )


@contextmanager
def model_sharer(tmp_path, capture, binary, contacts):
    """Start the public CLI, waiting for its atomically published one-file output."""
    trust = tmp_path / "sharing-trust.json"
    trust.write_bytes(encode_record(capture.trust))
    handoff = tmp_path / "model.foxhandoff"
    repo = tmp_path / "sharing-peer"
    arguments = [
        sys.executable,
        "-m",
        "foxlight_model_preservation.cli",
        "peer-share",
        "--store",
        str(capture.store),
        "--manifest",
        capture.identity,
        "--trust",
        str(trust),
        "--repo",
        str(repo),
        "--ipfs",
        binary,
        "--output",
        str(handoff),
        "--allow-peer-network",
        "--share-public",
        "--public-model",
        "--allow-private-peers",
        "--seconds",
        "120",
    ]
    for contact in contacts:
        arguments.extend(("--bootstrap-address", contact))
    process = subprocess.Popen(  # noqa: S603 - own module and separate arguments
        arguments,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        end = time.monotonic() + 40
        while not handoff.exists() and time.monotonic() < end:
            if process.poll() is not None:
                pytest.fail(
                    "one-file sharing startup failed: " + repr(process.communicate())
                )
            time.sleep(0.05)
        assert handoff.exists(), "one-file sharing did not publish a complete reference"
        yield process, handoff, repo
    finally:
        if process.poll() is None:
            process.send_signal(2)
            try:
                process.wait(timeout=25)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        output = process.communicate(timeout=3)
        end = time.monotonic() + 15
        while (repo / "api").exists() and time.monotonic() < end:
            time.sleep(0.05)
        assert not (repo / "api").exists(), "sharing stop left native work running"
        assert process.returncode in (130, -9), output


@pytest.mark.parametrize("publisher_lost", [False, True])
def test_one_file_receive_and_operator_stop_keep_archive(
    tmp_path, capture, native_binary, publisher_lost
):
    from foxlight_model_preservation.handoffs import read_handoff
    from foxlight_model_preservation.peer_sharing import prepare_peer_handoff

    original = verify_store(capture.store, capture.identity, capture.trust)
    with node(tmp_path, "handoff-router", native_binary, mode="mirror") as (
        _,
        _,
        contact,
    ):
        with node(
            tmp_path,
            "handoff-retainer",
            native_binary,
            mode="mirror",
            bootstrap=(contact,),
        ) as (_, retainer, _):
            with model_sharer(tmp_path, capture, native_binary, (contact,)) as (
                process,
                handoff,
                sharing_repo,
            ):
                record = read_handoff(handoff.read_bytes())
                assert record.reference.manifest_id == capture.identity
                assert record.reference.manifest == original
                assert contact in record.contacts
                assert (
                    PeerClient(
                        sharing_repo, native_binary, allow_network=True
                    ).profile.mode
                    == "mirror"
                )
                if publisher_lost:
                    retained = prepare_peer_handoff(
                        capture.store,
                        capture.identity,
                        capture.trust,
                        retainer,
                        public_model=True,
                        bootstrap=(contact,),
                    )
                    assert retained.reference.mirrors == record.reference.mirrors
                    # Only the original handoff remains: no new mirror inventory,
                    # source service, publisher key or Core is supplied to receiver.
                    process.kill()
                    process.wait(timeout=3)
                    end = time.monotonic() + 15
                    while (sharing_repo / "api").exists() and time.monotonic() < end:
                        time.sleep(0.05)
                    assert not (sharing_repo / "api").exists()
                    shutil.rmtree(sharing_repo)
                receiver = tmp_path / "one-file-receiver"
                receiver_repo = tmp_path / "one-file-receiver-peer"
                result = subprocess.run(  # noqa: S603 - own module, fixed arguments
                    [
                        sys.executable,
                        "-m",
                        "foxlight_model_preservation.cli",
                        "handoff-fetch",
                        "--handoff",
                        str(handoff),
                        "--store",
                        str(receiver),
                        "--repo",
                        str(receiver_repo),
                        "--ipfs",
                        native_binary,
                        "--allow-peer-network",
                        "--allow-private-peers",
                        "--seconds",
                        "60",
                        "--accept-key",
                        capture.trust.keys[0].key_id,
                        "--operation-id",
                        "same-file-recovery",
                    ],
                    capture_output=True,
                    timeout=85,
                    check=False,
                )
                assert result.returncode == 0, result.stderr.decode()
                assert json.loads(result.stdout)["serving"] is False
                assert not (receiver_repo / "api").exists()
                assert (
                    verify_store(receiver, capture.identity, capture.trust) == original
                )
                restored = tmp_path / "one-file-restored"
                restore(receiver, capture.identity, capture.trust, restored)
                for member in ("weights", "tokenizer"):
                    assert (
                        restored / "members" / member / "model.bin"
                    ).read_bytes() == (
                        capture.sources[member] / "model.bin"
                    ).read_bytes()
            assert handoff.exists()
            assert (
                verify_store(capture.store, capture.identity, capture.trust) == original
            )


def test_cold_peer_discovery_after_original_publisher_loss(
    tmp_path, capture, native_binary
):
    import importlib.util

    assert importlib.util.find_spec("skulk") is None
    assert importlib.util.find_spec("skulk_capability_sdk") is None
    from foxlight_model_preservation.models import Recipe
    from foxlight_model_preservation.storage import pack

    # Empty auxiliary files are valid archive members too.
    (capture.sources["weights"] / "empty.txt").write_bytes(b"")
    recipe = capture.recipe.model_dump(mode="json")
    recipe["members"][0]["files"].append({"path": "empty.txt"})
    capture.recipe = Recipe.model_validate_json(json.dumps(recipe))
    capture.identity = pack(
        capture.recipe, capture.sources, capture.store, (capture.key,)
    )
    original = verify_store(capture.store, capture.identity, capture.trust)
    with directory(capture.store) as descriptor:
        envelope = read_metadata(descriptor, manifest_path(capture.identity))
    with node(tmp_path, "router", native_binary, mode="mirror") as (_, router, contact):
        with node(
            tmp_path, "retainer", native_binary, mode="mirror", bootstrap=(contact,)
        ) as (mirror_process, mirror, _):
            with node(
                tmp_path, "original", native_binary, mode="mirror", bootstrap=(contact,)
            ) as (_, producer, _):
                first = publish_peer_model(
                    capture.store,
                    capture.identity,
                    capture.trust,
                    producer,
                    public_model=True,
                )
                second = publish_peer_model(
                    capture.store,
                    capture.identity,
                    capture.trust,
                    mirror,
                    public_model=True,
                )
                assert first["cid"] == second["cid"]
                source = Publisher(tmp_path / "http-tree", capture)
                source.mirrors = ("ipfs://" + str(first["cid"]),)
                source.publish(2)
                operator_cache = tmp_path / "operator-cache"
                source_id = follow(
                    operator_cache, source.bootstrap(str(source.path)), allow_local=True
                ).source_id
                refresh_source(operator_cache, source_id)
                record = PeerSource(
                    source=source.bootstrap("https://gone.invalid/source"),
                    ipns=producer.publisher_name(),
                )
                follow_peer_source(
                    operator_cache, encode_record(record), peer_source_identity(record)
                )
                feed = publish_peer_source(
                    operator_cache, source_id, producer, public_metadata=True
                )
                snapshot = export_source(operator_cache, source_id)
                backup_cache = tmp_path / "mirror-cache"
                from foxlight_model_preservation.discovery import bootstrap_identity

                follow_source(backup_cache, snapshot, bootstrap_identity(record.source))
                follow_peer_source(
                    backup_cache, encode_record(record), peer_source_identity(record)
                )
                retained = publish_peer_source(
                    backup_cache,
                    source_id,
                    mirror,
                    public_metadata=True,
                    publish_name=False,
                )
                assert retained["cid"] == feed["cid"]
                record = record.model_copy(update={"snapshots": (feed["cid"],)})
            # Producer blocks/identity, source HTTP tree and original inputs vanish.
            shutil.rmtree(tmp_path / "original")
            shutil.rmtree(source.path)
            shutil.rmtree(capture.store)
            for path in capture.sources.values():
                shutil.rmtree(path)
            with node(tmp_path, "cold", native_binary, bootstrap=(contact,)) as (
                _,
                consumer,
                consumer_address,
            ):
                cache = tmp_path / "empty-cache"
                assert list_sources(cache) == ()
                follow_peer_source(
                    cache, encode_record(record), peer_source_identity(record)
                )
                resolved = consumer.command(
                    "name",
                    "resolve",
                    "--nocache",
                    "--recursive=false",
                    "/ipns/" + record.ipns,
                    maximum=512,
                )
                assert resolved.decode().strip() == "/ipfs/" + feed["cid"]
                assert (
                    refresh_peer_source(cache, source_id, consumer).freshness == "fresh"
                )
                assert search_sources(cache, "offline")[0].manifest == capture.identity
                received = tmp_path / "received"
                # Separate action/control retains the existing cancellation fence.
                receiver = PeerClient(
                    consumer.repo, native_binary, allow_network=True, seconds=90
                )
                restored_manifest = fetch_peer_model(
                    cache,
                    source_id,
                    "small_text",
                    capture.identity,
                    capture.trust,
                    received,
                    receiver,
                    maximum_bytes=1000000,
                )
                assert restored_manifest == original
                from foxlight_model_preservation.peer_invitations import (
                    fetch_peer_invitation,
                    inspect_peer_model,
                )

                metadata = inspect_peer_model(
                    capture.identity,
                    capture.trust,
                    ("ipfs://" + first["cid"],),
                    PeerClient(
                        consumer.repo, native_binary, allow_network=True, seconds=90
                    ),
                )
                referenced = tmp_path / "reference-received"
                assert (
                    fetch_peer_invitation(
                        metadata,
                        capture.trust,
                        referenced,
                        PeerClient(
                            consumer.repo, native_binary, allow_network=True, seconds=90
                        ),
                        maximum_bytes=1000000,
                        operation_id="receive",
                    )
                    == original
                )
                assert (
                    verify_store(referenced, capture.identity, capture.trust)
                    == original
                )
                assert (
                    verify_store(received, capture.identity, capture.trust) == original
                )
                with directory(received) as descriptor:
                    assert (
                        read_metadata(descriptor, manifest_path(capture.identity))
                        == envelope
                    )
                restore(
                    received, capture.identity, capture.trust, tmp_path / "restored"
                )
                assert (tmp_path / "restored/members/tokenizer/model.bin").exists()
                # A receiver has the blocks, but cannot supply them to a new peer.
                mirror_process.send_signal(2)
                mirror_process.wait(timeout=12)
                with node(tmp_path, "probe", native_binary, bootstrap=(contact,)) as (
                    _,
                    probe,
                    _,
                ):
                    probe.command("swarm", "connect", consumer_address)
                    short = PeerClient(
                        probe.repo, native_binary, allow_network=True, seconds=3
                    )
                    with pytest.raises(PreservationError):
                        short.cat(
                            "/ipfs/"
                            + first["cid"]
                            + "/"
                            + manifest_path(capture.identity),
                            maximum=8 * 1024**2,
                        )
                assert search_sources(cache, "offline")
                assert (
                    verify_store(received, capture.identity, capture.trust) == original
                )
                # Routing-only bootstrap never held the model itself.
                absent = PeerClient(
                    router.repo, native_binary, allow_network=True, seconds=3
                )
                with pytest.raises(PreservationError):
                    absent.cat(
                        "/ipfs/" + first["cid"] + "/" + manifest_path(capture.identity),
                        maximum=8 * 1024**2,
                    )


def test_abrupt_caller_loss_stops_actual_owned_native_node(tmp_path, native_binary):
    with node(tmp_path, "lifetime", native_binary) as (process, client, _address):
        port = int(client.address.rsplit("/", 1)[-1])
        process.kill()
        process.wait(timeout=3)
        end = time.monotonic() + 10
        while time.monotonic() < end:
            with socket.socket() as connection:
                if connection.connect_ex(("127.0.0.1", port)) != 0:
                    break
            time.sleep(0.1)
        else:
            pytest.fail("native listener outlived abrupt caller pipe loss")


def test_native_shutdown_is_reported_as_session_failure(tmp_path, native_binary):
    with node(tmp_path, "unexpected-stop", native_binary) as (process, client, _):
        client.command("shutdown")
        assert process.wait(timeout=12) != 0
