#!/usr/bin/env python3
"""Guided installation of the independently verified Sovereign Ubuntu release.

Uses only Ubuntu's Python standard library and APT. Run as your normal user;
administrator authorization is requested only after review and byte verification.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import ssl
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

SETUP_VERSION = "0.1.0"
VERSION = "0.1.0-3"
REPOSITORY = "https://apt.foxlight.ai"
RELEASE = (
    "https://github.com/Foxlight-Foundation/foxlight-model-preservation/"
    "releases/download/sovereign-linux-v" + VERSION
)
KEY_SHA256 = "f9a76cf4131603e3b7d905868efcf53f7ebea98ccdbfaf0d47ae0ac825cfd338"
PACKAGES = {
    "amd64": "455dc39253e24a8fd3f74c659a865c96fd7ba4e33a85845e02462493cdd8e599",
    "arm64": "f52f61121d56dd0ff181dda664318c546399b42ca32a2e284f11d4159faeef08",
}
KEY_PATH = Path("/usr/share/keyrings/sovereign-archive-keyring.gpg")
SOURCE_PATH = Path("/etc/apt/sources.list.d/sovereign.list")
SOURCE = (f"deb [signed-by={KEY_PATH}] {REPOSITORY} sovereign main\n").encode()
APT = [
    "/usr/bin/apt-get",
    "-o",
    "APT::Update::Error-Mode=any",
    "-o",
    "Acquire::AllowInsecureRepositories=false",
    "-o",
    "Acquire::AllowDowngradeToInsecureRepositories=false",
    "-o",
    "APT::Get::AllowUnauthenticated=false",
    "-o",
    "Acquire::Check-Valid-Until=true",
]


class SetupError(Exception):
    """A failed precondition that must stop setup without bypassing verification."""


def run(arguments: list[str]) -> None:
    """Run a fixed local program without a shell and stop on any failed step."""
    subprocess.run(arguments, check=True)  # noqa: S603


def architecture() -> str:
    """Require the qualified Ubuntu release and a native supported architecture."""
    if platform.system() != "Linux":
        raise SetupError(
            "This installer is for Ubuntu 24.04. Use the Mac installer on macOS."
        )
    release = platform.freedesktop_os_release()
    if release.get("ID") != "ubuntu" or release.get("VERSION_ID") != "24.04":
        raise SetupError(
            "Ubuntu 24.04 is required. Other Linux releases have not been qualified."
        )
    value = subprocess.check_output(  # noqa: S603
        ["/usr/bin/dpkg", "--print-architecture"], text=True
    ).strip()
    if value not in PACKAGES:
        raise SetupError(
            "This release supports 64-bit Intel/AMD (amd64) and ARM (arm64)."
        )
    return value


def validate_file(path: Path, expected: str) -> None:
    """Reject a missing, linked or changed input before it can be installed."""
    if path.is_symlink() or not path.is_file():
        raise SetupError(f"The downloaded file is missing or is a link: {path.name}")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected:
        raise SetupError(
            f"Verification failed for {path.name}. "
            "Nothing from this file will be installed."
        )


def check_configuration() -> None:
    """Preserve custom source/key settings rather than silently replacing them."""
    for path, expected in (
        (KEY_PATH, KEY_SHA256),
        (SOURCE_PATH, hashlib.sha256(SOURCE).hexdigest()),
    ):
        # The containing directories must also be real directories. Never follow
        # a substituted repository/key destination while running as administrator.
        if any(parent.is_symlink() for parent in path.parents):
            raise SetupError(
                f"Setup stopped because {path.parent} is a link. "
                "Your settings were preserved."
            )
        if not path.exists() and not path.is_symlink():
            continue
        if path.is_symlink() or not path.is_file():
            raise SetupError(
                f"Setup will not replace {path}. Your settings were preserved."
            )
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise SetupError(
                f"You already have custom settings at {path}. "
                "Setup left them unchanged. See the manual installation guide."
            )
        if path.stat().st_uid != 0 or stat.S_IMODE(path.stat().st_mode) & 0o022:
            raise SetupError(
                f"{path} needs root ownership and safe permissions. "
                "Setup left it unchanged; "
                "ask your administrator to check its permissions."
            )


def download(url: str, destination: Path, expected: str, limit: int) -> None:
    """Download bounded HTTPS bytes and require the independently pinned digest."""
    request = urllib.request.Request(  # noqa: S310 - fixed HTTPS release URLs
        url, headers={"User-Agent": "Foxlight-Sovereign-Setup/" + SETUP_VERSION}
    )
    with urllib.request.urlopen(  # noqa: S310 - fixed HTTPS release URLs
        request, timeout=60, context=ssl.create_default_context()
    ) as response:
        if not response.url.startswith("https://"):
            raise SetupError("The download left HTTPS. Setup stopped.")
        count = 0
        with destination.open("xb") as target:
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > limit:
                    raise SetupError(
                        "The download is larger than expected. Setup stopped."
                    )
                target.write(chunk)
    validate_file(destination, expected)


def write_new(path: Path, content: bytes) -> None:
    """Create a root-owned configuration file, preserving an existing exact file."""
    if path.exists():
        return  # check_configuration already required an exact, non-linked file.
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
    path.chmod(0o644)


def install_system(directory: Path, selected: str) -> None:
    """Recheck inputs, configure only the scoped source and install with secure APT."""
    if os.geteuid() != 0 or selected != architecture():
        raise SetupError(
            "The system installation step requires administrator authorization "
            "on the same architecture."
        )
    key = directory / KEY_PATH.name
    package = directory / f"foxlight-sovereign_{VERSION}_{selected}.deb"
    validate_file(key, KEY_SHA256)
    validate_file(package, PACKAGES[selected])
    check_configuration()
    write_new(KEY_PATH, key.read_bytes())
    write_new(SOURCE_PATH, SOURCE)
    print("\n2 of 3 — Checking signed software updates. Please wait…", flush=True)
    run([*APT, "update"])
    print(
        "\n3 of 3 — Installing Sovereign and Ubuntu's required desktop libraries…",
        flush=True,
    )
    # No allow-downgrades, allow-unauthenticated, kernel changes or model trust
    # grants. A later installed version cannot be silently downgraded by -y.
    run([*APT, "--no-remove", "install", "-y", str(package)])
    installed = subprocess.check_output(  # noqa: S603
        ["/usr/bin/dpkg-query", "-W", "-f=${Status} ${Version}", "foxlight-sovereign"],
        text=True,
    ).strip()
    if installed != "install ok installed " + VERSION:
        raise SetupError(
            "APT did not report the expected installed version. "
            "Setup stopped; retain the output for your administrator."
        )


def main() -> int:
    """Review setup, verify downloads as the user, then request bounded elevation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="check compatibility and existing settings without changing anything",
    )
    parser.add_argument(
        "--version", action="version", version="Sovereign setup " + SETUP_VERSION
    )
    parser.add_argument(
        "--system-install",
        nargs=2,
        metavar=("DIRECTORY", "ARCHITECTURE"),
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    try:
        if args.system_install:
            install_system(
                Path(args.system_install[0]).resolve(strict=True),
                args.system_install[1],
            )
            return 0
        selected = architecture()
        check_configuration()
        print(f"\nSovereign setup — Ubuntu 24.04 ({selected})\n")
        print(
            "Keep AI models, their required files and signed provenance. "
            "Recover them even if the original source disappears."
        )
        print(
            "\nThis will install Sovereign "
            + VERSION
            + " and add its signed Foxlight update source."
        )
        print(
            "Ubuntu will also install any missing desktop libraries. "
            "Your administrator password may be needed."
        )
        print(
            "No Skulk, account or GPU is required. "
            "This starts no sharing, model downloads or background service."
        )
        print(
            "Your Library and recovery files remain yours "
            "when the app or update source is removed."
        )
        if args.check:
            print("\nCompatibility check passed. No files were changed or downloaded.")
            return 0
        if os.geteuid() == 0:
            raise SetupError(
                "Run this installer as your normal user. "
                "It will ask for administrator authorization when needed."
            )
        if (
            input(
                "\nInstall Sovereign and enable its signed updates? "
                "Type install to continue: "
            )
            .strip()
            .lower()
            != "install"
        ):
            print("Setup cancelled. Nothing was changed or downloaded.")
            return 0
        print(
            "\n1 of 3 — Downloading and verifying the software key and app. "
            "No models are downloaded…",
            flush=True,
        )
        with tempfile.TemporaryDirectory(prefix="sovereign-setup-") as temporary:
            directory = Path(temporary)
            directory.chmod(
                0o755
            )  # Public verified bytes must be readable by APT's _apt user.
            download(
                REPOSITORY + "/" + KEY_PATH.name,
                directory / KEY_PATH.name,
                KEY_SHA256,
                1024 * 1024,
            )
            name = f"foxlight-sovereign_{VERSION}_{selected}.deb"
            download(
                RELEASE + "/" + name,
                directory / name,
                PACKAGES[selected],
                256 * 1024 * 1024,
            )
            print(
                "\nBoth files verified. Ubuntu will now request permission "
                "to install the app. Enter your Ubuntu password only "
                "at its password prompt.",
                flush=True,
            )
            run(
                [
                    "/usr/bin/sudo",
                    "/usr/bin/python3",
                    "-I",
                    str(Path(__file__).resolve()),
                    "--system-install",
                    str(directory),
                    selected,
                ]
            )
        print("\nSovereign is ready. Open Sovereign from your Applications menu.")
        print(
            "Choose Browse public models, Open recovery kit, or "
            "Open sharing file or source. Help → Getting started "
            "is always available."
        )
        print(
            "\nUpdates arrive through Ubuntu's normal software updates. "
            "To remove only the app: sudo apt remove foxlight-sovereign"
        )
        print(
            f"To stop Foxlight updates: sudo rm {SOURCE_PATH} "
            "(then sudo apt update). Your Library and saved kits are retained."
        )
        return 0
    except (
        SetupError,
        OSError,
        subprocess.CalledProcessError,
        urllib.error.URLError,
        EOFError,
    ) as error:
        print(f"\nSetup stopped: {error}", file=sys.stderr)
        print(
            "No verification checks were bypassed. If Ubuntu reports a network "
            "or package error, fix it and run setup again. "
            "Existing custom settings and your Library are retained.",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print(
            "\nSetup interrupted. Run it again when ready; your Library is retained.",
            file=sys.stderr,
        )
        return 130


if __name__ == "__main__":
    sys.exit(main())
