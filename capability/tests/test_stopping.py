"""Stopping the screen, the Windows home folder and refusals that carry a body."""

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from foxlight_preservation_library.stopping import (
    stop_on_signals,
    stop_when_input_closes,
)


async def test_closing_standard_input_stops_the_screen():
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    read, write = os.pipe()
    try:
        thread = stop_when_input_closes(loop, stopped, read)
        os.write(write, b"ignored")
        await asyncio.sleep(0.05)
        assert not stopped.is_set()
        os.close(write)
        await asyncio.wait_for(stopped.wait(), 5)
        thread.join(5)
    finally:
        os.close(read)


def test_stopping_another_way_while_input_is_open_exits_cleanly():
    """The watcher may still be reading when the screen stops by a signal."""
    program = (
        "import asyncio\n"
        "from foxlight_preservation_library.stopping import stop_when_input_closes\n"
        "async def main():\n"
        "    stop_when_input_closes(asyncio.get_running_loop(), asyncio.Event())\n"
        "    await asyncio.sleep(0.2)\n"
        "asyncio.run(main())\n"
        "print('clean', flush=True)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", program],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert child.stdin is not None
    assert child.stdout is not None and child.stderr is not None
    try:
        # Standard input stays open the whole time, as when the Windows app is
        # still running and the screen stops another way.
        child.wait(timeout=20)
        out, err = child.stdout.read(), child.stderr.read()
    finally:
        child.stdin.close()
        child.stdout.close()
        child.stderr.close()
    assert child.returncode == 0, err
    assert out.strip() == b"clean"


async def test_without_signal_support_handlers_are_skipped():
    class NoSignals:
        def add_signal_handler(self, *args):
            raise NotImplementedError

        def remove_signal_handler(self, *args):
            raise AssertionError("nothing was added")

    remove = stop_on_signals(NoSignals(), asyncio.Event())  # type: ignore[arg-type]
    remove()


def test_windows_home_is_local_app_data(monkeypatch):
    import foxlight_preservation_library.desktop as desktop

    monkeypatch.setattr(desktop.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(Path("/users/someone/AppData/Local")))
    assert desktop.default_home() == Path(
        "/users/someone/AppData/Local/Foxlight/Sovereign"
    )
    monkeypatch.delenv("LOCALAPPDATA")
    assert desktop.default_home() == Path.home() / "AppData/Local/Foxlight/Sovereign"


@pytest.mark.parametrize("size", [2, 60_000])
async def test_a_refused_control_with_a_body_still_gets_its_reply(tmp_path, size):
    """Windows resets a socket closed with unread bytes, losing the reply."""
    pytest.importorskip("foxlight_model_preservation")
    from foxlight_preservation_library.desktop import DesktopClient
    from foxlight_preservation_library.server import LibraryServer

    client = DesktopClient(tmp_path / "owner")
    server = LibraryServer(None, client, asyncio.get_running_loop())
    server.start()
    try:
        url = urlsplit(server.url)
        body = b"{" + b" " * (size - 2) + b"}"
        # No X-Preservation-Action header: refused before the body is read.
        head = (
            f"POST {url.path}api/desktop-plan HTTP/1.1\r\n"
            f"Host: {url.netloc}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n\r\n"
        ).encode()

        def exchange() -> bytes:
            with socket.create_connection((url.hostname, url.port), timeout=5) as raw:
                # Clients may send the body after the headers, as httpx does;
                # it arrives after the server has decided to refuse.
                raw.sendall(head)
                time.sleep(0.3)
                raw.sendall(body)
                received = b""
                while chunk := raw.recv(65536):
                    received += chunk
                return received

        replies = await asyncio.gather(*(asyncio.to_thread(exchange) for _ in range(5)))
        for reply in replies:
            assert reply.startswith(b"HTTP/1.0 400 "), reply[:80]
            assert reply.endswith(b'{"error":"invalid_control"}')
    finally:
        await asyncio.to_thread(server.close)
        await client.close()
