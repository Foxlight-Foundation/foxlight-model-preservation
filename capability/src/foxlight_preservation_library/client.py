"""Bounded HTTP control of local Core; no verifier, key or byte worker lives here."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import TYPE_CHECKING, cast

import httpx
from pydantic import JsonValue

if TYPE_CHECKING:
    from skulk_capability_sdk.contracts import Contract
    from skulk_capability_sdk.operations import OperationCall, OperationStatus

from .contracts import CopyInput, HostReport, LibraryCall, Settings


class CoreRefused(ValueError):
    """A bounded host-safe code; never raw response text or secret paths."""


class CoreClient:
    """Translate SDK controls to the one canonical durable Core journal."""

    def __init__(
        self,
        settings: Settings,
        owner_id: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.owner_id = owner_id
        # Keep public sessions outside the existing governed copy operation's
        # immutable input/result contract while retaining one Library inventory.
        self.sharing_owner_id = hashlib.sha256(
            (owner_id + "\x00sharing").encode()
        ).hexdigest()
        self.http = httpx.AsyncClient(
            base_url=settings.skulk_api_url,
            timeout=10,
            trust_env=False,
            transport=transport,
            follow_redirects=False,
        )

    async def close(self) -> None:
        """Close HTTP controls; Core jobs continue independently of this child."""
        await self.http.aclose()

    async def request(
        self,
        method: str,
        path: str,
        body: dict[str, JsonValue] | None = None,
        *,
        content: bytes | None = None,
    ) -> dict[str, JsonValue]:
        """Read a bounded safe control response; never transfer artifact bytes."""
        try:
            async with self.http.stream(
                method,
                "/preservation/" + path,
                json=body,
                content=content,
                headers={"Content-Type": "application/octet-stream"}
                if content is not None
                else None,
            ) as response:
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 65536:
                        raise CoreRefused("host_response_too_large")
                if response.status_code == 404 and (
                    path.startswith(
                        ("kit-uploads", "kits/", "setup/folder", "setup/default")
                    )
                    or path.endswith(
                        (
                            "/reclaim",
                            "/temporary-files",
                            "/reclaim-work",
                            "/publication",
                            "/invitation",
                            "/resume",
                            "setup/publisher",
                        )
                    )
                ):
                    raise CoreRefused("core_upgrade_required")
                if response.status_code not in {200, 202, 204}:
                    try:
                        failure: JsonValue = json.loads(data)
                        detail = (
                            failure.get("detail") if isinstance(failure, dict) else None
                        )
                        wrapped = (
                            failure.get("error") if isinstance(failure, dict) else None
                        )
                        if detail is None and isinstance(wrapped, dict):
                            detail = wrapped.get("message")
                    except (ValueError, AttributeError):
                        detail = None
                    if (
                        isinstance(detail, str)
                        and len(detail) < 80
                        and all(c.islower() or c == "_" for c in detail)
                    ):
                        raise CoreRefused(detail)
                    raise CoreRefused("host_unavailable_or_refused")
                if response.status_code == 204:
                    return {}
                value: JsonValue = json.loads(data)
                if not isinstance(value, dict):
                    raise CoreRefused("invalid_host_response")
                return value
        except httpx.HTTPError as error:
            raise CoreRefused("host_unavailable") from error

    async def library(self, call: LibraryCall) -> HostReport:
        """Read this owner's activity or enroll storage; neither starts a job."""
        if call.action == "setup_device":
            return HostReport(report=await self.request("POST", "setup/device", {}))
        report = await self.request("GET", f"library?offset={call.offset}")
        jobs = report.get("jobs")
        if not isinstance(jobs, list):
            raise CoreRefused("invalid_host_page")
        report["jobs"] = [
            job
            for job in jobs
            if isinstance(job, dict)
            and job.get("owner_id") in {self.owner_id, self.sharing_owner_id}
        ]
        return HostReport(report=report)

    async def metadata(
        self, method: str, path: str, body: dict[str, JsonValue] | None = None
    ) -> bytes:
        """Read at most 8 MiB of public metadata outside the control plane.

        Only the server's fixed invitation/manifest routes call this. It never
        accepts an arbitrary URL, transfers weights or exposes private keys.
        """
        try:
            async with self.http.stream(
                method, "/preservation/" + path, json=body
            ) as response:
                if response.status_code == 404:
                    raise CoreRefused("core_upgrade_required")
                if response.status_code != 200:
                    raise CoreRefused("public_metadata_refused")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 8 * 1024**2:
                        raise CoreRefused("public_metadata_too_large")
                return bytes(data)
        except httpx.HTTPError as error:
            raise CoreRefused("host_unavailable") from error

    async def cancel_job(self, job_id: str) -> dict[str, JsonValue]:
        """Check the immutable owner binding before forwarding a stop effect."""
        job = await self.request("GET", "jobs/" + job_id)
        if job.get("owner_id") not in {self.owner_id, self.sharing_owner_id}:
            raise CoreRefused("job_owner_changed")
        return await self.request("POST", "jobs/" + job_id + "/cancel", {})

    def _job_id(self, operation_id: str) -> str:
        return hashlib.sha256(
            (self.owner_id + "\x00" + operation_id).encode()
        ).hexdigest()

    def _input(self, plan: dict[str, JsonValue]) -> dict[str, JsonValue]:
        value = {
            field: plan.get(field)
            for field in (
                "kind",
                "location_id",
                "model_id",
                "generation_id",
                "copy_id",
                "upload_id",
                "historical",
            )
        }
        value["historical"] = plan.get("historical", False)
        value["plan_digest"] = plan["plan_id"]
        return cast(
            dict[str, JsonValue],
            CopyInput.model_validate(value).model_dump(mode="json", exclude_none=True),
        )

    async def _status(self, job: dict[str, JsonValue]) -> OperationStatus:
        from skulk_capability_sdk.operations import OperationStatus, input_digest

        plan = await self.request("GET", "plans/" + cast(str, job["plan_id"]))
        bound_input = self._input(plan)
        for permission in (
            "accept_owner_capture",
            "allow_repository_code",
            "restore_deleted",
        ):
            if job.get(permission):
                bound_input[permission] = job[permission]
        state = job.get("state")
        if state not in {
            "queued",
            "running",
            "cancelling",
            "succeeded",
            "failed",
            "cancelled",
            "interrupted",
        }:
            raise CoreRefused("invalid_host_job")
        # The SDK has no cancelling state: expose running plus the exact stage.
        values = {
            "operation_id": job["operation_id"],
            "state": "running" if state == "cancelling" else state,
            "submission": "accepted",
            "stage": "cancelling" if state == "cancelling" else job["stage"],
            "submitted_at": job["created_at"],
            "updated_at": job["updated_at"],
            "finished_at": job.get("finished_at"),
            "stale": time.time() - cast(int, job["updated_at"]) > 120
            and state in {"queued", "running", "cancelling"},
            "input_digest": input_digest(
                self.owner_id + "/foxlight.preservation.copy@0.2.0", bound_input
            ),
            "input": bound_input,
            "backend_reference": job["job_id"],
            "result": job.get("result"),
            "error": job.get("error_code"),
        }
        return OperationStatus.model_validate_json(json.dumps(values))

    async def operation(self, call: OperationCall) -> Contract:
        """Use SDK plan/start/status/list/cancel/log without a second job runner."""
        from skulk_capability_sdk.operations import (
            OperationLog,
            OperationPage,
            OperationPlan,
        )

        if call.op in {"plan", "start"}:
            selection = CopyInput.model_validate(call.input)
            if call.op == "plan":
                value = cast(
                    dict[str, JsonValue],
                    selection.model_dump(
                        mode="json",
                        exclude={
                            "plan_digest",
                            "accept_owner_capture",
                            "allow_repository_code",
                            "restore_deleted",
                        },
                        exclude_none=True,
                    ),
                )
                plan = await self.request("POST", "plans", value)
                reviewed = CopyInput.model_validate(self._input(plan))
                return OperationPlan(
                    plan_digest=cast(str, plan["plan_id"]),
                    summary=(
                        f"{selection.kind.capitalize()} {plan['model_id']} "
                        f"with {plan['member_count']} required members "
                        f"({plan['size_bytes']} bytes). "
                        f"Publication: {plan.get('publication') or 'not assessed'}. "
                        "Capture acceptance required: "
                        f"{bool(plan.get('requires_owner_acceptance'))}; "
                        "repository code permission required: "
                        f"{bool(plan.get('requires_code_permission'))}. "
                        "Removed aliases needing confirmation: "
                        f"{plan.get('deleted_aliases', [])}. "
                        "Capture keys: "
                        f"{json.dumps(plan.get('publisher_key_ids', []))}."
                    ),
                    expires_at=cast(int, plan["expires_at"]),
                    input=cast(
                        dict[str, JsonValue],
                        reviewed.model_dump(mode="json", exclude_none=True),
                    ),
                )
            if selection.plan_digest is None or call.operation_id is None:
                raise CoreRefused("reviewed_plan_and_operation_id_required")
            retained = await self.request("GET", "plans/" + selection.plan_digest)
            if any(
                retained.get(field, False if field == "historical" else None)
                != getattr(selection, field)
                for field in (
                    "kind",
                    "location_id",
                    "model_id",
                    "generation_id",
                    "copy_id",
                    "upload_id",
                    "historical",
                )
            ):
                raise CoreRefused("reviewed_selection_changed")
            return await self._status(
                await self.request(
                    "POST",
                    "jobs",
                    {
                        "plan_id": selection.plan_digest,
                        "operation_id": call.operation_id,
                        "owner_id": self.owner_id,
                        **(
                            {"accept_owner_capture": True}
                            if selection.accept_owner_capture
                            else {}
                        ),
                        **(
                            {"allow_repository_code": True}
                            if selection.allow_repository_code
                            else {}
                        ),
                        **(
                            {"restore_deleted": True}
                            if selection.restore_deleted
                            else {}
                        ),
                    },
                )
            )
        if call.op == "list":
            page = await self.request("GET", f"library?offset={call.offset or 0}")
            jobs = page.get("jobs")
            if not isinstance(jobs, list):
                raise CoreRefused("invalid_host_page")
            selected = [
                job
                for job in jobs
                if isinstance(job, dict) and job.get("owner_id") == self.owner_id
            ]
            observations = await asyncio.gather(
                *(self._status(cast(dict[str, JsonValue], job)) for job in selected)
            )
            statuses = tuple(
                observation.model_copy(
                    update={"backend_reference": None, "result": None, "input": None}
                )
                for observation in observations
            )

            return OperationPage(
                operations=statuses,
                next_offset=cast(int | None, page.get("next_offset")),
                active=sum(job.state in {"queued", "running"} for job in statuses),
                outstanding=sum(job.state == "interrupted" for job in statuses),
            )
        if call.operation_id is None:
            raise CoreRefused("operation_id_required")
        job_id = self._job_id(call.operation_id)
        job = (
            await self.cancel_job(job_id)
            if call.op == "cancel"
            else await self.request("GET", "jobs/" + job_id)
        )
        if job.get("owner_id") != self.owner_id:
            raise CoreRefused("job_owner_changed")
        if call.op == "log":
            # Core retains safe status rather than arbitrary worker stdout.
            text = (
                f"{job['state']}: {job['stage']}\n" if (call.offset or 0) == 0 else ""
            )
            return OperationLog(
                operation_id=call.operation_id,
                offset=call.offset or 0,
                next_offset=(call.offset or 0) + len(text.encode()),
                truncated=False,
                text=text,
            )
        return await self._status(job)
