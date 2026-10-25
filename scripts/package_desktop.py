#!/usr/bin/env python3
"""Build a self-contained local Mac app from explicit runtime and wheel inputs.

The builder copies a portable CPython runtime and a clean, hash-locked dependency
site, adds the two public wheels, compiles the narrow native shell, and records
all input identities. It downloads no runtimes, accesses no signing credentials
and starts no model services. Public distribution additionally requires Developer
ID signing, notarization and installed clean-machine qualification.
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


def main() -> None:
    """Stage an application without system Python or a developer checkout."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--site-packages", type=Path, required=True)
    parser.add_argument("--root-wheel", type=Path, required=True)
    parser.add_argument("--library-wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--build-directory", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        parser.error("revision must be an exact source commit")
    runtime = args.runtime.resolve()
    if any(
        path.is_symlink() and not path.resolve().is_relative_to(runtime)
        for path in runtime.rglob("*")
    ):
        parser.error("portable runtime contains an external symlink")
    if any(
        (args.site_packages / name).exists()
        for name in ("skulk", "skulk_capability_sdk")
    ):
        parser.error(
            "the independent application cannot bundle Core or the managed SDK"
        )
    project = Path(__file__).resolve().parents[1]
    output = args.output.absolute()
    if output.exists() or output.suffix != ".app":
        parser.error("output must be a new .app directory")
    for path in (args.runtime, args.site_packages):
        if not path.is_dir():
            parser.error("explicit portable runtime and clean site required")
    for path in (args.root_wheel, args.library_wheel):
        if not path.is_file() or path.suffix != ".whl":
            parser.error("two exact public wheels required")
    subprocess.run(  # noqa: S603 - explicit local builder arguments, no shell
        [
            "/usr/bin/swift",
            "build",
            "--package-path",
            str(project / "desktop/macos"),
            "--scratch-path",
            str(args.build_directory),
        ],
        check=True,
    )
    binary = subprocess.check_output(  # noqa: S603 - fixed system compiler
        [
            "/usr/bin/swift",
            "build",
            "--package-path",
            str(project / "desktop/macos"),
            "--scratch-path",
            str(args.build_directory),
            "--show-bin-path",
        ],
        text=True,
    ).strip()
    contents = output / "Contents"
    resources = contents / "Resources"
    (contents / "MacOS").mkdir(parents=True)
    resources.mkdir()
    shutil.copy2(Path(binary) / "Sovereign", contents / "MacOS/Sovereign")
    shutil.copytree(
        args.runtime,
        resources / "python",
        symlinks=True,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copytree(
        args.site_packages,
        resources / "site-packages",
        symlinks=False,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    subprocess.run(  # noqa: S603 - explicit local builder arguments, no shell
        [
            str(shutil.which("uv") or "/usr/bin/uv"),
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(resources / "site-packages"),
            str(args.root_wheel),
            str(args.library_wheel),
        ],
        check=True,
    )
    with (contents / "Info.plist").open("wb") as stream:
        plistlib.dump(
            {
                "CFBundleExecutable": "Sovereign",
                "CFBundleIdentifier": "ai.foxlight.sovereign",
                "CFBundleName": "Sovereign",
                "CFBundleDisplayName": "Sovereign",
                "CFBundlePackageType": "APPL",
                "CFBundleShortVersionString": "0.1.0",
                "CFBundleVersion": "1",
                "LSMinimumSystemVersion": "13.0",
                "NSHighResolutionCapable": True,
                "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
            },
            stream,
        )
    receipt = {
        "schema_version": "sovereign.desktop-build.v1",
        "source_revision": args.revision,
        "wheels": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (args.root_wheel, args.library_wheel)
        },
        "runtime_files": {
            str(path.relative_to(args.runtime)): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in args.runtime.rglob("*")
            if path.is_file() and not path.is_symlink()
        },
        "distribution": "local-unnotarized-preview",
    }
    (resources / "build-receipt.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n"
    )
    # Ad hoc signing is for this local preview, never a Developer ID claim.
    subprocess.run(  # noqa: S603 - fixed signing tool, local preview output
        ["/usr/bin/codesign", "--force", "--sign", "-", str(output)], check=True
    )
    subprocess.run(["/usr/bin/codesign", "--verify", str(output)], check=True)  # noqa: S603
    print(output)


if __name__ == "__main__":
    main()
