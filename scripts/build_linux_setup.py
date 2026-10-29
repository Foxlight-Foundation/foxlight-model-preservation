#!/usr/bin/env python3
"""Build the small, architecture-independent graphical Ubuntu setup package offline."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SETUP_VERSION = "0.1.1"
PACKAGE_VERSION = SETUP_VERSION + "-1"
SHARE = Path("usr/share/foxlight-sovereign-setup")


def stage(directory: Path) -> Path:
    """Stage only reviewed local code, art and metadata; never launch or download."""
    directory.mkdir(parents=True, exist_ok=False)
    files = {
        "scripts/install_sovereign_linux.py": SHARE / "installer.py",
        "desktop/linux-setup/setup.py": SHARE / "setup.py",
        "desktop/linux-setup/setup.css": SHARE / "setup.css",
        "capability/web/src/branding/sovereign-dark-navbar.svg": SHARE / "wordmark.svg",
        "desktop/linux-setup/install": Path("usr/libexec/sovereign-setup-install"),
        "desktop/linux-setup/launcher": Path("usr/bin/sovereign-setup"),
        "desktop/linux-setup/sovereign-setup.desktop": Path(
            "usr/share/applications/ai.foxlight.Sovereign.Setup.desktop"
        ),
        "desktop/linux-setup/ai.foxlight.sovereign.setup.policy": Path(
            "usr/share/polkit-1/actions/ai.foxlight.sovereign.setup.policy"
        ),
        "capability/web/src/branding/sovereign-icon.svg": Path(
            "usr/share/icons/hicolor/scalable/apps/ai.foxlight.Sovereign.Setup.svg"
        ),
        "LICENSE": Path("usr/share/doc/foxlight-sovereign-setup/copyright"),
    }
    executable = {"usr/libexec/sovereign-setup-install", "usr/bin/sovereign-setup"}
    for source, relative in files.items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT / source, target)
        target.chmod(0o755 if str(relative) in executable else 0o644)
    size = sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())
    control = directory / "DEBIAN"
    control.mkdir()
    (control / "control").write_text(
        "Package: foxlight-sovereign-setup\n"
        f"Version: {PACKAGE_VERSION}\nArchitecture: all\n"
        "Maintainer: Foxlight Foundation\nSection: utils\nPriority: optional\n"
        "Depends: python3 (>= 3.12), python3-gi, gir1.2-gtk-3.0, "
        "librsvg2-common, pkexec, ca-certificates\n"
        f"Installed-Size: {(size + 1023) // 1024}\n"
        "Homepage: https://github.com/Foxlight-Foundation/foxlight-model-preservation\n"
        "Description: Graphical setup for independent Sovereign model preservation\n"
        " Review and install Sovereign on Ubuntu 24.04 without a terminal.\n"
        " Verifies the exact app and scoped software key before requesting Ubuntu\n"
        " authorization and enabling signed updates. No Skulk, account or GPU.\n"
    )
    directory.chmod(0o755)
    (control / "control").chmod(0o644)
    for path in directory.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
    return directory


def main() -> None:
    """Assemble a reproducible all-architecture .deb with no maintainer scripts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    output = args.directory.resolve()
    if output.exists():
        parser.error("a new staging directory is required")
    tree = stage(output / "package")
    epoch = subprocess.check_output(  # noqa: S603 - fixed local Git query
        ["/usr/bin/git", "-C", str(PROJECT), "show", "-s", "--format=%ct", "HEAD"],
        text=True,
    ).strip()
    artifact = output / f"foxlight-sovereign-setup_{PACKAGE_VERSION}_all.deb"
    subprocess.run(  # noqa: S603 - offline package assembly, no shell
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
        env={**os.environ, "SOURCE_DATE_EPOCH": epoch},
    )
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    (output / "SHA256SUMS").write_text(digest + "  " + artifact.name + "\n")
    print(artifact)


if __name__ == "__main__":
    main()
