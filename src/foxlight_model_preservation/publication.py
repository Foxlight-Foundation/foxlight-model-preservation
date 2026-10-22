"""Reviewed public publications, separate from private capture evidence."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import cast, final

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import Field, JsonValue

from .assurance import load_retained_card
from .cards import identity_json
from .models import (
    MAX_METADATA_BYTES,
    Digest,
    FileIdentity,
    Identifier,
    Intake,
    Manifest,
    ManifestMember,
    PreservationError,
    Record,
    TrustProfile,
)
from .operations import OperationControl, RequestChanged
from .signing import Envelope, digest, encode_record, public_record, sign_manifest
from .storage import (
    atomic_bytes,
    copy_verified_object,
    directory,
    manifest_path,
    object_path,
    read_metadata,
    unique_objects,
    verify_object,
    verify_store,
    verify_store_contents,
    writer_lock,
)

_PRIVATE_COMPONENTS = frozenset(
    {
        ".skulk",
        ".preservation",
        ".operations",
        ".ssh",
        ".aws",
        ".git",
        ".env",
        "credentials",
        "id_rsa",
        "id_ed25519",
        "installed-records.json",
        "installed-card.json",
        "owner-trust.json",
        ".foxlight-capture-notice.txt",
    }
)
PUBLISHER_NOTICE_PATH = "NOTICE.publisher.txt"
MAX_PUBLISHER_NOTICE_BYTES = 1024 * 1024


@final
class PublicMemberTerms(Record):
    """Explicit terms for a retained data member; never a license grant."""

    member_id: Identifier
    license_id: str = Field(min_length=1, max_length=200)
    terms_reference: str = Field(min_length=1, max_length=2048)
    decision: str = Field(min_length=1, max_length=4096)
    notice_paths: tuple[str, ...] = Field(min_length=1, max_length=100)


@final
class PublisherNotice(Record):
    """Explicit publisher-supplied UTF-8 notice, separate from upstream evidence.

    The fixed filename cannot replace a source file or introduce executable
    paths. Supplying this text declares no upstream authorship or license grant.
    Original required notices remain mandatory and unchanged.
    """

    member_id: Identifier
    text: str = Field(min_length=1, max_length=65536)


@final
class PublicationRequest(Record):
    """Public-facing metadata and terms the publisher deliberately selects."""

    name: str = Field(min_length=1, max_length=200)
    workloads: tuple[str, ...] = Field(min_length=1, max_length=100)
    runtime_requirements: tuple[str, ...] = Field(default=(), max_length=100)
    members: tuple[PublicMemberTerms, ...] = Field(min_length=1, max_length=256)
    notices: tuple[PublisherNotice, ...] = Field(default=(), max_length=256)


def _notice_bytes(request: PublicationRequest) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for notice in request.notices:
        if (
            notice.member_id in result
            or "\x00" in notice.text
            or not notice.text.strip()
        ):
            raise PreservationError("duplicate or non-text publisher notice")
        try:
            result[notice.member_id] = notice.text.encode("utf-8")
        except UnicodeError as error:
            raise PreservationError("publisher notice must be UTF-8 text") from error
    if sum(len(data) for data in result.values()) > MAX_PUBLISHER_NOTICE_BYTES:
        raise PreservationError("publisher notices exceed metadata allowance")
    return result


def _public_manifest(source: Manifest, request: PublicationRequest) -> Manifest:
    selected = {
        member.member_id: member
        for member in source.members
        if not (member.role == "notices" and member.member_id == "source-evidence")
    }
    terms = {item.member_id: item for item in request.members}
    notices = _notice_bytes(request)
    if not set(notices).issubset(selected):
        raise PreservationError("publisher notice must select a public data member")
    if len(terms) != len(request.members) or set(terms) != set(selected):
        raise PreservationError(
            "public terms must select every data member exactly once"
        )
    if source.root_member not in selected:
        raise PreservationError("public publication requires a data root")
    members: list[ManifestMember] = []
    for member in selected.values():
        choice = terms[member.member_id]
        if choice.license_id.strip().casefold() in {
            "unresolved-owner-capture",
            "unknown",
            "unresolved",
        }:
            raise PreservationError(
                "public publication needs a separate resolved terms decision"
            )
        files = tuple(
            file for file in member.files if file.path != ".foxlight-capture-notice.txt"
        )
        if member.member_id in notices:
            if any(file.path == PUBLISHER_NOTICE_PATH for file in member.files):
                raise PreservationError("publisher notice cannot replace a source file")
            if PUBLISHER_NOTICE_PATH not in choice.notice_paths:
                raise PreservationError("publisher notice must be included in terms")
            data = notices[member.member_id]
            files = (
                *files,
                FileIdentity(
                    path=PUBLISHER_NOTICE_PATH, size=len(data), sha256=digest(data)
                ),
            )
        for file in files:
            if any(
                part.casefold() in _PRIVATE_COMPONENTS
                or part.casefold().startswith(".env.")
                or part.casefold().endswith((".pem", ".key"))
                for part in PurePosixPath(file.path).parts
            ):
                raise PreservationError(
                    "private operational files cannot enter a public publication"
                )
        if (
            not set(member.intake.notice_paths)
            .difference({".foxlight-capture-notice.txt"})
            .issubset(choice.notice_paths)
        ):
            raise PreservationError(
                "original notices must remain in the public selection"
            )
        retained: dict[str, JsonValue] | None = None
        if member.retained_card is not None:
            # Only the explicit public card/proof profile is transferable.
            # Arbitrary retained metadata may be private operational evidence.
            profile = load_retained_card(identity_json(member.retained_card))
            retained = cast(dict[str, JsonValue], profile.model_dump(mode="json"))
        members.append(
            ManifestMember.model_validate(
                {
                    **member.model_dump(
                        exclude={"files", "intake", "dependencies", "retained_card"}
                    ),
                    "files": files,
                    "dependencies": tuple(
                        item for item in member.dependencies if item in selected
                    ),
                    "retained_card": retained,
                    "intake": Intake(
                        audience="public",
                        license_id=choice.license_id,
                        terms_reference=choice.terms_reference,
                        decision=choice.decision,
                        notice_paths=choice.notice_paths,
                    ),
                }
            )
        )
    return Manifest(
        schema_version="foxlight.model-preservation.v1",
        name=request.name,
        root_member=source.root_member,
        workloads=request.workloads,
        runtime_requirements=request.runtime_requirements,
        members=tuple(members),
    )


def review_publication(source: Manifest, request: PublicationRequest) -> Manifest:
    """Transform authenticated metadata for review without IO or weight hashing.

    Args:
        source: An already signature-verified manifest selected by the caller.
        request: Deliberate public metadata, per-member terms and optional notices.

    Returns:
        Exact planned public metadata; its identity is SHA-256 of encode_record.

    This establishes neither current byte availability nor redistribution rights.
    Source notices remain intact. Added notice text is publisher supplied, not
    original signed upstream evidence. Actual publication rechecks every byte
    and requires the exact reviewed identity and a distinct publishing key.
    """
    return _public_manifest(source, request)


def plan_publication(
    source: Path,
    source_id: str,
    trust: TrustProfile,
    request: PublicationRequest,
    *,
    control: OperationControl | None = None,
) -> Manifest:
    """Verify the capture and return exact public metadata for read-only review.

    The new identity is SHA-256 of ``encode_record(result)``. Review every retained
    field and selected file: this prevents known operational-evidence exports,
    not secrets hidden inside an intentionally selected model file or public card.
    """
    return review_publication(
        verify_store(source, source_id, trust, control=control), request
    )


def publish_public(
    source: Path,
    source_id: str,
    trust: TrustProfile,
    request: PublicationRequest,
    destination: Path,
    keys: tuple[Ed25519PrivateKey, ...],
    *,
    expected_manifest_id: Digest,
    control: OperationControl | None = None,
) -> str:
    """Publish reviewed public files under a distinct key into a fresh directory.

    Existing directories, including empty folders and prior publications, refuse
    without modification. An interrupted fresh output may contain only selected
    public objects; it is not a complete publication until its envelope appears.
    """
    with (control or OperationControl()).operation("pack") as active:
        return _publish_public(
            source,
            source_id,
            trust,
            request,
            destination,
            keys,
            expected_manifest_id=expected_manifest_id,
            control=active,
        )


def _publish_public(
    source: Path,
    source_id: str,
    trust: TrustProfile,
    request: PublicationRequest,
    destination: Path,
    keys: tuple[Ed25519PrivateKey, ...],
    *,
    expected_manifest_id: Digest,
    control: OperationControl,
) -> str:
    """Publish reviewed public files into a separate root and identity.

    An explicit local publisher controls paths/keys. Remote applications must map
    enrolled opaque handles to them. Signatures attest an owner publication;
    neither new terms nor a new signature manufacture upstream authorization.
    """
    if destination.exists() or destination.is_symlink():
        raise PreservationError("public output must be a fresh directory")
    if source.resolve().is_relative_to(
        destination.resolve()
    ) or destination.resolve().is_relative_to(source.resolve()):
        raise PreservationError("public and private archive roots must be separate")
    manifest = review_publication(
        verify_store_contents(source, source_id, trust, control), request
    )
    manifest_id = digest(encode_record(manifest))
    if manifest_id != expected_manifest_id:
        raise RequestChanged("public publication differs from the reviewed plan")
    if manifest_id == source_id:
        raise PreservationError(
            "already-public content needs an exact mirror, not a new owner publication"
        )
    with directory(source) as origin:
        envelope = Envelope.model_validate_json(
            read_metadata(origin, manifest_path(source_id))
        )
        source_keys = {item.keyid for item in envelope.signatures} | {
            key.key_id for key in trust.keys
        }
    if not keys or any(public_record(key).key_id in source_keys for key in keys):
        raise PreservationError(
            "public publication requires a separate publishing identity"
        )
    encoded = encode_record(sign_manifest(manifest, keys))
    if len(encoded) > MAX_METADATA_BYTES:
        raise PreservationError("public envelope exceeds metadata size limit")
    active = control
    active.checkpoint()
    try:
        destination.mkdir(mode=0o700, parents=True)
    except FileExistsError as error:
        raise PreservationError("public output must be a fresh directory") from error
    with (
        directory(source) as origin,
        directory(destination) as target,
        writer_lock(target),
    ):
        added = {digest(data): data for data in _notice_bytes(request).values()}
        for file in unique_objects(manifest):
            if file.sha256 in added:
                active.checkpoint()
                atomic_bytes(target, object_path(file.sha256), added[file.sha256])
                active.advance(copied=file.size)
            else:
                copy_verified_object(
                    origin, object_path(file.sha256), target, file, active
                )
        active.phase("checking")
        for file in unique_objects(manifest):
            verify_object(target, file, control=active)
        active.phase("publishing")
        active.checkpoint()
        atomic_bytes(target, manifest_path(manifest_id), encoded)
        active.mark_committed()

    return manifest_id
