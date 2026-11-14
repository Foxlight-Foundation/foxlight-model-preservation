"""Help keep models available: one long-lived mirror of this computer's backups.

One switch and one licence policy cover every backup. Eligible backups join on
their own; there is no per-model step and no hour limit. Serving resumes after a
restart only under the owner's single standing consent, which is bounded and
revocable. Upload limits are the owner's choice and off unless turned on;
the storage allowance is always bounded. Only members whose licence is in the
owner's selected set are ever published, and a backup with no identifiable
licence stays private. Withholding or disabling blocks serving without deleting
anything.
"""

from __future__ import annotations

import contextlib
import http.client
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol, cast
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import (
    Field,
    JsonValue,
    ValidationError,
    field_validator,
    model_validator,
)

from ..archives import ArchiveManifest, verify_archive_manifest
from ..assurance import load_retained_card
from ..bucket import (
    PRESETS,
    BucketClient,
    BucketCredentials,
    BucketTarget,
    check_bucket,
    delete_credentials,
    load_credentials,
    publish_holdings,
    publish_objects,
    publish_peers,
    publish_to_bucket,
    r2_jurisdiction,
    remove_from_bucket,
    save_credentials,
    target_for,
)
from ..cards import identity_json
from ..handoffs import Handoff
from ..invitations import create_invitation
from ..licences import (
    DEFAULT_LICENSES,
    LICENCE_DEFINITION_VERSION,
    UNIDENTIFIED,
    definition_json,
    identified_licences,
    normalize_license,
)
from ..mirror import model_action, run_mirror
from ..mirror_cards import (
    MAX_PEERS_BYTES,
    PEERS_PATH,
    PeersDocument,
    base_url,
    build_peers,
    issue_card,
    verify_card,
)
from ..mirror_directory import MirrorDirectory
from ..mirror_holdings import issue_holdings, store_holdings
from ..mirror_intake import retain_local
from ..mirror_models import (
    HeldModel,
    MirrorObservation,
    MirrorState,
    SourceGrant,
    Terms,
)
from ..mirror_native import configure as configure_native
from ..mirror_objects import issue_objects, served_hashes
from ..mirror_rendezvous import Rendezvous, rendezvous_available
from ..mirror_state import MirrorLedger, grant_restart
from ..models import (
    Digest,
    Manifest,
    ManifestMember,
    PreservationError,
    Record,
    TrustProfile,
)
from ..nostr import DEFAULT_RELAYS, find_cards, publish_card
from ..operations import OperationControl, OperationError, failure_code
from ..pirateface_seed import issue_torrents, seeded
from ..signing import (
    canonical_bytes,
    create_key,
    digest,
    encode_record,
    load_key,
    public_record,
)
from ..storage import atomic_bytes, directory, manifest_path, read_metadata
from ..transfer import MirrorHTTPError, TransferPolicy, response
from .library import Library
from .presentation import captured_card_metadata, card_summary
from .sharing import MemberDecision, Sharing

# The licence families live in one canonical, versioned definition
# (foxlight_model_preservation.licences). Permissive, attribution, copyleft and
# responsible-AI licences are on by default; makers' custom terms are off.
# Eligibility follows the licence itself, never whether the download was gated.
# The address check reaches public routes only, like any other visitor would.
ADDRESS_CHECK_POLICY = TransferPolicy(allow_http=True, timeout=10, deadline_seconds=30)
_CAPTURE_NOTICE = ".foxlight-capture-notice.txt"
_LICENSE_NAMES = ("license", "licence", "copying")
DEFAULT_PORT = 8743
CONSENT_SECONDS = 365 * 86400


class Holding(Protocol):
    """A saved copy as the owner records it; no path is ever supplied by a view."""

    @property
    def copy_id(self) -> str: ...
    @property
    def manifest_id(self) -> str: ...
    @property
    def name(self) -> str: ...
    @property
    def size_bytes(self) -> int: ...
    @property
    def trust(self) -> TrustProfile: ...


__all__ = [
    "CONSENT_SECONDS",
    "DEFAULT_LICENSES",
    "UNIDENTIFIED",
    "CommunityMirror",
    "CommunityPolicy",
    "normalize_license",
]


class CommunityPolicy(Record):
    """The owner's one standing choice; changing it never deletes retained bytes.

    Upload limits are opt-in: ``None`` means no daily or monthly cap (bytes are
    still counted for the status). Only the v2 policy can say "no cap"; an
    older app refuses a v2 file rather than misreading it. A v1 policy keeps
    whatever caps it already had.
    """

    schema_version: Literal[
        "sovereign.community-mirror.v1", "sovereign.community-mirror.v2"
    ] = "sovereign.community-mirror.v2"
    enabled: bool = False
    share: Literal["eligible", "chosen"] = "eligible"
    licenses: tuple[str, ...] = Field(default=DEFAULT_LICENSES, max_length=128)
    chosen: tuple[Digest, ...] = Field(default=(), max_length=256)
    withheld: tuple[Digest, ...] = Field(default=(), max_length=256)
    daily_upload_bytes: int | None = Field(default=None, ge=1, le=2**53 - 1)
    monthly_upload_bytes: int | None = Field(default=None, ge=1, le=2**53 - 1)
    port: int = Field(default=DEFAULT_PORT, ge=1024, le=65535)
    public_url: str | None = Field(default=None, min_length=8, max_length=2048)
    announce_dht: bool = True
    publish_nostr: bool = True
    nostr_relays: tuple[str, ...] = Field(default=DEFAULT_RELAYS, max_length=16)
    consent_expires: int | None = None
    rarest_first: bool = False
    # Also seed each shared model's matching Pirate Face torrent from the
    # mirror's stored files (after looking it up on Pirate Face).
    seed_pirate_face: bool = False

    @field_validator("public_url")
    @classmethod
    def plain_url(cls, value: str | None) -> str | None:
        """Accept only a credential-free HTTP(S) base URL the owner says works."""
        if value is None:
            return None
        try:
            value = base_url(value)
        except PreservationError:
            raise PreservationError("community_address_invalid") from None
        if _url_port(value) is None:
            raise PreservationError("community_address_port_invalid")
        return value

    @field_validator("nostr_relays")
    @classmethod
    def secure_relays(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Accept only credential-free wss:// relay URLs, once each."""
        for value in values:
            parsed = urlsplit(value)
            if (
                parsed.scheme != "wss"
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
                or len(value) > 512
            ):
                raise PreservationError("community_relay_invalid")
        return tuple(dict.fromkeys(values))

    @field_validator("licenses")
    @classmethod
    def identified(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Keep normalized, identified licences only, once each."""
        return identified_licences(values)

    @model_validator(mode="after")
    def upload_limits(self) -> CommunityPolicy:
        """Only v2 says "no cap".

        A saved policy always loads as earlier releases accepted it: a daily
        limit above the monthly one stays readable (the monthly limit binds).
        Only a change to the limits themselves must keep daily within monthly.
        """
        uncapped = self.daily_upload_bytes is None or self.monthly_upload_bytes is None
        if uncapped and self.schema_version == "sovereign.community-mirror.v1":
            raise PreservationError("community_policy_version")
        return self


def _url_port(value: str) -> int | None:
    """The address's TCP port, or None when it names no usable port."""
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        return 443 if parsed.scheme == "https" else 80
    return port if 1 <= port <= 65535 else None


def _bucket_failure(error: BaseException) -> str:
    """A bucket failure the app can explain, never exception text or a URL."""
    code = str(error)
    if isinstance(error, PreservationError) and re.fullmatch(
        r"bucket_[a-z_]{1,60}", code
    ):
        return code
    return failure_code(error)


def _reserve(ledger: MirrorLedger, count: int) -> bool:
    """Charge bucket uploads to the mirror's limits, waiting out brief contention.

    Serving charges the same ledger and holds its lock only for one small write,
    so a busy lock means "a moment", not "over the limit".
    """
    for _attempt in range(50):
        try:
            return ledger.reserve(count)
        except PreservationError as error:
            if str(error) != "another writer owns this destination":
                raise
            time.sleep(0.1)
    return ledger.reserve(count)


class BucketState(Record):
    """A connected cloud bucket; it names a keychain entry and holds no secret.

    Version 2 adds ``synced_at``. A state without it is still written as
    version 1, byte for byte as before, so older apps keep reading it; one
    with it is written as version 2, which older apps refuse rather than
    misread (see ``encode_bucket``).
    """

    schema_version: Literal[
        "sovereign.community-bucket.v1", "sovereign.community-bucket.v2"
    ] = "sovereign.community-bucket.v1"
    target: BucketTarget
    synced: tuple[Digest, ...] = Field(default=(), max_length=1000)
    # Uploads that started but were not confirmed. Their manifest may be
    # public (a lost reply, a crash), so they are withdrawn like synced ones.
    pending: tuple[Digest, ...] = Field(default=(), max_length=1000)
    last_error: str | None = Field(default=None, max_length=200)
    last_synced: int | None = None
    # When each synced model's upload was confirmed (version 2 only).
    synced_at: dict[Digest, int] = Field(default_factory=dict, max_length=1000)

    @model_validator(mode="after")
    def versioned(self) -> BucketState:
        """Version 1 has no upload times; times belong only to synced models."""
        if self.synced_at and self.schema_version == "sovereign.community-bucket.v1":
            raise PreservationError("bucket_state_invalid")
        if not set(self.synced_at) <= set(self.synced) or any(
            value < 0 for value in self.synced_at.values()
        ):
            raise PreservationError("bucket_state_invalid")
        return self


def encode_bucket(state: BucketState) -> bytes:
    """Encode bucket state in the oldest version that can hold it."""
    value = state.model_dump(mode="json")
    if state.synced_at:
        value["schema_version"] = "sovereign.community-bucket.v2"
    else:
        value.pop("synced_at")
        value["schema_version"] = "sovereign.community-bucket.v1"
    return canonical_bytes(value)


# The version of the announcement disclosure (DHT and Nostr) the app shows before
# consent. A consent recorded without it announces nothing until reviewed.
ANNOUNCE_DISCLOSURE = 1


class AnnounceReview(Record):
    """Which announcement disclosure one standing consent was given under.

    Kept beside the policy rather than in it, so a policy written by this
    version still reads in older apps. A review covers exactly one consent:
    a new consent, or one renewed elsewhere, needs a new review.
    """

    schema_version: Literal["sovereign.community-announce-review.v1"] = (
        "sovereign.community-announce-review.v1"
    )
    disclosure: int = Field(ge=1)
    consent_expires: int = Field(ge=0)


class Joined(Record):
    """Which public identity each saved copy contributes to the mirror."""

    schema_version: Literal["sovereign.community-joined.v1"] = (
        "sovereign.community-joined.v1"
    )
    copies: dict[Digest, Digest] = Field(default_factory=dict, max_length=256)


Status = Literal[
    "sharing",
    "eligible",
    "failed",
    "withheld",
    "not_chosen",
    "license_not_selected",
    "no_license",
    "unsupported",
]


def _card_license(member: ManifestMember) -> str:
    if member.retained_card is None:
        return ""
    try:
        retained = load_retained_card(identity_json(member.retained_card))
    except (ValueError, PreservationError):
        return ""
    metadata = captured_card_metadata(retained.registry_proof, retained.envelope)
    value = card_summary(retained.envelope, metadata=metadata).get("license")
    return value if isinstance(value, str) else ""


def member_decision(member: ManifestMember) -> MemberDecision | None:
    """Derive one data member's licence and terms reference, or None if unknown.

    A licence declared at capture wins; otherwise the retained original model
    card's licence is used. The terms reference names the member's own licence
    file when it has one, so the published notice points at real bytes.
    """
    license_id = member.intake.license_id
    if normalize_license(license_id) in UNIDENTIFIED:
        license_id = _card_license(member)
    if normalize_license(license_id) in UNIDENTIFIED:
        return None
    reference = next(
        (
            file.path
            for file in member.files
            if PurePosixPath(file.path).name.casefold().startswith(_LICENSE_NAMES)
        ),
        None,
    )
    if reference is None:
        reference = member.intake.terms_reference
        if reference in {_CAPTURE_NOTICE, "see-original-card", ""}:
            reference = (
                f"Model card: {member.origin_repository}@{member.origin_revision}"
            )
    return MemberDecision(
        member_id=member.member_id,
        license_id=license_id.strip(),
        terms_reference=reference,
    )


def _data_members(manifest: Manifest) -> tuple[ManifestMember, ...]:
    return tuple(
        member
        for member in manifest.members
        if not (member.role == "notices" and member.member_id == "source-evidence")
    )


class CommunityMirror:
    """Own the app's single long-lived public mirror and its standing choice."""

    # Requests to a connected bucket's endpoint and public address.
    bucket_policy = TransferPolicy(timeout=300, deadline_seconds=86400)

    def __init__(self, home: Path, sharing: Sharing) -> None:
        self.home = home
        self.sharing = sharing
        # Settings and the mirror key stay in the home; the served pool sits in
        # the library folder beside the copies its objects are linked from.
        self.library = getattr(sharing, "library", None) or Library(home)
        self.path = home / "sharing/community"
        self.observation: MirrorObservation | None = None
        self.last_error: str | None = None
        self.failures: dict[str, str] = {}
        self.directory = MirrorDirectory(home / "mirror-directory")
        self.rendezvous: Rendezvous | None = None
        # Connecting, disconnecting and sync progress replace bucket.json; this
        # keeps a long sync from writing over a bucket connected meanwhile.
        self._bucket_lock = threading.Lock()
        # One reconciliation at a time: discovery rounds and withdrawal retries.
        self._bucket_sync_lock = threading.Lock()
        self._key_lock = threading.Lock()
        # Gossip rounds (and the rendezvous they own) never overlap.
        self._gossip_lock = threading.Lock()

    # Policy and membership -------------------------------------------------

    def policy(self) -> CommunityPolicy:
        """Read the standing choice; a missing file means the switch is off."""
        return self._load_policy()[0]

    def _load_policy(self) -> tuple[CommunityPolicy, bool]:
        """Read the policy, and whether its stored public address is unusable.

        Earlier versions accepted a public address whose port cannot be used
        (such as ``:abc`` or ``:99999``). Such an address is set aside rather
        than breaking every read: the mirror keeps its other choices and the
        status says the address needs setting again.
        """
        try:
            with directory(self.path) as root:
                data = read_metadata(root, "policy.json")
        except FileNotFoundError:
            return CommunityPolicy(), False
        try:
            return CommunityPolicy.model_validate_json(data), False
        except ValidationError:
            stored: JsonValue = json.loads(data)
            if not isinstance(stored, dict) or stored.get("public_url") is None:
                raise
            without = {**stored, "public_url": None}
            # Anything else wrong with the policy still refuses as before.
            return CommunityPolicy.model_validate_json(identity_json(without)), True

    def _save(self, policy: CommunityPolicy) -> None:
        with directory(self.path, create=True) as root:
            atomic_bytes(root, "policy.json", encode_record(policy))

    def joined(self) -> Joined:
        """Read which saved copies already contribute which public identity."""
        try:
            with directory(self.path) as root:
                return Joined.model_validate_json(read_metadata(root, "joined.json"))
        except FileNotFoundError:
            return Joined()

    def _record_joined(self, copy_id: str, manifest_id: str) -> None:
        joined = self.joined()
        with directory(self.path, create=True) as root:
            atomic_bytes(
                root,
                "joined.json",
                encode_record(
                    joined.model_copy(
                        update={"copies": {**joined.copies, copy_id: manifest_id}}
                    )
                ),
            )

    def consented(self, policy: CommunityPolicy | None = None) -> bool:
        """Whether the standing consent covers serving right now."""
        selected = policy or self.policy()
        return bool(
            selected.enabled
            and selected.consent_expires is not None
            and selected.consent_expires > int(time.time())
        )

    def announce_reviewed(self, policy: CommunityPolicy | None = None) -> bool:
        """Whether the current consent was given with the announcement disclosure."""
        selected = policy or self.policy()
        try:
            with directory(self.path) as root:
                review = AnnounceReview.model_validate_json(
                    read_metadata(root, "announce-review.json")
                )
        except (FileNotFoundError, ValueError):
            return False
        return (
            review.disclosure >= ANNOUNCE_DISCLOSURE
            and review.consent_expires == selected.consent_expires
        )

    def announcing(self) -> tuple[bool, bool]:
        """The DHT and Nostr choices in force: both off until reviewed."""
        policy = self.policy()
        if not self.announce_reviewed(policy):
            return False, False
        return policy.announce_dht, policy.publish_nostr

    def configure(self, body: dict[str, JsonValue]) -> CommunityPolicy:
        """Apply a bounded change to the standing choice.

        Turning the switch on records one standing consent for a year; turning it
        off revokes it at once. Licences can be widened or narrowed; identifiers
        that name no licence are refused.
        """
        allowed = {
            "enabled",
            "consent",
            "share",
            "licenses",
            "daily_upload_bytes",
            "monthly_upload_bytes",
            "port",
            "public_url",
            "announce_dht",
            "publish_nostr",
            "nostr_relays",
            "rarest_first",
            "announce_disclosure",
            "seed_pirate_face",
        }
        if not body or not set(body) <= allowed:
            raise PreservationError("invalid_control")
        disclosed = "announce_disclosure" in body
        if disclosed and body["announce_disclosure"] != ANNOUNCE_DISCLOSURE:
            raise PreservationError("invalid_control")
        policy = self.policy()
        changes: dict[str, JsonValue] = {
            key: value
            for key, value in body.items()
            if key not in {"consent", "announce_disclosure"}
        }
        # Any change this version saves is written as v2, which older apps
        # refuse instead of misreading; stored caps stay exactly as they were.
        changes["schema_version"] = "sovereign.community-mirror.v2"
        if {"daily_upload_bytes", "monthly_upload_bytes"} & set(body):
            # Only limits the owner sets now must keep daily within monthly;
            # limits saved earlier are kept exactly, whatever they were.
            daily = changes.get("daily_upload_bytes", policy.daily_upload_bytes)
            monthly = changes.get("monthly_upload_bytes", policy.monthly_upload_bytes)
            if (
                isinstance(daily, int)
                and isinstance(monthly, int)
                and not isinstance(daily, bool)
                and not isinstance(monthly, bool)
                and daily > monthly
            ):
                raise PreservationError("community_upload_limit_invalid")
        if body.get("enabled") is True:
            if body.get("consent") is not True:
                raise PreservationError("community_consent_required")
            changes["consent_expires"] = int(time.time()) + CONSENT_SECONDS
        elif body.get("enabled") is False:
            changes["consent_expires"] = None
        elif "consent" in body:
            raise PreservationError("invalid_control")
        try:
            updated = CommunityPolicy.model_validate_json(
                identity_json({**policy.model_dump(mode="json"), **changes})
            )
        except ValidationError as error:
            # Surface this module's own refusal codes; anything else is invalid.
            for detail in error.errors():
                cause = detail.get("ctx", {}).get("error")
                if isinstance(cause, PreservationError):
                    raise PreservationError(str(cause)) from None
            raise PreservationError("invalid_control") from None
        if "enabled" in body and not disclosed:
            # A consent recorded or revoked without the disclosure leaves no
            # earlier review behind, even within the same second.
            try:
                with directory(self.path) as root:
                    os.unlink("announce-review.json", dir_fd=root)
            except FileNotFoundError:
                pass
        self._save(updated)
        # Saved after the policy: if this write fails, announcing stays off.
        if disclosed and self.consented(updated) and updated.consent_expires:
            with directory(self.path, create=True) as root:
                atomic_bytes(
                    root,
                    "announce-review.json",
                    encode_record(
                        AnnounceReview(
                            disclosure=ANNOUNCE_DISCLOSURE,
                            consent_expires=updated.consent_expires,
                        )
                    ),
                )
        if not updated.enabled:
            # The saved opt-out already stops new serving; the owner cancels the
            # running controller regardless. A grant that cannot be cleared now
            # (for example during an upload reservation) is cleared after drain.
            self.revoke_grant()
        return updated

    def choose(self, copy_id: str, *, chosen: bool, withheld: bool) -> None:
        """Include or keep back one saved copy without affecting any other."""
        policy = self.policy()
        include = tuple(item for item in policy.chosen if item != copy_id)
        keep = tuple(item for item in policy.withheld if item != copy_id)
        self._save(
            policy.model_copy(
                update={
                    "chosen": (*include, copy_id) if chosen else include,
                    "withheld": (*keep, copy_id) if withheld else keep,
                }
            )
        )

    def forget(self, copy_id: str) -> None:
        """Drop a removed copy from the sharing choices and its joined record.

        Its public identity stops being served at the next sync unless another
        saved copy still provides it; the pool itself is never edited here.
        """
        self.choose(copy_id, chosen=False, withheld=False)
        joined = self.joined()
        if copy_id in joined.copies:
            remaining = {
                key: value for key, value in joined.copies.items() if key != copy_id
            }
            with directory(self.path, create=True) as root:
                atomic_bytes(
                    root,
                    "joined.json",
                    encode_record(joined.model_copy(update={"copies": remaining})),
                )

    # Eligibility -------------------------------------------------------------

    def candidate(self, copy: Holding) -> dict[str, JsonValue]:
        """Describe one saved copy's standing under the current choice."""
        policy = self.policy()
        joined = self.joined().copies
        status: Status
        licenses: list[JsonValue] = []
        decisions: list[JsonValue] | None = None
        try:
            manifest = self.sharing.origin_manifest(
                self.library.find("copies/" + copy.copy_id),
                copy.manifest_id,
                copy.trust,
            )
            members = _data_members(manifest)
            already_public = all(
                member.intake.audience == "public" for member in manifest.members
            )
            found = [member_decision(member) for member in members]
            licenses = [item.license_id for item in found if item is not None]
            if any(item is None for item in found):
                status = "no_license"
            elif not all(
                normalize_license(item.license_id) in policy.licenses
                for item in found
                if item is not None
            ):
                status = "license_not_selected"
            else:
                status = "eligible"
                decisions = (
                    []
                    if already_public
                    else [item.model_dump(mode="json") for item in found if item]
                )
        except (ValueError, OSError, PreservationError):
            status = "unsupported"
        if status == "eligible":
            if copy.copy_id in policy.withheld:
                status = "withheld"
            elif policy.share == "chosen" and copy.copy_id not in policy.chosen:
                status = "not_chosen"
            elif copy.copy_id in joined:
                status = "sharing"
            elif copy.copy_id in self.failures:
                status = "failed"
        return {
            "copy_id": copy.copy_id,
            "name": copy.name,
            "size_bytes": copy.size_bytes,
            "status": status,
            "licenses": licenses,
            "public_manifest_id": joined.get(copy.copy_id),
            "error_code": self.failures.get(copy.copy_id)
            if status == "failed"
            else None,
            "decisions": cast(JsonValue, decisions),
        }

    # Mirror ------------------------------------------------------------------

    def pool_available(self) -> bool:
        """False while the pool's library drive is unplugged (not merely empty)."""
        if self.library.available():
            return True
        return any(
            (root / "sharing/community/mirror").is_dir()
            for root in self.library.roots()
        )

    def _mirror(self) -> Path:
        try:
            return self.library.find("sharing/community/mirror")
        except PreservationError:
            # The library drive is unplugged: reads see no pool. Creating one
            # (ledger) still refuses rather than using the system drive.
            return self.path / "mirror"

    def ledger(self) -> MirrorLedger:
        """Open the one community mirror, creating it the first time it is used."""
        policy = self.policy()
        path = self._mirror()
        if not path.exists():
            path = self.library.private_root() / "sharing/community/mirror"
            with directory(path.parent, create=True):
                pass
            MirrorLedger.initialize(
                path,
                name="Sovereign community mirror",
                # Linked objects cost no new space; the logical budget is the
                # whole volume and real free space is still checked per model.
                disk_bytes=min(
                    max(shutil.disk_usage(path.parent).total, 256 * 1024**2),
                    2**53 - 1,
                ),
                ingress_daily=1,
                egress_daily=policy.daily_upload_bytes,
                egress_monthly=policy.monthly_upload_bytes,
                bind="0.0.0.0",  # noqa: S104 - served only under standing consent
                port=policy.port,
            )
        ledger = MirrorLedger(path)
        state = ledger.read()
        if (state.egress_daily, state.egress_monthly, state.port) != (
            policy.daily_upload_bytes,
            policy.monthly_upload_bytes,
            policy.port,
        ):
            ledger.update(
                lambda current: current.model_copy(
                    update={
                        "egress_daily": policy.daily_upload_bytes,
                        "egress_monthly": policy.monthly_upload_bytes,
                        "port": policy.port,
                        "restart": None,
                    }
                )
            )
        return ledger

    def sync(self, copies: Sequence[Holding], control: OperationControl) -> int:
        """Bring the mirror in line with the standing choice; return models joined.

        Eligible copies join as signed public network PMF, sharing their bytes
        with the private copy. Copies that are withheld or no longer eligible are
        blocked from serving; nothing is ever deleted here.
        """
        ledger = self.ledger()
        added = 0
        wanted: set[str] = set()
        # A pool created while an earlier library drive was unplugged lacks the
        # selections recorded for the old one: those copies join it again.
        present = set(ledger.read().models)
        for copy in copies:
            control.checkpoint()
            view = self.candidate(copy)
            if view["status"] not in {"sharing", "eligible", "failed"}:
                continue
            identity = self.joined().copies.get(copy.copy_id)
            if identity is None or identity not in present:
                try:
                    self._join(ledger, copy, view, control)
                except OperationError:
                    raise
                except (ValueError, OSError) as error:
                    # One damaged or unreadable backup must not take healthy
                    # models offline: record it and keep reconciling the rest.
                    self.failures[copy.copy_id] = failure_code(error)
                    continue
                self.failures.pop(copy.copy_id, None)
                identity = self.joined().copies[copy.copy_id]
                added += 1
            wanted.add(identity)
        # One public identity can come from several copies (for example your own
        # backup and a received copy of the same model): serve it while any of
        # them is eligible, and block it only when none is.
        for identity, held in ledger.read().models.items():
            if identity in wanted and held.error:
                # A transient verification error must not end serving for good:
                # retry it, and the controller re-verifies every byte first.
                model_action(ledger, identity, "retry")
            if identity in wanted and held.blocked:
                model_action(ledger, identity, "unblock")
            elif identity not in wanted and not held.blocked:
                model_action(ledger, identity, "block")
        return added

    def _join(
        self,
        ledger: MirrorLedger,
        copy: Holding,
        view: dict[str, JsonValue],
        control: OperationControl,
    ) -> None:
        store = self.library.find("copies/" + copy.copy_id)
        plan = self.sharing.plan(store, copy.manifest_id, copy.trust, view["decisions"])
        self.sharing.require_space(store, cast(int, plan["size_bytes"]))
        job_id = digest(("community:" + copy.copy_id).encode())
        self.sharing.remove_work(job_id)
        public_store, public_trust = self.sharing.prepare_public(
            job_id, store, copy.manifest_id, copy.trust, plan, control.fork()
        )
        public = Manifest.model_validate_json(identity_json(plan["public_manifest"]))
        identity = str(plan["public_manifest_id"])
        held = ledger.read().models.get(identity)
        if held is None or held.state != "retained" or held.error:
            # A first join, or intake that was interrupted (a pending selection,
            # possibly with an error): explicitly retry before claiming it.
            if held is not None and held.error:
                model_action(ledger, identity, "retry")
            retain_local(
                ledger,
                public_store,
                identity,
                SourceGrant(
                    trust=public_trust,
                    terms=tuple(
                        Terms(license_id=license_id, terms_reference=reference)
                        for license_id, reference in sorted(
                            {
                                (m.intake.license_id, m.intake.terms_reference)
                                for m in public.members
                            }
                        )
                    ),
                    max_bytes=public.total_bytes + 8 * 1024**2,
                ),
                control=control.fork(),
                confirm_reintake=held is not None and held.state == "removed",
                link_objects=True,
            )
        held = ledger.read().models.get(identity)
        if held is None or held.state != "retained" or held.error:
            raise PreservationError("community_join_incomplete")
        self._record_joined(copy.copy_id, identity)
        try:
            self.sharing.remove_work(job_id)
        except OSError:
            pass  # Scratch is owner-private and removed on the next join.

    def serve(
        self,
        control: OperationControl,
        observe: Callable[[MirrorObservation], None] | None = None,
    ) -> None:
        """Serve every joined model until stopped, under the standing consent."""
        policy = self.policy()
        if not self.consented(policy) or policy.consent_expires is None:
            raise PreservationError("community_consent_required")
        ledger = self.ledger()
        # A pending selection is complete but awaits re-verification; the
        # controller verifies every byte and promotes it before serving it.
        if not any(
            held.state in {"retained", "pending"}
            and not held.blocked
            and not held.error
            for held in ledger.read().models.values()
        ):
            raise PreservationError("community_nothing_to_share")
        self._apply_pirate_face(ledger, policy)
        grant_restart(ledger, policy.consent_expires)

        def record(value: MirrorObservation) -> None:
            self.observation = value
            if observe is not None:
                observe(value)

        run_mirror(ledger, resume=True, control=control, observe=record)

    def _apply_pirate_face(self, ledger: MirrorLedger, policy: CommunityPolicy) -> bool:
        """Match the mirror's Pirate Face seeding to the choice; True if changed."""
        native = ledger.read().native_policy
        current = bool(native and native.pirate_face)
        wanted = (
            policy.seed_pirate_face
            and self.consented(policy)
            and rendezvous_available()
        )
        if current == wanted or (native is None and not wanted):
            return False
        configure_native(ledger, pirate_face=wanted, max_active=4)
        return True

    def pirate_face_status(self, state: MirrorState) -> dict[str, JsonValue]:
        """How many shared models were found on Pirate Face, and are seeded."""
        shared = {
            identity
            for identity, held in state.models.items()
            if held.state == "retained" and not held.blocked and not held.error
        }
        records = [
            record
            for identity, record in state.pirate_face.items()
            if identity in shared
        ]
        native = state.native_policy
        return {
            "available": rendezvous_available(),
            "on": bool(native and native.pirate_face),
            "checked": len(records),
            "listed": sum(1 for record in records if record.listed),
            "matched": sum(1 for record in records if record.objects),
            "seeding": sum(
                1
                for record in records
                if record.objects and record.verified and record.error is None
            ),
            "differs": sum(
                1 for record in records if record.error == "pirate_face_copy_differs"
            ),
        }

    def revoke_grant(self) -> bool:
        """Clear the mirror's standing restart grant; report whether it is gone."""
        try:
            MirrorLedger(self._mirror()).update(
                lambda current: current.model_copy(update={"restart": None})
            )
        except FileNotFoundError:
            return True  # No mirror yet, so there is no standing grant to revoke.
        except (OSError, ValueError):
            return False
        return True

    # Discovery ---------------------------------------------------------------

    def _mirror_key(self) -> Ed25519PrivateKey:
        # Serving and discovery announce from different threads; without the
        # lock one could read the key while the other is still writing it.
        with self._key_lock:
            path = self.path / "mirror-key.pem"
            if not path.exists():
                with directory(self.path, create=True):
                    pass
                create_key(path, None)
            return load_key(path, None)

    def addresses(self) -> tuple[str, ...]:
        """Addresses this computer itself can be reached at, if any.

        A declared public URL, or this mirror's address when the computer has a
        public address. A computer behind a router has none; a connected bucket
        is announced separately, so it can still be found.
        """
        policy = self.policy()
        if policy.public_url is not None:
            return (policy.public_url,)
        reach = self.reachability()
        if reach["status"] == "public_address" and isinstance(reach["mirror_url"], str):
            return (reach["mirror_url"],)
        return ()

    # Cloud bucket ------------------------------------------------------------

    def bucket(self) -> BucketState | None:
        """The connected bucket, if the owner connected one."""
        try:
            with directory(self.path) as root:
                return BucketState.model_validate_json(
                    read_metadata(root, "bucket.json")
                )
        except FileNotFoundError:
            return None

    def _save_bucket(self, state: BucketState, *, replacing: bool = False) -> bool:
        """Save bucket state; a sync's update is dropped if the bucket changed."""
        with self._bucket_lock:
            current = self.bucket()
            if not replacing and (
                current is None or current.target.key_name != state.target.key_name
            ):
                return False
            with directory(self.path, create=True) as root:
                atomic_bytes(root, "bucket.json", encode_bucket(state))
            return True

    def connect_bucket(self, body: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Check a bucket end to end, and only then remember it.

        The keys are tried in memory first: an upload, an anonymous download
        from the public address, and cleanup. Only a bucket that passes is saved,
        with its keys in the operating system keychain and nowhere else.
        """
        allowed = {
            "provider",
            "bucket",
            "account_id",
            "region",
            "endpoint",
            "public_url",
            "jurisdiction",
            "access_key",
            "secret_key",
        }
        if not set(body) <= allowed or not {
            "provider",
            "bucket",
            "access_key",
            "secret_key",
        } <= set(body):
            raise PreservationError("invalid_control")
        text = {
            key: value.strip()
            for key, value in body.items()
            if isinstance(value, str) and value.strip()
        }
        if text.get("provider") not in PRESETS:
            raise PreservationError("invalid_control")
        try:
            credentials = BucketCredentials(
                text.get("access_key", ""), text.get("secret_key", "")
            )
            target = target_for(
                text["provider"],  # type: ignore[arg-type]
                bucket=text.get("bucket", ""),
                key_name="community-" + secrets.token_hex(8),
                account_id=text.get("account_id"),
                region=text.get("region"),
                endpoint=text.get("endpoint"),
                public_url=text.get("public_url"),
                jurisdiction=text.get("jurisdiction"),
            )
        except ValueError as error:
            code = str(error)
            raise PreservationError(
                code if code.startswith("bucket_") else "bucket_details_invalid"
            ) from None
        # The owner is waiting on this check, so it is bounded tightly.
        check = replace(self.bucket_policy, timeout=15, deadline_seconds=45)
        result = check_bucket(BucketClient(target, credentials, check), check)
        if result["status"] != "ready":
            return {"status": result["status"]}
        save_credentials(target.key_name, credentials)
        with self._bucket_lock:
            previous = self.bucket()
            with directory(self.path, create=True) as root:
                atomic_bytes(
                    root, "bucket.json", encode_bucket(BucketState(target=target))
                )
        if previous is not None:
            delete_credentials(previous.target.key_name)
        return {"status": "ready", "public_url": target.public_url}

    # One contents read returns at most this many models, and its reply (as the
    # app's control server encodes it) stays within this many bytes, well under
    # the 64 KiB control limit whatever the names are.
    BUCKET_PAGE = 50
    BUCKET_PAGE_BYTES = 48 * 1024

    def _bucket_held(self) -> dict[str, HeldModel]:
        try:
            return dict(MirrorLedger(self._mirror()).read().models)
        except FileNotFoundError:
            return {}

    def bucket_contents(self, offset: int = 0) -> dict[str, JsonValue]:
        """List the models the bucket serves, from this app's own records.

        Nothing is listed remotely: the bucket holds exactly the manifests this
        app confirmed uploading (``synced``), and each one's name and size come
        from the verified copy this mirror retains. Pages are bounded by count
        and by encoded size; ``next_offset`` continues the list, or is None at
        its end. Signing keys are read per model (``bucket_model_keys``).
        """
        state = self.bucket()
        if state is None:
            return {
                "models": [],
                "total": 0,
                "offset": 0,
                "next_offset": None,
                "public_url": None,
            }
        held = self._bucket_held()
        page: dict[str, JsonValue] = {
            "models": [],
            "total": len(state.synced),
            "offset": offset,
            "next_offset": None,
            "public_url": state.target.public_url,
        }
        rows: list[JsonValue] = []
        position = offset
        for identity in state.synced[offset : offset + self.BUCKET_PAGE]:
            row = self._bucket_row(identity, held.get(identity), state)
            candidate = {
                **page,
                "models": [*rows, row],
                "next_offset": len(state.synced),
            }
            # Measured as the control server sends it (ASCII-escaped JSON).
            size = len(json.dumps(candidate, separators=(",", ":")))
            if rows and size > self.BUCKET_PAGE_BYTES:
                break
            rows.append(row)
            position += 1
        page["models"] = rows
        page["next_offset"] = position if position < len(state.synced) else None
        return page

    def _bucket_row(
        self, identity: str, held: HeldModel | None, state: BucketState
    ) -> dict[str, JsonValue]:
        name: str | None = None
        size: int | None = None
        if held is not None:
            try:
                manifest = self._bucket_manifest(identity, held.grant.trust)
                name, size = manifest.name, manifest.total_bytes
            except (ValueError, OSError):
                pass  # Shown by its identity alone; the bucket copy is intact.
        return {
            "manifest_id": identity,
            "name": name,
            "size_bytes": size,
            "synced_at": state.synced_at.get(identity),
        }

    def _bucket_manifest(self, identity: str, trust: TrustProfile) -> ArchiveManifest:
        """Verify the retained signed manifest of one model the bucket serves."""
        with directory(self._mirror() / "store") as root:
            return verify_archive_manifest(
                read_metadata(root, manifest_path(identity)), trust, identity
            )

    def bucket_model_keys(self, manifest_id: str) -> dict[str, JsonValue]:
        """The public signing keys of one model the bucket serves.

        These are the keys of the trust profile its handoff carries, for the
        owner to confirm with recipients through a separate channel.
        """
        if re.fullmatch(r"[0-9a-f]{64}", manifest_id) is None:
            raise PreservationError("invalid_control")
        state = self.bucket()
        held = self._bucket_held().get(manifest_id)
        if state is None or manifest_id not in state.synced or held is None:
            raise PreservationError("bucket_model_unavailable")
        trust = held.grant.trust
        return {
            "manifest_id": manifest_id,
            "keys": [
                key.key_id for key in trust.keys if key.key_id not in trust.revoked_keys
            ],
            "threshold": trust.threshold,
        }

    def bucket_handoff(self, manifest_id: str) -> bytes:
        """A one-file handoff for one model the bucket serves.

        It is the ordinary ``.foxhandoff`` (an unchanged signed invitation)
        with the bucket's public address as its only mirror: the signed
        manifest, its exact identity and the public trust profile of the key
        that signed it. Recipients inspect it offline and authenticate the key
        fingerprint independently. It holds no bucket key and no local path.
        """
        if re.fullmatch(r"[0-9a-f]{64}", manifest_id) is None:
            raise PreservationError("invalid_control")
        state = self.bucket()
        if state is None or manifest_id not in state.synced:
            raise PreservationError("bucket_model_unavailable")
        held = self._bucket_held().get(manifest_id)
        if held is None:
            raise PreservationError("bucket_model_unavailable")
        try:
            invitation = create_invitation(
                self._mirror() / "store",
                manifest_id,
                held.grant.trust,
                (state.target.public_url,),
            )
            return encode_record(Handoff(reference=invitation))
        except (ValueError, OSError):
            raise PreservationError("bucket_handoff_unavailable") from None

    def disconnect_bucket(self) -> None:
        """Stop using the bucket. Its contents are left exactly as they are."""
        with self._bucket_lock:
            state = self.bucket()
            if state is None:
                return
            with directory(self.path) as root:
                os.unlink("bucket.json", dir_fd=root)
        delete_credentials(state.target.key_name)

    def sync_bucket(self, control: OperationControl) -> BucketState | None:
        """Keep the bucket matching what this mirror serves.

        Unwanted models are withdrawn first, by removing only their manifest, so
        a failing upload can never keep a withheld model public. Turning sharing
        off withdraws them all. Each served model is then uploaded (only missing
        objects travel, manifest last), counted against the same daily and
        monthly upload limits as serving when the owner set them. One model's
        failure does not stop the others; failures are recorded and retried
        later.
        """
        with self._bucket_sync_lock:
            return self._sync_bucket(control)

    def _sync_bucket(self, control: OperationControl) -> BucketState | None:
        found = self.bucket()
        if found is None:
            return None
        state: BucketState = found
        synced = list(state.synced)
        pending = [item for item in state.pending if item not in synced]
        synced_at = dict(state.synced_at)
        failure: str | None = None

        def save() -> bool:
            nonlocal state
            times = {key: value for key, value in synced_at.items() if key in synced}
            state = BucketState.model_validate(
                {
                    **dict(state),
                    "schema_version": "sovereign.community-bucket.v2"
                    if times
                    else "sovereign.community-bucket.v1",
                    "synced": tuple(synced),
                    "pending": tuple(pending),
                    "last_error": failure,
                    "synced_at": times,
                }
            )
            return self._save_bucket(state)

        try:
            client = BucketClient(
                state.target,
                load_credentials(state.target.key_name),
                self.bucket_policy,
            )
            if self.consented() and not self.pool_available():
                # Sharing is on but the pool's drive is unplugged: nothing was
                # withdrawn, so leave the bucket as it is until it is back.
                # Turning sharing off still withdraws everything below.
                return state
            try:
                ledger = MirrorLedger(self._mirror())
                models = ledger.read().models
            except FileNotFoundError:
                # Never shared yet: there is nothing to upload, only to withdraw.
                ledger, models = None, {}
            held = self.held() if self.consented() else frozenset[str]()
            for identity in [item for item in synced + pending if item not in held]:
                control.checkpoint()
                try:
                    remove_from_bucket(client, identity)
                except OperationError:
                    raise
                except (ValueError, OSError) as error:
                    # Kept for the next attempt; the others are still withdrawn.
                    failure = _bucket_failure(error)
                    continue
                for listed in (synced, pending):
                    if identity in listed:
                        listed.remove(identity)
                if not save():
                    return self.bucket()
            for identity in sorted(held - set(synced)):
                if ledger is None or identity not in models:
                    continue
                control.checkpoint()
                if identity not in pending:
                    # Recorded before anything is sent, so an opt-out withdraws
                    # it even if this upload's outcome is never learned.
                    pending.append(identity)
                    if not save():
                        return self.bucket()
                try:
                    publish_to_bucket(
                        self._mirror() / "store",
                        identity,
                        models[identity].grant.trust,
                        client,
                        control=control.fork(),
                        reserve=lambda count: _reserve(ledger, count),
                    )
                except OperationError:
                    raise
                except (ValueError, OSError) as error:
                    failure = _bucket_failure(error)
                    if str(error) == "bucket_upload_limit_reached":
                        break  # The allowance renews; try again then.
                    continue  # Other models may still upload.
                synced.append(identity)
                synced_at[identity] = int(time.time())
                pending.remove(identity)
                if not save():
                    return self.bucket()
        except OperationError:
            _ = save()
            raise
        except (ValueError, OSError) as error:
            failure = _bucket_failure(error)
        else:
            state = state.model_copy(update={"last_synced": int(time.time())})
        return state if save() else self.bucket()

    def held(self) -> frozenset[str]:
        """Public identities this mirror retains and serves right now."""
        try:
            models = MirrorLedger(self._mirror()).read().models
        except FileNotFoundError:
            return frozenset()
        return frozenset(
            identity
            for identity, held in models.items()
            if held.state == "retained" and not held.blocked and not held.error
        )

    def announce(self) -> bytes | None:
        """Publish this mirror's signed card and the peers it has itself checked.

        The document is served at /.well-known/sovereign/peers.json and read on
        every request, so updating it needs no restart.
        """
        held = self.held()
        addresses = self.addresses()
        bucket = self.bucket()
        if bucket is not None and bucket.synced:
            # The bucket is reachable from anywhere, even when this computer is
            # not. A card has one coverage filter, so without another address
            # it claims only what the bucket already holds.
            addresses = (bucket.target.public_url, *addresses)
            if len(addresses) == 1:
                held = held & set(bucket.synced)
        card = (
            issue_card(self._mirror_key(), addresses, held)
            if addresses and held
            else None
        )
        document = build_peers(card, self.directory.gossip())
        # Object coverage lets catalog saves find files here by hash. It is
        # signed by the same mirror key and served beside the peers document.
        objects = holdings = torrents = None
        if card is not None:
            # Optional: a coverage problem must never stop the card renewing.
            with contextlib.suppress(PreservationError, OSError):
                objects = issue_objects(
                    self._mirror_key(), served_hashes(self._mirror() / "public")
                )
            # What this mirror keeps, by name, so others can find it in Find.
            # It lists exactly the copies the card covers.
            with contextlib.suppress(PreservationError, OSError):
                holdings = issue_holdings(
                    self._mirror_key(),
                    store_holdings(self._mirror() / "public", held),
                )
            # The Pirate Face torrents it seeds, when that is turned on.
            with contextlib.suppress(PreservationError, OSError, ValueError):
                torrents = issue_torrents(
                    self._mirror_key(),
                    seeded(MirrorLedger(self._mirror()).read(), held),
                )
        try:
            with directory(self._mirror()) as root:
                atomic_bytes(root, "peers.json", document)
                if objects is not None:
                    atomic_bytes(root, "objects.json", objects)
                if holdings is not None:
                    atomic_bytes(root, "holdings.json", holdings)
                if torrents is not None:
                    atomic_bytes(root, "torrents.json", torrents)
        except FileNotFoundError:
            pass  # No mirror yet; the next sync creates it and announces.
        # Only a document carrying this mirror's card is worth replacing the
        # bucket's copy with; a transient empty one would hide the bucket.
        if bucket is not None and bucket.synced and card is not None:
            try:
                client = BucketClient(
                    bucket.target,
                    load_credentials(bucket.target.key_name),
                    self.bucket_policy,
                )
                publish_peers(client, document)
                if objects is not None:
                    publish_objects(client, objects)
                if holdings is not None:
                    publish_holdings(client, holdings)
            except (ValueError, OSError):
                pass  # The next round publishes it again.
        return card

    def _nostr_key(self) -> bytes:
        with directory(self.path, create=True) as root:
            try:
                secret = read_metadata(root, "nostr-key")
            except FileNotFoundError:
                atomic_bytes(root, "nostr-key", secrets.token_bytes(32))
                secret = read_metadata(root, "nostr-key")
        if len(secret) != 32:
            raise PreservationError("community_nostr_key_invalid")
        return secret

    def gossip(self, control: OperationControl) -> None:
        """One round: announce, learn mirrors, confirm what they serve, re-publish.

        A reachable mirror joins the BitTorrent DHT rendezvous (when the optional
        binding is installed), announces its port and looks up other mirrors.
        Only plain public routes are crawled; private addresses never are.
        """
        with self._gossip_lock:
            self._gossip(control)

    def _gossip(self, control: OperationControl) -> None:
        policy = TransferPolicy(allow_http=True, timeout=10, deadline_seconds=600)
        relay_policy = TransferPolicy(timeout=10, deadline_seconds=600)
        seeds: tuple[str, ...] = ()
        port = self.dht_port()
        # Announcing an address with nothing to serve would only expose it.
        if port is not None and self.held() and rendezvous_available():
            if self.rendezvous is None:
                self.rendezvous = Rendezvous()
            self.rendezvous.announce(port, seconds=20, control=control)
            seeds = self.rendezvous.lookup(seconds=20, control=control)
        else:
            self._close_rendezvous()
        current = self.policy()
        relays = current.nostr_relays if self.announcing()[1] else ()
        if relays:
            # Cards found on relays are verified like any other; relays only
            # see this mirror's address, which its card publishes anyway.
            self.directory.ingest(find_cards(relays, relay_policy, control=control))
        self.directory.crawl(seeds, policy, control=control)
        self.directory.spot_check(self.held(), policy, control=control)
        self.sync_bucket(control)
        card = self.announce()
        if card is not None and relays:
            publish_card(card, self._nostr_key(), relays, relay_policy, control=control)

    def dht_port(self) -> int | None:
        """The public HTTP port to announce on the DHT, or None to stay off it.

        The DHT records only the address it sees plus a port, so an announcement
        must name the port people actually reach: a declared plain-HTTP public
        URL's own port (for a port forward), or this mirror's port when the
        computer has a public address. An HTTPS endpoint cannot be expressed
        that way, so it relies on cards, gossip and relays instead.
        """
        if not self.announcing()[0]:
            return None
        return self._reachable_port(self.policy())

    def _reachable_port(self, policy: CommunityPolicy) -> int | None:
        """The plain HTTP port people reach, whatever the announcement choice."""
        if policy.public_url is not None:
            parsed = urlsplit(policy.public_url)
            if parsed.scheme != "http" or parsed.path not in {"", "/"}:
                return None
            return _url_port(policy.public_url)
        return policy.port if self.addresses() else None

    def close_rendezvous(self) -> None:
        """Leave the DHT; the mirror no longer announces itself there."""
        with self._gossip_lock:
            self._close_rendezvous()

    def _close_rendezvous(self) -> None:
        if self.rendezvous is not None:
            self.rendezvous.close()
            self.rendezvous = None

    # Reachability ------------------------------------------------------------

    def reachability(self) -> dict[str, JsonValue]:
        """Say plainly whether other networks could reach this computer directly.

        Sovereign never configures the router (UPnP stays off) and asks no
        outside service. A computer with only private addresses is behind a
        router: people outside its network cannot reach it directly.
        """
        address: str | None = None
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
                # UDP connect selects a route without sending any packet.
                route.connect(("192.0.2.1", 9))
                address = str(route.getsockname()[0])
        except OSError:
            pass
        policy = self.policy()
        public = address is not None and ipaddress.ip_address(address).is_global
        return {
            "local_address": address,
            "port": policy.port,
            "status": "public_address"
            if public
            else "private_network"
            if address is not None
            else "offline",
            "mirror_url": f"http://{address}:{policy.port}" if address else None,
        }

    def announcements(self) -> dict[str, JsonValue]:
        """Say whether this mirror announces itself publicly, and if not, why.

        Mirrors the rules the discovery round applies: the DHT needs a plain
        HTTP port people can reach, something served and the optional binding;
        Nostr needs any reachable address (a synced bucket counts) and
        something served. Reasons are codes the app explains in sentences.
        """
        policy, address_invalid = self._load_policy()
        consented = self.consented(policy)
        available = rendezvous_available()
        held = bool(self.held())
        own = self.addresses()
        bucket = self.bucket()
        bucket_live = bucket is not None and bool(bucket.synced)
        port = self._reachable_port(policy)
        review = consented and not self.announce_reviewed(policy)
        dht_reasons: list[JsonValue] = []
        if review:
            dht_reasons.append("awaiting_review")
        elif not policy.announce_dht:
            dht_reasons.append("turned_off")
        else:
            if not consented:
                dht_reasons.append("not_sharing")
            if not available:
                dht_reasons.append("dht_unavailable")
            if not held:
                dht_reasons.append("nothing_shared")
            if address_invalid:
                dht_reasons.append("address_invalid")
            elif port is None:
                dht_reasons.append(
                    "address_not_plain_http"
                    if policy.public_url is not None
                    else "no_reachable_address"
                )
        nostr_reasons: list[JsonValue] = []
        if review:
            nostr_reasons.append("awaiting_review")
        elif not policy.publish_nostr:
            nostr_reasons.append("turned_off")
        elif not policy.nostr_relays:
            nostr_reasons.append("no_relays")
        else:
            if not consented:
                nostr_reasons.append("not_sharing")
            if not held:
                nostr_reasons.append("nothing_shared")
            if address_invalid:
                nostr_reasons.append("address_invalid")
            if not own and not bucket_live:
                nostr_reasons.append("no_reachable_address")
        return {
            "dht_available": available,
            "dht_announcing": not dht_reasons,
            "nostr_publishing": not nostr_reasons,
            "announce_reasons": {"dht": dht_reasons, "nostr": nostr_reasons},
            "announce_review_required": review,
            "address_invalid": address_invalid,
        }

    def check_address(self) -> dict[str, JsonValue]:
        """Best effort: fetch the declared public address's peers document.

        The request goes from this computer to the owner's own address and to
        no other service. Success shows the address answers from here; it
        cannot prove people outside this network can reach it.
        """
        url = self.policy().public_url
        if url is None:
            raise PreservationError("community_address_required")
        outcome: str
        status: int | None = None
        policy = ADDRESS_CHECK_POLICY
        deadline = time.monotonic() + policy.deadline_seconds
        try:
            with response(url, PEERS_PATH, policy) as received:
                data = b""
                # A slow sender cannot stretch the check: every read is bounded
                # by the socket timeout, and the whole check by the deadline.
                while len(data) <= MAX_PEERS_BYTES:
                    if time.monotonic() > deadline:
                        raise TimeoutError("address check deadline exceeded")
                    chunk = received.read1(min(65536, MAX_PEERS_BYTES + 1 - len(data)))
                    if not chunk:
                        break
                    data += chunk
        except TimeoutError:
            outcome = "too_slow"
        except MirrorHTTPError as error:
            outcome, status = "http_error", error.status_code
        except PreservationError as error:
            # Every resolved address was private, so none was even tried.
            private = (
                "no permitted reachable address" in str(error)
                and error.__cause__ is None
            )
            outcome = "private_address" if private else "unreachable"
        except (OSError, http.client.HTTPException):
            outcome = "unreachable"
        else:
            outcome = self._peers_outcome(data)
        return {"address": url, "outcome": outcome, "http_status": status}

    def _peers_outcome(self, data: bytes) -> str:
        """Whether a fetched peers document carries this mirror's own card."""
        if len(data) > MAX_PEERS_BYTES:
            return "not_a_mirror"
        try:
            document = PeersDocument.model_validate_json(data)
        except ValueError:
            return "not_a_mirror"
        if document.self_card is None:
            return "no_card"
        try:
            card = verify_card(encode_record(document.self_card))
        except (ValueError, PreservationError):
            return "no_card"
        if card.mirror_key != public_record(self._mirror_key()):
            return "other_mirror"
        return "this_mirror"

    def read(self, copies: Sequence[Holding]) -> dict[str, JsonValue]:
        """Summarize the switch, policy, every copy's standing and live serving."""
        policy = self.policy()
        rows = [self.candidate(copy) for copy in copies]
        observation = self.observation
        usage: dict[str, JsonValue] = {}
        path = self._mirror()
        try:
            state = MirrorLedger(path).read()
        except FileNotFoundError:
            pass  # Not created yet, or being created by the mirror thread.
        else:
            usage = {
                "uploaded_today_bytes": state.egress_day_used,
                "uploaded_this_month_bytes": state.egress_month_used,
                "pirate_face": self.pirate_face_status(state),
            }
        return {
            "policy": policy.model_dump(mode="json"),
            "consented": self.consented(policy),
            "default_licenses": list(DEFAULT_LICENSES),
            "licence_families": definition_json(),
            "licence_definition": LICENCE_DEFINITION_VERSION,
            "copies": cast(
                list[JsonValue],
                [{k: v for k, v in row.items() if k != "decisions"} for row in rows],
            ),
            "sharing_count": sum(1 for row in rows if row["status"] == "sharing"),
            "running": bool(observation and observation.running),
            "http_port": observation.http_port if observation else None,
            "reachability": self.reachability(),
            **self.announcements(),
            "last_error": self.last_error,
            "bucket": self._bucket_summary(),
            "bucket_providers": [
                {
                    "id": key,
                    "name": preset.name,
                    "asks": list(preset.asks),
                    "optional": list(preset.optional),
                    "choices": {
                        name: [
                            {"value": value, "label": label} for value, label in options
                        ]
                        for name, options in preset.choices.items()
                    },
                    "note": preset.note,
                }
                for key, preset in PRESETS.items()
            ],
            **usage,
        }

    def _bucket_summary(self) -> JsonValue:
        """What the owner sees about the bucket: never its keys."""
        state = self.bucket()
        if state is None:
            return None
        return {
            "provider": state.target.provider,
            "provider_name": PRESETS[state.target.provider].name,
            "jurisdiction": r2_jurisdiction(state.target),
            "bucket": state.target.bucket,
            "public_url": state.target.public_url,
            "model_count": len(state.synced),
            # Includes uploads whose outcome was never confirmed: they may be
            # public, so the bucket is only empty when this reaches zero.
            "withdraw_count": len(set(state.synced) | set(state.pending)),
            "last_synced": state.last_synced,
            "last_error": state.last_error,
        }
