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


def install_native_cli(contents: Path) -> dict[str, str]:
    """Install relocatable commands and replace external-Python console stubs."""
    source = Path(__file__).resolve().parents[1] / "desktop/macos/cli"
    resources = contents / "Resources"
    # Apple TN2206 places scripts in Resources, covered by the outer seal,
    # rather than relying on script signatures stored in extended attributes.
    commands = resources / "bin"
    commands.mkdir()
    internal = resources / "site-packages/bin"
    if internal.is_symlink():
        raise ValueError("console-script directory must remain inside the app")
    internal.mkdir(exist_ok=True)
    # These are installer-generated stubs, not package modules. Only the two
    # supported native commands belong in this app; optional Core clients and
    # third-party developer stubs must not point at an external Python either.
    stubs = list(internal.iterdir())
    if any(path.is_dir() and not path.is_symlink() for path in stubs):
        raise ValueError("unexpected directory in generated console scripts")
    for stub in stubs:
        stub.unlink()
    bootstrap = resources / "native-cli.py"
    shutil.copyfile(source / "native-cli.py", bootstrap)
    bootstrap.chmod(0o644)
    paths = [bootstrap]
    for name in ("model-preserve", "foxlight-sovereign"):
        command = commands / name
        shutil.copyfile(source / "launcher.sh", command)
        command.chmod(0o755)
        paths.append(command)
        alias = internal / name
        alias.symlink_to("../../bin/" + name)
    return {
        "Contents/" + str(path.relative_to(contents)): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in paths
    }


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
    parser.add_argument(
        "--uv", type=Path, default=Path(shutil.which("uv") or "/usr/bin/uv")
    )
    parser.add_argument(
        "--configuration", choices=("debug", "release"), default="debug"
    )
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--build-number", default="1")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        parser.error("revision must be an exact source commit")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.version):
        parser.error("version must contain three numeric components")
    if not re.fullmatch(r"[1-9][0-9]*", args.build_number):
        parser.error("build number must be a positive integer")
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
            "--configuration",
            args.configuration,
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
            "--configuration",
            args.configuration,
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
            str(args.uv.resolve()),
            "--no-config",
            "--offline",
            "--no-cache",
            "pip",
            "install",
            "--no-deps",
            "--no-index",
            "--target",
            str(resources / "site-packages"),
            str(args.root_wheel),
            str(args.library_wheel),
        ],
        check=True,
    )
    native_cli = install_native_cli(contents)
    with (contents / "Info.plist").open("wb") as stream:
        plistlib.dump(
            {
                "CFBundleExecutable": "Sovereign",
                "CFBundleIdentifier": "ai.foxlight.sovereign",
                "CFBundleName": "Sovereign",
                "CFBundleDisplayName": "Sovereign",
                "CFBundlePackageType": "APPL",
                "CFBundleShortVersionString": args.version,
                "CFBundleVersion": args.build_number,
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
        "native_cli": native_cli,
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
