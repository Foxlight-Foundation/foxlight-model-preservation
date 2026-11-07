"""One reviewed switch over Core's persistent mirror, as Sovereign's community mirror.

Sovereign's community mirror records one standing choice (what to share, a
licence list and one consent that lasts a year) and turns it into a bounded
exact-policy restart grant on the core mirror ledger. Skulk Core already owns
that ledger through its persistent-mirror controller, so this module drives
the same controller with the same policy: it retains the reviewed public copies
whose every member licence is on the owner's list, grants restart permission
for the consent's year and starts the controller in resume mode.

The Library keeps no durable state of its own. Every step is an ordinary
operator decision on Core, recorded in Core's mirror history; a closed or
restarted Library never repeats a decision or consents again on its own.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import suppress
from typing import Final, Literal

from pydantic import (
    BaseModel,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
    model_validator,
)

from .client import CoreClient, CoreRefused
from .compatibility import check_core
from .contracts import Contract
from .licences import (
    DEFAULT_LICENSES,
    LICENCE_DEFINITION_VERSION,
    UNIDENTIFIED,
    definition_json,
    licence_status,
    normalize_license,
)
from .mirrors import (
    MirrorConfigurationInput,
    MirrorCopyInput,
    MirrorStartInput,
    reviewed_public_copy,
)

__all__ = [
    "CONSENT_SECONDS",
    "DEFAULT_LICENSES",
    "UNIDENTIFIED",
    "ShareSwitch",
    "licence_status",
    "normalize_license",
    "share_control",
]

# The licence families, their defaults and the identifiers that name no
# licence come from the core's canonical definition (see licences.py), shipped
# here byte for byte because the managed Library does not install the core.
# One consent lasts a year, as on Sovereign; the core caps grants at 366 days.
CONSENT_SECONDS: Final = 365 * 86400
# The disclosure text version the sheet showed before consent.
DISCLOSURE_VERSION: Final = 1
_GIB: Final = 1024**3
# Defaults for a first mirror. Advanced settings change them. The storage
# allowance is always bounded and the download budget protects this host's
# connection; upload limits are opt-in, so a new setup has none (None).
DEFAULT_CONFIGURATION: Final[dict[str, JsonValue]] = {
    "name": "Library community mirror",
    "disk_bytes": 50 * _GIB,
    "ingress_daily": 20 * _GIB,
    "egress_daily": None,
    "egress_monthly": None,
    "http": True,
    "exposure": "network",
    "port": 8743,
    "ipfs": False,
    "torrent": False,
    "peer_setup_id": None,
    "max_active": 2,
    "session_seconds": 3600,
    "session_bytes": 64 * 1024**2,
    "upload_rate": 1024**2,
}
_TERMINAL: Final = frozenset({"succeeded", "failed", "cancelled", "interrupted"})
_ACTIVE: Final = frozenset({"queued", "running", "cancelling"})

Status = Literal[
    "sharing",
    "kept_back",
    "pending",
    "eligible",
    "license_not_selected",
    "no_license",
    "unsupported",
    "removed",
    "review_unavailable",
]
Step = Literal["stopping", "configuring", "keeping", "permitting", "starting"]


def _validated[T: BaseModel](contract: type[T], body: dict[str, JsonValue]) -> T:
    """Surface a policy refusal's own code instead of a generic invalid input."""
    try:
        return contract.model_validate_json(json.dumps(body))
    except ValidationError as error:
        for detail in error.errors():
            cause = (detail.get("ctx") or {}).get("error")
            if isinstance(cause, CoreRefused):
                raise CoreRefused(str(cause)) from error
        raise


def _allowed(licences: tuple[str, ...]) -> frozenset[str]:
    return frozenset(normalize_license(item) for item in licences)


class _Licences(Contract):
    licenses: tuple[str, ...] = Field(min_length=1, max_length=128)

    @field_validator("licenses")
    @classmethod
    def identified(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Refuse an entry that names no licence; it could admit unlicensed models."""
        for item in value:
            if len(item) > 200 or normalize_license(item) in UNIDENTIFIED:
                raise CoreRefused("community_license_unidentified")
        return value


class ShareReviewInput(_Licences):
    """The exact public copies to evaluate against a licence list; reads only."""

    copies: tuple[MirrorCopyInput, ...] = Field(max_length=256)


class ShareStartInput(_Licences):
    """The one reviewed decision: what, which licences, how, and the consent."""

    consent: Literal[True] = Field(
        description="Keep serving the listed public copies until turned off."
    )
    disclosure: Literal[1] = Field(
        description="The version of the disclosure shown before consent."
    )
    policy_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    share: Literal["eligible", "chosen"]
    copies: tuple[MirrorCopyInput, ...] = Field(min_length=1, max_length=256)
    configuration: MirrorConfigurationInput

    @model_validator(mode="after")
    def bounded_service(self) -> ShareStartInput:
        """Serving needs a transport; every allowance is explicit and finite."""
        settings = self.configuration
        if not (settings.http or settings.ipfs or settings.torrent):
            raise CoreRefused("mirror_transport_required")
        if (
            settings.egress_daily is not None
            and settings.egress_monthly is not None
            and settings.egress_daily > settings.egress_monthly
        ):
            raise CoreRefused("mirror_allowance_invalid")
        if len({copy.manifest_id for copy in self.copies}) != len(self.copies):
            raise CoreRefused("mirror_selection_duplicate")
        return self


class ShareSwitch:
    """In-memory progress over durable Core decisions; never replays a consent."""

    poll_seconds: float = 0.5
    start_wait_seconds: float = 30.0

    def __init__(self, client: CoreClient) -> None:
        self.client = client
        self._task: asyncio.Task[None] | None = None
        self._job: str | None = None
        self._reviews: dict[tuple[str, str], tuple[str, list[str]]] = {}
        # Whether this Core keeps a standing intake policy under the grant.
        self._intake = False
        self._progress: dict[str, JsonValue] = self._idle()

    @staticmethod
    def _idle() -> dict[str, JsonValue]:
        return {
            "state": "idle",
            "step": None,
            "kept": 0,
            "total": 0,
            "skipped": [],
            "error_code": None,
            "expires": None,
        }

    def read(self) -> dict[str, JsonValue]:
        """Report this Library session's latest switch decision and defaults.

        The owner's saved choices (share mode and licences) are read from
        Core's mirror page, where a Core with standing intake keeps them.
        """
        return {
            "progress": dict(self._progress),
            "default_licenses": list(DEFAULT_LICENSES),
            "licence_families": definition_json(),
            "licence_definition": LICENCE_DEFINITION_VERSION,
            "defaults": dict(DEFAULT_CONFIGURATION),
            "consent_seconds": CONSENT_SECONDS,
            "disclosure": DISCLOSURE_VERSION,
        }

    async def _review(self, copy: MirrorCopyInput) -> tuple[str, list[str]]:
        key = (copy.copy_id, copy.manifest_id)
        cached = self._reviews.get(key)
        if cached is not None:
            return cached
        _, checked = await reviewed_public_copy(self.client, copy)
        value = (
            checked.archive_format,
            [member.license_id for member in checked.members],
        )
        # An exact manifest identity names immutable signed terms.
        self._reviews[key] = value
        return value

    async def _held(self) -> dict[str, dict[str, JsonValue]]:
        held: dict[str, dict[str, JsonValue]] = {}
        offset = 0
        while True:
            page = await self.client.request("GET", f"mirror?offset={offset}")
            models = page.get("models")
            for model in models if isinstance(models, list) else []:
                if isinstance(model, dict):
                    identity = model.get("manifest_id")
                    if isinstance(identity, str):
                        held[identity] = model
            following = page.get("next_offset")
            if following is None:
                break
            if type(following) is not int or not offset < following <= 1000:
                raise CoreRefused("invalid_host_response")
            offset = following
        return held

    async def review(self, body: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Evaluate each exact copy under the licence list; starts nothing."""
        selected = _validated(ShareReviewInput, body)
        allowed = _allowed(selected.licenses)
        held = await self._held() if selected.copies else {}
        rows: list[JsonValue] = []
        for copy in selected.copies:
            licences: list[str] = []
            status: Status
            model = held.get(copy.manifest_id)
            try:
                archive, licences = await self._review(copy)
                status = (
                    "unsupported"
                    if archive != "preservation-v1"
                    else licence_status(licences, allowed)
                )
            except CoreRefused:
                status = "review_unavailable"
            if model is not None and model.get("state") == "removed":
                status = "removed"
            elif model is not None and model.get("state") in {"retained", "pending"}:
                status = (
                    "kept_back"
                    if model.get("blocked") is True
                    else "pending"
                    if model.get("state") == "pending"
                    else "sharing"
                )
            rows.append(
                {
                    "copy_id": copy.copy_id,
                    "manifest_id": copy.manifest_id,
                    "status": status,
                    "licenses": list(dict.fromkeys(licences)),
                }
            )
        return {"copies": rows}

    async def start(self, body: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Begin the one reviewed decision; Core performs and records each step."""
        decision = _validated(ShareStartInput, body)
        if self._task is not None and not self._task.done():
            raise CoreRefused("mirror_share_busy")
        page = await self.client.request("GET", "mirror")
        if page.get("policy_id") != decision.policy_id:
            raise CoreRefused("mirror_policy_changed")
        if page.get("configured") is True and page.get("available") is not True:
            raise CoreRefused("mirror_unavailable")
        try:
            features = await check_core(self.client)
        except CoreRefused:
            # Unknown support is treated as an older Core: limits required,
            # and no standing intake policy is sent.
            features = {}
        settings = decision.configuration
        if (
            settings.egress_daily is None or settings.egress_monthly is None
        ) and not features.get("Uncapped uploads"):
            # An older Core can't store "no upload limit"; ask for one.
            raise CoreRefused("mirror_upload_limit_required")
        self._intake = features.get("Standing intake") is True
        self._progress = {
            **self._idle(),
            "state": "working",
            "total": len(decision.copies),
        }
        self._task = asyncio.create_task(self._run(decision, page))
        return self.read()

    async def stop(self) -> dict[str, JsonValue]:
        """Stop sharing in one action: setup, any owned step and the controller."""
        task, owned = self._task, self._job
        if task is not None and not task.done():
            _ = task.cancel()
            with suppress(asyncio.CancelledError, CoreRefused):
                await task
        if owned is not None:
            # A Core step runs on after the Library stops waiting for it.
            job = await self.client.request("GET", "mirror/jobs/" + owned)
            if job.get("state") in _ACTIVE:
                _ = await self.client.request("DELETE", "mirror/jobs/" + owned)
        page = await self.client.request("GET", "mirror")
        controller = page.get("controller_job_id")
        stopped: dict[str, JsonValue] | None = None
        if isinstance(controller, str):
            # Core records cancellation, drains the controller, clears running
            # intent and pauses the role so a restart does not resume it.
            stopped = await self.client.request("DELETE", "mirror/jobs/" + controller)
        self._progress = {**self._progress, "state": "stopped", "step": None}
        return {**self.read(), "job": stopped}

    def _step(self, step: Step) -> None:
        self._progress = {**self._progress, "step": step}

    async def _policy(self) -> str | None:
        page = await self.client.request("GET", "mirror")
        value = page.get("policy_id")
        return value if isinstance(value, str) else None

    async def _decide(self, **decision: JsonValue) -> dict[str, JsonValue]:
        operation = uuid.uuid4().hex
        # JSON validation, as Core receives it: lists stand for tuples.
        checked = MirrorStartInput.model_validate_json(
            json.dumps({**decision, "operation_id": operation})
        )
        job = await self.client.request(
            "POST",
            "mirror/jobs",
            {**checked.wire(), "owner_id": self.client.mirror_owner_id},
        )
        if job.get("operation_id") != operation or not isinstance(
            job.get("job_id"), str
        ):
            raise CoreRefused("invalid_job_identity")
        self._job = str(job["job_id"])
        return job

    async def _settle(
        self, job: dict[str, JsonValue], *, until_running: bool = False
    ) -> dict[str, JsonValue]:
        deadline = time.monotonic() + self.start_wait_seconds
        while job.get("state") not in _TERMINAL:
            if until_running and (
                job.get("state") == "running" or time.monotonic() >= deadline
            ):
                return job
            await asyncio.sleep(self.poll_seconds)
            job = await self.client.request("GET", "mirror/jobs/" + str(job["job_id"]))
        if job.get("state") != "succeeded":
            code = job.get("error_code")
            raise CoreRefused(code if isinstance(code, str) else "mirror_worker_failed")
        return job

    async def _run(self, decision: ShareStartInput, page: dict[str, JsonValue]) -> None:
        try:
            await self._perform(decision, page)
        except CoreRefused as error:
            self._progress = {
                **self._progress,
                "state": "failed",
                "error_code": str(error),
            }
        except (ValueError, OSError):
            self._progress = {
                **self._progress,
                "state": "failed",
                "error_code": "host_unavailable",
            }
        finally:
            self._job = None

    async def _perform(
        self, decision: ShareStartInput, page: dict[str, JsonValue]
    ) -> None:
        controller = page.get("controller_job_id")
        if isinstance(controller, str):
            # Core refuses new intake or a new grant beside a running
            # controller; the reviewed change pauses sharing briefly.
            self._step("stopping")
            _ = await self.client.request("DELETE", "mirror/jobs/" + controller)
            _ = await self._settle_stopped(controller)
        policy = page.get("policy_id")
        policy = policy if isinstance(policy, str) else None
        wanted = decision.configuration.model_dump(mode="json")
        if page.get("configured") is not True or page.get("configuration") != wanted:
            # Existing settings stay untouched unless the reviewed ones differ.
            self._step("configuring")
            _ = await self._settle(
                await self._decide(
                    kind="configure", policy_id=policy, configuration=wanted
                )
            )
            policy = await self._policy()
        if self._intake:
            # Core keeps the owner's choice as a standing intake policy that
            # the grant covers, so the saved list shows after a reload and a
            # newly kept matching copy joins without new consent. Withheld
            # copies stay as Core has them.
            current = page.get("intake")
            withheld: JsonValue = (
                current.get("withheld", []) if isinstance(current, dict) else []
            )
            wanted_intake: dict[str, JsonValue] = {
                "share": decision.share,
                "licenses": list(
                    dict.fromkeys(normalize_license(item) for item in decision.licenses)
                ),
                "definition": LICENCE_DEFINITION_VERSION,
                "withheld": withheld,
            }
            if current != wanted_intake:
                self._step("configuring")
                _ = await self._settle(
                    await self._decide(
                        kind="intake", policy_id=policy, intake=wanted_intake
                    )
                )
                policy = await self._policy()
        self._step("keeping")
        allowed = _allowed(decision.licenses)
        held = await self._held()
        kept = 0
        skipped: list[JsonValue] = []
        for copy in decision.copies:
            model = held.get(copy.manifest_id)
            state = model.get("state") if model is not None else None
            if state in {"retained", "pending"}:
                # Existing selections and their per-copy choices are kept as is.
                kept += 1
            elif state == "removed":
                # Returning a removed copy is a separate explicit re-intake.
                skipped.append({"copy_id": copy.copy_id, "reason": "removed"})
            else:
                reason: str | None
                try:
                    archive, licences = await self._review(copy)
                    reason = (
                        "unsupported"
                        if archive != "preservation-v1"
                        else None
                        if licence_status(licences, allowed) == "eligible"
                        else licence_status(licences, allowed)
                    )
                except CoreRefused as error:
                    reason = str(error)
                if reason is None:
                    try:
                        _ = await self._settle(
                            await self._decide(
                                kind="retain",
                                policy_id=policy,
                                copy_id=copy.copy_id,
                                manifest_id=copy.manifest_id,
                                confirm_redistribution=True,
                            )
                        )
                        kept += 1
                    except CoreRefused as error:
                        reason = str(error)
                    policy = await self._policy()
                if reason is not None:
                    skipped.append({"copy_id": copy.copy_id, "reason": reason})
            self._progress = {**self._progress, "kept": kept, "skipped": skipped}
        if kept == 0:
            raise CoreRefused("community_nothing_to_share")
        self._step("permitting")
        expires = int(time.time()) + CONSENT_SECONDS
        _ = await self._settle(
            await self._decide(
                kind="grant_resume",
                policy_id=policy,
                confirm_resume=True,
                resume_expires=expires,
            )
        )
        self._step("starting")
        _ = await self._settle(
            await self._decide(
                kind="run",
                policy_id=policy,
                public_participation=True,
                mode="resume",
            ),
            until_running=True,
        )
        self._progress = {
            **self._progress,
            "state": "done",
            "step": None,
            "expires": expires,
        }

    async def _settle_stopped(self, job_id: str) -> dict[str, JsonValue]:
        while True:
            job = await self.client.request("GET", "mirror/jobs/" + job_id)
            if job.get("state") in _TERMINAL:
                return job
            await asyncio.sleep(self.poll_seconds)


async def share_control(
    switch: ShareSwitch, action: str, body: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    """Route the switch's four controls; reads never start or change sharing."""
    if action == "mirror-share-read":
        return switch.read()
    if action == "mirror-share-review":
        return await switch.review(body)
    if action == "mirror-share-start":
        return await switch.start(body)
    if action == "mirror-share-stop":
        return await switch.stop()
    raise CoreRefused("unknown_control")
