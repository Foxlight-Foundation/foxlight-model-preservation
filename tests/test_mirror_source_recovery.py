"""Source-loss recovery needs original authority durable before any object read."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_discovery import Publisher
from test_discovery import mirror as http_mirror
from test_mirror import cycle, mirror
from test_transfer import running_seed

from foxlight_model_preservation.discovery import bootstrap_identity, list_sources
from foxlight_model_preservation.kits import import_kit
from foxlight_model_preservation.mirror import (
    configure_http,
    follow,
    model_action,
    public_selection,
    recover_completed,
    run_mirror,
)
from foxlight_model_preservation.mirror_intake import retain_local
from foxlight_model_preservation.mirror_models import (
    MirrorState,
    RestartGrant,
    SourceGrant,
    Terms,
)
from foxlight_model_preservation.mirror_native import configure
from foxlight_model_preservation.mirror_prune import prune
from foxlight_model_preservation.mirror_recovery import (
    export_selection_kit,
    inspect_selection,
    recover_selection,
)
from foxlight_model_preservation.mirror_state import (
    MirrorLedger,
    encode_state,
    grant_restart,
    policy_id,
    restart_allowed,
)
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationControl
from foxlight_model_preservation.signing import digest, encode_record, public_record
from foxlight_model_preservation.storage import (
    manifest_path,
    object_path,
    pack,
    restore,
)


def crash(ledger, manifest, stage):
    script = """
import os,sys
from pathlib import Path
from foxlight_model_preservation import mirror,transfer
from foxlight_model_preservation.mirror_state import MirrorLedger
from foxlight_model_preservation.operations import OperationControl
ledger=MirrorLedger(Path(sys.argv[1]))
identity,stage=sys.argv[2:]
original=transfer.atomic_bytes
def publication(root,path,data):
    if path == 'manifests/'+identity+'.json':
        if stage == 'published': original(root,path,data)
        os._exit(73)
    return original(root,path,data)
transfer.atomic_bytes=publication
update=ledger.update
def pin(action):
    result=update(action)
    held=result.models.get(identity)
    if stage == 'pinned' and held is not None and held.envelope_sha256 is not None:
        os._exit(73)
    return result
ledger.update=pin
with ledger.service_lock(): mirror.reconcile(ledger,OperationControl())
raise RuntimeError('fixture missed crash point')
"""
    result = subprocess.run(  # noqa: S603 - isolated process, fixed crash fixture
        [sys.executable, "-c", script, str(ledger.path), manifest, stage],
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 73, result.stderr.decode()
    return MirrorLedger(ledger.path)


@pytest.mark.parametrize("stage", ["unpublished", "published"])
def test_real_http_intake_crash_and_total_source_loss_preserve_original_evidence(
    tmp_path, capture, monkeypatch, stage
):
    second = Ed25519PrivateKey.generate()
    capture.trust = capture.trust.model_copy(
        update={"keys": (*capture.trust.keys, public_record(second)), "threshold": 2}
    )
    opaque = {"future": {"schema": 91, "values": [None, True, "unchanged"]}}
    members = list(capture.recipe.members)
    members[0] = members[0].model_copy(update={"retained_card": opaque})
    members[1] = members[1].model_copy(update={"role": "future-companion-" + "x" * 74})
    capture.identity = pack(
        capture.recipe.model_copy(update={"members": tuple(members)}),
        capture.sources,
        capture.store,
        (capture.key, second),
    )
    wrapper = capture.store / manifest_path(capture.identity)
    original = b" \n" + json.dumps(json.loads(wrapper.read_bytes()), indent=2).encode()
    wrapper.write_bytes(original)
    publisher = Publisher(tmp_path / "publisher", capture)
    ledger = MirrorLedger.initialize(
        tmp_path / "mirror",
        name="Source-loss crash fixture",
        disk_bytes=2 * 1024**3,
        ingress_daily=1024**3,
        egress_daily=1024**3,
        egress_monthly=1024**3,
        port=0,
    )
    with (
        running_seed(capture) as (_seed, weights),
        http_mirror(publisher.path) as (
            metadata,
            requests,
        ),
    ):
        publisher.mirrors = (weights,)
        publisher.publish(version=2)
        bootstrap = publisher.bootstrap(metadata)
        selected_grant = SourceGrant(
            trust=capture.trust,
            terms=(Terms(license_id="test-only", terms_reference="LICENSE"),),
            allow_http=True,
            allow_private=True,
            max_bytes=10**6,
        )
        follow(
            ledger,
            encode_record(bootstrap),
            bootstrap_identity(bootstrap),
            selected_grant,
        )
        ledger = crash(ledger, capture.identity, stage)
        assert requests
    held = ledger.read().models[capture.identity]
    assert held.state == "pending" and held.origin == "source"
    assert held.grant == selected_grant and held.envelope_sha256 == digest(original)
    assert held.catalog_id == list_sources(ledger.path / "discovery")[0].catalog_id
    assert held.source_id in ledger.read().sources and held.entry_id == "small_text"
    assert (ledger.path / "store" / manifest_path(capture.identity)).exists() == (
        stage == "published"
    )
    assert (
        ledger.path / "scratch/source-intake" / (capture.identity + ".json")
    ).read_bytes() == original
    shutil.rmtree(publisher.path)
    shutil.rmtree(capture.store)
    for path in (ledger.path / "discovery").iterdir():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    for source in capture.sources.values():
        shutil.rmtree(source)
    capture.key = None
    del second
    model_action(ledger, capture.identity, "pin")
    model_action(ledger, capture.identity, "block")
    grant_restart(ledger, int(time.time()) + 600)
    before = ledger.read()

    def forbidden(*args, **kwargs):
        raise AssertionError("offline recovery attempted networking or signing")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(Ed25519PrivateKey, "generate", forbidden)
    assert (
        inspect_selection(ledger, capture.identity).members[0].retained_card == opaque
    )
    manifest = recover_selection(ledger, capture.identity)
    after = ledger.read()
    assert after.restart is None and after.models[capture.identity].state == "retained"
    assert (
        after.models[capture.identity].pinned and after.models[capture.identity].blocked
    )
    assert after.ingress_used == before.ingress_used
    assert after.reserved_operations == before.reserved_operations
    assert after.models[capture.identity].grant == held.grant
    assert after.models[capture.identity].transfer_bytes == held.transfer_bytes
    kit = tmp_path / "recovered.foxkit"
    export_selection_kit(ledger, capture.identity, kit)
    imported, output = tmp_path / "imported", tmp_path / "materialized"
    import_kit(
        kit, imported, expected_manifest_id=capture.identity, trust=capture.trust
    )
    restore(imported, capture.identity, capture.trust, output)
    assert (output / ".preservation/envelope.json").read_bytes() == original
    assert len(manifest.members) == 2 and manifest.members[0].retained_card == opaque
    assert len(list(output.glob("members/*/model.bin"))) == 2
    assert len(list(output.glob("members/*/LICENSE"))) == 2


@pytest.mark.parametrize("stage", ["pinned", "unpublished"])
@pytest.mark.parametrize(
    "damage",
    [
        "none",
        "wrapper",
        "authority",
        "source",
        "catalog",
        "entry",
        "trust",
        "terms",
        "removed",
    ],
)
def test_partial_or_changed_source_never_recovers_or_discards_data(
    tmp_path, capture, stage, damage
):
    ledger, publisher = mirror(tmp_path, capture)
    ledger = crash(ledger, capture.identity, stage)
    held = ledger.read().models[capture.identity]
    private = ledger.path / "scratch/source-intake" / (capture.identity + ".json")
    if damage == "wrapper":
        private.write_bytes(private.read_bytes() + b" ")
    elif damage == "authority":
        private.with_suffix(".authority.json").write_bytes(b"{}")
    elif damage in {"source", "catalog", "entry", "trust", "terms", "removed"}:
        changes = {
            "source": {"source_id": "a" * 64},
            "catalog": {"catalog_id": "b" * 64},
            "entry": {"entry_id": "changed"},
            "removed": {"state": "removed"},
            "trust": {
                "grant": held.grant.model_copy(
                    update={
                        "trust": held.grant.trust.model_copy(
                            update={
                                "keys": (public_record(Ed25519PrivateKey.generate()),)
                            }
                        )
                    }
                )
            },
            "terms": {
                "grant": held.grant.model_copy(
                    update={
                        "terms": (Terms(license_id="other", terms_reference="LICENSE"),)
                    }
                )
            },
        }[damage]
        ledger.update(
            lambda state: state.model_copy(
                update={"models": {capture.identity: held.model_copy(update=changes)}}
            )
        )
    shutil.rmtree(publisher.path)
    shutil.rmtree(capture.store)
    if stage == "unpublished" and damage == "none":
        recover_selection(ledger, capture.identity)
        return
    before = {
        str(p.relative_to(ledger.path)): p.read_bytes()
        for p in ledger.path.rglob("*")
        if p.is_file() and p.name != ".write.lock"
    }
    with pytest.raises((ValueError, OSError)):
        recover_selection(ledger, capture.identity)
    after = {
        str(p.relative_to(ledger.path)): p.read_bytes()
        for p in ledger.path.rglob("*")
        if p.is_file() and p.name != ".write.lock"
    }
    assert after == before


def test_prune_protects_pending_source_shared_objects_and_requires_operator(
    tmp_path, capture
):
    ledger, publisher = mirror(tmp_path, capture)
    ledger = crash(ledger, capture.identity, "unpublished")
    other = pack(
        capture.recipe.model_copy(update={"name": "Other closure, same objects"}),
        capture.sources,
        capture.store,
        (capture.key,),
    )
    retain_local(
        ledger,
        capture.store,
        other,
        SourceGrant(
            trust=capture.trust,
            terms=(Terms(license_id="test-only", terms_reference="LICENSE"),),
        ),
    )
    shutil.rmtree(publisher.path)
    shutil.rmtree(capture.store)
    model_action(ledger, other, "block")
    assert prune(ledger, other, other)["unshared_objects_selected"] == 0
    private = ledger.path / "scratch/source-intake" / (capture.identity + ".json")
    assert private.exists()
    manifest = inspect_selection(ledger, capture.identity)
    assert all(
        (ledger.path / "store" / object_path(f.sha256)).exists()
        for member in manifest.members
        for f in member.files
    )
    model_action(ledger, capture.identity, "block")
    model_action(ledger, capture.identity, "pin")
    with pytest.raises(PreservationError, match="unpinned"):
        prune(ledger, capture.identity, capture.identity)
    model_action(ledger, capture.identity, "unpin")
    prune(ledger, capture.identity, capture.identity)
    assert not private.exists() and not private.with_suffix(".authority.json").exists()
    assert not list((ledger.path / "store/objects/sha256").iterdir())
    with pytest.raises(PreservationError, match="removed"):
        recover_selection(ledger, capture.identity)


def test_profile_v5_preserves_legacy_vectors_and_transport_settings(tmp_path, capture):
    ledger, _ = mirror(tmp_path, capture)
    grant_restart(ledger, int(time.time()) + 600)
    original_policy = policy_id(ledger.read())
    cycle(ledger)
    assert restart_allowed(ledger.read())
    assert policy_id(ledger.read()) == original_policy
    state = ledger.read()
    assert state.schema_version == "foxlight.mirror-state.v5"
    assert json.loads(encode_state(state))["http_enabled"]
    for old in range(1, 5):
        raw = state.model_dump(mode="json")
        raw["schema_version"] = f"foxlight.mirror-state.v{old}"
        with pytest.raises(ValueError, match="requires state v5"):
            MirrorState.model_validate_json(json.dumps(raw))
    configure_http(ledger, enabled=False)
    configure(ledger)
    assert ledger.read().schema_version == "foxlight.mirror-state.v5"
    assert not ledger.read().http_enabled and ledger.read().restart is None


def test_legacy_source_restart_needs_new_explicit_policy_grant(
    tmp_path, capture, monkeypatch
):
    ledger, _ = mirror(tmp_path, capture)
    legacy = ledger.read().model_copy(
        update={"schema_version": "foxlight.mirror-state.v1"}
    )
    legacy = legacy.model_copy(
        update={
            "restart": RestartGrant(
                policy_id=policy_id(legacy), expires=int(time.time()) + 600
            )
        }
    )
    ledger.update(lambda _: legacy)
    before = encode_state(ledger.read())

    def forbidden(*args, **kwargs):
        raise AssertionError("legacy restart started a listener")

    monkeypatch.setattr("foxlight_model_preservation.mirror.MirrorServer", forbidden)
    with pytest.raises(PreservationError, match="renewed restart grant"):
        run_mirror(ledger, resume=True)
    assert encode_state(ledger.read()) == before
    grant_restart(ledger, int(time.time()) + 600)
    assert ledger.read().schema_version == "foxlight.mirror-state.v5"
    assert restart_allowed(ledger.read())
    assert policy_id(ledger.read()) != policy_id(legacy)


def test_retry_uses_original_wrapper_and_original_reserved_allowance(
    tmp_path, capture, monkeypatch
):
    ledger, _ = mirror(tmp_path, capture)
    ledger = crash(ledger, capture.identity, "pinned")
    held = ledger.read().models[capture.identity]
    used = ledger.read().ingress_used
    original = (capture.store / manifest_path(capture.identity)).read_bytes()
    (capture.store / manifest_path(capture.identity)).write_bytes(original + b" ")

    def forbidden(*args, **kwargs):
        raise AssertionError("retry downloaded replacement provenance")

    monkeypatch.setattr(
        "foxlight_model_preservation.mirror.inspect_envelope", forbidden
    )
    cycle(ledger)
    after = ledger.read().models[capture.identity]
    assert after.state == "retained" and after.error is None
    assert after.operation_id == held.operation_id
    assert after.transfer_bytes == held.transfer_bytes
    assert ledger.read().ingress_used == used + 32 * 1024**2
    assert (
        ledger.path / "store" / manifest_path(capture.identity)
    ).read_bytes() == original


@pytest.mark.parametrize("filename", [".json", ".authority.json"])
def test_failed_metadata_commit_prevents_all_object_reads(
    tmp_path, capture, monkeypatch, filename
):
    from foxlight_model_preservation import mirror as owner

    ledger, _ = mirror(tmp_path, capture)
    original = owner.atomic_bytes

    def interrupted(root, path, data):
        if path == "source-intake/" + capture.identity + filename:
            raise OSError("injected metadata commit failure")
        return original(root, path, data)

    def forbidden(*args, **kwargs):
        raise AssertionError("objects read before metadata commit")

    monkeypatch.setattr(owner, "atomic_bytes", interrupted)
    monkeypatch.setattr("foxlight_model_preservation.transfer._download", forbidden)
    cycle(ledger)
    held = ledger.read().models[capture.identity]
    assert held.state == "pending" and held.error and held.envelope_sha256 is None
    assert not (ledger.path / "store/objects").exists()
    assert not (ledger.path / "store" / manifest_path(capture.identity)).exists()


@pytest.mark.parametrize("exhausted", [False, True])
def test_removed_source_needs_operator_retry_and_cannot_change_wrapper(
    tmp_path, capture, exhausted
):
    ledger, _ = mirror(tmp_path, capture)
    if not exhausted:
        ledger.update(
            lambda state: state.model_copy(
                update={
                    "sources": {
                        key: grant.model_copy(update={"max_bytes": 16 * 1024**2})
                        for key, grant in state.sources.items()
                    }
                }
            )
        )
    cycle(ledger)
    original = ledger.read().models[capture.identity]
    model_action(ledger, capture.identity, "block")
    prune(ledger, capture.identity, capture.identity)
    cycle(ledger)
    assert ledger.read().models[capture.identity].state == "removed"
    model_action(ledger, capture.identity, "retry")
    model_action(ledger, capture.identity, "unblock")
    path = capture.store / manifest_path(capture.identity)
    wrapper = path.read_bytes()
    path.write_bytes(wrapper + b" ")
    cycle(ledger)
    assert ledger.read().models[capture.identity].error
    assert not (ledger.path / "store/objects/sha256").exists() or not list(
        (ledger.path / "store/objects/sha256").iterdir()
    )
    path.write_bytes(wrapper)
    model_action(ledger, capture.identity, "retry")
    cycle(ledger)
    held = ledger.read().models[capture.identity]
    if exhausted:
        assert held.state == "pending" and "budget_exceeded" in held.error
    else:
        assert held.state == "retained" and held.error is None
    assert held.envelope_sha256 == original.envelope_sha256
    assert held.operation_id == original.operation_id
    assert held.grant == original.grant and held.catalog_id == original.catalog_id


@pytest.mark.parametrize("state", ["pending", "retained"])
def test_controller_never_promotes_or_serves_changed_source_attribution(
    tmp_path, capture, monkeypatch, state
):
    ledger, _ = mirror(tmp_path, capture)
    ledger = crash(ledger, capture.identity, "published")
    held = ledger.read().models[capture.identity]
    ledger.update(
        lambda current: current.model_copy(
            update={
                "models": {
                    capture.identity: held.model_copy(
                        update={"entry_id": "changed-attribution", "state": state}
                    )
                }
            }
        )
    )
    original = (ledger.path / "store" / manifest_path(capture.identity)).read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError("changed attribution reached byte verification")

    monkeypatch.setattr("foxlight_model_preservation.mirror.verify_object", forbidden)
    recover_completed(ledger, OperationControl())
    assert ledger.read().models[capture.identity].state == state
    allowed, _ = public_selection(ledger, OperationControl())
    assert not allowed
    assert (
        ledger.path / "store" / manifest_path(capture.identity)
    ).read_bytes() == original
