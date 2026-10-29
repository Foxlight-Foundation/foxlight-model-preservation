"""Help keep models available: one long-lived mirror of this computer's backups.

One switch and one licence policy cover every backup. Eligible backups join on
their own; there is no per-model step and no hour limit. Serving resumes after a
restart only under the owner's single standing consent, which is bounded and
revocable. Upload caps still apply. Only members whose licence is in the owner's
selected set are ever published, and a backup with no identifiable licence stays
private. Withholding or disabling blocks serving without deleting anything.
"""

from __future__ import annotations

import ipaddress
import shutil
import socket
import time
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol, cast

from pydantic import Field, JsonValue, field_validator

from ..assurance import load_retained_card
from ..cards import identity_json
from ..mirror import model_action, run_mirror
from ..mirror_intake import retain_local
from ..mirror_models import MirrorObservation, SourceGrant, Terms
from ..mirror_state import MirrorLedger, grant_restart
from ..models import (
    Digest,
    Manifest,
    ManifestMember,
    PreservationError,
    Record,
    TrustProfile,
)
from ..operations import OperationControl, OperationError, failure_code
from ..signing import digest, encode_record
from ..storage import atomic_bytes, directory, read_metadata
from .presentation import card_summary
from .sharing import MemberDecision, Sharing

# Permissive by default; the owner can widen the set. Eligibility follows the
# licence itself, never whether the original download was gated.
DEFAULT_LICENSES = (
    "apache-2.0",
    "mit",
    "bsd-2-clause",
    "bsd-3-clause",
    "bsd-3-clause-clear",
    "0bsd",
    "openrail",
    "openrail-m",
    "openrail++",
    "creativeml-openrail-m",
    "bigscience-openrail-m",
    "bigcode-openrail-m",
)
# Values that identify no licence. A member carrying one is never published.
UNIDENTIFIED = frozenset(
    {
        "",
        "unknown",
        "other",
        "none",
        "noassertion",
        "unlicensed",
        "unresolved",
        "unresolved-owner-capture",
        "see-original-card",
    }
)
_CAPTURE_NOTICE = ".foxlight-capture-notice.txt"
_LICENSE_NAMES = ("license", "licence", "copying")
DEFAULT_PORT = 8743
CONSENT_SECONDS = 365 * 86400


# Common spelled-out names that cards use instead of an SPDX identifier.
_ALIASES = {
    "apache-license-2.0": "apache-2.0",
    "apache-license,-version-2.0": "apache-2.0",
    "apache-2": "apache-2.0",
    "apache": "apache-2.0",
    "mit-license": "mit",
    "the-mit-license": "mit",
    "bsd-2-clause-license": "bsd-2-clause",
    "bsd-3-clause-license": "bsd-3-clause",
    "openrail-m-license": "openrail-m",
}


def normalize_license(value: str) -> str:
    """Compare licence identifiers case-insensitively, with spaces as dashes.

    A few common spelled-out names map to their SPDX identifiers.
    """
    folded = "-".join(value.strip().casefold().split())
    return _ALIASES.get(folded, folded)


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


class CommunityPolicy(Record):
    """The owner's one standing choice; changing it never deletes retained bytes."""

    schema_version: Literal["sovereign.community-mirror.v1"] = (
        "sovereign.community-mirror.v1"
    )
    enabled: bool = False
    share: Literal["eligible", "chosen"] = "eligible"
    licenses: tuple[str, ...] = Field(default=DEFAULT_LICENSES, max_length=128)
    chosen: tuple[Digest, ...] = Field(default=(), max_length=256)
    withheld: tuple[Digest, ...] = Field(default=(), max_length=256)
    daily_upload_bytes: int = Field(default=20 * 1024**3, ge=1, le=2**53 - 1)
    monthly_upload_bytes: int = Field(default=300 * 1024**3, ge=1, le=2**53 - 1)
    port: int = Field(default=DEFAULT_PORT, ge=1024, le=65535)
    consent_expires: int | None = None
    rarest_first: bool = False

    @field_validator("licenses")
    @classmethod
    def identified(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Keep normalized, identified licences only, once each."""
        result = tuple(dict.fromkeys(normalize_license(value) for value in values))
        if any(value in UNIDENTIFIED or len(value) > 200 for value in result):
            raise PreservationError("community_license_unidentified")
        return result


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
    value = card_summary(retained.envelope).get("license")
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

    def __init__(self, home: Path, sharing: Sharing) -> None:
        self.home = home
        self.sharing = sharing
        self.path = home / "sharing/community"
        self.observation: MirrorObservation | None = None
        self.last_error: str | None = None
        self.failures: dict[str, str] = {}

    # Policy and membership -------------------------------------------------

    def policy(self) -> CommunityPolicy:
        """Read the standing choice; a missing file means the switch is off."""
        try:
            with directory(self.path) as root:
                return CommunityPolicy.model_validate_json(
                    read_metadata(root, "policy.json")
                )
        except FileNotFoundError:
            return CommunityPolicy()

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
            "rarest_first",
        }
        if not body or not set(body) <= allowed:
            raise PreservationError("invalid_control")
        policy = self.policy()
        changes = {key: value for key, value in body.items() if key != "consent"}
        if body.get("enabled") is True:
            if body.get("consent") is not True:
                raise PreservationError("community_consent_required")
            changes["consent_expires"] = int(time.time()) + CONSENT_SECONDS
        elif body.get("enabled") is False:
            changes["consent_expires"] = None
        elif "consent" in body:
            raise PreservationError("invalid_control")
        updated = CommunityPolicy.model_validate_json(
            identity_json({**policy.model_dump(mode="json"), **changes})
        )
        self._save(updated)
        if not updated.enabled:
            self._revoke()
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
                self.home / "copies" / copy.copy_id, copy.manifest_id, copy.trust
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

    def ledger(self) -> MirrorLedger:
        """Open the one community mirror, creating it the first time it is used."""
        policy = self.policy()
        path = self.path / "mirror"
        if not path.exists():
            with directory(self.path, create=True):
                pass
            MirrorLedger.initialize(
                path,
                name="Sovereign community mirror",
                # Linked objects cost no new space; the logical budget is the
                # whole volume and real free space is still checked per model.
                disk_bytes=min(
                    max(shutil.disk_usage(self.path).total, 256 * 1024**2),
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
        for copy in copies:
            control.checkpoint()
            view = self.candidate(copy)
            if view["status"] not in {"sharing", "eligible", "failed"}:
                continue
            identity = self.joined().copies.get(copy.copy_id)
            if identity is None:
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
        store = self.home / "copies" / copy.copy_id
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
        if not any(
            held.state == "retained" and not held.blocked
            for held in ledger.read().models.values()
        ):
            raise PreservationError("community_nothing_to_share")
        grant_restart(ledger, policy.consent_expires)

        def record(value: MirrorObservation) -> None:
            self.observation = value
            if observe is not None:
                observe(value)

        run_mirror(ledger, resume=True, control=control, observe=record)

    def _revoke(self) -> None:
        path = self.path / "mirror"
        if path.exists():
            MirrorLedger(path).update(
                lambda current: current.model_copy(update={"restart": None})
            )

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

    def read(self, copies: Sequence[Holding]) -> dict[str, JsonValue]:
        """Summarize the switch, policy, every copy's standing and live serving."""
        policy = self.policy()
        rows = [self.candidate(copy) for copy in copies]
        observation = self.observation
        usage: dict[str, JsonValue] = {}
        path = self.path / "mirror"
        if path.exists():
            state = MirrorLedger(path).read()
            usage = {
                "uploaded_today_bytes": state.egress_day_used,
                "uploaded_this_month_bytes": state.egress_month_used,
            }
        return {
            "policy": policy.model_dump(mode="json"),
            "consented": self.consented(policy),
            "default_licenses": list(DEFAULT_LICENSES),
            "copies": cast(
                list[JsonValue],
                [{k: v for k, v in row.items() if k != "decisions"} for row in rows],
            ),
            "sharing_count": sum(1 for row in rows if row["status"] == "sharing"),
            "running": bool(observation and observation.running),
            "http_port": observation.http_port if observation else None,
            "reachability": self.reachability(),
            "last_error": self.last_error,
            **usage,
        }
