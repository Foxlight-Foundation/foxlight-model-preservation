"""Preview setup never adopts existing software or runs an unverified installer."""

from __future__ import annotations

import hashlib
import json
import socket
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
    assert (root / "model-store").is_dir()
    assert (root / "model-store").stat().st_mode & 0o077 == 0
    config = (root / "core" / "skulk.yaml").read_text()
    assert str(root / "model-store") in config
    assert str(root / "staging") in config
    state = preview._receipt(root)
    monkeypatch.setenv("VIRTUAL_ENV", "/an-existing-environment")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/another-existing-environment")
    environment = preview._environment(root, state)
    assert "VIRTUAL_ENV" not in environment
    assert environment["UV_PROJECT_ENVIRONMENT"] == str(root / "core" / ".venv")
    assert environment["SKULK_HOME"] == str(root / "state")
    assert environment["SKULK_MODELS_PATH"] == str(root / "staging")
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
    (root / "model-store").mkdir(mode=0o700)
    store_info = (root / "model-store").stat()
    info = root.stat()
    preview._write(
        root / "preview.json",
        json.dumps(
            {
                "format": 1,
                "core_revision": preview.CORE_REVISION,
                "device": info.st_dev,
                "inode": info.st_ino,
                "store_device": store_info.st_dev,
                "store_inode": store_info.st_ino,
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


def managed_profile(root: Path) -> None:
    """Create a private setup receipt/configuration without running software."""
    root.mkdir(mode=0o700)
    (root / "model-store").mkdir(mode=0o700)
    store_info = (root / "model-store").stat()
    info = root.stat()
    preview._write(
        root / "preview.json",
        json.dumps(
            {
                "format": 1,
                "core_revision": preview.CORE_REVISION,
                "device": info.st_dev,
                "inode": info.st_ino,
                "store_device": store_info.st_dev,
                "store_inode": store_info.st_ino,
                "namespace": "a" * 64,
            }
        ),
    )
    (root / "core").mkdir(mode=0o700)
    preview._write(
        root / "core" / "skulk.yaml",
        json.dumps(
            {
                "model_store": {
                    "store_host": socket.gethostname().split(".")[0],
                    "store_port": 13415,
                    "store_path": str(root / "model-store"),
                    "staging": {"node_cache_path": str(root / "staging")},
                }
            }
        ),
    )


@pytest.mark.parametrize("changed", ["store", "staging", "node_override"])
def test_changed_runtime_configuration_is_refused_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    root = tmp_path / "preview"
    managed_profile(root)
    path = root / "core" / "skulk.yaml"
    configuration = json.loads(path.read_text())
    store = configuration["model_store"]
    if changed == "store":
        store["store_path"] = str(tmp_path / "existing-store")
    elif changed == "staging":
        store["staging"]["node_cache_path"] = str(tmp_path / "existing-staging")
    else:
        store["nodes"] = {"other": {"staging": {"node_cache_path": "/other-store"}}}
    path.write_text(json.dumps(configuration))

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Changed ignored configuration must not launch any child")

    monkeypatch.setattr(preview.subprocess, "run", forbidden)
    with pytest.raises(preview.PreviewRefused, match="storage settings"):
        preview.launch(root, "core")
    assert not (tmp_path / "existing-store").exists()
    assert not (tmp_path / "existing-staging").exists()


def test_core_saved_yaml_can_retain_other_settings_inside_profile(
    tmp_path: Path,
) -> None:
    root = tmp_path / "preview"
    managed_profile(root)
    path = root / "core" / "skulk.yaml"
    configuration = json.loads(path.read_text())
    configuration["logging"] = {"enabled": False}
    configuration["model_store"]["store_http_host"] = "127.0.0.1"
    path.write_text(preview.yaml.safe_dump(configuration))
    preview._validate_configuration(root)


def test_installer_configuration_rewrite_is_refused_before_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "preview"
    managed_profile(root)
    script = b"# checksum-pinned fixture installer\n"
    monkeypatch.setattr(
        preview, "CORE_INSTALLER_SHA256", hashlib.sha256(script).hexdigest()
    )
    original_client = httpx.Client
    monkeypatch.setattr(
        preview.httpx,
        "Client",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, content=script)
            ),
            **kwargs,
        ),
    )

    def rewrite(*args: object, **kwargs: object) -> None:
        path = root / "core" / "skulk.yaml"
        configuration = json.loads(path.read_text())
        configuration["model_store"]["store_path"] = "/existing-store"
        path.write_text(json.dumps(configuration))

    monkeypatch.setattr(preview, "_install_core", rewrite)
    monkeypatch.setattr(
        preview.subprocess,
        "check_output",
        lambda *args, **kwargs: preview.CORE_REVISION,
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("A rewritten profile must not install Library or report success")

    monkeypatch.setattr(preview.subprocess, "run", forbidden)
    with pytest.raises(preview.PreviewRefused, match="storage settings"):
        preview.setup(root, resume=True, with_dashboard=False)


def test_store_alias_cannot_redirect_an_unchanged_profile_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "preview"
    managed_profile(root)
    other = tmp_path / "existing-store"
    other.mkdir()
    kept = other / "keep"
    kept.write_bytes(b"original-operator-data")
    (root / "model-store").rename(root / "retained-store")
    (root / "model-store").symlink_to(other, target_is_directory=True)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("A redirected Model Store must not launch any child")

    monkeypatch.setattr(preview.subprocess, "run", forbidden)
    with pytest.raises(preview.PreviewRefused, match="Model Store"):
        preview.launch(root, "core")
    assert kept.read_bytes() == b"original-operator-data"
    assert list(other.iterdir()) == [kept]


@pytest.mark.parametrize("replaced", [False, True])
def test_resume_never_recreates_or_adopts_a_missing_or_replaced_model_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replaced: bool
) -> None:
    root = tmp_path / "preview"
    managed_profile(root)
    store = root / "model-store"
    store.rename(root / "retained-store")
    if replaced:
        store.mkdir(mode=0o700)
        (store / "existing").write_bytes(b"preserve-existing-data")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail(
            "A missing or replaced store must not start installer network calls"
        )

    monkeypatch.setattr(preview.httpx, "Client", forbidden)
    with pytest.raises(preview.PreviewRefused, match="Model Store"):
        preview.setup(root, resume=True, with_dashboard=False)
    if replaced:
        assert (store / "existing").read_bytes() == b"preserve-existing-data"
    else:
        assert not store.exists()


@pytest.mark.parametrize("suffix", [".git", ""])
def test_public_git_preview_reinstalls_its_exact_library_commit(
    monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    revision = "a" * 40

    class Installed:
        version = "0.7.0"

        def read_text(self, name: str) -> str:
            assert name == "direct_url.json"
            return json.dumps(
                {
                    "url": preview.PUBLIC_REPOSITORY.removesuffix(".git") + suffix,
                    "subdirectory": "capability",
                    "vcs_info": {
                        "vcs": "git",
                        "commit_id": revision,
                        "requested_revision": "main",
                    },
                }
            )

    monkeypatch.setattr(preview, "distribution", lambda _: Installed())
    assert (
        preview._library_source()
        == "git+"
        + preview.PUBLIC_REPOSITORY
        + "@"
        + revision
        + "#subdirectory=capability"
    )


def test_wheel_preview_uses_its_current_release_not_an_older_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Installed:
        version = "0.7.0"

        def read_text(self, name: str) -> None:
            return None

    monkeypatch.setattr(preview, "distribution", lambda _: Installed())
    assert preview._library_source().endswith(
        "library-v0.7.0/foxlight_preservation_library-0.7.0-py3-none-any.whl"
    )


@pytest.mark.parametrize(
    "url,revision,subdirectory",
    [
        ("https://unrecognized.invalid/library.git", "a" * 40, "capability"),
        (preview.PUBLIC_REPOSITORY, "main", "capability"),
        (preview.PUBLIC_REPOSITORY, "a" * 40, "other"),
    ],
)
def test_preview_refuses_unknown_library_source_identity(
    monkeypatch: pytest.MonkeyPatch, url: str, revision: str, subdirectory: str
) -> None:
    class Installed:
        version = "0.7.0"

        def read_text(self, name: str) -> str:
            return json.dumps(
                {
                    "url": url,
                    "subdirectory": subdirectory,
                    "vcs_info": {"vcs": "git", "commit_id": revision},
                }
            )

    monkeypatch.setattr(preview, "distribution", lambda _: Installed())
    with pytest.raises(preview.PreviewRefused):
        preview._library_source()
