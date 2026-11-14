"""Keeping model copies on any connected drive, NAS or folder."""

import os
import time

import pytest
from test_local_owner import finish, submit

from foxlight_model_preservation.kits import export_kit
from foxlight_model_preservation.local_host.library import (
    MARKER,
    Library,
    LibraryMarker,
    move_tree,
)
from foxlight_model_preservation.local_host.owner import LocalOwner
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationControl
from foxlight_model_preservation.signing import digest, encode_record


def choose(library: Library, path) -> dict:
    review = library.review(str(path))
    return library.setup(review["review_id"])


def test_default_library_is_the_owner_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    library = Library(home)
    assert library.root() == home
    assert library.find("copies/x") == home / "copies/x"
    assert library.view()["system"] is True


def test_chosen_folder_is_created_marked_and_used(tmp_path):
    home, drive = tmp_path / "home", tmp_path / "drive"
    home.mkdir(mode=0o700)
    drive.mkdir()
    library = Library(home)
    review = library.review(str(drive / "Sovereign Library"))
    assert review["exists"] is False and review["reattach"] is False
    assert not (drive / "Sovereign Library").exists()  # nothing until setup
    view = library.setup(review["review_id"])
    folder = drive / "Sovereign Library"
    assert view["path"] == str(folder) and view["available"] and view["private"]
    assert os.stat(folder).st_mode & 0o777 == 0o700
    marker = LibraryMarker.model_validate_json((folder / MARKER).read_bytes())
    assert marker.owner_id == library.owner_id()
    assert library.root() == folder
    # Copies saved before the change are still found where they are.
    (home / "copies/old").mkdir(parents=True)
    assert library.find("copies/old") == home / "copies/old"
    assert library.find("copies/new") == folder / "copies/new"


def test_folders_with_other_data_or_another_owner_are_refused(tmp_path):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    library = Library(home)
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "photos.jpg").write_bytes(b"x")
    with pytest.raises(PreservationError, match="library_folder_not_empty"):
        library.review(str(busy))
    other = tmp_path / "other"
    other.mkdir()
    (other / MARKER).write_bytes(
        encode_record(LibraryMarker(owner_id=digest(b"another computer")))
    )
    with pytest.raises(PreservationError, match="library_folder_belongs_to_another"):
        library.review(str(other))
    with pytest.raises(PreservationError, match="library_folder_inside_sovereign"):
        library.review(str(home / "inside"))


def test_reattaching_this_owners_folder_keeps_its_contents(tmp_path):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    library = Library(home)
    folder = tmp_path / "Sovereign Library"
    choose(library, folder)
    (folder / "copies/kept").mkdir(parents=True)
    library.use_home()
    assert library.root() == home
    assert library.find("copies/kept") == folder / "copies/kept"  # still found
    review = library.review(str(folder))
    assert review["reattach"] is True
    library.setup(review["review_id"])
    assert library.root() == folder and (folder / "copies/kept").is_dir()


def test_missing_drive_stops_saving_and_is_never_recreated(tmp_path):
    home, drive = tmp_path / "home", tmp_path / "drive"
    home.mkdir(mode=0o700)
    drive.mkdir()
    library = Library(home)
    (home / "copies/old").mkdir(parents=True)
    choose(library, drive / "Sovereign Library")
    os.rename(drive, tmp_path / "unplugged")
    with pytest.raises(PreservationError, match="library_storage_unavailable"):
        library.root()
    assert library.view()["available"] is False
    # Copies on connected folders still work; the drive is not recreated.
    assert library.find("copies/old") == home / "copies/old"
    assert not drive.exists()
    os.rename(tmp_path / "unplugged", drive)
    assert library.root() == drive / "Sovereign Library"


def test_move_tree_reads_back_every_file_and_links_shared_objects(tmp_path):
    source = tmp_path / "source"
    data = b"weights" * 1000
    name = digest(data)
    (source / "objects/sha256").mkdir(parents=True)
    (source / "objects/sha256" / name).write_bytes(data)
    (source / "manifest.json").write_bytes(b"{}")
    links: dict = {}
    seen = []
    move_tree(source, tmp_path / "first", links, OperationControl(), seen.append)
    assert (tmp_path / "first/objects/sha256" / name).read_bytes() == data
    assert sum(seen) == len(data) + 2
    # A second store holding the same object links to the moved one.
    second = tmp_path / "published"
    (second / "store/objects/sha256").mkdir(parents=True)
    (second / "store/objects/sha256" / name).write_bytes(data)
    move_tree(second, tmp_path / "second", links, OperationControl(), lambda _: None)
    assert (
        os.stat(tmp_path / "second/store/objects/sha256" / name).st_ino
        == os.stat(tmp_path / "first/objects/sha256" / name).st_ino
    )
    os.symlink("/etc/hosts", source / "link")
    with pytest.raises(PreservationError, match="library_move_unsupported_file"):
        move_tree(source, tmp_path / "third", {}, OperationControl(), lambda _: None)


def wait_moved(owner, seconds: float = 60.0) -> dict:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        library = owner.read()["library"]
        if library["move"] and library["move"]["state"] != "moving":
            return library
        time.sleep(0.02)
    raise AssertionError("library move did not finish")


def test_owner_saves_to_the_chosen_drive_and_moves_older_copies(tmp_path, capture):
    kit = tmp_path / "selected.foxkit"
    export_kit(capture.store, capture.identity, capture.trust, kit)
    content = kit.read_bytes()
    owner = LocalOwner(tmp_path / "home")

    def import_kit() -> dict:
        upload = owner.control("upload-create", {"size_bytes": len(content)})
        owner.upload(upload["upload_id"], 0, content)
        plan = owner.control(
            "plan", {"kind": "kit_import", "upload_id": upload["upload_id"]}
        )
        job = finish(owner, submit(owner, plan))
        assert job["state"] == "succeeded"
        return job

    try:
        before = import_kit()
        assert (owner.home / "copies" / before["copy_id"]).is_dir()
        earlier_kit = finish(
            owner,
            submit(
                owner,
                owner.control(
                    "plan", {"kind": "kit_export", "copy_id": before["copy_id"]}
                ),
            ),
        )
        drive = tmp_path / "drive"
        drive.mkdir()
        review = owner.control("library-review", {"path": str(drive / "Library")})
        library = owner.control(
            "library-setup", {"review_id": review["review_id"], "confirmed": True}
        )
        folder = drive / "Library"
        assert library["path"] == str(folder)
        assert library["elsewhere_copies"] == 1
        after = import_kit()
        assert (folder / "copies" / after["copy_id"]).is_dir()
        assert not (owner.home / "copies" / after["copy_id"]).exists()
        owner.control("library-move", {})
        library = wait_moved(owner)
        assert library["move"]["state"] == "done", library
        assert library["elsewhere_copies"] == 0
        moved = folder / "copies" / before["copy_id"]
        assert moved.is_dir()
        assert not (owner.home / "copies" / before["copy_id"]).exists()
        # The moved copy keeps working: exporting it re-verifies every byte.
        exported = finish(
            owner,
            submit(
                owner,
                owner.control(
                    "plan", {"kind": "kit_export", "copy_id": before["copy_id"]}
                ),
            ),
        )
        assert exported["state"] == "succeeded"
        assert str(exported["output"]).startswith(str(folder))
        # A kit exported before the change still opens from where it was written.
        with owner.open_kit(earlier_kit["job_id"]) as stream:
            assert stream.read(4)
    finally:
        owner.close()


def test_unplugged_library_drive_refuses_saving_but_still_reads(tmp_path, capture):
    owner = LocalOwner(tmp_path / "home")
    try:
        drive = tmp_path / "drive"
        drive.mkdir()
        review = owner.control("library-review", {"path": str(drive / "Library")})
        owner.control(
            "library-setup", {"review_id": review["review_id"], "confirmed": True}
        )
        os.rename(drive, tmp_path / "unplugged")
        library = owner.read()["library"]
        assert library["available"] is False
        with pytest.raises(PreservationError, match="library_storage_unavailable"):
            owner.control("upload-create", {"size_bytes": 10})
    finally:
        owner.close()


def test_community_pool_is_created_on_the_library_drive_and_moves_there(tmp_path):
    from foxlight_model_preservation.local_host.community import CommunityMirror
    from foxlight_model_preservation.local_host.sharing import Sharing

    owner = LocalOwner(tmp_path / "home")
    try:
        # An existing pool (served objects and its ledger) on the system drive.
        pool = owner.home / "sharing/community/mirror"
        data = b"shared weights" * 100
        (pool / "store/objects/sha256").mkdir(parents=True)
        (pool / "store/objects/sha256" / digest(data)).write_bytes(data)
        (pool / "state.json").write_bytes(b"{}")
        drive = tmp_path / "drive"
        drive.mkdir()
        review = owner.control("library-review", {"path": str(drive / "Library")})
        owner.control(
            "library-setup", {"review_id": review["review_id"], "confirmed": True}
        )
        owner.control("library-move", {})
        library = wait_moved(owner)
        assert library["move"]["state"] == "done", library
        moved = drive / "Library/sharing/community/mirror"
        assert (moved / "store/objects/sha256" / digest(data)).read_bytes() == data
        assert not pool.exists()
        # The key and settings stay in the home.
        assert (owner.home / "sharing/community").is_dir()
    finally:
        owner.close()
    # A first pool goes straight to the library folder.
    home = tmp_path / "fresh"
    home.mkdir(mode=0o700)
    library = Library(home)
    choose(library, tmp_path / "drive2")
    community = CommunityMirror(home, Sharing(home, library))
    assert community.ledger().path == tmp_path / "drive2/sharing/community/mirror"


def test_moved_mirrors_reopen_and_share_objects_with_moved_copies(tmp_path, capture):
    from foxlight_model_preservation.mirror_state import MirrorLedger

    kit = tmp_path / "selected.foxkit"
    export_kit(capture.store, capture.identity, capture.trust, kit)
    content = kit.read_bytes()
    owner = LocalOwner(tmp_path / "home")
    try:
        upload = owner.control("upload-create", {"size_bytes": len(content)})
        owner.upload(upload["upload_id"], 0, content)
        plan = owner.control(
            "plan", {"kind": "kit_import", "upload_id": upload["upload_id"]}
        )
        job = finish(owner, submit(owner, plan))
        copy = owner.home / "copies" / job["copy_id"]
        shared = next(p for p in (copy / "objects/sha256").iterdir())
        # A published copy's store holding the same object, and its ledger.
        public = owner.home / "sharing/mirrors" / ("e" * 64)
        public.parent.mkdir(parents=True, mode=0o700)
        MirrorLedger.initialize(
            public,
            name="test",
            disk_bytes=10**9,
            ingress_daily=1,
            egress_daily=10**6,
            egress_monthly=10**7,
            bind="127.0.0.1",
            port=0,
        )
        (public / "store/objects/sha256").mkdir(parents=True)
        os.link(shared, public / "store/objects/sha256" / shared.name)
        # An upload started before the change stays usable afterwards.
        pending = owner.control("upload-create", {"size_bytes": len(content)})
        drive = tmp_path / "drive"
        drive.mkdir()
        review = owner.control("library-review", {"path": str(drive / "Library")})
        owner.control(
            "library-setup", {"review_id": review["review_id"], "confirmed": True}
        )
        assert pending["upload_id"] in {
            item["upload_id"] for item in owner.read()["uploads"]
        }
        owner.upload(pending["upload_id"], 0, content)
        owner.control("library-move", {})
        assert wait_moved(owner)["move"]["state"] == "done"
        folder = drive / "Library"
        moved_public = folder / "sharing/mirrors" / ("e" * 64)
        assert MirrorLedger(moved_public).read().name == "test"  # re-enrolled
        assert (
            os.stat(moved_public / "store/objects/sha256" / shared.name).st_ino
            == os.stat(
                folder / "copies" / job["copy_id"] / "objects/sha256" / shared.name
            ).st_ino
        )
        # The earlier folder (home) held the pending upload; it is still found.
        plan = owner.control(
            "plan", {"kind": "kit_import", "upload_id": pending["upload_id"]}
        )
        assert plan["kind"] == "kit_import"
    finally:
        owner.close()


def test_earlier_folders_are_kept_while_kits_or_restores_remain(tmp_path):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    library = Library(home)
    first = tmp_path / "first"
    choose(library, first)
    (first / "kits").mkdir()
    (first / "kits/old.foxkit").write_bytes(b"kit")
    choose(library, tmp_path / "second")
    assert str(first) in library.view()["previous"]
    assert library.find("kits/old.foxkit") == first / "kits/old.foxkit"


def test_move_copies_when_hard_links_are_unavailable(tmp_path, monkeypatch):
    data = b"weights" * 100
    name = digest(data)
    source = tmp_path / "source"
    (source / "store/objects/sha256").mkdir(parents=True)
    (source / "store/objects/sha256" / name).write_bytes(data)
    links = {name: tmp_path / "elsewhere"}
    (tmp_path / "elsewhere").write_bytes(data)

    def refuse(*_args, **_kwargs):
        raise OSError(45, "Operation not supported")

    monkeypatch.setattr(os, "link", refuse)
    move_tree(source, tmp_path / "moved", links, OperationControl(), lambda _: None)
    assert (tmp_path / "moved/store/objects/sha256" / name).read_bytes() == data


def test_mirror_stores_stay_home_on_drives_without_owner_only_permissions(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    library = Library(home)
    choose(library, tmp_path / "exfat")
    settings = library.settings()
    assert settings.current is not None
    library._write(  # what an exFAT or SMB volume reports
        settings.model_copy(
            update={"current": settings.current.model_copy(update={"private": False})}
        )
    )
    assert library.root() == tmp_path / "exfat"
    assert library.private_root() == home


def test_too_many_earlier_folders_is_refused_not_forgotten(tmp_path):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    library = Library(home)
    for index in range(9):
        choose(library, tmp_path / f"drive{index}")
    assert len(library.settings().previous) == 8
    with pytest.raises(PreservationError, match="library_location_limit"):
        choose(library, tmp_path / "drive9")
    assert library.root() == tmp_path / "drive8"  # unchanged
    assert str(tmp_path / "drive0") in library.view()["previous"]


def test_publication_scratch_check_survives_an_unplugged_drive(tmp_path):
    from foxlight_model_preservation.local_host.sharing import Sharing

    home, drive = tmp_path / "home", tmp_path / "drive"
    home.mkdir(mode=0o700)
    drive.mkdir()
    library = Library(home)
    choose(library, drive / "Library")
    os.rename(drive, tmp_path / "unplugged")
    assert Sharing(home, library).work_exists("a" * 64) is False


def test_pool_is_copied_only_after_serving_has_drained(tmp_path, monkeypatch):
    import threading

    from foxlight_model_preservation.local_host import owner as owner_module

    owner = LocalOwner(tmp_path / "home")
    events: list[str] = []
    release = threading.Event()

    def serving() -> None:
        # Stands in for a community worker still draining its HTTP handlers.
        release.wait(5)
        time.sleep(0.3)
        events.append("drained")
        owner._community_parked.set()
        release.wait(5)

    worker = threading.Thread(target=serving, daemon=True)
    try:
        pool = owner.home / "sharing/community/mirror"
        (pool / "store").mkdir(parents=True)
        (pool / "state").write_bytes(b"ledger")
        original = owner_module.move_tree

        def recording(*args, **kwargs):
            events.append("copy")
            return original(*args, **kwargs)

        monkeypatch.setattr(owner_module, "move_tree", recording)
        owner._community_thread = worker
        worker.start()
        drive = tmp_path / "drive"
        drive.mkdir()
        review = owner.control("library-review", {"path": str(drive / "Library")})
        owner.control(
            "library-setup", {"review_id": review["review_id"], "confirmed": True}
        )
        owner.control("library-move", {})
        release.set()
        assert wait_moved(owner)["move"]["state"] == "done"
        assert events == ["drained", "copy"]
        assert owner._community_hold is False
    finally:
        release.set()
        owner._community_thread = None
        owner.close()


def test_home_is_searched_first_once_it_is_current_again(tmp_path):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    library = Library(home)
    drive = tmp_path / "drive"
    choose(library, drive)
    (drive / "copies/same").mkdir(parents=True)
    library.use_home()
    (home / "copies/same").mkdir(parents=True)
    assert library.roots()[0] == home
    assert library.find("copies/same") == home / "copies/same"


def test_an_unreadable_source_folder_stops_the_move(tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "store/objects/sha256").mkdir(parents=True)
    (source / "store/objects/sha256" / ("a" * 64)).write_bytes(b"x")
    real = os.scandir

    def flaky(path="."):
        if str(path).endswith("sha256"):
            raise OSError(5, "Input/output error")
        return real(path)

    monkeypatch.setattr(os, "scandir", flaky)
    with pytest.raises(OSError):
        move_tree(source, tmp_path / "moved", {}, OperationControl(), lambda _: None)
