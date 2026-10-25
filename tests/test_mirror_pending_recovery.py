"""Explicit offline recovery of complete write-ahead selections after a crash."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from foxlight_model_preservation.kits import import_kit
from foxlight_model_preservation.mirror import model_action
from foxlight_model_preservation.mirror_intake import retain_local
from foxlight_model_preservation.mirror_models import SourceGrant, Terms
from foxlight_model_preservation.mirror_recovery import (
    export_selection_kit,
    inspect_selection,
    recover_selection,
)
from foxlight_model_preservation.mirror_state import MirrorLedger, grant_restart
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationCancelled, OperationControl
from foxlight_model_preservation.signing import public_record
from foxlight_model_preservation.storage import (
    manifest_path,
    object_path,
    pack,
    restore,
)


def initialize(tmp_path):
    return MirrorLedger.initialize(
        tmp_path / "mirror",
        name="Offline recovery fixture",
        disk_bytes=2 * 1024**3,
        ingress_daily=1024**3,
        egress_daily=1024**3,
        egress_monthly=1024**3,
        port=0,
    )


def grant(capture):
    return SourceGrant(
        trust=capture.trust,
        terms=(Terms(license_id="test-only", terms_reference="LICENSE"),),
    )


def make_pending(tmp_path, capture):
    ledger = initialize(tmp_path)
    retain_local(ledger, capture.store, capture.identity, grant(capture))
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
    return ledger


@pytest.mark.parametrize("published", [False, True])
def test_real_process_crash_then_offline_complete_recovery(
    tmp_path, capture, monkeypatch, published
):
    second = Ed25519PrivateKey.generate()
    capture.trust = capture.trust.model_copy(
        update={"keys": (*capture.trust.keys, public_record(second)), "threshold": 2}
    )
    members = list(capture.recipe.members)
    opaque = {"future": {"fields": [None, True, "preserved"], "version": 91}}
    members[0] = members[0].model_copy(update={"retained_card": opaque})
    recipe = capture.recipe.model_copy(update={"members": tuple(members)})
    capture.identity = pack(
        recipe, capture.sources, capture.store, (capture.key, second)
    )
    wrapper = capture.store / manifest_path(capture.identity)
    original = b" \n" + json.dumps(json.loads(wrapper.read_bytes()), indent=2).encode()
    wrapper.write_bytes(original)
    ledger = initialize(tmp_path)
    selected_grant = grant(capture)
    grant_file = tmp_path / "grant.json"
    grant_file.write_text(selected_grant.model_dump_json())
    script = """
import os,sys
from pathlib import Path
from foxlight_model_preservation import mirror_intake
from foxlight_model_preservation.mirror_models import SourceGrant
from foxlight_model_preservation.mirror_state import MirrorLedger
ledger=MirrorLedger(Path(sys.argv[1]))
manifest=sys.argv[3]
original=mirror_intake.atomic_bytes
def crash(root,path,data):
    if path == 'manifests/'+manifest+'.json':
        if sys.argv[5] == 'published': original(root,path,data)
        os._exit(73)
    return original(root,path,data)
mirror_intake.atomic_bytes=crash
mirror_intake.retain_local(ledger,Path(sys.argv[2]),manifest,
    SourceGrant.model_validate_json(Path(sys.argv[4]).read_bytes()))
"""
    crashed = subprocess.run(  # noqa: S603 - fixed isolated crash fixture
        [
            sys.executable,
            "-c",
            script,
            str(ledger.path),
            str(capture.store),
            capture.identity,
            str(grant_file),
            "published" if published else "unpublished",
        ],
        capture_output=True,
        timeout=15,
    )
    assert crashed.returncode == 73, crashed.stderr.decode()
    assert ledger.read().models[capture.identity].state == "pending"
    assert (
        ledger.path / "store" / manifest_path(capture.identity)
    ).exists() == published
    shutil.rmtree(capture.store)
    for source in capture.sources.values():
        shutil.rmtree(source)
    capture.key = None
    del second
    capture.sources.clear()

    def forbidden(*args, **kwargs):
        raise AssertionError("offline recovery attempted network or signing")

    with monkeypatch.context() as offline:
        offline.setattr(socket, "socket", forbidden)
        offline.setattr(Ed25519PrivateKey, "generate", forbidden)
        result = recover_selection(ledger, capture.identity)
        assert result.members[0].retained_card == opaque
        assert len(result.members) == 2
        selected = ledger.read().models[capture.identity]
        assert selected.state == "retained" and selected.grant == selected_grant
        recovered_wrapper = ledger.path / "store" / manifest_path(capture.identity)
        assert recovered_wrapper.read_bytes() == original
        kit = tmp_path / "recovered.foxkit"
        export_selection_kit(ledger, capture.identity, kit)
        destination = tmp_path / "imported"
        import_kit(
            kit, destination, expected_manifest_id=capture.identity, trust=capture.trust
        )
        output = tmp_path / "materialized"
        restore(destination, capture.identity, capture.trust, output)
        assert (output / ".preservation/envelope.json").read_bytes() == original
        assert len(list(output.glob("members/*/model.bin"))) == 2
        assert len(list(output.glob("members/*/LICENSE"))) == 2


def test_policy_block_pin_and_accounting_survive_but_restart_is_cleared(
    tmp_path, capture
):
    ledger = make_pending(tmp_path, capture)
    model_action(ledger, capture.identity, "pin")
    model_action(ledger, capture.identity, "block")
    ledger.update(
        lambda state: state.model_copy(
            update={
                "paused": True,
                "ingress_used": 123,
                "egress_day_used": 456,
                "models": {
                    capture.identity: state.models[capture.identity].model_copy(
                        update={"error": "storage_error"}
                    )
                },
            }
        )
    )
    grant_restart(ledger, int(time.time()) + 3600)
    before = ledger.read()
    recover_selection(ledger, capture.identity)
    after = ledger.read()
    assert after.restart is None and before.restart is not None
    assert (
        after.models[capture.identity].pinned and after.models[capture.identity].blocked
    )
    assert after.models[capture.identity].error is None
    expected = before.model_dump()
    expected["restart"] = None
    expected["models"][capture.identity].update(
        state="retained",
        error=None,
        verified_at=after.models[capture.identity].verified_at,
    )
    assert after.model_dump() == expected
    retained_state = (ledger.path / "mirror.json").read_bytes()
    recover_selection(ledger, capture.identity)
    assert (ledger.path / "mirror.json").read_bytes() == retained_state


@pytest.mark.parametrize(
    "damage",
    ["missing", "corrupt", "wrapper", "intent", "trust", "terms", "removed", "prune"],
)
def test_refusal_preserves_pending_bytes_and_policy(tmp_path, capture, damage):
    ledger = make_pending(tmp_path, capture)
    manifest = inspect_selection(ledger, capture.identity)
    if damage in {"missing", "corrupt"}:
        path = ledger.path / "store" / object_path(manifest.members[-1].files[0].sha256)
        if damage == "missing":
            path.unlink()
        else:
            path.write_bytes(b"bad")
    elif damage in {"wrapper", "intent"}:
        path = ledger.path / "store" / manifest_path(capture.identity)
        if damage == "wrapper":
            path.write_bytes(path.read_bytes() + b" ")
        else:
            path.unlink()
            (
                ledger.path / "scratch/local-intake" / (capture.identity + ".json")
            ).write_bytes(b"{}")
    elif damage in {"trust", "terms", "removed"}:
        selected = ledger.read().models[capture.identity]
        changes = (
            {"state": "removed"}
            if damage == "removed"
            else {
                "grant": selected.grant.model_copy(
                    update={
                        "trust": capture.trust.model_copy(
                            update={
                                "keys": (public_record(Ed25519PrivateKey.generate()),)
                            }
                        )
                    }
                    if damage == "trust"
                    else {
                        "terms": (Terms(license_id="other", terms_reference="LICENSE"),)
                    }
                )
            }
        )
        ledger.update(
            lambda state: state.model_copy(
                update={
                    "models": {capture.identity: selected.model_copy(update=changes)}
                }
            )
        )
    else:
        (ledger.path / "prune.json").write_bytes(b"unfinished operator deletion")
    before = {
        str(p.relative_to(ledger.path)): p.read_bytes()
        for p in ledger.path.rglob("*")
        if p.is_file()
    }
    with pytest.raises((ValueError, OSError)):
        recover_selection(ledger, capture.identity)
    after = {
        str(p.relative_to(ledger.path)): p.read_bytes()
        for p in ledger.path.rglob("*")
        if p.is_file()
    }
    assert after == before


@pytest.mark.parametrize("when", ["starting", "checking", "hashed", "publishing"])
def test_cancel_before_commit_keeps_pending_and_unpublished(tmp_path, capture, when):
    ledger = make_pending(tmp_path, capture)
    (ledger.path / "store" / manifest_path(capture.identity)).unlink()
    before = (ledger.path / "mirror.json").read_bytes()

    def observe(progress):
        if progress.stage == when or (when == "hashed" and progress.bytes_hashed):
            control.cancel()

    control = OperationControl(observe, interval_seconds=0)
    with pytest.raises(OperationCancelled):
        recover_selection(ledger, capture.identity, control=control)
    assert (ledger.path / "mirror.json").read_bytes() == before
    assert not (ledger.path / "store" / manifest_path(capture.identity)).exists()
    assert not control.progress.committed


def test_changed_selection_during_verification_is_never_undone(tmp_path, capture):
    ledger = make_pending(tmp_path, capture)
    changed = False

    def observe(progress):
        nonlocal changed
        if progress.bytes_hashed and not changed:
            changed = True
            model_action(ledger, capture.identity, "block")

    with pytest.raises(PreservationError, match="selection changed"):
        recover_selection(
            ledger,
            capture.identity,
            control=OperationControl(observe, interval_seconds=0),
        )
    selected = ledger.read().models[capture.identity]
    assert selected.blocked and selected.state == "pending"


def test_interrupted_receipt_commit_keeps_complete_wrapper_for_offline_retry(
    tmp_path, capture, monkeypatch
):
    ledger = make_pending(tmp_path, capture)
    (ledger.path / "store" / manifest_path(capture.identity)).unlink()
    original_update = ledger.update

    def interrupted(action):
        raise OSError("fixture crash after envelope publication")

    with monkeypatch.context() as failure:
        failure.setattr(ledger, "update", interrupted)
        with pytest.raises(OSError):
            recover_selection(ledger, capture.identity)
    assert ledger.read().models[capture.identity].state == "pending"
    original = (ledger.path / "store" / manifest_path(capture.identity)).read_bytes()
    assert original == (capture.store / manifest_path(capture.identity)).read_bytes()
    assert ledger.update == original_update
    recover_selection(ledger, capture.identity)
    assert ledger.read().models[capture.identity].state == "retained"


def test_source_pending_requires_its_already_authenticated_published_wrapper(
    tmp_path, capture
):
    ledger = make_pending(tmp_path, capture)
    selected = (
        ledger.read()
        .models[capture.identity]
        .model_copy(
            update={
                "origin": "source",
                "envelope_sha256": None,
                "source_id": "a" * 64,
                "entry_id": "fixture",
                "mirrors": ("https://example.invalid/fixture",),
            }
        )
    )
    ledger.update(
        lambda state: state.model_copy(update={"models": {capture.identity: selected}})
    )
    recover_selection(ledger, capture.identity)
    assert ledger.read().models[capture.identity].state == "retained"
    ledger.update(
        lambda state: state.model_copy(update={"models": {capture.identity: selected}})
    )
    (ledger.path / "store" / manifest_path(capture.identity)).unlink()
    with pytest.raises(FileNotFoundError):
        recover_selection(ledger, capture.identity)
    assert ledger.read().models[capture.identity] == selected


def test_unknown_or_unsafe_identity_refuses_without_selection(tmp_path, capture):
    ledger = make_pending(tmp_path, capture)
    before = (ledger.path / "mirror.json").read_bytes()
    for identity in ("f" * 64, "../mirror.json", "A" * 64):
        with pytest.raises(ValueError):
            recover_selection(ledger, identity)
    assert (ledger.path / "mirror.json").read_bytes() == before


def test_controller_or_another_writer_cannot_race_recovery(tmp_path, capture):
    ledger = make_pending(tmp_path, capture)
    with (
        ledger.service_lock(),
        pytest.raises(PreservationError, match="stop the active"),
    ):
        recover_selection(MirrorLedger(ledger.path), capture.identity)
    with ledger.child("store") as store:
        from foxlight_model_preservation.storage import writer_lock

        with writer_lock(store), pytest.raises(PreservationError):
            recover_selection(ledger, capture.identity)


def test_cli_recovery_reports_progress_and_keeps_no_network_grant(tmp_path, capture):
    ledger = make_pending(tmp_path, capture)
    (ledger.path / "store" / manifest_path(capture.identity)).unlink()
    shutil.rmtree(capture.store)
    result = subprocess.run(  # noqa: S603 - explicit isolated CLI fixture
        [
            sys.executable,
            "-m",
            "foxlight_model_preservation.cli",
            "mirror",
            "recover",
            "--dir",
            str(ledger.path),
            "--manifest",
            capture.identity,
            "--progress",
        ],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr.decode()
    receipt = json.loads(result.stdout)
    assert receipt == {
        "manifest": capture.identity,
        "members": 2,
        "state": "retained",
        "payload_bytes_verified": True,
        "network_started": False,
        "grants_imported": False,
    }
    progress = [json.loads(line) for line in result.stderr.splitlines()]
    assert progress[-1]["committed"] and progress[-1]["stage"] == "complete"
    assert progress[-1]["bytes_hashed"] > 0 and progress[-1]["bytes_received"] == 0
