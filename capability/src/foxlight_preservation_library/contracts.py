"""Public bounded capability controls and exact managed-bundle declaration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

if TYPE_CHECKING:
    from skulk_capability_sdk.contracts import Manifest


class Contract(BaseModel):
    """Strict public UI values independent of the optional managed SDK."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


INVENTORY_ID = "foxlight.preservation.library"
COPY_ID = "foxlight.preservation.copy"
TITLE = "Model Preservation"
SURFACE = "library"


class Settings(Contract):
    """Owner configuration; the first host adapter always controls its local Core."""

    skulk_api_url: str = Field(
        default="http://127.0.0.1:52415",
        max_length=256,
        description="Local Skulk Core authority on the model-store-owning host.",
    )

    @field_validator("skulk_api_url")
    @classmethod
    def local_authority(cls, value: str) -> str:
        """Reject remote URLs, embedded credentials and path/query discovery."""
        parts = urlsplit(value)
        if (
            parts.scheme != "http"
            or parts.hostname not in {"127.0.0.1", "::1", "localhost"}
            or parts.username is not None
            or parts.password is not None
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
            or parts.port is None
        ):
            raise ValueError("configure an explicit loopback Core URL with port")
        return value.rstrip("/")


class LibraryCall(Contract):
    """Read library pages or explicitly enroll the built-in private archive."""

    action: Literal["read", "setup_device"] = "read"
    offset: int = Field(
        default=0, ge=0, le=2**31, description="Bounded host page offset."
    )


class HostReport(Contract):
    """Bounded safe host report; actual fields follow the shipped Core API."""

    report: dict[str, JsonValue] = Field(
        description="Host observations without paths or private keys."
    )


class CopyInput(Contract):
    """Plan by exact selection; start uses the returned immutable plan digest."""

    kind: Literal["preserve", "check", "kit_export", "recover"]
    location_id: str = Field(min_length=1, max_length=128)
    model_id: str | None = Field(default=None, max_length=512)
    generation_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    copy_id: str | None = Field(default=None, max_length=128)
    upload_id: str | None = Field(default=None, max_length=128)
    historical: bool = False
    accept_owner_capture: bool = False
    allow_repository_code: bool = False
    restore_deleted: bool = False
    plan_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class SharingInput(Contract):
    """Public data-only selections; no runtime permissions or host paths."""

    kind: Literal["mirror_import", "http_share", "torrent_share"]
    location_id: str = Field(min_length=1, max_length=128)
    copy_id: str | None = Field(default=None, max_length=128)
    upload_id: str | None = Field(default=None, max_length=128)
    public_network: bool = False
    listen_port: int = Field(default=0, ge=0, le=65535)
    duration_seconds: int = Field(default=3600, ge=1, le=86400)
    upload_rate_bytes: int = Field(default=1048576, ge=1024, le=104857600)
    upload_limit_bytes: int = Field(default=1073741824, ge=1, le=2**53 - 1)
    advertised_url: str | None = Field(default=None, max_length=2048)


class CopyResult(Contract):
    """Exact completed retained copy, never an inferred runtime qualification."""

    manifest_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    copy_id: str | None = Field(default=None, min_length=1, max_length=128)
    restored: bool = False
    kit_ready: bool = False
    member_count: int | None = Field(default=None, ge=1)


def build_manifest(*, executable: str, executable_sha256: str) -> Manifest:
    """Declare the separately packaged UI and governed durable-copy contract."""
    from skulk_capability_sdk.contracts import (
        Bounds,
        Descriptor,
        Manifest,
        OperationBounds,
        StewardExposure,
        Surface,
    )
    from skulk_capability_sdk.operations import operation_descriptor

    descriptor = operation_descriptor(
        id=COPY_ID,
        version="0.2.0",
        title="Preserve, recover or check a model",
        description=(
            "Review an exact plan, start a Core-owned job, observe retained "
            "status or request cancellation. Page and child restart do not "
            "own its byte lifetime."
        ),
        input_schema=CopyInput.model_json_schema(),
        result_schema=CopyResult.model_json_schema(),
    )
    return Manifest(
        bundle_id="foxlight.model-preservation",
        skulk_requires=">=2.0.3,<3",
        bundle_version="0.4.1",
        executable=executable,
        executable_sha256=executable_sha256,
        arguments=(),
        effect="governed",
        title=TITLE,
        descriptors=(
            Descriptor(
                id=INVENTORY_ID,
                version="0.1.0",
                title="Model preservation library",
                description=(
                    "Read bounded local installed/library/activity observations, "
                    "or explicitly enroll this device's private archive."
                ),
                input_schema=LibraryCall.model_json_schema(),
                output_schema=HostReport.model_json_schema(),
            ),
            descriptor,
        ),
        operations=OperationBounds(
            max_queued=32,
            max_runtime_seconds=86400,
            stale_after_seconds=120,
            retain=256,
            log_bytes=8192,
        ),
        steward=(
            StewardExposure(
                qualified_id=descriptor.qualified_id,
                behavior="operation",
                risk="effect",
                provider_selection=None,
                summary=(
                    "Preserve or check an exact local model selection using "
                    "Core's owned durable jobs."
                ),
            ),
        ),
        surfaces=(Surface(surface_id=SURFACE, title=TITLE, kind="link"),),
        configuration_schema=Settings.model_json_schema(),
        preflight_profile="local",
        bounds=Bounds(call_seconds=20),
    )


def validate_manifest(manifest: Manifest) -> None:
    """Refuse publisher metadata that differs from this packaged implementation."""
    if manifest != build_manifest(
        executable=manifest.executable, executable_sha256=manifest.executable_sha256
    ):
        raise ValueError("manifest differs from this capability's generated contract")
