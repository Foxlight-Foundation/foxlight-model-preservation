"""Local operator authority for persistent HTTP mirrors; no runtime trust grants."""

from __future__ import annotations

from typing import Annotated, Literal, get_args

from pydantic import Field, field_validator, model_validator

from .licences import identified_licences
from .mirror_cards import base_url
from .models import Digest, Identifier, PreservationError, Record, TrustProfile
from .torrent import TorrentExport

StateVersion = Literal[
    "foxlight.mirror-state.v1",
    "foxlight.mirror-state.v2",
    "foxlight.mirror-state.v3",
    "foxlight.mirror-state.v4",
    "foxlight.mirror-state.v5",
    "foxlight.mirror-state.v6",
    "foxlight.mirror-state.v7",
]
_VERSIONS: tuple[StateVersion, ...] = get_args(StateVersion)


def reached(current: StateVersion, minimum: StateVersion) -> bool:
    """Whether a ledger profile is at least the given one."""
    return _VERSIONS.index(current) >= _VERSIONS.index(minimum)


def at_least(current: StateVersion, minimum: StateVersion) -> StateVersion:
    """Raise a ledger profile to a minimum; never lower a newer one."""
    return current if reached(current, minimum) else minimum


NativeCustodyName = Annotated[str, Field(pattern=r"^[a-f0-9]{64}-[a-f0-9]{24}$")]


class MirrorObservation(Record):
    """Live owned activity, never proof of publication or public reachability."""

    running: bool = Field(
        description="False only after owned work and listeners drain."
    )
    http_port: int | None = Field(
        default=None,
        ge=1,
        le=65535,
        description="Owned HTTP listener port, not public reachability evidence.",
    )
    native_workers: int = Field(
        ge=0,
        le=4,
        description="Live native workers, including preparation/drain, not replicas.",
    )


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
    # Also seed the matching Pirate Face torrent of each held copy (v7).
    pirate_face: bool = False


class PirateFaceSeed(Record):
    """What one held copy's Pirate Face lookup found; claims until pieces check.

    ``objects`` maps every file of the listed torrent onto a held object (by
    the SHA-256 Pirate Face recorded, or the same path and size). ``verified``
    is set only after libtorrent checked every piece against those bytes. A
    failed lookup or a delisting keeps a verified torrent seeding.
    """

    repository: str = Field(min_length=3, max_length=200)
    checked_at: int = Field(ge=0)
    listed: bool = False
    infohash: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    objects: tuple[Digest, ...] = Field(default=(), max_length=100_000)
    verified: bool = False
    lookup_error: str | None = Field(default=None, max_length=400)
    error: str | None = Field(default=None, max_length=400)
    last_started: int | None = None
    last_stopped: int | None = None
    sessions: int = Field(default=0, ge=0)


class NativeModel(Record):
    """Local custody or refusal receipt; a receipt alone never implies bytes."""

    identity: tuple[int, int] | None = None
    custody_name: NativeCustodyName | None = None
    pending_custody: tuple[NativeCustodyName, ...] = Field(default=(), max_length=256)
    ipfs_identity: tuple[int, int] | None = None
    ipfs_repo: str = Field(default="ipfs", pattern=r"^ipfs(?:-[a-f0-9]{24})?$")
    torrent: TorrentExport | None = None
    torrent_directory: str = Field(
        default="torrent", pattern=r"^torrent(?:-[a-f0-9]{24})?$"
    )
    cid: str | None = None
    error: str | None = None
    last_started: int | None = None
    last_stopped: int | None = None
    disk_reservation: int = Field(default=0, ge=0)
    contacts: tuple[str, ...] = Field(default=(), max_length=8)
    last_torrent_port: int = Field(default=0, ge=0, le=65535)
    publications: int = Field(default=0, ge=0)
    session_counts: dict[Literal["ipfs", "torrent"], int] = Field(
        default_factory=dict, max_length=2
    )


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


class WithdrawalAdvisory(Record):
    """A followed catalog stopped listing a retained copy: advice, not a takedown.

    Catalogs are discovery, not authority. The copy's own signed manifest,
    licence and notices are unchanged, so it keeps serving unless the operator
    blocks it or chose ``follow_withdrawals``. ``source_id`` and ``catalog_id``
    name the withdrawing source and the exact catalog version that omitted it;
    all three facts are None for a receipt converted from an earlier release.
    ``acknowledged`` records that the operator reviewed it and keeps serving.
    """

    source_id: Digest | None = None
    catalog_id: Digest | None = None
    observed_at: int | None = Field(default=None, ge=0)
    acknowledged: bool = False


class HeldModel(Record):
    """Retained exact selection and original policy, independent of catalog churn."""

    origin: Literal["source", "local"] = "source"
    envelope_sha256: Digest | None = None
    catalog_id: Digest | None = None
    source_id: Digest | None = None
    entry_id: str | None = Field(default=None, min_length=1, max_length=200)
    grant: SourceGrant
    mirrors: tuple[str, ...] = Field(default=(), max_length=16)
    operation_id: Identifier
    transfer_bytes: int | None = Field(default=None, ge=1, le=2**53 - 1)
    pinned: bool = False
    blocked: bool = False
    state: Literal["pending", "retained", "removed"] = "pending"
    error: str | None = None
    verified_at: int | None = None
    deferred: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        description="Budget refusal retried automatically; never a sticky error.",
    )
    withdrawal: WithdrawalAdvisory | None = Field(
        default=None,
        description="No current followed catalog lists it; served unless the "
        "operator blocks it or follows withdrawals.",
    )

    @model_validator(mode="after")
    def selection_authority(self) -> HeldModel:
        """Local custody cannot invent source authority or a network retry route."""
        if self.origin == "source":
            if self.source_id is None or self.entry_id is None or not self.mirrors:
                raise PreservationError(
                    "source selection needs its original source and mirrors"
                )
        elif (
            self.envelope_sha256 is None
            or self.catalog_id is not None
            or self.source_id is not None
            or self.entry_id is not None
            or self.mirrors
            or self.transfer_bytes is not None
            or self.grant.allow_http
            or self.grant.allow_private
            or self.grant.allow_local
        ):
            raise PreservationError(
                "local selection needs an exact wrapper and no network authority"
            )
        return self


class AnnouncePolicy(Record):
    """Explicit public announcement of this mirror's own reachable address.

    A signed mirror card names ``public_url``; it is served in the mirror's own
    peers document. Nostr publishing and DHT announcing are separate opt-ins
    that copy the address to third-party relays or the public DHT. This is not
    anonymous: the address, and the models it probably holds, are public.
    """

    public_url: str = Field(min_length=8, max_length=2048)
    contact: str | None = Field(default=None, min_length=1, max_length=200)
    nostr: bool = False
    dht: bool = False

    @model_validator(mode="after")
    def plain_url(self) -> AnnouncePolicy:
        """Accept only a credential-free HTTP(S) base URL without a trailing slash."""
        if base_url(self.public_url) != self.public_url:
            raise PreservationError("public URL must not end with a slash")
        return self


class RestartGrant(Record):
    """Separate expiring resume authority bound to this exact local policy."""

    policy_id: Digest
    expires: int = Field(ge=1)


class SourceIntake(Record):
    """Private write-ahead binding, never a signed archive or complete receipt."""

    schema_version: Literal["foxlight.mirror-source-intake.v1"] = (
        "foxlight.mirror-source-intake.v1"
    )
    manifest: Digest
    authority_sha256: Digest


class IntakePolicy(Record):
    """The owner's standing public intake, covered by the restart grant.

    ``share="eligible"`` admits every local public selection whose reviewed
    terms all name a selected licence, without new consent: such selections are
    part of what the grant already covers. ``"chosen"`` admits nothing on its
    own, so each selection is consented to individually, as without a policy.
    ``licenses`` is the exact resolved selection (families expanded, custom
    identifiers included) reviewed against licence ``definition``. A later
    definition never widens it. ``withheld`` copies are kept back from serving
    and from standing intake; withholding only narrows, so it is not part of
    the grant's policy identity. Changing anything else needs fresh consent.
    """

    share: Literal["eligible", "chosen"] = "eligible"
    licenses: tuple[str, ...] = Field(min_length=1, max_length=128)
    definition: int = Field(ge=1, le=1000)
    withheld: tuple[Digest, ...] = Field(default=(), max_length=1000)

    @field_validator("licenses")
    @classmethod
    def identified(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Store normalized identified licences once each, so identity is stable."""
        if identified_licences(values) != values:
            raise PreservationError("intake licences must be normalized and unique")
        return values

    @field_validator("withheld")
    @classmethod
    def once(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """List each withheld copy once."""
        if len(set(values)) != len(values):
            raise PreservationError("withheld copies must be unique")
        return values


class MirrorState(Record):
    """One atomic owner-only policy/ledger with conservative durable reservations."""

    schema_version: StateVersion = "foxlight.mirror-state.v1"
    identity: dict[str, tuple[int, int]]
    name: str = Field(min_length=1, max_length=200)
    salt: Digest
    disk_bytes: int = Field(ge=256 * 1024**2, le=2**53 - 1)
    ingress_daily: int = Field(ge=1, le=2**53 - 1)
    # None is no upload cap (v6): bytes are still counted, never refused.
    egress_daily: int | None = Field(ge=1, le=2**53 - 1)
    egress_monthly: int | None = Field(ge=1, le=2**53 - 1)
    bind: Literal["127.0.0.1", "0.0.0.0", "::1", "::"] = "127.0.0.1"  # noqa: S104
    port: int = Field(default=8080, ge=0, le=65535)
    http_enabled: bool = True
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
    # Pirate Face lookups and seeding, by held model (v7).
    pirate_face: dict[Digest, PirateFaceSeed] = Field(
        default_factory=dict, max_length=1000
    )
    announce: AnnouncePolicy | None = None
    intake: IntakePolicy | None = None
    follow_withdrawals: bool = Field(
        default=False,
        description="Operator's choice to stop serving copies a followed catalog "
        "withdrew; off means withdrawal is advisory only.",
    )

    @model_validator(mode="after")
    def native_profile_version(self) -> MirrorState:
        """Require native v2, local v3, HTTP v4, source v5 and uncapped/intake v6."""
        if (
            self.native_policy or self.native_models
        ) and self.schema_version == "foxlight.mirror-state.v1":
            raise PreservationError("native mirror policy requires state v2")
        if self.native_models and self.native_policy is None:
            raise PreservationError("native custody requires its enrolled policy")
        if any(held.origin == "local" for held in self.models.values()) and not reached(
            self.schema_version, "foxlight.mirror-state.v3"
        ):
            raise PreservationError("local mirror selection requires state v3")
        if not self.http_enabled and not reached(
            self.schema_version, "foxlight.mirror-state.v4"
        ):
            raise PreservationError("separate HTTP transport policy requires state v4")
        if any(
            held.origin == "source"
            and (held.envelope_sha256 is not None or held.catalog_id is not None)
            for held in self.models.values()
        ) and not reached(self.schema_version, "foxlight.mirror-state.v5"):
            raise PreservationError("source metadata custody requires state v5")
        if self.intake is not None and any(
            identity in self.models and not self.models[identity].blocked
            for identity in self.intake.withheld
        ):
            raise PreservationError("a withheld mirror selection must stay blocked")
        if needs_v6(self) and not reached(
            self.schema_version, "foxlight.mirror-state.v6"
        ):
            raise PreservationError(
                "uncapped uploads or a standing intake policy require state v6"
            )
        if needs_v7(self) and not reached(
            self.schema_version, "foxlight.mirror-state.v7"
        ):
            raise PreservationError("Pirate Face seeding requires state v7")
        if self.pirate_face and self.native_policy is None:
            raise PreservationError("Pirate Face seeding requires its native policy")
        return self


def needs_v6(state: MirrorState) -> bool:
    """Whether a state uses a choice that only the v6 profile can express."""
    return (
        state.egress_daily is None
        or state.egress_monthly is None
        or state.intake is not None
    )


def needs_v7(state: MirrorState) -> bool:
    """Whether a state uses Pirate Face seeding, which only v7 can express."""
    return bool(
        state.pirate_face
        or (state.native_policy is not None and state.native_policy.pirate_face)
    )


def withheld(state: MirrorState, identity: str) -> bool:
    """Whether the standing intake policy keeps this exact identity back."""
    return state.intake is not None and identity in state.intake.withheld


def upgrade_profile(state: MirrorState) -> MirrorState:
    """Raise the profile to v6 or v7 when needed, so older readers refuse it.

    Older releases accept only the versions they know and reject unknown
    fields, so they refuse a newer ledger outright instead of misreading "no
    cap", ignoring a standing intake policy or seeding without the choice.
    """
    if needs_v7(state):
        return state.model_copy(
            update={
                "schema_version": at_least(
                    state.schema_version, "foxlight.mirror-state.v7"
                )
            }
        )
    if needs_v6(state):
        return state.model_copy(
            update={
                "schema_version": at_least(
                    state.schema_version, "foxlight.mirror-state.v6"
                )
            }
        )
    return state
