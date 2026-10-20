"""Managed SDK child: thin controls and Library, independent of Core byte jobs."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import resource
from typing import cast

from pydantic import JsonValue
from skulk_capability_sdk.contracts import (
    Health,
    Hello,
    Invoke,
    Result,
    Shutdown,
    Startup,
    SurfaceReport,
    canonical,
    manifest_digest,
)
from skulk_capability_sdk.operations import OperationCall
from skulk_capability_sdk.storage import read_startup
from skulk_capability_sdk.wire import read_message, write_message

from .client import CoreClient
from .contracts import COPY_ID, INVENTORY_ID, SURFACE, LibraryCall, Settings
from .server import LibraryServer


async def run(startup: Startup) -> None:
    """Handshake and dispatch bounded calls; shutdown closes UI, not Core jobs."""
    client = CoreClient(
        Settings.model_validate(startup.configuration or {}),
        hashlib.sha256(startup.node_id.encode()).hexdigest(),
    )
    server = LibraryServer(startup, client, asyncio.get_running_loop())
    reader, writer = await asyncio.open_unix_connection(startup.socket)
    try:
        await write_message(
            writer,
            Hello(
                host_id=startup.host_id,
                transport_node_id=startup.transport_id,
                capability_node_id=startup.node_id,
                bundle_id=startup.manifest.bundle_id,
                bundle_version=startup.manifest.bundle_version,
                token=startup.token,
                manifest_sha256=manifest_digest(startup.manifest),
            ),
        )
        server.start()
        while True:
            message = await read_message(reader)
            if isinstance(message, Shutdown):
                return
            if isinstance(message, Health):
                await write_message(
                    writer,
                    Health(
                        nonce=message.nonce,
                        ready=True,
                        surfaces=(
                            SurfaceReport(
                                surface_id=SURFACE,
                                ready=True,
                                url=server.url,
                            ),
                        ),
                    ),
                )
            elif isinstance(message, Invoke):
                try:
                    async with asyncio.timeout(
                        min(message.remaining_seconds, 20) * 0.9
                    ):
                        if message.capability_id == INVENTORY_ID:
                            result = await client.library(
                                LibraryCall.model_validate(message.payload)
                            )
                        elif message.capability_id == COPY_ID:
                            result = await client.operation(
                                OperationCall.model_validate(message.payload)
                            )
                        else:
                            raise ValueError("unknown descriptor")
                    response = Result(
                        call_id=message.call_id,
                        payload=cast(
                            dict[str, JsonValue], json.loads(result.model_dump_json())
                        ),
                        error=None,
                    )
                    if len(canonical(response)) > 65000:
                        raise ValueError("result too large")
                except Exception:
                    response = Result(
                        call_id=message.call_id, payload=None, error="provider_error"
                    )
                await write_message(writer, response)
    finally:
        await asyncio.to_thread(server.close)
        await client.close()
        writer.close()
        await writer.wait_closed()


def main() -> None:
    """Read protected SDK startup from its inherited descriptor, never a key path."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-fd", type=int, required=True)
    arguments = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    asyncio.run(run(read_startup(arguments.config_fd)))


if __name__ == "__main__":
    main()
