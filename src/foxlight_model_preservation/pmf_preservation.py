"""Lossless preservation-v1/Skulk bridge; source signatures stay on source bytes.

The common PMF module does not import this optional ecosystem adapter. Registry
proof is retained, never elevated to authorization by the conversion signature.
"""

from __future__ import annotations

from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import Field

from .models import Digest, Manifest, PreservationError, Record, TrustProfile
from .operations import OperationControl
from .pmf import check_pmf_space, new_pmf_tree, sign_pmf_manifest, verify_pmf_contents
from .pmf_models import (
    PMF_VERSION,
    PMFDocument,
    PMFManifest,
    PMFMember,
    PMFOrigin,
    PMFProfile,
)
from .signing import (
    Envelope,
    canonical_bytes,
    decode_base64,
    digest,
    encode_record,
    sign_manifest,
    strict_json,
    verify_manifest,
)
from .storage import (
    atomic_bytes,
    copy_verified_object,
    directory,
    manifest_path,
    object_path,
    read_metadata,
    unique_objects,
)

PRESERVATION_PROFILE = "foxlight.preservation.v1"
SKULK_PROFILE = "skulk.retained-card.v1"
SOURCE_KEYS_PROFILE = "foxlight.preservation-source-keys.v1"
SOURCE_KEYS_DOCUMENT = "preservation-source-keys"
CONVERTER = "foxlight-model-preservation/pmf-preservation-v0.1"
PUBLIC_PROFILE = "foxlight.public-preservation.v1"
PUBLIC_DOCUMENT = "public-preservation-manifest"
PUBLIC_CONVERTER = "foxlight-model-preservation/public-pmf-v0.1"


def _adapter_revision() -> str:
    source = files("foxlight_model_preservation").joinpath("pmf_preservation.py")
    return digest(source.read_bytes())


class PMFConversion(Record):
    """Conversion fidelity for the declared source profile, not runtime approval."""

    converter: str = CONVERTER
    converter_version: str = Field(
        default_factory=lambda: version("foxlight-model-preservation")
    )
    adapter_source_sha256: Digest = Field(default_factory=_adapter_revision)
    source_schema: str
    source_identity: Digest
    output_schema: str
    output_identity: Digest
    source_envelope_sha256: Digest
    native: tuple[str, ...] = (
        "files/paths/sizes/sha256",
        "member dependency graph",
        "origins and artifact identities",
        "rights/notices and runtime requirements",
    )
    retained: tuple[str, ...] = (
        "original signed preservation envelope",
        "complete original cards, extensions and registry proof",
    )
    unrepresentable: tuple[str, ...] = Field(default=(), max_length=100)
    authority: str = (
        "owner capture only; original registry evidence needs independent verification"
    )


def _project(
    original: Manifest, envelope: bytes
) -> tuple[PMFManifest, dict[str, bytes]]:
    return _projection(
        original,
        PMFDocument(
            document_id="preservation-envelope",
            role="original-signed-archive",
            media_type="application/vnd.dsse.envelope.v1+json",
            schema_version="foxlight.model-preservation.v1",
            sha256=digest(envelope),
            size=len(envelope),
        ),
        envelope,
        PRESERVATION_PROFILE,
        CONVERTER,
    )


def _project_public(
    original: Manifest, payload: bytes
) -> tuple[PMFManifest, dict[str, bytes]]:
    return _projection(
        original,
        PMFDocument(
            document_id=PUBLIC_DOCUMENT,
            role="public-preservation-manifest",
            media_type="application/json",
            schema_version="foxlight.model-preservation.v1",
            sha256=digest(payload),
            size=len(payload),
        ),
        payload,
        PUBLIC_PROFILE,
        PUBLIC_CONVERTER,
    )


def _projection(
    original: Manifest,
    base: PMFDocument,
    data: bytes,
    profile_id: str,
    converter: str,
) -> tuple[PMFManifest, dict[str, bytes]]:
    records = {base.document_id: data}
    documents = [base]
    profiles = [PMFProfile(profile_id=profile_id, documents=(base.document_id,))]
    for index, member in enumerate(original.members):
        if member.retained_card is None:
            continue
        name = f"retained-{index:03d}"
        data = canonical_bytes(member.retained_card)
        records[name] = data
        schema = member.retained_card.get("schema_version")
        documents.append(
            PMFDocument(
                document_id=name,
                member_id=member.member_id,
                role="original-retained-card",
                media_type="application/json",
                schema_version=schema if isinstance(schema, str) else None,
                sha256=digest(data),
                size=len(data),
            )
        )
        if schema == "foxlight.retained-card.v1":
            profiles.append(
                PMFProfile(
                    profile_id=SKULK_PROFILE,
                    member_id=member.member_id,
                    documents=(name,),
                )
            )
    return PMFManifest(
        schema_version=PMF_VERSION,
        name=original.name,
        root_member=original.root_member,
        members=tuple(
            PMFMember(
                member_id=member.member_id,
                role=member.role,
                format=member.format,
                origin=PMFOrigin(
                    repository=member.origin_repository,
                    revision=member.origin_revision,
                    revision_scheme="git-sha1",
                ),
                artifact_identity=member.artifact_identity,
                loader_root=member.loader_root,
                dependencies=member.dependencies,
                files=member.files,
                intake=member.intake,
            )
            for member in original.members
        ),
        documents=tuple(documents),
        profiles=tuple(profiles),
        workloads=original.workloads,
        runtime_requirements=original.runtime_requirements,
        annotations={"pmf.converter": converter},
    ), records


def import_preservation(
    store: Path,
    manifest_id: str,
    trust: TrustProfile,
    destination: Path,
    keys: tuple[Ed25519PrivateKey, ...],
    *,
    source_key_candidates: TrustProfile | None = None,
    control: OperationControl | None = None,
) -> PMFConversion:
    """Convert an exact v1 capture (including Skulk captures) into signed PMF.

    Every original member and signed record is retained. No card deserialization
    is needed, so future card properties/profiles survive without a new runtime.
    The new signature describes the conversion; it is not a registry signature.
    Optional public source keys are retained as an opaque, optional profile.
    They remain candidates, never a replacement for the receiver's trust choice.
    Omitting them preserves the original projection and all existing identities.
    """
    with (control or OperationControl()).operation("pack") as active:
        with directory(store) as source:
            envelope = read_metadata(source, manifest_path(manifest_id))
            original = verify_manifest(envelope, trust, manifest_id)
            manifest, records = _project(original, envelope)
            if source_key_candidates is not None:
                # Check the proposed public candidates without adopting them as
                # the independent authority used above to authenticate the input.
                verify_manifest(envelope, source_key_candidates, manifest_id)
                data = encode_record(source_key_candidates)
                manifest = manifest.model_copy(
                    update={
                        "documents": (
                            *manifest.documents,
                            PMFDocument(
                                document_id=SOURCE_KEYS_DOCUMENT,
                                role="verification-key-candidates",
                                media_type="application/json",
                                schema_version=source_key_candidates.schema_version,
                                sha256=digest(data),
                                size=len(data),
                            ),
                        ),
                        "profiles": (
                            *manifest.profiles,
                            PMFProfile(
                                profile_id=SOURCE_KEYS_PROFILE,
                                documents=(SOURCE_KEYS_DOCUMENT,),
                            ),
                        ),
                    }
                )
                # Revalidate the complete descriptor, including the metadata cap.
                manifest = PMFManifest.model_validate_json(encode_record(manifest))
                records[SOURCE_KEYS_DOCUMENT] = data
            identity, signed = sign_pmf_manifest(manifest, keys)
            with new_pmf_tree(destination, active) as target:
                check_pmf_space(target, manifest)
                for file in unique_objects(original):
                    copy_verified_object(
                        source, object_path(file.sha256), target, file, active
                    )
                for data in records.values():
                    active.checkpoint()
                    atomic_bytes(target, object_path(digest(data)), data)
                atomic_bytes(target, manifest_path(identity), signed)
    return PMFConversion(
        source_schema=original.schema_version,
        source_identity=manifest_id,
        output_schema=PMF_VERSION,
        output_identity=identity,
        source_envelope_sha256=digest(envelope),
    )


def _without_source_keys(manifest: PMFManifest) -> PMFManifest:
    profile = next(
        (item for item in manifest.profiles if item.profile_id == SOURCE_KEYS_PROFILE),
        None,
    )
    if profile is None:
        return manifest
    document = next(
        (
            item
            for item in manifest.documents
            if item.document_id == SOURCE_KEYS_DOCUMENT
        ),
        None,
    )
    if (
        profile.member_id is not None
        or profile.required
        or profile.documents != (SOURCE_KEYS_DOCUMENT,)
        or document is None
        or document.member_id is not None
        or document.role != "verification-key-candidates"
        or document.media_type != "application/json"
        or document.schema_version != "foxlight.model-preservation.trust.v1"
    ):
        raise PreservationError("unsupported source key candidate profile")
    return manifest.model_copy(
        update={
            "documents": tuple(
                item
                for item in manifest.documents
                if item.document_id != SOURCE_KEYS_DOCUMENT
            ),
            "profiles": tuple(item for item in manifest.profiles if item != profile),
        }
    )


def export_preservation(
    store: Path,
    manifest_id: str,
    trust: TrustProfile,
    destination: Path,
    *,
    source_trust: TrustProfile,
    expected_source_manifest_id: str,
    control: OperationControl | None = None,
    link_objects: bool = False,
) -> PMFConversion:
    """Recover the unchanged original archive for Skulk or standalone v1 tools.

    Authenticate PMF and the original capture independently. Refuse altered or
    extended projections that cannot be exported losslessly to that original.
    A PMF owner key cannot substitute for original publisher/registry authority.
    ``link_objects`` hardlinks immutable store objects on the same filesystem.
    """
    with (control or OperationControl()).operation("restore") as active:
        with directory(store) as source:
            manifest, _signed = verify_pmf_contents(source, manifest_id, trust, active)
            profile = next(
                (
                    item
                    for item in manifest.profiles
                    if item.profile_id == PRESERVATION_PROFILE
                    and item.member_id is None
                ),
                None,
            )
            if profile is None or profile.documents != ("preservation-envelope",):
                raise PreservationError(
                    "PMF has no supported original preservation profile"
                )
            document = next(
                item
                for item in manifest.documents
                if item.document_id == "preservation-envelope"
            )
            envelope = read_metadata(source, object_path(document.sha256))
            original = verify_manifest(
                envelope, source_trust, expected_source_manifest_id
            )
            expected, records = _project(original, envelope)
            if _without_source_keys(manifest) != expected:
                raise PreservationError(
                    "PMF projection cannot be exported losslessly to its original"
                )
            for name, data in records.items():
                document = next(
                    item for item in manifest.documents if item.document_id == name
                )
                if read_metadata(source, object_path(document.sha256)) != data:
                    raise PreservationError(
                        "PMF source document differs from its original"
                    )
            candidate_bytes: bytes | None = None
            candidate_document = next(
                (
                    item
                    for item in manifest.documents
                    if item.document_id == SOURCE_KEYS_DOCUMENT
                ),
                None,
            )
            if candidate_document is not None:
                candidate_bytes = read_metadata(
                    source, object_path(candidate_document.sha256)
                )
                candidates = TrustProfile.model_validate_json(
                    canonical_bytes(strict_json(candidate_bytes))
                )
                verify_manifest(envelope, candidates, expected_source_manifest_id)
            with new_pmf_tree(destination, active) as target:
                check_pmf_space(
                    target, manifest, linked_from=source if link_objects else None
                )
                for file in unique_objects(original):
                    copy_verified_object(
                        source,
                        object_path(file.sha256),
                        target,
                        file,
                        active,
                        link=link_objects,
                    )
                atomic_bytes(
                    target, manifest_path(expected_source_manifest_id), envelope
                )
                if candidate_bytes is not None:
                    # A sidecar preserves the optional transport metadata without
                    # modifying the original signed format or granting authority.
                    atomic_bytes(target, "source-key-candidates.json", candidate_bytes)
    return PMFConversion(
        source_schema=PMF_VERSION,
        source_identity=manifest_id,
        output_schema=original.schema_version,
        output_identity=expected_source_manifest_id,
        source_envelope_sha256=digest(envelope),
    )


def build_preservation_pmf(
    original: Manifest, keys: tuple[Ed25519PrivateKey, ...]
) -> tuple[PMFManifest, dict[str, bytes]]:
    """Build a lossless owner-signed capture's PMF descriptor and exact records.

    No bytes are stored and no model/card interpretation occurs. The caller
    must verify all member bytes before publishing the signed PMF generation.
    """
    envelope = encode_record(sign_manifest(original, keys))
    if len(envelope) > 8 * 1024 * 1024:
        raise PreservationError("original signed metadata exceeds size limit")
    return _project(original, envelope)


def inspect_preservation_pmf(
    manifest: PMFManifest, envelope: bytes, *, source_trust: TrustProfile
) -> tuple[str, Manifest]:
    """Authenticate retained source metadata and require lossless projection.

    The caller first authenticates the externally selected PMF. Its signed
    document digest binds the exact original envelope, whose separate source
    signature must satisfy independently selected source trust. This checks no
    weights and grants no registry authority, code permission or runtime support.
    """
    signed = Envelope.model_validate_json(canonical_bytes(strict_json(envelope)))
    source_identity = digest(decode_base64(signed.payload))
    original = verify_manifest(envelope, source_trust, source_identity)
    expected, _records = _project(original, envelope)
    if _without_source_keys(manifest) != expected:
        raise PreservationError(
            "PMF projection cannot be exported losslessly to its original"
        )
    # Equality with the recomputed descriptor binds every retained record's
    # digest and size. Full archive verification checks their actual bytes.
    return source_identity, original


def public_network_pmf(
    original: Manifest, payload: bytes | None = None
) -> tuple[str, PMFManifest]:
    """Return the network PMF identity and descriptor for a public v1 manifest.

    ``payload`` is the exact signed payload when the manifest was read from an
    existing envelope (its digest is the original v1 identity); a newly built
    manifest uses its canonical encoding. The descriptor retains that unsigned
    payload, so the identity
    depends only on the model, its files, cards and reviewed terms, never on who
    signs it. Independent publishers of the same selection therefore share one
    identity, and mirrors holding it can serve each other's recipients.
    """
    if any(member.intake.audience != "public" for member in original.members):
        raise PreservationError("network PMF requires public intake for every member")
    manifest, _records = _project_public(
        original, encode_record(original) if payload is None else payload
    )
    return digest(encode_record(manifest)), manifest


def publish_network_pmf(
    store: Path,
    manifest_id: str,
    trust: TrustProfile,
    destination: Path,
    keys: tuple[Ed25519PrivateKey, ...],
    *,
    control: OperationControl | None = None,
    link_objects: bool = False,
) -> str:
    """Write a signed public network PMF of a complete public v1 archive.

    Every object is verified while copying. The v1 envelope's own signatures are
    not embedded: recipients authenticate the PMF signer they choose to trust.
    Nothing is served, listed or enrolled by this operation.
    ``link_objects`` hardlinks immutable store objects on the same filesystem.
    """
    with (control or OperationControl()).operation("pack") as active:
        with directory(store) as source:
            envelope = read_metadata(source, manifest_path(manifest_id))
            original = verify_manifest(envelope, trust, manifest_id)
            payload = decode_base64(
                Envelope.model_validate_json(
                    canonical_bytes(strict_json(envelope))
                ).payload
            )
            identity, manifest = public_network_pmf(original, payload)
            records = _project_public(original, payload)[1]
            signed_identity, signed = sign_pmf_manifest(manifest, keys)
            if signed_identity != identity:
                raise PreservationError("network PMF identity changed during signing")
            with new_pmf_tree(destination, active) as target:
                check_pmf_space(
                    target, manifest, linked_from=source if link_objects else None
                )
                for file in unique_objects(original):
                    copy_verified_object(
                        source,
                        object_path(file.sha256),
                        target,
                        file,
                        active,
                        link=link_objects,
                    )
                for data in records.values():
                    active.checkpoint()
                    atomic_bytes(target, object_path(digest(data)), data)
                atomic_bytes(target, manifest_path(identity), signed)
    return identity


def inspect_network_pmf(manifest: PMFManifest, payload: bytes) -> tuple[str, Manifest]:
    """Recover the exact public v1 manifest a network PMF was projected from.

    The caller first authenticates the PMF and reads its public document bytes.
    Equality with the recomputed projection binds every member, card and term.
    """
    original = Manifest.model_validate_json(canonical_bytes(strict_json(payload)))
    if manifest != public_network_pmf(original, payload)[1]:
        raise PreservationError("network PMF differs from its public projection")
    return digest(payload), original


def is_network_pmf(manifest: PMFManifest) -> bool:
    """Report whether a descriptor uses the public network projection profile."""
    return any(
        item.profile_id == PUBLIC_PROFILE and item.member_id is None
        for item in manifest.profiles
    )
