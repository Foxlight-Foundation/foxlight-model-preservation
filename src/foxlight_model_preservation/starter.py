"""A signed, one-time starter import list for persistent mirrors.

A starter list names exact preservation bundles (by manifest ID) in tiers, with
each bundle's licence summary, reviewed terms, size and mirror hints. It reuses
the signed catalog's building blocks: the same DSSE envelope and Ed25519
signatures, an identity that is the SHA-256 of the RFC 8785 payload, and
entries that carry a catalog entry's ``entry_id``, ``manifest`` and
``mirrors``. It adds what an operator must review before importing: the tier,
licence, terms, size and the model-signing trust profile.

Importing is a one-time copy, not a subscription. Each imported bundle becomes
an ordinary local selection (``mirror retain``) under the operator's own
authority. Nothing in a later list, from Foxlight or anyone else, can stop,
change or remove it; a later list is just another list the operator may choose
to import. Removed selections are never re-imported.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import Field, JsonValue, field_validator, model_validator

from .mirror_cards import base_url
from .mirror_intake import retain_local
from .mirror_models import SourceGrant, Terms
from .mirror_state import HEADROOM, MirrorLedger
from .models import (
    MAX_METADATA_BYTES,
    Digest,
    Identifier,
    PreservationError,
    Record,
    TrustProfile,
)
from .operations import OperationControl, failure_code
from .signing import (
    Envelope,
    canonical_bytes,
    decode_base64,
    digest,
    encode_record,
    load_trust,
    sign_record,
    strict_json,
    verify_payload,
)
from .transfer import TransferPolicy, fetch

STARTER_LIST_TYPE = "application/vnd.foxlight.sovereign.starter-list.v1+json"
STARTER_LIST_VERSION = "sovereign.starter-list.v1"
TIERS = ("small", "standard")
Tier = Literal["small", "standard"]
STARTER_CONSENT_FLAG = "--yes-i-accept-the-starter-list"
# Each import fetch is bounded; the largest starter bundle is about 20 GB.
FETCH_DEADLINE_SECONDS = 86400
_REVISION = r"^[0-9a-f]{40}$"


class StarterLicence(Record):
    """The licence an entry was admitted under, resolved from its base model.

    ``base_model`` is the upstream model whose card and licence file decided
    the licence; ``conversion`` is the repository the weights were taken from
    when that differs (a GGUF conversion, for example). A conversion's own tag
    never decides the licence.
    """

    license_id: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=300)
    base_model: str = Field(min_length=1, max_length=512)
    base_revision: str = Field(pattern=_REVISION)
    conversion: str | None = Field(default=None, min_length=1, max_length=512)
    conversion_revision: str | None = Field(default=None, pattern=_REVISION)

    @model_validator(mode="after")
    def paired(self) -> StarterLicence:
        """A conversion names its exact revision, and only then."""
        if (self.conversion is None) != (self.conversion_revision is None):
            raise PreservationError("starter conversion needs its exact revision")
        return self


class StarterEntry(Record):
    """One exact bundle in a starter tier; a catalog entry plus review facts."""

    entry_id: Identifier
    tier: Tier
    title: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=200)
    manifest: Digest
    recipe: str = Field(min_length=1, max_length=200)
    recipe_sha256: Digest
    size_bytes: int = Field(ge=1, le=2**53 - 1)
    licence: StarterLicence
    terms: tuple[Terms, ...] = Field(min_length=1, max_length=100)
    mirrors: tuple[str, ...] = Field(min_length=1, max_length=16)

    @field_validator("mirrors")
    @classmethod
    def plain_mirrors(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Mirror hints are credential-free HTTP(S) base URLs, once each."""
        result = tuple(base_url(value) for value in values)
        if len(set(result)) != len(result):
            raise PreservationError("duplicate starter mirror hint")
        return result


class StarterList(Record):
    """A versioned, signed one-time import list; never a followed catalog."""

    schema_version: Literal["sovereign.starter-list.v1"]
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    issued: str = Field(min_length=1, max_length=100)
    bundle_trust: TrustProfile
    entries: tuple[StarterEntry, ...] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique(self) -> StarterList:
        """Entry IDs and manifests are unique within one list."""
        if len({entry.entry_id for entry in self.entries}) != len(self.entries):
            raise PreservationError("duplicate starter entry")
        if len({entry.manifest for entry in self.entries}) != len(self.entries):
            raise PreservationError("duplicate starter manifest")
        return self


def list_identity(starter: StarterList) -> str:
    """The list ID: SHA-256 of the canonical (RFC 8785) payload."""
    return digest(encode_record(starter))


def sign_starter_list(
    starter: StarterList, keys: tuple[Ed25519PrivateKey, ...]
) -> bytes:
    """Sign a starter list in its own DSSE payload domain; return the envelope.

    Every signing key must be in the list's own bundle trust profile, so the
    list and its bundles are vouched for by the same reviewed keys.
    """
    load_trust(encode_record(starter.bundle_trust))
    enrolled = {key.key_id for key in starter.bundle_trust.keys}
    envelope = sign_record(starter, keys, STARTER_LIST_TYPE)
    if any(signature.keyid not in enrolled for signature in envelope.signatures):
        raise PreservationError("starter list signer is not in its bundle trust")
    encoded = encode_record(envelope)
    if len(encoded) > MAX_METADATA_BYTES:
        raise PreservationError("starter list exceeds metadata limit")
    return encoded


def verify_starter_list(
    data: bytes, expected_list_id: str, *, trust: TrustProfile | None = None
) -> StarterList:
    """Authenticate an exact starter list before any of it is shown or used.

    The expected list ID, obtained independently (for example from the
    published documentation), pins the exact payload. The signature must meet
    the threshold of the list's own bundle trust profile; when ``trust`` is
    supplied (independently enrolled keys), it must meet that too. Unknown
    fields, duplicate keys and other versions refuse.
    """
    envelope = Envelope.model_validate_json(canonical_bytes(strict_json(data)))
    if envelope.payloadType != STARTER_LIST_TYPE:
        raise PreservationError("not a starter list")
    payload = decode_base64(envelope.payload)
    if digest(payload) != expected_list_id:
        raise PreservationError("starter list differs from the expected list ID")
    value = strict_json(payload)
    if canonical_bytes(value) != payload:
        raise PreservationError("starter list is not RFC 8785 canonical JSON")
    starter = StarterList.model_validate_json(payload)
    bundle_trust = load_trust(encode_record(starter.bundle_trust))
    verify_payload(data, bundle_trust, expected_list_id, STARTER_LIST_TYPE)
    if trust is not None:
        verify_payload(data, trust, expected_list_id, STARTER_LIST_TYPE)
    return starter


def select_tier(starter: StarterList, tier: Tier) -> tuple[StarterEntry, ...]:
    """The entries a tier imports: standard includes everything in small."""
    if tier not in TIERS:
        raise PreservationError("unknown starter tier")
    wanted = TIERS[: TIERS.index(tier) + 1]
    return tuple(entry for entry in starter.entries if entry.tier in wanted)


def _size(value: int) -> str:
    for unit, scale in (("TB", 10**12), ("GB", 10**9), ("MB", 10**6), ("kB", 10**3)):
        if value >= scale:
            return f"{value / scale:.1f} {unit}"
    return f"{value} B"


@dataclass(frozen=True)
class StarterPlan:
    """What one import would do, computed offline from the ledger."""

    list_id: str
    tier: Tier
    entries: tuple[StarterEntry, ...]
    status: dict[str, str]
    new_bytes: int
    usage: int
    disk_bytes: int

    @property
    def fits(self) -> bool:
        """The new bytes fit the mirror's disk budget, with headroom."""
        return self.usage + self.new_bytes + HEADROOM <= self.disk_bytes


def plan_import(
    ledger: MirrorLedger, starter: StarterList, list_id: str, tier: Tier
) -> StarterPlan:
    """Classify each tier entry as new, already held or removed; offline."""
    state = ledger.read()
    entries = select_tier(starter, tier)
    status: dict[str, str] = {}
    for entry in entries:
        held = state.models.get(entry.manifest)
        if held is None:
            status[entry.entry_id] = "new"
        elif held.state == "removed":
            status[entry.entry_id] = "removed"
        elif held.error:
            status[entry.entry_id] = "error"
        else:
            status[entry.entry_id] = "held"
    return StarterPlan(
        list_id=list_id,
        tier=tier,
        entries=entries,
        status=status,
        new_bytes=sum(
            entry.size_bytes for entry in entries if status[entry.entry_id] == "new"
        ),
        usage=ledger.usage(),
        disk_bytes=state.disk_bytes,
    )


def review_import(
    starter: StarterList, plan: StarterPlan
) -> tuple[str, dict[str, JsonValue]]:
    """The one human review shown before consent, and its summary."""
    trust = starter.bundle_trust
    labels = {
        "new": "",
        "held": "  (already held: skipped)",
        "removed": "  (you removed it: never re-imported)",
        "error": "  (an earlier copy failed: `mirror retry` it first)",
    }
    lines = [
        "Sovereign starter collection: review before importing",
        "",
        f"List:  {starter.title} ({starter.schema_version}, issued {starter.issued})",
        f"  list id: {plan.list_id} (matches the expected ID)",
        f"Tier:  {plan.tier}"
        + ("  (includes the small tier)" if plan.tier == "standard" else ""),
        "",
        "Models:",
    ]
    for entry in plan.entries:
        licence = entry.licence
        source = licence.conversion or licence.base_model
        lines += [
            f"  {entry.title} [{entry.tier}] {_size(entry.size_bytes)}"
            + labels[plan.status[entry.entry_id]],
            f"    {entry.role}",
            f"    licence: {licence.license_id} (from {licence.base_model} "
            f"@ {licence.base_revision[:12]}); {licence.summary}",
            f"    weights: {source}; manifest {entry.manifest}",
        ]
    lines += [
        "",
        f"Model-signing keys accepted for these bundles (threshold "
        f"{trust.threshold} of {len(trust.keys)}; as of {trust.as_of}):",
        *(f"  {key.key_id}" for key in trust.keys),
        "  They are trusted only for the bundles you import from this list.",
        "",
        f"New download:  {_size(plan.new_bytes)}",
        f"Disk budget:   {_size(plan.disk_bytes)} ({_size(plan.usage)} already used)",
        "Fits:          "
        + ("yes" if plan.fits else "NO: raise --disk-bytes or choose a smaller tier"),
        "",
        "This is a one-time import. Each model becomes your own local selection:",
        "  nothing Foxlight publishes later can stop, change or remove it. A later",
        "  starter list is just a new list you may choose to import.",
        "Licences travel with every copy (each bundle carries its LICENSE and",
        "  notices). You are responsible for the law where you run this mirror;",
        "  this review is not legal advice. Serving is public and not anonymous.",
    ]
    summary: dict[str, JsonValue] = {
        "list_id": plan.list_id,
        "title": starter.title,
        "tier": plan.tier,
        "entries": [
            {
                "entry_id": entry.entry_id,
                "manifest": entry.manifest,
                "license_id": entry.licence.license_id,
                "size_bytes": entry.size_bytes,
                "status": plan.status[entry.entry_id],
            }
            for entry in plan.entries
        ],
        "model_keys": [key.key_id for key in trust.keys],
        "new_bytes": plan.new_bytes,
        "disk_bytes": plan.disk_bytes,
        "usage_bytes": plan.usage,
        "fits": plan.fits,
        "follows_publisher": False,
        "anonymous": False,
    }
    return "\n".join(lines), summary


@dataclass
class ImportOptions:
    """Operator choices for fetching starter bundles."""

    mirrors: tuple[str, ...] = ()
    allow_http: bool = False
    allow_private: bool = False
    staging: Path | None = None
    keep_going: bool = False
    progress: Callable[[str, str], None] | None = field(default=None)


def entry_grant(starter: StarterList, entry: StarterEntry) -> SourceGrant:
    """The fixed authority one imported entry is retained under.

    It is derived only from the reviewed list, so re-running the same import
    reproduces exactly the same grant.
    """
    return SourceGrant(
        trust=starter.bundle_trust, terms=entry.terms, max_bytes=entry.size_bytes
    )


def import_starter(
    ledger: MirrorLedger,
    starter: StarterList,
    list_id: str,
    tier: Tier,
    *,
    consented: bool,
    options: ImportOptions | None = None,
    control: OperationControl | None = None,
) -> dict[str, JsonValue]:
    """Fetch each new tier entry and retain it as an ordinary local selection.

    Requires consent to the printed review, a stopped controller and a disk
    budget that fits every new bundle; all three refuse before any download.
    Entries already held are skipped, so re-running is idempotent. Entries the
    operator removed are never re-imported. Each bundle is fetched into a
    staging store beside the mirror, verified under the list's trust profile,
    checked against the entry's size and terms, then hardlinked into the
    mirror store. No source is followed and nothing is scheduled: there is no
    relationship with the list or its publisher afterwards.
    """
    if not consented:
        raise PreservationError(
            "importing the starter list needs interactive consent or "
            + STARTER_CONSENT_FLAG
        )
    chosen = options or ImportOptions()
    if ledger.running():
        raise PreservationError("stop the active mirror controller first")
    state = ledger.read()
    if state.left:
        raise PreservationError("this mirror has left; it does not import")
    plan = plan_import(ledger, starter, list_id, tier)
    if not plan.fits:
        raise PreservationError(
            "starter import exceeds the mirror disk budget; raise it or choose "
            "a smaller tier"
        )
    staging = chosen.staging or ledger.path.parent / (
        ledger.path.name + ".starter-import"
    )
    grant_before = state.restart is not None
    imported: list[JsonValue] = []
    skipped: list[JsonValue] = []
    failed: list[JsonValue] = []
    for entry in plan.entries:
        status = plan.status[entry.entry_id]
        if status != "new":
            skipped.append({"entry_id": entry.entry_id, "reason": status})
            continue
        if chosen.progress is not None:
            chosen.progress(entry.entry_id, "fetching")
        store = staging / entry.manifest
        try:
            manifest = fetch(
                entry.manifest,
                starter.bundle_trust,
                (*chosen.mirrors, *entry.mirrors)[:16],
                store,
                TransferPolicy(
                    max_bytes=entry.size_bytes,
                    deadline_seconds=FETCH_DEADLINE_SECONDS,
                    allow_http=chosen.allow_http,
                    allow_private=chosen.allow_private,
                ),
                control=control.fork() if control is not None else None,
            )
            if manifest.total_bytes != entry.size_bytes:
                raise PreservationError("starter bundle size differs from its list")
            retain_local(
                ledger,
                store,
                entry.manifest,
                entry_grant(starter, entry),
                control=control.fork() if control is not None else None,
                link_objects=True,
            )
        except (ValueError, OSError) as error:
            if not chosen.keep_going:
                raise
            failed.append({"entry_id": entry.entry_id, "error": failure_code(error)})
            continue
        shutil.rmtree(store, ignore_errors=True)
        imported.append(entry.entry_id)
    try:
        staging.rmdir()
    except OSError:
        pass  # Absent, or a failed entry's partial store is kept for a rerun.
    return {
        "list_id": list_id,
        "tier": tier,
        "imported": imported,
        "skipped": skipped,
        "failed": failed,
        "follows_publisher": False,
        "restart_grant_invalidated": grant_before and ledger.read().restart is None,
        "recorded_at": int(time.time()),
    }
