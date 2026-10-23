"""Portable routing cannot enroll trust, start serving or change signed bytes."""

import json
import socket
from importlib.resources import files

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_discovery import Publisher
from test_invitations import invitation, mirror

from foxlight_model_preservation.cli import Arguments, argument_parser, run
from foxlight_model_preservation.handoffs import (
    Handoff,
    handoff_identity,
    read_handoff,
    read_reference,
    reference_contacts,
    write_handoff,
)
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.signing import encode_record, public_record
from foxlight_model_preservation.storage import restore, verify_store

CONTACT = "/ip4/127.0.0.1/tcp/4001/p2p/" + "1" * 40
CID = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3rkshzn2hvw7tmdjg5qzcblxe"


def arguments(*values):
    return argument_parser().parse_args(list(map(str, values)), namespace=Arguments())


def peer_reference(capture):
    pytest.importorskip("multiformats")
    from foxlight_model_preservation.peer_invitations import (
        peer_invitation_from_envelope,
    )
    from foxlight_model_preservation.storage import manifest_path

    return peer_invitation_from_envelope(
        (capture.store / manifest_path(capture.identity)).read_bytes(),
        capture.identity,
        capture.trust,
        ("ipfs://" + CID,),
    )


def test_schema_and_offline_review_keep_original_model_evidence(
    tmp_path, capture, monkeypatch
):
    assert (
        json.loads(
            files("foxlight_model_preservation")
            .joinpath("resources/handoff_v1.json")
            .read_bytes()
        )
        == Handoff.model_json_schema()
    )
    reference = invitation(capture)
    original = encode_record(reference)
    reference_file = tmp_path / "original.foxmodel"
    reference_file.write_bytes(original)

    def forbidden(*args, **kwargs):
        pytest.fail("offline handoff accessed a network")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    output = tmp_path / "model.foxhandoff"
    receipt = run(
        arguments("handoff-create", "--reference", reference_file, "--output", output)
    )
    assert receipt == run(arguments("handoff-inspect", "--handoff", output))
    assert receipt["network_requests"] == 0
    assert receipt["trusted"] is False and receipt["serving"] is False
    assert encode_record(read_reference(output.read_bytes())) == original
    assert reference_contacts(original) == ()
    assert read_handoff(output.read_bytes()).reference.envelope == reference.envelope
    assert output.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("mutation", ["version", "field", "duplicate", "signature"])
def test_malformed_or_modified_handoff_fails_offline(capture, mutation):
    value = json.loads(encode_record(Handoff(reference=invitation(capture))))
    if mutation == "version":
        value["schema_version"] = "foxlight.model-preservation.handoff.v2"
    elif mutation == "field":
        value["allow_network"] = "secret-not-a-grant"
    elif mutation == "signature":
        value["reference"]["envelope"]["signatures"][0]["sig"] = "AAAA"
    data = json.dumps(value).encode()
    if mutation == "duplicate":
        data = b'{"contacts":[],' + data[1:]
    with pytest.raises(PreservationError) as refused:
        read_handoff(data)
    assert "secret-not-a-grant" not in str(refused.value)


def test_contacts_are_bounded_hints_and_do_not_change_signed_identity(capture):
    reference = peer_reference(capture)
    handoff = Handoff(reference=reference, contacts=(CONTACT,))
    changed = Handoff(reference=reference, contacts=(CONTACT.replace("4001", "4002"),))
    assert handoff_identity(changed) != handoff_identity(handoff)
    assert read_reference(encode_record(changed)) == reference
    assert changed.reference.manifest_id == reference.manifest_id
    for contacts in (
        (),
        (CONTACT, CONTACT),
        tuple(CONTACT.replace("4001", str(4001 + i)) for i in range(9)),
        ("/dns4/example.org/tcp/4001/p2p/" + "1" * 40,),
        (CONTACT.replace("4001", "0"),),
        (CONTACT + "/http",),
    ):
        with pytest.raises(ValueError):
            Handoff(reference=reference, contacts=contacts)
    with pytest.raises(ValueError):
        Handoff(reference=invitation(capture), contacts=(CONTACT,))


def test_atomic_output_refuses_existing_and_symlink(tmp_path, capture):
    handoff = Handoff(reference=invitation(capture))
    target = tmp_path / "target"
    target.write_bytes(b"retained")
    with pytest.raises(FileExistsError):
        write_handoff(target, handoff)
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(FileExistsError):
        write_handoff(link, handoff)
    assert target.read_bytes() == b"retained"
    assert sorted(p.name for p in tmp_path.glob(".handoff-*")) == []


def test_wrong_trust_and_private_contacts_refuse_before_native_start(
    tmp_path, capture, monkeypatch
):
    reference = peer_reference(capture)
    handoff = tmp_path / "copy.foxhandoff"
    write_handoff(handoff, Handoff(reference=reference, contacts=(CONTACT,)))
    trust = tmp_path / "trust.json"
    wrong = capture.trust.model_copy(
        update={"keys": (public_record(Ed25519PrivateKey.generate()),)}
    )
    trust.write_bytes(encode_record(wrong))

    def forbidden(*args, **kwargs):
        pytest.fail("unapproved handoff attempted native startup or DNS")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr("foxlight_model_preservation.handoff_cli.executable", forbidden)
    command = arguments(
        "handoff-fetch",
        "--handoff",
        handoff,
        "--store",
        tmp_path / "received",
        "--repo",
        tmp_path / "peer",
        "--bundle-trust",
        trust,
        "--allow-peer-network",
    )
    with pytest.raises(PreservationError):
        run(command)
    trust.write_bytes(encode_record(capture.trust))
    with pytest.raises(PreservationError, match="private"):
        run(command)
    assert not (tmp_path / "peer").exists()
    assert not (tmp_path / "received").exists()


def test_http_one_file_recovers_full_closure_and_never_serves(tmp_path, capture):
    handoff = tmp_path / "copy.foxhandoff"
    receiver = tmp_path / "received"
    with mirror(capture) as endpoint:
        write_handoff(handoff, Handoff(reference=invitation(capture, endpoint)))
        received = run(
            arguments(
                "handoff-fetch",
                "--handoff",
                handoff,
                "--store",
                receiver,
                "--accept-key",
                capture.trust.keys[0].key_id,
                "--allow-http",
                "--allow-private",
                "--operation-id",
                "one-file-copy",
            )
        )
    assert received["serving"] is False
    assert verify_store(receiver, capture.identity, capture.trust) == verify_store(
        capture.store, capture.identity, capture.trust
    )
    restored = tmp_path / "restored"
    restore(receiver, capture.identity, capture.trust, restored)
    for member in ("weights", "tokenizer"):
        assert (restored / "members" / member / "model.bin").read_bytes() == (
            capture.sources[member] / "model.bin"
        ).read_bytes()
        assert not (restored / "members" / member / "secret-not-selected").exists()
    assert (capture.store / "manifests" / (capture.identity + ".json")).exists()
    assert not (receiver / "api").exists()


def test_source_follow_retains_source_pin_and_has_no_model_authority(
    tmp_path, capture, monkeypatch
):
    pytest.importorskip("multiformats")
    # The IPNS codec/name is independent of the bootstrap peer contact.
    from multiformats import CID as MultiformatCID
    from multiformats import multihash

    from foxlight_model_preservation.peer import PeerSource, peer_source_identity

    name = str(
        MultiformatCID("base36", 1, "libp2p-key", multihash.digest(b"x", "sha2-256"))
    )
    publisher = Publisher(tmp_path / "publisher", capture)
    reference = PeerSource(
        source=publisher.bootstrap("https://gone.invalid"), ipns=name
    )
    handoff = tmp_path / "source.foxhandoff"
    write_handoff(handoff, Handoff(reference=reference, contacts=(CONTACT,)))

    def forbidden(*args, **kwargs):
        pytest.fail("following a source must remain offline")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    cache = tmp_path / "cache"
    status = run(
        arguments(
            "handoff-follow",
            "--handoff",
            handoff,
            "--cache",
            cache,
            "--expected-peer-id",
            peer_source_identity(reference),
        )
    )
    assert status["freshness"] == "empty"
    assert status["catalog_id"] is None
    saved = cache / "peer-sources" / (status["source_id"] + ".json")
    assert saved.read_bytes() == encode_record(reference)
    assert "allow_private" not in json.loads(saved.read_bytes())
