"""Local operator selections outlive their origin without acquiring source authority."""

from __future__ import annotations

import base64
import http.client
import json
import queue
import shutil
import subprocess
import sys
import threading
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from foxlight_model_preservation.mirror import (
    model_action,
    public_selection,
    reconcile,
    record_model,
    recover_completed,
    run_mirror,
)
from foxlight_model_preservation.mirror_intake import retain_local
from foxlight_model_preservation.mirror_models import (
    HeldModel,
    MirrorState,
    SourceGrant,
    Terms,
)
from foxlight_model_preservation.mirror_native import configure
from foxlight_model_preservation.mirror_prune import prune
from foxlight_model_preservation.mirror_state import (
    MirrorLedger,
    encode_state,
    grant_restart,
    policy_id,
    restart_allowed,
)
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationCancelled, OperationControl
from foxlight_model_preservation.signing import encode_record, public_record
from foxlight_model_preservation.storage import (
    manifest_path,
    object_path,
    pack,
    restore,
    verify_store,
)
from foxlight_model_preservation.transfer import TransferPolicy, fetch


def local_mirror(tmp_path):
    return MirrorLedger.initialize(
        tmp_path / "mirror",
        name="Selected-copy fixture",
        disk_bytes=2 * 1024**3,
        ingress_daily=1024**3,
        egress_daily=1024**3,
        egress_monthly=1024**3,
        port=0,
    )


def local_grant(capture):
    return SourceGrant(
        trust=capture.trust,
        terms=(Terms(license_id="test-only", terms_reference="LICENSE"),),
    )


def test_offline_original_wrapper_companions_and_later_mirror_recovery(
    tmp_path, capture, monkeypatch
):
    ledger = local_mirror(tmp_path)
    second_key = Ed25519PrivateKey.generate()
    capture.trust = capture.trust.model_copy(
        update={
            "keys": (*capture.trust.keys, public_record(second_key)),
            "threshold": 2,
        }
    )
    members = list(capture.recipe.members)
    members[0] = members[0].model_copy(
        update={"retained_card": {"future-property": {"nested": [1, True, "opaque"]}}}
    )
    recipe = capture.recipe.model_copy(update={"members": tuple(members)})
    identity = pack(recipe, capture.sources, capture.store, (capture.key, second_key))
    path = capture.store / manifest_path(identity)
    original = (
        b" \n" + json.dumps(json.loads(path.read_bytes()), indent=2).encode() + b"\n"
    )
    path.write_bytes(original)

    def forbidden(*args, **kwargs):
        raise AssertionError("offline intake contacted the network")

    with monkeypatch.context() as offline:
        offline.setattr("socket.create_connection", forbidden)
        offline.setattr("socket.getaddrinfo", forbidden)
        manifest = retain_local(ledger, capture.store, identity, local_grant(capture))
    held = ledger.read().models[identity]
    assert held.origin == "local" and held.source_id is None and held.mirrors == ()
    assert not ledger.read().sources and not ledger.running()
    assert ledger.read().ingress_used == 0
    assert manifest.members[0].retained_card == members[0].retained_card
    target = ledger.path / "store"
    assert (target / manifest_path(identity)).read_bytes() == original
    for source in capture.sources.values():
        shutil.rmtree(source)
    shutil.rmtree(capture.store)

    ready = queue.Queue()
    failures = []

    def run():
        try:
            run_mirror(ledger, public=True, seconds=30, ready=ready.put)
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        port = ready.get(timeout=10)
        end = time.monotonic() + 10
        while True:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            connection.request("GET", "/" + manifest_path(identity))
            response = connection.getresponse()
            body = response.read()
            connection.close()
            if response.status == 200:
                break
            assert time.monotonic() < end
            time.sleep(0.05)
        assert body == original
        received = tmp_path / "recipient"
        assert (
            fetch(
                identity,
                capture.trust,
                (f"http://127.0.0.1:{port}",),
                received,
                TransferPolicy(allow_http=True, allow_private=True),
            )
            == manifest
        )
        assert (received / manifest_path(identity)).read_bytes() == original
        recovered = tmp_path / "recovered"
        restore(received, identity, capture.trust, recovered)
        assert (recovered / "members/tokenizer/model.bin").read_bytes() == (
            b"tokenizer\0" * 1000
        )
        assert (recovered / ".preservation/envelope.json").read_bytes() == original
    finally:
        ledger.update(lambda state: state.model_copy(update={"paused": True}))
        thread.join(15)
    assert not thread.is_alive() and not failures and not ledger.running()
    assert verify_store(target, identity, capture.trust) == manifest


def test_local_identity_and_policy_bind_restart_and_survive_native_configuration(
    tmp_path, capture
):
    ledger = local_mirror(tmp_path)
    legacy = encode_state(ledger.read())
    before = policy_id(ledger.read())
    grant_restart(ledger, int(time.time()) + 600)
    retain_local(ledger, capture.store, capture.identity, local_grant(capture))
    state = ledger.read()
    assert state.schema_version == "foxlight.mirror-state.v3"
    assert state.restart is None and policy_id(state) != before
    grant_restart(ledger, int(time.time()) + 600)
    granted = policy_id(ledger.read())
    for action in ("pin", "block", "unpin", "unblock"):
        model_action(ledger, capture.identity, action)
        assert policy_id(ledger.read()) == granted and restart_allowed(ledger.read())
    retain_local(ledger, capture.store, capture.identity, local_grant(capture))
    assert restart_allowed(ledger.read())
    changed = local_grant(capture).model_copy(update={"max_bytes": 1000000})
    with pytest.raises(PreservationError, match="original authority"):
        retain_local(ledger, capture.store, capture.identity, changed)
    configure(ledger)
    assert ledger.read().schema_version == "foxlight.mirror-state.v3"
    assert ledger.read().restart is None
    assert encode_state(MirrorState.model_validate_json(legacy)) == legacy
    invalid = ledger.read().model_dump(mode="json")
    invalid["schema_version"] = "foxlight.mirror-state.v2"
    with pytest.raises(ValueError, match="requires state v3"):
        MirrorState.model_validate_json(json.dumps(invalid))


@pytest.mark.parametrize(
    "cause", ["trust", "identity", "terms", "private", "network", "limit", "disk"]
)
def test_rejected_authority_or_limits_leave_no_selection(
    tmp_path, capture, monkeypatch, cause
):
    ledger = local_mirror(tmp_path)
    grant = local_grant(capture)
    identity = capture.identity
    if cause == "trust":
        grant = grant.model_copy(
            update={
                "trust": capture.trust.model_copy(
                    update={"keys": (public_record(Ed25519PrivateKey.generate()),)}
                )
            }
        )
    elif cause == "identity":
        identity = "f" * 64
    elif cause == "terms":
        grant = grant.model_copy(
            update={"terms": (Terms(license_id="other", terms_reference="LICENSE"),)}
        )
    elif cause == "private":
        member = capture.recipe.members[1]
        recipe = capture.recipe.model_copy(
            update={
                "members": (
                    capture.recipe.members[0],
                    member.model_copy(
                        update={
                            "intake": member.intake.model_copy(
                                update={"audience": "private"}
                            )
                        }
                    ),
                )
            }
        )
        identity = pack(recipe, capture.sources, capture.store, (capture.key,))
    elif cause == "network":
        grant = grant.model_copy(update={"allow_http": True})
    elif cause == "limit":
        grant = grant.model_copy(update={"max_bytes": 1})
    elif cause == "disk":
        monkeypatch.setattr(ledger, "usage", lambda: ledger.read().disk_bytes)
    before = encode_state(ledger.read())
    with pytest.raises((ValueError, FileNotFoundError)):
        retain_local(ledger, capture.store, identity, grant)
    assert encode_state(ledger.read()) == before
    assert not ledger.running()
    assert not (ledger.path / "store/manifests").exists()


def test_partial_copy_needs_operator_retry_and_never_fetches(
    tmp_path, capture, monkeypatch
):
    ledger = local_mirror(tmp_path)
    manifest = verify_store(capture.store, capture.identity, capture.trust)
    file = manifest.members[0].files[0]
    source = capture.store / object_path(file.sha256)
    original = source.read_bytes()
    source.write_bytes(b"x" * len(original))
    with pytest.raises(PreservationError):
        retain_local(ledger, capture.store, capture.identity, local_grant(capture))
    assert ledger.read().models[capture.identity].error
    assert not public_selection(ledger, OperationControl())[0]
    source.write_bytes(original)
    with pytest.raises(PreservationError, match="explicit mirror retry"):
        retain_local(ledger, capture.store, capture.identity, local_grant(capture))
    model_action(ledger, capture.identity, "retry")

    def forbidden(*args, **kwargs):
        raise AssertionError("local selection acquired a network repair path")

    monkeypatch.setattr("foxlight_model_preservation.mirror.fetch", forbidden)
    monkeypatch.setattr(
        "foxlight_model_preservation.mirror.inspect_manifest", forbidden
    )
    reconcile(ledger, OperationControl())
    assert ledger.read().models[capture.identity].state == "pending"
    retain_local(ledger, capture.store, capture.identity, local_grant(capture))
    assert ledger.read().models[capture.identity].state == "retained"


def test_complete_crash_intent_recovers_offline_and_prune_does_not_readmit(
    tmp_path, capture, monkeypatch
):
    import foxlight_model_preservation.mirror_intake as intake

    ledger = local_mirror(tmp_path)
    original = intake.record_model

    def crash(*args, **kwargs):
        raise KeyboardInterrupt("simulated process loss before receipt")

    with monkeypatch.context() as interruption:
        interruption.setattr(intake, "record_model", crash)
        with pytest.raises(KeyboardInterrupt):
            retain_local(ledger, capture.store, capture.identity, local_grant(capture))
    held = ledger.read().models[capture.identity]
    assert held.state == "pending" and held.error is None
    assert intake.record_model is original
    recover_completed(ledger, OperationControl())
    assert ledger.read().models[capture.identity].state == "retained"
    model_action(ledger, capture.identity, "pin")
    model_action(ledger, capture.identity, "block")
    with pytest.raises(PreservationError, match="unpinned blocked"):
        prune(ledger, capture.identity, capture.identity)
    model_action(ledger, capture.identity, "unpin")
    prune(ledger, capture.identity, capture.identity)
    reconcile(ledger, OperationControl())
    assert ledger.read().models[capture.identity].state == "removed"
    with pytest.raises(PreservationError, match="explicit mirror retry"):
        retain_local(ledger, capture.store, capture.identity, local_grant(capture))


def test_signed_wrapper_drift_is_not_silently_reinterpreted(tmp_path, capture):
    ledger = local_mirror(tmp_path)
    retain_local(ledger, capture.store, capture.identity, local_grant(capture))
    source = capture.store / manifest_path(capture.identity)
    source.write_bytes(source.read_bytes() + b"\n")
    with pytest.raises(PreservationError, match="original authority"):
        retain_local(ledger, capture.store, capture.identity, local_grant(capture))
    target = ledger.path / "store" / manifest_path(capture.identity)
    target.write_bytes(target.read_bytes() + b"\n")
    assert not public_selection(ledger, OperationControl())[0]
    assert (
        "original signed wrapper changed"
        in ledger.read().models[capture.identity].error
    )


def test_active_controller_and_interrupted_deletion_fence_local_intake(
    tmp_path, capture
):
    ledger = local_mirror(tmp_path)
    with (
        ledger.service_lock(),
        pytest.raises(PreservationError, match="stop the active"),
    ):
        retain_local(ledger, capture.store, capture.identity, local_grant(capture))
    (ledger.path / "prune.json").write_bytes(b"existing deletion intent")
    with pytest.raises(PreservationError, match="interrupted operator prune"):
        retain_local(ledger, capture.store, capture.identity, local_grant(capture))
    assert not ledger.read().models


def test_cancel_before_admission_leaves_no_intent(tmp_path, capture):
    ledger = local_mirror(tmp_path)
    control = OperationControl()
    control.cancel()
    with pytest.raises(OperationCancelled):
        retain_local(
            ledger,
            capture.store,
            capture.identity,
            local_grant(capture),
            control=control,
        )
    assert not ledger.read().models


def test_local_origin_cannot_carry_synthetic_source_or_mirrors(tmp_path, capture):
    ledger = local_mirror(tmp_path)
    retain_local(ledger, capture.store, capture.identity, local_grant(capture))
    held = ledger.read().models[capture.identity]
    for changes in (
        {"source_id": "a" * 64},
        {"mirrors": ("https://example.invalid",)},
        {"envelope_sha256": None},
    ):
        with pytest.raises(ValueError):
            HeldModel.model_validate(held.model_copy(update=changes).model_dump())
    before = encode_state(ledger.read())
    with pytest.raises(PreservationError, match="authority cannot be changed"):
        record_model(ledger, capture.identity, {"grant": local_grant(capture)})
    assert encode_state(ledger.read()) == before


def test_public_cli_retains_exact_copy_without_source_or_network(tmp_path, capture):
    ledger = local_mirror(tmp_path)
    trust = tmp_path / "trust.json"
    terms = tmp_path / "terms.json"
    trust.write_bytes(encode_record(capture.trust))
    terms.write_text('[{"license_id":"test-only","terms_reference":"LICENSE"}]')
    command = [
        sys.executable,
        "-m",
        "foxlight_model_preservation.cli",
        "mirror",
        "retain",
        "--dir",
        str(ledger.path),
        "--store",
        str(capture.store),
        "--manifest",
        capture.identity,
        "--bundle-trust",
        str(trust),
        "--terms-file",
        str(terms),
    ]
    denied = subprocess.run(command, capture_output=True, timeout=10)  # noqa: S603 - local CLI
    assert denied.returncode != 0 and not ledger.read().models
    completed = subprocess.run(  # noqa: S603 - local CLI
        [*command, "--confirm-redistribution"], capture_output=True, timeout=10
    )
    assert completed.returncode == 0, completed.stderr.decode()
    receipt = json.loads(completed.stdout)
    assert receipt["retained"] and not receipt["network_started"]
    assert not receipt["grants_imported"] and not ledger.read().sources
    assert verify_store(ledger.path / "store", capture.identity, capture.trust)


def test_pmf_transport_domain_refuses_before_local_selection(tmp_path):
    from pathlib import Path

    from foxlight_model_preservation.models import TrustProfile

    vector = json.loads(
        (
            Path(__file__).parent.parent / "examples/conformance/pmf-v0.1.json"
        ).read_bytes()
    )
    store = tmp_path / "pmf"
    envelope_path = store / manifest_path(vector["manifest_id"])
    envelope_path.parent.mkdir(parents=True)
    envelope_path.write_bytes(base64.b64decode(vector["envelope_base64"]))
    ledger = local_mirror(tmp_path)
    before = encode_state(ledger.read())
    with pytest.raises(PreservationError):
        retain_local(
            ledger,
            store,
            vector["manifest_id"],
            SourceGrant(
                trust=TrustProfile.model_validate_json(json.dumps(vector["trust"])),
                terms=(Terms(license_id="test-only", terms_reference="LICENSE"),),
            ),
        )
    assert encode_state(ledger.read()) == before and not ledger.running()
