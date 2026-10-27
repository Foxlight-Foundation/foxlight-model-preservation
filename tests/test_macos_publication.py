"""The public native lane refuses substituted containers and signing identity."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import plistlib
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "verify_macos_release", ROOT / "scripts/verify_macos_release.py"
)
assert spec and spec.loader
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


def test_closed_assets_refuse_tampering_extra_or_symlink(tmp_path):
    asset = tmp_path / "Sovereign.dmg"
    asset.write_bytes(b"reviewed final bytes")
    manifest = {
        "artifacts": {asset.name: hashlib.sha256(asset.read_bytes()).hexdigest()}
    }
    verify.check_assets(tmp_path, manifest)
    asset.write_bytes(b"substituted")
    with pytest.raises(ValueError, match="digest"):
        verify.check_assets(tmp_path, manifest)
    asset.unlink()
    asset.symlink_to(ROOT / "README.md")
    with pytest.raises(ValueError, match="regular"):
        verify.check_assets(tmp_path, manifest)
    (tmp_path / "unexpected").touch()
    with pytest.raises(ValueError, match="exactly"):
        verify.check_assets(tmp_path, manifest)


def test_full_codedirectory_and_developer_id_required():
    manifest = {"team": "EXPECTED12", "app_codedirectory_sha256": "a" * 64}
    details = (
        "TeamIdentifier=EXPECTED12\nAuthority=Developer ID Application: Foxlight\n"
        "Authority=Apple Root CA\nflags=0x10000(runtime)\n"
        f"CandidateCDHashFull sha256={'a' * 64}\n"
    )
    verify.check_identity(details, manifest, app=True)
    with pytest.raises(ValueError, match="identity"):
        verify.check_identity(
            details.replace("EXPECTED12", "OTHER12345"), manifest, app=True
        )
    with pytest.raises(ValueError, match="CodeDirectory"):
        verify.check_identity(details.replace("a" * 64, "a" * 40), manifest, app=True)
    with pytest.raises(ValueError, match="CodeDirectory"):
        verify.check_identity(details.replace("(runtime)", ""), manifest, app=True)


def test_receipt_keeps_binary_and_verifier_sources_separate(tmp_path):
    manifest = json.loads(verify.MANIFEST.read_bytes())
    app = tmp_path / "Sovereign.app"
    resources = app / "Contents/Resources"
    resources.mkdir(parents=True)
    receipt = {
        "schema_version": "sovereign.desktop-build.v2",
        "source_revision": manifest["source_revision"],
        "shell_source_revision": manifest["source_revision"],
        "shell_source_dirty": False,
        "inputs": manifest["inputs"],
        "configuration": "release",
        "distribution": "developer-id-candidate",
    }
    path = resources / "build-receipt.json"
    path.write_text(json.dumps(receipt))
    (app / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "ai.foxlight.sovereign",
                "CFBundleShortVersionString": manifest["inputs"]["version"],
                "CFBundleVersion": manifest["inputs"]["build_number"],
            }
        )
    )
    verify.check_receipt(app, manifest)
    receipt["source_revision"] = "f" * 40
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="receipt"):
        verify.check_receipt(app, manifest)
    receipt["source_revision"] = manifest["source_revision"]
    path.write_text(json.dumps(receipt))
    (resources / "outside").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="external symlink"):
        verify.check_receipt(app, manifest)


def test_approved_finder_window_stays_data_only(tmp_path):
    (tmp_path / "Sovereign.app").mkdir()
    (tmp_path / "Applications").symlink_to("/Applications")
    for name in (".DS_Store", ".VolumeIcon.icns", ".background.tiff"):
        (tmp_path / name).write_bytes(b"approved window data")
    verify.check_image_layout(tmp_path)
    extra = tmp_path / "unexpected-program"
    extra.write_bytes(b"program")
    with pytest.raises(ValueError, match="image layout"):
        verify.check_image_layout(tmp_path)
    extra.unlink()
    background = tmp_path / ".background.tiff"
    background.unlink()
    background.symlink_to(ROOT / "README.md")
    with pytest.raises(ValueError, match="image layout"):
        verify.check_image_layout(tmp_path)
