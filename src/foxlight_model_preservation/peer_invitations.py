"""Portable peer handoffs: offline review, independent trust and durable receiving."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path
from typing import Literal

from pydantic import Field, TypeAdapter, field_validator, model_validator

from .invitations import accept_model_keys
from .ledger import TransferBudget
from .models import (
    MAX_METADATA_BYTES,
    Digest,
    Manifest,
    PreservationError,
    Record,
    TrustProfile,
)
from .operations import OperationError
from .peer_runtime import PeerClient, content_id
from .signing import (
    Envelope,
    canonical_bytes,
    digest,
    encode_record,
    strict_json,
    verify_manifest,
)
from .storage import (
    CHUNK_SIZE,
    atomic_bytes,
    directory,
    file_identity,
    manifest_path,
    object_path,
    observe_objects,
    parent_directory,
    unique_objects,
    verify_object,
    writer_lock,
)


class PeerInvitation(Record):
    """Candidate-signed public model metadata and unsigned content-provider hints.

    Reading grants no network, signer, registry, code or serving permission.
    This distinct version leaves the released HTTP invitation contract unchanged.
    """

    schema_version: Literal["foxlight.model-preservation.peer-invitation.v1"] = (
        "foxlight.model-preservation.peer-invitation.v1"
    )
    manifest_id: Digest
    envelope: Envelope
    candidate_trust: TrustProfile
    mirrors: tuple[str, ...] = Field(min_length=1, max_length=16)

    @field_validator("mirrors")
    @classmethod
    def check_mirrors(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Accept only canonical IPFS roots, never gateways, URLs or local paths."""
        for value in values:
            if not value.startswith("ipfs://"):
                raise PreservationError("peer invitation requires IPFS root hints")
            content_id(value.removeprefix("ipfs://"))
        if len(set(values)) != len(values):
            raise PreservationError("duplicate peer model hint")
        return values

    @property
    def manifest(self) -> Manifest:
        """Inspect candidate signatures; this does not authenticate the publisher."""
        return verify_manifest(
            encode_record(self.envelope), self.candidate_trust, self.manifest_id
        )

    @model_validator(mode="after")
    def check_public(self) -> PeerInvitation:
        """Require bounded exact public metadata before any network effect."""
        if len(encode_record(self)) > MAX_METADATA_BYTES or any(
            member.intake.audience != "public" for member in self.manifest.members
        ):
            raise PreservationError("bounded public peer invitation required")
        return self


def read_peer_invitation(data: bytes) -> PeerInvitation:
    """Read a complete bounded handoff offline, retaining opaque card data."""
    try:
        return PeerInvitation.model_validate_json(canonical_bytes(strict_json(data)))
    except ValueError as error:
        raise PreservationError("invalid or unsafe peer invitation") from error


def peer_invitation_from_envelope(
    envelope: bytes, manifest_id: str, trust: TrustProfile, mirrors: tuple[str, ...]
) -> PeerInvitation:
    """Prepare an offline candidate reference without downloading model objects."""
    return PeerInvitation(
        manifest_id=manifest_id,
        envelope=Envelope.model_validate_json(canonical_bytes(strict_json(envelope))),
        candidate_trust=trust,
        mirrors=mirrors,
    )


def authenticate_peer_invitation(
    invitation: PeerInvitation, trust: TrustProfile
) -> Manifest:
    """Authenticate independently selected keys and exact metadata before bytes."""
    checked = read_peer_invitation(encode_record(invitation))
    return verify_manifest(encode_record(checked.envelope), trust, checked.manifest_id)


def accept_peer_invitation_keys(
    invitation: PeerInvitation, key_ids: tuple[str, ...]
) -> TrustProfile:
    """Use the existing copy-scoped threshold/revocation policy without enrollment."""
    checked = read_peer_invitation(encode_record(invitation))
    return accept_model_keys(
        checked.envelope, checked.manifest_id, checked.candidate_trust, key_ids
    )


def inspect_peer_model(
    manifest_id: str, trust: TrustProfile, mirrors: tuple[str, ...], client: PeerClient
) -> PeerInvitation:
    """Fetch only bounded signed metadata; independent selected trust stays required."""
    if client.profile.mode != "receive":
        raise PreservationError("metadata receiving needs a receive-only peer")
    TypeAdapter(Digest).validate_python(manifest_id, strict=True)
    roots = tuple(value for value in mirrors if value.startswith("ipfs://"))
    if not 1 <= len(roots) <= 16:
        raise PreservationError("bounded peer model hints required")
    before = client.exported_bytes
    for mirror in roots:
        client.control.checkpoint()
        try:
            cid = content_id(mirror.removeprefix("ipfs://"))
            remaining = 32 * 1024**2 - (client.exported_bytes - before)
            data = client.cat(
                "/ipfs/" + cid + "/" + manifest_path(manifest_id),
                maximum=min(MAX_METADATA_BYTES, remaining),
            )
            return peer_invitation_from_envelope(data, manifest_id, trust, roots)
        except (OSError, ValueError):
            client.control.checkpoint()
    raise PreservationError("peer model metadata unavailable")


def fetch_peer_invitation(
    invitation: PeerInvitation,
    trust: TrustProfile,
    destination: Path,
    client: PeerClient,
    *,
    maximum_bytes: int = 20 * 1024**3,
    operation_id: str | None = None,
) -> Manifest:
    """Receive an independently trusted exact closure with conservative retry charges.

    Optional local operation accounting is fsynced before each file request.
    Verified objects survive interruption; uncertain file reservations are never
    refunded after restart. Byte accounting excludes native DAG/wire/cache work.
    """
    with client.control.operation("fetch"):
        return _fetch_peer_invitation(
            invitation,
            trust,
            destination,
            client,
            maximum_bytes=maximum_bytes,
            operation_id=operation_id,
        )


def _fetch_peer_invitation(
    invitation: PeerInvitation,
    trust: TrustProfile,
    destination: Path,
    client: PeerClient,
    *,
    maximum_bytes: int,
    operation_id: str | None,
) -> Manifest:
    manifest = authenticate_peer_invitation(invitation, trust)
    if client.profile.mode != "receive":
        raise PreservationError("model receiving needs a receive-only peer")
    files = unique_objects(manifest)
    if sum(file.size for file in files) > maximum_bytes:
        raise PreservationError("complete model exceeds peer allowance")
    with directory(destination, create=True) as target, writer_lock(target):
        pinned = os.fstat(target)
        binding = digest(
            canonical_bytes(
                {
                    "kind": "peer-invitation-receive.v1",
                    "invitation": invitation.model_dump(mode="json"),
                    "trust": trust.model_dump(mode="json"),
                    "maximum_bytes": maximum_bytes,
                    "device": str(pinned.st_dev),
                    "inode": str(pinned.st_ino),
                }
            )
        )
        budget = TransferBudget(
            maximum_bytes,
            root=target,
            operation_id=operation_id,
            request_digest=binding,
        )
        missing, _, _ = observe_objects(target, files, client.control)
        # These hash-named private staging slots belong to this receiver only.
        # A prior crash cannot retain an unverified extra full-file allocation.
        for file in missing:
            try:
                os.unlink(".peer-object-" + file.sha256, dir_fd=target)
            except FileNotFoundError:
                pass
        if (
            shutil.disk_usage(target).free
            < sum(file.size for file in missing) + 16 * CHUNK_SIZE
        ):
            raise PreservationError("destination has insufficient free space")
        for file in missing:
            completed = False
            for mirror in invitation.mirrors:
                client.control.checkpoint()
                try:
                    client.control.phase("downloading")
                    staged_name = ".peer-object-" + file.sha256
                    try:
                        descriptor = os.open(
                            staged_name,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600,
                            dir_fd=target,
                        )
                        with os.fdopen(descriptor, "wb") as sink:
                            # Reserve only after local creation succeeds, and
                            # charge the declared maximum before network RPC.
                            budget.reserve(file.size)
                            budget.consume(file.size)
                            if file.size:
                                client.cat(
                                    "/ipfs/"
                                    + mirror[7:]
                                    + "/"
                                    + object_path(file.sha256),
                                    maximum=file.size,
                                    sink=sink,
                                )
                            sink.flush()
                            os.fsync(sink.fileno())
                        observed = file_identity(
                            target, staged_name, file.size, control=client.control
                        )
                        if observed.sha256 != file.sha256:
                            raise PreservationError(
                                "peer object failed integrity verification"
                            )
                        with parent_directory(
                            target, object_path(file.sha256), create=True
                        ) as (parent, name):
                            os.replace(
                                staged_name, name, src_dir_fd=target, dst_dir_fd=parent
                            )
                            os.fsync(parent)
                        os.fsync(target)
                    finally:
                        try:
                            os.unlink(staged_name, dir_fd=target)
                        except FileNotFoundError:
                            pass
                    completed = True
                    break
                except OperationError:
                    raise
                except OSError:
                    # A local write/fsync/rename failure is not another supplier
                    # opportunity. Stop before any further charged request.
                    budget.abandon()
                    raise
                except ValueError:
                    budget.abandon()
                    client.control.checkpoint()
            if not completed:
                raise PreservationError("no peer supplied the selected verified object")
        for file in files:
            verify_object(target, file, control=client.control)
        current = destination.lstat()
        if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
            pinned.st_dev,
            pinned.st_ino,
        ):
            raise PreservationError("peer destination changed")
        client.control.checkpoint()
        atomic_bytes(
            target,
            manifest_path(invitation.manifest_id),
            encode_record(invitation.envelope),
        )
        client.control.mark_committed()
    return manifest
