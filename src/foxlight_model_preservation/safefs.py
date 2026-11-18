"""The filesystem calls the stores' safety rests on, in one place.

Every store is reached through a pinned directory handle, and every path
inside it is opened one component at a time relative to such a handle without
following links. Publication is a synced write, a rename in the same directory
and a sync of that directory. Writers are fenced by locks the operating system
drops when a process dies, and private folders are readable by their owner
alone.

On macOS and Linux these are the POSIX calls (``openat`` with ``O_NOFOLLOW``,
``renameat``, ``flock``, owner and mode bits) made exactly as before this module
existed. Windows gets the same guarantees from its own handle-relative calls
(design: foxlight-docs windows-storage-2026-11-17); callers never branch on the
platform.

Every handle here is an ordinary descriptor on both platforms, directories
included, so ``os.close``, ``os.dup``, ``os.fstat``, ``os.fsync`` and
``os.fdopen`` keep working on them. What needs this module is anything that
names a path relative to a pinned directory, lists one, locks, or asks who
may read a file.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

PRIVATE_DIRECTORY = 0o700
PRIVATE_FILE = 0o600
# Opening never blocks on a FIFO or device; Windows has neither in a store.
NONBLOCK: int = getattr(os, "O_NONBLOCK", 0)


# Directories ------------------------------------------------------------------


def open_dir(path: Path | str, *, follow: bool = False) -> int:
    """Pin the directory at ``path``; a final symlink is refused unless ``follow``."""
    flags = os.O_RDONLY | os.O_DIRECTORY
    if not follow:
        flags |= os.O_NOFOLLOW
    return os.open(path, flags)


def open_dir_at(parent: int, name: str) -> int:
    """Pin the directory ``name`` inside ``parent``, never through a symlink."""
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)


def make_dir(
    path: Path | str, *, parents: bool = False, exist_ok: bool = False
) -> None:
    """Create the private folder ``path`` (and missing parents with ``parents``)."""
    Path(path).mkdir(parents=parents, exist_ok=exist_ok, mode=PRIVATE_DIRECTORY)


def mkdir_at(parent: int, name: str, mode: int = PRIVATE_DIRECTORY) -> None:
    """Create the folder ``name`` inside ``parent`` (private unless ``mode`` says)."""
    os.mkdir(name, mode=mode, dir_fd=parent)


def list_dir(handle: int) -> list[str]:
    """The names in a pinned directory, in no particular order."""
    return os.listdir(handle)


# Files ------------------------------------------------------------------------


def open_at(parent: int, name: str, flags: int, mode: int = PRIVATE_FILE) -> int:
    """Open ``name`` inside ``parent`` with ``os.O_*`` flags, never via a symlink."""
    return os.open(name, flags | os.O_NOFOLLOW, mode, dir_fd=parent)


def open_path(path: Path | str, flags: int, mode: int = PRIVATE_FILE) -> int:
    """Open ``path`` with ``os.O_*`` ``flags``; a final symlink is refused."""
    return os.open(path, flags | os.O_NOFOLLOW, mode)


def stat_at(parent: int, name: str, *, follow: bool = False) -> os.stat_result:
    """Stat ``name`` inside ``parent``; a symlink is described, not followed."""
    return os.stat(name, dir_fd=parent, follow_symlinks=follow)


def unlink_at(parent: int, name: str) -> None:
    """Remove the file (or symlink) ``name`` from ``parent``."""
    os.unlink(name, dir_fd=parent)


def rmdir_at(parent: int, name: str) -> None:
    """Remove the empty folder ``name`` from ``parent``."""
    os.rmdir(name, dir_fd=parent)


def rename_at(source_parent: int, source: str, target_parent: int, target: str) -> None:
    """Rename between pinned directories (``os.rename`` semantics)."""
    os.rename(source, target, src_dir_fd=source_parent, dst_dir_fd=target_parent)


def replace_at(
    source_parent: int, source: str, target_parent: int, target: str
) -> None:
    """Rename between pinned directories, atomically replacing ``target``."""
    os.replace(source, target, src_dir_fd=source_parent, dst_dir_fd=target_parent)


def link_at(source_parent: int, source: str, target_parent: int, target: str) -> None:
    """Hard-link ``source`` as ``target``; a symlink is linked, never followed."""
    os.link(
        source,
        target,
        src_dir_fd=source_parent,
        dst_dir_fd=target_parent,
        follow_symlinks=False,
    )


def link_path(source: Path | str, target: Path | str) -> None:
    """Hard-link ``source`` as ``target`` by path; a symlink is linked, not followed."""
    os.link(source, target, follow_symlinks=False)


def remove_tree_at(parent: int, name: str, *, ignore_errors: bool = False) -> None:
    """Remove the folder ``name`` inside ``parent`` and everything in it.

    Links inside are removed, never followed.
    """
    shutil.rmtree(name, dir_fd=parent, ignore_errors=ignore_errors)


def readlink_at(parent: int, name: str) -> str:
    """The target of the symlink ``name`` inside ``parent``."""
    return os.readlink(name, dir_fd=parent)


# Locks ------------------------------------------------------------------------


def lock(handle: int, *, wait: bool = False) -> None:
    """Take the exclusive lock on an open file.

    Without ``wait``, raises ``BlockingIOError`` when another holder has it.
    The operating system releases the lock if this process dies.
    """
    import fcntl  # noqa: PLC0415 - POSIX only

    fcntl.flock(handle, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)


def unlock(handle: int) -> None:
    """Release the lock from :func:`lock`."""
    import fcntl  # noqa: PLC0415 - POSIX only

    fcntl.flock(handle, fcntl.LOCK_UN)


# Ownership and identity -------------------------------------------------------


def owned(handle: int) -> bool:
    """Whether this user owns the open file or directory."""
    return os.fstat(handle).st_uid == os.getuid()


def path_owned(path: Path | str, *, follow: bool = True) -> bool:
    """Whether this user owns ``path`` (a final symlink is followed by default)."""
    return os.stat(path, follow_symlinks=follow).st_uid == os.getuid()


def private(handle: int) -> bool:
    """Whether this user owns the open file or directory and nobody else has access."""
    info = os.fstat(handle)
    return info.st_uid == os.getuid() and not info.st_mode & 0o077


def identity(info: os.stat_result) -> tuple[int, int]:
    """The (device, file) pair that names one file or folder while it exists."""
    return (info.st_dev, info.st_ino)
