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
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from typing import BinaryIO, Literal, cast

from pydantic import Field, JsonValue

from ..archives import verify_archive
from ..cards import ArtifactFile, ModelCardEnvelope, identity_json
from ..kits import export_kit, import_kit, inspect_kit, read_kit_envelope
from ..models import Manifest, PreservationError, Record, TrustProfile
from ..operations import OperationControl, Progress, failure_code
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
from .catalog import RegistryCatalog
from .presentation import (
    PURPOSE_WORDS,
    card_summary,
    copy_summary,
    manifest_summary,
    metadata_page,
    search_words,
)
from .selection import NOTICE, NOTICE_PATH, download_file, registry_recipe
from .sources import SourceLibrary

JobKind = Literal[
    "catalog_refresh",
    "receive",
    "kit_export",
    "restore",
    "kit_import",
    "source_refresh",
    "source_metadata",
    "source_receive",
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

    schema_version: Literal["sovereign.local-owner.v1"] = "sovereign.local-owner.v1"
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
            self._lock = threading.RLock()
            self._catalog_lock = threading.RLock()
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="sovereign"
            )
            self._controls: dict[str, OperationControl] = {}
            self._progress: dict[str, Progress] = {}
            self._summaries: dict[str, dict[str, JsonValue]] = {}
            self._closed = False
            try:
                self._journal = Journal.model_validate_json(
                    read_metadata(self._root, "journal.json")
                )
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
        with self._lock:
            for control in self._controls.values():
                control.cancel()
        self._executor.shutdown(wait=True)
        self._closed = True
        self._stack.close()

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
            try:
                summary: dict[str, JsonValue] = copy_summary(
                    self.home / "copies" / item.copy_id, item.manifest_id, item.trust
                )
            except (ValueError, OSError):
                summary = {"metadata_status": "unavailable"}
            self._summaries[item.copy_id] = summary
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
                    + identity_json(details).decode()
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
        value = {**value, "expires_at": int(time.time()) + 600}
        plan_id = digest(identity_json(value))
        atomic_bytes(self._root, "plans/" + plan_id + ".json", identity_json(value))
        return {
            **{
                key: item
                for key, item in value.items()
                if key not in {"recipe", "trust", "selection", "source_policy"}
            },
            "plan_id": plan_id,
        }

    def control(self, action: str, body: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Validate explicit view controls; never accept arbitrary API forwarding."""
        with self._lock:
            self._check()
            if action == "job-read" and set(body) == {"job_id"}:
                return self._job(str(body["job_id"])).model_dump(mode="json")
            if action == "source-read" and not body:
                return self.sources.read()
            if action == "source-search":
                return self.sources.search(body)
            if action == "source-review" and set(body) == {"upload_id"}:
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
                record, _, manifest, candidates = self.sources.reference(job.job_id)
                return {
                    "reference_job_id": job.job_id,
                    "manifest_id": record.manifest_id,
                    "name": manifest.name,
                    "size_bytes": manifest.total_bytes,
                    "member_count": len(manifest.members),
                    "members": [
                        member.origin_repository for member in manifest.members[:8]
                    ],
                    "keys": [key.key_id for key in candidates.keys],
                    "threshold": candidates.threshold,
                    "revoked_keys": list(candidates.revoked_keys),
                    "mirrors": list(
                        self.sources.http_mirrors(record.selection.mirrors)
                    ),
                }
            if action in {"search", "copy-search"}:
                if (
                    set(body) - {"query", "offset", "purpose"}
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
                    str(body.get("query", "")), offset, str(body.get("purpose", ""))
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
                if kind in {"source_refresh", "source_metadata"}:
                    return self._plan(self.sources.plan(body))
                if kind == "source_receive":
                    job = self._job(str(body.get("reference_job_id")))
                    if job.kind != "source_metadata" or job.state != "succeeded":
                        raise PreservationError("model_metadata_unavailable")
                    return self._plan(self.sources.receive_plan(body))
                if kind == "receive" and set(body) == {"kind", "catalog_id", "card_id"}:
                    with self._catalog_lock:
                        card, proof = self.catalog.select(
                            str(body["catalog_id"]), str(body["card_id"])
                        )
                    recipe = registry_recipe(card, proof)
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
                            "details": card_summary(card),
                            "historical": historical,
                            "members": [
                                item.origin_repository for item in recipe.members
                            ],
                            "size_bytes": sum(
                                self._selected_file(
                                    card,
                                    member.origin_repository,
                                    member.origin_revision,
                                    file.path,
                                ).size_bytes
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
        self._executor.submit(self._run, job, plan, control)
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
            else:
                with self._lock:
                    copy = self._copy(str(plan["copy_id"]))
                store = self.home / "copies" / copy.copy_id
                verify_archive(
                    store, copy.manifest_id, copy.trust, control=control.fork()
                )
                if job.kind == "kit_export":
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
                job = self._update(job, output=str(output))
            self._update(job, state="succeeded")
        except Exception as error:
            code = failure_code(error)
            if isinstance(error, PreservationError) and str(error) in {
                "insufficient_space",
                "selected_kit_envelope_changed",
                "sovereign_capture_trust_missing",
                "plan_changed",
                "source_review_changed",
                "source_model_reference_changed",
                "source_update_failed",
            }:
                code = str(error)
            self._update(
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
        record, envelope, _, candidates = self.sources.reference(
            str(plan["reference_job_id"])
        )
        if (
            record.envelope_sha256 != plan["envelope_sha256"]
            or record.manifest_id != plan["manifest_id"]
            or digest(encode_record(record)) != plan["reference_sha256"]
            or list(self.sources.http_mirrors(record.selection.mirrors))
            != plan["mirrors"]
        ):
            raise PreservationError("source_model_reference_changed")
        trust = TrustProfile.model_validate_json(identity_json(plan["trust"]))
        if (
            shutil.disk_usage(self.home).free
            < cast(int, plan["size_bytes"]) * 4 + 32 * 1024**2
        ):
            raise PreservationError("insufficient_space")
        original = self.home / "work" / job.job_id / "original"
        fetch(
            record.manifest_id,
            trust,
            tuple(cast(list[str], plan["mirrors"])),
            original,
            TransferPolicy(
                max_bytes=cast(int, plan["max_bytes"]),
                deadline_seconds=86400,
                allow_http=plan["allow_http"] is True,
                allow_private=plan["allow_private"] is True,
            ),
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
                original = self._selected_file(
                    card, member.origin_repository, member.origin_revision, file.path
                )
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

    @staticmethod
    def _selected_file(
        card: ModelCardEnvelope, repository: str, revision: str, path: str
    ) -> ArtifactFile:
        bundle = card.artifact.bundle
        if (repository, revision) == (
            card.artifact.repository,
            card.artifact.revision,
        ) and bundle:
            for file in bundle.files:
                if file.path == path:
                    return file
        image = card.card.get("image")
        if isinstance(image, dict) and isinstance(image.get("components"), list):
            for component in cast(list[JsonValue], image["components"]):
                if (
                    isinstance(component, dict)
                    and (component.get("repository"), component.get("revision"))
                    == (repository, revision)
                    and isinstance(component.get("files"), list)
                ):
                    for item in cast(list[JsonValue], component["files"]):
                        file = ArtifactFile.model_validate_json(identity_json(item))
                        if file.path == path:
                            return file
        raise PreservationError("missing selected file")

    def _record_copy(self, job: Job, copy: Copy) -> None:
        with self._lock:
            self._check()
            self._journal = self._journal.model_copy(
                update={"copies": (*self._journal.copies, copy)}
            )
            self._update(job, copy_id=copy.copy_id, state="succeeded")

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
