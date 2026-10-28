#!/usr/bin/env python3
"""Promote only both qualified native Linux packages into an attested release."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
REQUIRED_CHECKS = (
    "bundled_cli",
    "original_signed_envelope_and_opaque_cards",
    "tampered_copy_refused",
    "ambient_python_ignored",
    "actual_installed_native_window",
    "scoped_apparmor_launcher",
    "webkit_namespace_seccomp_sandbox",
    "native_file_picker_cancel",
    "newcomer_choices_and_guide",
    "fresh_launch_started_no_jobs",
    "cooperative_quit_and_owner_lock_reopen",
    "purge_retains_exact_user_files",
)


def digest(path: Path) -> str:
    """Hash the exact immutable package or release evidence bytes."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def assemble(inputs: Path, output: Path, revision: str) -> None:
    """Require the complete architecture pair, clean receipts and passed checks."""
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or output.exists():
        raise ValueError("a full source revision and new output directory are required")
    lock = json.loads((PROJECT / "desktop/linux/inputs.json").read_bytes())
    version = lock["version"] + "-" + lock["revision"]
    packages = sorted(inputs.glob("*/*.deb"))
    expected = {
        f"foxlight-sovereign_{version}_{architecture}.deb"
        for architecture in ("amd64", "arm64")
    }
    if len(packages) != 2 or {p.name for p in packages} != expected:
        raise ValueError("release requires exactly the qualified architecture pair")
    evidence = {}
    for package in packages:
        if package.is_symlink() or not package.is_file():
            raise ValueError("package must be a regular file")
        architecture = package.stem.rsplit("_", 1)[1]
        receipt = json.loads((package.parent / "build-receipt.json").read_bytes())
        qualification = json.loads((package.parent / "qualification.json").read_bytes())
        if (
            receipt.get("source_revision") != revision
            or receipt.get("source_dirty") is not False
            or receipt.get("inputs") != lock
            or receipt.get("architecture") != architecture
            or qualification.get("skulk_or_sdk_required") is not False
            or any(qualification.get(key) is not True for key in REQUIRED_CHECKS)
        ):
            raise ValueError(
                "clean exact-source package and full qualification required"
            )
        fields = subprocess.check_output(  # noqa: S603 - fixed package metadata reader
            [
                "/usr/bin/dpkg-deb",
                "-f",
                str(package),
                "Package",
                "Version",
                "Architecture",
            ],
            text=True,
        )
        if fields != (
            f"Package: foxlight-sovereign\nVersion: {version}\n"
            f"Architecture: {architecture}\n"
        ):
            raise ValueError("package metadata differs from the reviewed recipe")
        sums = (package.parent / "SHA256SUMS").read_text()
        if sums != f"{digest(package)}  {package.name}\n":
            raise ValueError("package digest changed after qualification")
        evidence[architecture] = {"build": receipt, "qualification": qualification}
    output.mkdir(parents=True)
    for package in packages:
        shutil.copyfile(package, output / package.name)
    record = {
        "schema_version": "sovereign.linux-release.v1",
        "tag": "sovereign-linux-v" + version,
        "version": version,
        "source_revision": revision,
        "architectures": ["amd64", "arm64"],
        "artifacts": {package.name: digest(package) for package in packages},
        "evidence": evidence,
        "qualification_limits": [
            "Native Ubuntu 24.04 amd64 and arm64; other distributions unqualified.",
            "Installed native launch and bundled CLI synthetic complete PMF recovery.",
            "No full native GUI recovery, model execution or privacy qualification.",
            "APT authentication is a separate signed repository publication.",
        ],
    }
    (output / "linux-release.json").write_text(json.dumps(record, indent=2) + "\n")
    (output / "SHA256SUMS").write_text(
        "".join(f"{digest(path)}  {path.name}\n" for path in sorted(output.iterdir()))
    )


def main() -> None:
    """Assemble release evidence from qualified native CI artifacts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    assemble(args.input, args.output, args.revision)


if __name__ == "__main__":
    main()
