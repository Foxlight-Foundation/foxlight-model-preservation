"""Public local Library launcher; Core retains all keys, bytes and durable jobs."""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import webbrowser

from .client import CoreClient, CoreRefused
from .compatibility import check_core
from .contracts import LibraryCall, Settings
from .server import LibraryServer


async def run(settings: Settings, *, open_browser: bool) -> None:
    """Serve a ticket-scoped loopback screen until stopped, without an SDK owner."""
    client = CoreClient(settings, "library-local")
    server = LibraryServer(None, client, asyncio.get_running_loop())
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    try:
        # Check the actual Core before reporting a usable screen. This never
        # creates an archive or starts copying, hosting, discovery or seeding.
        await client.library(LibraryCall())
        server.start()
        for event in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(event, stopped.set)
        print("Model Preservation Library: " + server.url, flush=True)
        print(
            "Keep this private local link on this device. Stop with Ctrl+C.", flush=True
        )
        if open_browser:
            await asyncio.to_thread(webbrowser.open, server.url)
        await stopped.wait()
    finally:
        for event in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(event)
        await asyncio.to_thread(server.close)
        await client.close()


async def compatibility(settings: Settings) -> bool:
    """Report actual API availability without opening a screen or starting work."""
    client = CoreClient(settings, "library-local")
    try:
        report = await check_core(client)
        for name, supported in report.items():
            print(name + ": " + ("available" if supported else "Core upgrade needed"))
        print("This checks API compatibility. Storage and jobs are checked when used.")
        return all(report.values())
    finally:
        await client.close()


def main() -> None:
    """Open Library against a configured local Core with no managed SDK dependency."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-url", default="http://127.0.0.1:52415")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check the running owner and API compatibility without opening Library.",
    )
    arguments = parser.parse_args()
    try:
        settings = Settings(skulk_api_url=arguments.core_url)
        if arguments.check:
            if not asyncio.run(compatibility(settings)):
                raise SystemExit(2)
        else:
            asyncio.run(run(settings, open_browser=not arguments.no_browser))
    except (CoreRefused, ValueError) as error:
        print(
            "Library could not open. Start a compatible Skulk Core with its local "
            "Model Store and preservation API enabled. For a separate pinned preview, "
            "run foxlight-preservation-preview setup. Guide: "
            "https://github.com/Foxlight-Foundation/"
            "foxlight-model-preservation/blob/main/capability/preview.md "
            + (
                str(error)
                if isinstance(error, CoreRefused)
                else "Use an explicit loopback Core URL and port."
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
