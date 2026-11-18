"""Owner-only and shared access for test fixtures, on every platform.

POSIX tests used ``chmod``; Windows keeps the same meaning in access lists
(design: foxlight-docs windows-storage-2026-11-17).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _icacls(*arguments: str) -> None:
    subprocess.run(  # noqa: S603 - the system tool, test paths
        ["icacls", *arguments],  # noqa: S607
        check=True,
        capture_output=True,
    )


def make_private(path: Path) -> None:
    """Only this user may reach ``path`` (``chmod 700``/``600`` on POSIX)."""
    if sys.platform != "win32":
        path.chmod(0o700 if path.is_dir() else 0o600)
        return
    from foxlight_model_preservation import _winfs  # noqa: PLC0415

    user = _winfs._user_sid()  # noqa: SLF001 - test support
    inherit = "(OI)(CI)" if path.is_dir() else ""
    _icacls(str(path), "/reset")
    _icacls(str(path), "/setowner", f"*{user}")
    _icacls(
        str(path),
        "/inheritance:r",
        "/grant:r",
        f"*{user}:{inherit}F",
        f"*S-1-5-18:{inherit}F",
    )


def make_shared(path: Path) -> None:
    """Others may read ``path`` too (``chmod 755``/``644`` on POSIX)."""
    if sys.platform != "win32":
        path.chmod(0o755 if path.is_dir() else 0o644)
        return
    inherit = "(OI)(CI)" if path.is_dir() else ""
    _icacls(str(path), "/grant", f"*S-1-1-0:{inherit}R")  # Everyone
