"""Windows-only threats from the storage design (foxlight-docs#768)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from foxlight_model_preservation import safefs

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

READER = """
import sys, time
with open(sys.argv[1], "rb") as stream:  # no delete sharing, like most programs
    print("OPEN", flush=True)
    time.sleep(float(sys.argv[2]))
"""


def _junction(link: Path, target: Path) -> None:
    subprocess.run(  # noqa: S603 - fixed system tool, test paths
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],  # noqa: S607
        check=True,
        capture_output=True,
    )


@pytest.fixture
def store(tmp_path: Path) -> Path:
    root = tmp_path / "store"
    safefs.make_dir(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"outside")
    _junction(root / "junction", outside)
    (root / "model-weights-long-name.safetensors").write_bytes(b"weights")
    return root


def test_a_junction_is_never_entered(store: Path) -> None:
    root = safefs.open_dir(store)
    try:
        with pytest.raises(OSError):
            os.close(safefs.open_dir_at(root, "junction"))
        with pytest.raises(OSError):
            os.close(safefs.open_dir(store / "junction"))
        import stat

        assert stat.S_ISLNK(safefs.stat_at(root, "junction").st_mode)
        # Removing the store removes the junction, never what it points to.
        safefs.unlink_at(root, "junction")
    finally:
        os.close(root)
    assert (store.parent / "outside" / "secret").read_bytes() == b"outside"


def test_names_are_one_plain_component(store: Path) -> None:
    root = safefs.open_dir(store)
    try:
        for name in (
            "junction\\secret",  # would walk through the junction
            "junction/secret",
            "model:hidden",  # an alternate data stream
            "..",
            "trailing.",
            "trailing ",
        ):
            with pytest.raises(OSError):
                os.close(safefs.open_at(root, name, os.O_RDONLY))
    finally:
        os.close(root)
    assert sorted(os.listdir(store)) == [
        "junction",
        "model-weights-long-name.safetensors",
    ]


def test_case_and_short_name_aliases_find_nothing(store: Path) -> None:
    root = safefs.open_dir(store)
    try:
        aliases = ["MODEL-WEIGHTS-LONG-NAME.SAFETENSORS"]
        listing = subprocess.run(  # noqa: S603
            ["cmd", "/c", "dir", "/x", str(store)],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        aliases += [
            line.split()[-2]
            for line in listing.splitlines()
            if "model-weights-long-name" in line and "~" in line
        ]
        for alias in aliases:
            with pytest.raises(FileNotFoundError):
                os.close(safefs.open_at(root, alias, os.O_RDONLY))
            with pytest.raises(FileNotFoundError):
                safefs.stat_at(root, alias)
        fd = safefs.open_at(root, "model-weights-long-name.safetensors", os.O_RDONLY)
        with os.fdopen(fd, "rb") as stream:
            assert stream.read() == b"weights"
    finally:
        os.close(root)


def test_inherited_access_is_not_private(tmp_path: Path) -> None:
    ordinary = tmp_path / "ordinary"
    ordinary.mkdir()  # inherits the temp folder's access
    fd = safefs.open_dir(ordinary)
    try:
        assert not safefs.private(fd)
    finally:
        os.close(fd)
    safefs.make_dir(tmp_path / "private")
    fd = safefs.open_dir(tmp_path / "private")
    try:
        assert safefs.private(fd)
        assert safefs.owned(fd)
        # Files and folders made inside inherit only the user and SYSTEM.
        safefs.mkdir_at(fd, "child")
        child = safefs.open_dir_at(fd, "child")
        try:
            assert safefs.owned(child)
        finally:
            os.close(child)
    finally:
        os.close(fd)


def test_another_programs_reader_delays_a_replace_briefly(tmp_path: Path) -> None:
    folder = tmp_path / "objects"
    safefs.make_dir(folder)
    (folder / "file").write_bytes(b"old")
    reader = subprocess.Popen(  # noqa: S603 - this interpreter, fixed script
        [sys.executable, "-c", READER, str(folder / "file"), "0.5"],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert reader.stdout is not None
    assert reader.stdout.readline().strip() == "OPEN"
    fd = safefs.open_dir(folder)
    try:
        new = safefs.open_at(fd, "pending", os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(new, "wb") as stream:
            stream.write(b"new")
        # The reader didn't allow deletion; the replace waits it out.
        safefs.replace_at(fd, "pending", fd, "file")
    finally:
        os.close(fd)
        reader.wait()
    assert (folder / "file").read_bytes() == b"new"


def test_a_folder_renamed_over_an_empty_folder_takes_its_place(tmp_path: Path) -> None:
    safefs.make_dir(tmp_path / "pending")
    (tmp_path / "pending" / "data").write_bytes(b"x")
    safefs.make_dir(tmp_path / "reserved")
    fd = safefs.open_dir(tmp_path)
    try:
        safefs.rename_at(fd, "pending", fd, "reserved")
        assert (tmp_path / "reserved" / "data").read_bytes() == b"x"
        safefs.make_dir(tmp_path / "full")
        (tmp_path / "full" / "keep").write_bytes(b"k")
        safefs.make_dir(tmp_path / "other")
        with pytest.raises(OSError):
            safefs.rename_at(fd, "other", fd, "full")
        assert (tmp_path / "full" / "keep").exists()
    finally:
        os.close(fd)


def test_written_bytes_are_exact(tmp_path: Path) -> None:
    fd = safefs.open_dir(safefs.make_dir(tmp_path / "s") or tmp_path / "s")
    try:
        out = safefs.open_at(fd, "f", os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        os.write(out, b"a\nb\r\n\x1a")
        os.close(out)
        with os.fdopen(safefs.open_at(fd, "f", os.O_RDONLY), "rb") as stream:
            assert stream.read() == b"a\nb\r\n\x1a"
        appended = safefs.open_at(fd, "f", os.O_WRONLY | os.O_APPEND)
        os.write(appended, b"z")
        os.close(appended)
        assert (tmp_path / "s" / "f").read_bytes() == b"a\nb\r\n\x1az"
        os.fsync(fd)  # a folder's entries can be flushed
    finally:
        os.close(fd)


def test_a_delete_waits_out_another_programs_brief_hold(tmp_path: Path) -> None:
    folder = tmp_path / "objects"
    safefs.make_dir(folder)
    (folder / "file").write_bytes(b"old")
    reader = subprocess.Popen(  # noqa: S603 - this interpreter, fixed script
        [sys.executable, "-c", READER, str(folder / "file"), "0.5"],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert reader.stdout is not None
    assert reader.stdout.readline().strip() == "OPEN"
    fd = safefs.open_dir(folder)
    try:
        # Opening the file to delete it is refused while the reader holds it.
        safefs.unlink_at(fd, "file")
    finally:
        os.close(fd)
        reader.wait()
    assert not (folder / "file").exists()
