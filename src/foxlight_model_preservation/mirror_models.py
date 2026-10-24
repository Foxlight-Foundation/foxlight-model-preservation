"""Local operator authority for persistent HTTP mirrors; no runtime trust grants."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .models import Digest, Identifier, PreservationError, Record, TrustProfile
from .torrent import TorrentExport


class NativePolicy(Record):
    """Explicit public native transports and finite, durably reserved sessions."""

    identity: tuple[int, int]
    ipfs_binary: str | None = None
    ipfs_sha256: Digest | None = None
    bootstrap: tuple[str, ...] = Field(default=(), max_length=8)
    allow_private_peers: bool = False
    torrent: bool = False
    max_active: int = Field(default=2, ge=1, le=4)
    session_seconds: int = Field(default=3600, ge=1, le=86400)
    session_bytes: int = Field(default=64 * 1024**2, ge=1, le=2**53 - 1)
    upload_rate: int = Field(default=1024**2, ge=1024, le=100 * 1024**2)


class NativeModel(Record):
    """Local per-model custody; stopping publication never removes these bytes."""

    identity: tuple[int, int]
    ipfs_identity: tuple[int, int] | None = None
    torrent: TorrentExport | None = None
    cid: str | None = None
    error: str | None = None
    last_started: int | None = None
    last_stopped: int | None = None
    disk_reservation: int = Field(default=0, ge=0)
    contacts: tuple[str, ...] = Field(default=(), max_length=8)
    last_torrent_port: int = Field(default=0, ge=0, le=65535)
    publications: int = Field(default=0, ge=0)


class Terms(Record):
    """An exact license/reference pair independently reviewed by the operator."""

    license_id: str = Field(min_length=1, max_length=200)
    terms_reference: str = Field(min_length=1, max_length=2048)


class SourceGrant(Record):
    """Source following is separate from explicit model/terms/network authority."""

    trust: TrustProfile
    terms: tuple[Terms, ...] = Field(min_length=1, max_length=100)
    allow_http: bool = False
    allow_private: bool = False
    allow_local: bool = False
    max_bytes: int = Field(default=20 * 1024**3, ge=1, le=2**53 - 1)


class HeldModel(Record):
    """Retained exact selection and original policy, independent of catalog churn."""

    source_id: Digest
    entry_id: str = Field(min_length=1, max_length=200)
    grant: SourceGrant
    mirrors: tuple[str, ...] = Field(min_length=1, max_length=16)
    operation_id: Identifier
    transfer_bytes: int | None = Field(default=None, ge=1, le=2**53 - 1)
    pinned: bool = False
    blocked: bool = False
    state: Literal["pending", "retained", "removed"] = "pending"
    error: str | None = None
    verified_at: int | None = None


class RestartGrant(Record):
    """Separate expiring resume authority bound to this exact local policy."""

    policy_id: Digest
    expires: int = Field(ge=1)


class MirrorState(Record):
    """One atomic owner-only policy/ledger with conservative durable reservations."""

    schema_version: Literal["foxlight.mirror-state.v1", "foxlight.mirror-state.v2"] = (
        "foxlight.mirror-state.v1"
    )
    identity: dict[str, tuple[int, int]]
    name: str = Field(min_length=1, max_length=200)
    salt: Digest
    disk_bytes: int = Field(ge=256 * 1024**2, le=2**53 - 1)
    ingress_daily: int = Field(ge=1, le=2**53 - 1)
    egress_daily: int = Field(ge=1, le=2**53 - 1)
    egress_monthly: int = Field(ge=1, le=2**53 - 1)
    bind: Literal["127.0.0.1", "0.0.0.0"] = "127.0.0.1"  # noqa: S104
    port: int = Field(default=8080, ge=0, le=65535)
    serve_start: int = Field(default=0, ge=0, le=23)
    serve_end: int = Field(default=0, ge=0, le=23)
    refresh_seconds: int = Field(default=21600, ge=60, le=86400)
    sources: dict[Digest, SourceGrant] = Field(default_factory=dict, max_length=16)
    models: dict[Digest, HeldModel] = Field(default_factory=dict, max_length=1000)
    paused: bool = False
    left: bool = False
    restart: RestartGrant | None = None
    clock_floor: int = Field(ge=0)
    day: str = ""
    month: str = ""
    ingress_used: int = Field(default=0, ge=0)
    egress_day_used: int = Field(default=0, ge=0)
    egress_month_used: int = Field(default=0, ge=0)
    reserved_operations: tuple[Identifier, ...] = Field(default=(), max_length=2000)
    last_cycle: int | None = None
    last_error: str | None = None
    native_policy: NativePolicy | None = None
    native_models: dict[Digest, NativeModel] = Field(
        default_factory=dict, max_length=1000
    )

    @model_validator(mode="after")
    def native_profile_version(self) -> MirrorState:
        """Native custody needs the explicit v2 profile, not a reinterpreted v1."""
        if (
            self.native_policy or self.native_models
        ) and self.schema_version != "foxlight.mirror-state.v2":
            raise PreservationError("native mirror policy requires state v2")
        if self.native_models and self.native_policy is None:
            raise PreservationError("native custody requires its enrolled policy")
        return self
