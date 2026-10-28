"""Pasted desktop instructions must authenticate before executing a helper."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("shell", ["sh", "bash", "zsh"])
@pytest.mark.parametrize("failure", ["curl", "gh", "none"])
def test_desktop_install_block_stops_before_unverified_execution(
    tmp_path: Path, shell: str, failure: str
) -> None:
    """Execute the actual documented block with inert command/launch canaries."""
    interpreter = shutil.which(shell)
    if interpreter is None:
        pytest.skip("shell is unavailable")
    guide = Path(__file__).parents[1] / "docs/installation.md"
    section = guide.read_text().split("## Select the independent desktop profile", 1)[1]
    block = section.split("```sh\n", 1)[1].split("```", 1)[0]
    target = tmp_path / "installed"
    (target / "bin").mkdir(parents=True)
    commands = tmp_path / "commands"
    commands.mkdir()
    for name in ("curl", "gh", "unzip", "python3", "launch"):
        path = (
            target / "bin/foxlight-sovereign" if name == "launch" else commands / name
        )
        path.write_text(
            "#!/bin/sh\n"
            + f"printf '%s\\n' '{name}' >> \"$SOVEREIGN_COMMANDS\"\n"
            + f'if [ "$SOVEREIGN_INSTALL_FAILURE" = "{name}" ]; then exit 22; fi\n'
            + ("mkdir sovereign-desktop-install\n" if name == "unzip" else "")
        )
        path.chmod(0o700)
    script = tmp_path / "install.sh"
    # Do not change HOME or permit a canary to reach an operator installation.
    script.write_text(block.replace("$HOME/sovereign-desktop", str(target)))
    log = tmp_path / "commands.log"
    result = subprocess.run(  # noqa: S603 - documented block and inert owned tools
        [interpreter, str(script)],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": str(commands) + os.pathsep + os.environ["PATH"],
            "SOVEREIGN_COMMANDS": str(log),
            "SOVEREIGN_INSTALL_FAILURE": failure,
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    expected = ["curl"]
    if failure != "curl":
        expected += ["curl", "gh"]
    if failure == "none":
        expected += ["unzip", "python3", "launch"]
    assert log.read_text().splitlines() == expected
    assert (result.returncode == 0) == (failure == "none"), result.stderr
