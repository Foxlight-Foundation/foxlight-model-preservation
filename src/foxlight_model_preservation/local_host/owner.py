"""Durable private model custody for the independent Sovereign application.

Only explicit submissions start finite work. A restarted owner records interrupted
jobs and never silently replays networking. Closing the view leaves jobs running;
closing the application requests a cooperative stop and retains its journal.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import threading
import time
import uuid
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import BinaryIO, Literal, cast

from pydantic import Field, JsonValue

from ..archives import verify_archive
from ..cards import identity_json
from ..kits import export_kit, import_kit, inspect_kit, read_kit_envelope
from ..mirror_models import MirrorObservation
from ..models import Manifest, PreservationError, Record, TrustProfile
from ..operations import (
    OperationCancelled,
    OperationControl,
    OperationError,
    Progress,
    failure_code,
)
from ..pmf import restore_pmf
from ..pmf_models import PMFManifest
from ..pmf_preservation import import_preservation
from ..signing import (
    create_key,
    digest,
    encode_record,
    load_key,
    strict_json,
)
from ..storage import atomic_bytes, directory, pack, read_metadata, writer_lock
from ..transfer import TransferPolicy, fetch
from .backups import Backups
from .catalog import RegistryCatalog
from .community import CommunityMirror
from .presentation import (
    PURPOSE_WORDS,
    captured_card_metadata,
    card_summary,
    copy_metadata,
    manifest_summary,
    metadata_page,
    search_words,
)
from .selection import (
    NOTICE,
    NOTICE_PATH,
    download_file,
    registry_file_map,
    registry_recipe,
)
from .sharing import Sharing
from .sources import HandoffReference, SourceLibrary

JobKind = Literal[
    "catalog_refresh",
    "receive",
    "kit_export",
    "restore",
    "kit_import",
    "source_refresh",
    "source_metadata",
    "source_receive",
    "backup",
    "publish",
    "share",
]


class Copy(Record):
    """One completed exact PMF generation and independently selected capture keys."""

    copy_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str
    size_bytes: int
    member_count: int
    trust: TrustProfile


class Job(Record):
    """Private immutable request binding with a durable, bounded outcome receipt."""

    job_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation_id: str
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: JobKind
    state: Literal[
        "queued", "running", "succeeded", "failed", "cancelled", "interrupted"
    ]
    created_at: float
    name: str
    error_code: str | None = None
    copy_id: str | None = None
    output: str | None = None


class Journal(Record):
    """Versioned local ownership; unrelated Skulk and mirror state is untouched."""

    schema_version: Literal["sovereign.local-owner.v2"] = "sovereign.local-owner.v2"
    copies: tuple[Copy, ...] = Field(default=(), max_length=256)
    jobs: tuple[Job, ...] = Field(default=(), max_length=256)


class LocalOwner:
    """Own local storage and bounded background work with one process writer."""

    def __init__(self, home: Path, *, catalog: RegistryCatalog | None = None) -> None:
        self.home = home.absolute()
        self._stack = ExitStack()
        try:
            self._root = self._stack.enter_context(directory(self.home, create=True))
            mode = os.fstat(self._root).st_mode
            if mode & 0o077:
                raise PreservationError("sovereign_owner_directory_must_be_private")
            self._stack.enter_context(writer_lock(self._root))
            self._identity = self._fingerprint()
            self.catalog = catalog or RegistryCatalog(home / "registry")
            self.sources = SourceLibrary(self.home)
            self.backups = Backups(self.home)
            self.sharing = Sharing(self.home)
            self.community = CommunityMirror(self.home, self.sharing)
            self._community_wake = threading.Event()
            self._community_control: OperationControl | None = None
            self._community_thread: threading.Thread | None = None
            self._gossip_thread: threading.Thread | None = None
            self._gossip_control: OperationControl | None = None
            self._stopping = threading.Event()
            self._lock = threading.RLock()
            self._idle = threading.Condition(self._lock)
            self._backup_calls = 0
            self._closing = False
            self._catalog_lock = threading.RLock()
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="sovereign"
            )
            self._sharing_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="sovereign-sharing"
            )
            self._sharing_observations: dict[str, MirrorObservation] = {}
            self._sharing_files: dict[str, str] = {}
            self._controls: dict[str, OperationControl] = {}
            self._progress: dict[str, Progress] = {}
            self._summaries: dict[str, dict[str, JsonValue]] = {}
            self._search_metadata: dict[str, str] = {}
            self._closed = False
            try:
                raw = read_metadata(self._root, "journal.json")
                value = strict_json(raw)
                legacy = isinstance(value, dict) and value.get("schema_version") == (
                    "sovereign.local-owner.v1"
                )
                if legacy and isinstance(value, dict):
                    value = {**value, "schema_version": "sovereign.local-owner.v2"}
                self._journal = Journal.model_validate_json(identity_json(value))
                if legacy:
                    # Only owner metadata migrates. Signed archives, kits and
                    # all original cards stay untouched; older owners refuse v2.
                    try:
                        read_metadata(self._root, "journal-before-v2.json")
                    except FileNotFoundError:
                        atomic_bytes(self._root, "journal-before-v2.json", raw)
            except FileNotFoundError:
                self._journal = Journal()
            self._journal = self._journal.model_copy(
                update={
                    "jobs": tuple(
                        job.model_copy(
                            update={
                                "state": "interrupted",
                                "error_code": "owner_restarted",
                            }
                        )
                        if job.state in {"queued", "running"}
                        else job
                        for job in self._journal.jobs
                    )
                }
            )
            self._save()
            # The owner's single standing consent resumes serving after restart.
            if self.community.consented():
                self._community_refresh()
        except BaseException:
            self._stack.close()
            raise

    def _fingerprint(self) -> tuple[int, int]:
        value = os.stat(self.home, follow_symlinks=False)
        return value.st_dev, value.st_ino

    def _check(self) -> None:
        if self._closed or self._fingerprint() != self._identity:
            raise PreservationError("sovereign_storage_changed")

    def _save(self) -> None:
        self._check()
        atomic_bytes(self._root, "journal.json", encode_record(self._journal))

    def close(self) -> None:
        """Stop finite work, wait for its terminal receipt and release ownership."""
        with self._idle:
            if self._closed:
                return
            if self._closing:
                self._idle.wait_for(lambda: self._closed)
                return
            self._closing = True
            self._idle.notify_all()
            for control in self._controls.values():
                control.cancel()
            community = self._community_thread
            gossip = self._gossip_thread
            self._stopping.set()
            if self._gossip_control is not None:
                self._gossip_control.cancel()
            self._community_wake.set()
            if self._community_control is not None:
                self._community_control.cancel()
        if community is not None:
            community.join()
        if gossip is not None:
            gossip.join()
        self._executor.shutdown(wait=True)
        self._sharing_executor.shutdown(wait=True)
        with self._idle:
            # NAS syscalls cannot always be cancelled. Keep exclusive ownership
            # until every accepted out-of-lock call has completed its writes.
            self._idle.wait_for(lambda: self._backup_calls == 0)
            self._closed = True
            self._stack.close()
            self._idle.notify_all()

    @contextmanager
    def _backup_call(self) -> Generator[None]:
        with self._idle:
            self._check()
            if self._closing:
                raise PreservationError("sovereign_owner_closing")
            self._backup_calls += 1
        try:
            yield
        finally:
            with self._idle:
                self._backup_calls -= 1
                self._idle.notify_all()

    def read(self, offset: int = 0) -> dict[str, JsonValue]:
        """Read compact receipts; paths and complete trust history stay private."""
        with self._lock:
            self._check()
            if not 0 <= offset <= 2**31:
                raise PreservationError("invalid_page")
            recent = sorted(
                self._journal.jobs, key=lambda job: job.created_at, reverse=True
            )
            history = self._history_names()
            selected = recent[offset : offset + 48]
            if len(selected) < 48:
                start = max(0, offset - len(recent))
                with directory(self.home / "history/receipts", create=True) as archived:
                    selected.extend(
                        Job.model_validate_json(read_metadata(archived, name))
                        for name in history[start : start + 48 - len(selected)]
                    )
            uploads: list[JsonValue] = []
            with directory(self.home / "uploads", create=True) as source:
                for name in sorted(os.listdir(source)):
                    if re.fullmatch(r"[0-9a-f]{32}\.json", name):
                        uploads.append(self._upload(name[:-5]))
            jobs = metadata_page(
                [
                    {
                        **item.model_dump(mode="json"),
                        "stage": self._progress[item.job_id].stage
                        if item.job_id in self._progress
                        else item.state,
                        "bytes_received": self._progress[item.job_id].bytes_received
                        if item.job_id in self._progress
                        else 0,
                        "temporary_sharing_files": item.kind == "publish"
                        and item.job_id not in self._controls
                        and self.sharing.work_exists(item.job_id),
                        "sharing": {
                            **self._sharing_observations[item.job_id].model_dump(
                                mode="json"
                            ),
                            "handoff_ready": self._handoff_ready(item),
                        }
                        if item.job_id in self._sharing_observations
                        else None,
                    }
                    for item in selected
                ],
                max_rows=48,
                byte_limit=32_000,
            )
            copies = metadata_page(
                (
                    item.model_dump(mode="json", exclude={"trust"})
                    for item in self._journal.copies
                ),
                byte_limit=16_000,
            )
            return {
                "host_kind": "sovereign",
                "locations": [],
                "models": [],
                "copies": cast(list[JsonValue], copies),
                "copy_count": len(self._journal.copies),
                "copies_next_offset": len(copies)
                if len(copies) < len(self._journal.copies)
                else None,
                "jobs": cast(list[JsonValue], jobs),
                "uploads": uploads,
                "active_count": len(self._controls),
                "next_offset": offset + len(jobs)
                if offset + len(jobs) < len(recent) + len(history)
                else None,
                "storage_label": "Sovereign on this device",
            }

    def _copy_view(self, item: Copy) -> dict[str, JsonValue]:
        # Immutable identities make this a display cache, never an authority or
        # a journal migration. Actual export/restore still reverify every byte.
        if item.copy_id not in self._summaries:
            summary: dict[str, JsonValue]
            try:
                summary, searchable = copy_metadata(
                    self.home / "copies" / item.copy_id, item.manifest_id, item.trust
                )
            except (ValueError, OSError):
                summary = {"metadata_status": "unavailable"}
                searchable = ""
            self._summaries[item.copy_id] = summary
            self._search_metadata[item.copy_id] = searchable
        return {
            **item.model_dump(mode="json", exclude={"trust"}),
            "details": self._summaries[item.copy_id],
        }

    def _search_copies(
        self, query: str, purpose: str, offset: int
    ) -> dict[str, JsonValue]:
        """Search retained metadata locally with bounded copy pages."""
        selected = list(self._journal.copies)
        if query or purpose:
            terms = query.casefold().split()
            matched: list[Copy] = []
            for item in selected:
                details = cast(dict[str, JsonValue], self._copy_view(item)["details"])
                categories = details.get("purposes", [])
                if purpose and (
                    not isinstance(categories, list) or purpose not in categories
                ):
                    continue
                text = (
                    item.name
                    + " "
                    + self._search_metadata[item.copy_id]
                    + " "
                    + search_words(details)
                ).casefold()
                if all(term in text for term in terms):
                    matched.append(item)
            selected = matched
        rows = metadata_page(self._copy_view(item) for item in selected[offset:])
        return {
            "results": cast(list[JsonValue], rows),
            "total": len(selected),
            "stored_total": len(self._journal.copies),
            "next_offset": offset + len(rows)
            if offset + len(rows) < len(selected)
            else None,
        }

    def _history_names(self) -> list[str]:
        current = {job.job_id for job in self._journal.jobs}
        with directory(self.home / "history/receipts", create=True) as root:
            return sorted(
                (
                    name
                    for name in os.listdir(root)
                    if re.fullmatch(r"[0-9]{20}-[0-9a-f]{64}\.json", name)
                    and name[21:85] not in current
                ),
                reverse=True,
            )

    def _job(self, identifier: str) -> Job:
        if not re.fullmatch(r"[0-9a-f]{64}", identifier):
            raise PreservationError("invalid_job_identity")
        for job in self._journal.jobs:
            if job.job_id == identifier:
                return job
        with directory(self.home / "history/receipts", create=True) as root:
            for name in os.listdir(root):
                if re.fullmatch(r"[0-9]{20}-" + identifier + r"\.json", name):
                    result = Job.model_validate_json(read_metadata(root, name))
                    if result.job_id != identifier:
                        raise PreservationError("invalid_job_identity")
                    return result
        raise PreservationError("job_unavailable")

    def _archive_history(self) -> None:
        if len(self._journal.jobs) < 256:
            return
        moved = tuple(
            job
            for job in self._journal.jobs
            if job.state not in {"queued", "running"}
            and job.job_id not in self._controls
        )[:128]
        if not moved:
            raise PreservationError("sovereign_job_capacity_reached")
        for job in moved:
            stamp = str(int(job.created_at * 1_000_000)).zfill(20)
            atomic_bytes(
                self._root,
                "history/receipts/" + stamp + "-" + job.job_id + ".json",
                encode_record(job),
            )
            atomic_bytes(
                self._root,
                "history/operations/" + digest(job.operation_id.encode()) + ".json",
                encode_record(job),
            )
        identities = {job.job_id for job in moved}
        self._journal = self._journal.model_copy(
            update={
                "jobs": tuple(
                    job for job in self._journal.jobs if job.job_id not in identities
                )
            }
        )

    def _copy(self, identifier: str) -> Copy:
        return (
            next(
                (item for item in self._journal.copies if item.copy_id == identifier),
                None,
            )
            or self._missing()
        )

    @staticmethod
    def _missing() -> Copy:
        raise PreservationError("sovereign_copy_unavailable")

    def _plan(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if value.get("kind") in {"receive", "source_receive"}:
            # This is the same conservative reserve enforced again by the
            # worker, observed now so the person can choose before downloading.
            selected_bytes = cast(int, value["size_bytes"])
            value = {
                **value,
                "required_free_bytes": selected_bytes * 4 + 32 * 1024**2,
                "free_bytes": shutil.disk_usage(self.home).free,
            }
        value = {**value, "expires_at": int(time.time()) + 600}
        from ..models import MAX_METADATA_BYTES

        if len(identity_json(value)) > MAX_METADATA_BYTES:
            raise PreservationError("metadata_preview_too_large")
        plan_id = digest(identity_json(value))
        atomic_bytes(self._root, "plans/" + plan_id + ".json", identity_json(value))
        return {
            **{
                key: item
                for key, item in value.items()
                if key
                not in {
                    "recipe",
                    "trust",
                    "selection",
                    "source_policy",
                    "public_manifest",
                    "publication_request",
                    "decisions",
                    "additional_locations",
                }
            },
            "plan_id": plan_id,
        }

    def control(self, action: str, body: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Validate explicit view controls; never accept arbitrary API forwarding."""
        # An unavailable NAS can block OS filesystem calls. Do not hold the
        # owner's receipt/search lock while inspecting an explicitly chosen path.
        if action == "backup-review" and set(body) == {"path", "label"}:
            if not isinstance(body["path"], str) or not isinstance(body["label"], str):
                raise PreservationError("invalid_control")
            with self._backup_call():
                return self.backups.review(body["path"], body["label"])
        if action == "backup-setup" and set(body) == {"review_id", "confirmed"}:
            if body["confirmed"] is not True:
                raise PreservationError("invalid_control")
            with self._backup_call():
                return self.backups.setup(str(body["review_id"]))
        with self._lock:
            self._check()
            if self._closing:
                raise PreservationError("sovereign_owner_closing")
            if action == "backup-read" and set(body) <= {"offset"}:
                offset = body.get("offset", 0)
                if type(offset) is not int or not 0 <= offset <= 16:
                    raise PreservationError("invalid_control")
                return self.backups.read(offset)
            if action == "sharing-work-remove" and set(body) == {"job_id"}:
                job = self._job(str(body["job_id"]))
                if job.kind != "publish" or job.job_id in self._controls:
                    raise PreservationError("sharing_busy_try_again")
                self.sharing.remove_work(job.job_id)
                return {"removed": True}
            if action == "community-read" and not body:
                return self.community.read(self._journal.copies)
            if action == "community-configure":
                try:
                    policy = self.community.configure(body)
                finally:
                    # Turning sharing off always stops the running mirror, even
                    # if saving or revoking failed part-way.
                    if not self.community.consented():
                        if self._community_control is not None:
                            self._community_control.cancel()
                        # Opting out also stops discovery traffic in progress.
                        if self._gossip_control is not None:
                            self._gossip_control.cancel()
                        self._community_wake.set()
                if policy.enabled:
                    self._community_refresh()
                return self.community.read(self._journal.copies)
            if action == "community-choose" and set(body) == {
                "copy_id",
                "chosen",
                "withheld",
            }:
                copy = self._copy(str(body["copy_id"]))
                chosen, withheld = body["chosen"], body["withheld"]
                if not isinstance(chosen, bool) or not isinstance(withheld, bool):
                    raise PreservationError("invalid_control")
                self.community.choose(copy.copy_id, chosen=chosen, withheld=withheld)
                if self.community.consented():
                    self._community_refresh()
                return self.community.read(self._journal.copies)
            if action == "sharing-read" and set(body) <= {"offset"}:
                offset = body.get("offset", 0)
                if type(offset) is not int or not 0 <= offset <= 256:
                    raise PreservationError("invalid_control")
                return self.sharing.read(offset)
            if action == "sharing-review" and set(body) == {"copy_id"}:
                copy = self._copy(str(body["copy_id"]))
                return self.sharing.review(
                    self.home / "copies" / copy.copy_id, copy.manifest_id, copy.trust
                )
            if action == "backup-forget" and set(body) == {"destination_id"}:
                if any(
                    job.kind == "backup" and job.job_id in self._controls
                    for job in self._journal.jobs
                ):
                    raise PreservationError("backup_busy_try_again")
                return self.backups.forget(str(body["destination_id"]))
            if action == "job-read" and set(body) == {"job_id"}:
                return self._job(str(body["job_id"])).model_dump(mode="json")
            if action == "copy-recovery-info" and set(body) == {"copy_id"}:
                copy = self._copy(str(body["copy_id"]))
                return {
                    "name": copy.name,
                    "manifest_id": copy.manifest_id,
                    "threshold": copy.trust.threshold,
                    "keys": [key.key_id for key in copy.trust.keys],
                    "revoked_keys": list(copy.trust.revoked_keys),
                }
            if action == "source-read" and not body:
                return self.sources.read()
            if action == "source-search":
                return self.sources.search(body)
            if action in {"source-review", "handoff-review"} and set(body) == {
                "upload_id"
            }:
                from ..discovery_models import MAX_STATE_BYTES
                from ..storage import open_file

                uploaded = self._upload(str(body["upload_id"]))
                if (
                    not uploaded["complete"]
                    or cast(int, uploaded["size_bytes"]) > MAX_STATE_BYTES
                ):
                    raise PreservationError("source_document_too_large")
                with (
                    directory(self.home / "uploads") as root,
                    open_file(root, str(body["upload_id"])) as stream,
                ):
                    document = stream.read(MAX_STATE_BYTES + 1)
                if action == "handoff-review":
                    return self.sources.review_handoff(document)
                return self.sources.review(document)
            if action in {"source-follow", "source-remove"}:
                if any(
                    job.kind in {"source_refresh", "source_metadata"}
                    and job.job_id in self._controls
                    for job in self._journal.jobs
                ):
                    raise PreservationError("discovery_busy_try_again")
                if action == "source-follow":
                    return self.sources.follow(body)
                if set(body) == {"source_id"}:
                    return self.sources.remove(str(body["source_id"]))
                raise PreservationError("invalid_control")
            if action == "source-reference" and set(body) == {"job_id"}:
                job = self._job(str(body["job_id"]))
                if job.kind != "source_metadata" or job.state != "succeeded":
                    raise PreservationError("model_metadata_unavailable")
                return self.sources.preview(job.job_id)
            if action in {"search", "copy-search"}:
                if (
                    set(body)
                    - (
                        {"query", "offset", "purpose", "ready_only"}
                        if action == "search"
                        else {"query", "offset", "purpose"}
                    )
                    or type(body.get("ready_only", False)) is not bool
                    or not isinstance(body.get("query", ""), str)
                    or len(str(body.get("query", ""))) > 500
                    or body.get("purpose", "") not in ("", *PURPOSE_WORDS)
                ):
                    raise PreservationError("invalid_control")
                offset = body.get("offset", 0)
                if type(offset) is not int or not 0 <= offset <= 100_000:
                    raise PreservationError("invalid_control")
                if action == "copy-search":
                    return self._search_copies(
                        str(body.get("query", "")), str(body.get("purpose", "")), offset
                    )
                return self.catalog.search(
                    str(body.get("query", "")),
                    offset,
                    str(body.get("purpose", "")),
                    body.get("ready_only") is True,
                )
            if action == "refresh" and not body:
                return self._start(
                    self._plan(
                        {
                            "kind": "catalog_refresh",
                            "name": "Update public model listings",
                        }
                    ),
                    str(uuid.uuid4()),
                )
            if action == "plan":
                kind = body.get("kind")
                if kind == "publish" and set(body) == {"kind", "copy_id", "decisions"}:
                    copy = self._copy(str(body["copy_id"]))
                    return self._plan(
                        {
                            "kind": "publish",
                            "copy_id": copy.copy_id,
                            **self.sharing.plan(
                                self.home / "copies" / copy.copy_id,
                                copy.manifest_id,
                                copy.trust,
                                body["decisions"],
                            ),
                        }
                    )
                if kind == "share" and {"kind", "public_id"} <= set(body) <= {
                    "kind",
                    "public_id",
                    "advertised_address",
                }:
                    return self._plan(
                        self.sharing.session_plan(
                            str(body["public_id"]), body.get("advertised_address")
                        )
                    )
                if kind in {"source_refresh", "source_metadata"}:
                    return self._plan(self.sources.plan(body))
                if kind == "source_receive":
                    identifier = str(body.get("reference_job_id"))
                    record, _, _, _ = self.sources.reference(identifier)
                    if not isinstance(record, HandoffReference):
                        job = self._job(identifier)
                        if job.kind != "source_metadata" or job.state != "succeeded":
                            raise PreservationError("model_metadata_unavailable")
                    return self._plan(self.sources.receive_plan(body))
                if kind == "receive" and set(body) == {"kind", "catalog_id", "card_id"}:
                    with self._catalog_lock:
                        card, proof = self.catalog.select(
                            str(body["catalog_id"]), str(body["card_id"])
                        )
                    recipe = registry_recipe(card, proof)
                    selected_files = registry_file_map(card)
                    vision = card.card.get("vision")
                    projector = (
                        vision.get("projector_file")
                        if isinstance(vision, dict)
                        else None
                    )
                    # An offline snapshot never silently acquires freshness.
                    try:
                        self.catalog.verify_current(proof, card.card_id)
                        historical = False
                    except PreservationError:
                        historical = True
                    return self._plan(
                        {
                            "kind": "receive",
                            "name": recipe.name,
                            "catalog_id": body["catalog_id"],
                            "card_id": card.card_id,
                            "recipe": recipe.model_dump(mode="json"),
                            "details": card_summary(
                                card,
                                metadata=captured_card_metadata(proof, card),
                                selected_files=(
                                    path
                                    for repository, revision, path in selected_files
                                    if (repository, revision)
                                    == (
                                        card.artifact.repository,
                                        card.artifact.revision,
                                    )
                                ),
                            ),
                            "historical": historical,
                            "file_count": len(selected_files),
                            "projector_included": isinstance(projector, str)
                            and (
                                card.artifact.repository,
                                card.artifact.revision,
                                projector,
                            )
                            in selected_files,
                            "members": [
                                item.origin_repository for item in recipe.members
                            ],
                            "size_bytes": sum(
                                selected_files[
                                    (
                                        member.origin_repository,
                                        member.origin_revision,
                                        file.path,
                                    )
                                ].size_bytes
                                for member in recipe.members
                                for file in member.files
                                if file.path != NOTICE_PATH
                            ),
                            "source": "huggingface.co and public HTTPS CDN redirects",
                        }
                    )
                if kind in {"kit_export", "restore"} and set(body) == {
                    "kind",
                    "copy_id",
                }:
                    copy = self._copy(str(body["copy_id"]))
                    return self._plan(
                        {
                            "kind": kind,
                            "copy_id": copy.copy_id,
                            "name": copy.name,
                            "manifest_id": copy.manifest_id,
                            "size_bytes": copy.size_bytes,
                            "details": self._copy_view(copy)["details"],
                            "member_count": copy.member_count,
                            "keys": [key.key_id for key in copy.trust.keys],
                        }
                    )
                if kind == "backup" and set(body) == {
                    "kind",
                    "copy_id",
                    "destination_id",
                }:
                    copy = self._copy(str(body["copy_id"]))
                    return self._plan(
                        {
                            "kind": "backup",
                            "copy_id": copy.copy_id,
                            "name": copy.name,
                            "manifest_id": copy.manifest_id,
                            "size_bytes": copy.size_bytes,
                            "member_count": copy.member_count,
                            **self.backups.selection(str(body["destination_id"])),
                            "details": self._copy_view(copy)["details"],
                        }
                    )
                if kind == "kit_import" and set(body) == {"kind", "upload_id"}:
                    uploaded = self._upload(str(body["upload_id"]))
                    info = inspect_kit(self.home / "uploads" / str(body["upload_id"]))
                    if uploaded["offset"] != uploaded["size_bytes"]:
                        raise PreservationError("kit_upload_incomplete")
                    return self._plan(
                        {
                            "kind": kind,
                            "upload_id": body["upload_id"],
                            "name": info.manifest.name,
                            "details": manifest_summary(info.manifest)
                            if isinstance(info.manifest, PMFManifest)
                            else None,
                            "manifest_id": info.manifest_id,
                            "envelope_sha256": digest(
                                read_kit_envelope(
                                    self.home / "uploads" / str(body["upload_id"])
                                )
                            ),
                            "threshold": info.trust.threshold,
                            "revoked_keys": list(info.trust.revoked_keys),
                            "trust": info.trust.model_dump(mode="json"),
                            "size_bytes": info.manifest.total_bytes,
                            "member_count": len(info.manifest.members),
                            "keys": [key.key_id for key in info.trust.keys],
                        }
                    )
                raise PreservationError("invalid_control")
            if (
                action == "start"
                and set(body) == {"plan_id", "operation_id", "confirmed"}
                and body["confirmed"] is True
            ):
                plan_id = str(body["plan_id"])
                if not re.fullmatch(r"[0-9a-f]{64}", plan_id):
                    raise PreservationError("invalid_control")
                plan = strict_json(
                    read_metadata(self._root, "plans/" + plan_id + ".json")
                )
                if not isinstance(plan, dict) or digest(identity_json(plan)) != plan_id:
                    raise PreservationError("plan_changed")
                return self._start(
                    {**plan, "plan_id": plan_id}, str(body["operation_id"])
                )
            if action == "cancel" and set(body) == {"job_id"}:
                control = self._controls.get(str(body["job_id"]))
                if control:
                    control.cancel()
                return {"stop_requested": control is not None}
            if action == "upload-remove" and set(body) == {"upload_id"}:
                identifier = str(body["upload_id"])
                self._upload(identifier)
                for job in self._journal.jobs:
                    if job.kind == "kit_import" and job.state in {"queued", "running"}:
                        plan = strict_json(
                            read_metadata(self._root, "plans/" + job.plan_id + ".json")
                        )
                        if (
                            not isinstance(plan, dict)
                            or plan.get("upload_id") == identifier
                        ):
                            raise PreservationError("kit_upload_in_use")
                with directory(self.home / "uploads") as root:
                    names = [
                        name
                        for name in (identifier, identifier + ".json")
                        if self._exists_regular(root, name)
                    ]
                    for name in names:
                        os.unlink(name, dir_fd=root)
                    os.fsync(root)
                return {"removed": True}
            if action == "upload-create" and set(body) == {"size_bytes"}:
                size = body["size_bytes"]
                if type(size) is not int or not 0 < size <= 2**50:
                    raise PreservationError("invalid_control")
                uploads = self.home / "uploads"
                with directory(uploads, create=True) as root:
                    if (
                        sum(
                            bool(re.fullmatch(r"[0-9a-f]{32}\.json", name))
                            for name in os.listdir(root)
                        )
                        >= 8
                    ):
                        raise PreservationError("remove_unused_kit_uploads")
                    name = uuid.uuid4().hex
                    atomic_bytes(
                        root, name + ".json", identity_json({"size_bytes": size})
                    )
                    atomic_bytes(root, name, b"")
                return {
                    "upload_id": name,
                    "size_bytes": size,
                    "offset": 0,
                    "complete": False,
                }
            raise PreservationError("sovereign_operation_unavailable")

    @staticmethod
    def _exists_regular(root: int, name: str) -> bool:
        try:
            value = os.stat(name, dir_fd=root, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(value.st_mode):
            raise PreservationError("unsafe_upload_transfer")
        return True

    def _upload(self, identifier: str) -> dict[str, JsonValue]:
        if not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise PreservationError("invalid_upload")
        from ..storage import open_file

        with directory(self.home / "uploads") as root:
            meta = strict_json(read_metadata(root, identifier + ".json"))
            if not isinstance(meta, dict):
                raise PreservationError("invalid_upload")
            try:
                with open_file(root, identifier) as stream:
                    offset = os.fstat(stream.fileno()).st_size
            except FileNotFoundError:
                offset = 0
        return {
            "upload_id": identifier,
            "size_bytes": meta["size_bytes"],
            "offset": offset,
            "complete": offset == meta["size_bytes"],
        }

    def upload(
        self, identifier: str, offset: int, content: bytes
    ) -> dict[str, JsonValue]:
        """Append one bounded exact-offset kit chunk without a weight-sized buffer."""
        from ..storage import open_file

        with self._lock:
            self._check()
            status = self._upload(identifier)
            if (
                not 0 < len(content) <= 4 * 1024**2
                or status["offset"] != offset
                or offset + len(content) > cast(int, status["size_bytes"])
            ):
                raise PreservationError("kit_transfer_refused")
            with (
                directory(self.home / "uploads") as root,
                open_file(root, identifier, write=True) as stream,
            ):
                stream.seek(offset)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            return self._upload(identifier)

    def _start(
        self, plan: dict[str, JsonValue], operation_id: str
    ) -> dict[str, JsonValue]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", operation_id):
            raise PreservationError("invalid_operation_identity")
        plan_id = str(plan["plan_id"])
        previous = next(
            (job for job in self._journal.jobs if job.operation_id == operation_id),
            None,
        )
        if previous is None:
            try:
                previous = Job.model_validate_json(
                    read_metadata(
                        self._root,
                        "history/operations/" + digest(operation_id.encode()) + ".json",
                    )
                )
            except FileNotFoundError:
                pass
        if previous:
            if previous.operation_id != operation_id or previous.plan_id != plan_id:
                raise PreservationError("request_changed")
            return previous.model_dump(mode="json")
        if cast(int, plan["expires_at"]) <= time.time():
            raise PreservationError("plan_missing_or_expired")
        if len(self._controls) >= 4:
            raise PreservationError("sovereign_job_capacity_reached")
        if plan["kind"] == "share" and any(
            job.kind == "share" and job.job_id in self._controls
            for job in self._journal.jobs
        ):
            raise PreservationError("sharing_busy_try_again")
        if (
            plan["kind"] in {"receive", "kit_import", "source_receive"}
            and len(self._journal.copies)
            + sum(
                job.kind in {"receive", "kit_import", "source_receive"}
                and job.state in {"queued", "running"}
                for job in self._journal.jobs
            )
            >= 256
        ):
            raise PreservationError("sovereign_library_capacity_reached")
        self._archive_history()
        job = Job(
            job_id=digest(
                identity_json({"operation_id": operation_id, "plan_id": plan_id})
            ),
            operation_id=operation_id,
            plan_id=plan_id,
            kind=cast(JobKind, plan["kind"]),
            state="queued",
            created_at=time.time(),
            name=str(plan["name"]),
        )
        self._journal = self._journal.model_copy(
            update={"jobs": (*self._journal.jobs, job)}
        )
        self._save()

        def observe(snapshot: Progress) -> None:
            with self._lock:
                self._progress[job.job_id] = snapshot

        control = OperationControl(observe)
        self._progress[job.job_id] = control.progress
        self._controls[job.job_id] = control
        executor = self._sharing_executor if job.kind == "share" else self._executor
        executor.submit(self._run, job, plan, control)
        return job.model_dump(mode="json")

    def _update(self, job: Job, **changes: JsonValue) -> Job:
        with self._lock:
            current = next(
                item for item in self._journal.jobs if item.job_id == job.job_id
            )
            job = current.model_copy(update=changes)
            self._journal = self._journal.model_copy(
                update={
                    "jobs": tuple(
                        job if item.job_id == job.job_id else item
                        for item in self._journal.jobs
                    )
                }
            )
            self._save()
            return job

    def _finish(self, job: Job, **changes: JsonValue) -> None:
        """Publish a terminal outcome and its inactive controls atomically."""
        with self._lock:
            self._controls.pop(job.job_id, None)
            self._progress.pop(job.job_id, None)
            self._update(job, **changes)

    def _run(
        self, job: Job, plan: dict[str, JsonValue], control: OperationControl
    ) -> None:
        try:
            job = self._update(job, state="running")
            control.checkpoint()
            if job.kind == "catalog_refresh":
                with self._catalog_lock:
                    self.catalog.refresh(control=control)
            elif job.kind == "receive":
                self._receive(job, plan, control)
            elif job.kind == "kit_import":
                self._import(job, plan, control)
            elif job.kind in {"source_refresh", "source_metadata"}:
                self.sources.run(job.job_id, plan, control)
            elif job.kind == "source_receive":
                self._source_receive(job, plan, control)
            elif job.kind == "share":

                def observe(value: MirrorObservation) -> None:
                    with self._lock:
                        self._sharing_observations[job.job_id] = value

                def ready(identity: str | None) -> None:
                    with self._lock:
                        if identity is None:
                            self._sharing_files.pop(job.job_id, None)
                        else:
                            self._sharing_files[job.job_id] = identity

                self.sharing.serve(job.job_id, plan, control, observe, ready)
            else:
                with self._lock:
                    copy = self._copy(str(plan["copy_id"]))
                store = self.home / "copies" / copy.copy_id
                verify_archive(
                    store, copy.manifest_id, copy.trust, control=control.fork()
                )
                if job.kind == "publish":
                    self.sharing.publish(
                        job.job_id,
                        copy.copy_id,
                        store,
                        copy.manifest_id,
                        copy.trust,
                        plan,
                        control.fork(),
                    )
                    output = None
                elif job.kind == "backup":
                    output = self.backups.write(
                        plan,
                        store,
                        copy.manifest_id,
                        copy.trust,
                        job.job_id,
                        control.fork(),
                    )
                elif job.kind == "kit_export":
                    with directory(self.home / "kits", create=True):
                        pass
                    output = self.home / "kits" / (job.job_id + ".foxkit")
                    export_kit(
                        store,
                        copy.manifest_id,
                        copy.trust,
                        output,
                        control=control.fork(),
                    )
                else:
                    with directory(self.home / "restored", create=True):
                        pass
                    output = self.home / "restored" / job.job_id
                    restore_pmf(
                        store,
                        copy.manifest_id,
                        copy.trust,
                        output,
                        control=control.fork(),
                    )
                job = self._update(job, output=str(output) if output else None)
            self._finish(job, state="succeeded")
        except Exception as error:
            code = failure_code(error)
            if job.kind == "backup" and isinstance(error, OSError):
                code = "backup_destination_unavailable"
            if isinstance(error, PreservationError) and str(error) in {
                "insufficient_space",
                "selected_kit_envelope_changed",
                "sovereign_capture_trust_missing",
                "plan_changed",
                "source_review_changed",
                "source_model_reference_changed",
                "source_update_failed",
                "backup_destination_changed",
                "public_copy_review_changed",
                "sharing_address_required",
            }:
                code = str(error)
            self._finish(
                job,
                state="cancelled" if failure_code(error) == "cancelled" else "failed",
                error_code=code,
            )
        finally:
            with self._lock:
                self._controls.pop(job.job_id, None)
                self._progress.pop(job.job_id, None)

    def _capture_trust(self) -> TrustProfile:
        with self._lock:
            try:
                return TrustProfile.model_validate_json(
                    read_metadata(self._root, "capture-trust.json")
                )
            except FileNotFoundError:
                key_path = self.home / "capture-key.pem"
                if key_path.exists():
                    raise PreservationError("sovereign_capture_trust_missing") from None
                trust = create_key(key_path, None)
                atomic_bytes(self._root, "capture-trust.json", encode_record(trust))
                return trust

    def _source_receive(
        self, job: Job, plan: dict[str, JsonValue], control: OperationControl
    ) -> None:
        record, envelope, selected, candidates = self.sources.reference(
            str(plan["reference_job_id"])
        )
        if (
            record.envelope_sha256 != plan["envelope_sha256"]
            or record.manifest_id != plan["manifest_id"]
            or digest(encode_record(record)) != plan["reference_sha256"]
            or list(self.sources.http_mirrors(record.mirrors)) != plan["mirrors"]
        ):
            raise PreservationError("source_model_reference_changed")
        trust = TrustProfile.model_validate_json(identity_json(plan["trust"]))
        if (
            shutil.disk_usage(self.home).free
            < cast(int, plan["size_bytes"]) * 4 + 32 * 1024**2
        ):
            raise PreservationError("insufficient_space")
        approved = tuple(cast(list[str], plan["mirrors"]))
        try:
            # Learn from the mirrors this download already contacts, and only
            # those: their signed peer cards seed this computer's own directory.
            self.community.directory.crawl(
                approved,
                TransferPolicy(
                    allow_http=plan["allow_http"] is True,
                    allow_private=plan["allow_private"] is True,
                    timeout=10,
                    deadline_seconds=60,
                ),
                max_documents=len(approved),
                expand=False,
                control=control.fork(),
            )
        except OperationError:
            raise
        except (ValueError, OSError):
            pass  # Discovery is optional; the approved download proceeds.
        policy = TransferPolicy(
            max_bytes=cast(int, plan["max_bytes"]),
            deadline_seconds=86400,
            allow_http=plan["allow_http"] is True,
            allow_private=plan["allow_private"] is True,
        )
        if isinstance(selected, PMFManifest):
            # A public PMF is kept exactly as published, under the publisher
            # keys this user accepted. Its identity is shared by every mirror
            # holding it, so this copy can later help keep it available.
            destination = self.home / "copies" / job.job_id
            with directory(destination.parent, create=True):
                pass
            manifest = fetch(
                record.manifest_id,
                trust,
                tuple(cast(list[str], plan["mirrors"])),
                destination,
                policy,
                control=control.fork(),
                operation_id="receive_" + job.job_id[:56],
                envelope=envelope,
            )
            self._record_copy(
                job,
                Copy(
                    copy_id=job.job_id,
                    manifest_id=record.manifest_id,
                    name=manifest.name,
                    size_bytes=manifest.total_bytes,
                    member_count=len(manifest.members),
                    trust=trust,
                ),
            )
            return
        original = self.home / "work" / job.job_id / "original"
        fetch(
            record.manifest_id,
            trust,
            tuple(cast(list[str], plan["mirrors"])),
            original,
            policy,
            control=control.fork(),
            operation_id="receive_" + job.job_id[:56],
            envelope=envelope,
        )
        owner_trust = self._capture_trust()
        destination = self.home / "copies" / job.job_id
        with directory(destination.parent, create=True):
            pass
        conversion = import_preservation(
            original,
            record.manifest_id,
            trust,
            destination,
            (load_key(self.home / "capture-key.pem", None),),
            source_key_candidates=candidates,
            control=control.fork(),
        )
        manifest = verify_archive(
            destination, conversion.output_identity, owner_trust, control=control.fork()
        )
        self._record_copy(
            job,
            Copy(
                copy_id=job.job_id,
                manifest_id=conversion.output_identity,
                name=manifest.name,
                size_bytes=manifest.total_bytes,
                member_count=len(manifest.members),
                trust=owner_trust,
            ),
        )

    def _receive(
        self, job: Job, plan: dict[str, JsonValue], control: OperationControl
    ) -> None:
        with self._catalog_lock:
            card, proof = self.catalog.select(
                str(plan["catalog_id"]), str(plan["card_id"])
            )
        recipe = registry_recipe(card, proof)
        selected_files = registry_file_map(card)
        if recipe.model_dump(mode="json") != plan["recipe"]:
            raise PreservationError("plan_changed")
        if not plan["historical"]:
            self.catalog.verify_current(proof, card.card_id)
        selected_bytes = cast(int, plan["size_bytes"])
        if shutil.disk_usage(self.home).free < selected_bytes * 4 + 32 * 1024**2:
            raise PreservationError("insufficient_space")
        work = self.home / "work" / job.job_id
        with directory(work, create=True):
            pass
        sources: dict[str, Path] = {}
        deadline = time.monotonic() + 86400
        control.phase("downloading")
        for member in recipe.members:
            selected = work / member.member_id
            with directory(selected, create=True):
                pass
            sources[member.member_id] = selected
            for file in member.files:
                control.checkpoint()
                if file.path == NOTICE_PATH:
                    (selected / file.path).write_bytes(NOTICE)
                    continue
                # The trusted descriptor, not a response header, bounds every object.
                original = selected_files[
                    (member.origin_repository, member.origin_revision, file.path)
                ]
                download_file(
                    member.origin_repository,
                    member.origin_revision,
                    original,
                    selected / file.path,
                    control,
                    deadline=deadline,
                )
        trust = self._capture_trust()
        key = load_key(self.home / "capture-key.pem", None)
        legacy = work / "original"
        identity = pack(recipe, sources, legacy, (key,), control=control.fork())
        destination = self.home / "copies" / job.job_id
        with directory(destination.parent, create=True):
            pass
        conversion = import_preservation(
            legacy, identity, trust, destination, (key,), control=control.fork()
        )
        manifest_id = conversion.output_identity
        manifest = verify_archive(
            destination, manifest_id, trust, control=control.fork()
        )
        self._record_copy(
            job,
            Copy(
                copy_id=job.job_id,
                manifest_id=manifest_id,
                name=manifest.name,
                size_bytes=manifest.total_bytes,
                member_count=len(manifest.members),
                trust=trust,
            ),
        )

    def _record_copy(self, job: Job, copy: Copy) -> None:
        with self._lock:
            self._check()
            self._journal = self._journal.model_copy(
                update={"copies": (*self._journal.copies, copy)}
            )
            self._update(job, copy_id=copy.copy_id, state="succeeded")
            # A new backup joins the community mirror with no per-model step.
            if self.community.consented():
                self._community_refresh()

    def _community_refresh(self) -> None:
        """Re-sync and restart the community mirror, starting it if needed."""
        with self._lock:
            if self._closing:
                return
            self._community_wake.set()
            if self._community_control is not None:
                self._community_control.cancel()
            if self._community_thread is None or not self._community_thread.is_alive():
                self._community_thread = threading.Thread(
                    target=self._community_loop,
                    name="sovereign-community",
                    daemon=True,
                )
                self._community_thread.start()
            if self._gossip_thread is None or not self._gossip_thread.is_alive():
                self._gossip_thread = threading.Thread(
                    target=self._gossip_loop, name="sovereign-gossip", daemon=True
                )
                self._gossip_thread.start()

    def _gossip_loop(self) -> None:
        """Every 15 minutes while sharing: announce, find and check mirrors."""
        try:
            while not self._stopping.is_set() and self.community.consented():
                control = OperationControl()
                with self._lock:
                    if self._stopping.is_set():
                        return
                    # Retained so shutdown can cancel a round in progress.
                    self._gossip_control = control
                try:
                    self.community.gossip(control)
                except (ValueError, OSError):
                    pass  # Discovery is best effort; serving never depends on it.
                finally:
                    with self._lock:
                        self._gossip_control = None
                # DHT entries expire after about half an hour; re-announce sooner.
                if self._stopping.wait(900):
                    return
        finally:
            self.community.close_rendezvous()

    def _community_loop(self) -> None:
        while True:
            control = OperationControl()
            copies: tuple[Copy, ...] = ()
            with self._lock:
                self._community_wake.clear()
                stop = self._closing or not self.community.consented()
                if stop:
                    self._community_control = None
                else:
                    self._community_control = control
                    copies = self._journal.copies
            if stop:
                # The controller has drained. Retry clearing a standing grant
                # that a concurrent upload reservation kept from clearing, unless
                # sharing has been turned back on meanwhile.
                for _attempt in range(10):
                    if self.community.consented() or self.community.revoke_grant():
                        break
                    time.sleep(0.5)
                with self._lock:
                    # A refresh that arrived while this worker was retiring saw
                    # it alive; keep serving instead of dropping that request.
                    if not self._closing and self.community.consented():
                        continue
                    self._community_thread = None
                return
            try:
                self.community.sync(copies, control)
                if not self._community_wake.is_set():
                    self.community.last_error = None
                    try:
                        self.community.announce()
                    except (ValueError, OSError):
                        pass  # Serving does not depend on announcing.
                    self.community.serve(control)
            except OperationCancelled:
                continue
            except (ValueError, OSError) as error:
                self.community.last_error = failure_code(error)
                # Retry after a quiet interval, or as soon as anything changes.
                self._community_wake.wait(300)

    def _import(
        self, job: Job, plan: dict[str, JsonValue], control: OperationControl
    ) -> None:
        destination = self.home / "copies" / job.job_id
        with directory(destination.parent, create=True):
            pass
        trust = TrustProfile.model_validate_json(identity_json(plan["trust"]))
        uploaded = self.home / "uploads" / str(plan["upload_id"])
        envelope = read_kit_envelope(uploaded)
        if digest(envelope) != plan["envelope_sha256"]:
            raise PreservationError("selected_kit_envelope_changed")
        inspection = inspect_kit(uploaded)
        from ..pmf_models import PMFManifest

        if isinstance(inspection.manifest, Manifest):
            work = self.home / "work" / job.job_id
            with directory(work, create=True):
                pass
            original = work / "original"
            import_kit(
                uploaded,
                original,
                trust=trust,
                expected_manifest_id=str(plan["manifest_id"]),
                expected_envelope=envelope,
                control=control.fork(),
            )
            owner_trust = self._capture_trust()
            conversion = import_preservation(
                original,
                inspection.manifest_id,
                trust,
                destination,
                (load_key(self.home / "capture-key.pem", None),),
                source_key_candidates=trust,
                control=control.fork(),
            )
            manifest_id, copy_trust = conversion.output_identity, owner_trust
        else:
            import_kit(
                uploaded,
                destination,
                trust=trust,
                expected_manifest_id=str(plan["manifest_id"]),
                expected_envelope=envelope,
                control=control.fork(),
            )
            manifest_id, copy_trust = inspection.manifest_id, trust
        manifest = verify_archive(
            destination, manifest_id, copy_trust, control=control.fork()
        )
        if not isinstance(manifest, PMFManifest):
            raise PreservationError("pmf_capture_required")
        self._record_copy(
            job,
            Copy(
                copy_id=job.job_id,
                manifest_id=manifest_id,
                name=manifest.name,
                size_bytes=manifest.total_bytes,
                member_count=len(manifest.members),
                trust=copy_trust,
            ),
        )

    def open_kit(self, job_id: str) -> BinaryIO:
        """Open an exact successful kit export; callers never supply a path."""
        with self._lock:
            self._check()
            job = self._job(job_id)
            if job.kind != "kit_export" or job.state != "succeeded":
                raise PreservationError("kit_not_ready")
            descriptor = os.open(
                self.home / "kits" / (job_id + ".foxkit"),
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            )
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                os.close(descriptor)
                raise PreservationError("kit_not_ready")
            return os.fdopen(descriptor, "rb")

    def publication_review(self, plan_id: str) -> bytes:
        """Return only a hash-bound public descriptor and decoded original cards."""
        with self._lock:
            self._check()
            if re.fullmatch(r"[0-9a-f]{64}", plan_id) is None:
                raise PreservationError("invalid_control")
            plan = strict_json(read_metadata(self._root, "plans/" + plan_id + ".json"))
            if (
                not isinstance(plan, dict)
                or digest(identity_json(plan)) != plan_id
                or plan.get("kind") != "publish"
            ):
                raise PreservationError("plan_changed")
            document = self.sharing.preview(plan)
            if digest(document) != plan["public_review_sha256"]:
                raise PreservationError("public_copy_review_changed")
            return document

    def _handoff_ready(self, job: Job) -> bool:
        if (
            job.job_id not in self._sharing_files
            or job.kind != "share"
            or job.state != "running"
        ):
            return False
        try:
            plan = strict_json(
                read_metadata(self._root, "plans/" + job.plan_id + ".json")
            )
            if not isinstance(plan, dict) or digest(identity_json(plan)) != job.plan_id:
                return False
            self.sharing.require_session(plan)
            return True
        except (ValueError, OSError):
            return False

    def open_handoff(self, job_id: str) -> BinaryIO:
        """Read one current explicit sharing session's verified metadata file."""
        with self._lock:
            self._check()
            job = self._job(job_id)
            observed = self._sharing_observations.get(job_id)
            if (
                job.kind != "share"
                or job.state != "running"
                or observed is None
                or not observed.running
                or not self._handoff_ready(job)
            ):
                raise PreservationError("sharing_not_ready")
            descriptor = os.open(
                self.home / "sharing/handoffs" / (job_id + ".foxhandoff"),
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            )
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                os.close(descriptor)
                raise PreservationError("sharing_not_ready")
            stream = os.fdopen(descriptor, "rb")
            try:
                from ..models import MAX_METADATA_BYTES

                data = stream.read(MAX_METADATA_BYTES + 1)
                if (
                    len(data) > MAX_METADATA_BYTES
                    or digest(data) != self._sharing_files[job_id]
                ):
                    raise PreservationError("sharing_not_ready")
                stream.seek(0)
                return stream
            except BaseException:
                stream.close()
                raise

    def restored_directory(self, job_id: str) -> Path:
        """Resolve only a successful own-job restore, for an explicit reveal action."""
        with self._lock:
            self._check()
            job = self._job(job_id)
            if job.kind != "restore" or job.state != "succeeded":
                raise PreservationError("restore_not_ready")
            output = self.home / "restored" / job_id
            with directory(output):
                pass
            return output

    def artifact_directory(self, job_id: str) -> Path:
        """Resolve an own completed restore or backup, never a caller-supplied path."""
        with self._lock:
            self._check()
            job = self._job(job_id)
            if job.kind != "backup":
                return self.restored_directory(job_id)
            if job.state != "succeeded":
                raise PreservationError("backup_not_ready")
            plan = strict_json(
                read_metadata(self._root, "plans/" + job.plan_id + ".json")
            )
            if not isinstance(plan, dict) or digest(identity_json(plan)) != job.plan_id:
                raise PreservationError("plan_changed")
        return self.backups.folder(plan)
