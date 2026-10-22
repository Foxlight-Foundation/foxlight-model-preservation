"""Preview setup never adopts existing software or runs an unverified installer."""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest

from foxlight_preservation_library import preview


def test_occupied_directory_is_kept_without_network_or_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "existing"
    root.mkdir()
    kept = root / "important"
    kept.write_bytes(b"preserve-me")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Existing software must not trigger a network or installer call")

    monkeypatch.setattr(preview.httpx, "Client", forbidden)
    monkeypatch.setattr(preview.subprocess, "run", forbidden)
    with pytest.raises(preview.PreviewRefused, match="already exists"):
        preview.setup(root, resume=False, with_dashboard=False)
    assert kept.read_bytes() == b"preserve-me"
    assert not (root / "preview.json").exists()


def test_bad_installer_checksum_runs_nothing_and_retains_isolated_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "preview"
    original_client = httpx.Client
    monkeypatch.setattr(
        preview.httpx,
        "Client",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, content=b"unverified shell bytes")
            ),
            **kwargs,
        ),
    )

    def forbidden(
        *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        pytest.fail("Checksum mismatch executed a child")

    monkeypatch.setattr(preview.subprocess, "run", forbidden)
    with pytest.raises(preview.PreviewRefused, match="checksum"):
        preview.setup(root, resume=False, with_dashboard=False)
    assert not (root / "install-core.sh").exists()
    assert str(root / "model-store") in (root / "core" / "skulk.yaml").read_text()
    state = preview._receipt(root)
    monkeypatch.setenv("VIRTUAL_ENV", "/an-existing-environment")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/another-existing-environment")
    environment = preview._environment(root, state)
    assert "VIRTUAL_ENV" not in environment
    assert environment["UV_PROJECT_ENVIRONMENT"] == str(root / "core" / ".venv")
    assert environment["SKULK_HOME"] == str(root / "state")
    assert environment["SKULK_MODELS_PATH"] == ""
    assert environment["SKULK_LIBP2P_NAMESPACE"] == state["namespace"]
    assert root.stat().st_mode & 0o077 == 0
    assert (root / "preview.json").stat().st_mode & 0o077 == 0
    root.rename(tmp_path / "original-preview")
    root.mkdir(mode=0o700)
    with pytest.raises((OSError, preview.PreviewRefused)):
        preview.setup(root, resume=True, with_dashboard=False)


def test_setup_refuses_a_running_preview_before_network_or_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    root = tmp_path / "preview"
    root.mkdir(mode=0o700)
    info = root.stat()
    preview._write(
        root / "preview.json",
        json.dumps(
            {
                "format": 1,
                "core_revision": preview.CORE_REVISION,
                "device": info.st_dev,
                "inode": info.st_ino,
                "namespace": "a" * 64,
            }
        ),
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("A running preview must exclude setup")

    monkeypatch.setattr(preview.httpx, "Client", forbidden)
    with preview._preview_lock(root, exclusive=False):
        with pytest.raises(preview.PreviewRefused, match="in use"):
            preview.setup(root, resume=True, with_dashboard=False)
    assert not (root / "core").exists()


def test_installer_keeps_full_private_log_and_only_profile_launch_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = tmp_path / "installer.sh"
    script.write_text(
        "printf '[skulk-install] dependencies ready\\n"
        "[skulk-install] install complete. Start a node with:\\n"
        "[skulk-install] wrong generic launch\\n'\n"
    )
    preview._install_core(["bash", str(script)], tmp_path, {})
    shown = capsys.readouterr().out
    assert "dependencies ready" in shown
    assert "wrong generic launch" not in shown
    assert "wrong generic launch" in (tmp_path / "installation.log").read_text()
    assert (tmp_path / "installation.log").stat().st_mode & 0o077 == 0
