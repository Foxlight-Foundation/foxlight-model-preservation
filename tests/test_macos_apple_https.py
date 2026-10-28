"""Runner routing never replaces root policy or hides command/cleanup failures."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

WRAPPER = Path(__file__).parents[1] / "scripts/macos_apple_https.sh"


def environment(tmp_path: Path) -> dict[str, str]:
    """Replace sudo entirely; these checks never invoke a real firewall tool."""
    binary = tmp_path / "bin"
    binary.mkdir()
    runner = tmp_path / "runner"
    runner.mkdir()
    (binary / "uname").write_text("#!/bin/sh\nprintf 'Darwin\\n'\n")
    (binary / "uname").chmod(0o700)
    sudo = binary / "sudo"
    sudo.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['TEST_CALLS'], 'a') as out:\n"
        "    out.write(json.dumps(args) + '\\n')\n"
        "assert args[0] == '/sbin/pfctl'\n"
        "if args[1:] == ['-sr']:\n"
        "    print('anchor \\\"com.apple/*\\\" all'"
        " if os.environ.get('TEST_ROOT', 'yes') == 'yes' else '')\n"
        "elif '-sr' in args:\n"
        "    if os.environ.get('TEST_EXISTING') == 'yes': print('block all')\n"
        "elif '-f' in args:\n"
        "    assert args[1:3] == ['-a', 'com.apple/sovereign-42-1-verify']\n"
        "    assert pathlib.Path(args[-1]).read_text() == "
        "'block return out quick proto udp from any to any port 443\\n'\n"
        "elif args[1:] == ['-E']:\n"
        "    print('Token : 321')\n"
        "elif '-F' in args:\n"
        "    assert args[1:] == ['-a', 'com.apple/sovereign-42-1-verify',"
        " '-F', 'rules']\n"
        "    sys.exit(int(os.environ.get('TEST_CLEANUP_STATUS', '0')))\n"
        "elif '-X' in args:\n"
        "    assert args[1:] == ['-X', '321']\n"
        "else:\n"
        "    raise AssertionError(args)\n"
    )
    sudo.chmod(0o700)
    return {
        **os.environ,
        "PATH": str(binary) + os.pathsep + os.defpath,
        "GITHUB_ACTIONS": "true",
        "GITHUB_REF": "refs/tags/sovereign-macos-publisher-v0.1.0-build8-v2",
        "GITHUB_REPOSITORY": "Foxlight-Foundation/foxlight-model-preservation",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "GITHUB_RUN_ID": "42",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_JOB": "verify",
        "RUNNER_TEMP": str(runner),
        "TEST_CALLS": str(tmp_path / "calls.jsonl"),
    }


@pytest.mark.parametrize(
    "command_status,cleanup_status,expected",
    [(0, 0, 0), (7, 0, 7), (0, 9, 9), (7, 9, 7)],
)
def test_scoped_cleanup_and_exit_status(
    tmp_path, command_status, cleanup_status, expected
):
    env = environment(tmp_path)
    env["TEST_CLEANUP_STATUS"] = str(cleanup_status)
    result = subprocess.run(  # noqa: S603 - fixed wrapper and fake sudo, owned vectors
        ["/bin/bash", str(WRAPPER), "/bin/sh", "-c", f"exit {command_status}"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == expected, result.stderr
    calls = [
        json.loads(line) for line in Path(env["TEST_CALLS"]).read_text().splitlines()
    ]
    assert calls[-2:] == [
        ["/sbin/pfctl", "-a", "com.apple/sovereign-42-1-verify", "-F", "rules"],
        ["/sbin/pfctl", "-X", "321"],
    ]
    assert all("-f" not in call or call[1] == "-a" for call in calls)


@pytest.mark.parametrize(
    "setting,value",
    [
        ("GITHUB_REF", "refs/heads/main"),
        ("RUNNER_ENVIRONMENT", "self-hosted"),
        ("GITHUB_ACTIONS", "false"),
    ],
)
def test_operator_or_unapproved_context_never_calls_sudo(tmp_path, setting, value):
    env = environment(tmp_path)
    env[setting] = value
    result = subprocess.run(  # noqa: S603 - fixed wrapper and fake sudo
        ["/bin/bash", str(WRAPPER), "/bin/true"], env=env, capture_output=True
    )
    assert result.returncode == 2
    assert not Path(env["TEST_CALLS"]).exists()


@pytest.mark.parametrize("setting", ["TEST_ROOT", "TEST_EXISTING"])
def test_existing_policy_refuses_any_mutation(tmp_path, setting):
    env = environment(tmp_path)
    env[setting] = "no" if setting == "TEST_ROOT" else "yes"
    result = subprocess.run(  # noqa: S603 - fixed wrapper and fake sudo
        ["/bin/bash", str(WRAPPER), "/bin/true"], env=env, capture_output=True
    )
    assert result.returncode == 2
    calls = [
        json.loads(line) for line in Path(env["TEST_CALLS"]).read_text().splitlines()
    ]
    assert all("-f" not in call and "-E" not in call for call in calls)
