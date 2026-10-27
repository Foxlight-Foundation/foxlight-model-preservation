"""Independent Sovereign Library; public core owns jobs without Skulk or an SDK."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import signal
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import BinaryIO

import httpx
from pydantic import JsonValue

from .client import CoreClient, CoreRefused
from .contracts import HostReport, LibraryCall
from .server import LibraryServer


class DesktopClient(CoreClient):
    """Adapt local public-core ownership to the same ticket-scoped view server."""

    def __init__(self, home: Path) -> None:
        # Optional import: managed Library remains usable with Core alone.
        from foxlight_model_preservation.local_host.owner import LocalOwner

        self.owner = LocalOwner(home)
        self.owner_id = "sovereign-local"
        self.sharing_owner_id = "sovereign-local-sharing"
        self.discovery_owner_id = "sovereign-local-discovery"
        self.mirror_owner_id = "sovereign-local-mirror"
        self.http = httpx.AsyncClient(
            base_url="http://sovereign.invalid",
            transport=httpx.MockTransport(lambda _: httpx.Response(403)),
            trust_env=False,
        )

    async def library(self, call: LibraryCall) -> HostReport:
        """Read local receipts without probing or configuring Skulk Core."""
        return HostReport(report=await asyncio.to_thread(self.owner.read, call.offset))

    async def request(
        self,
        method: str,
        path: str,
        body: dict[str, JsonValue] | None = None,
        *,
        content: bytes | None = None,
    ) -> dict[str, JsonValue]:
        """Forward only explicit independent controls or bounded kit chunks."""
        from foxlight_model_preservation.models import PreservationError

        try:
            if method == "POST" and path == "desktop-folder-choose" and body == {}:
                if sys.platform != "linux" or shutil.which("zenity") is None:
                    raise CoreRefused("folder_picker_unavailable")
                selected = await asyncio.to_thread(
                    subprocess.run,
                    [
                        "zenity",
                        "--file-selection",
                        "--directory",
                        "--title=Choose a backup drive or folder",
                    ],
                    capture_output=True,
                    check=False,
                    timeout=600,
                    text=True,
                )
                if selected.returncode == 1:
                    return {"path": None}
                if selected.returncode != 0 or not selected.stdout.strip():
                    raise CoreRefused("folder_picker_unavailable")
                return {
                    "path": str(Path(selected.stdout.strip()) / "Sovereign Backups")
                }
            if (
                method == "POST"
                and path == "desktop-reveal"
                and body is not None
                and set(body) == {"job_id"}
            ):
                output = await asyncio.to_thread(
                    self.owner.artifact_directory, str(body["job_id"])
                )
                await asyncio.to_thread(
                    subprocess.run,
                    [
                        "/usr/bin/open" if sys.platform == "darwin" else "xdg-open",
                        str(output),
                    ],
                    check=True,
                    timeout=10,
                )
                return {"opened": True}
            if method == "POST" and path.startswith("desktop-"):
                return await asyncio.to_thread(self.owner.control, path[8:], body or {})
            upload = re.fullmatch(
                r"kit-uploads/([0-9a-f]{32})\?offset=([0-9]{1,13})", path
            )
            if method == "PUT" and upload and content is not None:
                return await asyncio.to_thread(
                    self.owner.upload, upload[1], int(upload[2]), content
                )
            raise CoreRefused("sovereign_operation_unavailable")
        except PreservationError as error:
            code = str(error)
            allowed = {
                "complete_signed_file_map_required",
                "complete_signed_companion_map_required",
                "companion_adapter_required",
                "invalid_control",
                "plan_changed",
                "plan_missing_or_expired",
                "request_changed",
                "sovereign_storage_changed",
                "sovereign_copy_unavailable",
                "sovereign_job_capacity_reached",
                "sovereign_library_capacity_reached",
                "kit_upload_in_use",
                "remove_unused_kit_uploads",
                "source_document_too_large",
                "source_transport_unavailable",
                "handoff_transport_unavailable",
                "source_trust_confirmation_required",
                "source_review_changed",
                "source_no_longer_followed",
                "source_model_reference_changed",
                "source_update_failed",
                "discovery_busy_try_again",
                "candidate_bundle_keys_required",
                "public_model_mirror_required",
                "receiving_allowance_too_small",
                "model_metadata_unavailable",
                "metadata_preview_too_large",
                "backup_destination_changed",
                "backup_requires_empty_private_folder",
                "backup_destination_capacity",
                "backup_busy_try_again",
                "sharing_projection_unavailable",
                "public_copy_review_changed",
                "sharing_busy_try_again",
                "sharing_not_ready",
                "public_copy_capacity",
                "public_copy_unavailable",
            }
            raise CoreRefused(
                code if code in allowed else "invalid_or_unsafe"
            ) from None

    async def local_artifact(self, job_id: str) -> BinaryIO:
        """Open only one successful local recovery-kit receipt for streaming."""
        return await asyncio.to_thread(self.owner.open_kit, job_id)

    async def local_handoff(self, job_id: str) -> BinaryIO:
        """Open metadata only from this owner's currently running sharing job."""
        return await asyncio.to_thread(self.owner.open_handoff, job_id)

    async def close(self) -> None:
        """Finish cooperative local shutdown and retain durable receipts."""
        await asyncio.to_thread(self.owner.close)
        await self.http.aclose()


def default_home() -> Path:
    """Use the platform's per-user data directory without requiring a terminal."""
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Foxlight/Sovereign"
    return (
        Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
        / "foxlight/sovereign"
    )


async def run(home: Path, *, open_browser: bool) -> None:
    """Run the independent owner and its local screen until the app is closed."""
    client = DesktopClient(home)
    server = LibraryServer(None, client, asyncio.get_running_loop())
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    try:
        server.start()
        for event in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(event, stopped.set)
        print("Sovereign: " + server.url, flush=True)
        if open_browser:
            await asyncio.to_thread(webbrowser.open, server.url)
        await stopped.wait()
    finally:
        for event in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(event)
        await asyncio.to_thread(server.close)
        await client.close()


def main() -> None:
    """Open independent Sovereign; no Core, SDK, account, GPU or registry required."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=default_home())
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.home, open_browser=not args.no_browser))
    except ImportError:
        print(
            "Sovereign needs its matching public preservation core. Use the "
            "self-contained application or install both public packages. Guide: "
            "https://github.com/Foxlight-Foundation/"
            "foxlight-model-preservation/blob/main/docs/sovereign-desktop.md",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except (ValueError, OSError) as error:
        print(
            "Sovereign could not open its private storage. " + type(error).__name__,
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
