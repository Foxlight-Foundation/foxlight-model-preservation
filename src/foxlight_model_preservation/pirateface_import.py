"""Offline bridge from original-repository torrents to unchanged signed archives."""

from __future__ import annotations

import re
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from .archives import ArchiveManifest, archive_objects, verify_archive_manifest
from .models import (
    MAX_FILES,
    MAX_METADATA_BYTES,
    MAX_TOTAL_BYTES,
    FileIdentity,
    PreservationError,
    TrustProfile,
    canonical_path,
)
from .operations import OperationControl
from .pirateface import PirateFaceCandidate, read_candidate
from .pmf import new_pmf_tree
from .pmf_models import PMFManifest
from .signing import encode_record
from .storage import atomic_bytes, copy_verified_object, directory, manifest_path
from .torrent_binding import torrent_engine


def _component(value: object) -> str:
    if not isinstance(value, bytes):
        raise PreservationError("torrent path component must be UTF-8 bytes")
    result = value.decode("utf-8")
    canonical_path(result)
    if "/" in result:
        raise PreservationError("torrent path component contains a separator")
    return result


def _length(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_TOTAL_BYTES:
        raise PreservationError("invalid torrent file length")
    return value


def repository_inventory(
    metainfo: bytes,
    candidate: PirateFaceCandidate,
) -> dict[str, int]:
    """Parse bounded v1 metainfo without a session; reject unsafe/aliased paths.

    The infohash is a routing binding, not model authentication. Every selected
    local byte must still pass the original independently trusted manifest.
    """
    if len(metainfo) > MAX_METADATA_BYTES:
        raise PreservationError("torrent metainfo exceeds its metadata bound")
    engine = torrent_engine()
    try:
        decoded = engine.bdecode(metainfo)
        if engine.bencode(decoded) != metainfo:
            raise PreservationError("torrent metainfo must have one canonical encoding")
        info_value = decoded.get(b"info")
        if not isinstance(info_value, dict):
            raise PreservationError("torrent has no info dictionary")
        info = cast(dict[bytes, object], info_value)
        if b"meta version" in info or b"name.utf-8" in info:
            raise PreservationError(
                "only unambiguous v1 repository torrents are supported"
            )
        name = _component(info.get(b"name"))
        if b"symlink path" in info or info.get(b"attr", b"") != b"":
            raise PreservationError("torrent link/attribute entries are unsupported")
        raw_files = info.get(b"files")
        if raw_files is None:
            entries = [(name, _length(info.get(b"length")))]
            native_expected = {name: entries[0][1]}
        else:
            if (
                not isinstance(raw_files, list)
                or not 1 <= len(cast(list[object], raw_files)) <= MAX_FILES
            ):
                raise PreservationError("invalid torrent file inventory")
            entries: list[tuple[str, int]] = []
            for item in cast(list[object], raw_files):
                if not isinstance(item, dict):
                    raise PreservationError("invalid torrent file record")
                file = cast(dict[bytes, object], item)
                path = file.get(b"path")
                if (
                    not isinstance(path, list)
                    or not 1 <= len(cast(list[object], path)) <= 64
                ):
                    raise PreservationError("invalid torrent file path")
                if (
                    any(key in file for key in (b"path.utf-8", b"symlink path"))
                    or file.get(b"attr", b"") != b""
                ):
                    raise PreservationError(
                        "torrent link/attribute/alias entries are unsupported"
                    )
                relative = "/".join(
                    _component(part) for part in cast(list[object], path)
                )
                entries.append((canonical_path(relative), _length(file.get(b"length"))))
            native_expected = {name + "/" + path: size for path, size in entries}
        inventory = dict(entries)
        folded: set[str] = set()
        for path, _ in entries:
            key = path.casefold()
            if key in folded:
                raise PreservationError("duplicate or case-aliased torrent path")
            folded.add(key)
        for path in folded:
            parts = path.split("/")
            if any("/".join(parts[:index]) in folded for index in range(1, len(parts))):
                raise PreservationError("torrent file/directory overlap")
        native = engine.torrent_info(decoded)
        hashes = native.info_hashes()
        if not hashes.has_v1() or str(hashes.v1) != candidate.infohash:
            raise PreservationError("torrent infohash differs from candidate")
        files = native.files()
        actual = {
            files.file_path(i): files.file_size(i) for i in range(files.num_files())
        }
        if files.num_files() != len(entries) or actual != native_expected:
            raise PreservationError("native torrent path interpretation differs")
        return inventory
    except (RuntimeError, UnicodeError, TypeError, OverflowError) as error:
        raise PreservationError("invalid repository torrent metainfo") from error


def _bind_member(
    manifest: ArchiveManifest,
    candidate: PirateFaceCandidate,
    member_id: str,
    inventory: Mapping[str, int],
) -> None:
    selected = next((m for m in manifest.members if m.member_id == member_id), None)
    if selected is None:
        raise PreservationError("candidate member is not in the signed selection")
    if isinstance(manifest, PMFManifest):
        member = next(m for m in manifest.members if m.member_id == member_id)
        origin = member.origin
        if origin is None or origin.revision_scheme != "git-sha1":
            raise PreservationError("PirateFace mapping requires a signed Git origin")
        repository, revision = origin.repository, origin.revision
    else:
        member = next(m for m in manifest.members if m.member_id == member_id)
        repository, revision = member.origin_repository, member.origin_revision
    if (
        repository != candidate.repo_id
        or revision is None
        or not re.fullmatch(r"[0-9a-f]{40}", revision)
    ):
        raise PreservationError(
            "candidate repository differs from signed immutable origin"
        )
    if (
        candidate.revision_hint
        and re.fullmatch(r"[0-9a-f]{40}", candidate.revision_hint)
        and candidate.revision_hint != revision
    ):
        raise PreservationError("candidate pinned revision differs from signed origin")
    if any(inventory.get(file.path) != file.size for file in selected.files):
        raise PreservationError("torrent inventory does not cover the selected member")


def import_candidate(
    candidate: PirateFaceCandidate,
    metainfo: bytes,
    envelope: bytes,
    expected_manifest_id: str,
    trust: TrustProfile,
    candidate_member: str,
    sources: Mapping[str, Path],
    destination: Path,
    *,
    documents: Mapping[str, Path] | None = None,
    max_bytes: int = 20 * 1024**3,
    control: OperationControl | None = None,
) -> ArchiveManifest:
    """Verify local inputs into a new complete archive, without contacting peers.

    Authenticate the original envelope before parsing metainfo or opening weights.
    Require every companion and PMF source document. Retain the exact envelope and
    opaque provenance bytes; do not re-sign, enroll keys, execute or start sharing.
    Inputs are never modified or deleted. Existing destinations are refused.
    """
    with (control or OperationControl()).operation("pirateface_import") as active:
        active.phase("checking")
        manifest = verify_archive_manifest(envelope, trust, expected_manifest_id)
        candidate = read_candidate(encode_record(candidate))
        if type(max_bytes) is not int or not 0 < max_bytes <= MAX_TOTAL_BYTES:
            raise PreservationError(
                "import byte allowance must be positive and bounded"
            )
        objects = archive_objects(manifest)
        required = sum(file.size for file in objects)
        if required > max_bytes:
            raise PreservationError("signed archive exceeds the import byte allowance")
        documents = documents or {}
        source_documents = (
            manifest.documents if isinstance(manifest, PMFManifest) else ()
        )
        if set(sources) != {m.member_id for m in manifest.members} or set(
            documents
        ) != {d.document_id for d in source_documents}:
            raise PreservationError(
                "import requires exactly all members and source documents"
            )
        active.checkpoint()
        inventory = repository_inventory(metainfo, candidate)
        _bind_member(manifest, candidate, candidate_member, inventory)
        if destination.exists() or destination.is_symlink():
            raise PreservationError("import requires a new destination")
        resolved = destination.resolve()
        if any(
            resolved == root.resolve() or resolved.is_relative_to(root.resolve())
            for root in sources.values()
        ):
            raise PreservationError(
                "destination must be outside member input directories"
            )
        active.phase("copying")
        with new_pmf_tree(destination, active) as target:
            if shutil.disk_usage(target).free < required + 16 * 1024**2:
                raise PreservationError("insufficient space for a complete archive")
            for member in manifest.members:
                with directory(sources[member.member_id]) as source:
                    for file in member.files:
                        copy_verified_object(
                            source,
                            file.path,
                            target,
                            file,
                            active,
                            verify_reused_source=True,
                        )
            for document in source_documents:
                path = documents[document.document_id]
                with directory(path.parent) as source:
                    copy_verified_object(
                        source,
                        path.name,
                        target,
                        FileIdentity(
                            path=path.name, size=document.size, sha256=document.sha256
                        ),
                        active,
                        verify_reused_source=True,
                    )
            atomic_bytes(target, manifest_path(expected_manifest_id), envelope)
        return manifest
