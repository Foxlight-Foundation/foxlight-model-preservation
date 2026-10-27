"""Native commands retain argument, interpreter and relocation boundaries."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/package_desktop.py"
SPEC = importlib.util.spec_from_file_location("native_package", SCRIPT)
assert SPEC and SPEC.loader
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


def bundle(tmp_path: Path) -> Path:
    """Assemble a tiny installed layout with a real isolated Python interpreter."""
    contents = tmp_path / "Original.app/Contents"
    resources = contents / "Resources"
    (resources / "python/bin").mkdir(parents=True)
    (resources / "python/bin/python3").symlink_to(sys.executable)
    site = resources / "site-packages"
    (site / "bin").mkdir(parents=True)
    for name in ("model-preserve", "foxlight-sovereign", "unbundled-core-client"):
        (site / "bin" / name).write_text("#!/missing/external/python\n")
    for name, module in (
        ("foxlight_model_preservation", "cli.py"),
        ("foxlight_preservation_library", "desktop.py"),
    ):
        (site / name).mkdir()
        (site / name / "__init__.py").touch()
        (site / name / module).write_text(
            "import importlib.util, json, sys\n"
            "def main():\n"
            " print(json.dumps({'argv': sys.argv, 'isolated': sys.flags.isolated, "
            "'bytecode': sys.dont_write_bytecode, "
            "'outside_absent': importlib.util.find_spec('outside_marker') is None}))\n"
            " return 0\n"
        )
    hashes = package.install_native_cli(contents)
    assert len(hashes) == 3
    assert set(path.name for path in (site / "bin").iterdir()) == {
        "model-preserve",
        "foxlight-sovereign",
    }
    return contents.parent


@pytest.mark.parametrize("command", ["model-preserve", "foxlight-sovereign"])
def test_relocated_native_command_ignores_ambient_python(tmp_path, command):
    app = bundle(tmp_path)
    installed = tmp_path / "User Apps Ω/Renamed app.app"
    installed.parent.mkdir()
    shutil.move(app, installed)
    outside = tmp_path / "untrusted-working-directory"
    outside.mkdir()
    flag = tmp_path / "ambient-code-ran"
    (outside / "outside_marker.py").touch()
    (outside / "sitecustomize.py").write_text(
        "import os, pathlib\npathlib.Path(os.environ['TEST_FLAG']).touch()\n"
    )
    alias = tmp_path / "command-alias"
    alias.symlink_to(installed / "Contents/Resources/bin" / command)
    sentinel = tmp_path / "shell-interpolation"
    arguments = ["a filename with spaces Ω", "$(touch " + str(sentinel) + ")", "--help"]
    for executable in (
        alias,
        installed / "Contents/Resources/site-packages/bin" / command,
    ):
        result = subprocess.run(  # noqa: S603 - execute only our installed fixture
            [str(executable), *arguments],
            cwd=outside,
            env={
                **os.environ,
                "PATH": "/usr/bin:/bin",
                "PYTHONHOME": str(outside / "not-a-runtime"),
                "PYTHONPATH": str(outside),
                "__PYVENV_LAUNCHER__": str(outside / "not-a-runtime"),
                "TEST_FLAG": str(flag),
            },
            capture_output=True,
            text=True,
            check=True,
        )
        report = json.loads(result.stdout)
        assert report == {
            "argv": [command, *arguments],
            "isolated": 1,
            "bytecode": True,
            "outside_absent": True,
        }
        assert not flag.exists() and not sentinel.exists()
    assert not list(installed.rglob("__pycache__"))


def test_missing_bundled_runtime_never_falls_back(tmp_path):
    app = bundle(tmp_path)
    (app / "Contents/Resources/python/bin/python3").unlink()
    result = subprocess.run(  # noqa: S603 - execute only our installed fixture
        [str(app / "Contents/Resources/bin/model-preserve"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "bundled Sovereign runtime is missing" in result.stderr


def test_unexpected_console_directory_refuses_without_deleting_it(tmp_path):
    contents = tmp_path / "App.app/Contents"
    nested = contents / "Resources/site-packages/bin/unexpected"
    nested.mkdir(parents=True)
    (nested / "retained").write_bytes(b"keep")
    with pytest.raises(ValueError, match="unexpected directory"):
        package.install_native_cli(contents)
    assert (nested / "retained").read_bytes() == b"keep"


def test_generated_sidecar_disappearance_does_not_block_native_commands(
    tmp_path, monkeypatch
):
    contents = tmp_path / "App.app/Contents"
    internal = contents / "Resources/site-packages/bin"
    internal.mkdir(parents=True)
    stub = internal / "generated-command"
    sidecar = internal / "._generated-command"
    stub.write_text("#!/external/python\n")
    sidecar.write_bytes(b"filesystem metadata")
    original_iterdir, original_unlink = Path.iterdir, Path.unlink

    def snapshot(path):
        return iter([stub, sidecar]) if path == internal else original_iterdir(path)

    def remove_with_sidecar(path, *, missing_ok=False):
        if path == stub:
            original_unlink(sidecar)
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "iterdir", snapshot)
    monkeypatch.setattr(Path, "unlink", remove_with_sidecar)
    hashes = package.install_native_cli(contents)
    assert len(hashes) == 3
    assert {path.name for path in original_iterdir(internal)} == {
        "model-preserve",
        "foxlight-sovereign",
    }
    assert all(path.is_symlink() for path in original_iterdir(internal))
