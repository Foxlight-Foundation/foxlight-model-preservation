#!/usr/bin/env python3
"""Sign and notarize a reviewed Sovereign candidate; never publish it.

This intentionally uses an existing local Developer ID and Keychain notary
profile. It does not export keys or put credentials in the public build lane.
Run only when notarization is explicitly authorized. Completed artifacts remain
separate from the source and from all user model storage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import re
import shutil
import subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
MACHO = {
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
}


def run(arguments: list[str]) -> str:
    """Run one explicit platform tool, preserving failure for the operator."""
    return subprocess.check_output(arguments, text=True).strip()  # noqa: S603


def validate_candidate(app: Path, expected_source: str, team: str) -> dict:
    """Refuse wrong, dirty or modified build recipes before private signing."""
    if not re.fullmatch(r"[0-9a-f]{40}", expected_source):
        raise ValueError("expected source must be a full exact commit")
    if not re.fullmatch(r"[A-Z0-9]{10}", team):
        raise ValueError("expected team must be a ten-character Apple team ID")
    if app.is_symlink() or app.name != "Sovereign.app":
        raise ValueError("expected a regular Sovereign.app directory")
    if run(["git", "-C", str(PROJECT), "rev-parse", "HEAD"]) != expected_source:
        raise ValueError("signing checkout does not match expected source")
    if run(["git", "-C", str(PROJECT), "status", "--porcelain"]):
        raise ValueError("signing checkout must be clean")
    run(["/usr/bin/codesign", "--verify", "--strict", str(app)])
    receipt = json.loads((app / "Contents/Resources/build-receipt.json").read_bytes())
    lock = json.loads((PROJECT / "desktop/macos/inputs.json").read_bytes())
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    if (
        receipt.get("schema_version") != "sovereign.desktop-build.v2"
        or receipt.get("shell_source_revision") != expected_source
        or receipt.get("source_revision") != expected_source
        or receipt.get("shell_source_dirty") is not False
        or receipt.get("inputs") != lock
        or receipt.get("configuration") != "release"
        or receipt.get("distribution") != "ad-hoc-candidate"
        or info.get("CFBundleIdentifier") != "ai.foxlight.sovereign"
        or info.get("CFBundleShortVersionString") != lock["version"]
        or info.get("CFBundleVersion") != lock["build_number"]
    ):
        raise ValueError("candidate does not match the reviewed release recipe")
    # Confinement matters before walking/signing anything reachable from the app.
    for path in app.rglob("*"):
        if path.is_symlink() and not path.resolve().is_relative_to(app):
            raise ValueError("candidate contains an external symlink")
    return receipt


def sign(path: Path, identity: str, team: str, *, runtime: bool = True) -> None:
    """Sign individual code explicitly, with no permissive entitlements."""
    arguments = ["/usr/bin/codesign", "--force", "--timestamp", "--sign", identity]
    if runtime:
        arguments.extend(["--options", "runtime"])
    run([*arguments, str(path)])
    details = subprocess.run(  # noqa: S603
        ["/usr/bin/codesign", "--display", "--verbose=4", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stderr
    if (
        f"TeamIdentifier={team}\n" not in details
        or "Authority=Developer ID Application:" not in details
    ):
        raise ValueError("signing identity is not the expected Developer ID team")


def notarize(path: Path, profile: str, evidence: Path) -> str:
    """Require Apple's accepted result and retain the submission evidence."""
    result = run(
        [
            "/usr/bin/xcrun",
            "notarytool",
            "submit",
            str(path),
            "--keychain-profile",
            profile,
            "--wait",
            "--output-format",
            "json",
        ]
    )
    evidence.write_text(result + "\n")
    value = json.loads(result)
    if value.get("status") != "Accepted":
        raise ValueError(f"notarization was not accepted; inspect {evidence.name}")
    return value["id"]


def sha256(path: Path) -> str:
    """Hash final container bytes after stapling."""
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(part)
    return value.hexdigest()


def main() -> None:
    """Produce signed, stapled ZIP/DMG candidates without public upload."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--team", required=True)
    parser.add_argument("--notary-profile", required=True)
    args = parser.parse_args()
    app = args.app.absolute()
    receipt = validate_candidate(app, args.expected_source, args.team)
    output = args.directory.absolute()
    if output.exists():
        parser.error("release directory must be new")
    output.mkdir(parents=True)
    # Never mutate the original ad hoc candidate or inputs. Keep failures for
    # diagnosis, and never touch application data or operator model storage.
    staged = output / "staging/Sovereign.app"
    shutil.copytree(app, staged, symlinks=True)
    receipt["distribution"] = "developer-id-candidate"
    (staged / "Contents/Resources/build-receipt.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n"
    )
    for path in sorted(staged.rglob("*")):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as stream:
                is_code = stream.read(4) in MACHO
            if is_code:
                sign(path, args.identity, args.team)
    sign(staged, args.identity, args.team)
    run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(staged)])
    submission = output / "notary-submission.zip"
    run(
        [
            "/usr/bin/ditto",
            "-c",
            "-k",
            "--sequesterRsrc",
            "--keepParent",
            str(staged),
            str(submission),
        ]
    )
    app_notary = notarize(submission, args.notary_profile, output / "app-notary.json")
    run(["/usr/bin/xcrun", "stapler", "staple", str(staged)])
    run(["/usr/bin/xcrun", "stapler", "validate", str(staged)])
    run(["/usr/sbin/spctl", "--assess", "--type", "execute", str(staged)])
    basename = "Sovereign-" + receipt["inputs"]["version"] + "-macos-arm64"
    archive = output / (basename + ".zip")
    run(
        [
            "/usr/bin/ditto",
            "-c",
            "-k",
            "--sequesterRsrc",
            "--keepParent",
            str(staged),
            str(archive),
        ]
    )
    image_root = output / "disk-image"
    image_root.mkdir()
    shutil.copytree(staged, image_root / "Sovereign.app", symlinks=True)
    (image_root / "Applications").symlink_to("/Applications")
    image = output / (basename + ".dmg")
    run(
        [
            "/usr/bin/hdiutil",
            "create",
            "-volname",
            "Sovereign",
            "-srcfolder",
            str(image_root),
            "-format",
            "UDZO",
            str(image),
        ]
    )
    sign(image, args.identity, args.team, runtime=False)
    dmg_notary = notarize(image, args.notary_profile, output / "dmg-notary.json")
    run(["/usr/bin/xcrun", "stapler", "staple", str(image)])
    run(["/usr/bin/xcrun", "stapler", "validate", str(image)])
    run(
        [
            "/usr/sbin/spctl",
            "--assess",
            "--type",
            "open",
            "--context",
            "context:primary-signature",
            str(image),
        ]
    )
    artifacts = {path.name: sha256(path) for path in (archive, image)}
    (output / "SHA256SUMS").write_text(
        "".join(f"{value}  {name}\n" for name, value in artifacts.items())
    )
    (output / "release.json").write_text(
        json.dumps(
            {
                "schema_version": "sovereign.macos-release.v1",
                "source_revision": args.expected_source,
                "team": args.team,
                "inputs": receipt["inputs"],
                "artifacts": artifacts,
                "app_notarization": app_notary,
                "dmg_notarization": dmg_notary,
                "qualification": (
                    "signed-stapled-local-gatekeeper; installed journey separate"
                ),
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    print("Notarized candidates ready; nothing has been published: " + str(output))


if __name__ == "__main__":
    main()
