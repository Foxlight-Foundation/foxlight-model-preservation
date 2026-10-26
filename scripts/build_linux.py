#!/usr/bin/env python3
"""Build a native Debian candidate offline from pinned public software inputs.

The result is an unsigned local/CI candidate, not a signed APT publication. Only
its own AppArmor policy is reloaded; no services, account or network installers.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

from build_macos import check_input, digest, extract_library, extract_python

PROJECT = Path(__file__).resolve().parents[1]


def normalize_installer_metadata(site: Path) -> None:
    """Remove generated cache/stub records; retain wheel identity in the receipt."""
    if (site / "bin").exists():
        shutil.rmtree(site / "bin")
    for metadata in site.glob("*.dist-info"):
        removed = {"uv_cache.json", "direct_url.json"}
        for name in removed:
            (metadata / name).unlink(missing_ok=True)
        record = metadata / "RECORD"
        with record.open(newline="") as stream:
            rows = list(csv.reader(stream))
        with record.open("w", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            for row in rows:
                if row[0].startswith(("../bin/", "bin/")) or row[0] in {
                    metadata.name + "/" + name for name in removed
                }:
                    continue
                writer.writerow(row)


def runtime_notices(archive: Path, target: Path) -> dict[str, str]:
    """Copy bounded notices and the original runtime inventory from its full build."""
    target.mkdir()
    found: dict[str, str] = {}
    with subprocess.Popen(  # noqa: S603 - verified local archive, no shell
        ["/usr/bin/zstd", "--decompress", "--stdout", str(archive)],
        stdout=subprocess.PIPE,
    ) as process:
        with tarfile.open(fileobj=process.stdout, mode="r|") as source:
            for member in source:
                path = PurePosixPath(member.name)
                if not (
                    (path.parent == PurePosixPath("python/licenses"))
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
                stream = source.extractfile(member)
                if stream is None:
                    raise ValueError("runtime notice missing")
                content = stream.read()
                (target / path.name).write_bytes(content)
                found[path.name] = hashlib.sha256(content).hexdigest()
        if process.wait() != 0:
            raise ValueError("runtime notice archive could not be decoded")
    if "LICENSE.cpython.txt" not in found or "PYTHON.json" not in found:
        raise ValueError("original Python runtime notices required")
    return found


def main() -> None:
    """Assemble one architecture on its native Linux runner without downloading."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--library-archive", type=Path, required=True)
    parser.add_argument("--python-archive", type=Path, required=True)
    parser.add_argument("--notices-archive", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--uv", type=Path, default=Path(shutil.which("uv") or "uv"))
    args = parser.parse_args()
    lock = json.loads((PROJECT / "desktop/linux/inputs.json").read_bytes())
    selected = lock["architectures"][args.architecture]
    if platform.system() != "Linux" or platform.machine() != selected["machine"]:
        parser.error("build on the selected native Linux architecture")
    for path, expected in (
        (args.library_archive, lock["library"]),
        (args.python_archive, selected["python"]),
        (args.notices_archive, selected["notices"]),
    ):
        check_input(path, expected["sha256"])
    output = args.directory.resolve()
    if output.exists() or not args.wheelhouse.is_dir():
        parser.error("new staging directory and existing wheelhouse required")
    revision = subprocess.check_output(  # noqa: S603
        ["/usr/bin/git", "-C", str(PROJECT), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = bool(
        subprocess.check_output(  # noqa: S603
            ["/usr/bin/git", "-C", str(PROJECT), "status", "--porcelain"], text=True
        ).strip()
    )
    epoch = subprocess.check_output(  # noqa: S603
        ["/usr/bin/git", "-C", str(PROJECT), "show", "-s", "--format=%ct", "HEAD"],
        text=True,
    ).strip()
    environment = {
        **os.environ,
        "SOURCE_DATE_EPOCH": epoch,
        "UV_PYTHON_DOWNLOADS": "never",
    }
    output.mkdir(parents=True)
    library = output / "library"
    extract_library(args.library_archive, library)
    software = json.loads((library / "INSTALL.json").read_bytes())
    if software["source_revision"] != lock["library"]["source_revision"]:
        raise ValueError("public Library source differs from input lock")
    extract_python(args.python_archive, output / "runtime")
    tree = output / "package"
    resources = tree / "opt/foxlight/sovereign"
    resources.mkdir(parents=True)
    shutil.move(str(output / "runtime/python"), resources / "python")
    site = resources / "site-packages"
    subprocess.run(  # noqa: S603 - locked offline dependencies only
        [
            str(args.uv.resolve()),
            "--no-config",
            "--offline",
            "--no-cache",
            "pip",
            "install",
            "--python",
            str(resources / "python/bin/python3"),
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
    if any((site / name).exists() for name in ("skulk", "skulk_capability_sdk")):
        raise ValueError("independent package cannot contain Core or SDK")
    # Generated entry points embed staging paths. Only package-owned launchers
    # below are supported; do not ship an ambient-Python or build-path stub.
    normalize_installer_metadata(site)
    shutil.copyfile(
        PROJECT / "desktop/macos/cli/native-cli.py", resources / "native-cli.py"
    )
    shutil.copyfile(
        PROJECT / "desktop/linux/sovereign-linux.py", resources / "sovereign-linux.py"
    )
    notices = runtime_notices(args.notices_archive, resources / "runtime-notices")
    (resources / "build-receipt.json").write_text(
        json.dumps(
            {
                "schema_version": "sovereign.linux-build.v1",
                "source_revision": revision,
                "source_dirty": dirty,
                "inputs": lock,
                "architecture": args.architecture,
                "software": software,
                "runtime_notices": notices,
                "distribution": "unsigned-candidate",
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    commands = tree / "usr/bin"
    commands.mkdir(parents=True)
    for name, source in (
        ("model-preserve", "launcher.sh"),
        ("foxlight-sovereign", "launcher.sh"),
    ):
        shutil.copyfile(PROJECT / "desktop/linux" / source, commands / name)
        (commands / name).chmod(0o755)
    subprocess.run(  # noqa: S603 - fixed compiler, application-specific identity
        [
            "/usr/bin/cc",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-fPIE",
            "-fstack-protector-strong",
            "-D_FORTIFY_SOURCE=3",
            "-pie",
            "-Wl,-z,relro,-z,now",
            str(PROJECT / "desktop/linux/launcher.c"),
            "-o",
            str(commands / "sovereign"),
        ],
        check=True,
        env=environment,
    )
    policy = tree / "etc/apparmor.d"
    policy.mkdir(parents=True)
    shutil.copyfile(
        PROJECT / "desktop/linux/ai.foxlight.sovereign",
        policy / "ai.foxlight.sovereign",
    )
    applications = tree / "usr/share/applications"
    applications.mkdir(parents=True)
    shutil.copyfile(
        PROJECT / "desktop/linux/sovereign.desktop",
        applications / "ai.foxlight.Sovereign.desktop",
    )
    icons = tree / "usr/share/icons/hicolor/scalable/apps"
    icons.mkdir(parents=True)
    shutil.copyfile(
        PROJECT / "capability/web/src/branding/sovereign-icon.svg",
        icons / "ai.foxlight.Sovereign.svg",
    )
    documentation = tree / "usr/share/doc/foxlight-sovereign"
    documentation.mkdir(parents=True)
    shutil.copyfile(PROJECT / "LICENSE", documentation / "copyright")
    control = tree / "DEBIAN"
    control.mkdir()
    version = lock["version"] + "-" + lock["revision"]
    size = sum(path.stat().st_size for path in tree.rglob("*") if path.is_file())
    (control / "control").write_text(
        f"Package: foxlight-sovereign\nVersion: {version}\n"
        f"Architecture: {args.architecture}\n"
        "Maintainer: Foxlight Foundation\nSection: utils\nPriority: optional\n"
        "Depends: apparmor (>= 4.0), python3 (>= 3.11), python3-gi, gir1.2-gtk-3.0, "
        "gir1.2-webkit2-4.1, xdg-utils, ca-certificates, libc6 (>= 2.35), "
        "libgcc-s1, libstdc++6\n"
        f"Installed-Size: {(size + 1023) // 1024}\n"
        "Homepage: https://github.com/Foxlight-Foundation/foxlight-model-preservation\n"
        "Description: Independent AI model preservation and recovery\n"
        " Find, privately preserve and restore exact models, companions and signed\n"
        " provenance without Skulk. Installation starts no services or sharing.\n"
    )
    (control / "conffiles").write_text("/etc/apparmor.d/ai.foxlight.sovereign\n")
    for name in ("postinst", "postrm"):
        shutil.copyfile(PROJECT / "desktop/linux" / name, control / name)
        (control / name).chmod(0o755)
    for path in tree.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
        elif not path.is_symlink():
            path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
    artifact = output / f"foxlight-sovereign_{version}_{args.architecture}.deb"
    subprocess.run(  # noqa: S603 - package assembly, no maintainer scripts
        [
            "/usr/bin/dpkg-deb",
            "--root-owner-group",
            "--uniform-compression",
            "-Zxz",
            "-z6",
            "--build",
            str(tree),
            str(artifact),
        ],
        check=True,
        env=environment,
    )
    (output / "SHA256SUMS").write_text(digest(artifact) + "  " + artifact.name + "\n")
    print(artifact)


if __name__ == "__main__":
    main()
