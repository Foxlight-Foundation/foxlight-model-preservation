"""Offline handoffs and receive-only recovery using real signed bytes and HTTP."""

from __future__ import annotations

import json
import shutil
import socket
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from conftest import Capture
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from foxlight_model_preservation.cli import main
from foxlight_model_preservation.invitations import (
    Invitation,
    accept_invitation_keys,
    authenticate_invitation,
    create_invitation,
    fetch_invitation,
    read_invitation,
)
from foxlight_model_preservation.models import (
    MAX_METADATA_BYTES,
    PreservationError,
    Recipe,
    TrustProfile,
)
from foxlight_model_preservation.seed import SeedServer
from foxlight_model_preservation.signing import encode_record, public_record
from foxlight_model_preservation.storage import pack, restore, verify_store
from foxlight_model_preservation.transfer import TransferPolicy


@contextmanager
def mirror(capture: Capture) -> Iterator[str]:
    server = SeedServer(
        ("127.0.0.1", 0),
        capture.store,
        (capture.identity,),
        capture.trust,
        public=True,
        max_upload_bytes=10 * 1024**2,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def invitation(
    capture: Capture, source: str = "https://not-contacted.invalid/models/"
) -> Invitation:
    return create_invitation(capture.store, capture.identity, capture.trust, (source,))


def test_inspection_needs_no_weights_dns_or_registry(
    capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = invitation(capture)
    shutil.rmtree(capture.store / "objects")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("metadata inspection contacted a network")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    reread = create_invitation(
        capture.store, capture.identity, capture.trust, selected.mirrors
    )
    assert read_invitation(encode_record(reread)) == selected
    assert selected.manifest.total_bytes > 0
    assert authenticate_invitation(selected, capture.trust) == selected.manifest
    assert selected.envelope.payload == reread.envelope.payload
    assert selected.manifest.members[0].retained_card == {
        "verification_class": "legacy"
    }


def test_candidate_keys_are_not_automatically_accepted(
    capture: Capture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = invitation(capture)
    stranger = TrustProfile(
        schema_version="foxlight.model-preservation.trust.v1",
        keys=(public_record(Ed25519PrivateKey.generate()),),
        threshold=1,
        as_of="independent receiver policy",
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("untrusted invitation contacted a source")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    with pytest.raises(PreservationError):
        fetch_invitation(selected, stranger, tmp_path / "received")
    assert not (tmp_path / "received").exists()
    key = capture.trust.keys[0].key_id
    accepted = accept_invitation_keys(selected, (key,))
    assert authenticate_invitation(selected, accepted) == selected.manifest
    assert accepted == accept_invitation_keys(selected, (key,))
    with pytest.raises(PreservationError):
        accept_invitation_keys(selected, (stranger.keys[0].key_id,))
    with pytest.raises(PreservationError):
        accept_invitation_keys(selected, (key, key))
    with pytest.raises(PreservationError):
        accept_invitation_keys(selected, ())


@pytest.mark.parametrize(
    "source",
    [
        "/private/local-store",
        "file:///private/store",
        "ftp://example.org/store",
        "https://user:secret@example.org/store",
        "https://example.org/store?token=secret",
        "https://example.org/store#secret",
        "https://example.org:0/store",
        "https://example.org:99999/store",
        "https://example.org/a\nb",
        " https://example.org/store",
        "https://example.org/\\store",
    ],
)
def test_peer_cannot_supply_paths_credentials_or_unsafe_sources(
    capture: Capture, source: str
) -> None:
    with pytest.raises(PreservationError):
        invitation(capture, source)


def test_programmatic_bypass_still_cannot_fetch_local_paths(
    capture: Capture, tmp_path: Path
) -> None:
    selected = invitation(capture).model_copy(update={"mirrors": (str(capture.store),)})
    with pytest.raises(PreservationError):
        fetch_invitation(selected, capture.trust, tmp_path / "received")
    assert not (tmp_path / "received").exists()


@pytest.mark.parametrize(
    "mutation", ["digest", "domain", "version", "unknown", "duplicate"]
)
def test_changed_metadata_fails_offline(capture: Capture, mutation: str) -> None:
    value = json.loads(encode_record(invitation(capture)))
    if mutation == "digest":
        value["manifest_id"] = "0" * 64
    elif mutation == "domain":
        value["envelope"]["payloadType"] = "different-domain"
    elif mutation == "version":
        value["schema_version"] = "foxlight.model-preservation.invitation.v2"
    elif mutation == "unknown":
        value["private_key"] = "secret-must-not-be-echoed"
    encoded = json.dumps(value).encode()
    if mutation == "duplicate":
        encoded = b'{"manifest_id":"' + capture.identity.encode() + b'",' + encoded[1:]
    with pytest.raises(PreservationError) as refused:
        read_invitation(encoded)
    assert "secret-must-not-be-echoed" not in str(refused.value)


def test_private_bundle_and_oversized_wrapper_refuse(
    capture: Capture, tmp_path: Path
) -> None:
    body = capture.recipe.model_dump(mode="json")
    for member in body["members"]:
        member["intake"]["audience"] = "private"
    private = Recipe.model_validate_json(json.dumps(body))
    root = tmp_path / "private"
    identity = pack(private, capture.sources, root, (capture.key,))
    with pytest.raises(PreservationError):
        create_invitation(root, identity, capture.trust, ("https://example.org/",))
    with pytest.raises(PreservationError):
        read_invitation(b" " * (MAX_METADATA_BYTES + 1))


def test_verified_receive_resume_and_offline_restore(
    capture: Capture, tmp_path: Path
) -> None:
    expected = (capture.sources["weights"] / "model.bin").read_bytes()
    destination = tmp_path / "received"
    with mirror(capture) as source:
        selected = invitation(capture, source)
        accepted = accept_invitation_keys(selected, (capture.trust.keys[0].key_id,))
        with pytest.raises(PreservationError):
            fetch_invitation(selected, accepted, destination)
        assert not destination.exists()
        policy = TransferPolicy(max_bytes=1024**2, allow_private=True, allow_http=True)
        received = fetch_invitation(
            selected, accepted, destination, policy, operation_id="receiver"
        )
        assert received == selected.manifest
        assert (
            fetch_invitation(
                selected, accepted, destination, policy, operation_id="receiver"
            )
            == received
        )
    shutil.rmtree(capture.store)
    for path in capture.sources.values():
        shutil.rmtree(path)
    assert verify_store(destination, selected.manifest_id, accepted) == received
    restored = tmp_path / "restored"
    restore(destination, selected.manifest_id, accepted, restored)
    assert (restored / "members/weights/model.bin").read_bytes() == expected
    assert (restored / "members/tokenizer/model.bin").exists()


def test_cli_handoff_inspection_and_explicit_acceptance(
    capture: Capture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    trust_file = tmp_path / "trust.json"
    trust_file.write_bytes(encode_record(capture.trust))
    handoff = tmp_path / "shared.foxinvite.json"

    def call(*arguments: str) -> dict[str, object]:
        monkeypatch.setattr(sys, "argv", ["model-preserve", *arguments])
        assert main() == 0
        return json.loads(capsys.readouterr().out)

    with mirror(capture) as source:
        exported = call(
            "invite",
            capture.identity,
            "--store",
            str(capture.store),
            "--trust",
            str(trust_file),
            "--mirror",
            source,
            "--output",
            str(handoff),
        )
        assert (
            exported["serving"] is False and exported["verification"] == "metadata_only"
        )
        inspected = call("inspect-invitation", str(handoff))
        assert (
            inspected["publisher_authenticated"] is False
            and inspected["network_started"] is False
        )
        result = call(
            "fetch-invitation",
            str(handoff),
            "--accept-key",
            capture.trust.keys[0].key_id,
            "--store",
            str(tmp_path / "received"),
            "--allow-http",
            "--allow-private",
            "--operation-id",
            "cli-recipient",
            "--save-trust",
            str(tmp_path / "accepted-trust.json"),
        )
        assert result["manifest"] == capture.identity and result["admitted"] is False
        assert result["serving"] is False
    from foxlight_model_preservation.signing import load_trust

    retained = load_trust((tmp_path / "accepted-trust.json").read_bytes())
    assert verify_store(tmp_path / "received", capture.identity, retained)
    kit = tmp_path / "recipient.foxkit"
    call(
        "kit-export",
        capture.identity,
        "--store",
        str(tmp_path / "received"),
        "--trust",
        str(tmp_path / "accepted-trust.json"),
        "--output",
        str(kit),
    )
    assert kit.is_file()


def test_future_card_fields_remain_in_original_signed_payload(
    capture: Capture, tmp_path: Path
) -> None:
    body = capture.recipe.model_dump(mode="json")
    future = {
        "schema_version": "vendor.future-card.v999",
        "descriptive": {"new_sources": ["community:one", "community:two"]},
        "future_property": {"nested": [1, "kept", True]},
    }
    body["members"][0]["retained_card"] = future
    recipe = Recipe.model_validate_json(json.dumps(body))
    store = tmp_path / "future-card"
    identity = pack(recipe, capture.sources, store, (capture.key,))
    selected = create_invitation(
        store, identity, capture.trust, ("https://example.org/",)
    )
    received = read_invitation(encode_record(selected))
    assert received.envelope.payload == selected.envelope.payload
    assert (
        authenticate_invitation(received, capture.trust).members[0].retained_card
        == future
    )


def test_first_contact_respects_threshold_and_independent_revocation(
    capture: Capture, tmp_path: Path
) -> None:
    second, third = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    keys = (capture.key, second, third)
    trust = TrustProfile(
        schema_version="foxlight.model-preservation.trust.v1",
        keys=tuple(public_record(key) for key in keys),
        threshold=2,
        as_of="independently checked threshold",
    )
    store = tmp_path / "threshold-publication"
    identity = pack(capture.recipe, capture.sources, store, keys[:2])
    selected = create_invitation(
        store, identity, trust, ("https://not-contacted.invalid/models",)
    )
    first_id, second_id, _ = tuple(key.key_id for key in trust.keys)
    with pytest.raises(PreservationError):
        accept_invitation_keys(selected, (first_id,))
    accepted = accept_invitation_keys(selected, (first_id, second_id))
    assert accepted.threshold == 2
    assert authenticate_invitation(selected, accepted) == selected.manifest
    revoked = trust.model_copy(update={"revoked_keys": (first_id,)})
    with pytest.raises(PreservationError):
        authenticate_invitation(selected, revoked)


def test_cli_never_replaces_a_different_retained_trust_choice(
    capture: Capture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    handoff = tmp_path / "selected.invitation.json"
    handoff.write_bytes(encode_record(invitation(capture)))
    output = tmp_path / "previous-trust.json"
    output.write_bytes(encode_record(capture.trust))
    original = output.read_bytes()

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("conflicting saved policy attempted DNS")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "model-preserve",
            "fetch-invitation",
            str(handoff),
            "--accept-key",
            capture.trust.keys[0].key_id,
            "--save-trust",
            str(output),
            "--store",
            str(tmp_path / "received"),
        ],
    )
    assert main() == 1
    assert "saved trust differs" in capsys.readouterr().err
    assert output.read_bytes() == original
    assert not (tmp_path / "received").exists()
