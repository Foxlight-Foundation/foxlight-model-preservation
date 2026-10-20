"""Bounded HTTP control of local Core; no verifier, key or byte worker lives here."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import cast

import httpx
from pydantic import JsonValue
from skulk_capability_sdk.contracts import Contract
from skulk_capability_sdk.operations import (
    OperationCall,
    OperationLog,
    OperationPage,
    OperationPlan,
    OperationStatus,
    input_digest,
)

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
        self, method: str, path: str, body: dict[str, JsonValue] | None = None
    ) -> dict[str, JsonValue]:
        """Read a bounded safe control response; never transfer artifact bytes."""
        try:
            async with self.http.stream(
                method, "/preservation/" + path, json=body
            ) as response:
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 65536:
                        raise CoreRefused("host_response_too_large")
                if response.status_code not in {200, 202}:
                    try:
                        detail = json.loads(data).get("detail")
                    except (ValueError, AttributeError):
                        detail = None
                    if (
                        isinstance(detail, str)
                        and len(detail) < 80
                        and all(c.islower() or c == "_" for c in detail)
                    ):
                        raise CoreRefused(detail)
                    raise CoreRefused("host_unavailable_or_refused")
                value: JsonValue = json.loads(data)
                if not isinstance(value, dict):
                    raise CoreRefused("invalid_host_response")
                return value
        except httpx.HTTPError as error:
            raise CoreRefused("host_unavailable") from error

    async def library(self, call: LibraryCall) -> HostReport:
        """Read a page or explicitly enroll storage; neither action starts a job."""
        return HostReport(
            report=await self.request("POST", "setup/device", {})
            if call.action == "setup_device"
            else await self.request("GET", f"library?offset={call.offset}")
        )

    def _job_id(self, operation_id: str) -> str:
        return hashlib.sha256(
            (self.owner_id + "\x00" + operation_id).encode()
        ).hexdigest()

    def _input(self, plan: dict[str, JsonValue]) -> dict[str, JsonValue]:
        value = {
            field: plan.get(field)
            for field in ("kind", "location_id", "model_id", "generation_id", "copy_id")
        }
        value["plan_digest"] = plan["plan_id"]
        return cast(
            dict[str, JsonValue],
            CopyInput.model_validate(value).model_dump(mode="json", exclude_none=True),
        )

    async def _status(self, job: dict[str, JsonValue]) -> OperationStatus:
        plan = await self.request("GET", "plans/" + cast(str, job["plan_id"]))
        bound_input = self._input(plan)
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
                self.owner_id + "/foxlight.preservation.copy@0.1.0", bound_input
            ),
            "input": bound_input,
            "backend_reference": job["job_id"],
            "result": job.get("result"),
            "error": job.get("error_code"),
        }
        return OperationStatus.model_validate_json(json.dumps(values))

    async def operation(self, call: OperationCall) -> Contract:
        """Use SDK plan/start/status/list/cancel/log without a second job runner."""
        if call.op in {"plan", "start"}:
            selection = CopyInput.model_validate(call.input)
            if call.op == "plan":
                value = cast(
                    dict[str, JsonValue],
                    selection.model_dump(
                        mode="json", exclude={"plan_digest"}, exclude_none=True
                    ),
                )
                plan = await self.request("POST", "plans", value)
                reviewed = CopyInput.model_validate(self._input(plan))
                return OperationPlan(
                    plan_digest=cast(str, plan["plan_id"]),
                    summary=(
                        f"{selection.kind.capitalize()} {plan['model_id']} "
                        f"with {plan['member_count']} required members "
                        f"({plan['size_bytes']} bytes)."
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
                retained.get(field) != getattr(selection, field)
                for field in (
                    "kind",
                    "location_id",
                    "model_id",
                    "generation_id",
                    "copy_id",
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
        job = await self.request(
            "POST" if call.op == "cancel" else "GET",
            "jobs/" + job_id + ("/cancel" if call.op == "cancel" else ""),
            {} if call.op == "cancel" else None,
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
