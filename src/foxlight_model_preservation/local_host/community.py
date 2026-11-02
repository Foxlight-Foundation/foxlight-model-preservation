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
import os
import re
import secrets
import shutil
import socket
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol, cast
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import Field, JsonValue, ValidationError, field_validator

from ..assurance import load_retained_card
from ..bucket import (
    PRESETS,
    BucketClient,
    BucketCredentials,
    BucketTarget,
    check_bucket,
    delete_credentials,
    load_credentials,
    publish_peers,
    publish_to_bucket,
    remove_from_bucket,
    save_credentials,
    target_for,
)
from ..cards import identity_json
from ..mirror import model_action, run_mirror
from ..mirror_cards import base_url, build_peers, issue_card
from ..mirror_directory import MirrorDirectory
from ..mirror_intake import retain_local
from ..mirror_models import MirrorObservation, SourceGrant, Terms
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
from ..signing import create_key, digest, encode_record, load_key
from ..storage import atomic_bytes, directory, read_metadata
from ..transfer import TransferPolicy
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
    public_url: str | None = Field(default=None, min_length=8, max_length=2048)
    announce_dht: bool = True
    publish_nostr: bool = True
    nostr_relays: tuple[str, ...] = Field(default=DEFAULT_RELAYS, max_length=16)
    consent_expires: int | None = None
    rarest_first: bool = False

    @field_validator("public_url")
    @classmethod
    def plain_url(cls, value: str | None) -> str | None:
        """Accept only a credential-free HTTP(S) base URL the owner says works."""
        return None if value is None else base_url(value)

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
        result = tuple(dict.fromkeys(normalize_license(value) for value in values))
        if any(value in UNIDENTIFIED or len(value) > 200 for value in result):
            raise PreservationError("community_license_unidentified")
        return result


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
    """A connected cloud bucket; it names a keychain entry and holds no secret."""

    schema_version: Literal["sovereign.community-bucket.v1"] = (
        "sovereign.community-bucket.v1"
    )
    target: BucketTarget
    synced: tuple[Digest, ...] = Field(default=(), max_length=1000)
    last_error: str | None = Field(default=None, max_length=200)
    last_synced: int | None = None


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

    # Requests to a connected bucket's endpoint and public address.
    bucket_policy = TransferPolicy(timeout=60, deadline_seconds=86400)

    def __init__(self, home: Path, sharing: Sharing) -> None:
        self.home = home
        self.sharing = sharing
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
        # Gossip rounds (and the rendezvous they own) never overlap.
        self._gossip_lock = threading.Lock()

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
            "public_url",
            "announce_dht",
            "publish_nostr",
            "nostr_relays",
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
        self._save(updated)
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
        # A pending selection is complete but awaits re-verification; the
        # controller verifies every byte and promotes it before serving it.
        if not any(
            held.state in {"retained", "pending"}
            and not held.blocked
            and not held.error
            for held in ledger.read().models.values()
        ):
            raise PreservationError("community_nothing_to_share")
        grant_restart(ledger, policy.consent_expires)

        def record(value: MirrorObservation) -> None:
            self.observation = value
            if observe is not None:
                observe(value)

        run_mirror(ledger, resume=True, control=control, observe=record)

    def revoke_grant(self) -> bool:
        """Clear the mirror's standing restart grant; report whether it is gone."""
        try:
            MirrorLedger(self.path / "mirror").update(
                lambda current: current.model_copy(update={"restart": None})
            )
        except FileNotFoundError:
            return True  # No mirror yet, so there is no standing grant to revoke.
        except (OSError, ValueError):
            return False
        return True

    # Discovery ---------------------------------------------------------------

    def _mirror_key(self) -> Ed25519PrivateKey:
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
                atomic_bytes(root, "bucket.json", encode_record(state))
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
            )
        except ValueError as error:
            code = str(error)
            raise PreservationError(
                code if code.startswith("bucket_") else "bucket_details_invalid"
            ) from None
        result = check_bucket(
            BucketClient(target, credentials, self.bucket_policy), self.bucket_policy
        )
        if result["status"] != "ready":
            return {"status": result["status"]}
        save_credentials(target.key_name, credentials)
        with self._bucket_lock:
            previous = self.bucket()
            with directory(self.path, create=True) as root:
                atomic_bytes(
                    root, "bucket.json", encode_record(BucketState(target=target))
                )
        if previous is not None:
            delete_credentials(previous.target.key_name)
        return {"status": "ready", "public_url": target.public_url}

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
        objects travel, manifest last), charged to the same daily and monthly
        upload limits as serving. One model's failure does not stop the others;
        failures are recorded and retried later.
        """
        with self._bucket_sync_lock:
            return self._sync_bucket(control)

    def _sync_bucket(self, control: OperationControl) -> BucketState | None:
        found = self.bucket()
        if found is None:
            return None
        state: BucketState = found
        synced = list(state.synced)
        failure: str | None = None

        def save() -> bool:
            nonlocal state
            state = state.model_copy(
                update={"synced": tuple(synced), "last_error": failure}
            )
            return self._save_bucket(state)

        try:
            client = BucketClient(
                state.target,
                load_credentials(state.target.key_name),
                self.bucket_policy,
            )
            try:
                ledger = MirrorLedger(self.path / "mirror")
                models = ledger.read().models
            except FileNotFoundError:
                # Never shared yet: there is nothing to upload, only to withdraw.
                ledger, models = None, {}
            held = self.held() if self.consented() else frozenset[str]()
            for identity in [item for item in synced if item not in held]:
                control.checkpoint()
                remove_from_bucket(client, identity)
                synced.remove(identity)
                if not save():
                    return self.bucket()
            for identity in sorted(held - set(synced)):
                if ledger is None or identity not in models:
                    continue
                control.checkpoint()
                try:
                    publish_to_bucket(
                        self.path / "mirror/store",
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
            models = MirrorLedger(self.path / "mirror").read().models
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
        try:
            with directory(self.path / "mirror") as root:
                atomic_bytes(root, "peers.json", document)
        except FileNotFoundError:
            pass  # No mirror yet; the next sync creates it and announces.
        if bucket is not None and bucket.synced:
            try:
                publish_peers(
                    BucketClient(
                        bucket.target,
                        load_credentials(bucket.target.key_name),
                        self.bucket_policy,
                    ),
                    document,
                )
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
        if port is not None and rendezvous_available():
            if self.rendezvous is None:
                self.rendezvous = Rendezvous()
            self.rendezvous.announce(port, seconds=20, control=control)
            seeds = self.rendezvous.lookup(seconds=20, control=control)
        else:
            self._close_rendezvous()
        current = self.policy()
        relays = current.nostr_relays if current.publish_nostr else ()
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
        policy = self.policy()
        if not policy.announce_dht:
            return None
        if policy.public_url is not None:
            parsed = urlsplit(policy.public_url)
            if parsed.scheme != "http" or parsed.path not in {"", "/"}:
                return None
            return parsed.port or 80
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

    def read(self, copies: Sequence[Holding]) -> dict[str, JsonValue]:
        """Summarize the switch, policy, every copy's standing and live serving."""
        policy = self.policy()
        rows = [self.candidate(copy) for copy in copies]
        observation = self.observation
        usage: dict[str, JsonValue] = {}
        path = self.path / "mirror"
        try:
            state = MirrorLedger(path).read()
        except FileNotFoundError:
            pass  # Not created yet, or being created by the mirror thread.
        else:
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
            "bucket": self._bucket_summary(),
            "bucket_providers": [
                {
                    "id": key,
                    "name": preset.name,
                    "asks": list(preset.asks),
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
            "bucket": state.target.bucket,
            "public_url": state.target.public_url,
            "model_count": len(state.synced),
            "last_synced": state.last_synced,
            "last_error": state.last_error,
        }
