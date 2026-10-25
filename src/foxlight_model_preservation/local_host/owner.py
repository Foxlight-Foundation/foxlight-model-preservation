"""Durable private model custody for the independent Sovereign application.

Only explicit submissions start finite work. A restarted owner records interrupted
jobs and never silently replays networking. Closing the view leaves jobs running;
closing the application requests a cooperative stop and retains its journal.
"""

from __future__ import annotations

import os
import re
import shutil
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
from ..operations import OperationControl, failure_code
from ..pmf import restore_pmf
from ..pmf_preservation import import_preservation
from ..registry_proof import verify_registry_proof
from ..signing import (
    create_key,
    digest,
    encode_record,
    load_key,
    strict_json,
)
from ..storage import atomic_bytes, directory, pack, read_metadata, writer_lock
from .catalog import RegistryCatalog
from .selection import NOTICE, NOTICE_PATH, download_file, registry_recipe


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
    kind: Literal["catalog_refresh", "receive", "kit_export", "restore", "kit_import"]
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
            self._lock = threading.RLock()
            self._catalog_lock = threading.RLock()
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="sovereign"
            )
            self._controls: dict[str, OperationControl] = {}
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

    def read(self) -> dict[str, JsonValue]:
        """Read safe receipts and public capture keys; no signing material or paths."""
        with self._lock:
            self._check()
            return {
                "host_kind": "sovereign",
                "locations": [],
                "models": [],
                "copies": [
                    item.model_dump(mode="json") for item in self._journal.copies
                ],
                "jobs": [
                    {
                        **item.model_dump(mode="json"),
                        "bytes_received": self._controls[
                            item.job_id
                        ].progress.bytes_received
                        if item.job_id in self._controls
                        else 0,
                    }
                    for item in self._journal.jobs[-48:]
                ],
                "next_offset": None,
                "storage_label": "Sovereign on this device",
            }

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
                if key not in {"recipe", "trust"}
            },
            "plan_id": plan_id,
        }

    def control(self, action: str, body: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Validate explicit view controls; never accept arbitrary API forwarding."""
        with self._lock:
            self._check()
            if action == "search":
                if (
                    set(body) - {"query", "offset"}
                    or not isinstance(body.get("query", ""), str)
                    or len(str(body.get("query", ""))) > 500
                ):
                    raise PreservationError("invalid_control")
                offset = body.get("offset", 0)
                if type(offset) is not int or not 0 <= offset <= 100_000:
                    raise PreservationError("invalid_control")
                return self.catalog.search(str(body.get("query", "")), offset)
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
                if kind == "receive" and set(body) == {"kind", "catalog_id", "card_id"}:
                    with self._catalog_lock:
                        card, proof = self.catalog.select(
                            str(body["catalog_id"]), str(body["card_id"])
                        )
                    recipe = registry_recipe(card, proof)
                    # An offline snapshot never silently acquires freshness.
                    try:
                        verify_registry_proof(
                            encode_record(proof), self.catalog.anchor, card.card_id
                        )
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
            if action == "upload-create" and set(body) == {"size_bytes"}:
                size = body["size_bytes"]
                if type(size) is not int or not 0 < size <= 2**50:
                    raise PreservationError("invalid_control")
                uploads = self.home / "uploads"
                with directory(uploads, create=True) as root:
                    if len(os.listdir(root)) >= 16:
                        raise PreservationError("remove_unused_kit_uploads")
                    name = uuid.uuid4().hex
                    atomic_bytes(root, name, b"")
                    atomic_bytes(
                        root, name + ".json", identity_json({"size_bytes": size})
                    )
                return {
                    "upload_id": name,
                    "size_bytes": size,
                    "offset": 0,
                    "complete": False,
                }
            raise PreservationError("sovereign_operation_unavailable")

    def _upload(self, identifier: str) -> dict[str, JsonValue]:
        if not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise PreservationError("invalid_upload")
        from ..storage import open_file

        with directory(self.home / "uploads") as root:
            meta = strict_json(read_metadata(root, identifier + ".json"))
            if not isinstance(meta, dict):
                raise PreservationError("invalid_upload")
            with open_file(root, identifier) as stream:
                offset = os.fstat(stream.fileno()).st_size
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
        if previous:
            if previous.plan_id != plan_id:
                raise PreservationError("request_changed")
            return previous.model_dump(mode="json")
        if cast(int, plan["expires_at"]) <= time.time():
            raise PreservationError("plan_missing_or_expired")
        if len(self._journal.jobs) >= 256 or len(self._controls) >= 4:
            raise PreservationError("sovereign_job_capacity_reached")
        job = Job(
            job_id=digest(
                identity_json({"operation_id": operation_id, "plan_id": plan_id})
            ),
            operation_id=operation_id,
            plan_id=plan_id,
            kind=cast(
                Literal[
                    "catalog_refresh", "receive", "kit_export", "restore", "kit_import"
                ],
                plan["kind"],
            ),
            state="queued",
            created_at=time.time(),
            name=str(plan["name"]),
        )
        self._journal = self._journal.model_copy(
            update={"jobs": (*self._journal.jobs, job)}
        )
        self._save()
        control = OperationControl()
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
            self._update(
                job,
                state="cancelled" if failure_code(error) == "cancelled" else "failed",
                error_code=failure_code(error),
            )
        finally:
            with self._lock:
                self._controls.pop(job.job_id, None)

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
            verify_registry_proof(
                encode_record(proof), self.catalog.anchor, card.card_id
            )
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
        import stat

        with self._lock:
            self._check()
            job = next(
                (item for item in self._journal.jobs if item.job_id == job_id), None
            )
            if job is None or job.kind != "kit_export" or job.state != "succeeded":
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
            job = next(
                (item for item in self._journal.jobs if item.job_id == job_id), None
            )
            if job is None or job.kind != "restore" or job.state != "succeeded":
                raise PreservationError("restore_not_ready")
            output = self.home / "restored" / job_id
            with directory(output):
                pass
            return output
