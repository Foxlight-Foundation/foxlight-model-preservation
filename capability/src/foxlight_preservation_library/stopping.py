"""How a running Library screen is asked to stop, on every platform."""

from __future__ import annotations

import asyncio
import signal
import sys
import threading
from collections.abc import Callable
from typing import BinaryIO


def stop_on_signals(
    loop: asyncio.AbstractEventLoop, stopped: asyncio.Event
) -> Callable[[], None]:
    """Stop on SIGINT and SIGTERM where the event loop supports it.

    Windows' event loop has no signal handlers. There Ctrl+C raises
    KeyboardInterrupt, which ``asyncio.run`` turns into cancellation, so the
    caller's cleanup still runs. Returns a function that removes the handlers.
    """
    added: list[signal.Signals] = []
    for event in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(event, stopped.set)
        except NotImplementedError:
            continue
        added.append(event)

    def remove() -> None:
        for event in added:
            loop.remove_signal_handler(event)

    return remove


def stop_when_input_closes(
    loop: asyncio.AbstractEventLoop,
    stopped: asyncio.Event,
    stream: BinaryIO | None = None,
) -> threading.Thread:
    """Stop once standard input reaches end of file.

    A desktop shell that started the screen keeps a pipe to its standard input
    and closes it to ask for a clean stop. The pipe also closes if the shell
    exits for any other reason, so the screen never outlives its window.
    """
    source = stream if stream is not None else getattr(sys.stdin, "buffer", None)

    def watch() -> None:
        try:
            if source is not None:
                while source.read(4096):
                    pass
        except (OSError, ValueError):
            pass
        try:
            loop.call_soon_threadsafe(stopped.set)
        except RuntimeError:
            pass  # The loop already finished.

    thread = threading.Thread(target=watch, name="stop-when-input-closes", daemon=True)
    thread.start()
    return thread
