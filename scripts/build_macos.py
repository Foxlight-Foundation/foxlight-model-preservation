#!/usr/bin/env python3
"""Build an offline, self-contained Mac candidate from pinned public archives.

This downloads nothing. Fetch the two URLs in desktop/macos/inputs.json and
prepare its hash-locked wheelhouse explicitly. The candidate remains ad hoc
signed until release_macos.py signs/notarizes it. No services or sharing start.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

PROJECT = Path(__file__).resolve().parents[1]
INPUTS = PROJECT / "desktop/macos/inputs.json"
# libtorrent 2.0.14 publishes Apple silicon wheels for macOS 14.0 and 15.0 only.
# Bundle the 14.0 build so the DHT binding loads on 14 and 15; a wheelhouse
# prepared on a macOS 15 host without --platform would otherwise pick 15.0.
NATIVE_MACOS_CEILING = (14, 0)
LC_VERSION_MIN_MACOSX = 0x24
LC_BUILD_VERSION = 0x32


def _thin_minimum(data: bytes) -> tuple[int, int] | None:
    """Read one 64-bit little-endian Mach-O slice's minimum macOS version."""
    if data[:4] != b"\xcf\xfa\xed\xfe":
        return None
    count = int.from_bytes(data[16:20], "little")
    offset = 32
    for _ in range(count):
        command = int.from_bytes(data[offset : offset + 4], "little")
        size = int.from_bytes(data[offset + 4 : offset + 8], "little")
        if size < 8 or offset + size > len(data):
            raise ValueError("malformed Mach-O load command")
        if command == LC_BUILD_VERSION:
            encoded = int.from_bytes(data[offset + 12 : offset + 16], "little")
        elif command == LC_VERSION_MIN_MACOSX:
            encoded = int.from_bytes(data[offset + 8 : offset + 12], "little")
        else:
            offset += size
            continue
        return encoded >> 16, (encoded >> 8) & 0xFF
    return None


def macho_minimum(path: Path) -> tuple[int, int] | None:
    """Return the newest minimum macOS of a (possibly universal) Mach-O file."""
    data = path.read_bytes()
    if data[:4] != b"\xca\xfe\xba\xbe":
        return _thin_minimum(data)
    versions: list[tuple[int, int]] = []
    for index in range(int.from_bytes(data[4:8], "big")):
        entry = 8 + 20 * index
        start = int.from_bytes(data[entry + 8 : entry + 12], "big")
        length = int.from_bytes(data[entry + 12 : entry + 16], "big")
        version = _thin_minimum(data[start : start + length])
        if version is not None:
            versions.append(version)
    return max(versions) if versions else None


def check_native_floor(site: Path) -> None:
    """Refuse bundled native code that needs a newer macOS than the ceiling."""
    for path in sorted(site.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as stream:
            head = stream.read(4)
        if head not in {b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"}:
            continue
        minimum = macho_minimum(path)
        if minimum is not None and minimum > NATIVE_MACOS_CEILING:
            raise ValueError(
                f"{path.relative_to(site)} needs macOS {minimum[0]}.{minimum[1]}; "
                "prepare the wheelhouse with --platform macosx_14_0_arm64"
            )


def digest(path: Path) -> str:
    """Hash a regular input in bounded chunks."""
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(part)
    return value.hexdigest()


def check_input(path: Path, expected: str) -> None:
    """Refuse substituted or symlinked input before staging anything."""
    if path.is_symlink() or not path.is_file() or digest(path) != expected:
        raise ValueError(f"pinned archive hash mismatch: {path.name}")


def extract_library(archive: Path, target: Path) -> None:
    """Extract the pinned flat software archive, never paths or special files."""
    with zipfile.ZipFile(archive) as source:
        names: set[str] = set()
        for entry in source.infolist():
            name = entry.filename
            mode = entry.external_attr >> 16
            if (
                name in names
                or PurePosixPath(name).name != name
                or "\\" in name
                or name in ("", ".", "..")
                or entry.is_dir()
                or (mode & 0o170000) not in (0, 0o100000)
                or entry.file_size > 128 * 1024 * 1024
            ):
                raise ValueError("unsafe software archive inventory")
            names.add(name)
        target.mkdir()
        source.extractall(target)  # noqa: S202 - checked flat regular-file inventory


def extract_python(archive: Path, target: Path) -> None:
    """Extract only relative runtime members with confined link destinations."""
    # macOS /tmp aliases /private/tmp. Compare canonical roots on both sides.
    target = target.resolve()
    with tarfile.open(archive) as source:
        members = source.getmembers()
        names: set[str] = set()
        total = 0
        for item in members:
            path = PurePosixPath(item.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not path.parts
                or path.parts[0] != "python"
                or item.name in names
                or not (item.isfile() or item.isdir() or item.issym())
            ):
                raise ValueError("unsafe runtime archive member")
            names.add(item.name)
            total += item.size
            if total > 512 * 1024 * 1024:
                raise ValueError("runtime archive is too large")
            if item.issym():
                destination = (target / item.name).parent / item.linkname
                if not destination.resolve().is_relative_to(target / "python"):
                    raise ValueError("runtime link escapes the runtime")
        target.mkdir()
        # data filter additionally prevents traversal through preceding symlinks.
        source.extractall(target, filter="data")


def source_identity() -> tuple[str, bool]:
    """Record the actual shell/build checkout, separately from wheel sources."""
    revision = subprocess.check_output(  # noqa: S603
        ["/usr/bin/git", "-C", str(PROJECT), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = bool(
        subprocess.check_output(  # noqa: S603
            ["/usr/bin/git", "-C", str(PROJECT), "status", "--porcelain"], text=True
        ).strip()
    )
    return revision, dirty


def copy_runtime_notices(resources: Path) -> dict[str, str]:
    """Retain upstream runtime notices omitted by its small install archive."""
    source = PROJECT / "desktop/macos/runtime-notices"
    record = json.loads((source / "SOURCES.json").read_bytes())
    members: dict[str, str] = record["members"]
    if {path.name for path in source.iterdir()} != {*members, "SOURCES.json"}:
        raise ValueError("runtime notice inventory changed")
    for name, expected in members.items():
        if Path(name).name != name:
            raise ValueError("unsafe runtime notice name")
        check_input(source / name, expected)
    shutil.copytree(source, resources / "runtime-notices")
    return members


def main() -> None:
    """Stage the exact independent app; never sign with a private identity."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-archive", type=Path, required=True)
    parser.add_argument("--python-archive", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--uv", type=Path, default=Path(shutil.which("uv") or "uv"))
    args = parser.parse_args()
    lock = json.loads(INPUTS.read_bytes())
    check_input(args.library_archive, lock["library"]["sha256"])
    check_input(args.python_archive, lock["python"]["sha256"])
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("this candidate is qualified for native Apple silicon only")
    output = args.directory.resolve()
    if output.exists() or not args.wheelhouse.is_dir():
        parser.error("a new staging directory and existing wheelhouse are required")
    revision, dirty = source_identity()
    output.mkdir(parents=True)
    library = output / "library"
    extract_library(args.library_archive, library)
    record = json.loads((library / "INSTALL.json").read_bytes())
    if record["source_revision"] != lock["library"]["source_revision"]:
        raise ValueError("public library source does not match the input lock")
    extract_python(args.python_archive, output / "runtime")
    runtime = output / "runtime/python"
    site = output / "site-packages"
    # The public lock authenticates every dependency; no index/cache/source
    # resolver or Python download can supply unrecorded build inputs.
    subprocess.run(  # noqa: S603
        [
            str(args.uv.resolve()),
            "--no-config",
            "--offline",
            "--no-cache",
            "pip",
            "install",
            "--python",
            str(runtime / "bin/python3"),
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
        cwd=output,
        check=True,
        env={**os.environ, "UV_PYTHON_DOWNLOADS": "never"},
    )
    check_native_floor(site)
    app = output / "Sovereign.app"
    subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(PROJECT / "scripts/package_desktop.py"),
            "--runtime",
            str(runtime),
            "--site-packages",
            str(site),
            "--root-wheel",
            str(library / record["components"][0]["wheel"]),
            "--library-wheel",
            str(library / record["wheel"]),
            "--output",
            str(app),
            "--revision",
            revision,
            "--build-directory",
            str(output / "swift"),
            "--configuration",
            "release",
            "--uv",
            str(args.uv.resolve()),
            "--version",
            lock["version"],
            "--build-number",
            lock["build_number"],
        ],
        check=True,
    )
    receipt_path = app / "Contents/Resources/build-receipt.json"
    receipt = json.loads(receipt_path.read_bytes())
    notices = copy_runtime_notices(app / "Contents/Resources")
    receipt.update(
        schema_version="sovereign.desktop-build.v2",
        shell_source_revision=revision,
        shell_source_dirty=dirty,
        inputs=lock,
        software=record,
        configuration="release",
        distribution="ad-hoc-candidate",
        runtime_notices=notices,
    )
    receipt_path.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    subprocess.run(  # noqa: S603
        ["/usr/bin/codesign", "--force", "--sign", "-", str(app)], check=True
    )
    print(app)


if __name__ == "__main__":
    main()
