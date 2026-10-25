"""Retained evidence remains recoverable without its original application host."""

from __future__ import annotations

import http.client
import json
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from foxlight_model_preservation import kits, mirror_recovery, storage
from foxlight_model_preservation.kits import import_kit, inspect_kit
from foxlight_model_preservation.mirror import model_action, run_mirror
from foxlight_model_preservation.mirror_intake import retain_local
from foxlight_model_preservation.mirror_models import SourceGrant, Terms
from foxlight_model_preservation.mirror_prune import prune
from foxlight_model_preservation.mirror_recovery import (
    export_selection_kit,
    inspect_selection,
    selection_status,
)
from foxlight_model_preservation.mirror_state import MirrorLedger
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationCancelled, OperationControl
from foxlight_model_preservation.signing import public_record
from foxlight_model_preservation.storage import (
    manifest_path,
    object_path,
    pack,
    restore,
)


def retained(tmp_path, capture):
    ledger = MirrorLedger.initialize(
        tmp_path / "mirror",
        name="Independent retained fixture",
        disk_bytes=2 * 1024**3,
        ingress_daily=1024**3,
        egress_daily=1024**3,
        egress_monthly=1024**3,
        port=0,
    )
    grant = SourceGrant(
        trust=capture.trust,
        terms=(Terms(license_id="test-only", terms_reference="LICENSE"),),
    )
    retain_local(ledger, capture.store, capture.identity, grant)
    return ledger


def test_original_opaque_cards_threshold_and_complete_offline_recovery(
    tmp_path, capture, monkeypatch
):
    second = Ed25519PrivateKey.generate()
    capture.trust = capture.trust.model_copy(
        update={
            "keys": (*capture.trust.keys, public_record(second)),
            "threshold": 2,
        }
    )
    members = list(capture.recipe.members)
    opaque = {"future": {"vocabulary": "new-value", "proof": [None, True, 12]}}
    members[0] = members[0].model_copy(update={"retained_card": opaque})
    recipe = capture.recipe.model_copy(update={"members": tuple(members)})
    capture.identity = pack(
        recipe, capture.sources, capture.store, (capture.key, second)
    )
    path = capture.store / manifest_path(capture.identity)
    original = b" \n" + json.dumps(json.loads(path.read_bytes()), indent=2).encode()
    path.write_bytes(original)
    ledger = retained(tmp_path, capture)
    before = (ledger.path / "mirror.json").read_bytes()
    shutil.rmtree(capture.store)
    for source in capture.sources.values():
        shutil.rmtree(source)
    capture.key = None
    del second

    def forbidden(*args, **kwargs):
        raise AssertionError("offline recovery attempted network or signing")

    with monkeypatch.context() as offline:
        offline.setattr(socket.socket, "connect", forbidden)
        offline.setattr(Ed25519PrivateKey, "generate", forbidden)
        metadata = inspect_selection(ledger, capture.identity)
        assert metadata.members[0].retained_card == opaque
        assert len(metadata.members) == 2
        kit = tmp_path / "recipient.foxkit"
        result = export_selection_kit(ledger, capture.identity, kit)
        assert result.trust == capture.trust and result.manifest == metadata
        recovered = tmp_path / "recovered"
        with pytest.raises(PreservationError):
            import_kit(kit, recovered, expected_manifest_id=capture.identity)
        assert not recovered.exists()
        import_kit(
            kit, recovered, expected_manifest_id=capture.identity, trust=capture.trust
        )
        assert (recovered / manifest_path(capture.identity)).read_bytes() == original
        destination = tmp_path / "materialized"
        restore(recovered, capture.identity, capture.trust, destination)
        assert (destination / ".preservation/envelope.json").read_bytes() == original
        assert len(list(destination.glob("members/*/model.bin"))) == 2
        assert len(list(destination.glob("members/*/LICENSE"))) == 2
    assert (ledger.path / "mirror.json").read_bytes() == before
    assert not list(destination.rglob("secret-not-selected"))


def test_metadata_only_and_pending_intent_do_not_read_objects(
    tmp_path, capture, monkeypatch
):
    ledger = retained(tmp_path, capture)
    original_open = storage.open_file

    @contextmanager
    def no_objects(root, path, **kwargs):
        assert not path.startswith("objects/"), "inspection scanned payload bytes"
        with original_open(root, path, **kwargs) as stream:
            yield stream

    monkeypatch.setattr(storage, "open_file", no_objects)
    assert inspect_selection(ledger, capture.identity).name == "fixture"
    (ledger.path / "store" / manifest_path(capture.identity)).unlink()
    ledger.update(
        lambda state: state.model_copy(
            update={
                "models": {
                    capture.identity: state.models[capture.identity].model_copy(
                        update={"state": "pending", "verified_at": None}
                    )
                }
            }
        )
    )
    assert inspect_selection(ledger, capture.identity).name == "fixture"
    with pytest.raises(PreservationError, match="retained"):
        export_selection_kit(ledger, capture.identity, tmp_path / "partial.foxkit")
    assert not (tmp_path / "partial.foxkit").exists()


def test_exact_selection_off_page_removed_and_unavailable_are_distinct(
    tmp_path, capture
):
    ledger = retained(tmp_path, capture)
    held = selection_status(ledger, capture.identity)
    assert held is not None
    identities = [f"{i:064x}" for i in range(45)]
    models = {identity: held for identity in identities}
    models[identities[-1]] = held.model_copy(update={"state": "removed"})
    ledger.update(lambda state: state.model_copy(update={"models": models}))
    assert selection_status(ledger, identities[25]).state == "retained"
    assert selection_status(ledger, identities[-1]).state == "removed"
    assert selection_status(ledger, "f" * 64) is None
    with pytest.raises(PreservationError, match="removed"):
        inspect_selection(ledger, identities[-1])
    (ledger.path / "mirror.json").write_text("corrupt owner state")
    with pytest.raises(ValueError):
        selection_status(ledger, "f" * 64)


@pytest.mark.parametrize("identity", ["../mirror.json", "A" * 64, "a" * 63])
def test_bad_identity_refuses_before_read(tmp_path, capture, monkeypatch, identity):
    ledger = retained(tmp_path, capture)

    def forbidden():
        raise AssertionError("unsafe identity reached owner state")

    monkeypatch.setattr(ledger, "read", forbidden)
    with pytest.raises(ValueError):
        selection_status(ledger, identity)


@pytest.mark.parametrize("damage", ["envelope", "terms", "trust", "object"])
def test_export_reauthenticates_authority_and_every_object(tmp_path, capture, damage):
    ledger = retained(tmp_path, capture)
    if damage == "envelope":
        path = ledger.path / "store" / manifest_path(capture.identity)
        path.write_bytes(path.read_bytes() + b" ")
    elif damage in ("terms", "trust"):
        selected = ledger.read().models[capture.identity]
        grant = selected.grant.model_copy(
            update={
                "terms": (Terms(license_id="different", terms_reference="LICENSE"),)
            }
            if damage == "terms"
            else {
                "trust": capture.trust.model_copy(
                    update={"keys": (public_record(Ed25519PrivateKey.generate()),)}
                )
            }
        )
        ledger.update(
            lambda state: state.model_copy(
                update={
                    "models": {
                        capture.identity: selected.model_copy(update={"grant": grant})
                    }
                }
            )
        )
    else:
        manifest = inspect_selection(ledger, capture.identity)
        path = ledger.path / "store" / object_path(manifest.members[0].files[0].sha256)
        path.write_bytes(b"damaged")
    kit = tmp_path / "refused.foxkit"
    with pytest.raises(ValueError):
        export_selection_kit(ledger, capture.identity, kit)
    assert not kit.exists() and not list(tmp_path.glob(".kit-*"))


def test_source_directory_replacement_refuses_before_read(
    tmp_path, capture, monkeypatch
):
    ledger = retained(tmp_path, capture)
    actual_export = kits.export_kit

    def replace(*args, **kwargs):
        store = ledger.path / "store"
        store.rename(ledger.path / "previous-store")
        shutil.copytree(ledger.path / "previous-store", store)
        return actual_export(*args, **kwargs)

    monkeypatch.setattr(mirror_recovery, "export_kit", replace)
    kit = tmp_path / "replaced.foxkit"
    with pytest.raises(PreservationError, match="directory identity"):
        export_selection_kit(ledger, capture.identity, kit)
    assert not kit.exists() and not list(tmp_path.glob(".kit-*"))


def test_cancel_no_replace_and_deliberate_prune_preserve_operator_control(
    tmp_path, capture
):
    ledger = retained(tmp_path, capture)
    control = OperationControl()
    control.cancel()
    kit = tmp_path / "recipient.foxkit"
    with pytest.raises(OperationCancelled):
        export_selection_kit(ledger, capture.identity, kit, control=control)
    assert not kit.exists()
    export_selection_kit(ledger, capture.identity, kit)
    original = kit.read_bytes()
    with pytest.raises(FileExistsError):
        export_selection_kit(ledger, capture.identity, kit)
    assert kit.read_bytes() == original and not list(tmp_path.glob(".kit-*"))
    model_action(ledger, capture.identity, "block")
    prune(ledger, capture.identity, capture.identity)
    assert selection_status(ledger, capture.identity).state == "removed"
    with pytest.raises(PreservationError, match="removed"):
        export_selection_kit(ledger, capture.identity, tmp_path / "removed.foxkit")
    assert inspect_kit(kit).manifest_id == capture.identity


def test_cancel_during_copy_never_publishes_partial_kit(tmp_path, capture):
    ledger = retained(tmp_path, capture)
    seen = []

    def observe(progress):
        seen.append(progress)
        if progress.bytes_copied:
            control.cancel()

    control = OperationControl(observe, interval_seconds=0)
    kit = tmp_path / "cancelled.foxkit"
    with pytest.raises(OperationCancelled):
        export_selection_kit(ledger, capture.identity, kit, control=control)
    assert any(item.bytes_copied for item in seen)
    assert seen[-1].stage == "cancelled" and not seen[-1].committed
    assert not kit.exists() and not list(tmp_path.glob(".kit-*"))
    assert selection_status(ledger, capture.identity).state == "retained"


def test_export_during_actual_http_serving_does_not_stop_controller(tmp_path, capture):
    ledger = retained(tmp_path, capture)
    ready = queue.Queue()
    outcome = queue.Queue()
    control = OperationControl()

    def run():
        try:
            outcome.put(
                run_mirror(
                    ledger, public=True, seconds=30, control=control, prepared=ready.put
                )
            )
        except BaseException as error:
            outcome.put(error)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        port = ready.get(timeout=10)
        assert port is not None
        # Prepared is listener startup, not the asynchronous first verified
        # publication. Establish actual serving before testing a concurrent kit.
        until = time.monotonic() + 5
        while True:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            try:
                connection.request("GET", "/manifests/" + capture.identity + ".json")
                response = connection.getresponse()
                response.read()
                if response.status == 200:
                    break
                assert response.status == 404 and time.monotonic() < until
            finally:
                connection.close()
            time.sleep(0.05)
        export_selection_kit(
            ledger, capture.identity, tmp_path / "while-sharing.foxkit"
        )
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            connection.request("GET", "/manifests/" + capture.identity + ".json")
            response = connection.getresponse()
            assert response.status == 200
            assert (
                response.read()
                == (
                    ledger.path / "store" / manifest_path(capture.identity)
                ).read_bytes()
            )
        finally:
            connection.close()
        assert worker.is_alive()
    finally:
        control.cancel()
        worker.join(timeout=10)
    assert not worker.is_alive()
    result = outcome.get_nowait()
    assert not isinstance(result, BaseException) or isinstance(
        result, OperationCancelled
    ), result
    with pytest.raises(OSError):
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
    assert selection_status(ledger, capture.identity).state == "retained"


def test_standalone_cli_inspects_and_exports_without_original_store(tmp_path, capture):
    ledger = retained(tmp_path, capture)
    shutil.rmtree(capture.store)
    kit = tmp_path / "cli-recipient.foxkit"
    for action, options in (
        ("selection", []),
        ("inspect", []),
        ("export-kit", ["--output", str(kit)]),
    ):
        result = subprocess.run(  # noqa: S603 - isolated local CLI, synthetic capture
            [
                sys.executable,
                "-m",
                "foxlight_model_preservation.cli",
                "mirror",
                action,
                "--dir",
                str(ledger.path),
                "--manifest",
                capture.identity,
                *options,
            ],
            capture_output=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr.decode()
        receipt = json.loads(result.stdout)
        assert (
            receipt["manifest"] == capture.identity and not receipt["network_started"]
        )
        if action == "inspect":
            assert not receipt["payload_bytes_verified"]
            assert len(receipt["metadata"]["members"]) == 2
    assert inspect_kit(kit).manifest_id == capture.identity
