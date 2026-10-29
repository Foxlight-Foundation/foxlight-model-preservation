"""Graphical setup must retain the reviewed installer boundary and user consent."""

import importlib.util
import io
import os
import runpy
import subprocess
import sys
import threading
import types
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    spec = importlib.util.spec_from_file_location("graphical_setup_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_package_has_no_install_hooks_or_implicit_privilege_grant(tmp_path):
    builder = load(ROOT / "scripts/build_linux_setup.py")
    tree = builder.stage(tmp_path / "package")
    assert {p.name for p in (tree / "DEBIAN").iterdir()} == {"control"}
    assert "Architecture: all\n" in (tree / "DEBIAN/control").read_text()
    assert not (tree / "etc").exists()
    policy = ET.parse(  # noqa: S314 - owned fixed policy, no external input
        tree / "usr/share/polkit-1/actions/ai.foxlight.sovereign.setup.policy"
    )
    (action,) = policy.getroot().findall("action")
    assert action.attrib == {"id": "ai.foxlight.sovereign.setup.install"}
    assert {p.tag: p.text for p in action.find("defaults")} == {
        "allow_any": "auth_admin",
        "allow_inactive": "auth_admin",
        "allow_active": "auth_admin",
    }
    annotations = action.findall("annotate")
    assert len(annotations) == 1
    assert annotations[0].attrib == {"key": "org.freedesktop.policykit.exec.path"}
    assert annotations[0].text == "/usr/libexec/sovereign-setup-install"
    assert (tree / builder.SHARE / "installer.py").read_bytes() == (
        ROOT / "scripts/install_sovereign_linux.py"
    ).read_bytes()
    assert (tree / "usr/bin/sovereign-setup").stat().st_mode & 0o777 == 0o755
    assert (
        "Terminal=false"
        in (
            tree / "usr/share/applications/ai.foxlight.Sovereign.Setup.desktop"
        ).read_text()
    )


@pytest.mark.parametrize("mask", [0o022, 0o077])
def test_package_modes_do_not_depend_on_builder_umask(tmp_path, mask):
    builder = load(ROOT / "scripts/build_linux_setup.py")
    previous = os.umask(mask)
    try:
        tree = builder.stage(tmp_path / "package")
    finally:
        os.umask(previous)
    assert tree.stat().st_mode & 0o777 == 0o755
    assert (tree / "DEBIAN/control").stat().st_mode & 0o777 == 0o644
    for path in tree.rglob("*"):
        if path.is_dir():
            assert path.stat().st_mode & 0o777 == 0o755


def test_manual_qualification_cannot_publish_provenance():
    workflow = (
        ROOT / ".github/workflows/linux-graphical-setup-release.yml"
    ).read_text()
    attestation = workflow.split("  attest:\n", 1)[1].split("  publish:\n", 1)[0]
    assert attestation.splitlines()[0].strip() == "if: github.event_name == 'push'"


@pytest.fixture
def gui(monkeypatch):
    gtk = types.SimpleNamespace(Window=object)
    glib = types.SimpleNamespace(idle_add=lambda call, *args: call(*args))
    monkeypatch.setitem(
        sys.modules, "gi", types.SimpleNamespace(require_version=Mock())
    )
    monkeypatch.setitem(
        sys.modules, "gi.repository", types.SimpleNamespace(Gtk=gtk, GLib=glib)
    )
    original = runpy.run_path
    setup = original(str(ROOT / "scripts/install_sovereign_linux.py"))
    setup["architecture"] = lambda: "amd64"
    setup["check_configuration"] = Mock()
    setup["download"] = Mock()
    monkeypatch.setattr(runpy, "run_path", lambda _: setup)
    module = load(ROOT / "desktop/linux-setup/setup.py")
    module.download = setup["download"]
    window = object.__new__(module.SetupWindow)
    window.busy = True
    window.authorizing = False
    window.cancelled = threading.Event()
    window.cancel = Mock()
    window.status = Mock()
    window.finished = Mock()
    window.append_log = Mock()
    return module, window


def test_blocked_download_is_promptly_terminated_before_elevation(
    gui, monkeypatch, tmp_path
):
    module, _ = gui
    actual_download = load(ROOT / "desktop/linux-setup/setup.py").download
    cancel = threading.Event()
    process = Mock()
    process.__enter__ = Mock(return_value=process)
    process.__exit__ = Mock(return_value=False)

    def communicate(**kwargs):
        if not cancel.is_set():
            cancel.set()
            raise subprocess.TimeoutExpired("owned download", 0.25)
        return ("", None)

    process.communicate.side_effect = communicate
    child = Mock(return_value=process)
    monkeypatch.setattr(module.subprocess, "Popen", child)
    assert (
        actual_download(
            "https://example.org/file", tmp_path / "file", "a" * 64, 10, cancel
        )
        is False
    )
    process.terminate.assert_called_once()
    command = child.call_args.args[0]
    assert command[:4] == ["/usr/bin/python3", "-I", "-B", "-c"]
    assert "pkexec" not in " ".join(command)
    assert child.call_args.kwargs["stdin"] is subprocess.DEVNULL


def test_cancellation_wins_permission_boundary(gui, monkeypatch):
    module, window = gui
    window.cancelled.set()
    child = Mock(side_effect=AssertionError("must not elevate"))
    monkeypatch.setattr(module.subprocess, "Popen", child)
    window.install()
    module.SETUP["download"].assert_not_called()
    window.finished.assert_called_once_with("cancelled", "")
    child.assert_not_called()


def test_changed_download_is_never_given_to_administrator(gui, monkeypatch):
    module, window = gui
    module.SETUP["download"].side_effect = RuntimeError("Verification failed")
    child = Mock()
    monkeypatch.setattr(module.subprocess, "Popen", child)
    window.install()
    child.assert_not_called()
    assert window.finished.call_args.args == ("error", "Verification failed")


@pytest.mark.parametrize(
    "result,outcome", [(126, "error"), (127, "error"), (1, "error"), (0, "ready")]
)
def test_os_permission_failure_cannot_report_success(result, outcome, gui, monkeypatch):
    module, window = gui
    process = Mock()
    process.stdout = io.StringIO("2 of 3\n3 of 3\n")
    process.wait.return_value = result
    process.__enter__ = Mock(return_value=process)
    process.__exit__ = Mock(return_value=False)
    child = Mock(return_value=process)
    monkeypatch.setattr(module.subprocess, "Popen", child)
    window.install()
    command = child.call_args.args[0]
    assert command[:3] == [
        "/usr/bin/pkexec",
        "--disable-internal-agent",
        "/usr/libexec/sovereign-setup-install",
    ]
    assert len(command) == 4  # No caller-supplied code, configuration or pins.
    assert module.SETUP["download"].call_count == 2
    assert child.call_args.kwargs["stdin"] is subprocess.DEVNULL
    assert window.finished.call_args.args[0] == outcome


def test_cancel_is_checked_on_main_thread_before_elevation(gui, monkeypatch):
    module, window = gui
    original = module.GLib.idle_add

    def cancel_before_stage(call, *args):
        if call == window.permission_stage:
            window.cancelled.set()
        return original(call, *args)

    monkeypatch.setattr(module.GLib, "idle_add", cancel_before_stage)
    child = Mock()
    monkeypatch.setattr(module.subprocess, "Popen", child)
    window.install()
    assert module.SETUP["download"].call_count == 2
    child.assert_not_called()
    assert not window.authorizing
    window.finished.assert_called_once_with("cancelled", "")


def test_privileged_helper_does_not_accept_user_code_or_pins(monkeypatch, tmp_path):
    helper = (ROOT / "desktop/linux-setup/install").read_text()
    namespace = {"__name__": "__main__"}
    monkeypatch.setattr(sys, "argv", ["helper", str(tmp_path), "evil-config"])
    monkeypatch.setattr("os.geteuid", lambda: 0)
    loader = Mock()
    monkeypatch.setattr(runpy, "run_path", loader)
    with pytest.raises(SystemExit, match="authorization"):
        exec(compile(helper, "package-root-helper", "exec"), namespace)  # noqa: S102 - owned helper, invalid arguments must stop before loading
    loader.assert_not_called()
