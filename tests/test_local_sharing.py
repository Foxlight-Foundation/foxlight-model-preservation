"""Real independent publication, finite sharing and recipient custody boundaries."""

import json
import socket
import time
from urllib.request import ProxyHandler, build_opener

import pytest
from test_local_backups import saved
from test_local_handoffs import review
from test_local_owner import finish, submit
from test_publication import private_capture

from foxlight_model_preservation.handoffs import read_reference
from foxlight_model_preservation.local_host.owner import LocalOwner
from foxlight_model_preservation.mirror_state import MirrorLedger
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.storage import manifest_path, verify_store


def published(owner, tmp_path, capture):
    private = private_capture(capture, tmp_path)
    copy = saved(owner, tmp_path, private)
    review = owner.control("sharing-review", {"copy_id": copy["copy_id"]})
    assert review["already_public"] is False
    plan = owner.control(
        "plan",
        {
            "kind": "publish",
            "copy_id": copy["copy_id"],
            "decisions": [
                {
                    "member_id": item["member_id"],
                    "license_id": "test-only",
                    "terms_reference": "LICENSE",
                }
                for item in review["members"]
            ],
        },
    )
    descriptor = json.loads(owner.publication_review(plan["plan_id"]))["manifest"]
    assert "public_manifest" not in plan
    assert descriptor["runtime_requirements"] == list(
        private.recipe.runtime_requirements
    )
    assert len(descriptor["members"]) == 2
    assert "source-evidence" not in str(descriptor)
    receipt = finish(owner, submit(owner, plan))
    assert receipt["state"] == "succeeded", receipt
    result = owner.control("sharing-read", {})["public_copies"][0]
    return copy, result


def test_publication_offline_omits_private_evidence_and_uses_separate_key(
    tmp_path, capture, monkeypatch
):
    owner = LocalOwner(tmp_path / "owner")
    monkeypatch.setattr(
        socket,
        "socket",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("publication must remain offline")
        ),
    )
    try:
        copy, public = published(owner, tmp_path, capture)
        record = owner.sharing.get(public["public_id"])
        assert record.trust.keys != capture.trust.keys
        store = owner.home / "sharing/mirrors" / public["public_id"] / "store"
        manifest = verify_store(store, public["manifest_id"], record.trust)
        assert {item.member_id for item in manifest.members} == {"weights", "tokenizer"}
        assert all(item.intake.audience == "public" for item in manifest.members)
        assert b"old grants" not in b"".join(
            path.read_bytes() for path in store.rglob("*") if path.is_file()
        )
        assert owner.read()["copies"][0]["manifest_id"] == copy["manifest_id"]
        assert owner.sharing.session_plan(public["public_id"])["seconds"] == 3600
        assert not MirrorLedger(store.parent).running()
    finally:
        owner.close()


def test_real_share_receive_stop_and_concurrent_private_kit(tmp_path, capture):
    owner = LocalOwner(tmp_path / "sender")
    recipient = LocalOwner(tmp_path / "recipient")
    try:
        copy, public = published(owner, tmp_path, capture)
        plan = owner.control(
            "plan", {"kind": "share", "public_id": public["public_id"]}
        )
        shared = submit(owner, plan)
        for _ in range(500):
            job = next(
                item
                for item in owner.read()["jobs"]
                if item["job_id"] == shared["job_id"]
            )
            if job.get("sharing", {}) and job["sharing"].get("handoff_ready"):
                break
            assert job["state"] in {"queued", "running"}, job
            time.sleep(0.01)
        else:
            pytest.fail("owned mirror never prepared its exact handoff")
        assert job["sharing"]["running"] is True
        with owner.open_handoff(job["job_id"]) as stream:
            handoff = stream.read()
        retained_handoff = (
            owner.home / "sharing/handoffs" / (job["job_id"] + ".foxhandoff")
        )
        retained_handoff.write_bytes(handoff + b" ")
        with pytest.raises(PreservationError, match="sharing_not_ready"):
            owner.open_handoff(job["job_id"])
        retained_handoff.write_bytes(handoff)
        reference = read_reference(handoff)
        assert reference.manifest_id == public["manifest_id"]
        opener = build_opener(ProxyHandler({}))
        with opener.open(
            reference.mirrors[0] + "/" + manifest_path(public["manifest_id"]), timeout=5
        ) as response:
            assert response.status == 200
        selected = review(recipient, handoff)
        received = finish(
            recipient,
            submit(
                recipient,
                recipient.control(
                    "plan",
                    {
                        "kind": "source_receive",
                        "reference_job_id": selected["reference_job_id"],
                        "keys": selected["keys"],
                        "allow_http": True,
                        "allow_private": True,
                        "max_bytes": selected["size_bytes"] * 2,
                    },
                ),
            ),
        )
        assert received["state"] == "succeeded", received
        assert recipient.read()["copies"]
        # Listening is a separate executor, so private work can finish meanwhile.
        kit = finish(
            owner,
            submit(
                owner,
                owner.control(
                    "plan", {"kind": "kit_export", "copy_id": copy["copy_id"]}
                ),
            ),
        )
        assert kit["state"] == "succeeded", kit
        with pytest.raises(PreservationError, match="sharing_busy"):
            submit(
                owner,
                owner.control(
                    "plan", {"kind": "share", "public_id": public["public_id"]}
                ),
            )
        owner.control("cancel", {"job_id": job["job_id"]})
        stopped = finish(owner, shared)
        assert stopped["state"] == "cancelled", stopped
        observed = next(
            item for item in owner.read()["jobs"] if item["job_id"] == job["job_id"]
        )
        assert observed["sharing"]["running"] is False
        with pytest.raises(PreservationError, match="sharing_not_ready"):
            owner.open_handoff(job["job_id"])
        assert len(owner.control("sharing-read", {})["public_copies"]) == 1
        assert (
            owner.home
            / "sharing/mirrors"
            / public["public_id"]
            / "store"
            / manifest_path(public["manifest_id"])
        ).is_file()
    finally:
        owner.close()
        recipient.close()


def test_registry_card_extensions_and_signed_proof_survive_public_projection(
    tmp_path, monkeypatch
):
    from test_local_owner import accepted_catalog

    from foxlight_model_preservation.assurance import load_retained_card
    from foxlight_model_preservation.cards import identity_json
    from foxlight_model_preservation.signing import encode_record

    catalog, repo, data = accepted_catalog(
        tmp_path,
        companion=False,
        metadata={
            "future_review_note": {
                "read_me": "Synthetic unknown field for public-content review",
                "padding": "x" * 80_000,
            }
        },
    )

    def download(repository, revision, file, destination, control, **kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    monkeypatch.setattr(
        "foxlight_model_preservation.local_host.owner.download_file", download
    )
    owner = LocalOwner(tmp_path / "owner", catalog=catalog)
    try:
        listing = owner.control("search", {})
        received = finish(
            owner,
            submit(
                owner,
                owner.control(
                    "plan",
                    {
                        "kind": "receive",
                        "catalog_id": listing["catalog_id"],
                        "card_id": repo.card.card_id,
                    },
                ),
            ),
        )
        assert received["state"] == "succeeded", received
        copy = owner.read()["copies"][0]
        review = owner.control("sharing-review", {"copy_id": copy["copy_id"]})
        plan = owner.control(
            "plan",
            {
                "kind": "publish",
                "copy_id": copy["copy_id"],
                "decisions": [
                    {
                        "member_id": item["member_id"],
                        "license_id": "test-only",
                        "terms_reference": "Synthetic test declaration",
                    }
                    for item in review["members"]
                ],
            },
        )
        assert len(json.dumps(plan).encode()) < 65536
        preview_raw = owner.publication_review(plan["plan_id"])
        assert len(preview_raw) > 65536
        preview = json.loads(preview_raw)
        assert preview["cards"][0]["card"]["card"]["future_review_note"][
            "read_me"
        ].startswith("Synthetic unknown")
        assert (
            len(preview["cards"][0]["card"]["card"]["future_review_note"]["padding"])
            == 80_000
        )
        result = finish(owner, submit(owner, plan))
        assert result["state"] == "succeeded", result
        public = owner.sharing.get(result["job_id"])
        manifest = verify_store(
            owner.home / "sharing/mirrors" / public.public_id / "store",
            public.manifest_id,
            public.trust,
        )
        retained = load_retained_card(identity_json(manifest.members[0].retained_card))
        assert retained.envelope.card == repo.card.card
        assert retained.envelope.card["future_properties"]["never_drop"] is True
        assert encode_record(retained.registry_proof) == encode_record(repo.proof())
    finally:
        owner.close()


def test_changed_session_policy_refuses_before_opening_listener(
    tmp_path, capture, monkeypatch
):
    from foxlight_model_preservation.mirror import run_mirror

    owner = LocalOwner(tmp_path / "owner")
    try:
        _, public = published(owner, tmp_path, capture)
        plan = owner.control(
            "plan", {"kind": "share", "public_id": public["public_id"]}
        )
        ledger = MirrorLedger(owner.home / "sharing/mirrors" / public["public_id"])
        ledger.update(
            lambda state: state.model_copy(
                update={"egress_daily": state.egress_daily + 1}
            )
        )
        monkeypatch.setattr(
            socket,
            "socket",
            lambda *a, **kw: (_ for _ in ()).throw(
                AssertionError("stale grant opened a socket")
            ),
        )
        with pytest.raises(PreservationError, match="reviewed selection"):
            run_mirror(
                ledger,
                public=True,
                seconds=1,
                expected_policy_id=plan["mirror_policy_id"],
            )
    finally:
        owner.close()


def test_operator_block_hides_sharing_without_deleting_custody(tmp_path, capture):
    from foxlight_model_preservation.mirror import model_action

    owner = LocalOwner(tmp_path / "owner")
    try:
        _, public = published(owner, tmp_path, capture)
        ledger = MirrorLedger(owner.home / "sharing/mirrors" / public["public_id"])
        model_action(ledger, public["manifest_id"], "block")
        row = owner.control("sharing-read", {})["public_copies"][0]
        assert row["available_for_sharing"] is False
        with pytest.raises(PreservationError, match="public_copy_unavailable"):
            owner.control("plan", {"kind": "share", "public_id": public["public_id"]})
        assert (ledger.path / "store" / manifest_path(public["manifest_id"])).is_file()
        assert owner.read()["copies"]
    finally:
        owner.close()


def test_failed_serving_selection_callback_stops_and_drains(tmp_path):
    from foxlight_model_preservation.mirror import run_mirror

    ledger = MirrorLedger.initialize(
        tmp_path / "mirror",
        name="Controlled callback test",
        disk_bytes=1024**3,
        ingress_daily=1,
        egress_daily=1024,
        egress_monthly=1024,
        port=0,
    )

    def failed(_):
        raise RuntimeError("controlled callback failure")

    with pytest.raises(RuntimeError, match="controlled callback"):
        run_mirror(ledger, public=True, seconds=2, serving_selection=failed)
    assert not ledger.running()


def test_http_plan_refuses_native_policy_and_does_not_clear_operator_choices(
    tmp_path, capture
):
    from foxlight_model_preservation.mirror_models import NativePolicy

    owner = LocalOwner(tmp_path / "owner")
    try:
        _, public = published(owner, tmp_path, capture)
        ledger = MirrorLedger(owner.home / "sharing/mirrors" / public["public_id"])
        old_plan = owner.control(
            "plan", {"kind": "share", "public_id": public["public_id"]}
        )
        native = NativePolicy(identity=ledger.read().identity["."], torrent=True)
        ledger.update(lambda state: state.model_copy(update={"native_policy": native}))
        with pytest.raises(PreservationError, match="sharing_http_only_required"):
            owner.control("plan", {"kind": "share", "public_id": public["public_id"]})
        assert ledger.read().native_policy == native
        refused = finish(owner, submit(owner, old_plan))
        assert refused["state"] == "failed"
        assert not ledger.running()
        assert ledger.read().native_policy == native
    finally:
        owner.close()


def test_explicit_address_shares_on_lan_without_any_default_route(
    tmp_path, capture, monkeypatch
):
    original_socket = socket.socket

    def local_only(family=socket.AF_INET, type=socket.SOCK_STREAM, *args, **kwargs):
        assert type != socket.SOCK_DGRAM, (
            "reviewed address must not need an external route"
        )
        return original_socket(family, type, *args, **kwargs)

    owner = LocalOwner(tmp_path / "owner")
    try:
        _, public = published(owner, tmp_path, capture)
        plan = owner.control(
            "plan",
            {
                "kind": "share",
                "public_id": public["public_id"],
                "advertised_address": "127.0.0.1",
            },
        )
        monkeypatch.setattr(socket, "socket", local_only)
        job = submit(owner, plan)
        for _ in range(500):
            row = next(
                item for item in owner.read()["jobs"] if item["job_id"] == job["job_id"]
            )
            if row.get("sharing") and row["sharing"]["handoff_ready"]:
                break
            assert row["state"] in {"queued", "running"}, row
            time.sleep(0.01)
        else:
            pytest.fail("explicit LAN address did not prepare a handoff")
        with owner.open_handoff(job["job_id"]) as stream:
            reference = read_reference(stream.read())
        assert reference.mirrors[0].startswith("http://127.0.0.1:")
        ledger = MirrorLedger(owner.home / "sharing/mirrors" / public["public_id"])
        from foxlight_model_preservation.mirror import record_model

        record_model(
            ledger, public["manifest_id"], {"error": "controlled integrity refusal"}
        )
        current = next(
            item for item in owner.read()["jobs"] if item["job_id"] == job["job_id"]
        )
        assert current["sharing"]["handoff_ready"] is False
        with pytest.raises(PreservationError, match="sharing_not_ready"):
            owner.open_handoff(job["job_id"])
        owner.control("cancel", {"job_id": job["job_id"]})
        assert finish(owner, job)["state"] == "cancelled"
    finally:
        owner.close()


@pytest.mark.parametrize("additional", ["model", "source"])
def test_single_model_review_refuses_broader_operator_mirror_policy(
    tmp_path, capture, additional, monkeypatch
):
    from foxlight_model_preservation.local_host import sharing

    owner = LocalOwner(tmp_path / "owner")
    try:
        _, public = published(owner, tmp_path, capture)
        plan = owner.control(
            "plan", {"kind": "share", "public_id": public["public_id"]}
        )
        ledger = MirrorLedger(owner.home / "sharing/mirrors" / public["public_id"])
        current = ledger.read()
        held = current.models[public["manifest_id"]]
        if additional == "model":
            changed = current.model_copy(
                update={"models": {**current.models, "f" * 64: held}}
            )
        else:
            changed = current.model_copy(update={"sources": {"f" * 64: held.grant}})
        ledger.update(lambda _: changed)
        monkeypatch.setattr(
            sharing, "run_mirror", lambda *a, **kw: pytest.fail("no listener may open")
        )
        with pytest.raises(
            PreservationError, match="sharing_single_selection_required"
        ):
            owner.control("plan", {"kind": "share", "public_id": public["public_id"]})
        with pytest.raises(
            PreservationError, match="sharing_single_selection_required"
        ):
            owner.sharing.require_session({**plan, "mirror_policy_id": "f" * 64})
        refused = finish(owner, submit(owner, plan))
        assert refused["state"] == "failed"
        assert not ledger.running()
        assert ledger.read() == changed
    finally:
        owner.close()


def test_publication_cleans_only_disposable_work_and_manual_interrupted_cleanup(
    tmp_path, capture
):
    from foxlight_model_preservation.operations import OperationControl
    from foxlight_model_preservation.storage import directory

    owner = LocalOwner(tmp_path / "owner")
    try:
        copy, public = published(owner, tmp_path, capture)
        assert not owner.sharing.work_exists(public["public_id"])
        assert (
            owner.sharing.get(public["public_id"]).manifest_id == public["manifest_id"]
        )
        assert owner._copy(copy["copy_id"]).manifest_id == copy["manifest_id"]
        attempt = owner.home / "sharing/work" / public["public_id"]
        with directory(attempt, create=True):
            pass
        (attempt / "temporary.bin").write_bytes(b"owned temporary copy")
        active = next(
            item
            for item in owner.read()["jobs"]
            if item["job_id"] == public["public_id"]
        )
        assert active["temporary_sharing_files"] is True
        owner._controls[public["public_id"]] = OperationControl()
        with pytest.raises(PreservationError, match="sharing_busy_try_again"):
            owner.control("sharing-work-remove", {"job_id": public["public_id"]})
        owner._controls.pop(public["public_id"])
        owner.control("sharing-work-remove", {"job_id": public["public_id"]})
        assert not attempt.exists()
        assert (
            owner.sharing.get(public["public_id"]).manifest_id == public["manifest_id"]
        )
        assert owner._copy(copy["copy_id"]).manifest_id == copy["manifest_id"]
        # A substituted symlink is never followed into the retained mirror.
        attempt.symlink_to(
            owner.home / "sharing/mirrors" / public["public_id"],
            target_is_directory=True,
        )
        owner.control("sharing-work-remove", {"job_id": public["public_id"]})
        assert attempt.is_symlink()
        assert (
            owner.sharing.get(public["public_id"]).manifest_id == public["manifest_id"]
        )
    finally:
        owner.close()


def test_failed_publication_retains_scratch_until_operator_cleanup_after_restart(
    tmp_path, capture, monkeypatch
):
    from foxlight_model_preservation.local_host import sharing

    owner = LocalOwner(tmp_path / "owner")
    copy, public = published(owner, tmp_path, capture)
    members = owner.control("sharing-review", {"copy_id": copy["copy_id"]})["members"]
    plan = owner.control(
        "plan",
        {
            "kind": "publish",
            "copy_id": copy["copy_id"],
            "decisions": [
                {
                    "member_id": item["member_id"],
                    "license_id": "test-only",
                    "terms_reference": "LICENSE",
                }
                for item in members
            ],
        },
    )

    def fail(*args, **kwargs):
        original = args[3]
        original.mkdir()
        (original / "partial").write_bytes(b"private temporary bytes")
        raise OSError("controlled failed publication")

    monkeypatch.setattr(sharing, "export_preservation", fail)
    update = owner._update
    terminal_cleanup = []

    def observed_update(job, **changes):
        result = update(job, **changes)
        if changes.get("state") == "failed":
            terminal_cleanup.append(
                next(
                    row["temporary_sharing_files"]
                    for row in owner.read()["jobs"]
                    if row["job_id"] == job.job_id
                )
            )
        return result

    monkeypatch.setattr(owner, "_update", observed_update)
    failed = finish(owner, submit(owner, plan))
    assert failed["state"] == "failed"
    assert failed["temporary_sharing_files"] is True
    assert terminal_cleanup == [True]
    owner.close()
    owner = LocalOwner(owner.home)
    try:
        receipt = next(
            item for item in owner.read()["jobs"] if item["job_id"] == failed["job_id"]
        )
        assert receipt["temporary_sharing_files"] is True
        owner.control("sharing-work-remove", {"job_id": failed["job_id"]})
        assert not owner.sharing.work_exists(failed["job_id"])
        assert (
            owner.sharing.get(public["public_id"]).manifest_id == public["manifest_id"]
        )
        assert owner._copy(copy["copy_id"]).manifest_id == copy["manifest_id"]
    finally:
        owner.close()


def test_already_public_omitted_defaults_keep_original_signed_identity(
    tmp_path, capture
):
    import base64
    from dataclasses import replace

    from foxlight_model_preservation.signing import (
        BUNDLE_TYPE,
        Envelope,
        Signature,
        authenticated_encoding,
        canonical_bytes,
        digest,
        public_record,
    )

    original = verify_store(capture.store, capture.identity, capture.trust)
    value = original.model_dump(mode="json")
    value.pop("attestation")
    payload = canonical_bytes(value)
    identifier = digest(payload)
    envelope = Envelope(
        payloadType=BUNDLE_TYPE,
        payload=base64.b64encode(payload).decode(),
        signatures=(
            Signature(
                keyid=public_record(capture.key).key_id,
                sig=base64.b64encode(
                    capture.key.sign(authenticated_encoding(BUNDLE_TYPE, payload))
                ).decode(),
            ),
        ),
    )
    from foxlight_model_preservation.signing import encode_record

    raw = encode_record(envelope)
    (capture.store / manifest_path(identifier)).write_bytes(raw)
    owner = LocalOwner(tmp_path / "owner")
    try:
        copy = saved(owner, tmp_path, replace(capture, identity=identifier))
        plan = owner.control(
            "plan", {"kind": "publish", "copy_id": copy["copy_id"], "decisions": []}
        )
        assert (
            plan["already_public"] is True and plan["public_manifest_id"] == identifier
        )
        job = finish(owner, submit(owner, plan))
        assert job["state"] == "succeeded", job
        public = owner.sharing.get(job["job_id"])
        assert public.manifest_id == identifier
        store = owner.home / "sharing/mirrors" / job["job_id"] / "store"
        assert (store / manifest_path(identifier)).read_bytes() == raw
    finally:
        owner.close()
