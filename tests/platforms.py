"""Tests that exercise one platform's own tooling, marked once with a reason."""

from __future__ import annotations

import sys

import pytest

WINDOWS = sys.platform == "win32"

# The Docker entrypoint, Linux setup and test stubs are POSIX shell scripts.
posix_shell = pytest.mark.skipif(WINDOWS, reason="runs a POSIX shell script")
# Linux and macOS packaging, recovery and signing tooling.
linux_only = pytest.mark.skipif(WINDOWS, reason="Linux setup and packaging")
macos_only = pytest.mark.skipif(WINDOWS, reason="macOS packaging and signing")
# Not yet ported (foxlight-docs#768, "Not in this note"): the managed Kubo
# peer runtime and the I2P router run as separate native programs.
managed_peer_runtime = pytest.mark.skipif(
    WINDOWS, reason="the managed peer runtime is not on Windows yet"
)
i2p_runtime = pytest.mark.skipif(
    WINDOWS, reason="the I2P router runtime is not on Windows yet"
)
