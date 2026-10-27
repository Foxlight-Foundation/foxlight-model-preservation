#!/usr/bin/env python3
"""Verify reviewed final Mac bytes before execution, attestation or publication.

This verifies an existing Developer ID release. It never signs, rebuilds, grants
model authority, opens an operator Library or downloads model weights.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import re
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
MANIFEST = PROJECT / "desktop/macos/releases/0.1.0-build7.json"


def digest(path: Path) -> str:
    """Hash a regular retained file without following a substituted symlink."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("release assets must be regular files")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_assets(directory: Path, manifest: dict) -> None:
    """Require the closed, reviewed final container set before mounting it."""
    if {p.name for p in directory.iterdir()} != set(manifest["artifacts"]):
        raise ValueError("expected exactly the reviewed DMG and ZIP")
    for name, expected in manifest["artifacts"].items():
        if Path(name).name != name or digest(directory / name) != expected:
            raise ValueError("final container digest differs from reviewed manifest")


def run(arguments: list[str]) -> str:
    """Run a fixed platform verifier, preserving failures and diagnostics."""
    result = subprocess.run(  # noqa: S603 - fixed tool, digest-verified inputs
        arguments, text=True, capture_output=True, timeout=180
    )
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout + result.stderr


def check_identity(details: str, manifest: dict, *, app: bool) -> None:
    """Require Developer ID, hardened app and the complete reviewed CodeDirectory."""
    if (
        f"TeamIdentifier={manifest['team']}\n" not in details
        or "Authority=Developer ID Application:" not in details
        or "Authority=Apple Root CA\n" not in details
    ):
        raise ValueError("unexpected Apple Developer ID identity")
    if app and (
        f"CandidateCDHashFull sha256={manifest['app_codedirectory_sha256']}\n"
        not in details
        or "(runtime)" not in details
    ):
        raise ValueError("unexpected full app CodeDirectory or hardened runtime")


def verify_signature(path: Path, manifest: dict, scratch: Path, *, app: bool) -> None:
    """Check code seal, Apple chain, exact leaf certificate, ticket and Gatekeeper."""
    run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(path)])
    check_identity(
        run(["/usr/bin/codesign", "--display", "--verbose=4", str(path)]),
        manifest,
        app=app,
    )
    prefix = scratch / ("app-cert-" if app else "dmg-cert-")
    run(
        [
            "/usr/bin/codesign",
            "--display",
            "--extract-certificates=" + str(prefix),
            str(path),
        ]
    )
    leaf = prefix.with_name(prefix.name + "0").read_bytes()
    # Apple's certificate fingerprint identifier; payload integrity uses SHA-256.
    if (
        hashlib.sha1(leaf, usedforsecurity=False).hexdigest().upper()
        != manifest[  # noqa: S324
            "certificate_sha1"
        ]
    ):
        raise ValueError("Developer ID certificate fingerprint differs")
    run(["/usr/bin/xcrun", "stapler", "validate", str(path)])
    policy = (
        ["--type", "execute"]
        if app
        else ["--type", "open", "--context", "context:primary-signature"]
    )
    run(["/usr/sbin/spctl", "--assess", *policy, "--verbose=4", str(path)])


def check_receipt(app: Path, manifest: dict) -> None:
    """Bind native source, public Python inputs and build number independently."""
    receipt = json.loads((app / "Contents/Resources/build-receipt.json").read_bytes())
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    if (
        receipt.get("schema_version") != "sovereign.desktop-build.v2"
        or receipt.get("source_revision") != manifest["source_revision"]
        or receipt.get("shell_source_revision") != manifest["source_revision"]
        or receipt.get("shell_source_dirty") is not False
        or receipt.get("inputs") != manifest["inputs"]
        or receipt.get("configuration") != "release"
        or receipt.get("distribution") != "developer-id-candidate"
        or info.get("CFBundleIdentifier") != "ai.foxlight.sovereign"
        or info.get("CFBundleShortVersionString") != manifest["inputs"]["version"]
        or info.get("CFBundleVersion") != manifest["inputs"]["build_number"]
    ):
        raise ValueError("signed app receipt differs from reviewed publication")
    for item in app.rglob("*"):
        if item.is_symlink() and not item.resolve().is_relative_to(app.resolve()):
            raise ValueError("app contains an external symlink")


def qualify(app: Path, manifest: dict, scratch: Path) -> dict:
    """Run real bundled commands only after all external trust checks pass."""
    verify_signature(app, manifest, scratch, app=True)
    check_receipt(app, manifest)
    qualification = scratch / "offline-cli"
    run(
        [
            sys.executable,
            str(PROJECT / "scripts/qualify_macos_cli.py"),
            "--app",
            str(app),
            "--directory",
            str(qualification),
        ]
    )
    return json.loads((qualification / "qualification.json").read_bytes())


def check_image_layout(mount: Path) -> None:
    """Allow only the app, install link and three approved Finder window files."""
    hidden = {".DS_Store", ".VolumeIcon.icns", ".background.tiff"}
    if (
        {p.name for p in mount.iterdir()} != {"Sovereign.app", "Applications"} | hidden
        or not (mount / "Applications").is_symlink()
        or os.readlink(mount / "Applications") != "/Applications"
        or (mount / "Sovereign.app").is_symlink()
        or not (mount / "Sovereign.app").is_dir()
        or any(
            (mount / name).is_symlink() or not (mount / name).is_file()
            for name in hidden
        )
    ):
        raise ValueError("unexpected drag-to-Applications image layout")


def verify_containers(directory: Path, manifest: dict, scratch: Path) -> dict:
    """Verify the ZIP and the mounted read-only DMG, always detaching the image."""
    image = next(directory.glob("*.dmg"))
    archive = next(directory.glob("*.zip"))
    dmg = scratch / "dmg"
    dmg.mkdir()
    verify_signature(image, manifest, dmg, app=False)
    mount = dmg / "mount"
    mount.mkdir()
    run(
        [
            "/usr/bin/hdiutil",
            "attach",
            "-readonly",
            "-nobrowse",
            "-noautoopen",
            "-mountpoint",
            str(mount),
            str(image),
        ]
    )
    try:
        check_image_layout(mount)
        dmg_result = qualify(mount / "Sovereign.app", manifest, dmg)
    finally:
        run(["/usr/bin/hdiutil", "detach", str(mount)])
    zipped = scratch / "zip"
    zipped.mkdir()
    run(["/usr/bin/ditto", "-x", "-k", str(archive), str(zipped)])
    if {p.name for p in zipped.iterdir()} != {"Sovereign.app"}:
        raise ValueError("unexpected ZIP application layout")
    zip_result = qualify(zipped / "Sovereign.app", manifest, zipped)
    return {"dmg": dmg_result, "zip": zip_result}


def main() -> None:
    """Hash only for intake; otherwise produce a verification predicate and sums."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--hash-only", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--scratch", type=Path)
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_bytes())
    check_assets(args.directory, manifest)
    if args.hash_only:
        print("Exact reviewed final container inventory and digests accepted")
        return
    if (
        platform.system() != "Darwin"
        or platform.machine() != "arm64"
        or args.output is None
        or args.output.exists()
        or args.scratch is None
        or args.scratch.exists()
    ):
        parser.error("native verification requires arm64 Mac and two new directories")
    revision = run(["git", "-C", str(PROJECT), "rev-parse", "HEAD"]).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("verification source must be exact")
    args.scratch.mkdir(parents=True)
    checks = verify_containers(
        args.directory.resolve(), manifest, args.scratch.resolve()
    )
    args.output.mkdir(parents=True)
    # Large containers remain immutable in place. The workflow copies them to the
    # output after verification; the checksum list covers that same inventory.
    (args.output / "native-release.json").write_bytes(MANIFEST.read_bytes())
    predicate = {
        "schema_version": "sovereign.macos-verification.v1",
        "binary_source_revision": manifest["source_revision"],
        "verification_source_revision": revision,
        "release": manifest,
        "host": {"os": platform.mac_ver()[0], "architecture": platform.machine()},
        "checks": checks,
        "assertion": "Existing containers verified; not a native build attestation.",
    }
    (args.output / "verification.json").write_text(
        json.dumps(predicate, indent=2) + "\n"
    )
    hashes = {
        **manifest["artifacts"],
        **{
            name: digest(args.output / name)
            for name in ("native-release.json", "verification.json")
        },
    }
    (args.output / "SHA256SUMS").write_text(
        "".join(f"{value}  {name}\n" for name, value in sorted(hashes.items()))
    )
    print(
        json.dumps(
            {
                "accepted": True,
                "binary_source": manifest["source_revision"],
                "verifier_source": revision,
            }
        )
    )


if __name__ == "__main__":
    main()
