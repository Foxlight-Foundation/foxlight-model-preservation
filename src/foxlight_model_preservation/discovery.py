"""Standalone followed sources, stock TUF updates and offline local search.

Only the explicit refresh function accesses endpoints. Catalog keys have discovery
scope; selecting/fetching a model still requires independently supplied bundle
trust and an exact manifest. No query, inventory, advertisement or weight upload
is part of this module.
"""

from __future__ import annotations

import base64
import os
import re
import tempfile
import time
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TypeVar
from urllib.parse import urlsplit

from pydantic import JsonValue
from tuf.api.exceptions import (
    BadVersionNumberError,
    DownloadError,
    DownloadHTTPError,
    ExpiredMetadataError,
    RepositoryError,
    UnsignedMetadataError,
)
from tuf.api.metadata import (
    Metadata,
    Root,  # pyright: ignore[reportPrivateImportUsage]
    Snapshot,  # pyright: ignore[reportPrivateImportUsage]
    Targets,  # pyright: ignore[reportPrivateImportUsage]
    Timestamp,  # pyright: ignore[reportPrivateImportUsage]
)
from tuf.ngclient import Updater, UpdaterConfig
from tuf.ngclient.fetcher import FetcherInterface

from .discovery_models import (
    MAX_CATALOG_BYTES,
    MAX_ROLE_BYTES,
    MAX_ROOT_BYTES,
    MAX_ROOTS,
    MAX_SOURCES,
    MAX_STATE_BYTES,
    DiscoveryCacheBundle,
    DiscoveryCatalogTarget,
    SearchLocation,
    SearchResult,
    SourceBootstrap,
    SourceState,
    SourceStatus,
)
from .models import (
    MAX_METADATA_BYTES,
    Catalog,
    CatalogEntry,
    Manifest,
    PreservationError,
    TrustProfile,
)
from .operations import OperationControl, OperationError
from .signing import canonical_bytes, decode_base64, digest, encode_record, strict_json
from .signing import verify_catalog as verify_signed_catalog
from .storage import atomic_bytes, directory, open_file, writer_lock
from .transfer import (
    DEFAULT_POLICY,
    MirrorHTTPError,
    MirrorProtocolError,
    TransferPolicy,
    fetch,
    response,
)

T = TypeVar("T", Root, Timestamp, Snapshot, Targets)
ROLES = ("timestamp", "snapshot", "targets")
VIRTUAL_BASE = "https://discovery.invalid/"


class DiscoveryControl(Protocol):
    """A cancellation/progress fence owned by the standalone or runtime caller."""

    def checkpoint(self) -> None:
        """Stop before the next effect if the caller has cancelled."""
        ...

    def advance(
        self, *, hashed: int = 0, copied: int = 0, received: int = 0, reused: int = 0
    ) -> None:
        """Count bounded work while preserving the cancellation fence."""
        ...

    def mark_committed(self) -> None:
        """Acknowledge durable publication of one source's accepted cache."""
        ...


def _json(data: bytes, *, maximum_bytes: int = MAX_METADATA_BYTES) -> JsonValue:
    value = strict_json(data, maximum_bytes=maximum_bytes)
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 64 or nodes > 200_000:
            raise PreservationError("discovery JSON exceeds structural limits")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    return value


def parse_discovery_role(data: bytes, kind: type[T]) -> Metadata[T]:
    """Parse a bounded role; parsing alone establishes no signature authority."""
    if len(data) > (MAX_ROOT_BYTES if kind is Root else MAX_ROLE_BYTES):
        raise PreservationError("discovery role exceeds its size bound")
    value = _json(data)
    if not isinstance(value, dict):
        raise PreservationError("TUF metadata must be an object")
    signed = value.get("signed")
    if not isinstance(signed, dict) or signed.get("_type") != kind.type:
        raise PreservationError("discovery metadata has the wrong TUF role")
    result = Metadata[T].from_bytes(data)
    if not 1 <= result.signed.version <= 2**53 - 1:
        raise PreservationError("TUF version exceeds supported bounds")
    return result


def _encoded(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _root(bootstrap: SourceBootstrap) -> Metadata[Root]:
    result = parse_discovery_role(decode_base64(bootstrap.root), Root)
    result.signed.verify_delegate("root", result.signed_bytes, result.signatures)
    return result


def source_identity(bootstrap: SourceBootstrap) -> str:
    """Return the stable initial-root identity, independent of endpoint hints."""
    _root(bootstrap)
    return digest(decode_base64(bootstrap.root))


def bootstrap_identity(bootstrap: SourceBootstrap) -> str:
    """Identify the whole source file for independent first-contact authentication."""
    _root(bootstrap)
    return digest(encode_record(bootstrap))


def read_source_document(data: bytes) -> SourceBootstrap | DiscoveryCacheBundle:
    """Inspect a bounded source/cache document offline without enrolling its keys."""
    try:
        return _read_source_document(data)
    except RepositoryError as error:
        raise PreservationError(
            "source/cache signed evidence failed verification"
        ) from error


def _read_source_document(data: bytes) -> SourceBootstrap | DiscoveryCacheBundle:
    if len(data) > MAX_STATE_BYTES:
        raise PreservationError("source document exceeds its size bound")
    value = _json(data, maximum_bytes=MAX_STATE_BYTES)
    if (
        isinstance(value, dict)
        and value.get("schema_version") == "foxlight.discovery-cache.v1"
    ):
        bundle = DiscoveryCacheBundle.model_validate_json(canonical_bytes(value))
        _verify_cached(_bundle_state(bundle))
        return bundle
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "foxlight.discovery-source.v1"
    ):
        raise PreservationError("unsupported or missing discovery source version")
    bootstrap = SourceBootstrap.model_validate_json(canonical_bytes(value))
    _root(bootstrap)
    return bootstrap


def inspect_source(data: bytes) -> dict[str, JsonValue]:
    """Describe candidate identities offline without enrolling or refreshing."""
    record = read_source_document(data)
    bootstrap = record if isinstance(record, SourceBootstrap) else record.bootstrap
    root = _root(bootstrap)
    return {
        "source_id": source_identity(bootstrap),
        "bootstrap_id": bootstrap_identity(bootstrap),
        "name": bootstrap.name,
        "endpoints": list(bootstrap.endpoints),
        "root_version": root.signed.version,
        "candidate_root_keys": list(root.signed.roles["root"].keyids),
        "root_threshold": root.signed.roles["root"].threshold,
        "cached": isinstance(record, DiscoveryCacheBundle),
        "authority": "discovery-only; no bundle trust or execution permission",
    }


@contextmanager
def _cache(path: Path, *, create: bool = False) -> Generator[int]:
    with directory(path, create=create) as root:
        info = os.fstat(root)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PreservationError("discovery cache must be an owner-only directory")
        yield root


def _name(source_id: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", source_id) is None:
        raise PreservationError("expected a complete source SHA-256 identity")
    return source_id + ".json"


def _identities(root: int) -> list[str]:
    return sorted(
        name[:-5]
        for name in os.listdir(root)
        if re.fullmatch(r"[0-9a-f]{64}\.json", name)
    )


def _read(root: int, source_id: str) -> SourceState:
    with open_file(root, _name(source_id)) as stream:
        raw = stream.read(MAX_STATE_BYTES + 1)
    if len(raw) > MAX_STATE_BYTES:
        raise PreservationError("source state exceeds the cache bound")
    value = _json(raw, maximum_bytes=MAX_STATE_BYTES)
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "foxlight.discovery-state.v1"
    ):
        raise PreservationError(
            "unsupported source cache version; retain it for an upgraded reader"
        )
    state = SourceState.model_validate_json(canonical_bytes(value))
    if source_identity(state.bootstrap) != source_id:
        raise PreservationError("source cache identity mismatch")
    _roots(state)
    return state


def _save(root: int, source_id: str, state: SourceState) -> None:
    data = encode_record(state)
    if len(data) > MAX_STATE_BYTES:
        raise PreservationError("source cache exceeds its reserved size bound")
    atomic_bytes(root, _name(source_id), data)


def _roots(state: SourceState) -> list[Metadata[Root]]:
    roots = [_root(state.bootstrap)]
    for encoded in state.roots:
        successor = parse_discovery_role(decode_base64(encoded), Root)
        if successor.signed.version != roots[-1].signed.version + 1:
            raise PreservationError("source root rotation is not sequential")
        roots[-1].signed.verify_delegate(
            "root", successor.signed_bytes, successor.signatures
        )
        successor.signed.verify_delegate(
            "root", successor.signed_bytes, successor.signatures
        )
        roots.append(successor)
    return roots


def _verify_cached(
    state: SourceState,
) -> tuple[Catalog, DiscoveryCatalogTarget, datetime]:
    if state.catalog is None or state.accepted_root_version is None:
        raise PreservationError("source has no accepted catalog")
    roots = _roots(state)
    selected = next(
        (r for r in roots if r.signed.version == state.accepted_root_version), None
    )
    if selected is None or set(state.accepted_metadata) != set(ROLES):
        raise PreservationError("cache omits its authenticated root/role closure")
    timestamp_bytes = decode_base64(state.accepted_metadata["timestamp"])
    snapshot_bytes = decode_base64(state.accepted_metadata["snapshot"])
    targets_bytes = decode_base64(state.accepted_metadata["targets"])
    timestamp = parse_discovery_role(timestamp_bytes, Timestamp)
    snapshot = parse_discovery_role(snapshot_bytes, Snapshot)
    targets = parse_discovery_role(targets_bytes, Targets)
    for role, metadata in (
        ("timestamp", timestamp),
        ("snapshot", snapshot),
        ("targets", targets),
    ):
        selected.signed.verify_delegate(
            role, metadata.signed_bytes, metadata.signatures
        )
    snapshot_meta = timestamp.signed.snapshot_meta
    targets_meta = snapshot.signed.meta.get("targets.json")
    if targets_meta is None:
        raise PreservationError("snapshot omits discovery targets")
    for meta, raw, version in (
        (snapshot_meta, snapshot_bytes, snapshot.signed.version),
        (targets_meta, targets_bytes, targets.signed.version),
    ):
        if (
            meta.version != version
            or not meta.hashes
            or "sha256" not in meta.hashes
            or meta.length is None
        ):
            raise PreservationError(
                "discovery role requires exact version/length/SHA-256"
            )
        meta.verify_length_and_hashes(raw)
    target = targets.signed.targets.get("catalog.json")
    raw = decode_base64(state.catalog)
    if (
        target is None
        or target.length > MAX_CATALOG_BYTES
        or "sha256" not in target.hashes
    ):
        raise PreservationError("catalog target requires bounded length/SHA-256")
    target.verify_length_and_hashes(raw)
    custom_value = _json(canonical_bytes(target.custom))
    if (
        not isinstance(custom_value, dict)
        or custom_value.get("schema_version") != "foxlight.discovery-catalog.v1"
    ):
        raise PreservationError("unsupported or missing discovery catalog profile")
    custom = DiscoveryCatalogTarget.model_validate_json(canonical_bytes(custom_value))
    catalog = validate_catalog_target(raw, custom)
    expires = min(r.signed.expires for r in (selected, timestamp, snapshot, targets))
    return catalog, custom, expires


def validate_catalog_target(raw: bytes, custom: DiscoveryCatalogTarget) -> Catalog:
    """Verify catalog-domain signatures and exact optional description bindings."""
    if len(raw) > MAX_CATALOG_BYTES:
        raise PreservationError("discovery catalog exceeds its byte bound")
    catalog = verify_signed_catalog(raw, custom.catalog_trust, custom.catalog_id)
    entries = {entry.entry_id: entry.manifest for entry in catalog.entries}
    described: set[str] = set()
    for description in custom.descriptions:
        if (
            description.entry_id in described
            or entries.get(description.entry_id) != description.manifest
        ):
            raise PreservationError(
                "discovery description is not bound to its catalog entry"
            )
        described.add(description.entry_id)
    return catalog


def _bundle_state(bundle: DiscoveryCacheBundle) -> SourceState:
    return SourceState(
        bootstrap=bundle.bootstrap,
        roots=bundle.roots,
        metadata=bundle.metadata,
        accepted_metadata=bundle.metadata,
        accepted_root_version=bundle.accepted_root_version,
        catalog=bundle.catalog,
        imported=True,
    )


def _status(source_id: str, state: SourceState) -> SourceStatus:
    catalog_id = None
    expires = None
    freshness = "empty"
    roots = _roots(state)
    if state.catalog is not None:
        _, custom, expiry = _verify_cached(state)
        catalog_id, expires = custom.catalog_id, expiry.isoformat()
        freshness = (
            "superseded-root"
            if roots[-1].signed.version != state.accepted_root_version
            else "stale"
            if expiry <= datetime.now(UTC)
            else "imported"
            if state.imported
            else "retained"
            if state.last_error is not None
            else "fresh"
        )
    return SourceStatus(
        source_id=source_id,
        name=state.bootstrap.name,
        enabled=state.enabled,
        bootstrap_id=bootstrap_identity(state.bootstrap),
        root_version=roots[-1].signed.version,
        catalog_id=catalog_id,
        accepted_at=state.accepted_at,
        expires=expires,
        freshness=freshness,
        last_attempt_at=state.last_attempt_at,
        last_error=state.last_error,
    )


def follow_source(
    cache: Path,
    document: bytes,
    expected_bootstrap_id: str,
    *,
    allow_private: bool = False,
    allow_http: bool = False,
    allow_local: bool = False,
) -> SourceStatus:
    """Enroll an independently authenticated source, without connecting or fetching.

    Re-enrollment retains update floors and prior cache. Offline bundles are
    verified as historical evidence and never labeled as a successful refresh.
    """
    record = read_source_document(document)
    bootstrap = record if isinstance(record, SourceBootstrap) else record.bootstrap
    if bootstrap_identity(bootstrap) != expected_bootstrap_id:
        raise PreservationError(
            "source file differs from independently selected identity"
        )
    source_id = source_identity(bootstrap)
    incoming = (
        _bundle_state(record)
        if isinstance(record, DiscoveryCacheBundle)
        else SourceState(bootstrap=bootstrap)
    )
    with _cache(cache, create=True) as root, writer_lock(root):
        identities = _identities(root)
        if source_id in identities:
            state = _read(root, source_id)
            if isinstance(record, DiscoveryCacheBundle):
                # Import cannot rewind a previously followed source, even after removal.
                old_roots, new_roots = _roots(state), _roots(incoming)
                if len(new_roots) < len(old_roots):
                    raise PreservationError(
                        "cache import would roll back trusted roots"
                    )
                for previous, newer in zip(old_roots, new_roots, strict=False):
                    if previous.to_dict() != newer.to_dict():
                        raise PreservationError(
                            "cache import has conflicting trusted roots"
                        )
                # A higher role version cannot revive authority revoked by a
                # successor root, including one learned before a target failed.
                # A newly supplied successor also takes effect before imported
                # catalog replacement. First-contact historical snapshots remain
                # explicitly historical; they cannot later lower this floor.
                authority_floor = max(
                    old_roots[-1].signed.version, new_roots[-1].signed.version
                )
                if (
                    incoming.accepted_root_version is None
                    or incoming.accepted_root_version < authority_floor
                ):
                    raise PreservationError(
                        "cache import would roll back trusted root authority"
                    )
                for role in ROLES:
                    if role in state.metadata:
                        old = _json(decode_base64(state.metadata[role]))
                        new = _json(decode_base64(incoming.metadata.get(role, "")))
                        if not isinstance(old, dict) or not isinstance(new, dict):
                            raise PreservationError("cache import omits update floors")
                        old_signed, new_signed = old["signed"], new["signed"]
                        if (
                            not isinstance(old_signed, dict)
                            or not isinstance(new_signed, dict)
                            or not isinstance(new_signed.get("version"), int)
                            or not isinstance(old_signed.get("version"), int)
                        ):
                            raise PreservationError(
                                "cache import has invalid update floors"
                            )
                        new_version, old_version = (
                            new_signed["version"],
                            old_signed["version"],
                        )
                        if not isinstance(new_version, int) or not isinstance(
                            old_version, int
                        ):
                            raise PreservationError("invalid metadata version")
                        if new_version < old_version:
                            raise PreservationError(
                                "cache import would roll back metadata"
                            )
                        if (
                            new_signed["version"] == old_signed["version"]
                            and new_signed != old_signed
                        ):
                            raise PreservationError(
                                "cache import conflicts at an accepted version"
                            )
                state = incoming
        else:
            if len(identities) >= MAX_SOURCES:
                raise PreservationError(
                    "discovery source/cache reservation limit reached"
                )
            state = incoming
        state = state.model_copy(
            update={
                "bootstrap": bootstrap,
                "enabled": True,
                "allow_private": allow_private,
                "allow_http": allow_http,
                "allow_local": allow_local,
            }
        )
        _save(root, source_id, state)
        return _status(source_id, state)


def list_sources(cache: Path) -> tuple[SourceStatus, ...]:
    """Read cached identity/status only; no endpoint connection or implicit refresh."""
    if not cache.exists():
        return ()
    with _cache(cache) as root:
        statuses: list[SourceStatus] = []
        for identity in _identities(root):
            try:
                statuses.append(_status(identity, _read(root, identity)))
            except (ValueError, OSError, RepositoryError):
                statuses.append(
                    SourceStatus(
                        source_id=identity,
                        name="Unreadable source",
                        enabled=False,
                        bootstrap_id=None,
                        root_version=None,
                        catalog_id=None,
                        accepted_at=None,
                        expires=None,
                        freshness="invalid",
                        last_attempt_at=None,
                        last_error="source cache failed verification",
                    )
                )
        return tuple(statuses)


def remove_source(cache: Path, source_id: str) -> None:
    """Stop following without deleting update floors, archives or installed models."""
    with _cache(cache) as root, writer_lock(root):
        state = _read(root, source_id).model_copy(update={"enabled": False})
        _save(root, source_id, state)


def export_source(cache: Path, source_id: str) -> bytes:
    """Export bootstrap plus accepted signed cache, excluding local policy/errors."""
    with _cache(cache) as root:
        state = _read(root, source_id)
    if state.catalog is None:
        return encode_record(state.bootstrap)
    _verify_cached(state)
    if state.accepted_root_version is None:
        raise PreservationError("cache omits accepted root version")
    return encode_record(
        DiscoveryCacheBundle(
            bootstrap=state.bootstrap,
            roots=state.roots,
            metadata=state.accepted_metadata,
            accepted_root_version=state.accepted_root_version,
            catalog=state.catalog,
        )
    )


@dataclass
class _Budget:
    deadline: float
    bytes_left: int = 32 * 1024 * 1024
    requests_left: int = 128


class _Fetcher(FetcherInterface):
    def __init__(
        self,
        endpoint: str,
        state: SourceState,
        budget: _Budget,
        control: DiscoveryControl,
    ):
        self.endpoint, self.state, self.budget, self.control = (
            endpoint,
            state,
            budget,
            control,
        )

    def _fetch(self, url: str) -> Iterator[bytes]:
        if not url.startswith(VIRTUAL_BASE):
            raise PreservationError("TUF requested an unconfigured metadata source")
        relative = url[len(VIRTUAL_BASE) :]
        if not re.fullmatch(
            r"(?:metadata/(?:[1-9][0-9]{0,15}\.)?(?:root|timestamp|snapshot|targets)\.json|targets/(?:[0-9a-f]{64}\.)?catalog\.json)",
            relative,
        ):
            raise PreservationError("unsupported discovery metadata path/delegation")
        self.control.checkpoint()
        remaining = self.budget.deadline - time.monotonic()
        if (
            remaining <= 0
            or self.budget.requests_left <= 0
            or self.budget.bytes_left <= 0
        ):
            raise PreservationError("discovery metadata deadline/budget exhausted")
        self.budget.requests_left -= 1
        cap = (
            MAX_ROOT_BYTES
            if relative.endswith("root.json")
            else MAX_CATALOG_BYTES
            if relative.startswith("targets/")
            else MAX_ROLE_BYTES
        )
        data = bytearray()
        if "://" not in self.endpoint:
            if not self.state.allow_local:
                raise PreservationError(
                    "local metadata sources require explicit permission"
                )
            try:
                with (
                    directory(Path(self.endpoint)) as root,
                    open_file(root, relative) as stream,
                ):
                    expected = os.fstat(stream.fileno()).st_size
                    if expected > cap or expected > self.budget.bytes_left:
                        raise PreservationError("local metadata exceeds its byte bound")
                    data.extend(stream.read(min(cap + 1, self.budget.bytes_left)))
                    if len(data) != expected:
                        raise PreservationError("local metadata changed while reading")
            except FileNotFoundError as error:
                raise DownloadHTTPError("metadata not found", 404) from error
            self.budget.bytes_left -= len(data)
        else:
            parsed = urlsplit(self.endpoint)
            if parsed.scheme not in {"https", "http"}:
                raise PreservationError("metadata source must use HTTPS")
            policy = TransferPolicy(
                timeout=min(10.0, remaining),
                allow_http=self.state.allow_http,
                allow_private=self.state.allow_private,
            )
            try:
                with response(self.endpoint, relative, policy) as incoming:
                    if incoming.status != 200:
                        raise PreservationError(
                            "partial discovery metadata is not accepted"
                        )
                    while len(data) <= cap:
                        self.control.checkpoint()
                        if (
                            time.monotonic() >= self.budget.deadline
                            or self.budget.bytes_left <= 0
                        ):
                            raise PreservationError(
                                "discovery metadata deadline/budget exhausted"
                            )
                        chunk = incoming.read1(
                            min(65536, cap + 1 - len(data), self.budget.bytes_left)
                        )
                        if not chunk:
                            break
                        self.budget.bytes_left -= len(chunk)
                        data.extend(chunk)
                        self.control.advance(received=len(chunk))
            except MirrorHTTPError as error:
                raise DownloadHTTPError(
                    "metadata HTTP request failed", error.status_code
                ) from error
            except MirrorProtocolError as error:
                raise DownloadError("metadata HTTP response framing failed") from error
        if len(data) > cap or self.budget.bytes_left < 0:
            raise PreservationError("discovery metadata exceeds its byte bound")
        _json(bytes(data))
        if relative.startswith("metadata/"):
            if relative.endswith("root.json"):
                parse_discovery_role(bytes(data), Root)
            elif relative.endswith("timestamp.json"):
                parse_discovery_role(bytes(data), Timestamp)
            elif relative.endswith("snapshot.json"):
                parse_discovery_role(bytes(data), Snapshot)
            else:
                parse_discovery_role(bytes(data), Targets)
        yield bytes(data)


def _stage_metadata(stage: Path, state: SourceState) -> None:
    roots = _roots(state)
    for index, encoded in enumerate((state.bootstrap.root, *state.roots)):
        root = roots[index]
        history = stage / "root_history"
        history.mkdir(exist_ok=True)
        (history / f"{root.signed.version}.root.json").write_bytes(
            decode_base64(encoded)
        )
    for role, encoded in state.metadata.items():
        if role not in ROLES:
            raise PreservationError("unknown cached TUF role")
        (stage / (role + ".json")).write_bytes(decode_base64(encoded))


def _collect_metadata(stage: Path, state: SourceState) -> SourceState:
    # These files were written by the stock updater only after verification.
    roots = list(state.roots)
    initial = _root(state.bootstrap).signed.version
    for version in range(initial + len(roots) + 1, initial + MAX_ROOTS + 1):
        path = stage / "root_history" / f"{version}.root.json"
        if not path.exists():
            break
        raw = path.read_bytes()
        parse_discovery_role(raw, Root)
        roots.append(_encoded(raw))
    roles = dict(state.metadata)
    for role in ROLES:
        path = stage / (role + ".json")
        if path.exists():
            raw = path.read_bytes()
            if len(raw) > MAX_ROLE_BYTES:
                raise PreservationError("cached TUF role exceeds bound")
            roles[role] = _encoded(raw)
    result = state.model_copy(update={"roots": tuple(roots), "metadata": roles})
    _roots(result)
    return result


def refresh_source(
    cache: Path, source_id: str, *, control: DiscoveryControl | None = None
) -> SourceStatus:
    """Refresh with stock TUF and bounded endpoint failover; preserve last-good cache.

    Verified update metadata is retained even if a later step fails. No model
    weights, source recommendations, bundle keys or executable code are fetched.
    """
    return _refresh_source(cache, source_id, control=control)


def refresh_source_from_directory(
    cache: Path,
    source_id: str,
    metadata_directory: Path,
    *,
    control: DiscoveryControl | None = None,
) -> SourceStatus:
    """Verify privately staged peer metadata with the same stock TUF updater/floors.

    This explicit local transport never changes a source's endpoint hints or
    persisted filesystem/network permissions. The caller owns the staged tree;
    source metadata cannot choose a filesystem path.
    """
    return _refresh_source(
        cache,
        source_id,
        control=control,
        metadata_directory=metadata_directory.resolve(strict=True),
    )


def _refresh_source(
    cache: Path,
    source_id: str,
    *,
    control: DiscoveryControl | None = None,
    metadata_directory: Path | None = None,
) -> SourceStatus:
    control = control or OperationControl()
    budget = _Budget(time.monotonic() + 120)
    with _cache(cache) as root, writer_lock(root):
        state = _read(root, source_id)
        if not state.enabled:
            raise PreservationError("source is no longer followed")
        if len(state.roots) >= MAX_ROOTS:
            raise PreservationError(
                "source root-history bound reached; retain/export evidence "
                "and review an upgraded reader"
            )
        now = datetime.now(UTC).isoformat()
        if state.last_attempt_at is not None and now < state.last_attempt_at:
            raise PreservationError(
                "clock moved backwards since the last source update"
            )
        state = state.model_copy(update={"last_attempt_at": now})
        last_error: Exception | None = None
        endpoints = (
            (str(metadata_directory),)
            if metadata_directory is not None
            else state.bootstrap.endpoints
        )
        for endpoint in endpoints:
            control.checkpoint()
            with tempfile.TemporaryDirectory(prefix="model-discovery-") as temporary:
                stage = Path(temporary)
                _stage_metadata(stage, state)
                accepted = False
                try:
                    updater = Updater(
                        str(stage),
                        VIRTUAL_BASE + "metadata/",
                        target_base_url=VIRTUAL_BASE + "targets/",
                        fetcher=_Fetcher(
                            endpoint,
                            state
                            if metadata_directory is None
                            else state.model_copy(update={"allow_local": True}),
                            budget,
                            control,
                        ),
                        bootstrap=decode_base64(
                            (state.bootstrap.root, *state.roots)[-1]
                        ),
                        config=UpdaterConfig(
                            max_root_rotations=min(32, MAX_ROOTS - len(state.roots)),
                            max_delegations=0,
                            root_max_length=MAX_ROOT_BYTES,
                            timestamp_max_length=MAX_ROLE_BYTES,
                            snapshot_max_length=MAX_ROLE_BYTES,
                            targets_max_length=MAX_ROLE_BYTES,
                        ),
                    )
                    updater.refresh()
                    target = updater.get_targetinfo("catalog.json")
                    if (
                        target is None
                        or target.length > MAX_CATALOG_BYTES
                        or "sha256" not in target.hashes
                    ):
                        raise PreservationError(
                            "source omits a bounded SHA-256 catalog target"
                        )
                    output = stage / "downloaded-catalog.json"
                    updater.download_target(target, str(output))
                    updated = _collect_metadata(stage, state)
                    candidate = updated.model_copy(
                        update={
                            "catalog": _encoded(output.read_bytes()),
                            "accepted_metadata": updated.metadata,
                            "accepted_root_version": _roots(updated)[-1].signed.version,
                            "accepted_at": now,
                            "last_error": None,
                            "imported": False,
                        }
                    )
                    _verify_cached(candidate)
                    control.checkpoint()
                    _save(root, source_id, candidate)
                    control.mark_committed()
                    accepted = True
                    return _status(source_id, candidate)
                except OperationError:
                    raise
                except (
                    RepositoryError,
                    DownloadError,
                    ValueError,
                    OSError,
                ) as error:
                    last_error = error
                finally:
                    # Root/role floors survive target rejection, cancellation and
                    # mirror failover. Accepted catalog bytes remain untouched.
                    if not accepted:
                        state = _collect_metadata(stage, state)
                        state = state.model_copy(
                            update={"last_error": _refresh_error(last_error)}
                        )
                        _save(root, source_id, state)
        raise PreservationError(
            "source refresh failed; retained last accepted catalog"
        ) from last_error


def _refresh_error(error: Exception | None) -> str:
    if isinstance(error, ExpiredMetadataError):
        reason = "signed metadata expired"
    elif isinstance(error, BadVersionNumberError):
        reason = "metadata rollback rejected"
    elif isinstance(error, UnsignedMetadataError):
        reason = "metadata signature rejected"
    elif isinstance(error, DownloadError):
        reason = "metadata download failed or was not permitted"
    elif error is None:
        reason = "metadata update stopped"
    else:
        reason = "catalog or metadata rejected"
    return reason + "; retained last accepted catalog"


def search_sources(
    cache: Path, query: str, *, limit: int = 100
) -> tuple[SearchResult, ...]:
    """Search locally, grouping exact manifests while retaining source statements."""
    if len(query) > 500 or not 1 <= limit <= 1000:
        raise PreservationError("search query/result limit exceeds bounds")
    if not cache.exists():
        return ()
    words = query.casefold().split()
    matches: dict[str, list[SearchLocation]] = {}
    matching: set[str] = set()
    with _cache(cache) as root:
        for source_id in _identities(root):
            try:
                state = _read(root, source_id)
                if not state.enabled or state.catalog is None:
                    continue
                catalog, custom, _ = _verify_cached(state)
                status = _status(source_id, state)
            except (ValueError, OSError, RepositoryError):
                # list_sources retains a specific invalid-cache diagnostic.
                continue
            descriptions = {item.entry_id: item for item in custom.descriptions}
            for entry in catalog.entries:
                description = descriptions.get(entry.entry_id)
                terms = [entry.entry_id, entry.manifest, catalog.description]
                if description is not None:
                    terms.extend(
                        (
                            description.name,
                            description.summary,
                            *description.workloads,
                            *description.formats,
                            *description.licenses,
                        )
                    )
                haystack = " ".join(terms).casefold()
                if all(word in haystack for word in words):
                    matching.add(entry.manifest)
                matches.setdefault(entry.manifest, []).append(
                    SearchLocation(
                        source_id=source_id,
                        source_name=state.bootstrap.name,
                        entry_id=entry.entry_id,
                        mirrors=entry.mirrors,
                        freshness=status.freshness,
                        accepted_at=state.accepted_at,
                        description=description,
                    )
                )
    return tuple(
        SearchResult(manifest=manifest, locations=tuple(matches[manifest]))
        for manifest in sorted(matching)[:limit]
    )


def select_entry(
    cache: Path, source_id: str, entry_id: str, expected_manifest: str
) -> CatalogEntry:
    """Pin an exact cached selection; source updates cannot silently change it."""
    with _cache(cache) as root:
        state = _read(root, source_id)
    if not state.enabled:
        raise PreservationError("source is no longer followed")
    catalog, _, _ = _verify_cached(state)
    selected = next(
        (entry for entry in catalog.entries if entry.entry_id == entry_id), None
    )
    if selected is None or selected.manifest != expected_manifest:
        raise PreservationError(
            "selected catalog entry changed; review a new exact selection"
        )
    return selected


def fetch_selection(
    cache: Path,
    source_id: str,
    entry_id: str,
    expected_manifest: str,
    bundle_trust: TrustProfile,
    store: Path,
    policy: TransferPolicy = DEFAULT_POLICY,
    *,
    allow_local: bool = False,
    control: OperationControl | None = None,
    operation_id: str | None = None,
) -> Manifest:
    """Fetch one exact selection with independent bundle trust and receive policy.

    Local-file hints need their own opt-in; following a local metadata source
    does not grant access to model files. Candidate keys in descriptions are
    never substituted for this explicitly supplied trust profile.
    """
    entry = select_entry(cache, source_id, entry_id, expected_manifest)
    mirrors = tuple(
        mirror
        for mirror in entry.mirrors
        if "://" in mirror or (allow_local and Path(mirror).is_absolute())
    )
    if not mirrors:
        raise PreservationError("local file mirrors need separate explicit permission")
    return fetch(
        entry.manifest,
        bundle_trust,
        mirrors,
        store,
        policy,
        control=control,
        operation_id=operation_id,
    )
