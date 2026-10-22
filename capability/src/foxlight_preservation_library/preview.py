"""Install and explicitly launch an isolated, pinned preservation preview."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import stat
import subprocess
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

import httpx

# Updated only after the compatible Core candidate is integrated and verified.
CORE_REVISION = "c9b0d910558d4cd6a8372b29b10c1d6930fbdfe5"
CORE_INSTALLER_SHA256 = (
    "24cc7d0c3e33cb562883050def7a9f64c0e3abfdf15820bf8b21e8cc02c951b3"
)
LIBRARY_WHEEL = "https://github.com/Foxlight-Foundation/foxlight-model-preservation/releases/download/library-v0.6.0/foxlight_preservation_library-0.6.0-py3-none-any.whl"


class PreviewRefused(ValueError):
    """Actionable local setup refusal without deleting existing software or state."""


def _write(path: Path, content: str) -> None:
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "w") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _receipt(root: Path) -> dict[str, str | int]:
    info = root.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise PreviewRefused(
            "Use the original private preview directory, "
            "not a symlink or shared folder."
        )
    descriptor = os.open(
        root / "preview.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    )
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise PreviewRefused(
                "The preview receipt is unavailable. "
                "Keep this directory for inspection."
            )
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise PreviewRefused(
            "The preview receipt is too large. Keep this directory for inspection."
        )
    value: object = json.loads(raw)
    if not isinstance(value, dict):
        raise PreviewRefused(
            "The preview receipt is invalid. Keep this directory for inspection."
        )
    receipt = cast(dict[str, str | int], value)
    if (
        receipt.get("format") != 1
        or receipt.get("core_revision") != CORE_REVISION
        or (receipt.get("device"), receipt.get("inode")) != (info.st_dev, info.st_ino)
        or not isinstance(receipt.get("namespace"), str)
        or len(str(receipt["namespace"])) != 64
    ):
        raise PreviewRefused(
            "This directory does not match this pinned preview. "
            "Keep its contents and select a new directory."
        )
    return receipt


@contextmanager
def _preview_lock(
    root: Path, *, exclusive: bool, name: str = "preview.lock"
) -> Generator[None]:
    descriptor = os.open(
        root / name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise PreviewRefused("The preview lock is not a private regular file.")
        try:
            fcntl.flock(
                descriptor,
                (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB,
            )
        except BlockingIOError as error:
            raise PreviewRefused(
                "This preview is in use. Stop its runtimes before setup, "
                "or wait for the current setup to finish."
            ) from error
        yield
    finally:
        os.close(descriptor)


def _install_core(command: list[str], root: Path, environment: dict[str, str]) -> None:
    descriptor = os.open(
        root / "installation.log",
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(descriptor, "a") as log:
        with subprocess.Popen(
            command,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        ) as process:
            assert process.stdout is not None
            postamble = False
            for line in process.stdout:
                log.write(line)
                log.flush()
                plain = re.sub(r"\x1b\[[0-9;]*m", "", line)
                if plain.startswith(
                    "[skulk-install] install complete. Start a node with:"
                ):
                    postamble = True
                if not postamble:
                    print(line, end="", flush=True)
            status = process.wait()
            if status:
                raise subprocess.CalledProcessError(status, command)


def _environment(root: Path, receipt: dict[str, str | int]) -> dict[str, str]:
    environment = dict(os.environ)
    for key in ("VIRTUAL_ENV", "CONDA_PREFIX", "UV_ACTIVE"):
        environment.pop(key, None)
    return {
        **environment,
        "SKULK_HOME": str(root / "state"),
        "SKULK_MODELS_DIR": str(root / "worker-models"),
        "SKULK_LIBP2P_NAMESPACE": str(receipt["namespace"]),
        "UV_NO_DEV": "1",
        "UV_PROJECT_ENVIRONMENT": str(root / "core" / ".venv"),
        "UV_PROJECT": str(root / "core"),
        "SKULK_MODELS_PATH": str(root / "staging"),
        "SKULK_BOOTSTRAP_PEERS": "",
        "UV_TOOL_DIR": str(root / "tools"),
        "UV_TOOL_BIN_DIR": str(root / "bin"),
    }


def _clean_checkout(core: Path) -> None:
    if core.is_symlink() or (core / ".git").is_symlink():
        raise PreviewRefused("The preview checkout changed. Keep its contents.")
    if not (core / ".git").exists():
        return
    head = subprocess.run(
        ["git", "-C", str(core), "rev-parse", "--verify", "HEAD"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if head.returncode == 0:
        # The canonical dashboard builder may rewrite its own lock file.
        clean = subprocess.run(
            [
                "git",
                "-C",
                str(core),
                "diff",
                "--quiet",
                "HEAD",
                "--",
                ".",
                ":!dashboard-react/package-lock.json",
            ],
            check=False,
        )
        untracked = subprocess.check_output(
            ["git", "-C", str(core), "ls-files", "--others", "--exclude-standard"],
            text=True,
        )
        if clean.returncode or untracked.strip():
            raise PreviewRefused(
                "The preview checkout has local edits. "
                "Keep them and select a new preview directory."
            )


def setup(root: Path, *, resume: bool, with_dashboard: bool) -> None:
    """Install the pinned public Core and Library without starting either.

    Only a newly created private directory or an explicitly resumed matching
    preview is used. Existing installations, pairing and cluster state are not
    selected. The canonical Core installer still owns dependency/build handling.
    """
    for command in ("git", "uv", "bash"):
        if shutil.which(command) is None:
            raise PreviewRefused(
                "Install "
                + command
                + " first, then rerun setup. See the preview installation guide."
            )
    if root.exists() or root.is_symlink():
        if not resume:
            raise PreviewRefused(
                "That directory already exists. Choose a new directory, "
                "or --resume only your matching preview."
            )
        receipt = _receipt(root)
    else:
        if resume:
            raise PreviewRefused("No preview exists there. Run setup without --resume.")
        root.mkdir(mode=0o700)
        info = root.stat()
        receipt = {
            "format": 1,
            "core_revision": CORE_REVISION,
            "device": info.st_dev,
            "inode": info.st_ino,
            "namespace": secrets.token_hex(32),
        }
        _write(root / "preview.json", json.dumps(receipt, sort_keys=True) + "\n")
    with _preview_lock(root, exclusive=True):
        _setup(root, receipt, with_dashboard=with_dashboard)


def _setup(root: Path, receipt: dict[str, str | int], *, with_dashboard: bool) -> None:
    environment = _environment(root, receipt)
    core = root / "core"
    if core.is_symlink():
        raise PreviewRefused("The preview checkout is a symlink. No installer was run.")
    if not core.exists():
        core.mkdir(mode=0o700)
    _clean_checkout(core)
    config = core / "skulk.yaml"
    if config.is_symlink() or (config.exists() and not config.is_file()):
        raise PreviewRefused("The preview configuration changed. Keep its contents.")
    if not config.exists():
        _write(
            config,
            json.dumps(
                {
                    "model_store": {
                        "store_host": socket.gethostname().split(".")[0],
                        "store_port": 13415,
                        "store_http_host": "127.0.0.1",
                        "store_path": str(root / "model-store"),
                        "staging": {"node_cache_path": str(root / "staging")},
                    }
                },
                indent=2,
            )
            + "\n",
        )
    # Verify the exact immutable installer before executing it. A redirect or
    # oversized response never becomes shell input, and nothing is piped to bash.
    installer = bytearray()
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=60) as client:
        with client.stream(
            "GET",
            "https://raw.githubusercontent.com/Foxlight-Foundation/Skulk/"
            + CORE_REVISION
            + "/install.sh",
        ) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                installer.extend(chunk)
                if len(installer) > 1024 * 1024:
                    raise PreviewRefused(
                        "The pinned Core installer exceeded its expected limit."
                    )
    if hashlib.sha256(installer).hexdigest() != CORE_INSTALLER_SHA256:
        raise PreviewRefused(
            "The pinned Core installer checksum did not match. No installer was run."
        )
    installer_path = root / "install-core.sh"
    if installer_path.exists() or installer_path.is_symlink():
        descriptor = os.open(
            installer_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        )
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode) or stream.read(
                1024 * 1024 + 1
            ) != bytes(installer):
                raise PreviewRefused(
                    "The retained installer changed. "
                    "Keep this directory for inspection."
                )
    else:
        _write(installer_path, installer.decode("utf-8"))
    print(
        "Installing compatible Core preview "
        + CORE_REVISION
        + ". Native prerequisites and compilation may take several minutes.",
        flush=True,
    )
    _install_core(
        [
            "bash",
            str(installer_path),
            "--dir",
            str(core),
            "--ref",
            CORE_REVISION,
            *([] if with_dashboard else ["--headless"]),
        ],
        root,
        environment,
    )
    actual = subprocess.check_output(
        ["git", "-C", str(core), "rev-parse", "HEAD"], text=True
    ).strip()
    if actual != CORE_REVISION:
        raise PreviewRefused(
            "Core did not resolve to the pinned revision. No runtime was started."
        )
    subprocess.run(
        ["uv", "tool", "install", "--force", "--python", "3.13", LIBRARY_WHEEL],
        env=environment,
        check=True,
    )
    executable = shlex.quote(str(root / "bin" / "foxlight-preservation-preview"))
    directory = shlex.quote(str(root))
    print(
        "Preview installed. Start Core explicitly:\n  "
        + executable
        + " core --directory "
        + directory
        + "\nThen open Library in another terminal:\n  "
        + executable
        + " library --directory "
        + directory,
        flush=True,
    )


def launch(root: Path, action: str) -> None:
    """Explicitly launch only this preview with isolated state and cluster identity."""
    receipt = _receipt(root)
    with _preview_lock(root, exclusive=False):
        if action == "core":
            with _preview_lock(root, exclusive=True, name="core.lock"):
                _launch(root, action, receipt)
        else:
            _launch(root, action, receipt)


def _launch(root: Path, action: str, receipt: dict[str, str | int]) -> None:
    environment = _environment(root, receipt)
    if action == "core":
        _clean_checkout(root / "core")
        actual = subprocess.check_output(
            ["git", "-C", str(root / "core"), "rev-parse", "HEAD"], text=True
        ).strip()
        if actual != CORE_REVISION:
            raise PreviewRefused(
                "The preview checkout revision changed. No runtime was started."
            )
        command = [
            "uv",
            "run",
            "--frozen",
            "skulk",
            "--api-port",
            "53415",
            "--libp2p-port",
            "0",
        ]
        subprocess.run(command, cwd=root / "core", env=environment, check=True)
    else:
        subprocess.run(
            [
                str(root / "bin" / "foxlight-preservation-library"),
                "--core-url",
                "http://127.0.0.1:53415",
            ],
            env=environment,
            check=True,
        )


def main() -> None:
    """Provide public setup and explicit Core/Library launch commands."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("setup", "core", "library"))
    parser.add_argument(
        "--directory", type=Path, default=Path.home() / "foxlight-sovereign-preview"
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume a matching preview setup; never adopt an existing installation.",
    )
    parser.add_argument(
        "--with-dashboard",
        action="store_true",
        help="Also build Core’s dashboard; Library includes its own interface.",
    )
    arguments = parser.parse_args()
    try:
        selected = arguments.directory.expanduser().absolute()
        root = selected.parent.resolve(strict=True) / selected.name
        if arguments.action == "setup":
            setup(
                root, resume=arguments.resume, with_dashboard=arguments.with_dashboard
            )
        else:
            launch(root, arguments.action)
    except (
        PreviewRefused,
        OSError,
        ValueError,
        httpx.HTTPError,
        subprocess.SubprocessError,
    ) as error:
        print(
            "Preview could not finish: "
            + str(error)
            + "\nYour retained files were kept. Resolve the error and explicitly "
            "resume setup, or select a new directory.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
