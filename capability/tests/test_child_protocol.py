"""Real child health obeys the SDK's declared link-surface wire contract."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from skulk_capability_sdk.contracts import Health, Hello, Shutdown, Startup, canonical
from skulk_capability_sdk.wire import read_message, write_message

from foxlight_preservation_library.contracts import build_manifest


async def test_link_health_reports_url_without_a_proxy_port() -> None:
    with tempfile.TemporaryDirectory(prefix="mp-wire-", dir="/tmp") as temporary:
        root = Path(temporary)
        connection: asyncio.Future[
            tuple[asyncio.StreamReader, asyncio.StreamWriter]
        ] = asyncio.get_running_loop().create_future()

        def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            connection.set_result((reader, writer))

        server = await asyncio.start_unix_server(accept, path=str(root / "control"))
        manifest = build_manifest(executable=sys.executable, executable_sha256="a" * 64)
        startup = Startup(
            socket=str(root / "control"),
            token="f" * 64,
            node_id="wire-test",
            host_id="host",
            transport_id="transport",
            manifest=manifest,
        )
        with tempfile.TemporaryFile() as stream:
            _ = stream.write(canonical(startup))
            stream.seek(0)
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "foxlight_preservation_library.child",
                "--config-fd",
                str(stream.fileno()),
                pass_fds=(stream.fileno(),),
                cwd=root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        writer: asyncio.StreamWriter | None = None
        try:
            async with asyncio.timeout(10):
                reader, writer = await connection
                assert isinstance(await read_message(reader), Hello)
                await write_message(writer, Health(nonce="health", ready=False))
                reply = await read_message(reader)
                assert isinstance(reply, Health) and reply.ready
                assert manifest.surfaces[0].kind == "link"
                assert len(reply.surfaces) == 1
                assert reply.surfaces[0].url is not None
                assert reply.surfaces[0].port is None
                await write_message(writer, Shutdown())
                assert await process.wait() == 0
        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()
            if process.returncode is None:
                process.kill()
                await process.wait()
            server.close()
            await server.wait_closed()
