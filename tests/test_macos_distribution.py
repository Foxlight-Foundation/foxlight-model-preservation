"""Native distribution rejects archive escapes and unreviewed signing inputs."""

from __future__ import annotations

import importlib.util
import io
import json
import plistlib
import tarfile
import zipfile
from pathlib import Path

import pytest


def script(name):
    path = Path(__file__).parents[1] / "scripts" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = script("build_macos")
release = script("release_macos")


def test_substituted_native_input_refuses(tmp_path):
    archive = tmp_path / "runtime.tar.gz"
    archive.write_bytes(b"different runtime")
    with pytest.raises(ValueError, match="hash mismatch"):
        build.check_input(archive, "0" * 64)


def test_runtime_notice_inventory_retained(tmp_path):
    retained = build.copy_runtime_notices(tmp_path)
    assert "LICENSE.openssl-3.txt" in retained
    # The bundled DHT wheel ships no licence file; its upstream notice is added.
    assert "LICENSE.libtorrent.txt" in retained
    for name, expected in retained.items():
        assert build.digest(tmp_path / "runtime-notices" / name) == expected


def macho(major: int, minor: int, *, legacy: bool = False) -> bytes:
    """A minimal 64-bit Mach-O with one minimum-version load command."""
    version = (major << 16 | minor << 8).to_bytes(4, "little")
    if legacy:  # LC_VERSION_MIN_MACOSX: cmd, size, version, sdk
        command = (0x24).to_bytes(4, "little") + (16).to_bytes(4, "little")
        command += version + version
    else:  # LC_BUILD_VERSION: cmd, size, platform, minos, sdk, ntools
        command = (0x32).to_bytes(4, "little") + (24).to_bytes(4, "little")
        command += (1).to_bytes(4, "little") + version + version + bytes(4)
    header = b"\xcf\xfa\xed\xfe" + bytes(12) + (1).to_bytes(4, "little")
    header += len(command).to_bytes(4, "little") + bytes(8)
    return header + command


def universal(*slices: bytes) -> bytes:
    data = b"\xca\xfe\xba\xbe" + len(slices).to_bytes(4, "big")
    offset = 8 + 20 * len(slices)
    for item in slices:
        data += bytes(8) + offset.to_bytes(4, "big") + len(item).to_bytes(4, "big")
        data += bytes(4)
        offset += len(item)
    return data + b"".join(slices)


def test_bundled_native_code_reads_minimum_macos(tmp_path):
    thin = tmp_path / "thin.so"
    thin.write_bytes(macho(14, 0))
    legacy = tmp_path / "legacy.so"
    legacy.write_bytes(macho(11, 0, legacy=True))
    fat = tmp_path / "fat.so"
    fat.write_bytes(universal(macho(10, 12), macho(11, 0)))
    assert build.macho_minimum(thin) == (14, 0)
    assert build.macho_minimum(legacy) == (11, 0)
    assert build.macho_minimum(fat) == (11, 0)
    (tmp_path / "module.py").write_text("print('not native')\n")
    build.check_native_floor(tmp_path)


def test_bundled_native_code_newer_than_the_dht_floor_refuses(tmp_path):
    # A macOS 15 host picks libtorrent's macosx_15_0 wheel unless told otherwise.
    native = tmp_path / "libtorrent"
    native.mkdir()
    (native / "__init__.cpython-313-darwin.so").write_bytes(macho(15, 0))
    with pytest.raises(ValueError, match="macosx_14_0_arm64"):
        build.check_native_floor(tmp_path)


def test_bundled_notices_are_exact_upstream_libtorrent_licence(tmp_path):
    retained = build.copy_bundled_notices(tmp_path)
    assert set(retained) == {"LICENSE.libtorrent.txt"}
    text = (tmp_path / "LICENSE.libtorrent.txt").read_text()
    assert text.startswith("Copyright (c) 2003-2020, Arvid Norberg")
    assert "Redistributions in binary form must reproduce" in text
    record = json.loads(
        (Path(build.PROJECT) / "desktop/notices/SOURCES.json").read_bytes()
    )
    assert record["sources"]["LICENSE.libtorrent.txt"]["url"].endswith(
        "arvidn/libtorrent/v2.0.14/LICENSE"
    )
    # Linux appends the same notices to its runtime-notice set; never twice.
    with pytest.raises(ValueError, match="duplicate"):
        build.copy_bundled_notices(tmp_path)


@pytest.mark.parametrize("name", ["../outside", "/outside", "python/../../outside"])
def test_runtime_traversal_refuses_before_extraction(tmp_path, name):
    archive = tmp_path / "runtime.tar.gz"
    with tarfile.open(archive, "w:gz") as target:
        member = tarfile.TarInfo(name)
        member.size = 4
        target.addfile(member, io.BytesIO(b"data"))
    with pytest.raises(ValueError, match="unsafe runtime"):
        build.extract_python(archive, tmp_path / "runtime")
    assert not (tmp_path / "runtime").exists()


def test_runtime_external_symlink_refuses(tmp_path):
    archive = tmp_path / "runtime.tar.gz"
    with tarfile.open(archive, "w:gz") as target:
        link = tarfile.TarInfo("python/bin/python3")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../../outside"
        target.addfile(link)
    with pytest.raises(ValueError, match="link escapes"):
        build.extract_python(archive, tmp_path / "runtime")


def test_runtime_internal_link_and_regular_file(tmp_path):
    archive = tmp_path / "runtime.tar.gz"
    with tarfile.open(archive, "w:gz") as target:
        member = tarfile.TarInfo("python/bin/python3.13")
        member.size = 4
        target.addfile(member, io.BytesIO(b"code"))
        link = tarfile.TarInfo("python/bin/python3")
        link.type = tarfile.SYMTYPE
        link.linkname = "python3.13"
        target.addfile(link)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    build.extract_python(archive, alias / "runtime")
    assert (tmp_path / "runtime/python/bin/python3").read_bytes() == b"code"


@pytest.mark.parametrize("name", ["../outside", "/outside", "nested/file", "a\\b"])
def test_software_paths_refuse(tmp_path, name):
    archive = tmp_path / "software.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr(name, b"data")
    with pytest.raises(ValueError, match="unsafe software"):
        build.extract_library(archive, tmp_path / "software")
    assert not (tmp_path / "software").exists()


def candidate(tmp_path, monkeypatch):
    app = tmp_path / "Sovereign.app"
    resources = app / "Contents/Resources"
    resources.mkdir(parents=True)
    lock = json.loads((release.PROJECT / "desktop/macos/inputs.json").read_bytes())
    receipt = {
        "schema_version": "sovereign.desktop-build.v2",
        "source_revision": "a" * 40,
        "shell_source_revision": "a" * 40,
        "shell_source_dirty": False,
        "inputs": lock,
        "configuration": "release",
        "distribution": "ad-hoc-candidate",
    }
    (resources / "build-receipt.json").write_text(json.dumps(receipt))
    (app / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "ai.foxlight.sovereign",
                "CFBundleShortVersionString": lock["version"],
                "CFBundleVersion": lock["build_number"],
            }
        )
    )

    def run(arguments):
        if "rev-parse" in arguments:
            return "a" * 40
        return ""

    monkeypatch.setattr(release, "run", run)
    return app, receipt


@pytest.mark.parametrize(
    "field,value",
    [
        ("shell_source_dirty", True),
        ("source_revision", "b" * 40),
        ("shell_source_revision", "b" * 40),
        ("configuration", "debug"),
        ("inputs", {}),
    ],
)
def test_signing_refuses_unreviewed_recipe(tmp_path, monkeypatch, field, value):
    app, receipt = candidate(tmp_path, monkeypatch)
    receipt[field] = value
    (app / "Contents/Resources/build-receipt.json").write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="reviewed release recipe"):
        release.validate_candidate(app, "a" * 40, "ZP9J3449GF")


def test_signing_refuses_external_app_link(tmp_path, monkeypatch):
    app, _ = candidate(tmp_path, monkeypatch)
    (app / "Contents/Resources/outside").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="external symlink"):
        release.validate_candidate(app, "a" * 40, "ZP9J3449GF")
