"""Release locks reject substituted assets, unsafe inputs and implicit side effects."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest


def _script(name):
    path = Path(__file__).parents[1] / "scripts" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


install_release = _script("install_release")
release_install = _script("release_install")


def assets(root: Path) -> Path:
    root.mkdir()
    wheel = "foxlight_model_preservation-0.12.0-py3-none-any.whl"
    (root / wheel).write_bytes(b"synthetic software wheel")
    (root / "foxlight_model_preservation-0.12.0.tar.gz").write_bytes(
        b"synthetic archive"
    )
    wheel_hash = hashlib.sha256((root / wheel).read_bytes()).hexdigest()
    text = f"foxlight-model-preservation==0.12.0 \\\n    --hash=sha256:{wheel_hash}\n"
    for name in ("requirements.lock", "requirements-peer.lock"):
        (root / name).write_text(text)
    (root / "install.py").write_text("# synthetic helper\n")
    record = {
        "schema_version": "foxlight.software-install.v1",
        "project": "foxlight-model-preservation",
        "version": "0.12.0",
        "source_revision": "a" * 40,
        "python_min": [3, 11],
        "wheel": wheel,
        "wheel_sha256": wheel_hash,
        "requirements": ["requirements.lock", "requirements-peer.lock"],
    }
    (root / "INSTALL.json").write_text(json.dumps(record))
    resign(root)
    return root


def resign(root: Path) -> None:
    files = sorted(p for p in root.iterdir() if p.name != "SHA256SUMS")
    (root / "SHA256SUMS").write_text(
        "".join(
            hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n"
            for p in files
        )
    )


def test_substituted_release_refuses_before_environment_creation(tmp_path, monkeypatch):
    root = assets(tmp_path / "release")
    (root / "install.py").write_text("substituted")

    def forbidden(*args, **kwargs):
        raise AssertionError("mutation before verification")

    monkeypatch.setattr(install_release.subprocess, "run", forbidden)
    with pytest.raises(ValueError, match="hash mismatch"):
        install_release.install(root, tmp_path / "new")
    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize(
    "extra", ["0" * 64 + "  ../outside\n", "0" * 64 + "  requirements.lock\n"]
)
def test_unsafe_or_duplicate_inventory_refuses(tmp_path, extra):
    root = assets(tmp_path / "release")
    with (root / "SHA256SUMS").open("a") as stream:
        stream.write(extra)
    with pytest.raises(ValueError, match="unsafe/duplicate"):
        install_release.validate_assets(root, "requirements.lock")


def test_symlink_and_occupied_install_are_not_adopted(tmp_path, monkeypatch):
    root = assets(tmp_path / "release")
    target = tmp_path / "important"
    target.write_text("keep")
    (root / "install.py").unlink()
    (root / "install.py").symlink_to(target)
    with pytest.raises(ValueError, match="regular files"):
        install_release.validate_assets(root, "requirements.lock")
    (root / "install.py").unlink()
    (root / "install.py").write_text("# helper")
    resign(root)
    with pytest.raises(ValueError, match="must be new"):
        install_release.install(root, target)
    assert target.read_text() == "keep"


@pytest.mark.parametrize(
    "spec",
    [
        "example>=1 --hash=sha256:" + "a" * 64,
        "example==1",
        "skulk-capability-sdk==0.5.0 --hash=sha256:" + "a" * 64,
        "example @ https://untrusted.invalid/pkg.whl --hash=sha256:" + "a" * 64,
    ],
)
def test_unlocked_private_or_source_dependencies_refuse(tmp_path, spec):
    with pytest.raises(ValueError):
        release_install.check_requirements(spec)
    root = assets(tmp_path / "release")
    with (root / "requirements.lock").open("a") as stream:
        stream.write(spec + "\n")
    resign(root)
    with pytest.raises(ValueError):
        install_release.validate_assets(root, "requirements.lock")


def test_platform_markers_and_uv_comments_are_preserved():
    release_install.check_requirements(
        'example==1 ; python_version >= "3.11" \\\n --hash=sha256:'
        + "a" * 64
        + "\n    # via a dependency\n"
    )


def test_missing_wheel_hash_and_unselected_profile_refuse(tmp_path):
    root = assets(tmp_path / "release")
    with pytest.raises(ValueError, match="profile"):
        install_release.validate_assets(root, "../requirements.lock")
    (root / "requirements.lock").write_text("other==1 --hash=sha256:" + "a" * 64 + "\n")
    resign(root)
    with pytest.raises(ValueError, match="exact release wheel"):
        install_release.validate_assets(root, "requirements.lock")


def test_offline_requires_explicit_wheelhouse_without_mutation(tmp_path, monkeypatch):
    root = assets(tmp_path / "release")

    def forbidden(*a, **kw):
        raise AssertionError("must remain offline and read-only")

    monkeypatch.setattr(install_release.subprocess, "run", forbidden)
    with pytest.raises(ValueError, match="wheelhouse"):
        install_release.install(root, tmp_path / "new", offline=True)
    assert not (tmp_path / "new").exists()


def test_offline_environment_creation_cannot_inherit_seed_downloads(
    tmp_path, monkeypatch
):
    root = assets(tmp_path / "release")
    wheelhouse = tmp_path / "wheels"
    wheelhouse.mkdir()
    monkeypatch.setenv("UV_VENV_SEED", "true")
    monkeypatch.setattr(install_release.shutil, "which", lambda _: "/explicit/uv")
    calls = []

    def capture(command, **kwargs):
        calls.append((command, kwargs["env"]))

    monkeypatch.setattr(install_release.subprocess, "run", capture)
    install_release.install(root, tmp_path / "new", offline=True, wheelhouse=wheelhouse)
    assert len(calls) == 3
    for command, environment in calls:
        assert "--offline" in command and "--no-cache" in command
        assert environment["UV_VENV_SEED"] == "false"
        assert environment["UV_PYTHON_DOWNLOADS"] == "never"
    assert "--no-index" in calls[1][0]


def test_generated_zip_has_a_complete_checked_installation_closure(
    tmp_path, monkeypatch
):
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname="foxlight-model-preservation"\nversion="0.12.0"\n'
    )
    output = tmp_path / "dist"
    output.mkdir()
    wheel = output / "foxlight_model_preservation-0.12.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "foxlight_model_preservation-0.12.0.dist-info/METADATA",
            "Name: foxlight-model-preservation\nVersion: 0.12.0\n",
        )
    (output / "foxlight_model_preservation-0.12.0.tar.gz").write_bytes(
        b"fixture source"
    )

    class Export:
        stdout = "example==1 \\\n --hash=sha256:" + "a" * 64 + "\n"

    monkeypatch.setattr(release_install.subprocess, "run", lambda *a, **kw: Export())
    release_install.generate(project, output, "a" * 40)
    install_release.validate_assets(output, "requirements.lock")
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    with zipfile.ZipFile(
        output / "foxlight-model-preservation-0.12.0-install.zip"
    ) as archive:
        assert all("/" not in name for name in archive.namelist())
        archive.extractall(extracted)
    install_release.validate_assets(extracted, "requirements-peer.lock")
    install_release.validate_assets(extracted, "requirements-mirror.lock")


def library_assets(tmp_path, monkeypatch):
    project = tmp_path / "library"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname="foxlight-preservation-library"\nversion="0.16.0"\n'
        '[project.optional-dependencies]\ndesktop=["foxlight-model-preservation==0.21.0"]\n'
    )
    output = tmp_path / "release"
    output.mkdir()
    own = output / "foxlight_preservation_library-0.16.0-py3-none-any.whl"
    core = tmp_path / "foxlight_model_preservation-0.21.0-py3-none-any.whl"
    for path, name, version in (
        (own, "foxlight-preservation-library", "0.16.0"),
        (core, "foxlight-model-preservation", "0.21.0"),
    ):
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                name.replace("-", "_") + "-" + version + ".dist-info/METADATA",
                "Name: " + name + "\nVersion: " + version + "\n",
            )
    (output / "foxlight_preservation_library-0.16.0.tar.gz").write_bytes(b"source")
    calls = []

    class Export:
        stdout = "example==1 --hash=sha256:" + "a" * 64 + "\n"

    def export(command, **kwargs):
        calls.append(command)
        return Export()

    monkeypatch.setattr(release_install.subprocess, "run", export)
    release_install.generate(project, output, "b" * 40, desktop_core_wheel=core)
    return output, calls


def test_desktop_bundle_binds_both_wheels_and_keeps_default_core_free(
    tmp_path, monkeypatch
):
    root, calls = library_assets(tmp_path, monkeypatch)
    record = install_release.validate_assets(root, "requirements-desktop.lock")
    assert record["schema_version"] == "foxlight.software-install.v2"
    assert record["components"][0]["source_revision"] == record["source_revision"]
    assert (
        "foxlight-model-preservation==" not in (root / "requirements.lock").read_text()
    )
    assert "--no-emit-package" in calls[1] and "desktop" in calls[1]
    install_release.validate_assets(root, "requirements.lock")
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    with zipfile.ZipFile(
        root / "foxlight-preservation-library-0.16.0-install.zip"
    ) as archive:
        archive.extractall(extracted)
    install_release.validate_assets(extracted, "requirements-desktop.lock")
    monkeypatch.setattr(install_release.shutil, "which", lambda _: "/verified/uv")
    monkeypatch.setattr(install_release.subprocess, "run", lambda *args, **kwargs: None)
    result = install_release.install(
        extracted, tmp_path / "installed", selected="requirements-desktop.lock"
    )
    assert len(result["components_installed"]) == 1
    assert (
        tmp_path / "installed/sovereign-release" / record["components"][0]["wheel"]
    ).is_file()
    default = install_release.install(extracted, tmp_path / "default")
    assert default["components_installed"] == []


@pytest.mark.parametrize(
    "mutation",
    [
        "different_source",
        "different_version",
        "different_project",
        "missing_wheel",
        "missing_core_lock",
        "second_hash",
        "default_enrolls_core",
    ],
)
def test_desktop_component_mismatch_refuses_before_mutation(
    tmp_path, monkeypatch, mutation
):
    root, _ = library_assets(tmp_path, monkeypatch)
    record = json.loads((root / "INSTALL.json").read_text())
    component = record["components"][0]
    if mutation == "different_source":
        component["source_revision"] = "c" * 40
    elif mutation == "different_version":
        component["version"] = "0.22.0"
    elif mutation == "different_project":
        component["project"] = "unrelated-model-verifier"
    elif mutation == "missing_wheel":
        (root / component["wheel"]).unlink()
    elif mutation == "missing_core_lock":
        (root / "requirements-desktop.lock").write_text(
            (root / "requirements.lock").read_text()
        )
    elif mutation == "second_hash":
        p = root / "requirements-desktop.lock"
        p.write_text(
            p.read_text().replace(
                component["wheel_sha256"],
                component["wheel_sha256"] + " --hash=sha256:" + "c" * 64,
            )
        )
    else:
        (root / "requirements.lock").write_text(
            (root / "requirements-desktop.lock").read_text()
        )
    (root / "INSTALL.json").write_text(json.dumps(record))
    # Even re-hashed malformed metadata must refuse; checksums are not authorship.
    resign(root)

    def forbidden(*args, **kwargs):
        raise AssertionError("installation mutation before component verification")

    monkeypatch.setattr(install_release.subprocess, "run", forbidden)
    with pytest.raises(ValueError):
        install_release.install(
            root,
            tmp_path / "new",
            selected="requirements.lock"
            if mutation == "default_enrolls_core"
            else "requirements-desktop.lock",
        )
    assert not (tmp_path / "new").exists()


def test_older_installation_records_cannot_silently_select_desktop(tmp_path):
    root = assets(tmp_path / "release")
    with pytest.raises(ValueError, match="profile"):
        install_release.validate_assets(root, "requirements-desktop.lock")
