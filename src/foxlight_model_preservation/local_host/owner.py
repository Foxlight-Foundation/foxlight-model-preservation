"""Durable private model custody for the independent Sovereign application.

Only explicit submissions start finite work. A restarted owner records interrupted
jobs and never silently replays networking. Closing the view leaves jobs running;
closing the application requests a cooperative stop and retains its journal.
"""

from __future__ import annotations

import contextlib
import http.client
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
from urllib.parse import urlsplit

from pydantic import Field, JsonValue

from .. import hub
from ..archives import ArchiveManifest, verify_archive
from ..cards import ArtifactFile, ModelCardEnvelope, identity_json
from ..kits import export_kit, import_kit, inspect_kit, read_kit_envelope
from ..ledger import TransferBudget
from ..licences import (
    LICENCE_DEFINITION_VERSION,
    TermsAcceptance,
    member_terms,
    terms_review,
)
from ..mirror_directory import fetch_with_mirrors, load_seeds, permitted
from ..mirror_holdings import fetch_held_manifest, holding
from ..mirror_models import MirrorObservation
from ..models import (
    MAX_TOTAL_BYTES,
    FileIdentity,
    Manifest,
    ManifestMember,
    PreservationError,
    Recipe,
    Record,
    TrustProfile,
)
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
from ..storage import (
    atomic_bytes,
    directory,
    object_path,
    pack,
    read_metadata,
    writer_lock,
)
from ..transfer import SourcesExhausted, TransferPolicy, fetch_object
from .backups import Backups
from .catalog import RegistryCatalog, with_licence
from .community import CommunityMirror
from .library import Library, move_tree
from .presentation import (
    PURPOSE_WORDS,
    captured_card_metadata,
    card_license,
    card_summary,
    copy_metadata,
    manifest_summary,
    metadata_page,
    search_words,
)
from .selection import (
    NOTICE,
    NOTICE_PATH,
    OriginHTTPError,
    check_companions,
    companion_files,
    download_file,
    held_recipe,
    hub_recipe,
    registry_file_map,
    registry_recipe,
)
from .sharing import Sharing
from .sources import HandoffReference, SourceLibrary


def recipe_task(plan: dict[str, JsonValue]) -> str | None:
    """The Hub task a plan was reviewed with, if any."""
    details = plan.get("details")
    task = details.get("task") if isinstance(details, dict) else None
    return task if isinstance(task, str) else None


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

    _seeds_learned_at = float("-inf")
    # Reaching community mirrors for their holdings: public HTTPS only.
    _held_policy = TransferPolicy(timeout=15, deadline_seconds=60)

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
            self.library = Library(self.home)
            self.sources = SourceLibrary(self.home)
            self.backups = Backups(self.home)
            self.sharing = Sharing(self.home, self.library)
            self.community = CommunityMirror(self.home, self.sharing)
            self._community_wake = threading.Event()
            self._community_control: OperationControl | None = None
            self._community_thread: threading.Thread | None = None
            self._gossip_thread: threading.Thread | None = None
            self._gossip_control: OperationControl | None = None
            self._gossip_wake = threading.Event()
            self._withdraw_thread: threading.Thread | None = None
            self._move_thread: threading.Thread | None = None
            self._move_control: OperationControl | None = None
            self._move: dict[str, JsonValue] | None = None
            self._withdraw_control: OperationControl | None = None
            self._stopping = threading.Event()
            self._lock = threading.RLock()
            self._idle = threading.Condition(self._lock)
            self._backup_calls = 0
            self._closing = False
            self._catalog_lock = threading.RLock()
            # The Hugging Face token, read from the keychain at most once.
            self._token_lock = threading.Lock()
            self._token_cache: tuple[str | None] | None = None
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
            else:
                # A withdrawal that failed before the last shutdown resumes.
                self._bucket_withdrawal()
        except BaseException:
            self._stack.close()
            raise

    def _data(self) -> Path:
        """The library folder new copies and their working data go to."""
        return self.library.root()

    def _uploads(self) -> Path:
        return self._data() / "uploads"

    def _work(self, job_id: str) -> Path:
        # Resumed work stays where it started; new work uses the library folder.
        return self.library.find("work/" + job_id)

    def _copy_store(self, copy_id: str) -> Path:
        """Where a saved copy is: the library folder first, then earlier ones."""
        return self.library.find("copies/" + copy_id)

    def _library_view(self) -> dict[str, JsonValue]:
        view = self.library.view()
        elsewhere: list[tuple[str, Path]] = []
        if view["available"]:
            with contextlib.suppress(OSError, ValueError):
                elsewhere = self._elsewhere()
        moved = {
            relative.removeprefix("copies/")
            for relative, _ in elsewhere
            if relative.startswith("copies/")
        }
        return {
            **view,
            "elsewhere_copies": len(moved),
            "elsewhere_bytes": sum(
                copy.size_bytes
                for copy in self._journal.copies
                if copy.copy_id in moved
            ),
            "move": self._move,
        }

    def _elsewhere(self) -> list[tuple[str, Path]]:
        """Library entries kept outside the current library folder, copies first."""
        current = self.library.root()
        found: list[tuple[str, Path]] = []
        for copy in self._journal.copies:
            store = self._copy_store(copy.copy_id)
            if store.parent.parent != current and store.is_dir():
                found.append(("copies/" + copy.copy_id, store))
        for root in self.library.roots():
            mirrors = root / "sharing/mirrors"
            if root == current or not mirrors.is_dir():
                continue
            for name in sorted(os.listdir(mirrors)):
                relative = "sharing/mirrors/" + name
                if re.fullmatch(r"[0-9a-f]{64}", name) and (
                    self.library.find(relative) == mirrors / name
                ):
                    found.append((relative, mirrors / name))
        return found

    def _library_control(
        self, action: str, body: dict[str, JsonValue]
    ) -> dict[str, JsonValue] | None:
        if action == "library-review":
            path = body.get("path")
            if set(body) != {"path"} or not isinstance(path, str):
                raise PreservationError("invalid_control")
            # A NAS can block filesystem calls: never under the owner lock.
            with self._backup_call():
                return self.library.review(path)
        if action == "library-setup":
            if set(body) != {"review_id", "confirmed"} or body["confirmed"] is not True:
                raise PreservationError("invalid_control")
            with self._lock:
                if self._move_thread is not None:
                    raise PreservationError("library_moving")
            with self._backup_call():
                self.library.setup(str(body["review_id"]))
            with self._lock:
                return self._library_view()
        if action == "library-use-home":
            if body:
                raise PreservationError("invalid_control")
            with self._lock:
                self._check()
                if self._move_thread is not None:
                    raise PreservationError("library_moving")
                self.library.use_home()
                return self._library_view()
        if action == "library-move-stop":
            if body:
                raise PreservationError("invalid_control")
            with self._lock:
                if self._move_control is not None:
                    self._move_control.cancel()
                return self._library_view()
        if action != "library-move":
            return None
        if body:
            raise PreservationError("invalid_control")
        with self._lock:
            self._check()
            if self._closing:
                raise PreservationError("sovereign_owner_closing")
            if self._move_thread is not None:
                return self._library_view()
            # Moving a copy that a save, export or sharing session is reading
            # would break it: wait until nothing else is running.
            if self._controls:
                raise PreservationError("library_busy_try_again")
            items = self._elsewhere()
            if not items:
                return self._library_view()
            total = 0
            for _relative, source in items:
                for folder, _folders, files in os.walk(source):
                    total += sum(
                        os.lstat(Path(folder) / name).st_size for name in files
                    )
            control = OperationControl()
            self._move_control = control
            self._move = {
                "state": "moving",
                "items_done": 0,
                "items_total": len(items),
                "bytes_done": 0,
                "bytes_total": total,
                "error": None,
            }
            self._move_thread = threading.Thread(
                target=self._move_library,
                args=(items, control),
                name="sovereign-library-move",
                daemon=True,
            )
            self._move_thread.start()
            return self._library_view()

    def _move_library(
        self, items: list[tuple[str, Path]], control: OperationControl
    ) -> None:
        """Copy, verify, switch, then remove each entry; stops cleanly any time."""
        links: dict[str, Path] = {}
        copies = {copy.copy_id: copy for copy in self._journal.copies}

        def progress(size: int) -> None:
            with self._lock:
                if self._move is not None:
                    self._move["bytes_done"] = (
                        cast(int, self._move["bytes_done"]) + size
                    )

        state, error = "done", None
        try:
            for relative, source in items:
                control.checkpoint()
                current = self.library.root()
                target = current / relative
                with directory(target.parent, create=True):
                    pass
                staging = target.parent / (".moving-" + target.name)
                try:
                    move_tree(source, staging, links, control, progress)
                    copy_id = relative.removeprefix("copies/")
                    if relative.startswith("copies/") and copy_id in copies:
                        copy = copies[copy_id]
                        verify_archive(
                            staging,
                            copy.manifest_id,
                            copy.trust,
                            control=control.fork(),
                        )
                    with self._lock:
                        os.rename(staging, target)
                        with directory(target.parent) as parent:
                            os.fsync(parent)
                except BaseException:
                    with contextlib.suppress(OSError):
                        shutil.rmtree(staging)
                    raise
                # The new copy is found first from now on; the old one goes.
                shutil.rmtree(source)
                with self._lock:
                    if self._move is not None:
                        self._move["items_done"] = (
                            cast(int, self._move["items_done"]) + 1
                        )
            settings = self.library.settings()
            for location in settings.previous:
                root = Path(location.path)
                if not any(
                    (root / folder).is_dir() and os.listdir(root / folder)
                    for folder in ("copies", "sharing/mirrors")
                ):
                    self.library.forget_previous(location.path)
        except OperationCancelled:
            state = "stopped"
        except (OSError, ValueError) as caught:
            state, error = "failed", failure_code(caught)
        finally:
            with self._lock:
                if self._move is not None:
                    self._move["state"] = state
                    self._move["error"] = error
                self._move_thread = None
                self._move_control = None
                self._summaries.clear()

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
            withdraw = self._withdraw_thread
            mover = self._move_thread
            if self._move_control is not None:
                self._move_control.cancel()
            self._stopping.set()
            self._gossip_wake.set()
            if self._gossip_control is not None:
                self._gossip_control.cancel()
            if self._withdraw_control is not None:
                self._withdraw_control.cancel()
            self._community_wake.set()
            if self._community_control is not None:
                self._community_control.cancel()
        if community is not None:
            community.join()
        if gossip is not None:
            gossip.join()
        if withdraw is not None:
            withdraw.join()
        if mover is not None:
            mover.join()
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
            # A disconnected library drive hides pending uploads; it never
            # stops the Library screen from reading.
            if self.library.available():
                with directory(self._uploads(), create=True) as source:
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
                        "sources": self._job_sources(item.job_id)
                        if item.kind == "receive" and item.state == "succeeded"
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
                "hub_search": self.hub_search_enabled(),
                "library": self._library_view(),
                "hub_token": self._hub_token_user(),
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
                    self._copy_store(item.copy_id), item.manifest_id, item.trust
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

    def _accept_terms(
        self,
        plan: dict[str, JsonValue],
        plan_id: str,
        operation_id: str,
        accepted: JsonValue,
    ) -> None:
        """Require the reviewed terms to be accepted, and record it before work.

        Mirrors the Hugging Face gate: a model whose licence has use
        restrictions or conditions to pass on is kept only after the person
        ticks "I accept these terms" for exactly those licences. The record is
        written before the job starts, beside the job it belongs to.
        """
        terms = plan.get("terms")
        required = terms.get("accept") if isinstance(terms, dict) else None
        wanted = (
            sorted(str(item) for item in required) if isinstance(required, list) else []
        )
        job_id = digest(
            identity_json({"operation_id": operation_id, "plan_id": plan_id})
        )
        if any(job.job_id == job_id for job in self._journal.jobs):
            return  # A replay of a started job; its acceptance is on record.
        if accepted is None:
            if wanted:
                raise PreservationError("terms_acceptance_required")
            return
        if (
            not isinstance(accepted, list)
            or not all(isinstance(item, str) for item in accepted)
            or sorted(cast(list[str], accepted)) != wanted
        ):
            raise PreservationError("terms_acceptance_required")
        if not wanted or not isinstance(terms, dict):
            return
        record = TermsAcceptance(
            plan_id=plan_id,
            job_id=job_id,
            name=str(plan.get("name", ""))[:1000] or "model",
            definition=cast(int, terms.get("definition", LICENCE_DEFINITION_VERSION)),
            accepted=tuple(wanted),
            accepted_at=int(time.time()),
        )
        atomic_bytes(
            self._root, "terms-acceptance/" + job_id + ".json", encode_record(record)
        )

    def _plan(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if value.get("kind") in {"receive", "source_receive"}:
            # This is the same conservative reserve enforced again by the
            # worker, observed now so the person can choose before downloading.
            selected_bytes = cast(int, value["size_bytes"])
            value = {
                **value,
                "required_free_bytes": selected_bytes * 4 + 32 * 1024**2,
                "free_bytes": self.library.free_bytes(),
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
        if action == "community-check-address" and not body:
            # A best-effort request to the owner's own address; never hold the
            # owner lock across the network.
            with self._lock:
                self._check()
                if self._closing:
                    raise PreservationError("sovereign_owner_closing")
            return self.community.check_address()
        if action in {"community-bucket-connect", "community-bucket-disconnect"}:
            # Checking a bucket uploads and downloads over the network; do not
            # hold the owner lock meanwhile.
            with self._lock:
                self._check()
                if self._closing:
                    raise PreservationError("sovereign_owner_closing")
            if action == "community-bucket-connect":
                result = self.community.connect_bucket(body)
            elif not body:
                self.community.disconnect_bucket()
                result = {"status": "disconnected"}
            else:
                raise PreservationError("invalid_control")
            with self._lock:
                # The next discovery round uploads to the new bucket and
                # announces its address.
                if self._gossip_control is not None:
                    self._gossip_control.cancel()
                self._gossip_wake.set()
                return {**result, **self.community.read(self._journal.copies)}
        library_result = self._library_control(action, body)
        if library_result is not None:
            return library_result
        hub_result = self._hub_control(action, body)
        if hub_result is not None:
            return hub_result
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
            if action == "community-bucket-contents" and set(body) <= {"offset"}:
                offset = body.get("offset", 0)
                if type(offset) is not int or not 0 <= offset <= 1000:
                    raise PreservationError("invalid_control")
                return self.community.bucket_contents(offset)
            if action == "community-bucket-keys" and set(body) == {"manifest_id"}:
                manifest_id = body["manifest_id"]
                if not isinstance(manifest_id, str):
                    raise PreservationError("invalid_control")
                return self.community.bucket_model_keys(manifest_id)
            if action == "community-configure":
                try:
                    policy = self.community.configure(body)
                finally:
                    # Any policy change re-runs discovery under the new choice,
                    # and an opt-out leaves the DHT immediately.
                    if self._gossip_control is not None:
                        self._gossip_control.cancel()
                    self._gossip_wake.set()
                    # Turning sharing off always stops the running mirror, even
                    # if saving or revoking failed part-way.
                    if not self.community.consented():
                        self._bucket_withdrawal()
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
                    self._copy_store(copy.copy_id), copy.manifest_id, copy.trust
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
                    directory(self._uploads()) as root,
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
                                self._copy_store(copy.copy_id),
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
                        metadata = captured_card_metadata(proof, card)
                        licence = self._registry_licence(
                            str(body["catalog_id"]), card, metadata
                        )
                    recipe = registry_recipe(card, proof, licence=licence)
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
                    details = card_summary(
                        card,
                        metadata=with_licence(metadata, licence),
                        selected_files=(
                            path
                            for repository, revision, path in selected_files
                            if (repository, revision)
                            == (
                                card.artifact.repository,
                                card.artifact.revision,
                            )
                        ),
                    )
                    card_license = details.get("license")
                    return self._plan(
                        {
                            "kind": "receive",
                            "name": recipe.name,
                            "catalog_id": body["catalog_id"],
                            "card_id": card.card_id,
                            "recipe": recipe.model_dump(mode="json"),
                            "details": details,
                            "terms": terms_review(
                                member_terms(
                                    recipe.members,
                                    fallback=card_license
                                    if isinstance(card_license, str)
                                    else "",
                                    # Only the card's own artifact inherits its
                                    # licence; companions keep their own.
                                    fallback_origin=(
                                        card.artifact.repository,
                                        card.artifact.revision,
                                    ),
                                )
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
                    info = inspect_kit(self._uploads() / str(body["upload_id"]))
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
                                    self._uploads() / str(body["upload_id"])
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
                and set(body) - {"accepted_terms", "accept_unconfirmed_origin"}
                == {"plan_id", "operation_id", "confirmed"}
                and body["confirmed"] is True
                and body.get("accept_unconfirmed_origin", False) in (True, False)
            ):
                plan_id = str(body["plan_id"])
                if not re.fullmatch(r"[0-9a-f]{64}", plan_id):
                    raise PreservationError("invalid_control")
                plan = strict_json(
                    read_metadata(self._root, "plans/" + plan_id + ".json")
                )
                if not isinstance(plan, dict) or digest(identity_json(plan)) != plan_id:
                    raise PreservationError("plan_changed")
                self._accept_terms(
                    plan, plan_id, str(body["operation_id"]), body.get("accepted_terms")
                )
                details = plan.get("details")
                if (
                    plan.get("origin") == "mirror"
                    and isinstance(details, dict)
                    and details.get("origin_check") != "matches"
                    and body.get("accept_unconfirmed_origin") is not True
                ):
                    # Nothing independent vouches for these bytes; the owner
                    # must say so explicitly, and the copy records it.
                    raise PreservationError("held_origin_acknowledgement_required")
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
                with directory(self._uploads()) as root:
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
                uploads = self._uploads()
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

        with directory(self._uploads()) as root:
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
                directory(self._uploads()) as root,
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
        if self._move_thread is not None:
            raise PreservationError("library_moving")
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
                store = self._copy_store(copy.copy_id)
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
                    with directory(self._data() / "kits", create=True):
                        pass
                    output = self._data() / "kits" / (job.job_id + ".foxkit")
                    export_kit(
                        store,
                        copy.manifest_id,
                        copy.trust,
                        output,
                        control=control.fork(),
                    )
                else:
                    with directory(self._data() / "restored", create=True):
                        pass
                    output = self._data() / "restored" / job.job_id
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
                "origin_unavailable",
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
        if self.library.free_bytes() < cast(int, plan["size_bytes"]) * 4 + 32 * 1024**2:
            raise PreservationError("insufficient_space")
        # The approved hints are bound above; discovered mirrors are only ever
        # appended after them, under the same receive policy, and never change
        # which keys are trusted. Learning reads only the approved mirrors' own
        # peers documents: no DHT, relay or announcement. Starter mirrors are
        # asked only after the approved and known mirrors all failed.
        approved = tuple(cast(list[str], plan["mirrors"]))
        policy = TransferPolicy(
            max_bytes=cast(int, plan["max_bytes"]),
            deadline_seconds=86400,
            allow_http=plan["allow_http"] is True,
            allow_private=plan["allow_private"] is True,
        )
        operation_id = "receive_" + job.job_id[:56]
        if isinstance(selected, PMFManifest):
            # A public PMF is kept exactly as published, under the publisher
            # keys this user accepted. Its identity is shared by every mirror
            # holding it, so this copy can later help keep it available.
            destination = self._data() / "copies" / job.job_id
            with directory(destination.parent, create=True):
                pass
            manifest = self._fetch_with_mirrors(
                record.manifest_id,
                trust,
                approved,
                destination,
                policy,
                control.fork(),
                operation_id,
                envelope,
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
        original = self._work(job.job_id) / "original"
        self._fetch_with_mirrors(
            record.manifest_id,
            trust,
            approved,
            original,
            policy,
            control.fork(),
            operation_id,
            envelope,
        )
        owner_trust = self._capture_trust()
        destination = self._data() / "copies" / job.job_id
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

    def _fetch_with_mirrors(
        self,
        manifest_id: str,
        trust: TrustProfile,
        approved: tuple[str, ...],
        destination: Path,
        policy: TransferPolicy,
        control: OperationControl,
        operation_id: str,
        envelope: bytes,
    ) -> ArchiveManifest:
        """Receive from approved hints and known mirrors, then starters; score them."""
        try:
            seeds = load_seeds()
        except (OSError, ValueError):
            seeds = ()  # An unreadable seed list never blocks an approved receive.
        return fetch_with_mirrors(
            manifest_id,
            trust,
            approved,
            destination,
            policy,
            mirrors=self.community.directory,
            seeds=seeds,
            control=control,
            operation_id=operation_id,
            envelope=envelope,
        )

    def _registry_licence(
        self,
        catalog_id: str,
        card: ModelCardEnvelope,
        metadata: dict[str, JsonValue],
    ) -> str | None:
        """The one licence used to show, review and record a card without its own.

        Precedence matches what the listing shows: the card's own licence (kept
        in the retained card, so nothing extra is recorded), then the catalog's
        metadata, then the registry's separate signed licence file.
        """
        if card_license(card.card):
            return None
        named = metadata.get("license")
        if isinstance(named, str) and named:
            return named
        return self.catalog.licences(catalog_id).get(card.card_id)

    def _job_sources(self, job_id: str) -> JsonValue:
        """Which sources supplied a finished save: origin files and mirror hosts.

        Kept beside the journal rather than in it, so older versions can still
        read the journal. Only host names are reported, never paths.
        """
        try:
            with directory(self.home / "job-sources") as root:
                value = strict_json(read_metadata(root, job_id + ".json"))
        except (FileNotFoundError, PreservationError, ValueError):
            return None
        if not isinstance(value, dict):
            return None
        counts = {
            str(source): count
            for source, count in value.items()
            if type(count) is int and count > 0
        }
        mirrors = sorted(
            {
                urlsplit(source).hostname or "mirror"
                for source in counts
                if source != "origin"
            }
        )[:4]
        return {
            "origin_files": counts.get("origin", 0),
            "mirror_files": sum(
                n for source, n in counts.items() if source != "origin"
            ),
            "mirrors": cast(list[JsonValue], mirrors),
        }

    def _save_file(
        self,
        repository: str,
        revision: str,
        file: ArtifactFile,
        destination: Path,
        staging: Path,
        control: OperationControl,
        *,
        deadline: float,
        preferred: tuple[str, ...] = (),
    ) -> str:
        """Download one signed catalog file, falling back to mirrors by its hash.

        The origin is always tried first; a dropped or failing transfer resumes
        once from its verified prefix. If the origin still fails, or refuses the
        file outright, the same bytes are fetched by SHA-256 from the starter
        (seed) mirrors and mirrors this owner already knows, over HTTPS to
        public addresses only, and accepted only if they match the signed card.
        ``pack`` re-checks every file. Returns ``"origin"`` or the mirror used.
        """
        # Resume buffers and mirror objects live in the job's staging area,
        # outside every member's files, keyed by the signed hash.
        key = (file.object_id or "").removeprefix("sha256:") or digest(
            file.path.encode()
        )
        partial = staging / "partial" / (key + ".partial")
        mirror_store = staging / "mirror"
        origin_error: BaseException | None = None
        for _attempt in range(2):
            try:
                download_file(
                    repository,
                    revision,
                    file,
                    destination,
                    control,
                    deadline=deadline,
                    partial=partial,
                    token=self._hub_token(),
                )
                return "origin"
            except OperationError:
                raise
            except OriginHTTPError as error:
                origin_error = error
                if error.status_code < 500:
                    # Missing, refused or rate limited: mirrors, not a retry.
                    break
            except (OSError, ValueError, http.client.HTTPException) as error:
                origin_error = error
        if file.object_id is None or not file.object_id.startswith("sha256:"):
            raise PreservationError("origin_unavailable") from origin_error
        # The origin may have left a partial file; mirror bytes replace it.
        destination.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
        policy = TransferPolicy()
        seeds = load_seeds()[:4]
        mirrors = self.community.directory
        sha256 = file.object_id.removeprefix("sha256:")
        if seeds and time.monotonic() - self._seeds_learned_at > 3600:
            # Read the seeds' own cards and signed object coverage (at most
            # one hourly, bounded, never expanding past them) so mirrors that
            # claim this file can be asked first.
            self._seeds_learned_at = time.monotonic()
            try:
                mirrors.crawl(
                    seeds,
                    TransferPolicy(deadline_seconds=20, timeout=5),
                    max_documents=len(seeds),
                    expand=False,
                    control=control.fork(),
                )
            except OperationError:
                raise
            except (OSError, ValueError, http.client.HTTPException):
                pass
        claimed = mirrors.object_sources(sha256, policy=policy, limit=4)
        known = sorted(
            address
            for address in mirrors.card_addresses()
            if address not in seeds
            and address not in claimed
            and permitted(address, policy)
        )[:4]
        # Mirrors holding this exact copy first, then those claiming the file,
        # then seeds, then other known ones.
        held = tuple(address for address in preferred if permitted(address, policy))
        sources = tuple(dict.fromkeys((*held, *claimed, *seeds, *known)))
        if not sources:
            raise PreservationError("origin_unavailable") from origin_error
        control.phase("mirroring")
        identity = FileIdentity(
            path=file.path,
            size=file.size_bytes,
            sha256=file.object_id.removeprefix("sha256:"),
        )
        outcomes: dict[str, bool] = {}
        try:
            with directory(mirror_store, create=True) as root:
                used = fetch_object(
                    sources,
                    identity,
                    root,
                    policy,
                    deadline=deadline,
                    # One bad mirror can spend a full transfer twice (a retry)
                    # before the next is tried, so the save's allowance for
                    # this file is bounded but leaves room for that.
                    budget=TransferBudget(
                        min(MAX_TOTAL_BYTES, identity.size * 4 + 64 * 1024**2)
                    ),
                    control=control,
                    source_outcome=outcomes.__setitem__,
                )
        except SourcesExhausted:
            raise PreservationError("origin_unavailable") from origin_error
        finally:
            self.community.directory.record_many(outcomes, seeds=seeds)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(mirror_store / object_path(identity.sha256), destination)
        control.phase("downloading")
        return used

    def _receive(
        self, job: Job, plan: dict[str, JsonValue], control: OperationControl
    ) -> None:
        # Mirrors that hold this exact copy are asked first for its files.
        holders = tuple(
            str(address) for address in cast(list[JsonValue], plan.get("holders") or [])
        )
        if plan.get("origin") == "mirror":
            # The plan holds the exact recipe and files reviewed; the job
            # verifies every byte against them.
            recipe = Recipe.model_validate_json(identity_json(plan["recipe"]))
            selected_files = {
                (str(row[0]), str(row[1]), str(row[2])): ArtifactFile(
                    path=str(row[2]),
                    size_bytes=cast(int, row[3]),
                    object_id=str(row[4]),
                )
                for row in cast(list[list[JsonValue]], plan["files"])
            }
        elif plan.get("origin") == "hub":
            # The reviewed plan carries the exact commit, paths, sizes and
            # SHA-256s; the recipe is rebuilt from them and must match.
            exact = {
                path: (cast(int, value[0]), cast(str, value[1]))
                for path, value in cast(
                    dict[str, list[JsonValue]], plan["files"]
                ).items()
            }
            recipe, selected_files = hub_recipe(
                str(plan["repository"]),
                str(plan["revision"]),
                exact,
                licence=cast(dict[str, JsonValue], plan["details"]).get("license")  # pyright: ignore[reportArgumentType]
                if isinstance(plan.get("details"), dict)
                else None,
                task=recipe_task(plan),
            )
            if recipe.model_dump(mode="json") != plan["recipe"]:
                raise PreservationError("plan_changed")
        else:
            with self._catalog_lock:
                card, proof = self.catalog.select(
                    str(plan["catalog_id"]), str(plan["card_id"])
                )
                licence = self._registry_licence(
                    str(plan["catalog_id"]), card, captured_card_metadata(proof, card)
                )
            recipe = registry_recipe(card, proof, licence=licence)
            selected_files = registry_file_map(card)
            if recipe.model_dump(mode="json") != plan["recipe"]:
                raise PreservationError("plan_changed")
            if not plan["historical"]:
                self.catalog.verify_current(proof, card.card_id)
        selected_bytes = cast(int, plan["size_bytes"])
        if self.library.free_bytes() < selected_bytes * 4 + 32 * 1024**2:
            raise PreservationError("insufficient_space")
        work = self._work(job.job_id)
        with directory(work, create=True):
            pass
        sources: dict[str, Path] = {}
        supplied: dict[str, int] = {}
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
                source = self._save_file(
                    member.origin_repository,
                    member.origin_revision,
                    original,
                    selected / file.path,
                    work / ".staging",
                    control,
                    deadline=deadline,
                    preferred=holders,
                )
                supplied[source] = supplied.get(source, 0) + 1
        with directory(self.home / "job-sources", create=True) as root:
            atomic_bytes(
                root, job.job_id + ".json", identity_json(cast(JsonValue, supplied))
            )
        trust = self._capture_trust()
        key = load_key(self.home / "capture-key.pem", None)
        legacy = work / "original"
        identity = pack(recipe, sources, legacy, (key,), control=control.fork())
        destination = self._data() / "copies" / job.job_id
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

    # Hugging Face is a Find source like any other; nothing here holds the
    # owner lock across the network.
    def hub_search_enabled(self) -> bool:
        """Whether the person chose to send search words to huggingface.co."""
        try:
            value = strict_json(read_metadata(self._root, "hub-search.json"))
        except (OSError, PreservationError):
            return False
        return isinstance(value, dict) and value.get("enabled") is True

    def _hub_control(
        self, action: str, body: dict[str, JsonValue]
    ) -> dict[str, JsonValue] | None:
        if action == "hub-consent":
            if set(body) != {"enabled"} or type(body["enabled"]) is not bool:
                raise PreservationError("invalid_control")
            with self._lock:
                self._check()
                atomic_bytes(
                    self._root,
                    "hub-search.json",
                    identity_json({"enabled": body["enabled"]}),
                )
            return {"hub_search": body["enabled"]}
        if action == "hub-token-set":
            token = body.get("token")
            if set(body) != {"token"} or not isinstance(token, str):
                raise PreservationError("invalid_control")
            self._open_check()
            # Checked with Hugging Face before it is kept; never echoed back.
            user = hub.whoami(token)
            hub.save_token(token)
            with self._lock:
                self._check()
                atomic_bytes(
                    self._root, "hub-token.json", identity_json({"user": user})
                )
            with self._token_lock:
                self._token_cache = (token,)
            return {"hub_token": user}
        if action == "hub-token-remove":
            if body:
                raise PreservationError("invalid_control")
            self._open_check()
            hub.delete_token()
            with self._lock:
                self._check()
                atomic_bytes(
                    self._root, "hub-token.json", identity_json({"user": None})
                )
            with self._token_lock:
                self._token_cache = (None,)
            return {"hub_token": None}
        if action == "held-refresh":
            if body:
                raise PreservationError("invalid_control")
            self._open_check()
            # Bounded discovery from the seed mirrors, expanding to the mirrors
            # they know, so community holdings are findable without sharing.
            with contextlib.suppress(OSError, ValueError, http.client.HTTPException):
                self.community.directory.crawl(
                    load_seeds()[:4],
                    TransferPolicy(deadline_seconds=60, timeout=8),
                    max_documents=16,
                    expand=True,
                    control=OperationControl(),
                )
            return {"mirrors": len(self.community.directory.read().mirrors)}
        if action == "held-search":
            query = body.get("query", "")
            if set(body) - {"query"} or not isinstance(query, str) or len(query) > 200:
                raise PreservationError("invalid_control")
            self._open_check()
            return {
                "source": "mirrors",
                "results": cast(
                    JsonValue,
                    self.community.directory.held_models(
                        query, policy=self._held_policy
                    ),
                ),
            }
        if action == "hub-search":
            query = body.get("query")
            if set(body) != {"query"} or not isinstance(query, str) or len(query) > 200:
                raise PreservationError("invalid_control")
            if not self.hub_search_enabled():
                raise PreservationError("hub_search_not_enabled")
            self._open_check()
            return {
                "source": "huggingface",
                "results": [
                    item.view()  # pyright: ignore[reportReturnType]
                    for item in hub.search(query, token=self._hub_token())
                ],
            }
        if action == "hub-review":
            repository = body.get("repository")
            if set(body) - {"repository", "revision", "variant"} or not isinstance(
                repository, str
            ):
                raise PreservationError("invalid_control")
            revision, variant = body.get("revision"), body.get("variant")
            if (revision is not None and not isinstance(revision, str)) or (
                variant is not None and not isinstance(variant, str)
            ):
                raise PreservationError("invalid_control")
            self._open_check()
            pinned = hub.snapshot(
                hub.repository_id(repository), revision, token=self._hub_token()
            )
            return cast(
                dict[str, JsonValue],
                {**pinned.view(), "default_files": pinned.default_selection(variant)},
            )
        if action != "plan":
            return None
        if body.get("kind") == "receive" and body.get("origin") == "mirror":
            manifest = body.get("manifest")
            if (
                set(body) != {"kind", "origin", "manifest"}
                or not isinstance(manifest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", manifest)
            ):
                raise PreservationError("invalid_control")
            self._open_check()
            return self._held_plan(manifest)
        if body.get("kind") == "receive" and body.get("origin") == "hub":
            if set(body) != {"kind", "origin", "repository", "revision", "files"}:
                raise PreservationError("invalid_control")
            repository, revision, files = (
                body["repository"],
                body["revision"],
                body["files"],
            )
            if (
                not isinstance(repository, str)
                or not isinstance(revision, str)
                or not isinstance(files, list)
                or not files
                or len(files) > hub.MAX_FILES
                or not all(isinstance(item, str) for item in files)
            ):
                raise PreservationError("invalid_control")
            self._open_check()
            pinned = hub.snapshot(
                hub.repository_id(repository), revision, token=self._hub_token()
            )
            return self._hub_plan(pinned, cast(list[str], files))
        if body.get("kind") == "receive" and set(body) == {
            "kind",
            "catalog_id",
            "card_id",
        }:
            # A listing without its own complete file list is saved from the
            # exact revision it pins, with the file list Hugging Face gives
            # for that commit. Nothing about the listing is removed or changed.
            with self._catalog_lock:
                card, proof = self.catalog.select(
                    str(body["catalog_id"]), str(body["card_id"])
                )
                if card.artifact.bundle is not None or card.card.get("image"):
                    return None
                # Unsupported companion declarations are refused before any
                # request reaches Hugging Face.
                check_companions(card, None)
                metadata = captured_card_metadata(proof, card)
                # This path retains no card, so the listing's own licence is
                # carried explicitly; only without one do the catalog and
                # registry licence file apply. Hub metadata is never preferred.
                licence = card_license(card.card) or self._registry_licence(
                    str(body["catalog_id"]), card, metadata
                )
            self._open_check()
            pinned = hub.snapshot(
                card.artifact.repository,
                card.artifact.revision,
                token=self._hub_token(),
            )
            gguf = card.card.get("gguf_file")
            files = set(
                pinned.default_selection(gguf if isinstance(gguf, str) else None)
            )
            named = companion_files(card)
            if any("mmproj" in path.lower() for path in named):
                # The listing names its projector; no other one is taken.
                files = {path for path in files if "mmproj" not in path.lower()}
            available = {item.path for item in pinned.files}
            files |= {path for path in named if path in available}
            check_companions(
                card,
                {
                    (pinned.repository, pinned.revision, item.path): ArtifactFile(
                        path=item.path, size_bytes=item.size_bytes
                    )
                    for item in pinned.files
                    if item.path in files
                },
            )
            return self._hub_plan(
                pinned,
                sorted(files),
                licence=licence,
                listing={
                    "catalog_id": str(body["catalog_id"]),
                    "card_id": card.card_id,
                    "name": card.alias,
                },
            )
        return None

    def _origin_check(self, manifest: PMFManifest | Manifest) -> str:
        """Compare every member's files with Hugging Face at its own revision.

        "matches" only when every member could be checked and all match;
        "differs" when any file is missing or different; otherwise
        "unconfirmed". Never a gate: the result is shown and recorded.
        """
        results: list[str] = []
        for member in manifest.members:
            if isinstance(member, ManifestMember):
                repository, revision = member.origin_repository, member.origin_revision
            else:
                origin = member.origin
                repository = origin.repository if origin is not None else None
                revision = origin.revision if origin is not None else None
            listed = {
                file.path: file.sha256
                for file in member.files
                if file.path != NOTICE_PATH
            }
            if not repository or not revision or not listed:
                results.append("unconfirmed")
                continue
            try:
                pinned = hub.snapshot(repository, revision, token=self._hub_token())
                exact = hub.exact_hashes(
                    pinned, listed, OperationControl(), token=self._hub_token()
                )
                results.append(
                    "matches"
                    if all(exact[path][1] == sha for path, sha in listed.items())
                    else "differs"
                )
            except PreservationError as error:
                results.append(
                    "differs"
                    if str(error) in {"hub_file_unknown", "hub_file_changed"}
                    else "unconfirmed"
                )
        if "differs" in results:
            return "differs"
        return "matches" if results and set(results) == {"matches"} else "unconfirmed"

    def _held_plan(self, manifest_id: str) -> dict[str, JsonValue]:
        """Plan saving a copy that community mirrors keep, by its content address.

        The manifest's identity proves its file list; no signer is trusted.
        The origin it claims is checked against Hugging Face when possible,
        and the result is recorded with the copy rather than gating it.
        """
        holders = self.community.directory.holders(
            manifest_id, policy=self._held_policy
        )
        if not holders:
            raise PreservationError("held_copy_unavailable")
        manifest, signers = fetch_held_manifest(manifest_id, holders, self._held_policy)
        described = holding(manifest_id, manifest)
        repository, revision = described.origin_repository, described.origin_revision
        check = self._origin_check(manifest)
        hosts = sorted({urlsplit(address).hostname or address for address in holders})
        origin_text = (
            f"{repository}@{revision}" if repository and revision else "not stated"
        )
        decision = (
            f"Kept from community mirrors ({', '.join(hosts[:8])}). "
            f"Claimed origin {origin_text}: "
            + {
                "matches": "matches Hugging Face.",
                "differs": "differs from Hugging Face.",
                "unconfirmed": "not confirmed (Hugging Face unavailable).",
            }[check]
            + f" Original signer {', '.join(signers[:4])}. "
            + (
                ""
                if check == "matches"
                else "Kept only after the owner acknowledged that its origin could "
                "not be confirmed. "
            )
            + "Explicit private preservation; no redistribution grant."
        )
        recipe, selected = held_recipe(manifest, decision)
        licence = described.licence
        return self._plan(
            {
                "kind": "receive",
                "origin": "mirror",
                "name": recipe.name,
                "manifest": manifest_id,
                "holders": cast(JsonValue, list(holders[:8])),
                "recipe": recipe.model_dump(mode="json"),
                "files": [
                    [repo, rev, path, file.size_bytes, file.object_id or ""]
                    for (repo, rev, path), file in sorted(selected.items())
                ],
                "details": {
                    "license": licence,
                    "origin_repository": repository,
                    "origin_revision": revision,
                    "origin_check": check,
                    "mirrors": cast(JsonValue, hosts[:8]),
                    "signers": cast(JsonValue, list(signers[:4])),
                    "file_list": "mirror",
                },
                "terms": terms_review(
                    member_terms(
                        recipe.members,
                        fallback=licence or "",
                        fallback_origin=(repository or "", revision or ""),
                    )
                ),
                "historical": False,
                "file_count": len(selected),
                "projector_included": any(
                    "mmproj" in path.lower() for (_r, _v, path) in selected
                ),
                "members": [member.origin_repository for member in recipe.members],
                "size_bytes": sum(file.size_bytes for file in selected.values()),
                "source": "community mirrors, and huggingface.co while it still has "
                "the same files",
            }
        )

    def _hub_token_user(self) -> str | None:
        """The Hugging Face account a stored token belongs to, if any."""
        try:
            value = strict_json(read_metadata(self._root, "hub-token.json"))
        except (OSError, PreservationError):
            return None
        user = value.get("user") if isinstance(value, dict) else None
        return user if isinstance(user, str) else None

    def _hub_token(self) -> str | None:
        """The stored token, read from the keychain at most once.

        An unavailable keychain means downloads proceed without a token rather
        than fail; the person still sees their account under Source and privacy.
        """
        with self._token_lock:
            if self._token_cache is None:
                token = None
                if self._hub_token_user() is not None:
                    try:
                        token = hub.load_token()
                    except PreservationError:
                        token = None
                self._token_cache = (token,)
            return self._token_cache[0]

    def _open_check(self) -> None:
        with self._lock:
            self._check()
            if self._closing:
                raise PreservationError("sovereign_owner_closing")

    def _hub_plan(
        self,
        pinned: hub.HubSnapshot,
        files: list[str],
        *,
        licence: str | None = None,
        listing: dict[str, str] | None = None,
    ) -> dict[str, JsonValue]:
        selected = sorted(set(files))
        for path in selected:
            pinned.file(path)
        exact = hub.exact_hashes(
            pinned, selected, OperationControl(), token=self._hub_token()
        )
        licence = licence or pinned.licence
        recipe, _ = hub_recipe(
            pinned.repository,
            pinned.revision,
            exact,
            licence=licence,
            task=pinned.task,
        )
        size = sum(size for size, _ in exact.values())
        return self._plan(
            {
                "kind": "receive",
                "origin": "hub",
                "name": listing["name"] if listing else recipe.name,
                "repository": pinned.repository,
                "revision": pinned.revision,
                "files": {path: [size, sha] for path, (size, sha) in exact.items()},
                "recipe": recipe.model_dump(mode="json"),
                "listing": cast(JsonValue, listing),
                "details": {
                    "license": licence,
                    "task": pinned.task,
                    "library": pinned.library,
                    "gated": pinned.gated,
                    "file_list": "huggingface",
                },
                "terms": terms_review(
                    member_terms(
                        recipe.members,
                        fallback=licence or "",
                        fallback_origin=(pinned.repository, pinned.revision),
                    )
                ),
                "historical": False,
                "file_count": len(exact),
                "projector_included": any("mmproj" in path.lower() for path in exact),
                "members": [pinned.repository],
                "size_bytes": size,
                "source": "huggingface.co and public HTTPS CDN redirects",
            }
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

    # Seconds before retrying a bucket sync that failed; doubles up to a round.
    _bucket_retry = 30.0

    def _gossip_loop(self) -> None:
        """Every 15 minutes while sharing: announce, find and check mirrors.

        A round whose bucket upload or withdrawal failed (other than at the
        owner's upload limit, which renews by itself) comes back sooner, backing
        off from ``_bucket_retry`` seconds, so one refused request does not keep
        a model off the bucket until the next discovery round.
        """
        retry = self._bucket_retry
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
                # A policy change wakes this worker at once, so opting out or
                # turning the DHT off takes effect immediately.
                delay = 900.0
                try:
                    bucket = self.community.bucket()
                except (ValueError, OSError):
                    bucket = None
                if (
                    bucket is not None
                    and bucket.last_error is not None
                    and bucket.last_error != "bucket_upload_limit_reached"
                ):
                    delay, retry = retry, min(retry * 2, 900.0)
                else:
                    retry = self._bucket_retry
                self._gossip_wake.wait(delay)
                self._gossip_wake.clear()
        finally:
            self.community.close_rendezvous()
            # Sharing was turned off: withdraw models from a connected bucket
            # too, so it stops offering them.
            self._bucket_withdrawal()

    def _bucket_selection_changed(
        self, before: frozenset[str], after: frozenset[str]
    ) -> None:
        """Bring a connected bucket up to date with what is served now.

        New models upload now rather than at the next discovery round, and a
        failed upload is retried once the mirror has applied changed settings
        such as upload limits. When a model stops being served, an upload in
        progress is cancelled so the withdrawal happens first, not after hours
        of uploading, and that model's upload is never finished.
        """
        bucket = self.community.bucket()
        if bucket is None:
            return
        if before - after:
            with self._lock:
                if self._gossip_control is not None:
                    self._gossip_control.cancel()
        if before != after or bucket.last_error is not None:
            self._gossip_wake.set()

    # Seconds before retrying a withdrawal the bucket refused; doubles.
    _withdraw_retry = 30.0

    def _bucket_withdrawal(self) -> None:
        """Start withdrawing from the bucket while sharing is off, if needed."""
        with self._lock:
            if self._closing or self.community.consented():
                return
            if self._withdraw_thread is not None and self._withdraw_thread.is_alive():
                return
            self._withdraw_thread = threading.Thread(
                target=self._withdraw_loop, name="sovereign-withdraw", daemon=True
            )
            self._withdraw_thread.start()

    def _withdraw_loop(self) -> None:
        """Retry until the bucket offers nothing, sharing resumes, or shutdown.

        Opting out must stop the bucket offering models even if it was briefly
        unreachable, and even across restarts, without renewed consent.
        """
        delay = self._withdraw_retry
        while not self._stopping.is_set() and not self.community.consented():
            state = self.community.bucket()
            if state is None or not (state.synced or state.pending):
                return
            control = OperationControl()
            with self._lock:
                if self._stopping.is_set():
                    return
                self._withdraw_control = control
            try:
                self.community.sync_bucket(control)
            except (ValueError, OSError):
                pass  # Recorded on the bucket state; retried below.
            finally:
                with self._lock:
                    self._withdraw_control = None
            state = self.community.bucket()
            if state is None or not (state.synced or state.pending):
                return
            self._stopping.wait(delay)
            delay = min(delay * 2, 900.0)

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
                before = self.community.held()
                self.community.sync(copies, control)
                after = self.community.held()
                self._bucket_selection_changed(before, after)
                if after and not before:
                    # The DHT announces only while something is served: the
                    # first served model starts a discovery round at once.
                    self._gossip_wake.set()
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
        destination = self._data() / "copies" / job.job_id
        with directory(destination.parent, create=True):
            pass
        trust = TrustProfile.model_validate_json(identity_json(plan["trust"]))
        uploaded = self._uploads() / str(plan["upload_id"])
        envelope = read_kit_envelope(uploaded)
        if digest(envelope) != plan["envelope_sha256"]:
            raise PreservationError("selected_kit_envelope_changed")
        inspection = inspect_kit(uploaded)
        from ..pmf_models import PMFManifest

        if isinstance(inspection.manifest, Manifest):
            work = self._work(job.job_id)
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
                self.library.find("kits/" + job_id + ".foxkit"),
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

    def bucket_handoff(self, manifest_id: str) -> bytes:
        """Return the one-file handoff for one model the connected bucket serves.

        Public metadata only: the signed manifest, the public trust profile of
        its signing key and the bucket's public address. Never a bucket key.
        """
        with self._lock:
            self._check()
            return self.community.bucket_handoff(manifest_id)

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
            output = self.library.find("restored/" + job_id)
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
