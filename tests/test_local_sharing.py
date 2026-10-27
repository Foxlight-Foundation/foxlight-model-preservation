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
