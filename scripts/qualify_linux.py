#!/usr/bin/env python3
"""Qualify an actually installed candidate in a disposable Linux desktop session.

Run only on an owned ephemeral runner under isolated D-Bus/Xvfb. This checks the
bundled CLI, opaque PMF round trip/tamper refusal, real native first launch and
file chooser cancellation. Full GUI recovery and signed APT remain separate.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
RESOURCES = Path("/opt/foxlight/sovereign")


def require(condition: bool, message: str) -> None:
    """Keep qualification assertions active under optimized Python too."""
    if not condition:
        raise RuntimeError(message)


def qualify_cli(root: Path) -> dict[str, object]:
    """Exercise package-installed commands with hostile ambient Python inputs."""
    flag = root / "ambient-code-ran"
    (root / "sitecustomize.py").write_text(
        "from pathlib import Path; Path(" + repr(str(flag)) + ").touch()\n"
    )
    environment = {
        **os.environ,
        "PYTHONPATH": str(root),
        "PYTHONHOME": str(root / "invalid"),
    }

    def command(arguments: list[str], succeeds: bool = True) -> str:
        result = subprocess.run(  # noqa: S603 - exact installed commands, no shell
            arguments,
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
        require(
            result.returncode == (0 if succeeds else 1), result.stdout + result.stderr
        )
        return result.stdout if succeeds else result.stderr

    command(["/usr/bin/model-preserve", "--help"])
    command(["/usr/bin/foxlight-sovereign", "--help"])
    command(["/usr/bin/xdg-open", "--version"])
    command(
        [
            str(RESOURCES / "python/bin/python3"),
            "-I",
            "-B",
            "-c",
            "import importlib.util,sys;sys.path.insert(0,sys.argv[1]);"
            "assert importlib.util.find_spec('skulk') is None;"
            "assert importlib.util.find_spec('skulk_capability_sdk') is None",
            str(RESOURCES / "site-packages"),
        ]
    )
    vector = json.loads((PROJECT / "examples/conformance/pmf-v0.1.json").read_bytes())
    store = root / "store"
    (store / "manifests").mkdir(parents=True)
    (store / "objects/sha256").mkdir(parents=True)
    envelope = base64.b64decode(vector["envelope_base64"])
    (store / "manifests" / (vector["manifest_id"] + ".json")).write_bytes(envelope)
    for identity, encoded in vector["objects_base64"].items():
        (store / "objects/sha256" / identity).write_bytes(base64.b64decode(encoded))
    trust = root / "fixture-public-trust.json"
    trust.write_text(json.dumps(vector["trust"]))
    common = [vector["manifest_id"], "--store", str(store), "--trust", str(trust)]
    verified = json.loads(command(["/usr/bin/model-preserve", "pmf-verify", *common]))
    require(verified["verification"] == "complete", "PMF verification failed")
    require(verified["authority"] == "owner-capture", "capture acquired authority")
    restored = root / "Restored files Ω"
    command(
        ["/usr/bin/model-preserve", "pmf-restore", *common, "--output", str(restored)]
    )
    manifest = vector["manifest"]
    expected = {
        "members/" + member["member_id"] + "/" + item["path"]: base64.b64decode(
            vector["objects_base64"][item["sha256"]]
        )
        for member in manifest["members"]
        for item in member["files"]
    }
    expected.update(
        {
            "documents/" + item["document_id"]: base64.b64decode(
                vector["objects_base64"][item["sha256"]]
            )
            for item in manifest["documents"]
        }
    )
    expected[".pmf/envelope.json"] = envelope
    require(
        {
            str(path.relative_to(restored)): path.read_bytes()
            for path in restored.rglob("*")
            if path.is_file()
        }
        == expected,
        "original files, opaque cards or signed wrapper changed",
    )
    identity = manifest["members"][0]["files"][0]["sha256"]
    (store / "objects/sha256" / identity).write_bytes(b"tampered fixture")
    refused = root / "refused"
    command(
        ["/usr/bin/model-preserve", "pmf-restore", *common, "--output", str(refused)],
        False,
    )
    require(
        not refused.exists() and not flag.exists(), "tamper or ambient code accepted"
    )
    return {
        "bundled_cli": True,
        "skulk_or_sdk_required": False,
        "original_signed_envelope_and_opaque_cards": True,
        "members": len(manifest["members"]),
        "restored_files": len(expected),
        "tampered_copy_refused": True,
        "ambient_python_ignored": True,
    }


def qualify_gui(root: Path) -> dict[str, object]:
    """Launch the installed identity, inspect native accessibility and stop it."""
    import gi
    import pyatspi

    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib

    def descendants(pid: int) -> set[int]:
        parents = {}
        for item in Path("/proc").iterdir():
            if not item.name.isdigit():
                continue
            try:
                value = (item / "stat").read_text().rsplit(")", 1)[1].split()
                parents[int(item.name)] = int(value[1])
            except (OSError, ValueError, IndexError):
                continue
        result = {pid}
        while True:
            children = {child for child, parent in parents.items() if parent in result}
            if children <= result:
                return result
            result |= children

    def accessible_nodes():
        pending = [(pyatspi.Registry.getDesktop(0), 0)]
        visited = 0
        while pending and visited < 10000:
            node, depth = pending.pop()
            visited += 1
            try:
                yield node
                if depth < 30:
                    pending.extend((child, depth + 1) for child in node if child)
            except (GLib.Error, RuntimeError):
                continue

    def action(name: str) -> bool:
        for node in accessible_nodes():
            try:
                if node.name == name and node.getRole() == pyatspi.ROLE_PUSH_BUTTON:
                    actions = node.queryAction()
                    if actions.nActions and actions.doAction(0):
                        return True
            except (GLib.Error, RuntimeError):
                continue
        return False

    home = root / "native-library"
    log = root / "native-stderr.log"
    with log.open("wb") as stream:
        app = subprocess.Popen(  # noqa: S603 - actual installed fixed launcher
            ["/usr/bin/sovereign", "--data-directory", str(home)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stream,
            env={
                **os.environ,
                "NO_AT_BRIDGE": "0",
                "GTK_MODULES": "gail:atk-bridge",
                "GTK_USE_PORTAL": "0",
                "GDK_BACKEND": "x11",
            },
        )
        try:
            deadline = time.monotonic() + 60
            rendered = False
            bounds = None
            while time.monotonic() < deadline:
                require(app.poll() is None, "native app exited: " + log.read_text())
                names = []
                for node in accessible_nodes():
                    try:
                        names.append(node.name)
                        if (
                            node.name == "Sovereign"
                            and node.getRole() == pyatspi.ROLE_FRAME
                        ):
                            bounds = node.queryComponent().getExtents(
                                pyatspi.DESKTOP_COORDS
                            )
                    except (GLib.Error, RuntimeError):
                        pass
                if any("Find a model. Keep your own copy." in name for name in names):
                    rendered = True
                    break
                time.sleep(0.25)
            require(
                rendered, "actual native Library did not render: " + log.read_text()
            )
            profile = Path(f"/proc/{app.pid}/attr/current").read_text()
            require(
                profile.startswith("ai.foxlight.sovereign "),
                "installed launcher did not enter its scoped AppArmor profile",
            )
            parent_map = Path(f"/proc/{app.pid}/uid_map").read_text()
            sandboxed = False
            for pid in descendants(app.pid):
                try:
                    command = Path(f"/proc/{pid}/cmdline").read_bytes()
                    if b"WebKitWebProcess" not in command:
                        continue
                    status = dict(
                        line.split(":", 1)
                        for line in Path(f"/proc/{pid}/status").read_text().splitlines()
                    )
                    child_map = Path(f"/proc/{pid}/uid_map").read_text()
                    if (
                        status["NoNewPrivs"].strip() == "1"
                        and status["Seccomp"].strip() == "2"
                        and child_map != parent_map
                    ):
                        sandboxed = True
                except (OSError, KeyError):
                    continue
            require(sandboxed, "native WebKit namespace/seccomp sandbox not observed")
            # Accessibility can precede painting. A background gradient alone
            # also has many colors: require dark foreground text inside the
            # actual app window, excluding the black Xvfb desktop border.
            require(bounds is not None, "native window bounds unavailable")
            screen = Gdk.get_default_root_window()
            deadline = time.monotonic() + 20
            dark = 0
            while time.monotonic() < deadline:
                image = Gdk.pixbuf_get_from_window(
                    screen, bounds.x, bounds.y, bounds.width, bounds.height
                )
                pixels = bytes(image.get_pixels())
                dark = sum(
                    max(pixels[index : index + 3]) < 140
                    for index in range(0, len(pixels) - 3, image.get_n_channels())
                )
                if dark > 1000:
                    break
                time.sleep(0.25)
            image.savev(str(root / "native-first-launch.png"), "png", [], [])
            require(dark > 1000, "native interface remained visually blank")
            for label in (
                "Browse public models",
                "Choose a recovery kit",
                "Choose a sharing file",
                "Getting started",
            ):
                require(label in names, "newcomer control missing: " + label)
            require(
                action("Choose a recovery kit"), "newcomer file chooser control missing"
            )
            canceled = False
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if action("Cancel"):
                    canceled = True
                    break
                time.sleep(0.25)
            require(canceled, "native file chooser did not open or cancel")
        finally:
            if app.poll() is None:
                app.terminate()
            app.wait(timeout=30)
        require(app.returncode == 0, "native Quit did not finish cooperatively")
    # The actual owner must have released its lock. Reopening locally may not
    # replay jobs or manufacture a model after first launch/picker cancellation.
    result = subprocess.run(  # noqa: S603 - fixed isolated installed runtime
        [
            str(RESOURCES / "python/bin/python3"),
            "-I",
            "-B",
            "-c",
            "import sys,json;sys.path.insert(0,sys.argv[1]);"
            "from foxlight_model_preservation.local_host.owner import LocalOwner;"
            "owner=LocalOwner(__import__('pathlib').Path(sys.argv[2]));"
            "page=owner.read();owner.close();"
            "print(json.dumps({'jobs':page['jobs'],'copies':page['copies']}))",
            str(RESOURCES / "site-packages"),
            str(home),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    require(result.returncode == 0, "owned child did not release its Library")
    require(
        json.loads(result.stdout) == {"jobs": [], "copies": []},
        "opening or canceling started model work",
    )
    return {
        "actual_installed_native_window": True,
        "scoped_apparmor_launcher": True,
        "webkit_namespace_seccomp_sandbox": True,
        "native_file_picker_cancel": True,
        "newcomer_choices_and_guide": True,
        "fresh_launch_started_no_jobs": True,
        "cooperative_quit_and_owner_lock_reopen": True,
        "full_native_recovery_or_real_model_qualification": False,
    }


def main() -> None:
    """Write scoped acceptance evidence outside all operator data."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    root = args.directory.resolve()
    if root.exists() or not os.environ.get("DISPLAY"):
        parser.error("new test directory and owned Xvfb display required")
    root.mkdir(parents=True)
    record = {
        "schema_version": "sovereign.linux-qualification.v1",
        **qualify_cli(root),
        **qualify_gui(root),
        "signed_apt_or_publication": False,
    }
    (root / "qualification.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record))


if __name__ == "__main__":
    main()
