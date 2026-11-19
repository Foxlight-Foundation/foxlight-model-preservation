#!/usr/bin/env python3
"""Build an unsigned Windows candidate offline from pinned public inputs.

Fetch the three URLs in desktop/windows/inputs.json and prepare its
hash-locked win_amd64 wheelhouse first; this downloads nothing. The result is
the staged app and a per-user MSI. Both are unsigned: the signing lane signs
every PE file and then the MSI (installer note foxlight-docs#769). Nothing is
started, and no service or sharing is turned on.

Needs Windows x64, Python 3.14 or later to run it (for compression.zstd), uv,
cargo with the MSVC toolchain, and the WiX Toolset v5 `wix` command.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath

from build_macos import (
    check_input,
    copy_bundled_notices,
    digest,
    extract_library,
    extract_python,
)

PROJECT = Path(__file__).resolve().parents[1]
WINDOWS = PROJECT / "desktop/windows"
# The Library may never bring Core's managed SDK into the independent app.
FORBIDDEN = ("skulk", "skulk_capability_sdk")


def normalize_installer_metadata(site: Path) -> None:
    """Drop generated launchers and cache records; wheels stay in the receipt."""
    for name in ("bin", "Scripts"):
        if (site / name).exists():
            shutil.rmtree(site / name)
    removed = {"uv_cache.json", "direct_url.json"}
    for metadata in site.glob("*.dist-info"):
        for name in removed:
            (metadata / name).unlink(missing_ok=True)
        record = metadata / "RECORD"
        with record.open(newline="") as stream:
            rows = list(csv.reader(stream))
        with record.open("w", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            for row in rows:
                if row[0].startswith(("../bin/", "bin/", "../Scripts/", "Scripts/")):
                    continue
                if row[0] in {metadata.name + "/" + name for name in removed}:
                    continue
                writer.writerow(row)


def runtime_notices(archive: Path, target: Path) -> dict[str, str]:
    """Copy the runtime's licences and inventory from its full build."""
    try:
        from compression import zstd  # Python 3.14+
    except ImportError as error:
        raise SystemExit("run this build with Python 3.14 or later") from error
    target.mkdir()
    found: dict[str, str] = {}
    with (
        zstd.open(archive) as stream,
        tarfile.open(fileobj=stream, mode="r|") as source,
    ):
        for member in source:
            path = PurePosixPath(member.name)
            if not (
                path.parent == PurePosixPath("python/licenses")
                or member.name == "python/PYTHON.json"
            ):
                continue
            if (
                not member.isfile()
                or member.size > 2 * 1024 * 1024
                or path.name in found
                or len(found) > 100
            ):
                raise ValueError("unexpected runtime notice inventory")
            content = source.extractfile(member)
            if content is None:
                raise ValueError("runtime notice missing")
            data = content.read()
            (target / path.name).write_bytes(data)
            found[path.name] = hashlib.sha256(data).hexdigest()
    if "LICENSE.cpython.txt" not in found or "PYTHON.json" not in found:
        raise ValueError("original Python runtime notices required")
    return {**found, **copy_bundled_notices(target)}


def git(*arguments: str) -> str:
    binary = shutil.which("git")
    if binary is None:
        raise SystemExit("git is required to record the source revision")
    return subprocess.check_output(  # noqa: S603 - resolved git, fixed arguments
        [binary, "-C", str(PROJECT), *arguments], text=True
    ).strip()


def msi_version(version: str, build_number: str) -> str:
    """MSI major upgrades compare only the first three fields: a.b.<build>."""
    major, minor, _patch = version.split(".")
    if int(build_number) > 65535:
        raise ValueError("MSI build field is at most 65535")
    return f"{major}.{minor}.{build_number}"


def main() -> None:
    """Stage the app, compile its bytecode, build the shell, package the MSI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-archive", type=Path, required=True)
    parser.add_argument("--python-archive", type=Path, required=True)
    parser.add_argument("--notices-archive", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--uv", type=Path, default=Path(shutil.which("uv") or "uv"))
    parser.add_argument(
        "--cargo", type=Path, default=Path(shutil.which("cargo") or "cargo")
    )
    parser.add_argument("--wix", type=Path, default=Path(shutil.which("wix") or "wix"))
    parser.add_argument(
        "--checkout-library",
        action="store_true",
        help=(
            "use a Library install archive built from this same checkout instead "
            "of the pinned release (CI previews only; never released)"
        ),
    )
    args = parser.parse_args()
    if sys.platform != "win32" or platform.machine() not in {"AMD64", "x86_64"}:
        parser.error("build on Windows x64")
    lock = json.loads((WINDOWS / "inputs.json").read_bytes())
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", lock["version"]):
        parser.error("version must contain three numeric components")
    if not re.fullmatch(r"[1-9][0-9]*", lock["build_number"]):
        parser.error("build number must be a positive integer")
    pinned = [
        (args.python_archive, lock["python"]),
        (args.notices_archive, lock["notices"]),
    ]
    if not args.checkout_library:
        pinned.append((args.library_archive, lock["library"]))
    for path, expected in pinned:
        check_input(path, expected["sha256"])
    output = args.directory.resolve()
    if output.exists() or not args.wheelhouse.is_dir():
        parser.error("a new staging directory and an existing wheelhouse are required")
    revision = git("rev-parse", "HEAD")
    dirty = bool(git("status", "--porcelain"))
    environment = {
        **os.environ,
        "SOURCE_DATE_EPOCH": git("show", "-s", "--format=%ct", "HEAD"),
        "UV_PYTHON_DOWNLOADS": "never",
    }
    output.mkdir(parents=True)
    library = output / "library"
    extract_library(args.library_archive, library)
    software = json.loads((library / "INSTALL.json").read_bytes())
    expected_source = (
        revision if args.checkout_library else lock["library"]["source_revision"]
    )
    if software["source_revision"] != expected_source:
        raise ValueError("Library source does not match the input lock or checkout")
    extract_python(args.python_archive, output / "runtime")
    app = output / "Sovereign"
    app.mkdir()
    shutil.move(str(output / "runtime/python"), app / "python")
    python = app / "python/python.exe"
    site = app / "site-packages"
    # The lock authenticates every dependency: no index, cache or resolver.
    subprocess.run(  # noqa: S603 - locked offline dependencies only
        [
            str(args.uv.resolve()),
            "--no-config",
            "--offline",
            "--no-cache",
            "pip",
            "install",
            "--python",
            str(python),
            "--target",
            str(site),
            "--require-hashes",
            "--no-index",
            "--find-links",
            str(args.wheelhouse.resolve()),
            "--find-links",
            str(library),
            "-r",
            str(library / "requirements-desktop.lock"),
        ],
        check=True,
        env=environment,
    )
    if any((site / name).exists() for name in FORBIDDEN):
        raise ValueError("the independent app cannot contain Core or the SDK")
    normalize_installer_metadata(site)
    # The app runs Python with -B and never writes into its install folder, so
    # bytecode is compiled here. Unchecked hashes: installed file times vary.
    subprocess.run(  # noqa: S603 - the bundled runtime, fixed arguments
        [
            str(python),
            "-I",
            "-m",
            "compileall",
            "-q",
            "-j",
            "0",
            "--invalidation-mode",
            "unchecked-hash",
            str(app / "python/Lib"),
            str(site),
        ],
        check=True,
        env=environment,
    )
    target = output / "cargo"
    subprocess.run(  # noqa: S603 - the locked shell crate
        [
            # Not resolved: cargo.exe is a rustup proxy that dispatches by name.
            str(args.cargo),
            "build",
            "--release",
            "--locked",
            "--manifest-path",
            str(WINDOWS / "shell/Cargo.toml"),
            "--target-dir",
            str(target),
        ],
        check=True,
        env=environment,
    )
    shutil.copyfile(target / "release/Sovereign.exe", app / "Sovereign.exe")
    notices = runtime_notices(args.notices_archive, app / "runtime-notices")
    (app / "build-receipt.json").write_text(
        json.dumps(
            {
                "schema_version": "sovereign.windows-build.v1",
                "source_revision": revision,
                "source_dirty": dirty,
                "inputs": lock,
                "software": software,
                "shell_sha256": digest(app / "Sovereign.exe"),
                "runtime_notices": notices,
                "library_archive_sha256": digest(args.library_archive),
                "distribution": (
                    "checkout-preview"
                    if args.checkout_library
                    else "unsigned-candidate"
                ),
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    version = msi_version(lock["version"], lock["build_number"])
    package = output / (
        f"Sovereign-{lock['version']}-build{lock['build_number']}-windows-x64.msi"
    )
    subprocess.run(  # noqa: S603 - fixed WiX source, staged app
        [
            str(args.wix),
            "build",
            str(WINDOWS / "Sovereign.wxs"),
            "-arch",
            "x64",
            "-d",
            f"Version={version}",
            "-bindpath",
            f"App={app}",
            "-bindpath",
            str(WINDOWS),
            "-out",
            str(package),
        ],
        check=True,
        env=environment,
    )
    print(package)


if __name__ == "__main__":
    main()
