"""One reviewable collection file that sets up a headless public mirror.

``mirror quickstart`` wraps ``init``, ``follow`` and ``grant-resume``. The
collection file bundles everything an operator must independently review: the
source document, its expected bootstrap ID, the model trust profile and the
accepted terms. Nothing is enrolled, followed or granted until the operator
has seen one review and explicitly consented to public serving.
"""

from __future__ import annotations

import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, JsonValue

from .discovery import inspect_source, list_sources
from .mirror import (
    apply_source_grant,
    configure_mirror,
    derived_model_limit,
    follow,
)
from .mirror_announce import DISCLOSURE, dht_port
from .mirror_models import AnnouncePolicy, SourceGrant, Terms
from .mirror_state import MirrorLedger, grant_restart, policy_id
from .models import Digest, PreservationError, Record, TrustProfile
from .signing import canonical_bytes, load_trust, strict_json

COLLECTION_VERSION = "sovereign.mirror-collection.v1"
MAX_COLLECTION_BYTES = 1024**2
MAX_RESUME_SECONDS = 366 * 86400
CONSENT_FLAG = "--yes-i-consent-to-public-serving"
_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4, "P": 1024**5}
_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 7 * 86400}


def parse_size(text: str) -> int:
    """Parse a byte count with optional binary unit: ``100G`` is 100 * 1024**3.

    Accepts K, M, G, T and P (each a power of 1024), optionally followed by
    ``i``/``iB``/``B``, case-insensitively. A bare integer is bytes.
    """
    match = re.fullmatch(r"\s*(\d{1,16})\s*([kmgtp]?)(?:i?b)?\s*", text, re.IGNORECASE)
    if match is None:
        raise PreservationError(
            "size must be an integer with an optional binary unit, such as 100G"
        )
    value = int(match.group(1)) * _UNITS[match.group(2).upper()]
    if not 1 <= value <= 2**53 - 1:
        raise PreservationError("size is outside the supported range")
    return value


def format_size(value: int) -> str:
    """Render bytes in the largest exact-or-one-decimal binary unit."""
    for unit in ("P", "T", "G", "M", "K"):
        scale = _UNITS[unit]
        if value >= scale:
            amount = value / scale
            text = f"{amount:.1f}".removesuffix(".0")
            return f"{text} {unit}iB"
    return f"{value} B"


def parse_duration(text: str) -> int:
    """Parse a duration such as ``365d``, ``12h``, ``90m`` or ``3600s``."""
    match = re.fullmatch(r"\s*(\d{1,9})\s*([smhdw]?)\s*", text)
    if match is None:
        raise PreservationError("duration must look like 365d, 12h, 30m or 60s")
    return int(match.group(1)) * _SECONDS[match.group(2) or "s"]


def default_name() -> str:
    """Name a mirror after this host, bounded to the ledger's name limit."""
    name = socket.gethostname().strip()[:200]
    return name or "sovereign-mirror"


class CollectionSource(Record):
    """A source document as a file beside the collection, or inline."""

    file: str | None = Field(default=None, min_length=1, max_length=1024)
    inline: dict[str, JsonValue] | None = None


class MirrorCollection(Record):
    """Everything a mirror operator reviews before following one source."""

    schema_version: Literal["sovereign.mirror-collection.v1"]
    title: str = Field(min_length=1, max_length=200)
    source: CollectionSource
    expected_bootstrap_id: Digest
    trust: TrustProfile
    terms: tuple[Terms, ...] = Field(min_length=1, max_length=100)
    max_bytes: int | None = Field(default=None, ge=1, le=2**53 - 1)
    allow_http: bool = False


def _bounded(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise PreservationError("collection input exceeds its size bound")
    return data


def load_collection(path: Path) -> tuple[MirrorCollection, bytes]:
    """Strictly parse a collection and its source document; never connect.

    Duplicate JSON keys, unknown fields, absolute or parent-escaping source
    paths and mismatched trust key fingerprints all refuse.
    """
    value = strict_json(_bounded(path, MAX_COLLECTION_BYTES))
    collection = MirrorCollection.model_validate_json(canonical_bytes(value))
    load_trust(canonical_bytes(collection.trust.model_dump(mode="json")))
    source = collection.source
    if (source.file is None) == (source.inline is None):
        raise PreservationError("collection source needs exactly one file or inline")
    if source.file is not None:
        relative = PurePosixPath(source.file)
        if relative.is_absolute() or ".." in relative.parts or "\\" in source.file:
            raise PreservationError("collection source file must stay beside it")
        document = _bounded(path.parent / relative, 16 * 1024**2)
    else:
        document = canonical_bytes(source.inline)
    return collection, document


@dataclass(frozen=True)
class QuickstartSettings:
    """Operator choices that complete a collection into one mirror policy."""

    name: str
    disk_bytes: int
    ingress_daily: int
    egress_daily: int
    egress_monthly: int
    bind: str = "127.0.0.1"
    port: int = 8080
    announce: AnnouncePolicy | None = None
    resume_seconds: int = 180 * 86400
    max_bytes: int | None = None


def model_limit(collection: MirrorCollection, settings: QuickstartSettings) -> int:
    """Operator override, else the collection's limit, else the disk-derived one."""
    if settings.max_bytes is not None:
        return settings.max_bytes
    if collection.max_bytes is not None:
        return collection.max_bytes
    return derived_model_limit(settings.disk_bytes)


def review(
    collection: MirrorCollection, document: bytes, settings: QuickstartSettings
) -> tuple[str, dict[str, JsonValue]]:
    """Build the single human review and its machine-readable summary, offline."""
    source = inspect_source(document)
    if source["bootstrap_id"] != collection.expected_bootstrap_id:
        raise PreservationError(
            "source file differs from the collection's expected bootstrap ID"
        )
    if not 1 <= settings.resume_seconds <= MAX_RESUME_SECONDS:
        raise PreservationError("restart grant must last between 1s and 366 days")
    warnings: list[str] = []
    announce = settings.announce
    if announce is not None and announce.public_url.startswith("http://"):
        warnings.append(
            "public URL uses plain http://: transfers are not encrypted; "
            "prefer https:// behind a TLS reverse proxy"
        )
    if announce is not None and announce.dht and dht_port(announce) is None:
        warnings.append(
            "DHT announcing needs a plain-HTTP root URL; this URL is not "
            "announced on the DHT"
        )
    if settings.bind in {"0.0.0.0", "::"}:  # noqa: S104 - reported, not chosen
        warnings.append(
            "listener binds every interface: it is directly reachable without "
            "the reverse proxy"
        )
    expires = int(time.time()) + settings.resume_seconds
    limit = model_limit(collection, settings)
    lines = [
        "Sovereign mirror quickstart: review before consenting",
        "",
        f"Collection: {collection.title} ({collection.schema_version})",
        f"Source: {source['name']}",
        f"  source id:    {source['source_id']}",
        f"  bootstrap id: {source['bootstrap_id']} (matches the collection)",
        "  endpoints:    "
        + ", ".join(str(item) for item in _items(source["endpoints"])),
        "",
        f"Model signing keys (threshold {collection.trust.threshold} of "
        f"{len(collection.trust.keys)}; as of {collection.trust.as_of}):",
        *(
            f"  {key.key_id}"
            + (" (revoked)" if key.key_id in collection.trust.revoked_keys else "")
            for key in collection.trust.keys
        ),
        "",
        "Accepted terms (every member of every model must match one exactly):",
        *(f"  {item.license_id}: {item.terms_reference}" for item in collection.terms),
        "",
        f"Mirror name:     {settings.name}",
        f"Per-model limit: {format_size(limit)}",
        f"Disk budget:     {format_size(settings.disk_bytes)}",
        f"Ingress budget:  {format_size(settings.ingress_daily)} per UTC day",
        f"Egress budget:   {format_size(settings.egress_daily)} per UTC day, "
        f"{format_size(settings.egress_monthly)} per UTC month",
        f"Listener:        {settings.bind} port {settings.port}",
        f"Public URL:      {announce.public_url if announce else 'none'}",
        "Announcement:    "
        + (
            "signed mirror card in /.well-known/sovereign/peers.json"
            + (", Nostr relays" if announce.nostr else "")
            + (", BitTorrent DHT" if announce.dht else "")
            if announce
            else "none (this mirror is not announced)"
        ),
        "Restart grant:   unattended serving until "
        + time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(expires)),
        "",
        "What is exposed:",
        "  - Anyone who reaches the listener can download the verified public",
        "    model files and signed manifests this mirror retains.",
        "  - Clients' IP addresses are visible to this host and its reverse proxy;",
        "    this host's IP is visible to the source, mirrors and peers it contacts.",
        "  - This is not anonymous.",
        *(
            [
                "  - The public URL, a filter of the models probably held, followed",
                "    catalog IDs"
                + (" and contact" if announce.contact else "")
                + " are published in a signed mirror card.",
                *("  " + line for line in _wrap(DISCLOSURE)),
            ]
            if announce
            else []
        ),
        "Not exposed: private keys, the ledger, partial or unverified bytes",
        "  and models you block.",
        "Catalog withdrawal is advisory: a copy a publisher stops listing keeps",
        "  serving and is flagged in status. You decide; block it to stop serving.",
    ]
    if warnings:
        lines += ["", "Warnings:", *("  ! " + item for item in warnings)]
    summary: dict[str, JsonValue] = {
        "collection": collection.title,
        "source_id": source["source_id"],
        "bootstrap_id": source["bootstrap_id"],
        "model_keys": [key.key_id for key in collection.trust.keys],
        "terms": [item.model_dump(mode="json") for item in collection.terms],
        "per_model_limit": limit,
        "disk_bytes": settings.disk_bytes,
        "ingress_daily": settings.ingress_daily,
        "egress_daily": settings.egress_daily,
        "egress_monthly": settings.egress_monthly,
        "bind": settings.bind,
        "port": settings.port,
        "announce": announce.model_dump(mode="json") if announce else None,
        "restart_expires": expires,
        "warnings": list(warnings),
        "anonymous": False,
    }
    return "\n".join(lines), summary


def _items(value: JsonValue) -> list[JsonValue]:
    return list(value) if isinstance(value, list) else []


def _wrap(text: str, width: int = 74) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    return [*lines, current] if current else lines


def apply_quickstart(
    directory: Path,
    collection: MirrorCollection,
    document: bytes,
    settings: QuickstartSettings,
    *,
    consented: bool,
) -> tuple[MirrorLedger, dict[str, JsonValue]]:
    """Initialize or update, follow and grant restart after explicit consent.

    Safe to repeat on every start (the container does). On an existing mirror
    it applies changed settings, which clears the old grant only when the
    policy actually changes. A source already followed under the same reviewed
    bootstrap keeps its cached roots and catalog: its grant is applied without
    reimporting the collection's possibly older snapshot, so a later refresh
    never turns into a rollback refusal. A paused or departed mirror refuses:
    quickstart never resumes or rejoins it. Without consent nothing is written.
    No network is used; ``mirror run`` starts serving separately.
    """
    if not consented:
        raise PreservationError(
            "public serving needs interactive consent or " + CONSENT_FLAG
        )
    config: dict[str, object] = {
        "name": settings.name,
        "disk_bytes": settings.disk_bytes,
        "ingress_daily": settings.ingress_daily,
        "egress_daily": settings.egress_daily,
        "egress_monthly": settings.egress_monthly,
        "bind": settings.bind,
        "port": settings.port,
        "announce": settings.announce,
    }
    grant = SourceGrant(
        trust=collection.trust,
        terms=collection.terms,
        allow_http=collection.allow_http,
        max_bytes=model_limit(collection, settings),
    )
    source = inspect_source(document)
    source_id = str(source["source_id"])
    if (directory / "mirror.json").exists():
        ledger = MirrorLedger(directory)
        state = ledger.read()
        if state.left:
            raise PreservationError(
                "this mirror has left; quickstart does not rejoin it. Use a new "
                "mirror directory to serve again"
            )
        if state.paused:
            raise PreservationError(
                "this mirror is paused; run `mirror resume` first if you want it "
                "to serve again"
            )
        before = policy_id(state)
        configure_mirror(ledger, config)
        created = False
    elif directory.exists():
        raise PreservationError(
            "mirror directory exists without mirror.json (an interrupted init?); "
            "inspect it and remove it before running quickstart again"
        )
    else:
        ledger = MirrorLedger.initialize(directory, **config)
        before = None
        created = True
    followed = {
        status.source_id: status.bootstrap_id
        for status in list_sources(ledger.path / "discovery")
    }
    reimported = not (
        source_id in ledger.read().sources
        and followed.get(source_id) == collection.expected_bootstrap_id
    )
    if reimported:
        follow(ledger, document, collection.expected_bootstrap_id, grant)
    else:
        apply_source_grant(ledger, source_id, grant)
    changed = before is None or policy_id(ledger.read()) != before
    expires = int(time.time()) + settings.resume_seconds
    grant_restart(ledger, expires)
    return ledger, {
        "initialized": created,
        "source_id": source_id,
        "source_reimported": reimported,
        "policy_changed": changed,
        "restart_expires": expires,
        "network_started": False,
    }
