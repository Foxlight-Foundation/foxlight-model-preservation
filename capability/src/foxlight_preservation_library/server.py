"""Ticket-scoped operator UI with bounded threads and same-origin controls."""

from __future__ import annotations

import asyncio
import concurrent.futures
import http.server
import ipaddress
import json
import mimetypes
import re
import secrets
import socket
import socketserver
import threading
from importlib.resources import files
from typing import TYPE_CHECKING, Final, cast
from urllib.parse import urlsplit

import httpx
from pydantic import JsonValue

if TYPE_CHECKING:
    from skulk_capability_sdk.contracts import Startup

from .client import CoreClient, CoreRefused
from .compatibility import check_core
from .contracts import (
    DefaultStorageInput,
    DeliveryInput,
    FolderConfirmationInput,
    FolderReviewInput,
    HandoffInput,
    LibraryCall,
    SharingInput,
    SharingReferenceInput,
)
from .discovery import discovery_control

#: The exact built interface files this screen serves. A ``None`` type uses the
#: platform registry; fonts are explicit because ``mimetypes`` may not know
#: ``.woff2``. Every asset is local: the page makes no external requests.
STATIC_ASSETS: Final[dict[str, str | None]] = {
    "index.html": None,
    "assets/library.js": None,
    "assets/index.css": None,
    "assets/instrument-sans-latin-wght-normal.woff2": "font/woff2",
    "assets/jetbrains-mono-latin-wght-normal.woff2": "font/woff2",
    "THIRD_PARTY_NOTICES.txt": None,
    "LICENSE.txt": None,
}
#: Per-asset size bound; larger built files are refused rather than served.
STATIC_ASSET_LIMIT: Final = 2 * 1024 * 1024


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], app: LibraryServer) -> None:
        self.app = app
        self.slots = threading.BoundedSemaphore(8)
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(address, _Handler)

    def server_bind(self) -> None:
        """Bind the authorized address without an incidental reverse lookup.

        Called during construction; returns None after recording the numeric
        listener and actual port. Host/Origin and peer checks remain unchanged.
        """
        socketserver.TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])

    def process_request(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: tuple[str, int],
    ) -> None:
        """Bound admitted request threads before spawning, including slow clients."""
        if not isinstance(request, socket.socket):
            request[1].close()
            return
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        request.settimeout(10)
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: tuple[str, int],
    ) -> None:
        """Release admission after this bounded request completes."""
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class LibraryServer:
    """Serve only SDK-authorized addresses; close does not cancel Core work."""

    def __init__(
        self,
        startup: Startup | None,
        client: CoreClient,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.startup = startup
        self.client = client
        self.loop = loop
        self.prefix = "/s/" + secrets.token_hex(32) + "/"
        self.servers: list[_Server] = []
        self.threads: list[threading.Thread] = []
        self.static = files("foxlight_preservation_library").joinpath("static")

    def _bind_hosts(self) -> tuple[str, ...]:
        if self.startup is None:
            return ("127.0.0.1",)
        from skulk_capability_sdk.serving import bind_hosts

        return bind_hosts(self.startup)

    def advertised_host(self) -> str:
        """Return the bound local host or the managed owner-authorized host."""
        if self.startup is None:
            return "127.0.0.1"
        from skulk_capability_sdk.serving import advertised_host

        return advertised_host(self.startup)

    def peer_allowed(self, address: str) -> bool:
        """Limit the public launcher to loopback and preserve managed peer policy."""
        if self.startup is None:
            return ipaddress.ip_address(address).is_loopback
        from skulk_capability_sdk.serving import peer_allowed

        return peer_allowed(address)

    def start(self) -> None:
        """Start local screen listeners without any model/mirror/swarm listener."""
        port = 0
        try:
            for host in self._bind_hosts():
                server = _Server((host, port), self)
                port = server.server_port
                self.servers.append(server)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                self.threads.append(thread)
                thread.start()
        except BaseException:
            self.close()
            raise

    @property
    def url(self) -> str:
        """Report the volatile SDK screen URL; never embed Core credentials."""
        return f"http://{self.advertised_host()}:{self.servers[0].server_port}{self.prefix}"

    def close(self) -> None:
        """Stop this child's UI listeners only; durable Core jobs are retained."""
        for server in self.servers:
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=2)
        self.servers.clear()
        self.threads.clear()

    async def dispatch(
        self, action: str, body: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        """Allow exact UI controls; only explicit folder review accepts a host path."""
        if action in {
            "peer-read",
            "peer-setup",
            "peer-runtime-read",
            "peer-runtime-start",
            "peer-runtime-stop",
            "discovery-read",
            "discovery-search",
            "discovery-start",
            "discovery-observe",
            "discovery-cancel",
            "source-review",
            "source-follow",
            "source-remove",
        }:
            return await discovery_control(self.client, action, body)
        if action == "folder-review":
            selection = FolderReviewInput.model_validate(body)
            return await self.client.request(
                "POST", "setup/folder/review", selection.model_dump(mode="json")
            )
        if action == "folder-setup":
            confirmation = FolderConfirmationInput.model_validate(body)
            return await self.client.request(
                "POST", "setup/folder", confirmation.model_dump(mode="json")
            )
        if action == "storage-default":
            selected_storage = DefaultStorageInput.model_validate(body)
            return await self.client.request(
                "POST", "setup/default", selected_storage.model_dump(mode="json")
            )
        if action == "setup":
            return (
                await self.client.library(LibraryCall(action="setup_device"))
            ).report
        if action == "plan":
            return await self.client.request("POST", "plans", body)
        if action == "sharing-plan":
            selected = SharingInput.model_validate(body)
            return await self.client.request(
                "POST",
                "plans",
                selected.model_dump(
                    mode="json",
                    exclude={"allow_peer_network", "peer_setup_id"}
                    if selected.kind != "peer_share"
                    else set(),
                ),
            )
        if action == "core-features":
            if body:
                raise ValueError("empty feature read required")
            return cast(dict[str, JsonValue], await check_core(self.client))
        if action == "sharing-reference":
            selected_job = SharingReferenceInput.model_validate(body)
            job = await self.client.request("GET", "jobs/" + selected_job.job_id)
            if (
                job.get("owner_id") != self.client.sharing_owner_id
                or job.get("kind") != "peer_share"
            ):
                raise CoreRefused("job_owner_changed")
            plan_id = job.get("plan_id")
            if (
                not isinstance(plan_id, str)
                or re.fullmatch(r"[0-9a-f]{64}", plan_id) is None
            ):
                raise CoreRefused("invalid_job_identity")
            plan = await self.client.request("GET", "plans/" + plan_id)
            copy_id, manifest_id = plan.get("copy_id"), plan.get("manifest_id")
            if plan.get("kind") != "peer_share" or any(
                not isinstance(value, str)
                or re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in (copy_id, manifest_id)
            ):
                raise CoreRefused("public_metadata_refused")
            return {"copy_id": copy_id, "manifest_id": manifest_id}
        if action == "delivery-plan":
            delivery = DeliveryInput.model_validate(body)
            return await self.client.request(
                "POST", "plans", delivery.model_dump(mode="json", exclude_unset=True)
            )
        if action == "publisher-setup":
            if body:
                raise CoreRefused("invalid_submission")
            return await self.client.request("POST", "setup/publisher", {})
        if action in {"publication-source", "invitation-inspect", "receive-preview"}:
            field = {
                "publication-source": "copy_id",
                "invitation-inspect": "upload_id",
                "receive-preview": "job_id",
            }[action]
            identifier = body.get(field)
            width = 32 if field == "upload_id" else 64
            if (
                set(body) != {field}
                or not isinstance(identifier, str)
                or re.fullmatch(r"[0-9a-f]{" + str(width) + "}", identifier) is None
            ):
                raise CoreRefused("invalid_submission")
            if action == "receive-preview":
                job = await self.client.request("GET", "jobs/" + identifier)
                if (
                    job.get("owner_id") != self.client.sharing_owner_id
                    or job.get("kind") != "receive"
                ):
                    raise CoreRefused("job_owner_changed")
            path = (
                "copies/" + identifier + "/publication"
                if action == "publication-source"
                else "kit-uploads/" + identifier + "/invitation"
                if action == "invitation-inspect"
                else "jobs/" + identifier + "/invitation"
            )
            return await self.client.request("GET", path)
        if action in {"start", "resume-receive"}:
            job_id = body.get("job_id")
            if action == "resume-receive":
                if (
                    not isinstance(job_id, str)
                    or re.fullmatch(r"[0-9a-f]{64}", job_id) is None
                ):
                    raise CoreRefused("invalid_job_identity")
                body = {key: value for key, value in body.items() if key != "job_id"}
            if (
                not {"plan_id", "operation_id"}.issubset(body)
                or set(body)
                - {
                    "plan_id",
                    "operation_id",
                    "accept_owner_capture",
                    "allow_repository_code",
                    "restore_deleted",
                    "public_participation",
                }
                or any(
                    type(body[key]) is not bool
                    for key in (
                        "accept_owner_capture",
                        "allow_repository_code",
                        "restore_deleted",
                        "public_participation",
                    )
                    if key in body
                )
            ):
                raise CoreRefused("invalid_submission")
            plan_id = body.get("plan_id")
            if (
                not isinstance(plan_id, str)
                or re.fullmatch(r"[0-9a-f]{64}", plan_id) is None
            ):
                raise CoreRefused("invalid_submission")
            selected_plan = await self.client.request("GET", "plans/" + plan_id)
            owner = (
                self.client.sharing_owner_id
                if selected_plan.get("kind")
                in {
                    "mirror_import",
                    "http_share",
                    "torrent_share",
                    "peer_share",
                    "publish",
                    "receive",
                }
                else self.client.owner_id
            )
            if action == "resume-receive":
                if (
                    selected_plan.get("kind") != "receive"
                    or selected_plan.get("resume_job_id") != job_id
                ):
                    raise CoreRefused("invalid_submission")
                original = await self.client.request("GET", "jobs/" + cast(str, job_id))
                if (
                    original.get("owner_id") != self.client.sharing_owner_id
                    or original.get("kind") != "receive"
                ):
                    raise CoreRefused("job_owner_changed")
                return await self.client.request(
                    "POST",
                    "jobs/" + cast(str, job_id) + "/resume",
                    {**body, "owner_id": owner},
                )
            return await self.client.request(
                "POST", "jobs", {**body, "owner_id": owner}
            )
        if action == "cancel":
            job_id = body.get("job_id")
            if (
                set(body) != {"job_id"}
                or not isinstance(job_id, str)
                or len(job_id) != 64
                or any(c not in "0123456789abcdef" for c in job_id)
            ):
                raise CoreRefused("invalid_job_identity")
            return await self.client.cancel_job(job_id)
        if action == "upload-create":
            purpose = body.get("purpose", "kit")
            if (
                "size_bytes" not in body
                or set(body) - {"size_bytes", "purpose"}
                or not isinstance(purpose, str)
                or purpose not in {"kit", "invitation", "publication", "source"}
            ):
                raise CoreRefused("invalid_upload")
            return await self.client.request("POST", "kit-uploads", body)
        if action in {"upload-status", "upload-remove"}:
            upload_id = body.get("upload_id")
            if (
                set(body) != {"upload_id"}
                or not isinstance(upload_id, str)
                or re.fullmatch(r"[0-9a-f]{32}", upload_id) is None
            ):
                raise CoreRefused("invalid_upload")
            return await self.client.request(
                "GET" if action == "upload-status" else "DELETE",
                "kit-uploads/" + upload_id,
            )
        if action in {"reclaim", "work-preview", "reclaim-work"}:
            job_id = body.get("job_id")
            if (
                set(body) != {"job_id"}
                or not isinstance(job_id, str)
                or re.fullmatch(r"[0-9a-f]{64}", job_id) is None
            ):
                raise CoreRefused("invalid_job_identity")
            job = await self.client.request("GET", "jobs/" + job_id)
            permitted = {self.client.owner_id}
            if action != "reclaim":
                permitted.add(self.client.sharing_owner_id)
            if job.get("owner_id") not in permitted:
                raise CoreRefused("job_owner_changed")
            if action == "work-preview":
                return await self.client.request(
                    "GET", "jobs/" + job_id + "/temporary-files"
                )
            return await self.client.request(
                "POST",
                "jobs/"
                + job_id
                + "/"
                + ("reclaim" if action == "reclaim" else "reclaim-work"),
                {},
            )
        if action == "retry-plan":
            job_id = body.get("job_id")
            if (
                set(body) != {"job_id"}
                or not isinstance(job_id, str)
                or re.fullmatch(r"[0-9a-f]{64}", job_id) is None
            ):
                raise CoreRefused("invalid_job_identity")
            job = await self.client.request("GET", "jobs/" + job_id)
            if job.get("owner_id") != self.client.owner_id:
                raise CoreRefused("job_owner_changed")
            previous = await self.client.request(
                "GET", "plans/" + cast(str, job["plan_id"])
            )
            return await self.client.request(
                "POST",
                "plans",
                {
                    key: previous[key]
                    for key in (
                        "kind",
                        "location_id",
                        "model_id",
                        "generation_id",
                        "copy_id",
                        "upload_id",
                        "historical",
                    )
                    if previous.get(key) is not None
                },
            )
        if action.startswith("library"):
            parsed = urlsplit(action)
            if parsed.path != "library" or (
                parsed.query and not parsed.query.startswith("offset=")
            ):
                raise CoreRefused("invalid_page")
            offset = int(parsed.query.removeprefix("offset=") or "0")
            return (await self.client.library(LibraryCall(offset=offset))).report
        raise CoreRefused("unknown_control")


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        """Keep ticket paths, operator addresses and model choices out of logs."""

    def _reply(
        self, status: int, data: bytes, content_type: str = "application/json"
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self' data:; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
            ),
        )
        self.end_headers()
        self.wfile.write(data)

    def _app(self) -> tuple[LibraryServer, str] | None:
        server = cast(_Server, self.server)
        app = server.app
        host = self.headers.get("Host", "")
        allowed = {
            f"127.0.0.1:{server.server_port}",
            f"localhost:{server.server_port}",
            f"{app.advertised_host()}:{server.server_port}",
        }
        if (
            not app.peer_allowed(self.client_address[0])
            or host not in allowed
            or not self.path.startswith(app.prefix)
        ):
            self._reply(403, b'{"error":"operator_screen_required"}')
            return None
        if self.headers.get("Origin") not in {None, "http://" + host}:
            self._reply(403, b'{"error":"same_origin_required"}')
            return None
        return app, self.path[len(app.prefix) :]

    def do_GET(self) -> None:
        """Serve bounded static assets or the exact private inventory read."""
        selected = self._app()
        if selected is None:
            return
        app, relative = selected
        if re.fullmatch(r"api/manifest/[0-9a-f]{64}", relative):
            self._metadata(
                app, "GET", "plans/" + relative.rsplit("/", 1)[1] + "/public-manifest"
            )
            return
        if re.fullmatch(r"api/trust/[0-9a-f]{64}", relative):
            self._trust(app, relative.rsplit("/", 1)[1])
            return
        if re.fullmatch(r"api/torrent/[0-9a-f]{64}", relative):
            self._download(app, relative.rsplit("/", 1)[1], torrent=True)
            return
        if re.fullmatch(r"api/download/[0-9a-f]{64}", relative):
            self._download(app, relative.rsplit("/", 1)[1])
            return
        if relative.startswith("api/library"):
            self._control(app, relative.removeprefix("api/"), {})
            return
        name = relative or "index.html"
        if (
            "?" in name
            or "\\" in name
            or any(part in {"", ".", ".."} for part in name.split("/"))
        ):
            self._reply(404, b'{"error":"not_found"}')
            return
        if name not in STATIC_ASSETS:
            self._reply(404, b'{"error":"not_found"}')
            return
        path = app.static.joinpath(name)
        if not path.is_file():
            self._reply(404, b'{"error":"not_found"}')
            return
        data = path.read_bytes()
        if len(data) > STATIC_ASSET_LIMIT:
            self._reply(413, b'{"error":"asset_too_large"}')
            return
        self._reply(
            200,
            data,
            STATIC_ASSETS[name]
            or mimetypes.guess_type(name)[0]
            or "application/octet-stream",
        )

    def do_POST(self) -> None:
        """Require same-origin typed JSON controls; no cross-origin action form."""
        selected = self._app()
        if selected is None:
            return
        app, relative = selected
        invitation = re.fullmatch(r"api/invitation/([0-9a-f]{64})", relative)
        handoff = re.fullmatch(r"api/handoff/([0-9a-f]{64})", relative)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if (
                self.headers.get("X-Preservation-Action") != "operator"
                or self.headers.get_content_type() != "application/json"
                or not 0 < length <= 65536
                or (
                    invitation is None
                    and handoff is None
                    and relative
                    not in {
                        "api/peer-read",
                        "api/core-features",
                        "api/peer-setup",
                        "api/peer-runtime-read",
                        "api/peer-runtime-start",
                        "api/peer-runtime-stop",
                        "api/discovery-read",
                        "api/discovery-search",
                        "api/discovery-start",
                        "api/discovery-observe",
                        "api/discovery-cancel",
                        "api/source-review",
                        "api/source-follow",
                        "api/source-remove",
                        "api/setup",
                        "api/folder-review",
                        "api/folder-setup",
                        "api/storage-default",
                        "api/plan",
                        "api/sharing-plan",
                        "api/sharing-reference",
                        "api/start",
                        "api/cancel",
                        "api/upload-create",
                        "api/upload-status",
                        "api/upload-remove",
                        "api/retry-plan",
                        "api/reclaim",
                        "api/work-preview",
                        "api/reclaim-work",
                        "api/delivery-plan",
                        "api/publisher-setup",
                        "api/publication-source",
                        "api/invitation-inspect",
                        "api/receive-preview",
                        "api/resume-receive",
                    }
                )
            ):
                raise ValueError("invalid control")
            body: JsonValue = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("invalid object")
        except (ValueError, OSError):
            self._reply(400, b'{"error":"invalid_control"}')
            return
        if handoff is not None:
            try:
                if set(body) not in ({"mirrors"}, {"sharing_job_id"}):
                    raise ValueError("one handoff route required")
                value = HandoffInput.model_validate(body)
                if not value.mirrors and value.sharing_job_id is None:
                    raise ValueError("handoff route required")
            except ValueError:
                self._reply(400, b'{"error":"invalid_control"}')
                return
            self._metadata(
                app,
                "POST",
                "public-copies/" + handoff[1] + "/handoff",
                value.model_dump(mode="json", exclude_unset=True),
            )
            return
        if invitation is not None:
            if set(body) != {"mirrors"}:
                self._reply(400, b'{"error":"invalid_control"}')
                return
            self._metadata(
                app, "POST", "public-copies/" + invitation[1] + "/invitation", body
            )
            return
        self._control(app, relative.removeprefix("api/"), body)

    def _metadata(
        self,
        app: LibraryServer,
        method: str,
        path: str,
        body: dict[str, JsonValue] | None = None,
    ) -> None:
        """Serve bounded public metadata; the private screen URL is never shared."""
        future = asyncio.run_coroutine_threadsafe(
            app.client.metadata(method, path, body), app.loop
        )
        try:
            self._reply(200, future.result(timeout=12), "application/json")
        except concurrent.futures.TimeoutError:
            _ = future.cancel()
            self._reply(503, b'{"error":"host_unavailable"}')
        except (CoreRefused, ValueError, OSError) as error:
            code = str(error) if isinstance(error, CoreRefused) else "host_unavailable"
            self._reply(409, json.dumps({"error": code}).encode())

    def do_PUT(self) -> None:
        """Proxy one bounded binary chunk separately from SDK/control JSON."""
        selected = self._app()
        if selected is None:
            return
        app, relative = selected
        future: concurrent.futures.Future[dict[str, JsonValue]] | None = None
        match = re.fullmatch(
            r"api/upload/([0-9a-f]{32})\?offset=([0-9]{1,13})", relative
        )
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if (
                match is None
                or self.headers.get("X-Preservation-Action") != "operator"
                or self.headers.get_content_type() != "application/octet-stream"
                or not 0 < length <= 4 * 1024 * 1024
            ):
                raise ValueError("invalid kit chunk")
            content = self.rfile.read(length)
            if len(content) != length:
                raise ValueError("truncated chunk")
            future = asyncio.run_coroutine_threadsafe(
                app.client.request(
                    "PUT",
                    "kit-uploads/" + match[1] + "?offset=" + match[2],
                    content=content,
                ),
                app.loop,
            )
            result = future.result(timeout=12)
            self._reply(200, json.dumps(result).encode())
        except concurrent.futures.TimeoutError:
            if future is not None:
                _ = future.cancel()
            self._reply(503, b'{"error":"host_unavailable"}')
        except (ValueError, OSError):
            self._reply(409, b'{"error":"kit_transfer_refused"}')

    def _trust(self, app: LibraryServer, copy_id: str) -> None:
        """Download only public verification material; never a private key."""
        try:
            future = asyncio.run_coroutine_threadsafe(
                app.client.request("GET", "public-copies/" + copy_id + "/trust"),
                app.loop,
            )
            encoded = json.dumps(future.result(timeout=12)).encode()
            self._reply(200, encoded, "application/json")
        except (CoreRefused, ValueError, OSError, concurrent.futures.TimeoutError):
            self._reply(409, b'{"error":"public_copy_unavailable"}')

    def _download(
        self, app: LibraryServer, job_id: str, *, torrent: bool = False
    ) -> None:
        """Stream a completed own-job kit without buffering it in the UI child."""
        started = False
        try:
            future = asyncio.run_coroutine_threadsafe(
                app.client.request("GET", "jobs/" + job_id), app.loop
            )
            job = future.result(timeout=12)
            if (
                job.get("owner_id")
                not in {app.client.owner_id, app.client.sharing_owner_id}
                or job.get("kind") != ("torrent_share" if torrent else "kit_export")
                or (not torrent and job.get("state") != "succeeded")
            ):
                raise CoreRefused("kit_not_ready")
            headers = {"Accept-Encoding": "identity"}
            byte_range = self.headers.get("Range")
            if byte_range is not None:
                if re.fullmatch(r"bytes=[0-9]{0,13}-[0-9]{0,13}", byte_range) is None:
                    raise CoreRefused("invalid_range")
                headers["Range"] = byte_range
            with (
                httpx.Client(
                    base_url=app.client.http.base_url,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=30,
                ) as transfer,
                transfer.stream(
                    "GET",
                    "/preservation/" + ("torrents/" if torrent else "kits/") + job_id,
                    headers=headers,
                ) as response,
            ):
                if response.status_code not in {200, 206} or response.headers.get(
                    "Content-Encoding"
                ) not in {None, "identity"}:
                    raise CoreRefused("kit_download_refused")
                length = int(response.headers.get("Content-Length", "0"))
                if not 0 < length <= (8 * 1024**2 if torrent else 8 * 1024**4):
                    raise CoreRefused("invalid_kit_length")
                self.send_response(response.status_code)
                self.send_header(
                    "Content-Type",
                    "application/x-bittorrent"
                    if torrent
                    else "application/octet-stream",
                )
                self.send_header("Content-Length", str(length))
                self.send_header(
                    "Content-Disposition",
                    'attachment; filename="'
                    + ("model-" if torrent else "recovery-")
                    + job_id
                    + ('.torrent"' if torrent else '.foxkit"'),
                )
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                if response.status_code == 206:
                    content_range = response.headers.get("Content-Range", "")
                    if (
                        re.fullmatch(
                            r"bytes [0-9]{1,13}-[0-9]{1,13}/[0-9]{1,13}", content_range
                        )
                        is None
                    ):
                        raise CoreRefused("invalid_range")
                    self.send_header("Content-Range", content_range)
                self.end_headers()
                started = True
                sent = 0
                for chunk in response.iter_raw(chunk_size=1024 * 1024):
                    sent += len(chunk)
                    if sent > length:
                        raise CoreRefused("kit_length_changed")
                    self.wfile.write(chunk)
                if sent != length:
                    self.close_connection = True
        except (
            CoreRefused,
            ValueError,
            OSError,
            httpx.HTTPError,
            concurrent.futures.TimeoutError,
        ):
            if not started:
                self._reply(409, b'{"error":"kit_download_refused"}')
            self.close_connection = True

    def _control(
        self, app: LibraryServer, action: str, body: dict[str, JsonValue]
    ) -> None:
        future = asyncio.run_coroutine_threadsafe(app.dispatch(action, body), app.loop)
        try:
            result = future.result(timeout=12)
            encoded = json.dumps(result, separators=(",", ":")).encode()
            if len(encoded) > 65536:
                raise CoreRefused("host_response_too_large")
            self._reply(200, encoded)
        except concurrent.futures.TimeoutError:
            _ = future.cancel()
            self._reply(503, b'{"error":"host_unavailable"}')
        except (CoreRefused, ValueError, OSError) as error:
            code = str(error) if isinstance(error, CoreRefused) else "host_unavailable"
            self._reply(409, json.dumps({"error": code}).encode())
