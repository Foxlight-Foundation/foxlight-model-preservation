"""Explicit mounted-folder backups containing complete independently usable kits.

Only a confirmed setup creates one empty private folder. Later writes require the
original directory and its marker; missing mounts are never recreated. Reading
local settings performs no potentially blocking NAS probes or background work.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from pydantic import Field, JsonValue

from ..cards import identity_json
from ..kits import export_kit
from ..models import Digest, PreservationError, Record, TrustProfile
from ..operations import OperationControl
from ..signing import digest, encode_record
from ..storage import atomic_bytes, directory, read_metadata

MARKER = ".sovereign-backup.json"


class BackupDestination(Record):
    """Private owner settings; portable kits contain no path or private key."""

    schema_version: Literal["sovereign.backup-destination.v1"] = (
        "sovereign.backup-destination.v1"
    )
    destination_id: Digest
    label: str = Field(min_length=1, max_length=80)
    path: str = Field(min_length=1, max_length=4096)
    identity: tuple[int, int]
    parent_identity: tuple[int, int]
    same_filesystem: bool


def _identity(descriptor: int) -> tuple[int, int]:
    value = os.fstat(descriptor)
    return value.st_dev, value.st_ino


@contextmanager
def _child(parent: int, name: str) -> Generator[int]:
    descriptor = os.open(
        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
    )
    try:
        yield descriptor
    finally:
        os.close(descriptor)


class Backups:
    """Remember reviewed destinations without moving an owner's canonical store."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self._setup_lock = threading.Lock()

    def read(self) -> dict[str, JsonValue]:
        """List local settings only; no drive is touched and no work is started."""
        with directory(self.home / "backup-destinations", create=True) as root:
            rows = [
                BackupDestination.model_validate_json(read_metadata(root, name))
                for name in sorted(os.listdir(root))
                if len(name) == 69 and name.endswith(".json")
            ]
        return {"destinations": [row.model_dump(mode="json") for row in rows]}

    def _load(self, identifier: str) -> BackupDestination:
        if len(identifier) != 64 or any(
            c not in "0123456789abcdef" for c in identifier
        ):
            raise PreservationError("invalid_control")
        with directory(self.home) as root:
            result = BackupDestination.model_validate_json(
                read_metadata(root, "backup-destinations/" + identifier + ".json")
            )
        if result.destination_id != identifier:
            raise PreservationError("backup_destination_changed")
        return result

    def review(self, path: str, label: str) -> dict[str, JsonValue]:
        """Review one existing empty private folder or a new direct child offline."""
        if not path or not Path(path).is_absolute() or not 0 < len(label.strip()) <= 80:
            raise PreservationError("invalid_control")
        chosen = Path(path)
        if chosen.name in {"", ".", ".."} or ".." in chosen.parts:
            raise PreservationError("invalid_control")
        parent = chosen.parent.resolve(strict=True)
        chosen = parent / chosen.name
        with directory(parent) as source:
            parent_identity = _identity(source)
            try:
                with _child(source, chosen.name) as target:
                    self._empty_private(target)
                    identity: tuple[int, int] | None = _identity(target)
            except FileNotFoundError:
                identity = None
            free = shutil.disk_usage(source).free
        with directory(self.home) as owner:
            owner_device = os.fstat(owner).st_dev
        value: dict[str, JsonValue] = {
            "path": str(chosen),
            "label": label.strip(),
            "parent_identity": list(parent_identity),
            "identity": list(identity) if identity else None,
            "same_filesystem": (identity or parent_identity)[0] == owner_device,
            "expires_at": int(time.time()) + 600,
        }
        review_id = digest(identity_json(value))
        with directory(self.home) as owner:
            atomic_bytes(
                owner, "backup-reviews/" + review_id + ".json", identity_json(value)
            )
        return {
            **value,
            "review_id": review_id,
            "creates_folder": identity is None,
            "free_bytes": free,
        }

    @staticmethod
    def _empty_private(descriptor: int) -> None:
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or info.st_mode & 0o077 or os.listdir(descriptor):
            raise PreservationError("backup_requires_empty_private_folder")

    def setup(self, identifier: str) -> dict[str, JsonValue]:
        """Confirm the exact review once; never chmod or reuse someone else's data."""
        with self._setup_lock:
            return self._setup(identifier)

    def _setup(self, identifier: str) -> dict[str, JsonValue]:
        if len(identifier) != 64 or any(
            c not in "0123456789abcdef" for c in identifier
        ):
            raise PreservationError("invalid_control")
        from ..signing import strict_json

        with directory(self.home) as owner:
            raw = read_metadata(owner, "backup-reviews/" + identifier + ".json")
        value = strict_json(raw)
        if not isinstance(value, dict) or digest(raw) != identifier:
            raise PreservationError("backup_destination_changed")
        expires = value.get("expires_at")
        if type(expires) is not int or expires <= time.time():
            raise PreservationError("plan_missing_or_expired")
        destinations = self.read()["destinations"]
        if not isinstance(destinations, list) or len(destinations) >= 16:
            raise PreservationError("backup_destination_capacity")
        path = Path(str(value["path"]))
        with directory(path.parent) as parent:
            if list(_identity(parent)) != value["parent_identity"]:
                raise PreservationError("backup_destination_changed")
            if value["identity"] is None:
                os.mkdir(path.name, mode=0o700, dir_fd=parent)
                os.fsync(parent)
            with _child(parent, path.name) as target:
                self._empty_private(target)
                if (
                    value["identity"] is not None
                    and list(_identity(target)) != value["identity"]
                ):
                    raise PreservationError("backup_destination_changed")
                result = BackupDestination(
                    destination_id=digest(uuid.uuid4().bytes),
                    label=str(value["label"]),
                    path=str(path),
                    identity=_identity(target),
                    parent_identity=_identity(parent),
                    same_filesystem=value["same_filesystem"] is True,
                )
                with directory(path) as visible:
                    if _identity(visible) != result.identity:
                        raise PreservationError("backup_destination_changed")
                atomic_bytes(target, MARKER, encode_record(result))
        with directory(self.home) as owner:
            atomic_bytes(
                owner,
                "backup-destinations/" + result.destination_id + ".json",
                encode_record(result),
            )
        return result.model_dump(mode="json")

    def forget(self, identifier: str) -> dict[str, JsonValue]:
        """Remove only local settings; never contact the drive or delete a kit."""
        self._load(identifier)
        with directory(self.home / "backup-destinations") as root:
            os.unlink(identifier + ".json", dir_fd=root)
            os.fsync(root)
        return {"forgotten": True}

    def selection(self, identifier: str) -> dict[str, JsonValue]:
        """Pin local destination settings for a finite reviewed backup job."""
        result = self._load(identifier)
        return {
            "destination_id": identifier,
            "destination_sha256": digest(encode_record(result)),
            "destination_label": result.label,
            "destination_path": result.path,
            "same_filesystem": result.same_filesystem,
        }

    def write(
        self,
        selection: dict[str, JsonValue],
        store: Path,
        manifest_id: str,
        trust: TrustProfile,
        job_id: str,
        control: OperationControl,
    ) -> Path:
        """Write and read back one complete new kit on the original connected drive."""
        result = self._load(str(selection["destination_id"]))
        if digest(encode_record(result)) != selection["destination_sha256"]:
            raise PreservationError("backup_destination_changed")
        path = Path(result.path)
        with directory(path.parent) as parent, _child(parent, path.name) as target:
            if (
                _identity(parent) != result.parent_identity
                or _identity(target) != result.identity
                or read_metadata(target, MARKER) != encode_record(result)
            ):
                raise PreservationError("backup_destination_changed")
            output = path / ("Sovereign-" + job_id + ".foxkit")
            export_kit(
                store,
                manifest_id,
                trust,
                output,
                expected_destination_identity=result.identity,
                control=control,
            )
            with directory(path) as visible:
                if _identity(visible) != result.identity:
                    raise PreservationError("backup_destination_changed")
            return output

    def folder(self, selection: dict[str, JsonValue]) -> Path:
        """Resolve only the original enrolled folder for an explicit native reveal."""
        result = self._load(str(selection["destination_id"]))
        if digest(encode_record(result)) != selection["destination_sha256"]:
            raise PreservationError("backup_destination_changed")
        path = Path(result.path)
        with directory(path.parent) as parent, _child(parent, path.name) as target:
            if (
                _identity(parent) != result.parent_identity
                or _identity(target) != result.identity
                or read_metadata(target, MARKER) != encode_record(result)
            ):
                raise PreservationError("backup_destination_changed")
        return path
