"""Secrets in the operating system's keychain, never in files or arguments.

macOS uses the Keychain through ``security -i`` (the secret is read from
stdin, hex-encoded, so it never appears in a process argument list); other
systems use the Secret Service through ``secret-tool``. Failures never carry
the secret: subprocess errors are not chained and tool output is not shown.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .models import PreservationError

_NAME = re.compile(r"[A-Za-z0-9._-]{1,128}")


def _check(service: str, account: str, error: str) -> None:
    if not _NAME.fullmatch(service) or not _NAME.fullmatch(account):
        raise PreservationError(error)


def _keychain_arg(path: Path) -> str:
    text = str(path)
    if not re.fullmatch(r"[A-Za-z0-9._/~-]+", text):
        raise PreservationError("bucket_keychain_path_invalid")
    return text


def save_secret(  # noqa: PLR0913
    service: str,
    account: str,
    secret: str,
    *,
    label: str,
    error: str,
    keychain: Path | None = None,
) -> None:
    """Store one secret; ``error`` is the code raised on any failure."""
    _check(service, account, error)
    if os.uname().sysname == "Darwin":
        run(
            ["/usr/bin/security", "-i"],
            f"add-generic-password -U -s {service} -a {account} "
            f"-X {secret.encode().hex()}"
            + (f" {_keychain_arg(keychain)}" if keychain else "")
            + "\n",
            error,
        )
    else:
        run(
            [
                "secret-tool",
                "store",
                "--label",
                label,
                "service",
                service,
                "account",
                account,
            ],
            secret,
            error,
        )


def load_secret(
    service: str, account: str, *, error: str, keychain: Path | None = None
) -> str:
    """Read one secret back; a missing entry raises ``error``."""
    _check(service, account, error)
    if os.uname().sysname == "Darwin":
        raw = run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                service,
                "-a",
                account,
                "-w",
                *([str(keychain)] if keychain else []),
            ],
            None,
            error,
        )
    else:
        raw = run(
            ["secret-tool", "lookup", "service", service, "account", account],
            None,
            error,
        )
    return raw.rstrip("\n")


# ``security`` exits with errSecItemNotFound's code when nothing is stored.
_MAC_NOT_FOUND = 44


def delete_secret(
    service: str,
    account: str,
    *,
    error: str,
    keychain: Path | None = None,
    strict: bool = False,
) -> None:
    """Remove one secret; a missing entry is already removed.

    With ``strict``, any other failure (a locked or unavailable keychain)
    raises ``error``, so a caller never reports a secret removed that is
    still stored.
    """
    _check(service, account, error)
    if os.uname().sysname == "Darwin":
        command = [
            "/usr/bin/security",
            "delete-generic-password",
            "-s",
            service,
            "-a",
            account,
            *([str(keychain)] if keychain else []),
        ]
    else:
        command = ["secret-tool", "clear", "service", service, "account", account]
    if not strict:
        try:
            run(command, None, error)
        except PreservationError:
            pass  # Already absent, or the keychain is unavailable; nothing to leak.
        return
    returncode = _status(command, error)
    darwin = os.uname().sysname == "Darwin"
    if returncode != 0 and not (darwin and returncode == _MAC_NOT_FOUND):
        raise PreservationError(error)


def _status(command: list[str], error: str) -> int:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed system keychain tools
            command, capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PreservationError(error) from None
    return completed.returncode


def run(command: list[str], stdin: str | None, error: str) -> str:
    """Run a fixed keychain tool; any failure raises ``error`` without detail."""
    try:
        completed = subprocess.run(  # noqa: S603 - fixed system keychain tools
            command,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        # The original exception can carry the command and its input; it is
        # deliberately not chained.
        raise PreservationError(error) from None
    if completed.returncode != 0:
        # Keychain tools may echo arguments; never surface their output.
        raise PreservationError(error)
    return completed.stdout
