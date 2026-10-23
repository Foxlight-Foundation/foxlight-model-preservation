"""Portable peer source hints and verified metadata/model transfer over IPFS."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from tuf.api.metadata import Root, Snapshot, Targets, Timestamp  # pyright: ignore[reportPrivateImportUsage]

from .discovery import (
    bootstrap_identity,
    export_source,
    follow_source,
    list_sources,
    parse_discovery_role,
    read_source_document,
    refresh_source_from_directory,
    select_entry,
    source_identity,
)
from .discovery_models import (
    MAX_SOURCES,
    MAX_STATE_BYTES,
    DiscoveryCacheBundle,
    SourceBootstrap,
    SourceStatus,
)
from .models import (
    MAX_METADATA_BYTES,
    FileIdentity,
    Manifest,
    PreservationError,
    Record,
    TrustProfile,
)
from .peer_runtime import PeerClient, content_id
from .signing import (
    canonical_bytes,
    decode_base64,
    digest,
    encode_record,
    strict_json,
    verify_manifest,
)
from .storage import (
    CHUNK_SIZE,
    atomic_bytes,
    copy_verified_object,
    directory,
    file_identity,
    manifest_path,
    object_path,
    observe_objects,
    open_file,
    read_metadata,
    unique_objects,
    verify_store_contents,
    writer_lock,
)
from .transfer import TransferPolicy, fetch


class PeerSource(Record):
    """First-contact pin for an IPNS hint, with a separate immutable TUF authority."""

    schema_version: Literal["foxlight.peer-source.v1"] = "foxlight.peer-source.v1"
    source: SourceBootstrap
    ipns: str = Field(min_length=1, max_length=200)

    snapshots: tuple[str, ...] = Field(default=(), max_length=8)

    @field_validator("snapshots")
    @classmethod
    def valid_snapshots(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Retain bounded immutable fallback snapshots, not imported permissions."""
        if len(set(values)) != len(values):
            raise PreservationError("snapshot hints must be unique")
        return tuple(content_id(value) for value in values)

    @field_validator("ipns")
    @classmethod
    def valid_name(cls, value: str) -> str:
        """Require a canonical key name, never DNSLink, URLs or arbitrary paths."""
        return content_id(value, name=True)


def inspect_peer_source(data: bytes) -> PeerSource:
    """Inspect bounded portable peer metadata offline; no network or enrollment."""
    if len(data) > 256 * 1024:
        raise PreservationError("peer source file exceeds 256 KiB")
    # Duplicate keys are rejected, as for discovery bootstraps, so a reviewer
    # and this parser cannot read different values from one pinned file.
    record = PeerSource.model_validate_json(
        canonical_bytes(strict_json(data, maximum_bytes=256 * 1024))
    )
    source_identity(record.source)
    return record


def peer_source_identity(record: PeerSource) -> str:
    """Pin both the source authority and transport name through a trusted channel."""
    return digest(encode_record(record))


def _profile_path(cache: Path, source: str) -> Path:
    if re.fullmatch(r"[0-9a-f]{64}", source) is None:
        raise PreservationError("expected complete source identity")
    return cache / "peer-sources" / (source + ".json")


def follow_peer_source(cache: Path, data: bytes, expected_peer_id: str) -> SourceStatus:
    """Enroll only an explicitly pinned file; following does not start peer traffic."""
    record = inspect_peer_source(data)
    if peer_source_identity(record) != expected_peer_id:
        raise PreservationError(
            "peer source differs from independently selected identity"
        )
    source = source_identity(record.source)
    path = _profile_path(cache, source)
    # Ordinary source following retains all existing verifier floors and the
    # operator's receiver grants; the peer hint cannot enroll bundle keys or
    # grant (or silently revoke) HTTP/private/local-file access.
    status = follow_source(
        cache, encode_record(record.source), bootstrap_identity(record.source)
    )
    with directory(path.parent, create=True) as root, writer_lock(root):
        if (
            len([name for name in os.listdir(root) if name.endswith(".json")])
            >= MAX_SOURCES
            and not path.exists()
        ):
            raise PreservationError("peer source reservation bound reached")
        atomic_bytes(root, path.name, encode_record(record))
    return status


def read_peer_source(cache: Path, source: str) -> PeerSource:
    """Read a private enrolled hint without reaching an IPNS resolver."""
    path = _profile_path(cache, source)
    with directory(path.parent) as root, open_file(root, path.name) as stream:
        data = stream.read(256 * 1024 + 1)
    record = inspect_peer_source(data)
    if source_identity(record.source) != source:
        raise PreservationError("peer source binding changed")
    return record


def _stage_snapshot(data: bytes, record: PeerSource, target: Path) -> None:
    bundle = read_source_document(data)
    if not isinstance(bundle, DiscoveryCacheBundle) or source_identity(
        bundle.bootstrap
    ) != source_identity(record.source):
        raise PreservationError("peer snapshot differs from enrolled source authority")
    # Historical evidence is validated above, but cannot become fresh here.
    # The normal Updater below still checks current expiry and retained floors.
    with directory(target, create=True) as root:
        for encoded in (bundle.bootstrap.root, *bundle.roots):
            raw = decode_base64(encoded)
            metadata = parse_discovery_role(raw, Root)
            atomic_bytes(root, f"metadata/{metadata.signed.version}.root.json", raw)
        versions = {
            "timestamp": parse_discovery_role(
                decode_base64(bundle.metadata["timestamp"]), Timestamp
            ).signed.version,
            "snapshot": parse_discovery_role(
                decode_base64(bundle.metadata["snapshot"]), Snapshot
            ).signed.version,
            "targets": parse_discovery_role(
                decode_base64(bundle.metadata["targets"]), Targets
            ).signed.version,
        }
        for role, version in versions.items():
            raw = decode_base64(bundle.metadata[role])
            atomic_bytes(root, f"metadata/{role}.json", raw)
            atomic_bytes(root, f"metadata/{version}.{role}.json", raw)
        catalog = decode_base64(bundle.catalog)
        atomic_bytes(root, "targets/catalog.json", catalog)
        atomic_bytes(root, "targets/" + digest(catalog) + ".catalog.json", catalog)


def refresh_peer_source(
    cache: Path,
    source: str,
    client: PeerClient,
    *,
    historical: bool = False,
) -> SourceStatus:
    """Refresh through peers; historical pinned snapshots require a separate choice.

    IPNS/DHT locations are hints. TUF still authenticates every fresh update.
    Historical fallback imports only a snapshot pinned in the trusted source
    file, remains labeled historical, and cannot lower retained authority floors.
    """
    record = read_peer_source(cache, source)
    if not any(
        item.source_id == source and item.enabled for item in list_sources(cache)
    ):
        raise PreservationError("peer source is no longer followed")
    candidates = list(record.snapshots)
    if not historical:
        try:
            resolved = (
                client.command(
                    "name",
                    "resolve",
                    "--nocache",
                    "--recursive=false",
                    "/ipns/" + record.ipns,
                    maximum=512,
                )
                .decode()
                .strip()
            )
            match = re.fullmatch(r"/ipfs/([^/]+)", resolved)
            if match is None:
                raise PreservationError("IPNS must resolve to an exact immutable root")
            cid = content_id(match[1])
            candidates = [cid, *(item for item in candidates if item != cid)]
        except (ValueError, OSError):
            client.control.checkpoint()
    if not candidates:
        raise PreservationError("no peer source name or pinned snapshot is available")
    before = client.exported_bytes
    last_error: Exception | None = None
    for cid in candidates:
        remaining = MAX_STATE_BYTES - (client.exported_bytes - before)
        if remaining <= 0:
            break
        try:
            data = client.cat("/ipfs/" + cid + "/source.json", maximum=remaining)
            with tempfile.TemporaryDirectory(prefix="peer-source-") as stage:
                target = Path(stage) / "metadata-tree"
                _stage_snapshot(data, record, target)
                if historical:
                    bundle = read_source_document(data)
                    if not isinstance(bundle, DiscoveryCacheBundle):
                        raise PreservationError("historical peer snapshot required")
                    # The peer cannot replace local HTTP hints or receiver grants.
                    bundle = bundle.model_copy(update={"bootstrap": record.source})
                    return follow_source(
                        cache, encode_record(bundle), bootstrap_identity(record.source)
                    )
                return refresh_source_from_directory(
                    cache, source, target, control=client.control
                )
        except (ValueError, OSError) as error:
            client.control.checkpoint()
            last_error = error
    raise PreservationError(
        "no peer supplied an acceptable source snapshot"
    ) from last_error


def _require_mirror(client: PeerClient, confirmed: bool) -> None:
    if not confirmed or client.profile.mode != "mirror":
        raise PreservationError(
            "public publication requires a mirror profile and explicit consent"
        )


def publish_peer_source(
    cache: Path,
    source: str,
    client: PeerClient,
    *,
    public_metadata: bool,
    publish_name: bool = True,
) -> dict[str, str]:
    """Publish only the reviewed source snapshot and update a separate IPNS pointer.

    Source snapshots retain the original source endpoint hints. The operator must
    review those and the catalog for publication; no local grants/errors/inventory
    or private signing keys enter this export. Kubo owns IPNS signing and seqno.
    """
    _require_mirror(client, public_metadata)
    record = read_peer_source(cache, source)
    # IPNS transport signing belongs to this repo, not a TUF role key.
    name = client.publisher_name()
    if publish_name and name != record.ipns:
        raise PreservationError("IPNS publisher does not match the pinned peer source")
    data = export_source(cache, source)
    if not isinstance(read_source_document(data), DiscoveryCacheBundle):
        raise PreservationError("source publication needs a verified cached snapshot")
    with tempfile.TemporaryDirectory(prefix="peer-source-export-") as temporary:
        with directory(Path(temporary)) as root:
            atomic_bytes(root, "source.json", data)
        cid = (
            client.command(
                "add",
                "--recursive",
                "--quiet",
                "--cid-version=1",
                "--pin=true",
                temporary,
                maximum=8192,
            )
            .decode()
            .splitlines()[-1]
        )
    content_id(cid)
    if publish_name:
        _ = client.command(
            "name",
            "publish",
            "--key=self",
            "--lifetime=48h",
            "--allow-offline=false",
            "/ipfs/" + cid,
            maximum=8192,
        )
    return {
        "source_id": source,
        "ipns": record.ipns,
        "cid": cid,
        "publication": "IPNS pointer" if publish_name else "retained snapshot only",
    }


def publish_peer_model(
    store: Path,
    manifest_id: str,
    trust: TrustProfile,
    client: PeerClient,
    *,
    public_model: bool,
) -> dict[str, str | int]:
    """Verify the complete public closure before publishing only its exact objects."""
    _require_mirror(client, public_model)
    manifest = verify_store_contents(store, manifest_id, trust, client.control)
    if any(member.intake.audience != "public" for member in manifest.members):
        raise PreservationError(
            "peer model publication requires public intake for every member"
        )
    with directory(store) as root:
        envelope = read_metadata(root, manifest_path(manifest_id))
    verify_manifest(envelope, trust, manifest_id)
    files = unique_objects(manifest)
    # Stage beside the store, on its volume, instead of in system temporary
    # storage. Exporters serialize on this private area's own lock (not the
    # store writer lock), so a crash-orphaned stage is removed by the next one.
    with (
        directory(store / ".peer-export", create=True) as area,
        writer_lock(area),
    ):
        for name in os.listdir(area):
            if name.startswith("stage-"):
                shutil.rmtree(name, dir_fd=area)
        if (
            shutil.disk_usage(area).free
            < sum(file.size for file in files) + len(envelope) + 16 * CHUNK_SIZE
        ):
            raise PreservationError("store volume has insufficient free space")
        temporary = tempfile.mkdtemp(prefix="stage-", dir=store / ".peer-export")
        try:
            with directory(store) as origin, directory(Path(temporary)) as target:
                for file in files:
                    copy_verified_object(
                        origin, object_path(file.sha256), target, file, client.control
                    )
                atomic_bytes(target, manifest_path(manifest_id), envelope)
            cid = (
                client.command(
                    "add",
                    "--recursive",
                    "--quiet",
                    "--cid-version=1",
                    "--pin=true",
                    temporary,
                    maximum=8 * 1024**2,
                )
                .decode()
                .splitlines()[-1]
            )
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
    content_id(cid)
    return {
        "manifest": manifest_id,
        "cid": cid,
        "mirror": "ipfs://" + cid,
        "verified_bytes": manifest.total_bytes,
    }


def _stage_peer_objects(
    store: Path,
    cid: str,
    files: tuple[FileIdentity, ...],
    client: PeerClient,
    remaining: Callable[[], int],
) -> None:
    """Receive peer objects into the store's own resumable partial slots.

    Staging on the store volume avoids a second full copy in system temporary
    storage (often a size-limited tmpfs), checks free space first, and leaves
    verified partials that a later fetch reuses after an interruption. A wrong
    or incomplete partial is discarded; Kubo offers no ranged resume here.
    """
    with directory(store, create=True) as target, writer_lock(target):
        missing, _reused, partial = observe_objects(
            target, files, client.control, include_partials=True
        )
        additional = sum(file.size for file in missing) - partial
        if missing and shutil.disk_usage(target).free < additional + 16 * CHUNK_SIZE:
            raise PreservationError("destination has insufficient free space")
        client.control.phase("downloading")
        for file in missing:
            pending = ".partial/" + file.sha256
            with open_file(target, pending, write=True) as sink:
                size = os.fstat(sink.fileno()).st_size
            if (
                size == file.size
                and (
                    file_identity(target, pending, file.size, control=client.control)
                ).sha256
                == file.sha256
            ):
                continue
            with open_file(target, pending, write=True) as sink:
                sink.truncate(0)
                if file.size:
                    client.cat(
                        "/ipfs/" + cid + "/" + object_path(file.sha256),
                        maximum=min(file.size, remaining()),
                        sink=sink,
                    )
            observed = file_identity(target, pending, control=client.control)
            if (observed.size, observed.sha256) != (file.size, file.sha256):
                with open_file(target, pending, write=True) as sink:
                    sink.truncate(0)
                raise PreservationError("peer object failed integrity verification")


def fetch_peer_model(
    cache: Path,
    source: str,
    entry: str,
    expected_manifest: str,
    trust: TrustProfile,
    store: Path,
    client: PeerClient,
    *,
    maximum_bytes: int = 20 * 1024**3,
) -> Manifest:
    """Receive a selected complete public model; never publish or trust source keys.

    The explicit allowance covers exported manifest/object bytes, not native DAG
    routing/protocol traffic. Failed attempts charge their exported bytes. The
    standard receiver verifies again and publishes only a complete generation.
    """
    if client.profile.mode != "receive":
        raise PreservationError("receiving needs a receive-only peer profile")
    if not 1 <= maximum_bytes <= 2**53 - 1:
        raise PreservationError("invalid peer receive allowance")
    selected = select_entry(cache, source, entry, expected_manifest)
    roots = tuple(
        content_id(mirror.removeprefix("ipfs://"))
        for mirror in selected.mirrors
        if mirror.startswith("ipfs://")
    )
    if not roots:
        raise PreservationError("selected model has no IPFS content-provider hint")
    started_bytes = client.exported_bytes
    last_error: Exception | None = None
    for cid in roots:
        client.control.checkpoint()
        try:
            envelope = client.cat(
                "/ipfs/" + cid + "/" + manifest_path(expected_manifest),
                maximum=min(
                    MAX_METADATA_BYTES,
                    maximum_bytes - (client.exported_bytes - started_bytes),
                ),
            )
            manifest = verify_manifest(envelope, trust, expected_manifest)
            if any(member.intake.audience != "public" for member in manifest.members):
                raise PreservationError(
                    "peer receiving requires public intake for every member"
                )
            files = unique_objects(manifest)
            if sum(file.size for file in files) > maximum_bytes - (
                client.exported_bytes - started_bytes
            ):
                raise PreservationError(
                    "peer receive allowance cannot cover complete selected model"
                )
            _stage_peer_objects(
                store,
                cid,
                files,
                client,
                lambda: maximum_bytes - (client.exported_bytes - started_bytes),
            )
            # Only the small signed envelope uses system temporary storage.
            with tempfile.TemporaryDirectory(prefix="peer-model-receive-") as temporary:
                with directory(Path(temporary)) as root:
                    atomic_bytes(root, manifest_path(expected_manifest), envelope)
                # The private staging path comes from this function, never from
                # a peer/catalog. Only the ordinary receiver publishes the store,
                # consuming the verified partials staged above without a copy.
                # Each attempt needs its own invocation control so a failed CID
                # cannot block failover to the next one.
                return fetch(
                    expected_manifest,
                    trust,
                    (temporary,),
                    store,
                    TransferPolicy(max_bytes=maximum_bytes),
                    control=client.control.fork(),
                )
        except (OSError, ValueError) as error:
            # Cancellation is not mirror failover.
            client.control.checkpoint()
            last_error = error
            if client.exported_bytes - started_bytes >= maximum_bytes:
                break
    raise PreservationError(
        "no peer supplied the complete selected verified model"
    ) from last_error
