"""Bounded persistent-mirror controls over Core's independently owned jobs."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import ConfigDict, Field, JsonValue, ValidationError, model_validator

from .client import CoreClient, CoreRefused
from .contracts import Contract


class MirrorConfigurationInput(Contract):
    """Offline resource/transport choices; no path, executable or signer input."""

    name: str = Field(min_length=1, max_length=200)
    disk_bytes: int = Field(ge=256 * 1024**2, le=2**53 - 1)
    ingress_daily: int = Field(ge=1, le=2**53 - 1)
    egress_daily: int = Field(ge=1, le=2**53 - 1)
    egress_monthly: int = Field(ge=1, le=2**53 - 1)
    http: bool = False
    exposure: Literal["loopback", "network"] = "loopback"
    port: int = Field(default=8080, ge=0, le=65535)
    ipfs: bool = False
    torrent: bool = False
    peer_setup_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    max_active: int = Field(default=2, ge=1, le=4)
    session_seconds: int = Field(default=3600, ge=1, le=86400)
    session_bytes: int = Field(default=64 * 1024**2, ge=1, le=2**53 - 1)
    upload_rate: int = Field(default=1024**2, ge=1024, le=100 * 1024**2)

    @model_validator(mode="after")
    def peer_selection(self) -> MirrorConfigurationInput:
        """Bind IPFS to an exact reviewed local connection profile."""
        if self.ipfs != (self.peer_setup_id is not None):
            raise ValueError("IPFS requires its exact reviewed peer setup")
        return self


class MirrorStartInput(Contract):
    """One immutable operator decision with separate effect confirmations."""

    operation_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9._:@-]+$"
    )
    kind: Literal[
        "configure", "retain", "run", "grant_resume", "revoke_resume", "model", "prune"
    ]
    policy_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    configuration: MirrorConfigurationInput | None = None
    copy_id: str | None = Field(default=None, min_length=1, max_length=128)
    manifest_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    action: Literal["pin", "unpin", "block", "unblock", "retry"] | None = None
    confirm_redistribution: bool = False
    confirm_reintake: bool = False
    public_participation: bool = False
    mode: Literal["finite", "resume"] = "finite"
    seconds: int = Field(default=3600, ge=1, le=86400)
    resume_expires: int | None = Field(default=None, ge=1)
    confirm_resume: bool = False
    confirm_delete_manifest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def exact_decision(self) -> MirrorStartInput:
        """Refuse missing consent or choices belonging to another decision."""
        if (self.configuration is not None) != (self.kind == "configure"):
            raise ValueError("configuration belongs only to configure")
        if self.kind != "configure" and self.policy_id is None:
            raise ValueError("review the current mirror policy")
        if self.kind == "retain":
            if (
                not self.copy_id
                or not self.manifest_id
                or not self.confirm_redistribution
            ):
                raise ValueError(
                    "retain requires an exact public copy and reviewed terms"
                )
        elif (
            self.copy_id is not None
            or self.confirm_redistribution
            or self.confirm_reintake
        ):
            raise ValueError("intake confirmations belong only to retain")
        if self.kind in {"model", "prune"} and self.manifest_id is None:
            raise ValueError("select the exact retained model")
        if (
            self.kind not in {"model", "prune", "retain"}
            and self.manifest_id is not None
        ):
            raise ValueError("model identity belongs only to model operations")
        if (self.action is not None) != (self.kind == "model"):
            raise ValueError("model controls require their exact action")
        if self.kind == "run":
            if not self.public_participation:
                raise ValueError("public sharing requires explicit permission")
        elif self.public_participation or self.mode != "finite" or self.seconds != 3600:
            raise ValueError("run permission belongs only to run")
        if self.kind == "grant_resume":
            if not self.confirm_resume or self.resume_expires is None:
                raise ValueError("restart permission requires a confirmed expiry")
        elif self.confirm_resume or self.resume_expires is not None:
            raise ValueError("restart permission belongs only to grant_resume")
        if self.kind == "prune":
            if self.confirm_delete_manifest != self.manifest_id:
                raise ValueError("deletion requires exact model confirmation")
        elif self.confirm_delete_manifest is not None:
            raise ValueError("deletion confirmation belongs only to prune")
        return self


class MirrorPageInput(Contract):
    """One bounded page of global managed-mirror selections."""

    offset: int = Field(default=0, ge=0, le=1000)


class MirrorHistoryInput(Contract):
    """One bounded page of this Library's permanent mirror decisions."""

    offset: int = Field(default=0, ge=0, le=4096)


class MirrorJobInput(Contract):
    """An exact known job; a stopped old job cannot stop a newer controller."""

    job_id: str = Field(pattern=r"^[0-9a-f]{64}$")


class MirrorCopyInput(Contract):
    """Exact accepted public selection whose complete terms the operator reviews."""

    copy_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$")
    manifest_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    member_count: int = Field(ge=1, le=256)


class _ReviewMember(Contract):
    """Required display data; unknown additive response fields remain opaque."""

    model_config = ConfigDict(extra="ignore")
    member_id: str = Field(min_length=1, max_length=64)
    role: str = Field(min_length=1, max_length=64)
    repository: str = Field(max_length=512)
    revision: str = Field(max_length=40)
    format: str = Field(max_length=128)
    size_bytes: int = Field(ge=0)
    file_count: int = Field(ge=1)
    license_id: str = Field(max_length=200)
    terms_reference: str = Field(max_length=2048)
    notice_paths: tuple[str, ...] = Field(max_length=100)


class _CopyReview(Contract):
    """Validate the complete versioned view before presenting an operator grant."""

    model_config = ConfigDict(extra="ignore")
    schema_version: Literal["skulk.preservation-public-review.v1"]
    copy_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$")
    manifest_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    archive_format: Literal["preservation-v1", "pmf-v0.1"]
    name: str = Field(max_length=200)
    size_bytes: int = Field(ge=0)
    workloads: tuple[str, ...] = Field(max_length=100)
    runtime_requirements: tuple[str, ...] = Field(max_length=100)
    members: tuple[_ReviewMember, ...] = Field(min_length=1, max_length=256)


async def mirror_control(
    client: CoreClient, action: str, body: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    """Translate typed choices without transferring model bytes or local authority."""
    if action == "mirror-copy-review":
        copy = MirrorCopyInput.model_validate(body)
        review = await client.request(
            "GET", "public-copies/" + copy.copy_id + "/review"
        )
        # Validation is deliberately separate from the returned dictionary:
        # preserve unknown additive metadata while refusing incomplete terms.
        try:
            checked = _CopyReview.model_validate_json(json.dumps(review))
        except ValidationError as error:
            raise CoreRefused("public_copy_review_changed") from error
        if (
            checked.copy_id != copy.copy_id
            or checked.manifest_id != copy.manifest_id
            or len(checked.members) != copy.member_count
            or len({member.member_id for member in checked.members})
            != copy.member_count
            or sum(member.size_bytes for member in checked.members)
            != checked.size_bytes
        ):
            raise CoreRefused("public_copy_review_changed")
        return review
    if action == "mirror-read":
        page = MirrorPageInput.model_validate(body)
        return await client.request("GET", f"mirror?offset={page.offset}")
    if action == "mirror-history":
        history = MirrorHistoryInput.model_validate(body)
        return await client.request(
            "GET",
            f"mirror/jobs?owner_id={client.mirror_owner_id}&offset={history.offset}",
        )
    if action == "mirror-start":
        decision = MirrorStartInput.model_validate(body)
        return await client.request(
            "POST",
            "mirror/jobs",
            {**decision.model_dump(mode="json"), "owner_id": client.mirror_owner_id},
        )
    if action in {"mirror-observe", "mirror-stop"}:
        selected = MirrorJobInput.model_validate(body)
        path = "mirror/jobs/" + selected.job_id
        job = await client.request("GET", path)
        if job.get("job_id") != selected.job_id:
            raise CoreRefused("invalid_job_identity")
        if job.get("owner_id") != client.mirror_owner_id:
            # Startup controllers use Core's namespace. Only the current global
            # controller may cross this presentation namespace; private/older
            # operations from another client remain outside this ticket's view.
            page = await client.request("GET", "mirror")
            if (
                job.get("kind") != "run"
                or page.get("controller_job_id") != selected.job_id
            ):
                raise CoreRefused("job_owner_changed")
        return await client.request("DELETE", path) if action == "mirror-stop" else job
    raise CoreRefused("unknown_control")
