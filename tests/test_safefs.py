"""The guarantees the stores rest on, as one contract every platform must pass."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from foxlight_model_preservation import safefs

LOCKER = """
import os, sys, time
from foxlight_model_preservation import safefs
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
try:
    safefs.lock(fd)
except BlockingIOError:
    print("BUSY", flush=True)
    raise SystemExit
print("LOCKED", flush=True)
if sys.argv[2] == "hold":
    time.sleep(60)
"""


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "store"
    safefs.make_dir(root / "objects", parents=True)
    outside = tmp_path / "outside"
    safefs.make_dir(outside)
    (outside / "secret").write_bytes(b"outside")
    (root / "objects" / "file").write_bytes(b"inside")
    (root / "link").symlink_to(outside / "secret")
    (root / "dirlink").symlink_to(outside, target_is_directory=True)
    return root


def test_opens_never_follow_a_link(tree: Path) -> None:
    root = safefs.open_dir(tree)
    try:
        with pytest.raises(OSError):
            os.close(safefs.open_at(root, "link", os.O_RDONLY))
        with pytest.raises(OSError):
            os.close(safefs.open_dir_at(root, "dirlink"))
        with pytest.raises(OSError):
            os.close(safefs.open_dir(tree / "dirlink"))
        with pytest.raises(OSError):
            os.close(safefs.open_path(tree / "link", os.O_RDONLY))
        objects = safefs.open_dir_at(root, "objects")
        try:
            fd = safefs.open_at(objects, "file", os.O_RDONLY)
            with os.fdopen(fd, "rb") as stream:
                assert stream.read() == b"inside"
        finally:
            os.close(objects)
    finally:
        os.close(root)


def test_stat_at_describes_a_link_rather_than_its_target(tree: Path) -> None:
    import stat as stat_module

    root = safefs.open_dir(tree)
    try:
        assert stat_module.S_ISLNK(safefs.stat_at(root, "link").st_mode)
        assert stat_module.S_ISREG(safefs.stat_at(root, "link", follow=True).st_mode)
    finally:
        os.close(root)


def test_publication_replaces_a_file_a_reader_holds_open(tree: Path) -> None:
    root = safefs.open_dir(tree)
    try:
        objects = safefs.open_dir_at(root, "objects")
        try:
            reader = os.fdopen(safefs.open_at(objects, "file", os.O_RDONLY), "rb")
            fd = safefs.open_at(
                objects, "pending", os.O_WRONLY | os.O_CREAT | os.O_EXCL
            )
            with os.fdopen(fd, "wb") as stream:
                stream.write(b"new")
                stream.flush()
                os.fsync(stream.fileno())
            safefs.replace_at(objects, "pending", objects, "file")
            os.fsync(objects)
            with reader:
                assert reader.read() == b"inside"  # the reader keeps the old bytes
            assert sorted(safefs.list_dir(objects)) == ["file"]
            assert (tree / "objects" / "file").read_bytes() == b"new"
        finally:
            os.close(objects)
    finally:
        os.close(root)


def test_names_change_only_inside_pinned_directories(tree: Path) -> None:
    root = safefs.open_dir(tree)
    try:
        objects = safefs.open_dir_at(root, "objects")
        try:
            safefs.link_at(objects, "file", root, "copy")
            assert os.stat(tree / "copy").st_nlink == 2
            safefs.rename_at(root, "copy", objects, "moved")
            safefs.unlink_at(objects, "moved")
            safefs.mkdir_at(objects, "empty")
            assert safefs.private(fd := safefs.open_dir_at(objects, "empty"))
            os.close(fd)
            safefs.rmdir_at(objects, "empty")
            assert sorted(safefs.list_dir(objects)) == ["file"]
        finally:
            os.close(objects)
    finally:
        os.close(root)


def test_a_linked_link_stays_a_link(tree: Path) -> None:
    root = safefs.open_dir(tree)
    try:
        safefs.link_at(root, "link", root, "link2")
        assert os.path.islink(tree / "link2")
        assert safefs.readlink_at(root, "link2") == safefs.readlink_at(root, "link")
    finally:
        os.close(root)


def test_removing_a_tree_never_follows_links_out_of_it(tree: Path) -> None:
    (tree / "objects" / "inner").symlink_to(tree.parent / "outside")
    root = safefs.open_dir(tree)
    try:
        safefs.remove_tree_at(root, "objects")
        assert "objects" not in safefs.list_dir(root)
    finally:
        os.close(root)
    assert (tree.parent / "outside" / "secret").read_bytes() == b"outside"


def test_new_folders_are_private_and_owned(tmp_path: Path) -> None:
    safefs.make_dir(tmp_path / "a" / "b", parents=True)
    safefs.make_dir(tmp_path / "a" / "b", parents=True, exist_ok=True)
    fd = safefs.open_dir(tmp_path / "a" / "b")
    try:
        assert safefs.owned(fd)
        assert safefs.private(fd)
    finally:
        os.close(fd)
    assert safefs.path_owned(tmp_path / "a")
    with pytest.raises(FileExistsError):
        safefs.make_dir(tmp_path / "a")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_a_folder_others_can_read_is_not_private(tmp_path: Path) -> None:
    folder = tmp_path / "shared"
    folder.mkdir(mode=0o755)
    os.chmod(folder, 0o755)  # noqa: S103 - the case under test
    fd = safefs.open_dir(folder)
    try:
        assert safefs.owned(fd)
        assert not safefs.private(fd)
    finally:
        os.close(fd)


def test_the_writer_lock_is_exclusive_and_dies_with_its_holder(tmp_path: Path) -> None:
    lock = str(tmp_path / ".write.lock")
    holder = subprocess.Popen(  # noqa: S603 - this interpreter, fixed script
        [sys.executable, "-c", LOCKER, lock, "hold"], stdout=subprocess.PIPE, text=True
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "LOCKED"
        second = subprocess.run(  # noqa: S603 - this interpreter, fixed script
            [sys.executable, "-c", LOCKER, lock, "try"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert second.stdout.strip() == "BUSY"
        fd = os.open(lock, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                safefs.lock(fd)
        finally:
            os.close(fd)
    finally:
        holder.kill()
        holder.wait()
    after = subprocess.run(  # noqa: S603 - this interpreter, fixed script
        [sys.executable, "-c", LOCKER, lock, "try"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert after.stdout.strip() == "LOCKED"


def test_a_waiting_lock_is_taken_once_free(tmp_path: Path) -> None:
    fd = os.open(tmp_path / "lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        safefs.lock(fd, wait=True)
        safefs.unlock(fd)
        safefs.lock(fd)
        safefs.unlock(fd)
    finally:
        os.close(fd)


def test_identity_names_one_folder(tmp_path: Path) -> None:
    safefs.make_dir(tmp_path / "a")
    safefs.make_dir(tmp_path / "b")
    a = safefs.identity(os.stat(tmp_path / "a"))
    assert a == safefs.identity(os.stat(tmp_path / "a"))
    assert a != safefs.identity(os.stat(tmp_path / "b"))


def test_only_the_seam_makes_these_calls() -> None:
    """New code must go through safefs, or Windows silently loses a guarantee."""
    import re

    package = Path(safefs.__file__).parent
    forbidden = re.compile(
        r"dir_fd=|O_NOFOLLOW|O_DIRECTORY|O_NONBLOCK|\bfcntl\b|getuid\(|"
        r"os\.listdir\((?:fd|root|parent|descriptor)\)"
    )
    offenders = [
        f"{path.relative_to(package)}:{number}"
        for path in package.rglob("*.py")
        if path.name != "safefs.py"
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if forbidden.search(line)
    ]
    assert offenders == []
