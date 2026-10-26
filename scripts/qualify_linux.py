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
import runpy
import subprocess
import sys
import time
import urllib.request
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
    """Render the actual installed GTK/WebKit shell and cancel its real picker."""
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gdk, GLib, Gtk, WebKit2

    state = {"rendered": False, "picker": False, "pending": False}
    failure: list[str] = []
    started = time.monotonic()
    main_window = None
    view = None

    def close() -> None:
        if main_window is not None:
            main_window.emit("delete-event", None)

    def inspected(widget, result, _data) -> None:
        state["pending"] = False
        try:
            text = widget.run_javascript_finish(result).get_js_value().to_string()
            if "Find a model. Keep your own copy." not in text:
                return
            require("Skulk is optional" in text, "independent Library was not rendered")
            require(view.get_context().is_ephemeral(), "browser session persists")
            require(view.get_context().get_sandbox_enabled(), "WebKit sandbox disabled")
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(
                view.get_uri() + "api/library?offset=0", timeout=5
            ) as response:
                page = json.load(response)
            require(
                page["host_kind"] == "sovereign" and not page["jobs"],
                "opening the application started model work",
            )
            native = main_window.get_window()
            image = Gdk.pixbuf_get_from_window(
                native,
                0,
                0,
                main_window.get_allocated_width(),
                main_window.get_allocated_height(),
            )
            image.savev(str(root / "native-first-launch.png"), "png", [], [])
            state["rendered"] = True
            view.run_javascript(
                "document.querySelector('input[aria-label="
                '"Open recovery kit"]\').click()',
                None,
                None,
                None,
            )
        except Exception as error:
            failure.append(type(error).__name__ + ": " + str(error))
            close()

    def observe() -> bool:
        nonlocal main_window, view
        try:
            if time.monotonic() - started > 60:
                raise RuntimeError("native first launch or file chooser timed out")
            windows = Gtk.Window.list_toplevels()
            for window in windows:
                if window.get_title() == "Sovereign":
                    main_window = window
                    child = window.get_child()
                    if isinstance(child, WebKit2.WebView):
                        view = child
                if isinstance(window, Gtk.FileChooserDialog) and window.get_visible():
                    state["picker"] = True
                    window.response(Gtk.ResponseType.CANCEL)
                    GLib.timeout_add(200, lambda: close() or False)
                    return False
            if view is not None and not state["rendered"] and not state["pending"]:
                state["pending"] = True
                view.run_javascript("document.body.innerText", None, inspected, None)
            return True
        except Exception as error:
            failure.append(type(error).__name__ + ": " + str(error))
            close()
            return False

    GLib.timeout_add(250, observe)
    home = root / "native-library"
    sys.argv = ["sovereign", "--data-directory", str(home)]
    runpy.run_path(str(RESOURCES / "sovereign-linux.py"), run_name="__main__")
    require(not failure, "; ".join(failure))
    require(state["rendered"] and state["picker"], "native controls not observed")
    require(home.is_dir(), "independent owner did not retain its Library")
    return {
        "actual_installed_native_window": True,
        "ephemeral_browser": True,
        "webkit_sandbox_enabled": True,
        "native_file_picker_cancel": True,
        "fresh_launch_started_no_jobs": True,
        "cooperative_quit": True,
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
