"""Peer references cannot weaken offline trust or refill a retained allowance."""

import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from foxlight_model_preservation.invitations import read_invitation
from foxlight_model_preservation.ledger import read_transfer_receipt
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationControl
from foxlight_model_preservation.peer_invitations import (
    accept_peer_invitation_keys,
    authenticate_peer_invitation,
    fetch_peer_invitation,
    inspect_peer_model,
    peer_invitation_from_envelope,
    read_peer_invitation,
)
from foxlight_model_preservation.peer_runtime import PeerProfile
from foxlight_model_preservation.signing import encode_record, public_record
from foxlight_model_preservation.storage import manifest_path, verify_store

CID = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3rkshzn2hvw7tmdjg5qzcblxe"


@pytest.fixture(autouse=True)
def peer_extra():
    pytest.importorskip("multiformats")


class Transport:
    def __init__(self, capture, *, bad=False):
        self.capture = capture
        self.bad = bad
        self.paths = []
        self.profile = PeerProfile(mode="receive", configuration_sha256="a" * 64)
        self.control = OperationControl()
        self.exported_bytes = 0

    def cat(self, path, *, maximum, sink=None):
        self.paths.append(path)
        relative = path.split("/", 3)[-1]
        data = (self.capture.store / relative).read_bytes()
        if self.bad and relative.startswith("objects/"):
            data = b"x" * len(data)
        assert len(data) <= maximum
        self.exported_bytes += len(data)
        if sink is None:
            return data
        sink.write(data)
        return b""


def reference(capture):
    return peer_invitation_from_envelope(
        (capture.store / manifest_path(capture.identity)).read_bytes(),
        capture.identity,
        capture.trust,
        ("ipfs://" + CID,),
    )


def test_peer_handoff_is_separate_offline_and_keeps_all_metadata(capture):
    from importlib.resources import files

    from foxlight_model_preservation.peer_invitations import PeerInvitation

    assert (
        json.loads(
            files("foxlight_model_preservation")
            .joinpath("resources/peer_invitation_v1.json")
            .read_bytes()
        )
        == PeerInvitation.model_json_schema()
    )
    invitation = reference(capture)
    assert read_peer_invitation(encode_record(invitation)) == invitation
    assert (
        authenticate_peer_invitation(invitation, capture.trust) == invitation.manifest
    )
    assert (
        encode_record(invitation.envelope)
        == (capture.store / manifest_path(capture.identity)).read_bytes()
    )
    with pytest.raises(PreservationError):
        read_invitation(encode_record(invitation))
    with pytest.raises(PreservationError):
        accept_peer_invitation_keys(invitation, ())
    trust = accept_peer_invitation_keys(
        invitation, tuple(k.key_id for k in capture.trust.keys)
    )
    assert trust.keys == capture.trust.keys
    poisoned = json.loads(encode_record(invitation))
    poisoned["mirrors"] = ["https://gateway.example/ipfs/" + CID]
    with pytest.raises(PreservationError):
        read_peer_invitation(json.dumps(poisoned).encode())


def test_metadata_only_handoff_accepts_mixed_source_hints_without_objects(capture):
    transport = Transport(capture)
    invitation = inspect_peer_model(
        capture.identity,
        capture.trust,
        ("https://offline.invalid", "ipfs://" + CID),
        transport,
    )
    assert invitation.mirrors == ("ipfs://" + CID,)
    assert len(transport.paths) == 1 and "/manifests/" in transport.paths[0]
    with pytest.raises(ValueError):
        inspect_peer_model("../bad", capture.trust, invitation.mirrors, transport)
    assert len(transport.paths) == 1


def test_peer_model_trust_is_checked_before_network(tmp_path, capture):
    invitation = reference(capture)
    wrong = capture.trust.model_copy(
        update={"keys": (public_record(Ed25519PrivateKey.generate()),)}
    )
    transport = Transport(capture)
    with pytest.raises(PreservationError):
        fetch_peer_invitation(invitation, wrong, tmp_path / "receiver", transport)
    assert transport.paths == []


def test_complete_verified_objects_are_reused_offline(tmp_path, capture):
    invitation = reference(capture)
    destination = tmp_path / "receiver"
    transport = Transport(capture)
    assert (
        fetch_peer_invitation(
            invitation,
            capture.trust,
            destination,
            transport,
            maximum_bytes=1000000,
            operation_id="receive",
        )
        == invitation.manifest
    )
    assert (
        verify_store(destination, capture.identity, capture.trust)
        == invitation.manifest
    )
    again = Transport(capture)
    fetch_peer_invitation(
        invitation,
        capture.trust,
        destination,
        again,
        maximum_bytes=1000000,
        operation_id="receive",
    )
    assert again.paths == []
    assert read_transfer_receipt(destination, "receive").charged_bytes > 0


def test_corrupt_peer_never_completes_or_refills_the_budget(tmp_path, capture):
    invitation = reference(capture)
    destination = tmp_path / "receiver"
    with pytest.raises(PreservationError):
        fetch_peer_invitation(
            invitation,
            capture.trust,
            destination,
            Transport(capture, bad=True),
            maximum_bytes=1000000,
            operation_id="receive",
        )
    assert not (destination / manifest_path(capture.identity)).exists()
    receipt = read_transfer_receipt(destination, "receive")
    again = Transport(capture)
    with pytest.raises(PreservationError):
        fetch_peer_invitation(
            invitation,
            capture.trust,
            destination,
            again,
            maximum_bytes=1000000,
            operation_id="receive",
        )
    assert again.paths == []
    assert read_transfer_receipt(destination, "receive") == receipt
    with pytest.raises(PreservationError):
        fetch_peer_invitation(
            invitation,
            capture.trust,
            destination,
            Transport(capture),
            maximum_bytes=2000000,
            operation_id="receive",
        )


def test_offline_peer_invitation_cli_needs_no_native_runtime(tmp_path, capture):
    import subprocess
    import sys

    path = tmp_path / "model.foxpeer-model"
    path.write_bytes(encode_record(reference(capture)))
    result = subprocess.run(  # noqa: S603 - known Python CLI, no shell
        [
            sys.executable,
            "-m",
            "foxlight_model_preservation.cli",
            "peer-invitation-inspect",
            "--invitation",
            str(path),
        ],
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["network_requests"] == 0


def test_nonpersistent_failover_also_consumes_its_allowance(tmp_path, capture):
    import shutil

    from multiformats import CID as ContentID
    from multiformats import multihash

    from foxlight_model_preservation.storage import unique_objects

    invitation = reference(capture)
    roots = tuple(
        "ipfs://"
        + ContentID(
            "base32", 1, "dag-pb", multihash.digest(bytes([i]), "sha2-256")
        ).encode()
        for i in range(16)
    )
    invitation = invitation.model_copy(update={"mirrors": roots})
    files = unique_objects(invitation.manifest)
    largest = max(files, key=lambda item: item.size)
    destination = tmp_path / "receiver"
    destination.mkdir(mode=0o700)
    (destination / "objects/sha256").mkdir(parents=True)
    for file in files:
        if file != largest:
            shutil.copyfile(
                capture.store / "objects/sha256" / file.sha256,
                destination / "objects/sha256" / file.sha256,
            )
    maximum = sum(file.size for file in files)
    transport = Transport(capture, bad=True)
    with pytest.raises(PreservationError):
        fetch_peer_invitation(
            invitation, capture.trust, destination, transport, maximum_bytes=maximum
        )
    assert transport.exported_bytes <= maximum
    assert len(transport.paths) < len(roots)
    assert not (destination / manifest_path(capture.identity)).exists()
