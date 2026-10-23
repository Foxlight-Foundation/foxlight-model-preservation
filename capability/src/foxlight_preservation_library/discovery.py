"""Typed HTTP presentation of canonical Core discovery; no source cache or worker."""

from typing import Literal

from pydantic import Field, JsonValue

from .client import CoreClient, CoreRefused
from .contracts import Contract


class SourceReviewInput(Contract):
    """Select only completed host-created bootstrap metadata ingress."""

    upload_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class SourceFollowInput(Contract):
    """Confirm the reviewed metadata authority, without model or code grants."""

    review_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: bool
    allow_http: bool = False
    allow_private: bool = False


class SourceInput(Contract):
    """An exact known source handle, never an endpoint or filesystem path."""

    source_id: str = Field(pattern=r"^[0-9a-f]{64}$")


class DiscoveryPageInput(Contract):
    """Bounded metadata activity, under this client's separate owner namespace."""

    offset: int = Field(default=0, ge=0, le=1024)


class DiscoveryQueryInput(Contract):
    """Local cached query; never becomes a supplier URL."""

    query: str = Field(default="", max_length=500)
    offset: int = Field(default=0, ge=0, le=1000)
    supplier_offset: int = Field(default=0, ge=0, le=16000)


class DiscoveryStartInput(Contract):
    """Explicit finite metadata work; Core independently validates the selection."""

    operation_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9._:@-]+$"
    )
    kind: Literal["refresh", "model_metadata"]
    source_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    entry_id: str | None = Field(default=None, max_length=512)
    manifest_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    accept_source_keys: bool = False
    allow_http: bool = False
    allow_private: bool = False


class DiscoveryJobInput(Contract):
    """Observe or cancel only this client's retained metadata operation."""

    job_id: str = Field(pattern=r"^[0-9a-f]{64}$")


async def discovery_control(
    client: CoreClient, action: str, body: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    """Translate exact typed UI actions while retaining Core authority and jobs."""
    if action == "discovery-read":
        page = DiscoveryPageInput.model_validate(body)
        return await client.request(
            "GET",
            f"discovery?owner_id={client.discovery_owner_id}&offset={page.offset}",
        )
    if action == "discovery-search":
        query = DiscoveryQueryInput.model_validate(body)
        return await client.request(
            "POST", "discovery/search", query.model_dump(mode="json")
        )
    if action == "source-review":
        upload = SourceReviewInput.model_validate(body)
        return await client.request(
            "POST", "discovery/reviews", upload.model_dump(mode="json")
        )
    if action == "source-follow":
        choice = SourceFollowInput.model_validate(body)
        return await client.request(
            "POST", "discovery/sources", choice.model_dump(mode="json")
        )
    if action == "source-remove":
        selected = SourceInput.model_validate(body)
        return await client.request("DELETE", "discovery/sources/" + selected.source_id)
    if action == "discovery-start":
        selection = DiscoveryStartInput.model_validate(body)
        return await client.request(
            "POST",
            "discovery/jobs",
            {
                **selection.model_dump(mode="json", exclude_none=True),
                "owner_id": client.discovery_owner_id,
            },
        )
    if action in {"discovery-observe", "discovery-cancel"}:
        selected_job = DiscoveryJobInput.model_validate(body)
        path = "discovery/jobs/" + selected_job.job_id
        observed = await client.request("GET", path)
        if observed.get("owner_id") != client.discovery_owner_id:
            raise CoreRefused("job_owner_changed")
        return (
            await client.request("POST", path + "/cancel", {})
            if action == "discovery-cancel"
            else observed
        )
    raise CoreRefused("unknown_control")
