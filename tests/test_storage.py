"""Actual byte copying, safe publication, and new-machine materialization."""

import shutil
from pathlib import Path

import pytest
from conftest import Capture

from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.storage import (
    CHUNK_SIZE,
    directory,
    manifest_path,
    object_path,
    pack,
    restore,
    unique_objects,
    verify_store,
    writer_lock,
)


def test_complete_restore_is_independent(capture: Capture, tmp_path: Path) -> None:
    output = tmp_path / "restored"
    manifest = restore(capture.store, capture.identity, capture.trust, output)
    for member in manifest.members:
        assert member.retained_card == {"verification_class": "legacy"}
        for file in member.files:
            source = capture.sources[member.member_id] / file.path
            restored = output / "members" / member.member_id / file.path
            assert source.read_bytes() == restored.read_bytes()
            assert source.stat().st_ino != restored.stat().st_ino
        assert not (
            output / "members" / member.member_id / "secret-not-selected"
        ).exists()
    assert (output / ".preservation/envelope.json").is_file()
    for source in capture.sources.values():
        (source / "model.bin").unlink()
    verify_store(capture.store, capture.identity, capture.trust)


def test_restore_never_overwrites(capture: Capture, tmp_path: Path) -> None:
    destination = tmp_path / "existing"
    destination.mkdir()
    keep = destination / "keep"
    keep.write_bytes(b"keep")
    with pytest.raises(PreservationError):
        restore(capture.store, capture.identity, capture.trust, destination)
    assert keep.read_bytes() == b"keep"


@pytest.mark.parametrize("directory_link", [False, True])
def test_symlink_capture_rejected(
    capture: Capture,
    tmp_path: Path,
    directory_link: bool,
) -> None:
    source = capture.sources["weights"]
    if directory_link:
        moved = tmp_path / "real-source"
        source.rename(moved)
        source.symlink_to(moved, target_is_directory=True)
    else:
        (source / "model.bin").unlink()
        (source / "model.bin").symlink_to(source / "secret-not-selected")
    target = tmp_path / "failed"
    with pytest.raises(OSError):
        pack(capture.recipe, capture.sources, target, (capture.key,))
    assert not list(target.glob("manifests/*.json"))


def test_object_symlink_rejected(capture: Capture, tmp_path: Path) -> None:
    manifest = verify_store(capture.store, capture.identity, capture.trust)
    object_file = capture.store / object_path(manifest.members[0].files[0].sha256)
    content = object_file.read_bytes()
    outside = tmp_path / "outside"
    outside.write_bytes(content)
    object_file.unlink()
    object_file.symlink_to(outside)
    with pytest.raises(OSError):
        verify_store(capture.store, capture.identity, capture.trust)


def test_concurrent_writer_fenced(capture: Capture) -> None:
    with directory(capture.store) as root, writer_lock(root):
        with pytest.raises(PreservationError, match="another writer"):
            pack(capture.recipe, capture.sources, capture.store, (capture.key,))


def test_expected_origin_hash_and_source_destination(capture: Capture) -> None:
    member = capture.recipe.members[0]
    selected = member.files[0].model_copy(update={"expected_sha256": "0" * 64})
    changed = member.model_copy(update={"files": (selected, member.files[1])})
    recipe = capture.recipe.model_copy(
        update={"members": (changed, capture.recipe.members[1])}
    )
    with pytest.raises(PreservationError, match="expected hash"):
        pack(recipe, capture.sources, capture.store.parent / "bad", (capture.key,))
    with pytest.raises(PreservationError, match="inside"):
        pack(
            capture.recipe,
            capture.sources,
            capture.sources["weights"] / "backup",
            (capture.key,),
        )
    assert (capture.store / manifest_path(capture.identity)).exists()


def test_non_regular_source_never_blocks(capture: Capture, tmp_path: Path) -> None:
    import os

    selected = capture.sources["weights"] / "model.bin"
    selected.unlink()
    os.mkfifo(selected)
    with pytest.raises(PreservationError, match="regular"):
        pack(capture.recipe, capture.sources, tmp_path / "fifo", (capture.key,))


def test_repeated_capture_reuses_verified_objects_on_full_disk(
    capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    full = shutil.disk_usage(capture.store)._replace(free=0)
    monkeypatch.setattr(shutil, "disk_usage", lambda _: full)
    assert (
        pack(capture.recipe, capture.sources, capture.store, (capture.key,))
        == capture.identity
    )
    verify_store(capture.store, capture.identity, capture.trust)


def test_new_capture_needs_space_only_for_unique_objects(
    capture: Capture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = verify_store(capture.store, capture.identity, capture.trust)
    required = sum(file.size for file in unique_objects(manifest))
    logical = sum(file.size for member in manifest.members for file in member.files)
    assert required < logical  # Both members carry the same notice bytes.
    target = tmp_path / "deduplicated"
    baseline = shutil.disk_usage(tmp_path)

    def remaining_space(_: object) -> object:
        used = sum(item.stat().st_size for item in target.glob("objects/sha256/*"))
        return baseline._replace(free=required + 16 * CHUNK_SIZE - used)

    monkeypatch.setattr(shutil, "disk_usage", remaining_space)
    assert (
        pack(capture.recipe, capture.sources, target, (capture.key,))
        == capture.identity
    )
    verify_store(target, capture.identity, capture.trust)


@pytest.mark.parametrize("corrupted", [False, True])
@pytest.mark.parametrize("enough_space", [False, True])
def test_reuse_requires_valid_bytes_and_space_for_replacement(
    capture: Capture,
    monkeypatch: pytest.MonkeyPatch,
    corrupted: bool,
    enough_space: bool,
) -> None:
    manifest = verify_store(capture.store, capture.identity, capture.trust)
    file = manifest.members[0].files[0]
    target = capture.store / object_path(file.sha256)
    envelope = capture.store / manifest_path(capture.identity)
    previous_envelope = envelope.read_bytes()
    if corrupted:
        target.write_bytes(b"x" * file.size)
    else:
        target.unlink()
    available = shutil.disk_usage(capture.store)._replace(
        free=file.size + 16 * CHUNK_SIZE - (0 if enough_space else 1)
    )
    monkeypatch.setattr(shutil, "disk_usage", lambda _: available)
    if enough_space:
        assert (
            pack(capture.recipe, capture.sources, capture.store, (capture.key,))
            == capture.identity
        )
        verify_store(capture.store, capture.identity, capture.trust)
    else:
        with pytest.raises(PreservationError, match="insufficient free space"):
            pack(capture.recipe, capture.sources, capture.store, (capture.key,))
        if corrupted:
            assert target.read_bytes() == b"x" * file.size
        else:
            assert not target.exists()
    assert envelope.read_bytes() == previous_envelope
