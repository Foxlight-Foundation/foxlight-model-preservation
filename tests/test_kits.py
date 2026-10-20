"""Disaster recovery, exact framing, explicit key trust and safe publication."""

import shutil
import struct
from pathlib import Path

import pytest
from conftest import Capture

from foxlight_model_preservation.kits import MAGIC, export_kit, import_kit, inspect_kit
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationCancelled, OperationControl
from foxlight_model_preservation.storage import verify_store


def test_survives_original_loss(capture: Capture, tmp_path: Path) -> None:
    kit = tmp_path / "saved.foxkit"
    info = export_kit(capture.store, capture.identity, capture.trust, kit)
    assert info.kit_bytes == kit.stat().st_size
    shutil.rmtree(capture.store)
    for source in capture.sources.values():
        shutil.rmtree(source)
    assert inspect_kit(kit).manifest_id == capture.identity
    restored = tmp_path / "recovered"
    with pytest.raises(PreservationError, match="explicit kit-key"):
        import_kit(kit, restored, expected_manifest_id=capture.identity)
    import_kit(
        kit, restored, expected_manifest_id=capture.identity, accept_kit_key=True
    )
    assert verify_store(restored, capture.identity, capture.trust) == info.manifest
    assert not list(restored.rglob("*.pem"))
    assert not list(restored.rglob("secret-not-selected"))
    with pytest.raises(FileExistsError):
        import_kit(
            kit, restored, expected_manifest_id=capture.identity, trust=capture.trust
        )


@pytest.mark.parametrize(
    "damage", ["truncated", "trailing", "object", "header", "oversized"]
)
def test_damage_never_publishes(capture: Capture, tmp_path: Path, damage: str) -> None:
    kit = tmp_path / "saved.foxkit"
    export_kit(capture.store, capture.identity, capture.trust, kit)
    data = bytearray(kit.read_bytes())
    if damage == "truncated":
        del data[-1:]
    elif damage == "trailing":
        data.extend(b"x")
    elif damage == "object":
        data[-1] ^= 1
    elif damage == "header":
        data[0] ^= 1
    else:
        data[len(MAGIC) : len(MAGIC) + 8] = struct.pack(">Q", 2**63)
    kit.write_bytes(data)
    out = tmp_path / "bad"
    with pytest.raises(ValueError):
        import_kit(kit, out, expected_manifest_id=capture.identity, trust=capture.trust)
    assert not out.exists()
    assert not list(tmp_path.glob(".kit-import-*"))


def test_refuse_wrong_identity_symlinks_and_cancel(
    capture: Capture, tmp_path: Path
) -> None:
    kit = tmp_path / "saved.foxkit"
    export_kit(capture.store, capture.identity, capture.trust, kit)
    with pytest.raises(PreservationError, match="changed"):
        import_kit(
            kit, tmp_path / "bad", expected_manifest_id="0" * 64, trust=capture.trust
        )
    with pytest.raises(FileExistsError):
        export_kit(capture.store, capture.identity, capture.trust, kit)
    link = tmp_path / "link"
    link.symlink_to(kit)
    with pytest.raises(OSError):
        inspect_kit(link)
    control = OperationControl()
    control.cancel()
    with pytest.raises(OperationCancelled):
        import_kit(
            kit,
            tmp_path / "cancel",
            expected_manifest_id=capture.identity,
            trust=capture.trust,
            control=control,
        )
    assert not (tmp_path / "cancel").exists()
