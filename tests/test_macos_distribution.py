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
    build.extract_python(archive, tmp_path / "runtime")
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
