"""PMF transport is independent of runtime cards and retains legacy kit bytes."""

import shutil
from pathlib import Path

import pytest
from conftest import Capture

from foxlight_model_preservation.archives import verify_archive
from foxlight_model_preservation.kits import (
    MAGIC,
    PMF_MAGIC,
    export_kit,
    import_kit,
    inspect_kit,
    read_kit_document,
)
from foxlight_model_preservation.models import PreservationError, TrustProfile
from foxlight_model_preservation.pmf_models import PMFManifest
from foxlight_model_preservation.pmf_preservation import (
    import_preservation,
    inspect_preservation_pmf,
)
from foxlight_model_preservation.storage import directory, object_path, read_metadata


def test_pmf_kit_restores_without_original_archive(
    capture: Capture, tmp_path: Path
) -> None:
    portable = tmp_path / "portable"
    result = import_preservation(
        capture.store, capture.identity, capture.trust, portable, (capture.key,)
    )
    identity = result.output_identity
    original = read_metadata_document(portable, identity, capture.trust)
    legacy_kit = tmp_path / "legacy.foxkit"
    export_kit(capture.store, capture.identity, capture.trust, legacy_kit)
    legacy_bytes = legacy_kit.read_bytes()
    assert legacy_bytes.startswith(MAGIC)
    kit = tmp_path / "portable.foxkit"
    export_kit(portable, identity, capture.trust, kit)
    assert kit.read_bytes().startswith(PMF_MAGIC)
    assert isinstance(inspect_kit(kit).manifest, PMFManifest)
    assert (
        read_kit_document(
            kit,
            "preservation-envelope",
            trust=capture.trust,
            expected_manifest_id=identity,
        )
        == original
    )
    shutil.rmtree(portable)
    shutil.rmtree(capture.store)
    recovered = tmp_path / "recovered"
    import_kit(kit, recovered, expected_manifest_id=identity, trust=capture.trust)
    manifest = verify_archive(recovered, identity, capture.trust)
    assert isinstance(manifest, PMFManifest)
    source_id, source = inspect_preservation_pmf(
        manifest, original, source_trust=capture.trust
    )
    assert source_id == capture.identity and len(source.members) == 2
    assert legacy_kit.read_bytes() == legacy_bytes
    with pytest.raises(PreservationError):
        read_kit_document(
            kit,
            "preservation-envelope",
            trust=capture.trust,
            expected_manifest_id="0" * 64,
        )


def read_metadata_document(portable: Path, identity: str, trust: TrustProfile) -> bytes:
    manifest = verify_archive(portable, identity, trust)
    assert isinstance(manifest, PMFManifest)
    document = next(
        item
        for item in manifest.documents
        if item.document_id == "preservation-envelope"
    )
    with directory(portable) as root:
        return read_metadata(root, object_path(document.sha256))


@pytest.mark.parametrize("offset", [0, -1])
def test_pmf_kit_refuses_version_confusion_and_corruption(
    capture: Capture, tmp_path: Path, offset: int
) -> None:
    portable = tmp_path / "portable"
    result = import_preservation(
        capture.store, capture.identity, capture.trust, portable, (capture.key,)
    )
    kit = tmp_path / "portable.foxkit"
    export_kit(portable, result.output_identity, capture.trust, kit)
    content = bytearray(kit.read_bytes())
    if offset == 0:
        content[: len(MAGIC)] = MAGIC
    else:
        content[-1] ^= 1
    kit.write_bytes(content)
    output = tmp_path / "refused"
    with pytest.raises(PreservationError):
        import_kit(
            kit,
            output,
            expected_manifest_id=result.output_identity,
            trust=capture.trust,
        )
    assert not output.exists()


def test_generic_pmf_kit_needs_no_card_or_source_profile(
    capture: Capture, tmp_path: Path
) -> None:
    from test_pmf import common_manifest

    from foxlight_model_preservation.pmf import pack_pmf, restore_pmf

    manifest = common_manifest(capture)
    portable = tmp_path / "portable"
    identity = pack_pmf(manifest, capture.sources, {}, portable, (capture.key,))
    kit = tmp_path / "generic.foxkit"
    export_kit(portable, identity, capture.trust, kit)
    imported = tmp_path / "imported"
    import_kit(kit, imported, expected_manifest_id=identity, trust=capture.trust)
    assert verify_archive(imported, identity, capture.trust) == manifest
    restore_pmf(imported, identity, capture.trust, tmp_path / "files")
    assert (tmp_path / "files/members/weights/model.bin").read_bytes() == (
        capture.sources["weights"] / "model.bin"
    ).read_bytes()


def test_converter_signature_cannot_replace_original_trust(
    capture: Capture, tmp_path: Path
) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from test_pmf import new_trust

    converter = Ed25519PrivateKey.generate()
    converter_trust = new_trust(converter)
    portable = tmp_path / "portable"
    result = import_preservation(
        capture.store, capture.identity, capture.trust, portable, (converter,)
    )
    manifest = verify_archive(portable, result.output_identity, converter_trust)
    assert isinstance(manifest, PMFManifest)
    original = read_metadata_document(portable, result.output_identity, converter_trust)
    with pytest.raises(PreservationError):
        inspect_preservation_pmf(manifest, original, source_trust=converter_trust)
    assert (
        inspect_preservation_pmf(manifest, original, source_trust=capture.trust)[0]
        == capture.identity
    )
