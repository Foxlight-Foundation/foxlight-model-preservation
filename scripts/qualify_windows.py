#!/usr/bin/env python3
"""Qualify an unsigned Windows MSI on a clean Windows: install, run, upgrade, remove.

Runs on Windows x64 as the signed-in user, without administrator rights:

1. Install the MSI per user; check the folder, the Start menu entry and the
   Apps entry.
2. The window's WebView2 check passes, and its test switch reports the
   runtime as missing.
3. The bundled runtime loads its native modules (libtorrent, cryptography).
4. Start the installed Library exactly as the window does, read its one-time
   address, load its page, then close its standard input: it stops cleanly.
5. Nothing was written into the install folder.
6. Install the upgrade MSI over it: one installed copy remains.
7. Uninstall: the app is gone, the Library's data stays.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import threading
import time
import urllib.request
import winreg
from pathlib import Path

INSTALL = Path(os.environ["LOCALAPPDATA"]) / "Programs/Sovereign"
HOME = Path(os.environ["LOCALAPPDATA"]) / "Foxlight/Sovereign"
SHORTCUT = (
    Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Sovereign.lnk"
)
MSIEXEC = Path(os.environ["SystemRoot"]) / "System32/msiexec.exe"
ADDRESS = re.compile(r"Sovereign: (http://127\.0\.0\.1:[0-9]+/s/[0-9a-f]{64}/)")
START = (
    "import sys,runpy;sys.path.insert(0,sys.argv.pop(1));"
    "runpy.run_module('foxlight_preservation_library.desktop',run_name='__main__')"
)


def msiexec(*arguments: str, log: Path) -> None:
    result = subprocess.run(  # noqa: S603 - the system installer, fixed arguments
        [str(MSIEXEC), *arguments, "/qn", "/l*v", str(log)], check=False
    )
    if result.returncode != 0:
        tail = log.read_text(encoding="utf-16", errors="replace")[-4000:]
        raise SystemExit(
            f"msiexec {arguments[0]} failed ({result.returncode}):\n{tail}"
        )


def installed_products() -> list[str]:
    """Per-user Windows Installer products named Sovereign."""
    names = []
    try:
        products = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Installer\Products"
        )
    except FileNotFoundError:
        return names
    with products:
        for index in range(winreg.QueryInfoKey(products)[0]):
            code = winreg.EnumKey(products, index)
            with winreg.OpenKey(products, code) as product:
                try:
                    name, _ = winreg.QueryValueEx(product, "ProductName")
                except FileNotFoundError:
                    continue
            if name == "Sovereign":
                names.append(code)
    return names


def inventory(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def shell_check(missing: bool) -> int:
    environment = dict(os.environ)
    environment.pop("SOVEREIGN_TEST_WEBVIEW2_MISSING", None)
    if missing:
        environment["SOVEREIGN_TEST_WEBVIEW2_MISSING"] = "1"
    return subprocess.run(  # noqa: S603 - the installed window, fixed argument
        [str(INSTALL / "Sovereign.exe"), "--check-webview2"],
        env=environment,
        check=False,
        timeout=60,
    ).returncode


def run_library() -> None:
    """Start, load and stop the installed Library the way the window does."""
    child = subprocess.Popen(  # noqa: S603 - the installed runtime, fixed arguments
        [
            str(INSTALL / "python/python.exe"),
            "-I",
            "-B",
            "-c",
            START,
            str(INSTALL / "site-packages"),
            "--no-browser",
            "--stop-on-input-close",
        ],
        cwd=INSTALL,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    stdin, stdout = child.stdin, child.stdout
    if stdin is None or stdout is None:
        raise SystemExit("the Library's pipes did not open")
    lines: list[bytes] = []
    reader = threading.Thread(target=lambda: lines.append(stdout.readline()))
    reader.start()
    reader.join(120)
    match = ADDRESS.fullmatch(lines[0].decode().strip()) if lines else None
    if match is None:
        child.kill()
        _, error = child.communicate()
        raise SystemExit("the Library printed no address:\n" + error.decode()[-4000:])
    with urllib.request.urlopen(match[1], timeout=30) as response:  # noqa: S310
        page = response.read()
        if response.status != 200 or b"<html" not in page.lower():
            raise SystemExit("the Library page did not load")
    started = time.monotonic()
    stdin.close()
    code = child.wait(60)
    if code != 0:
        raise SystemExit(f"the Library stopped with {code}")
    elapsed = time.monotonic() - started
    print(f"library: page served; stopped {elapsed:.1f} s after its input closed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--msi", type=Path, required=True)
    parser.add_argument("--upgrade-msi", type=Path, required=True)
    parser.add_argument("--logs", type=Path, required=True)
    args = parser.parse_args()
    args.logs.mkdir(parents=True, exist_ok=True)
    if INSTALL.exists() or installed_products():
        raise SystemExit("qualify on a Windows without Sovereign installed")

    msiexec("/i", str(args.msi.resolve()), log=args.logs / "install.log")
    required = (
        "Sovereign.exe",
        "python/python.exe",
        "site-packages",
        "build-receipt.json",
    )
    for path in required:
        if not (INSTALL / path).exists():
            raise SystemExit(f"installed app lacks {path}")
    if not SHORTCUT.is_file():
        raise SystemExit("no Start menu entry")
    if len(installed_products()) != 1:
        raise SystemExit("expected one installed Sovereign")
    print(f"install: per user at {INSTALL}, {len(inventory(INSTALL))} files")

    before = inventory(INSTALL)
    if shell_check(missing=True) != 3:
        raise SystemExit("the WebView2 test switch did not report a missing runtime")
    available = shell_check(missing=False)
    if available not in (0, 3):
        raise SystemExit(f"the WebView2 check failed ({available})")
    print("webview2: " + ("available" if available == 0 else "absent on this runner"))

    subprocess.run(  # noqa: S603 - the installed runtime, fixed arguments
        [
            str(INSTALL / "python/python.exe"),
            "-I",
            "-B",
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "import libtorrent, cryptography.hazmat.bindings._rust, pydantic_core; "
            "import foxlight_model_preservation; "
            "import foxlight_preservation_library.desktop; "
            "print('native modules: libtorrent', libtorrent.__version__)",
            str(INSTALL / "site-packages"),
        ],
        check=True,
    )
    run_library()
    if not HOME.is_dir():
        raise SystemExit(f"the Library did not create {HOME}")
    if inventory(INSTALL) != before:
        raise SystemExit("running the app wrote into its install folder")
    print("install folder: unchanged by running")

    msiexec("/i", str(args.upgrade_msi.resolve()), log=args.logs / "upgrade.log")
    if len(installed_products()) != 1:
        raise SystemExit("the upgrade left more than one installed Sovereign")
    print("upgrade: replaced in place")

    msiexec("/x", str(args.upgrade_msi.resolve()), log=args.logs / "uninstall.log")
    left = (INSTALL / "Sovereign.exe").exists() or SHORTCUT.exists()
    if left or installed_products():
        raise SystemExit("uninstall left the app behind")
    if not HOME.is_dir():
        raise SystemExit("uninstall removed the Library's data")
    print("uninstall: app removed, Library data kept")
    print("QUALIFIED")


if __name__ == "__main__":
    main()
