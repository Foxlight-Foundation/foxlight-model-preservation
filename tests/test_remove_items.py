"""Finished Activity entries and saved copies can be removed."""

import pytest
from test_local_owner import finish, submit

from foxlight_model_preservation.kits import export_kit
from foxlight_model_preservation.local_host.owner import LocalOwner
from foxlight_model_preservation.models import PreservationError


def import_copy(owner, kit):
    content = kit.read_bytes()
    upload = owner.control("upload-create", {"size_bytes": len(content)})
    owner.upload(upload["upload_id"], 0, content)
    plan = owner.control(
        "plan", {"kind": "kit_import", "upload_id": upload["upload_id"]}
    )
    job = finish(owner, submit(owner, plan))
    assert job["state"] == "succeeded"
    return job


@pytest.fixture
def kit(tmp_path, capture):
    path = tmp_path / "selected.foxkit"
    export_kit(capture.store, capture.identity, capture.trust, path)
    return path


def test_finished_activity_entries_are_removed_with_their_own_files(tmp_path, kit):
    owner = LocalOwner(tmp_path / "home")
    try:
        saved = import_copy(owner, kit)
        exported = finish(
            owner,
            submit(
                owner,
                owner.control(
                    "plan", {"kind": "kit_export", "copy_id": saved["copy_id"]}
                ),
            ),
        )
        kit_file = owner.home / "kits" / (exported["job_id"] + ".foxkit")
        assert kit_file.is_file()
        result = owner.control("activity-remove", {"job_ids": [exported["job_id"]]})
        assert result == {"removed": 1}
        ids = {job["job_id"] for job in owner.read()["jobs"]}
        assert exported["job_id"] not in ids and saved["job_id"] in ids
        assert not kit_file.exists()  # only reachable from its Activity entry
        # The saved copy is untouched.
        assert owner.read()["copies"][0]["copy_id"] == saved["copy_id"]
        # A replay of the removed request is still recognised, not re-run.
        with pytest.raises(PreservationError):
            owner._job(exported["job_id"])
        assert owner.control("activity-remove", {"finished": True})["removed"] >= 1
        assert owner.read()["jobs"] == []
        assert owner.read()["copies"]  # copies are not Activity
    finally:
        owner.close()


def test_running_work_is_never_removed(tmp_path, kit, monkeypatch):
    owner = LocalOwner(tmp_path / "home")
    try:
        saved = import_copy(owner, kit)
        owner._controls[saved["job_id"]] = object()  # still running
        assert owner.control("activity-remove", {"job_ids": [saved["job_id"]]}) == {
            "removed": 0
        }
        owner._controls.pop(saved["job_id"])
        with pytest.raises(PreservationError):
            owner.control("activity-remove", {"job_ids": "all"})
    finally:
        owner.close()


def test_a_saved_copy_is_removed_only_when_confirmed(tmp_path, kit):
    owner = LocalOwner(tmp_path / "home")
    try:
        saved = import_copy(owner, kit)
        store = owner.home / "copies" / saved["copy_id"]
        assert store.is_dir()
        with pytest.raises(PreservationError, match="invalid_control"):
            owner.control("copy-remove", {"copy_id": saved["copy_id"]})
        owner.control("copy-remove", {"copy_id": saved["copy_id"], "confirmed": True})
        assert owner.read()["copies"] == []
        assert not store.exists()
        with pytest.raises(PreservationError):
            owner.control(
                "copy-remove", {"copy_id": saved["copy_id"], "confirmed": True}
            )
    finally:
        owner.close()


def test_a_copy_in_use_is_not_removed(tmp_path, kit, monkeypatch):
    owner = LocalOwner(tmp_path / "home")
    try:
        saved = import_copy(owner, kit)
        plan = owner.control(
            "plan", {"kind": "kit_export", "copy_id": saved["copy_id"]}
        )
        # A queued export of this copy holds it.
        monkeypatch.setattr(owner._executor, "submit", lambda *args: None)
        started = submit(owner, plan)
        with pytest.raises(PreservationError, match="copy_busy_try_again"):
            owner.control(
                "copy-remove", {"copy_id": saved["copy_id"], "confirmed": True}
            )
        owner._controls.pop(started["job_id"])
    finally:
        owner.close()


def test_a_finished_save_keeps_no_second_copy_in_its_working_folder(tmp_path, kit):
    owner = LocalOwner(tmp_path / "home")
    try:
        saved = import_copy(owner, kit)
        # Removed right after the copy is recorded (just after "succeeded").
        import time

        end = time.monotonic() + 10
        work = owner.home / "work" / saved["job_id"]
        while work.exists() and time.monotonic() < end:
            time.sleep(0.02)
        assert not work.exists()
        # Leftovers from earlier releases are cleared on the next start.
        leftover = owner.home / "work" / saved["copy_id"] / "original"
        leftover.mkdir(parents=True)
        (leftover / "weights.bin").write_bytes(b"x" * 1000)
    finally:
        owner.close()
    reopened = LocalOwner(tmp_path / "home")
    try:
        end = time.monotonic() + 10
        while leftover.parent.exists() and time.monotonic() < end:
            time.sleep(0.05)
        assert not leftover.parent.exists()
        assert reopened.read()["copies"]  # the saved copy is untouched
    finally:
        reopened.close()


def test_removing_a_failed_save_releases_its_partial_store(tmp_path):
    import time
    import uuid

    from foxlight_model_preservation.local_host.owner import Job

    owner = LocalOwner(tmp_path / "home")
    try:
        job = Job(
            job_id="f" * 64,
            operation_id=str(uuid.uuid4()),
            plan_id="e" * 64,
            kind="source_receive",
            state="failed",
            created_at=time.time(),
            name="half-downloaded",
            error_code="origin_unavailable",
        )
        owner._journal = owner._journal.model_copy(update={"jobs": (job,)})
        partial = owner.home / "copies" / job.job_id / "objects"
        partial.mkdir(parents=True)
        (partial / "piece").write_bytes(b"x" * 100)
        assert owner.control("activity-remove", {"job_ids": [job.job_id]}) == {
            "removed": 1
        }
        assert not partial.parent.exists()
    finally:
        owner.close()
