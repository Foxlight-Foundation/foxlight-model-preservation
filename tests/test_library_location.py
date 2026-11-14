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
