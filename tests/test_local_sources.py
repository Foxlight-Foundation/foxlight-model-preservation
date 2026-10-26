"""Actual independent owner: source trust, finite work and complete offline custody."""

import shutil
import socket
import threading

import pytest
from test_discovery import Publisher, mirror
from test_local_owner import finish, submit

from foxlight_model_preservation.local_host.owner import LocalOwner
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.signing import encode_record
from foxlight_model_preservation.storage import manifest_path


def enroll(owner, document):
    upload = owner.control("upload-create", {"size_bytes": len(document)})
    owner.upload(upload["upload_id"], 0, document)
    review = owner.control("source-review", {"upload_id": upload["upload_id"]})
    owner.control("upload-remove", {"upload_id": upload["upload_id"]})
    owner.control(
        "source-follow",
        {
            "review_id": review["review_id"],
            "confirmed": True,
            "allow_http": True,
            "allow_private": True,
        },
    )
    return review


def source_plan(owner, kind, source, **kwargs):
    return owner.control(
        "plan", {"kind": kind, "source_id": source["source_id"], **kwargs}
    )


def test_independent_follow_search_receive_and_offline_restore(tmp_path, capture):
    publisher = Publisher(tmp_path / "publisher", capture)
    home = tmp_path / "owner"
    original_envelope = (capture.store / manifest_path(capture.identity)).read_bytes()
    with (
        mirror(publisher.path) as (endpoint, metadata_requests),
        mirror(capture.store) as (model_endpoint, model_requests),
    ):
        publisher.mirrors = (model_endpoint,)
        publisher.publish()
        owner = LocalOwner(home)
        try:
            source = enroll(owner, encode_record(publisher.bootstrap(endpoint)))
            assert metadata_requests == model_requests == []
            assert owner.control("source-search", {})["total"] == 0
            refreshed = finish(
                owner, submit(owner, source_plan(owner, "source_refresh", source))
            )
            assert refreshed["state"] == "succeeded", refreshed
            found = owner.control("source-search", {"query": "offline writing"})[
                "results"
            ][0]
            assert found["name"] == "First model"
            assert found["has_candidate_keys"] is True
            assert model_requests == []
            details_plan = source_plan(
                owner,
                "source_metadata",
                source,
                entry_id=found["entry_id"],
                manifest_id=found["manifest_id"],
                allow_http=True,
                allow_private=True,
            )
            details_job = finish(owner, submit(owner, details_plan))
            assert details_job["state"] == "succeeded", details_job
            assert model_requests == ["/" + manifest_path(capture.identity)]
            reference = owner.control(
                "source-reference", {"job_id": details_job["job_id"]}
            )
            request = {
                "kind": "source_receive",
                "reference_job_id": details_job["job_id"],
                "keys": [],
                "allow_http": True,
                "allow_private": True,
                "max_bytes": reference["size_bytes"] * 2,
            }
            with pytest.raises(PreservationError):
                owner.control("plan", request)
            assert len(model_requests) == 1  # following never enrolls model keys
            request["keys"] = reference["keys"]
            receive_plan = owner.control("plan", request)
            received = finish(owner, submit(owner, receive_plan))
            assert received["state"] == "succeeded", received
            assert model_requests.count("/" + manifest_path(capture.identity)) == 1
            copy = owner.read()["copies"][0]
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
            with owner.open_kit(kit["job_id"]) as stream:
                saved = stream.read()
            # Stop following never deletes preserved models or restores.
            owner.control("source-remove", {"source_id": source["source_id"]})
            assert owner.control("source-search", {})["total"] == 0
            assert len(owner.read()["copies"]) == 1
        finally:
            owner.close()
    shutil.rmtree(home)
    shutil.rmtree(publisher.path)
    shutil.rmtree(capture.store)
    recipient = LocalOwner(tmp_path / "recipient")
    try:
        upload = recipient.control("upload-create", {"size_bytes": len(saved)})
        recipient.upload(upload["upload_id"], 0, saved)
        imported = finish(
            recipient,
            submit(
                recipient,
                recipient.control(
                    "plan", {"kind": "kit_import", "upload_id": upload["upload_id"]}
                ),
            ),
        )
        assert imported["state"] == "succeeded", imported
        restored = finish(
            recipient,
            submit(
                recipient,
                recipient.control(
                    "plan", {"kind": "restore", "copy_id": imported["copy_id"]}
                ),
            ),
        )
        assert restored["state"] == "succeeded", restored
        output = recipient.restored_directory(restored["job_id"])
        assert (output / "members/weights/model.bin").read_bytes() == (
            b"weights\0"
        ) * 1000
        assert (output / "members/tokenizer/model.bin").read_bytes() == (
            b"tokenizer\0"
        ) * 1000
        assert any(
            file.is_file() and file.read_bytes() == original_envelope
            for file in output.rglob("*")
        )
    finally:
        recipient.close()


def test_source_update_failure_and_restart_keep_offline_cache(
    tmp_path, capture, monkeypatch
):
    publisher = Publisher(tmp_path / "publisher", capture)
    with mirror(publisher.path) as (endpoint, requests):
        owner = LocalOwner(tmp_path / "owner")
        try:
            source = enroll(owner, encode_record(publisher.bootstrap(endpoint)))
            assert (
                finish(
                    owner, submit(owner, source_plan(owner, "source_refresh", source))
                )["state"]
                == "succeeded"
            )
            previous = owner.control("source-search", {})
            publisher.publish(version=2, wrong_catalog_key=True)
            failed = finish(
                owner, submit(owner, source_plan(owner, "source_refresh", source))
            )
            assert failed["state"] == "failed"
            retained = owner.control("source-search", {})
            assert (
                retained["results"][0]["manifest_id"]
                == previous["results"][0]["manifest_id"]
            )
            assert retained["results"][0]["freshness"] == "retained"
        finally:
            owner.close()
        requests.clear()
        owner = LocalOwner(tmp_path / "owner")
        try:
            monkeypatch.setattr(
                socket,
                "getaddrinfo",
                lambda *a, **kw: pytest.fail("offline search performed DNS"),
            )
            assert owner.control("source-search", {}) == retained
            assert owner.control("source-read", {})["sources"][0]["last_error"]
            assert requests == []
        finally:
            owner.close()


def test_metadata_cancel_does_not_replay_after_owner_restart(
    tmp_path, capture, monkeypatch
):
    publisher = Publisher(tmp_path / "publisher", capture)
    home = tmp_path / "owner"
    owner = LocalOwner(home)
    entered, release = threading.Event(), threading.Event()
    with mirror(publisher.path) as (endpoint, requests):
        source = enroll(owner, encode_record(publisher.bootstrap(endpoint)))
        original = owner.sources.run

        def blocked(job_id, plan, control):
            entered.set()
            assert release.wait(5)
            control.checkpoint()
            return original(job_id, plan, control)

        monkeypatch.setattr(owner.sources, "run", blocked)
        receipt = submit(owner, source_plan(owner, "source_refresh", source))
        try:
            assert entered.wait(5)
            owner.control("cancel", {"job_id": receipt["job_id"]})
        finally:
            release.set()
        assert finish(owner, receipt)["state"] == "cancelled"
        assert requests == []
        owner.close()
        owner = LocalOwner(home)
        try:
            assert owner.read()["jobs"][0]["state"] == "cancelled"
            assert requests == []
        finally:
            owner.close()


def test_changed_source_selection_refuses_before_model_contact(tmp_path, capture):
    publisher = Publisher(tmp_path / "publisher", capture)
    with (
        mirror(publisher.path) as (endpoint, _),
        mirror(capture.store) as (model_endpoint, model_requests),
    ):
        publisher.mirrors = (model_endpoint,)
        publisher.publish()
        owner = LocalOwner(tmp_path / "owner")
        try:
            source = enroll(owner, encode_record(publisher.bootstrap(endpoint)))
            assert (
                finish(
                    owner, submit(owner, source_plan(owner, "source_refresh", source))
                )["state"]
                == "succeeded"
            )
            plan = source_plan(
                owner,
                "source_metadata",
                source,
                entry_id="small_text",
                manifest_id=capture.identity,
                allow_http=True,
                allow_private=True,
            )
            publisher.name = "Changed source statement"
            publisher.publish(version=2)
            assert (
                finish(
                    owner, submit(owner, source_plan(owner, "source_refresh", source))
                )["state"]
                == "succeeded"
            )
            refused = finish(owner, submit(owner, plan))
            assert refused["state"] == "failed"
            assert refused["error_code"] == "source_model_reference_changed"
            assert model_requests == []
        finally:
            owner.close()


def test_model_detail_permissions_and_original_wrapper_pin(tmp_path, capture):
    publisher = Publisher(tmp_path / "publisher", capture)
    with (
        mirror(publisher.path) as (endpoint, _),
        mirror(capture.store) as (model_endpoint, model_requests),
    ):
        publisher.mirrors = (model_endpoint,)
        publisher.publish()
        owner = LocalOwner(tmp_path / "owner")
        try:
            source = enroll(owner, encode_record(publisher.bootstrap(endpoint)))
            assert (
                finish(
                    owner, submit(owner, source_plan(owner, "source_refresh", source))
                )["state"]
                == "succeeded"
            )
            # Source HTTP/private permission is not inherited by model metadata.
            plan = source_plan(
                owner,
                "source_metadata",
                source,
                entry_id="small_text",
                manifest_id=capture.identity,
            )
            assert finish(owner, submit(owner, plan))["state"] == "failed"
            assert model_requests == []
            plan = source_plan(
                owner,
                "source_metadata",
                source,
                entry_id="small_text",
                manifest_id=capture.identity,
                allow_http=True,
                allow_private=True,
            )
            metadata = finish(owner, submit(owner, plan))
            assert metadata["state"] == "succeeded"
            reference = owner.control(
                "source-reference", {"job_id": metadata["job_id"]}
            )
            request = {
                "kind": "source_receive",
                "reference_job_id": metadata["job_id"],
                "keys": reference["keys"],
                "allow_http": True,
                "allow_private": True,
                "max_bytes": reference["size_bytes"] * 2,
            }
            planned = owner.control("plan", request)
            raw = owner.home / "model-references" / (metadata["job_id"] + ".envelope")
            raw.write_bytes(
                raw.read_bytes() + b" "
            )  # valid JSON/signatures, different original wrapper
            refused = finish(owner, submit(owner, planned))
            assert refused["state"] == "failed"
            assert refused["error_code"] == "source_model_reference_changed"
            assert len(model_requests) == 1  # no object fetch after a changed review
            assert owner.read()["copies"] == []
        finally:
            owner.close()
