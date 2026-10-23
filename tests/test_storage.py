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


def _second_signer(capture: Capture):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from foxlight_model_preservation.signing import public_record

    key = Ed25519PrivateKey.generate()
    trust = capture.trust.model_copy(update={"keys": (public_record(key),)})
    return key, trust


def test_resigning_the_same_capture_keeps_every_signer(capture: Capture) -> None:
    from foxlight_model_preservation.storage import pack

    key, trust = _second_signer(capture)
    assert pack(capture.recipe, capture.sources, capture.store, (key,)) == (
        capture.identity
    )
    verify_store(capture.store, capture.identity, capture.trust)
    verify_store(capture.store, capture.identity, trust)
    both = capture.trust.model_copy(
        update={"keys": (*capture.trust.keys, *trust.keys), "threshold": 2}
    )
    verify_store(capture.store, capture.identity, both)


def test_fetching_a_weaker_envelope_cannot_strip_existing_signatures(
    capture: Capture, tmp_path: Path
) -> None:
    from foxlight_model_preservation.storage import pack
    from foxlight_model_preservation.transfer import fetch

    key, trust = _second_signer(capture)
    mirror = tmp_path / "single-signer-mirror"
    shutil.copytree(capture.store, mirror)
    pack(capture.recipe, capture.sources, capture.store, (key,))
    both = capture.trust.model_copy(
        update={"keys": (*capture.trust.keys, *trust.keys), "threshold": 2}
    )
    fetch(capture.identity, capture.trust, (str(mirror),), capture.store)
    verify_store(capture.store, capture.identity, both)


def test_unrelated_or_corrupt_existing_envelope_is_replaced(capture: Capture) -> None:
    from foxlight_model_preservation.storage import manifest_path, pack

    (capture.store / manifest_path(capture.identity)).write_text("{not json")
    key, trust = _second_signer(capture)
    pack(capture.recipe, capture.sources, capture.store, (key,))
    verify_store(capture.store, capture.identity, trust)
    with pytest.raises(PreservationError, match="threshold"):
        verify_store(capture.store, capture.identity, capture.trust)


def test_signers_survive_a_differently_encoded_identical_payload(
    tmp_path: Path,
) -> None:
    import base64
    import json

    from foxlight_model_preservation.signing import canonical_bytes
    from foxlight_model_preservation.storage import (
        directory,
        manifest_path,
        publish_envelope,
    )

    payload = bytes([0xFB, 0xEF, 0xFF]) * 4
    standard = base64.b64encode(payload).decode()
    url_safe = base64.urlsafe_b64encode(payload).decode()
    assert standard != url_safe

    def envelope(encoded: str, key: str) -> bytes:
        signature = {"keyid": "ed25519:" + key * 64, "sig": "A" * 86 + "=="}
        return canonical_bytes(
            {"payloadType": "t", "payload": encoded, "signatures": [signature]}
        )

    identity = "0" * 64
    with directory(tmp_path, create=True) as root:
        publish_envelope(root, identity, envelope(standard, "a"))
        publish_envelope(root, identity, envelope(url_safe, "b"))
    stored = json.loads((tmp_path / manifest_path(identity)).read_bytes())
    assert stored["payload"] == url_safe
    assert [item["keyid"][-1] for item in stored["signatures"]] == ["b", "a"]
