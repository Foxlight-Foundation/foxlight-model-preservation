# ruff: noqa: E402 - the platform assert comes before the imports
"""POSIX side of :mod:`safefs`: the calls the stores made before it existed."""

# basedpyright analyzes this module only for POSIX systems.
import sys

assert sys.platform != "win32"  # noqa: S101 - imported only on POSIX

import errno
import fcntl
import os
import shutil
from pathlib import Path

__all__ = [
    "change_time",
    "identity",
    "link_at",
    "link_path",
    "list_dir",
    "lock",
    "make_dir",
    "mkdir_at",
    "open_at",
    "open_dir",
    "open_dir_at",
    "open_path",
    "owned",
    "path_owned",
    "path_private",
    "private",
    "readlink_at",
    "TREE_REMOVAL_SAFE",
    "remove_tree",
    "remove_tree_at",
    "rename_at",
    "replace_at",
    "rmdir_at",
    "free_bytes",
    "stat_at",
    "sync_at",
    "unlink_at",
    "unlock",
]

PRIVATE_DIRECTORY = 0o700


def _one(name: str) -> str:
    """One plain component, as on Windows: never a path or the folder itself."""
    if not name or name in {".", ".."} or "/" in name or "\0" in name:
        raise OSError(errno.EINVAL, "Invalid name", name)
    return name


PRIVATE_FILE = 0o600


def open_dir(path: Path | str, *, follow: bool = False) -> int:
    """Pin the directory at ``path``; a final symlink is refused unless ``follow``."""
    flags = os.O_RDONLY | os.O_DIRECTORY
    if not follow:
        flags |= os.O_NOFOLLOW
    return os.open(path, flags)


def open_dir_at(parent: int, name: str) -> int:
    """Pin the directory ``name`` inside ``parent``, never through a symlink."""
    return os.open(
        _one(name), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
    )


def make_dir(
    path: Path | str, *, parents: bool = False, exist_ok: bool = False
) -> None:
    """Create the private folder ``path`` (and missing parents with ``parents``)."""
    Path(path).mkdir(parents=parents, exist_ok=exist_ok, mode=PRIVATE_DIRECTORY)


def mkdir_at(parent: int, name: str, mode: int = PRIVATE_DIRECTORY) -> None:
    """Create the folder ``name`` inside ``parent`` (private unless ``mode`` says)."""
    os.mkdir(_one(name), mode=mode, dir_fd=parent)


def list_dir(handle: int) -> list[str]:
    """The names in a pinned directory, in no particular order."""
    return os.listdir(handle)


def open_at(parent: int, name: str, flags: int, mode: int = PRIVATE_FILE) -> int:
    """Open ``name`` inside ``parent`` with ``os.O_*`` flags, never via a symlink."""
    return os.open(_one(name), flags | os.O_NOFOLLOW, mode, dir_fd=parent)


def open_path(path: Path | str, flags: int, mode: int = PRIVATE_FILE) -> int:
    """Open ``path`` with ``os.O_*`` ``flags``; a final symlink is refused."""
    return os.open(path, flags | os.O_NOFOLLOW, mode)


def sync_at(parent: int, name: str) -> None:
    """Make the data of the file ``name`` inside ``parent`` durable."""
    descriptor = os.open(_one(name), os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def free_bytes(where: int | Path | str) -> int:
    """Free space on the drive holding a pinned folder (handle) or a path."""
    return shutil.disk_usage(where).free


def stat_at(parent: int, name: str, *, follow: bool = False) -> os.stat_result:
    """Stat ``name`` inside ``parent``; a symlink is described, not followed."""
    return os.stat(_one(name), dir_fd=parent, follow_symlinks=follow)


def unlink_at(parent: int, name: str) -> None:
    """Remove the file (or symlink) ``name`` from ``parent``."""
    os.unlink(_one(name), dir_fd=parent)


def rmdir_at(parent: int, name: str) -> None:
    """Remove the empty folder ``name`` from ``parent``."""
    os.rmdir(_one(name), dir_fd=parent)


def rename_at(source_parent: int, source: str, target_parent: int, target: str) -> None:
    """Rename between pinned directories (``os.rename`` semantics)."""
    os.rename(
        _one(source), _one(target), src_dir_fd=source_parent, dst_dir_fd=target_parent
    )


def replace_at(
    source_parent: int, source: str, target_parent: int, target: str
) -> None:
    """Rename between pinned directories, atomically replacing ``target``."""
    os.replace(
        _one(source), _one(target), src_dir_fd=source_parent, dst_dir_fd=target_parent
    )


def link_at(source_parent: int, source: str, target_parent: int, target: str) -> None:
    """Hard-link ``source`` as ``target``; a symlink is linked, never followed."""
    os.link(
        _one(source),
        _one(target),
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
    shutil.rmtree(_one(name), dir_fd=parent, ignore_errors=ignore_errors)


# Whether remove_tree can't be steered out of the tree by a link (fd-based
# rmtree); systems without it refuse removals that need it.
TREE_REMOVAL_SAFE: bool = shutil.rmtree.avoids_symlink_attacks


def remove_tree(path: Path | str) -> None:
    """Remove the folder ``path`` and everything in it, never following a link."""
    shutil.rmtree(path)


def readlink_at(parent: int, name: str) -> str:
    """The target of the symlink ``name`` inside ``parent``."""
    return os.readlink(_one(name), dir_fd=parent)


def lock(handle: int, *, wait: bool = False) -> None:
    """Take the exclusive lock on an open file.

    Without ``wait``, raises ``BlockingIOError`` when another holder has it.
    The operating system releases the lock if this process dies.
    """
    fcntl.flock(handle, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)


def unlock(handle: int) -> None:
    """Release the lock from :func:`lock`."""
    fcntl.flock(handle, fcntl.LOCK_UN)


def owned(handle: int) -> bool:
    """Whether this user owns the open file or directory."""
    return os.fstat(handle).st_uid == os.getuid()


def path_owned(path: Path | str, *, follow: bool = True) -> bool:
    """Whether this user owns ``path`` (a final symlink is followed by default)."""
    return os.stat(path, follow_symlinks=follow).st_uid == os.getuid()


def path_private(path: Path | str) -> bool:
    """Whether nobody but the owner can reach ``path`` (a final link is followed)."""
    return not os.stat(path).st_mode & 0o077


def private(handle: int) -> bool:
    """Whether this user owns the open file or directory and nobody else has access."""
    info = os.fstat(handle)
    return info.st_uid == os.getuid() and not info.st_mode & 0o077


def change_time(handle: int) -> int:
    """When the open file's data or metadata last changed (``st_ctime``).

    Compared only with itself: a different value means something changed.
    """
    return os.fstat(handle).st_ctime_ns


def identity(info: os.stat_result) -> tuple[int, int]:
    """The (device, file) pair that names one file or folder while it exists."""
    return (info.st_dev, info.st_ino)
