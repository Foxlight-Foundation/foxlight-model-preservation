"""Where Sovereign keeps model copies: any connected drive, NAS or local folder.

The owner's home on the system drive keeps keys, settings and the journal. Model
copies, downloads in progress, uploads, kits, restored folders and published
copies' stores live in the *library folder*, which defaults to the home and can
be moved to any mounted folder the person chooses.

A chosen library folder is one dedicated folder Sovereign creates (or an empty
one the person picked). A marker inside it names the Sovereign that owns it, so
a second computer pointed at the same NAS folder is refused rather than trusting
network file locks. A missing drive is never recreated and never silently
replaced by the system drive: saving stops with ``library_storage_unavailable``
until the drive is connected again.

Copies saved before a move stay where they were and keep working: a copy is
looked up in the current library folder first, then in earlier ones. Moving
them is a separate, verified step (see the owner's library move).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import Field, JsonValue

from ..cards import identity_json
from ..models import Digest, PreservationError, Record
from ..operations import OperationControl
from ..signing import digest, encode_record, strict_json
from ..storage import CHUNK_SIZE, atomic_bytes, directory, read_metadata

MARKER = ".sovereign-library.json"
FOLDER = "Sovereign Library"
SETTINGS = "library-location.json"
OWNER = "library-owner.json"
MAX_PREVIOUS = 8


class LibraryLocation(Record):
    """One library folder outside the owner's home."""

    schema_version: Literal["sovereign.library-location.v1"] = (
        "sovereign.library-location.v1"
    )
    path: str = Field(min_length=1, max_length=4096)
    identity: tuple[int, int]
    # Whether the folder keeps owner-only permissions on this filesystem.
    private: bool


class LibrarySettings(Record):
    """The current library folder (None: the owner's home) and earlier ones."""

    schema_version: Literal["sovereign.library-settings.v1"] = (
        "sovereign.library-settings.v1"
    )
    current: LibraryLocation | None = None
    previous: tuple[LibraryLocation, ...] = Field(default=(), max_length=MAX_PREVIOUS)


class LibraryMarker(Record):
    """Written inside a library folder; names the Sovereign that owns it."""

    schema_version: Literal["sovereign.library-marker.v1"] = (
        "sovereign.library-marker.v1"
    )
    owner_id: Digest


def _identity(path: Path) -> tuple[int, int]:
    value = os.stat(path, follow_symlinks=False)
    return value.st_dev, value.st_ino


class Library:
    """Resolve and change where large model data lives."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self._lock = threading.Lock()

    def owner_id(self) -> str:
        """A stable random identity for this Sovereign, kept on the system drive."""
        with directory(self.home) as root:
            try:
                value = strict_json(read_metadata(root, OWNER))
                if isinstance(value, dict) and isinstance(value.get("owner_id"), str):
                    return str(value["owner_id"])
            except FileNotFoundError:
                pass
            created = digest(uuid.uuid4().bytes)
            atomic_bytes(root, OWNER, identity_json({"owner_id": created}))
            return created

    def settings(self) -> LibrarySettings:
        """Read local settings without touching any drive."""
        with directory(self.home) as root:
            try:
                return LibrarySettings.model_validate_json(
                    read_metadata(root, SETTINGS)
                )
            except FileNotFoundError:
                return LibrarySettings()

    def _write(self, settings: LibrarySettings) -> None:
        with directory(self.home) as root:
            atomic_bytes(root, SETTINGS, encode_record(settings))

    def _available(self, location: LibraryLocation) -> bool:
        path = Path(location.path)
        try:
            if _identity(path) != tuple(location.identity):
                return False
            with directory(path) as root:
                marker = LibraryMarker.model_validate_json(read_metadata(root, MARKER))
        except (OSError, ValueError, PreservationError):
            return False
        return marker.owner_id == self.owner_id()

    def root(self) -> Path:
        """Where new large data goes; refuses while the chosen drive is missing."""
        current = self.settings().current
        if current is None:
            return self.home
        if not self._available(current):
            raise PreservationError("library_storage_unavailable")
        return Path(current.path)

    def available(self) -> bool:
        """Whether the current library folder is connected and is this owner's."""
        current = self.settings().current
        return current is None or self._available(current)

    def roots(self) -> list[Path]:
        """Every connected library folder, current first, then earlier ones."""
        settings = self.settings()
        # The current library folder is always searched first, including the
        # home when it is current again, so a moved entry wins over its source.
        found: list[Path] = [] if settings.current is not None else [self.home]
        for location in (settings.current, *settings.previous):
            if location is not None and self._available(location):
                found.append(Path(location.path))
        if self.home not in found:
            found.append(self.home)
        return found

    def find(self, relative: str) -> Path:
        """The existing location of a library entry, else where a new one goes."""
        for root in self.roots():
            candidate = root / relative
            if os.path.lexists(candidate):
                return candidate
        return self.root() / relative

    def private_root(self) -> Path:
        """Where mirror stores go: they need owner-only folders.

        A library folder on a drive that can't keep owner-only permissions
        (exFAT, some network shares) still holds copies; published copies and
        the community pool then stay in the owner's home.
        """
        current = self.settings().current
        if current is not None and not current.private:
            return self.home
        return self.root()

    def free_bytes(self) -> int:
        """Free space where the next copy will be written."""
        return shutil.disk_usage(self.root()).free

    def view(self) -> dict[str, JsonValue]:
        """Local state for the Library screen; probes only already-known folders."""
        settings = self.settings()
        current = settings.current
        available = current is None or self._available(current)
        free: int | None
        try:
            free = shutil.disk_usage(Path(current.path) if current else self.home).free
        except OSError:
            free = None
        return {
            "path": current.path if current else None,
            "system": current is None,
            "available": available,
            "private": True if current is None else current.private,
            "free_bytes": free if available else None,
            "previous": [location.path for location in settings.previous],
        }

    def review(self, path: str) -> dict[str, JsonValue]:
        """Check a chosen folder offline; nothing is created until setup."""
        chosen = Path(path)
        if (
            not path
            or len(path) > 4096
            or not chosen.is_absolute()
            or chosen.name in {"", ".", ".."}
            or ".." in chosen.parts
        ):
            raise PreservationError("invalid_control")
        try:
            parent = chosen.parent.resolve(strict=True)
        except OSError as error:
            raise PreservationError("library_folder_unavailable") from error
        chosen = parent / chosen.name
        if chosen == self.home or self.home in chosen.parents:
            raise PreservationError("library_folder_inside_sovereign")
        owner = self.owner_id()
        reattach = False
        try:
            info = os.stat(chosen, follow_symlinks=False)
        except FileNotFoundError:
            info = None
        except OSError as error:
            raise PreservationError("library_folder_unavailable") from error
        if info is not None:
            if not os.path.isdir(chosen) or os.path.islink(chosen):
                raise PreservationError("library_folder_not_empty")
            if info.st_uid != os.getuid():
                raise PreservationError("library_folder_not_yours")
            entries = os.listdir(chosen)
            if MARKER in entries:
                with directory(chosen) as root:
                    marker = LibraryMarker.model_validate_json(
                        read_metadata(root, MARKER)
                    )
                if marker.owner_id != owner:
                    raise PreservationError("library_folder_belongs_to_another")
                reattach = True
            elif entries:
                raise PreservationError("library_folder_not_empty")
            free = shutil.disk_usage(chosen).free
        else:
            if os.stat(parent).st_uid != os.getuid() and not os.access(parent, os.W_OK):
                raise PreservationError("library_folder_not_yours")
            free = shutil.disk_usage(parent).free
        value: dict[str, JsonValue] = {
            "path": str(chosen),
            "exists": info is not None,
            "identity": [info.st_dev, info.st_ino] if info else None,
            "parent_identity": list(_identity(parent)),
            "reattach": reattach,
            "same_drive": (info.st_dev if info else _identity(parent)[0])
            == os.stat(self.home).st_dev,
            "expires_at": int(time.time()) + 600,
        }
        review_id = digest(identity_json(value))
        with directory(self.home) as root:
            atomic_bytes(
                root, "library-reviews/" + review_id + ".json", identity_json(value)
            )
        return {**value, "review_id": review_id, "free_bytes": free}

    def setup(self, review_id: str) -> dict[str, JsonValue]:
        """Create (or re-attach) the reviewed folder and make it current."""
        if len(review_id) != 64 or any(c not in "0123456789abcdef" for c in review_id):
            raise PreservationError("invalid_control")
        with self._lock:
            with directory(self.home) as root:
                raw = read_metadata(root, "library-reviews/" + review_id + ".json")
            value = strict_json(raw)
            if not isinstance(value, dict) or digest(raw) != review_id:
                raise PreservationError("library_folder_changed")
            expires = value.get("expires_at")
            if type(expires) is not int or expires <= time.time():
                raise PreservationError("plan_missing_or_expired")
            path = Path(str(value["path"]))
            if list(_identity(path.parent)) != value["parent_identity"]:
                raise PreservationError("library_folder_changed")
            owner = self.owner_id()
            if value["identity"] is None:
                try:
                    os.mkdir(path, mode=0o700)
                except FileExistsError as error:
                    raise PreservationError("library_folder_changed") from error
            elif list(_identity(path)) != value["identity"]:
                raise PreservationError("library_folder_changed")
            with directory(path) as target:
                if value["reattach"] is not True:
                    if os.listdir(target):
                        raise PreservationError("library_folder_not_empty")
                    atomic_bytes(
                        target, MARKER, encode_record(LibraryMarker(owner_id=owner))
                    )
                else:
                    marker = LibraryMarker.model_validate_json(
                        read_metadata(target, MARKER)
                    )
                    if marker.owner_id != owner:
                        raise PreservationError("library_folder_belongs_to_another")
            # Owner-only permissions are kept where the filesystem allows it;
            # some drive formats and network shares cannot hold them.
            private = os.stat(path).st_mode & 0o077 == 0
            location = LibraryLocation(
                path=str(path), identity=_identity(path), private=private
            )
            self._use(location)
        return self.view()

    def use_home(self) -> dict[str, JsonValue]:
        """Keep new copies on the system drive again; nothing is moved or deleted."""
        with self._lock:
            self._use(None)
        return self.view()

    def _use(self, location: LibraryLocation | None) -> None:
        settings = self.settings()
        previous = [
            item
            for item in (settings.current, *settings.previous)
            if item is not None and (location is None or item.path != location.path)
        ]
        if len(previous) > MAX_PREVIOUS:
            # Never stop looking in a folder that may still hold copies: move
            # them first, which forgets folders left empty.
            raise PreservationError("library_location_limit")
        self._write(LibrarySettings(current=location, previous=tuple(previous)))

    def forget_previous(self, path: str) -> None:
        """Drop an earlier folder once nothing Sovereign needs is left in it."""
        with self._lock:
            settings = self.settings()
            self._write(
                settings.model_copy(
                    update={
                        "previous": tuple(
                            item for item in settings.previous if item.path != path
                        )
                    }
                )
            )


def _hash(path: Path, control: OperationControl) -> str:
    digest_ = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            control.checkpoint()
            digest_.update(chunk)
    return digest_.hexdigest()


def move_tree(
    source: Path,
    staging: Path,
    links: dict[str, Path],
    control: OperationControl,
    progress: Callable[[int], None],
) -> None:
    """Copy one library entry into ``staging`` and check every file read back.

    Content-addressed objects (``objects/sha256/<hash>``) already copied in
    this move are hard-linked instead of copied again, so a published copy's
    store keeps sharing its files with the copy it came from. Symbolic links
    and special files are refused. The source is never changed here.
    """
    if not shutil.rmtree.avoids_symlink_attacks:
        raise PreservationError("safe_temporary_cleanup_unavailable")
    if os.path.lexists(staging):
        shutil.rmtree(staging)
    os.mkdir(staging, mode=0o700)
    expected: dict[Path, str] = {}

    def unreadable(error: OSError) -> None:
        # A folder that can't be listed (e.g. a NAS hiccup) must stop the
        # move: skipping it would put an incomplete copy in place.
        raise error

    for folder, folders, files in os.walk(
        source, onerror=unreadable, followlinks=False
    ):
        control.checkpoint()
        relative = Path(folder).relative_to(source)
        for name in folders:
            if os.path.islink(Path(folder) / name):
                raise PreservationError("library_move_unsupported_file")
            os.mkdir(staging / relative / name, mode=0o700)
        for name in files:
            original = Path(folder) / name
            info = os.lstat(original)
            if not stat.S_ISREG(info.st_mode):
                raise PreservationError("library_move_unsupported_file")
            target = staging / relative / name
            parts = (relative / name).parts
            object_hash = (
                parts[-1]
                if len(parts) >= 3
                and parts[-3:-1] == ("objects", "sha256")
                and len(parts[-1]) == 64
                else None
            )
            linked = links.get(object_hash) if object_hash else None
            if linked is not None and os.stat(linked).st_dev == os.stat(staging).st_dev:
                try:
                    os.link(linked, target)
                except OSError:
                    pass  # e.g. exFAT has no hard links: copy instead
                else:
                    expected[target] = object_hash  # type: ignore[assignment]
                    continue
            hasher = hashlib.sha256()
            with open(original, "rb") as reader, open(target, "xb") as writer:
                while chunk := reader.read(CHUNK_SIZE):
                    control.checkpoint()
                    hasher.update(chunk)
                    writer.write(chunk)
                    progress(len(chunk))
                writer.flush()
                os.fsync(writer.fileno())
            expected[target] = hasher.hexdigest()
            if object_hash is not None and expected[target] == object_hash:
                links[object_hash] = target
    # Read every file back from the new drive before anything is switched.
    control.phase("checking")
    for target, value in expected.items():
        if _hash(target, control) != value:
            raise PreservationError("library_move_verification_failed")
    for folder, _folders, _files in os.walk(staging):
        descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
