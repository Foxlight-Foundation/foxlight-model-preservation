"""One-file app receiving preserves the public tools' independent trust boundary."""

import json
import shutil

import pytest
from test_discovery import mirror
from test_local_owner import finish, submit

from foxlight_model_preservation.handoffs import Handoff
from foxlight_model_preservation.invitations import create_invitation
from foxlight_model_preservation.local_host.owner import LocalOwner
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.signing import encode_record


def review(owner, document):
    upload = owner.control("upload-create", {"size_bytes": len(document)})
    owner.upload(upload["upload_id"], 0, document)
    result = owner.control("handoff-review", {"upload_id": upload["upload_id"]})
    owner.control("upload-remove", {"upload_id": upload["upload_id"]})
    return result


@pytest.mark.parametrize("wrapper", [False, True])
def test_handoff_offline_review_separate_keys_and_complete_receive(
    tmp_path, capture, wrapper
):
    owner = LocalOwner(tmp_path / "recipient")
    try:
        with mirror(capture.store) as (endpoint, requests):
            invitation = create_invitation(
                capture.store, capture.identity, capture.trust, (endpoint,)
            )
            document = encode_record(
                Handoff(reference=invitation) if wrapper else invitation
            )
            selected = review(owner, document)
            assert selected["from_handoff"] is True
            assert selected["manifest_id"] == capture.identity
            assert requests == []
            assert owner.control("source-read", {})["sources"] == []
            assert owner.read()["jobs"] == []
            request = {
                "kind": "source_receive",
                "reference_job_id": selected["reference_job_id"],
                "keys": [],
                "allow_http": True,
                "allow_private": True,
                "max_bytes": selected["size_bytes"] * 2,
            }
            with pytest.raises(PreservationError):
                owner.control("plan", request)
            assert requests == []
            request["keys"] = selected["keys"]
            plan = owner.control("plan", request)
            received = finish(owner, submit(owner, plan))
            assert received["state"] == "succeeded", received
            assert len(owner.read()["copies"]) == 1
            assert not any(
                "manifests" in path for path in requests
            )  # envelope was in file
            assert (
                owner.home
                / "model-references"
                / (selected["reference_job_id"] + ".handoff")
            ).read_bytes() == document
        shutil.rmtree(capture.store)
        copy = owner.read()["copies"][0]
        restored = finish(
            owner,
            submit(
                owner,
                owner.control("plan", {"kind": "restore", "copy_id": copy["copy_id"]}),
            ),
        )
        assert restored["state"] == "succeeded", restored
        assert (
            owner.restored_directory(restored["job_id"]) / "members/weights/model.bin"
        ).is_file()
    finally:
        owner.close()


def test_changed_handoff_cannot_replace_reviewed_transport(tmp_path, capture):
    owner = LocalOwner(tmp_path / "recipient")
    try:
        invitation = create_invitation(
            capture.store, capture.identity, capture.trust, ("https://mirror.example",)
        )
        selected = review(owner, encode_record(Handoff(reference=invitation)))
        plan = owner.control(
            "plan",
            {
                "kind": "source_receive",
                "reference_job_id": selected["reference_job_id"],
                "keys": selected["keys"],
                "allow_http": False,
                "allow_private": False,
                "max_bytes": selected["size_bytes"],
            },
        )
        path = (
            owner.home / "model-references" / (selected["reference_job_id"] + ".json")
        )
        value = json.loads(path.read_bytes())
        value["invitation"]["mirrors"] = ["https://replacement.example"]
        path.write_text(json.dumps(value))
        refused = finish(owner, submit(owner, plan))
        assert refused["state"] == "failed"
        assert refused["error_code"] == "source_model_reference_changed"
        assert owner.read()["copies"] == []
    finally:
        owner.close()
