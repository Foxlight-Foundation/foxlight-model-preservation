"""A non-Skulk embedding owner contains all actual native sharing processes."""

from __future__ import annotations

import hashlib
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from test_mirror import cycle, mirror
from test_mirror_native import await_condition, ipfs_home, native_binary

from foxlight_model_preservation.mirror_native import configure
from foxlight_model_preservation.storage import manifest_path, verify_store


def group_members(identity: int) -> tuple[tuple[int, str], ...]:
    """Observe live members of the test's still-owned group, excluding zombies."""
    result = subprocess.check_output(  # noqa: S603 - fixed local process observation
        ["ps", "-axo", "pid=,pgid=,stat=,comm="],  # noqa: S607
        text=True,
    )
    members: list[tuple[int, str]] = []
    for line in result.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4 and int(parts[1]) == identity and "Z" not in parts[2]:
            members.append((int(parts[0]), parts[3]))
    return tuple(members)


def test_embedded_native_owner_contains_daemon_and_commands_and_retains_evidence(
    tmp_path, capture
):
    pytest.importorskip("multiformats")
    binary = native_binary()
    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    configure(
        ledger,
        ipfs_binary=binary,
        ipfs_sha256=hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
        allow_private_peers=True,
        session_seconds=60,
    )
    # This independently owned host deliberately has no Skulk imports and no
    # nested Python worker entry. Frozen hosts can select the same adapter.
    program = """
import sys, threading
from pathlib import Path
from foxlight_model_preservation.mirror import run_mirror
from foxlight_model_preservation.mirror_state import MirrorLedger
from foxlight_model_preservation.operations import OperationControl
from foxlight_model_preservation.peer_runtime import serve_peer
control = OperationControl()
def lifetime():
    sys.stdin.buffer.read(1)
    control.cancel()
threading.Thread(target=lifetime, daemon=True).start()
run_mirror(MirrorLedger(Path(sys.argv[1])), public=True, seconds=60,
           control=control, native_peer_runner=serve_peer)
"""
    process = subprocess.Popen(  # noqa: S603 - fixed synthetic embedding program
        [sys.executable, "-c", program, str(ledger.path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        await_condition(
            lambda: bool(
                ledger.read().native_models.get(capture.identity)
                and ledger.read().native_models[capture.identity].cid
            )
        )
        members = group_members(process.pid)
        assert any(
            pid != process.pid and Path(name).name == "ipfs" for pid, name in members
        )
        port = int(
            (ipfs_home(ledger, capture.identity) / "api")
            .read_text()
            .strip()
            .rsplit("/", 1)[-1]
        )
        reserved = ledger.read().egress_month_used
        # Keep the group leader unreaped until all actual members have stopped.
        # A caller can kill this one owned group after abrupt outer-worker loss.
        process.kill()
        os.killpg(process.pid, signal.SIGKILL)
        await_condition(lambda: not group_members(process.pid), 5)
        process.wait(timeout=3)
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0
        assert not ledger.running()
        assert ledger.read().egress_month_used == reserved
        assert (
            len(
                verify_store(
                    ledger.path / "store", capture.identity, capture.trust
                ).members
            )
            == 2
        )
        assert (
            ledger.path / "store" / manifest_path(capture.identity)
        ).read_bytes() == (capture.store / manifest_path(capture.identity)).read_bytes()
    finally:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
        for stream in (process.stdin, process.stderr):
            if stream is not None:
                stream.close()
