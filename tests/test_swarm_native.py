"""Download-only swarm receive over isolated loopback; no public peers."""

import hashlib
import time

import pytest

from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationControl
from foxlight_model_preservation.pirateface_find import parse_torrent
from foxlight_model_preservation.swarm import SwarmReceiver
from foxlight_model_preservation.torrent_binding import torrent_engine

pytest.importorskip("libtorrent", reason="optional native BitTorrent extra")

SETTINGS = {
    "listen_interfaces": "127.0.0.1:0",
    "enable_dht": False,
    "enable_lsd": False,
    "enable_upnp": False,
    "enable_natpmp": False,
    "enable_incoming_utp": False,
    "enable_outgoing_utp": False,
    "alert_mask": 0,
}


def repository(tmp_path):
    root = tmp_path / "seed" / "test__weights"
    root.mkdir(parents=True)
    contents = {
        "config.json": b'{"model": "test"}',
        "model.safetensors": bytes(range(256)) * 400,
        "other.bin": b"\x01" * 70000,
    }
    for name, data in contents.items():
        (root / name).write_bytes(data)
    engine = torrent_engine()
    files = engine.file_storage()
    for name, data in contents.items():
        files.add_file(root.name + "/" + name, len(data))
    creator = engine.create_torrent(files, 16384, engine.create_torrent.v1_only)
    engine.set_piece_hashes(creator, str(root.parent), lambda _piece: None)
    data = engine.bencode(creator.generate())
    infohash = str(engine.torrent_info(engine.bdecode(data)).info_hashes().v1)
    return root, contents, parse_torrent(data, infohash, "test/weights")


def test_selected_files_arrive_verified_and_nothing_else_is_kept(tmp_path):
    root, contents, swarm = repository(tmp_path)
    engine = torrent_engine()
    seeder = engine.session(SETTINGS)
    seed = seeder.add_torrent(
        {
            "ti": engine.torrent_info(engine.bdecode(swarm.data)),
            "save_path": str(root.parent),
            "flags": 0,
        }
    )
    deadline = time.monotonic() + 20
    while not seed.status().is_seeding and time.monotonic() < deadline:
        time.sleep(0.05)
    assert seed.status().is_seeding
    receiver = SwarmReceiver(
        swarm,
        tmp_path / "staging",
        settings={**SETTINGS, "unchoke_slots_limit": 0},
        peers=(("127.0.0.1", seeder.listen_port()),),
        stall_seconds=20,
    )
    try:
        assert receiver.has("model.safetensors", len(contents["model.safetensors"]))
        assert not receiver.has("model.safetensors", 1)
        control = OperationControl()
        target = tmp_path / "work" / "model.safetensors"
        receiver.fetch(
            "model.safetensors",
            hashlib.sha256(contents["model.safetensors"]).hexdigest(),
            target,
            control,
            deadline=time.monotonic() + 30,
        )
        assert target.read_bytes() == contents["model.safetensors"]
        # A wrong expected hash is never accepted.
        with pytest.raises(PreservationError, match="swarm_file_mismatch"):
            receiver.fetch(
                "config.json",
                "0" * 64,
                tmp_path / "work" / "config.json",
                control,
                deadline=time.monotonic() + 30,
            )
        assert not (tmp_path / "work" / "config.json").exists()
    finally:
        receiver.close()
        seeder.pause()
    # The unselected file was never written whole.
    other = tmp_path / "staging" / swarm.name / "other.bin"
    assert not other.exists() or other.read_bytes() != contents["other.bin"]


def test_a_swarm_with_no_peers_stalls_out(tmp_path):
    _root, contents, swarm = repository(tmp_path)
    receiver = SwarmReceiver(
        swarm, tmp_path / "staging", settings=SETTINGS, stall_seconds=1
    )
    try:
        with pytest.raises(PreservationError, match="swarm_unavailable"):
            receiver.fetch(
                "config.json",
                hashlib.sha256(contents["config.json"]).hexdigest(),
                tmp_path / "out",
                OperationControl(),
                deadline=time.monotonic() + 30,
            )
    finally:
        receiver.close()


def test_another_downloader_gets_nothing_from_a_receiver(tmp_path):
    """Receive-only: no unchoke and no allowed-fast pieces for other peers."""
    root, contents, swarm = repository(tmp_path)
    engine = torrent_engine()
    seeder = engine.session(SETTINGS)
    seed = seeder.add_torrent(
        {
            "ti": engine.torrent_info(engine.bdecode(swarm.data)),
            "save_path": str(root.parent),
            "flags": 0,
        }
    )
    deadline = time.monotonic() + 20
    while not seed.status().is_seeding and time.monotonic() < deadline:
        time.sleep(0.05)
    receiver = SwarmReceiver(
        swarm,
        tmp_path / "staging",
        settings=SETTINGS,
        peers=(("127.0.0.1", seeder.listen_port()),),
        stall_seconds=20,
    )
    leech = engine.session(SETTINGS)
    try:
        receiver.fetch(
            "model.safetensors",
            hashlib.sha256(contents["model.safetensors"]).hexdigest(),
            tmp_path / "work" / "model.safetensors",
            OperationControl(),
            deadline=time.monotonic() + 30,
        )
        # The seeder leaves: only the receiver holds pieces now.
        seeder.remove_torrent(seed)
        wanting = leech.add_torrent(
            {
                "ti": engine.torrent_info(engine.bdecode(swarm.data)),
                "save_path": str(tmp_path / "leech"),
                "flags": 0,
            }
        )
        wanting.connect_peer(("127.0.0.1", receiver._session.listen_port()))
        time.sleep(6)
        assert receiver._handle.status().total_payload_upload == 0
        assert wanting.status().total_payload_download == 0
    finally:
        receiver.close()
        leech.pause()
        seeder.pause()
