"""A save's scratch files move into place: one copy on disk, every byte checked."""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
from pathlib import Path

import pytest
from conftest import Capture

from foxlight_model_preservation import storage
from foxlight_model_preservation.archives import (
    published_archive_manifest,
    verify_archive,
)
from foxlight_model_preservation.models import PreservationError, Recipe
from foxlight_model_preservation.pmf_preservation import import_preservation
from foxlight_model_preservation.storage import (
    manifest_path,
    object_path,
    pack,
    verify_store,
)


def _scratch(capture: Capture, root: Path) -> tuple[Recipe, dict[str, Path]]:
    """A copy of the fixture's sources, with every file's expected hash."""
    sources: dict[str, Path] = {}
    members = []
    for member in capture.recipe.members:
        folder = root / member.member_id
        shutil.copytree(capture.sources[member.member_id], folder)
        sources[member.member_id] = folder
        files = [
            file.model_copy(
                update={
                    "expected_sha256": hashlib.sha256(
                        (folder / file.path).read_bytes()
                    ).hexdigest()
                }
            )
            for file in member.files
        ]
        members.append(member.model_copy(update={"files": tuple(files)}))
    return capture.recipe.model_copy(update={"members": tuple(members)}), sources


def test_consume_moves_files_and_keeps_the_same_identity(
    tmp_path: Path, capture: Capture
) -> None:
    recipe, sources = _scratch(capture, tmp_path / "scratch")
    weights = sources["weights"] / "model.bin"
    inode = os.stat(weights).st_ino
    identity = pack(recipe, sources, tmp_path / "moved", (capture.key,), consume=True)
    # The same signed manifest a copying capture makes.
    assert identity == pack(
        recipe,
        _scratch(capture, tmp_path / "again")[1],
        tmp_path / "copied",
        (capture.key,),
    )
    assert not weights.exists()
    moved = [
        path
        for path in (tmp_path / "moved" / "objects" / "sha256").iterdir()
        if os.stat(path).st_ino == inode
    ]
    assert len(moved) == 1  # the downloaded file itself, not a copy
    verify_store(tmp_path / "moved", identity, capture.trust)


def test_consume_refuses_bytes_that_differ_and_publishes_nothing(
    tmp_path: Path, capture: Capture
) -> None:
    recipe, sources = _scratch(capture, tmp_path / "scratch")
    weights = sources["weights"] / "model.bin"
    data = bytearray(weights.read_bytes())
    data[0] ^= 1  # same size, different bytes
    weights.write_bytes(bytes(data))
    destination = tmp_path / "moved"
    with pytest.raises(PreservationError):
        pack(recipe, sources, destination, (capture.key,), consume=True)
    assert not (destination / "manifests").exists() or not any(
        (destination / "manifests").iterdir()
    )


def test_consume_needs_every_expected_hash(tmp_path: Path, capture: Capture) -> None:
    with pytest.raises(PreservationError, match="expected hashes"):
        pack(
            capture.recipe,
            capture.sources,
            tmp_path / "moved",
            (capture.key,),
            consume=True,
        )


def _legacy(capture: Capture, tmp_path: Path) -> Path:
    store = tmp_path / "legacy"
    shutil.copytree(capture.store, store)
    return store


def test_storing_moves_objects_and_checks_every_one_first(
    tmp_path: Path, capture: Capture
) -> None:
    legacy = _legacy(capture, tmp_path)
    destination = tmp_path / "copy"
    conversion = import_preservation(
        legacy,
        capture.identity,
        capture.trust,
        destination,
        (capture.key,),
        consume=True,
    )
    assert not any((legacy / "objects" / "sha256").iterdir())
    manifest = published_archive_manifest(
        destination, conversion.output_identity, capture.trust
    )
    assert manifest == verify_archive(
        destination, conversion.output_identity, capture.trust
    )


def test_storing_refuses_a_changed_object_and_publishes_nothing(
    tmp_path: Path, capture: Capture
) -> None:
    legacy = _legacy(capture, tmp_path)
    objects = sorted((legacy / "objects" / "sha256").iterdir())
    target = max(objects, key=lambda path: path.stat().st_size)
    data = bytearray(target.read_bytes())
    data[-1] ^= 1
    target.write_bytes(bytes(data))
    destination = tmp_path / "copy"
    with pytest.raises(PreservationError):
        import_preservation(
            legacy,
            capture.identity,
            capture.trust,
            destination,
            (capture.key,),
            consume=True,
        )
    assert not destination.exists()


def test_another_drive_falls_back_to_a_checked_copy(
    tmp_path: Path, capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = os.rename

    def cross_device(source, destination, *args, **kwargs):
        # The scratch files sit on "another drive": renaming them fails.
        if str(source) in {"model.bin", "LICENSE"}:
            raise OSError(errno.EXDEV, "cross-device link")
        return real(source, destination, *args, **kwargs)

    monkeypatch.setattr(storage.os, "rename", cross_device)
    recipe, sources = _scratch(capture, tmp_path / "scratch")
    identity = pack(recipe, sources, tmp_path / "moved", (capture.key,), consume=True)
    assert (sources["weights"] / "model.bin").exists()  # copied, not moved
    verify_store(tmp_path / "moved", identity, capture.trust)
    assert (tmp_path / "moved" / manifest_path(identity)).exists()
    assert all(
        (tmp_path / "moved" / object_path(file.expected_sha256 or "")).exists()
        for member in recipe.members
        for file in member.files
    )
