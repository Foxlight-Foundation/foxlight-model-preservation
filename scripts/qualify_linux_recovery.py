#!/usr/bin/env python3
"""Qualify complete recovery through an installed native Linux app, offline.

Run in an owned ephemeral Xvfb/D-Bus/network namespace. Positive operations use
AT-SPI and native file choosers, never owner APIs or injected WebView scripts.
The installed isolated core prepares synthetic inputs and independently checks
results only after the app quits. External kits require independent exact pins.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RESOURCES = Path("/opt/foxlight/sovereign")


def require(condition: bool, message: str) -> None:
    """Keep qualification assertions active under optimized Python too."""
    if not condition:
        raise RuntimeError(message)


def digest(path: Path) -> str:
    """Hash large model files without loading them into memory."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def installed(arguments: list[str], *, timeout: int = 1800) -> dict:
    """Use only the published app's isolated Python and installed libraries."""
    result = subprocess.run(  # noqa: S603 - fixed installed runtime, no shell
        [
            str(RESOURCES / "python/bin/python3"),
            "-I",
            "-B",
            str(Path(__file__).resolve()),
            "--installed-helper",
            *arguments,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    require(result.returncode == 0, result.stderr)
    return json.loads(result.stdout)


def helper(arguments: list[str]) -> None:
    """Prepare public fixtures or verify GUI results with installed core only."""
    sys.path.insert(0, str(RESOURCES / "site-packages"))
    import importlib.metadata
    import importlib.util

    from foxlight_model_preservation.kits import (
        export_kit,
        inspect_kit,
        read_kit_envelope,
    )
    from foxlight_model_preservation.local_host.owner import LocalOwner
    from foxlight_model_preservation.models import TrustProfile

    require(importlib.util.find_spec("skulk") is None, "Skulk was installed")
    require(importlib.util.find_spec("skulk_capability_sdk") is None, "SDK installed")
    operation, target = arguments[:2]
    path = Path(target)
    if operation == "prepare":
        vector = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "examples/conformance/pmf-v0.1.json"
            ).read_bytes()
        )
        store = path / "fixture-store"
        (store / "manifests").mkdir(parents=True)
        (store / "objects/sha256").mkdir(parents=True)
        (store / "manifests" / (vector["manifest_id"] + ".json")).write_bytes(
            base64.b64decode(vector["envelope_base64"])
        )
        for identity, encoded in vector["objects_base64"].items():
            (store / "objects/sha256" / identity).write_bytes(base64.b64decode(encoded))
        export_kit(
            store,
            vector["manifest_id"],
            TrustProfile.model_validate_json(json.dumps(vector["trust"])),
            path / "input.foxkit",
        )
        path = path / "input.foxkit"
    if operation in {"prepare", "inspect"}:
        inspection = inspect_kit(path)
        manifest = inspection.manifest
        result = {
            "manifest_id": inspection.manifest_id,
            "kit_bytes": inspection.kit_bytes,
            "trust": inspection.trust.model_dump(mode="json"),
            "envelope_sha256": hashlib.sha256(read_kit_envelope(path)).hexdigest(),
            "member_count": len(manifest.members),
            "members": [
                {
                    "id": member.member_id,
                    "role": member.role,
                    "files": [
                        {"path": item.path, "bytes": item.size, "sha256": item.sha256}
                        for item in member.files
                    ],
                }
                for member in manifest.members
            ],
            "documents": [
                {
                    "id": item.document_id,
                    "bytes": item.size,
                    "sha256": item.sha256,
                    "role": item.role,
                }
                for item in manifest.documents
            ],
        }
        print(json.dumps(result))
        return
    require(operation == "read", "unknown installed helper")
    owner = LocalOwner(path)
    try:
        page = owner.read()
    finally:
        owner.close()
    print(
        json.dumps(
            {
                "copies": page["copies"],
                "jobs": page["jobs"],
                "core_version": importlib.metadata.version(
                    "foxlight-model-preservation"
                ),
                "library_version": importlib.metadata.version(
                    "foxlight-preservation-library"
                ),
            }
        )
    )


class NativeJourney:
    """Drive a single actual native GTK/WebKit app in an isolated desktop."""

    def __init__(self, root: Path, home: Path) -> None:
        import gi
        import pyatspi

        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk, GLib

        self.spi, self.gdk, self.glib = pyatspi, Gdk, GLib
        self.root, self.home = root, home
        self.app = None
        self.stream = None
        self.launches = 0
        self.operation_timeout = 120

    def nodes(self):
        """Read bounded accessibility state from this disposable display."""
        pending = [(self.spi.Registry.getDesktop(0), 0)]
        visited = 0
        while pending and visited < 15000:
            node, depth = pending.pop()
            visited += 1
            try:
                yield node
                if depth < 40:
                    pending.extend((child, depth + 1) for child in node if child)
            except (self.glib.Error, RuntimeError):
                continue

    def texts(self) -> list[str]:
        """Read human-visible labels, including selectable verification keys."""
        result = []
        for node in self.nodes():
            try:
                if node.name:
                    result.append(node.name)
                if "Text" in node.get_interfaces():
                    result.append(node.queryText().getText(0, 8192))
            except (self.glib.Error, RuntimeError):
                continue
        return result

    def wait(self, condition, description: str, timeout: int = 120) -> None:
        """Wait for a native UI milestone, retaining actionable failure evidence."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            require(self.app is not None and self.app.poll() is None, "app exited")
            if condition():
                return
            time.sleep(0.5)
        self.snapshot("failure")
        raise RuntimeError("Timed out: " + description)

    def find(self, name: str, role=None):
        """Find an exact accessible control, avoiding duplicate background controls."""
        for node in self.nodes():
            try:
                if node.name == name and (role is None or node.getRole() == role):
                    if node.getState().contains(self.spi.STATE_SHOWING):
                        return node
            except (self.glib.Error, RuntimeError):
                continue
        return None

    def click(self, name: str, role=None) -> None:
        """Activate a visible enabled native accessibility action."""
        role = role or self.spi.ROLE_PUSH_BUTTON
        self.wait(lambda: self.find(name, role) is not None, name)
        node = self.find(name, role)
        require(node.getState().contains(self.spi.STATE_ENABLED), name + " disabled")
        require(node.queryAction().doAction(0), name + " action refused")

    def snapshot(self, name: str) -> None:
        """Retain the actual screen and accessibility labels for the milestone."""
        screen = self.gdk.get_default_root_window()
        image = self.gdk.pixbuf_get_from_window(
            screen, 0, 0, screen.get_width(), screen.get_height()
        )
        image.savev(str(self.root / (name + ".png")), "png", [], [])
        (self.root / (name + ".txt")).write_text("\n".join(self.texts()))

    def launch(self) -> None:
        """Start the installed launcher against the same durable private Library."""
        self.launches += 1
        self.stream = (self.root / f"native-{self.launches}.log").open("wb")
        self.app = subprocess.Popen(  # noqa: S603 - installed fixed launcher
            ["/usr/bin/sovereign", "--data-directory", str(self.home)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=self.stream,
            env={
                **os.environ,
                "NO_AT_BRIDGE": "0",
                "GTK_MODULES": "gail:atk-bridge",
                "GTK_USE_PORTAL": "0",
                "GDK_BACKEND": "x11",
            },
        )
        self.wait(lambda: self.find("Open recovery kit") is not None, "native app")
        require(
            Path(f"/proc/{self.app.pid}/attr/current")
            .read_text()
            .startswith("ai.foxlight.sovereign "),
            "scoped installed AppArmor identity absent",
        )

    def quit(self) -> dict:
        """Quit cooperatively, then independently read durable owner state."""
        if self.app is not None and self.app.poll() is None:
            self.app.terminate()
            self.app.wait(timeout=60)
        require(self.app is not None and self.app.returncode == 0, "native quit failed")
        self.stream.close()
        return installed(["read", str(self.home)])

    def picker(self, path: Path, *, save: bool = False) -> None:
        """Enter a path through GTK's actual chooser and confirm its native action."""
        self.wait(lambda: self.find("Cancel") is not None, "native chooser")
        subprocess.run(  # noqa: S603 - fixed key chord in disposable runner GUI
            ["/usr/bin/xdotool", "key", "--clearmodifiers", "ctrl+l"],
            check=True,
            timeout=10,
        )

        def location():
            for node in self.nodes():
                try:
                    if node.getRole() in {self.spi.ROLE_ENTRY, self.spi.ROLE_TEXT}:
                        if node.getState().contains(self.spi.STATE_FOCUSED):
                            return node
                except (self.glib.Error, RuntimeError):
                    continue
            return None

        self.wait(lambda: location() is not None, "chooser location entry")
        require(location().queryEditableText().setTextContents(str(path)), "path entry")
        self.click("Save" if save else "Open")

    def done(self, count: int) -> None:
        """Wait for completed GUI jobs without starting operations through APIs."""
        self.wait(
            lambda: (
                "Done" in self.texts()
                and len(json.loads((self.home / "journal.json").read_bytes())["jobs"])
                == count
                and all(
                    job["state"] == "succeeded"
                    for job in json.loads((self.home / "journal.json").read_bytes())[
                        "jobs"
                    ]
                )
            ),
            f"{count} completed GUI jobs",
            timeout=self.operation_timeout,
        )
        require("Could not finish" not in self.texts(), "a GUI job failed")


def run(root: Path, kit: Path | None, expected_path: Path | None) -> dict:
    """Recover all parts offline, restart twice, and compare exact pinned bytes."""
    require(
        not root.exists() and bool(os.environ.get("DISPLAY")), "new root/Xvfb required"
    )
    root.mkdir(parents=True)
    require(
        not Path("/proc/net/route").read_text().splitlines()[1:],
        "external routes exist",
    )
    with socket.socket() as probe:
        probe.settimeout(2)
        require(
            probe.connect_ex(("1.1.1.1", 443)) != 0, "external networking available"
        )
    if kit is None:
        expected = installed(["prepare", str(root)])
        kit = root / "input.foxkit"
    else:
        require(expected_path is not None, "external kit needs independent pins")
        expected = json.loads(expected_path.read_bytes())
        require(
            installed(["inspect", str(kit)]) == expected, "kit differs from exact pins"
        )
        require(
            kit.is_relative_to(Path(tempfile.gettempdir())),
            "disposable staged kit required",
        )
        kit = kit.rename(root / "input.foxkit")
    (root / "expected.json").write_text(json.dumps(expected, indent=2) + "\n")
    input_digest = digest(kit)
    home = root / "native-library"
    journey = NativeJourney(root, home)
    journey.operation_timeout = 1200 if expected["kit_bytes"] > 100_000_000 else 120
    try:
        journey.launch()
        journey.snapshot("01-fresh-apt-app")
        journey.click("Open recovery kit")
        journey.picker(kit)
        journey.wait(
            lambda: journey.find("Import verified copy") is not None,
            "signed capture trust review",
            timeout=journey.operation_timeout,
        )
        texts = "\n".join(journey.texts())
        require(expected["manifest_id"] in texts, "review exact manifest missing")
        for key in expected["trust"]["keys"]:
            require(key["key_id"] in texts, "independently pinned capture key missing")
        require(
            not journey.find("Import verified copy")
            .getState()
            .contains(journey.spi.STATE_ENABLED),
            "import enabled before explicit trust choice",
        )
        journey.snapshot("02-signed-capture-review")
        journey.click(
            "I checked and accept these keys for this exact capture.",
            journey.spi.ROLE_CHECK_BOX,
        )
        journey.click("Import verified copy")
        journey.done(1)
        journey.click("Library")
        journey.wait(
            lambda: journey.find("Restore files") is not None, "imported Library"
        )
        journey.snapshot("03-imported-library")
        imported = journey.quit()
        require(len(imported["copies"]) == 1, "complete copy missing")
        require(len(imported["jobs"]) == 1, "unexpected import jobs")
        require(imported["jobs"][0]["state"] == "succeeded", "import incomplete")
        # Only runner-owned staging is removed; never remove an operator input.
        require(kit.is_relative_to(root), "kit must be inside owned test output")
        kit.unlink()
        journey.launch()
        journey.wait(
            lambda: journey.find("Restore files") is not None, "Library after restart"
        )
        journey.snapshot("04-restarted-library-without-input-kit")
        journey.click("Restore files")
        journey.wait(
            lambda: "Provenance and privacy" in journey.texts(), "restore review"
        )
        journey.snapshot("05-restore-review")
        journey.click("Restore files")
        journey.done(2)
        journey.click("Library")
        journey.click("Save recovery kit")
        exported = root / "Saved complete kit Ω.foxkit"
        journey.picker(exported, save=True)
        journey.wait(
            lambda: exported.exists(),
            "native saved kit",
            timeout=journey.operation_timeout,
        )
        # Native WebKit may create the destination before its stream finishes.
        journey.wait(
            lambda: exported.stat().st_size == expected["kit_bytes"],
            "saved kit bytes",
            timeout=journey.operation_timeout,
        )
        journey.click("Activity")
        journey.done(3)
        journey.snapshot("06-restored-and-exported")
        completed = journey.quit()
        require(len(completed["jobs"]) == 3, "unexpected jobs or sharing")
        require(
            all(job["state"] == "succeeded" for job in completed["jobs"]),
            "job incomplete",
        )
        require(
            {job["kind"] for job in completed["jobs"]}
            == {"kit_import", "restore", "kit_export"},
            "network or sharing started",
        )
        copy = completed["copies"][0]
        require(
            copy["manifest_id"] == expected["manifest_id"], "manifest identity changed"
        )
        require(copy["member_count"] == expected["member_count"], "companion missing")
        restore_job = next(job for job in completed["jobs"] if job["kind"] == "restore")
        restored = home / "restored" / restore_job["job_id"]
        files = {
            "members/" + member["id"] + "/" + item["path"]: item
            for member in expected["members"]
            for item in member["files"]
        }
        files.update(
            {"documents/" + item["id"]: item for item in expected["documents"]}
        )
        files[".pmf/envelope.json"] = {"sha256": expected["envelope_sha256"]}
        actual = {
            str(path.relative_to(restored))
            for path in restored.rglob("*")
            if path.is_file()
        }
        require(actual == set(files), "restored closure differs")
        for name, item in files.items():
            path = restored / name
            require(digest(path) == item["sha256"], "restored bytes changed: " + name)
            if "bytes" in item:
                require(path.stat().st_size == item["bytes"], "restored size changed")
        require(
            installed(["inspect", str(exported)]) == expected,
            "saved kit metadata changed",
        )
        require(digest(exported) == input_digest, "saved kit bytes differ from input")
        journey.launch()
        journey.wait(
            lambda: journey.find("Restore files") is not None, "second restart Library"
        )
        journey.click("Activity")
        journey.done(3)
        journey.snapshot("07-second-restart-durable-activity")
        restarted = journey.quit()
        require(restarted == completed, "restart changed copies/jobs or replayed work")
        result = {
            "schema_version": "sovereign.linux-gui-recovery.v1",
            "package_version": subprocess.check_output(
                ["/usr/bin/dpkg-query", "-W", "-f=${Version}", "foxlight-sovereign"],
                text=True,
            ),  # noqa: S603,S607
            "build_receipt": json.loads(
                (RESOURCES / "build-receipt.json").read_bytes()
            ),
            "core_version": completed["core_version"],
            "library_version": completed["library_version"],
            "manifest_id": expected["manifest_id"],
            "member_roles": [member["role"] for member in expected["members"]],
            "member_count": expected["member_count"],
            "restored_files": len(files),
            "kit_bytes": expected["kit_bytes"],
            "kit_sha256": input_digest,
            "exact_file_sizes_hashes_cards_and_signed_envelope": True,
            "native_open_trust_review_restore_and_save": True,
            "explicit_capture_trust_required": True,
            "original_input_kit_removed_after_import": True,
            "external_network_unavailable": True,
            "skulk_or_sdk_required": False,
            "app_restarts": 2,
            "completed_jobs_unchanged_after_restart": True,
            "sharing_or_network_jobs_started": False,
            "operating_system_reboot_or_model_inference": False,
        }
        (root / "qualification.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
    finally:
        if journey.app is not None and journey.app.poll() is None:
            journey.snapshot("failure")
            journey.quit()


def main() -> None:
    """Require isolated output and optional independently pinned kit input."""
    if sys.argv[1:2] == ["--installed-helper"]:
        helper(sys.argv[2:])
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--kit", type=Path)
    parser.add_argument("--expected", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.directory.resolve(), args.kit, args.expected)))


if __name__ == "__main__":
    main()
