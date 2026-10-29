#!/usr/bin/python3
"""Exercise installed setup and real polkit dialogs on an owned ephemeral desktop.

Run as the disposable runner user inside a PAM/logind session, D-Bus and Xvfb.
The temporary password is read only in memory, never retained in evidence.
No fake authorization rules or alternative package-install calls are used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import gi
import pyatspi

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib  # noqa: E402

if TYPE_CHECKING:
    from gi.repository import Atspi


def require(condition: bool, message: str) -> None:
    """Keep qualification assertions enabled with optimized Python too."""
    if not condition:
        raise RuntimeError(message)


def nodes() -> Iterator[Atspi.Accessible]:
    """Read bounded accessibility state without reading password contents."""
    pending = [(pyatspi.Registry.getDesktop(0), 0)]
    count = 0
    while pending and count < 12000:
        node, depth = pending.pop()
        count += 1
        try:
            yield node
            if depth < 35:
                pending.extend((child, depth + 1) for child in node if child)
        except (GLib.Error, RuntimeError):
            continue


def visible(text: str) -> bool:
    """Find a human-visible label without exporting a native text field."""
    for node in nodes():
        try:
            if text in (node.name or "") and node.getState().contains(
                pyatspi.STATE_SHOWING
            ):
                return True
        except (GLib.Error, RuntimeError):
            continue
    return False


def wait(
    check: Callable[[], bool],
    description: str,
    timeout: int = 120,
    abort_on_error: bool = False,
) -> None:
    """Wait for a meaningful screen state, or fail without bypassing the step."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        if abort_on_error and visible("Setup needs attention."):
            raise RuntimeError(
                "Setup stopped; retained native details describe the failure"
            )
        time.sleep(0.25)
    raise RuntimeError("Timed out: " + description)


def click(name: str) -> None:
    """Activate an exact visible, enabled native button."""
    for node in nodes():
        try:
            state = node.getState()
            if (
                node.name == name
                and node.getRole() == pyatspi.ROLE_PUSH_BUTTON
                and state.contains(pyatspi.STATE_SHOWING)
                and state.contains(pyatspi.STATE_ENABLED)
            ):
                require(node.queryAction().doAction(0), "Button refused: " + name)
                return
        except (GLib.Error, RuntimeError):
            continue
    raise RuntimeError("Button not available: " + name)


def password_entry() -> Atspi.Accessible | None:
    """Find the OS agent's password field; never read or log its value."""
    for node in nodes():
        try:
            if (
                node.getRole() == pyatspi.ROLE_PASSWORD_TEXT
                and node.getState().contains(pyatspi.STATE_SHOWING)
            ):
                return node
        except (GLib.Error, RuntimeError):
            continue
    return None


def snapshot(root: Path, name: str) -> None:
    """Retain only native rendered pixels, with no password entered."""
    screen = Gdk.get_default_root_window()
    image = Gdk.pixbuf_get_from_window(
        screen, 0, 0, screen.get_width(), screen.get_height()
    )
    image.savev(str(root / (name + ".png")), "png", [], [])


def main() -> None:
    """Check review cancellation, denied permission, successful setup and launch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--password-file", type=Path, required=True)
    args = parser.parse_args()
    root = args.directory.resolve()
    root.mkdir(parents=True, exist_ok=False)
    password = args.password_file.read_text().strip()
    args.password_file.unlink()
    require(os.geteuid() != 0, "qualification must run as the ordinary user")
    require(bool(os.environ.get("DISPLAY")), "owned display required")
    key = Path("/usr/share/keyrings/sovereign-archive-keyring.gpg")
    source = Path("/etc/apt/sources.list.d/sovereign.list")
    require(
        not key.exists() and not source.exists(), "fresh software settings required"
    )
    env = {
        **os.environ,
        "NO_AT_BRIDGE": "0",
        "GTK_MODULES": "gail:atk-bridge",
        "GDK_BACKEND": "x11",
    }
    with (root / "native.log").open("wb") as log:
        agent = subprocess.Popen(  # noqa: S603 - Ubuntu's real authentication agent
            ["/usr/lib/policykit-1-gnome/polkit-gnome-authentication-agent-1"],
            env=env,
            stdout=log,
            stderr=log,
        )
        app = None
        try:
            app = subprocess.Popen(
                ["/usr/bin/sovereign-setup"], env=env, stdout=log, stderr=log
            )  # noqa: S603
            wait(lambda: visible("Ready for Ubuntu 24.04"), "initial review")
            snapshot(root, "01-review")
            click("Cancel")
            require(app.wait(timeout=15) == 0, "review cancellation did not close")
            require(not key.exists() and not source.exists(), "cancel changed settings")
            print("Native review cancellation preserved settings.", flush=True)
            app = subprocess.Popen(
                ["/usr/bin/sovereign-setup"], env=env, stdout=log, stderr=log
            )  # noqa: S603
            wait(lambda: visible("Ready for Ubuntu 24.04"), "second review")
            click("Install Sovereign")
            wait(lambda: password_entry() is not None, "real Ubuntu permission dialog")
            require(agent.poll() is None, "Ubuntu authentication agent exited")
            require(
                visible("Password for packer:"),
                "unexpected disposable image administrator",
            )
            require(
                visible("install Sovereign and enable its signed"),
                "friendly OS permission message missing",
            )
            snapshot(root, "02-ubuntu-permission")
            click("Cancel")
            wait(lambda: visible("Ubuntu did not grant"), "permission refusal feedback")
            require(
                not key.exists() and not source.exists(),
                "denied permission changed settings",
            )
            snapshot(root, "03-permission-cancelled")
            print("Real OS permission cancellation preserved settings.", flush=True)
            click("Try again")
            wait(lambda: password_entry() is not None, "retry permission dialog")
            entry = password_entry()
            require(entry is not None, "password entry disappeared")
            require(visible("Password for packer:"), "administrator identity changed")
            entry.queryEditableText().setTextContents(password)
            password = ""
            click("Authenticate")
            wait(
                lambda: visible("Sovereign is ready."),
                "verified GUI installation",
                timeout=480,
                abort_on_error=True,
            )
            snapshot(root, "04-ready")
            bounds = None
            for node in nodes():
                if (
                    node.name == "Install Sovereign"
                    and node.getRole() == pyatspi.ROLE_FRAME
                ):
                    bounds = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
            require(
                bounds is not None and bounds.width <= 1024 and bounds.height <= 720,
                "setup's ready action does not fit a small laptop screen",
            )
            require(
                key.exists() and source.exists(), "verified software settings missing"
            )
            require(
                hashlib.sha256(key.read_bytes()).hexdigest()
                == "f9a76cf4131603e3b7d905868efcf53f7ebea98ccdbfaf0d47ae0ac825cfd338",
                "wrong software key",
            )
            installed = subprocess.check_output(  # noqa: S603
                ["/usr/bin/dpkg-query", "-W", "-f=${Version}", "foxlight-sovereign"],
                text=True,
            )
            require(installed == "0.1.0-3", "wrong app installed")
            click("Open Sovereign")
            require(app.wait(timeout=15) == 0, "setup did not hand off to app")
            app = None
            wait(
                lambda: visible("Find a model. Keep your own copy."),
                "installed app launch",
            )
            snapshot(root, "05-open-sovereign")
            # Stop only the exact app identity launched on this disposable host.
            owned = []
            for item in Path("/proc").iterdir():
                try:
                    if (
                        item.name.isdigit()
                        and item.stat().st_uid == os.geteuid()
                        and b"/opt/foxlight/sovereign/sovereign-linux.py\x00"
                        in (item / "cmdline").read_bytes()
                    ):
                        owned.append(int(item.name))
                except (OSError, ValueError):
                    continue
            require(len(owned) == 1, "expected one normal-user app instance")
            os.kill(owned[0], signal.SIGTERM)
            wait(
                lambda: not Path("/proc", str(owned[0])).exists(),
                "cooperative app shutdown",
            )
        except Exception:
            snapshot(root, "failure")
            texts = []
            for node in nodes():
                try:
                    if node.getRole() == pyatspi.ROLE_PASSWORD_TEXT:
                        continue
                    if node.name:
                        texts.append(node.name)
                    if (
                        node.getRole() == pyatspi.ROLE_TEXT
                        and "Text" in node.get_interfaces()
                    ):
                        texts.append(node.queryText().getText(0, 65536))
                except (GLib.Error, RuntimeError):
                    continue
            (root / "failure-visible.txt").write_text("\n".join(texts))
            raise
        finally:
            if app is not None and app.poll() is None:
                app.terminate()
                app.wait(timeout=15)
            agent.terminate()
            agent.wait(timeout=15)
    (root / "qualification.json").write_text(
        json.dumps(
            {
                "setup_package_version": "0.1.0-1",
                "app_package_version": "0.1.0-3",
                "architecture": subprocess.check_output(
                    ["/usr/bin/dpkg", "--print-architecture"], text=True
                ).strip(),  # noqa: S603
                "native_review_cancel_no_changes": True,
                "real_os_permission_cancel_no_changes": True,
                "real_os_password_authentication": True,
                "verified_app_install_and_graphical_handoff": True,
                "ready_window_fits_1024x768_after_permission_retry": True,
                "authorization_bypass_or_cached_grant": False,
                "app_center_download_handoff_or_participant_study": False,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
