"""The guided bootstrap must fail before elevation or package execution."""

import hashlib
import importlib.util
import io
import json
import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "linux_setup", ROOT / "scripts/install_sovereign_linux.py"
)
assert spec and spec.loader
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


@pytest.fixture
def configured(tmp_path, monkeypatch):
    key = tmp_path / "key.gpg"
    source = tmp_path / "sovereign.list"
    monkeypatch.setattr(setup, "KEY_PATH", key)
    monkeypatch.setattr(setup, "SOURCE_PATH", source)
    monkeypatch.setattr(setup, "KEY_SHA256", hashlib.sha256(b"key").hexdigest())
    monkeypatch.setattr(setup, "SOURCE", b"scoped source\n")
    original_stat = Path.stat

    def root_owned(path, **kwargs):
        value = original_stat(path, **kwargs)
        if path in (key, source):
            fields = list(value)
            fields[4] = 0
            return os.stat_result(fields)
        return value

    monkeypatch.setattr(Path, "stat", root_owned)
    return key, source


@pytest.fixture
def user_flow(monkeypatch, configured):
    monkeypatch.setattr(setup, "architecture", lambda: "amd64")
    monkeypatch.setattr(setup.os, "geteuid", lambda: 1000)
    monkeypatch.setattr("sys.argv", ["install_sovereign_linux.py"])
    commands = Mock()
    monkeypatch.setattr(setup, "run", commands)
    return commands


@pytest.mark.parametrize(
    "system,release,arch",
    [
        ("Darwin", {}, "amd64"),
        ("Linux", {"ID": "ubuntu", "VERSION_ID": "22.04"}, "amd64"),
        ("Linux", {"ID": "debian", "VERSION_ID": "24.04"}, "amd64"),
        ("Linux", {"ID": "ubuntu", "VERSION_ID": "24.04"}, "i386"),
    ],
)
def test_unqualified_platform_stops(system, release, arch, monkeypatch):
    monkeypatch.setattr(setup.platform, "system", lambda: system)
    monkeypatch.setattr(setup.platform, "freedesktop_os_release", lambda: release)
    monkeypatch.setattr(setup.subprocess, "check_output", lambda *a, **k: arch)
    with pytest.raises(setup.SetupError):
        setup.architecture()


@pytest.mark.parametrize("arch", ["amd64", "arm64"])
def test_native_architecture_detection(arch, monkeypatch):
    monkeypatch.setattr(setup.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        setup.platform,
        "freedesktop_os_release",
        lambda: {"ID": "ubuntu", "VERSION_ID": "24.04"},
    )
    monkeypatch.setattr(setup.subprocess, "check_output", lambda *a, **k: arch + "\n")
    assert setup.architecture() == arch


def test_cancel_and_check_never_download_or_elevate(user_flow, monkeypatch):
    download = Mock(side_effect=AssertionError("must not download"))
    monkeypatch.setattr(setup, "download", download)
    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert setup.main() == 0
    monkeypatch.setattr("sys.argv", ["installer", "--check"])
    assert setup.main() == 0
    download.assert_not_called()
    user_flow.assert_not_called()


@pytest.mark.parametrize("name", ["key", "package"])
def test_changed_download_never_elevates(name, user_flow, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "install")
    calls = []

    def corrupt(url, destination, expected, limit):
        calls.append(url)
        if name == "key" or len(calls) == 2:
            destination.write_bytes(b"changed bytes")
            setup.validate_file(destination, expected)

    monkeypatch.setattr(setup, "download", corrupt)
    assert setup.main() == 1
    assert len(calls) == (1 if name == "key" else 2)
    user_flow.assert_not_called()


def test_custom_configuration_is_retained(configured):
    key, source = configured
    key.write_bytes(b"operator key")
    source.write_bytes(b"operator source")
    with pytest.raises(setup.SetupError, match="custom settings"):
        setup.check_configuration()
    assert key.read_bytes() == b"operator key"
    assert source.read_bytes() == b"operator source"


def test_matching_manual_configuration_is_reused_without_rewrite(configured):
    key, source = configured
    key.write_bytes(b"key")
    source.write_bytes(setup.SOURCE)
    key.chmod(0o644)
    source.chmod(0o644)
    before = (key.stat().st_mtime_ns, source.stat().st_mtime_ns)
    setup.check_configuration()
    assert before == (key.stat().st_mtime_ns, source.stat().st_mtime_ns)
    assert key.read_bytes() == b"key" and source.read_bytes() == setup.SOURCE


@pytest.mark.parametrize(
    "kind", ["key-link", "source-link", "parent-link", "unsafe-mode"]
)
def test_unsafe_configuration_is_not_replaced(kind, configured, tmp_path, monkeypatch):
    key, source = configured
    key.write_bytes(b"key")
    source.write_bytes(setup.SOURCE)
    key.chmod(0o644)
    source.chmod(0o644)
    if kind.endswith("-link") and kind != "parent-link":
        path = key if kind == "key-link" else source
        path.unlink()
        path.symlink_to(tmp_path / "missing")
    elif kind == "parent-link":
        (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
        monkeypatch.setattr(setup, "SOURCE_PATH", tmp_path / "link/sovereign.list")
    else:
        key.chmod(0o666)
    with pytest.raises(setup.SetupError):
        setup.check_configuration()


def root_install(stage, monkeypatch):
    """Load the exact by-value helper with inert privileged system commands."""
    monkeypatch.setattr(setup.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        setup.platform,
        "freedesktop_os_release",
        lambda: {"ID": "ubuntu", "VERSION_ID": "24.04"},
    )
    monkeypatch.setattr(
        setup.subprocess,
        "check_output",
        lambda command, **k: (
            "amd64\n"
            if command[0].endswith("/dpkg")
            else "install ok installed " + setup.VERSION
        ),
    )
    monkeypatch.setitem(setup.PACKAGES, "amd64", hashlib.sha256(b"package").hexdigest())
    command = setup.system_command(stage, "amd64")
    assert command[:4] == ["/usr/bin/sudo", "/usr/bin/python3", "-I", "-c"]
    namespace = {"__name__": "root_helper_test"}
    exec(compile(command[4], "by-value-root-helper", "exec"), namespace)  # noqa: S102 - exact owned helper, system calls replaced before use
    return lambda: namespace["install"](json.loads(command[5]), str(stage))


@pytest.mark.parametrize("failure", ["digest", "update"])
def test_privileged_failure_never_executes_package(
    failure, configured, tmp_path, monkeypatch
):
    key, _ = configured
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / key.name).write_bytes(b"key")
    package = stage / ("foxlight-sovereign_" + setup.VERSION + "_amd64.deb")
    package.write_bytes(b"package")
    install = root_install(stage, monkeypatch)
    commands = Mock(side_effect=subprocess.CalledProcessError(1, "apt update"))
    monkeypatch.setattr(setup.subprocess, "run", commands)
    if failure == "digest":
        package.write_bytes(b"changed")
    with pytest.raises((RuntimeError, subprocess.CalledProcessError)):
        install()
    if failure == "digest":
        commands.assert_not_called()
        assert not key.exists()
    else:
        assert commands.call_count == 1
        assert commands.call_args.args[0][-1] == "update"
        assert "APT::Update::Error-Mode=any" in commands.call_args.args[0]
        assert "Acquire::Check-Valid-Until=true" in commands.call_args.args[0]


def test_user_replacement_during_update_cannot_change_installed_bytes(
    configured, tmp_path, monkeypatch
):
    key, source = configured
    stage = tmp_path / "incoming"
    stage.mkdir()
    (stage / key.name).write_bytes(b"key")
    package = stage / ("foxlight-sovereign_" + setup.VERSION + "_amd64.deb")
    package.write_bytes(b"package")
    install = root_install(stage, monkeypatch)
    consumed = []

    def apt(command, **kwargs):
        if command[-1] == "update":
            package.write_bytes(b"ATTACK: unverified maintainer scripts")
            (stage / key.name).write_bytes(b"ATTACK: different key")
        else:
            protected = Path(command[-1])
            assert protected.parent != stage
            assert stat.S_IMODE(protected.stat().st_mode) == 0o644
            assert stat.S_IMODE(protected.parent.stat().st_mode) == 0o755
            consumed.append(protected.read_bytes())

    monkeypatch.setattr(setup.subprocess, "run", apt)
    install()
    assert consumed == [b"package"]
    assert key.read_bytes() == b"key"
    assert source.read_bytes() == setup.SOURCE
    assert package.read_bytes().startswith(b"ATTACK:")


@pytest.mark.parametrize(
    "body,limit,expected,https",
    [
        (b"key", 2, b"key", True),
        (b"changed", 100, b"key", True),
        (b"key", 100, b"key", False),
    ],
)
def test_download_size_digest_and_https_are_required(
    body, limit, expected, https, tmp_path, monkeypatch
):
    response = io.BytesIO(body)
    response.url = "https://example.org/key" if https else "http://example.org/key"
    monkeypatch.setattr(setup.urllib.request, "urlopen", lambda *a, **k: response)
    with pytest.raises(setup.SetupError):
        setup.download(
            "https://example.org/key",
            tmp_path / "key",
            hashlib.sha256(expected).hexdigest(),
            limit,
        )


def test_verification_uses_reviewed_native_release():
    import json

    manifest = json.loads((ROOT / "desktop/linux/inputs.json").read_text())
    assert setup.VERSION == manifest["version"] + "-" + manifest["revision"]
    assert all(len(value) == 64 for value in setup.PACKAGES.values())
    assert setup.SOURCE == (
        b"deb [signed-by=/usr/share/keyrings/sovereign-archive-keyring.gpg] "
        b"https://apt.foxlight.ai sovereign main\n"
    )


@pytest.mark.parametrize("changed", [False, True])
def test_actual_documented_bootstrap_authenticates_before_execution(
    changed, monkeypatch
):
    import shlex

    guide = (ROOT / "docs/linux-distribution.md").read_text()
    block = (
        guide.split("## Guided setup (recommended)", 1)[1]
        .split("```sh\n", 1)[1]
        .split("```", 1)[0]
    )
    command = shlex.split(block)
    assert command[:3] == ["python3", "-I", "-c"]
    original = (ROOT / "scripts/install_sovereign_linux.py").read_bytes()
    assert hashlib.sha256(original).hexdigest() in command[3]
    response = io.BytesIO(original + b"changed" if changed else original)
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: response)
    child = Mock(return_value=0)
    monkeypatch.setattr("subprocess.call", child)
    with pytest.raises(SystemExit) as result:
        exec(compile(command[3], "documented-bootstrap", "exec"), {})  # noqa: S102 - owned documentation, inert download and process canaries
    if changed:
        assert "verification failed" in str(result.value)
        child.assert_not_called()
    else:
        assert result.value.code == 0
        assert child.call_args.args[0][1:3] == ["-I", "-c"]
        assert child.call_args.args[0][3] == original.decode()
