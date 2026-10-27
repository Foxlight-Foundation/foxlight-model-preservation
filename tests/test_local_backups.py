"""Complete backup files survive owner loss; disconnects cannot redirect writes."""

import json
import shutil
from pathlib import Path

import pytest
from test_local_owner import finish, submit

from foxlight_model_preservation.kits import export_kit, verify_kit
from foxlight_model_preservation.local_host.owner import LocalOwner
from foxlight_model_preservation.models import PreservationError


def saved(owner, tmp_path, capture):
    kit = tmp_path / "initial.foxkit"
    export_kit(capture.store, capture.identity, capture.trust, kit)
    data = kit.read_bytes()
    upload = owner.control("upload-create", {"size_bytes": len(data)})
    owner.upload(upload["upload_id"], 0, data)
    job = finish(
        owner,
        submit(
            owner,
            owner.control(
                "plan", {"kind": "kit_import", "upload_id": upload["upload_id"]}
            ),
        ),
    )
    assert job["state"] == "succeeded", job
    return owner.read()["copies"][0]


def destination(owner, path):
    review = owner.control(
        "backup-review", {"path": str(path), "label": "My other drive"}
    )
    return owner.control(
        "backup-setup", {"review_id": review["review_id"], "confirmed": True}
    )


def test_complete_backup_and_fresh_owner_restore_without_originals(tmp_path, capture):
    owner = LocalOwner(tmp_path / "owner")
    copy = saved(owner, tmp_path, capture)
    target = destination(owner, tmp_path / "backup")
    plan = owner.control(
        "plan",
        {
            "kind": "backup",
            "copy_id": copy["copy_id"],
            "destination_id": target["destination_id"],
        },
    )
    job = finish(owner, submit(owner, plan))
    assert job["state"] == "succeeded", job
    assert owner.artifact_directory(job["job_id"]) == Path(target["path"])
    kit = Path(job["output"])
    data = kit.read_bytes()
    owner.control("backup-forget", {"destination_id": target["destination_id"]})
    assert kit.read_bytes() == data
    owner.close()
    shutil.rmtree(owner.home)
    shutil.rmtree(capture.store)
    recipient = LocalOwner(tmp_path / "fresh")
    try:
        upload = recipient.control("upload-create", {"size_bytes": len(data)})
        recipient.upload(upload["upload_id"], 0, data)
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
        assert (
            recipient.restored_directory(restored["job_id"])
            / "members/weights/model.bin"
        ).read_bytes() == b"weights\0" * 1000
    finally:
        recipient.close()


@pytest.mark.parametrize("replacement", ["missing", "folder", "symlink", "marker"])
def test_disconnected_or_replaced_destination_does_not_receive_backup(
    tmp_path, capture, replacement
):
    owner = LocalOwner(tmp_path / "owner")
    try:
        copy = saved(owner, tmp_path, capture)
        path = tmp_path / "backup"
        target = destination(owner, path)
        plan = owner.control(
            "plan",
            {
                "kind": "backup",
                "copy_id": copy["copy_id"],
                "destination_id": target["destination_id"],
            },
        )
        if replacement == "marker":
            (path / ".sovereign-backup.json").write_text("{}")
        else:
            path.rename(tmp_path / "disconnected")
            if replacement == "folder":
                path.mkdir(mode=0o700)
            elif replacement == "symlink":
                path.symlink_to(tmp_path / "disconnected", target_is_directory=True)
        job = finish(owner, submit(owner, plan))
        assert job["state"] == "failed", job
        assert not list(tmp_path.rglob("Sovereign-*.foxkit"))
        assert len(owner.read()["copies"]) == 1
        if replacement == "missing":
            assert not path.exists()
    finally:
        owner.close()


def test_backup_review_never_creates_missing_mount_or_changes_permissions(tmp_path):
    owner = LocalOwner(tmp_path / "owner")
    try:
        with pytest.raises(FileNotFoundError):
            owner.control(
                "backup-review",
                {"path": str(tmp_path / "not-mounted" / "backup"), "label": "NAS"},
            )
        assert not (tmp_path / "not-mounted").exists()
        shared = tmp_path / "shared"
        shared.mkdir(mode=0o755)
        with pytest.raises(PreservationError, match="empty_private"):
            owner.control("backup-review", {"path": str(shared), "label": "Shared"})
        assert shared.stat().st_mode & 0o777 == 0o755
    finally:
        owner.close()


def test_journal_migration_keeps_previous_owner_metadata(tmp_path):
    owner = LocalOwner(tmp_path / "owner")
    owner.close()
    path = owner.home / "journal.json"
    value = json.loads(path.read_bytes())
    value["schema_version"] = "sovereign.local-owner.v1"
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    updated = LocalOwner(owner.home)
    updated.close()
    assert json.loads(path.read_bytes())["schema_version"] == "sovereign.local-owner.v2"
    assert (owner.home / "journal-before-v2.json").read_bytes() == raw


def test_kit_readback_detects_corrupt_bytes_without_import(tmp_path, capture):
    kit = tmp_path / "copy.foxkit"
    export_kit(capture.store, capture.identity, capture.trust, kit)
    verify_kit(kit, trust=capture.trust, expected_manifest_id=capture.identity)
    with kit.open("r+b") as stream:
        stream.seek(-1, 2)
        old = stream.read(1)
        stream.seek(-1, 2)
        stream.write(bytes([old[0] ^ 1]))
    with pytest.raises(PreservationError, match="integrity"):
        verify_kit(kit, trust=capture.trust, expected_manifest_id=capture.identity)


def test_existing_destination_space_comes_from_that_drive_not_parent(
    tmp_path, monkeypatch
):
    import os
    from collections import namedtuple

    from foxlight_model_preservation.local_host.backups import Backups

    folder = tmp_path / "drive"
    folder.mkdir(mode=0o700)
    owner = tmp_path / "owner"
    owner.mkdir(mode=0o700)
    target_inode = folder.stat().st_ino
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(
        "foxlight_model_preservation.local_host.backups.shutil.disk_usage",
        lambda fd: (
            Usage(1000, 900, 100)
            if os.fstat(fd).st_ino == target_inode
            else Usage(10000, 100, 9900)
        ),
    )
    existing = Backups(owner).review(str(folder), "Connected drive")
    assert existing["free_bytes"] == 100
    new = Backups(owner).review(str(tmp_path / "new-folder"), "New child")
    assert new["free_bytes"] == 9900
