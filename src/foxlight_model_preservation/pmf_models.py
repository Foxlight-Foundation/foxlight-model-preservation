"""Portable Model Format draft 0.1: common files and opaque source documents.

No Skulk model/card classes are needed to retain, authenticate or restore PMF.
An optional profile describes how an ecosystem interprets retained documents.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, JsonValue, field_validator, model_validator

from .models import (
    MAX_METADATA_BYTES,
    MAX_TOTAL_BYTES,
    Digest,
    FileIdentity,
    Identifier,
    Intake,
    PreservationError,
    Record,
    canonical_path,
    check_members,
)

PMF_VERSION = "pmf.manifest.v0.1"
PMF_TYPE = "application/vnd.pmf.manifest.v0.1+json"


class PMFOrigin(Record):
    """An optional source statement, never a requirement for a Git repository."""

    repository: str | None = Field(default=None, min_length=1, max_length=2048)
    revision: str | None = Field(default=None, min_length=1, max_length=512)
    revision_scheme: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def revision_has_scheme(self) -> Self:
        """Do not mistake an arbitrary version label for a Git/content digest."""
        if (self.revision is None) != (self.revision_scheme is None):
            raise PreservationError(
                "origin revision and scheme must be supplied together"
            )
        return self


class PMFMember(Record):
    """One artifact and its required companions, without runtime-specific data."""

    member_id: Identifier
    role: str = Field(min_length=1, max_length=100)
    format: str = Field(min_length=1, max_length=100)
    origin: PMFOrigin | None = None
    artifact_identity: str | None = Field(default=None, min_length=1, max_length=200)
    loader_root: str | None = None
    dependencies: tuple[Identifier, ...] = Field(default=(), max_length=256)
    files: tuple[FileIdentity, ...] = Field(min_length=1, max_length=100_000)
    intake: Intake

    @field_validator("loader_root")
    @classmethod
    def portable_root(cls, value: str | None) -> str | None:
        """Keep the loader directory within the member namespace."""
        return canonical_path(value) if value is not None else None


class PMFDocument(Record):
    """Exact source bytes stored as an object; metadata does not grant authority."""

    document_id: Identifier
    member_id: Identifier | None = None
    role: str = Field(min_length=1, max_length=100)
    media_type: str = Field(min_length=1, max_length=200)
    schema_version: str | None = Field(default=None, min_length=1, max_length=200)
    sha256: Digest
    size: int = Field(ge=0, le=MAX_METADATA_BYTES)


class PMFProfile(Record):
    """Versioned interpretation of source records; optional profiles stay opaque."""

    profile_id: str = Field(min_length=1, max_length=200)
    member_id: Identifier | None = None
    documents: tuple[Identifier, ...] = Field(min_length=1, max_length=1024)
    required: bool = False


class PMFManifest(Record):
    """Draft common archive contract, signed in its own explicit DSSE domain."""

    schema_version: Literal["pmf.manifest.v0.1"]
    name: str = Field(min_length=1, max_length=200)
    root_member: Identifier
    members: tuple[PMFMember, ...] = Field(min_length=1, max_length=256)
    documents: tuple[PMFDocument, ...] = Field(default=(), max_length=1024)
    profiles: tuple[PMFProfile, ...] = Field(default=(), max_length=1024)
    workloads: tuple[str, ...] = Field(default=(), max_length=100)
    runtime_requirements: tuple[str, ...] = Field(default=(), max_length=100)
    attestation: Literal["owner-capture"] = "owner-capture"
    required_features: tuple[str, ...] = Field(default=(), max_length=100)
    annotations: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def complete_graph(self) -> Self:
        """Bind all records/profiles to the complete, unambiguous member graph."""
        check_members(self.members, self.root_member)
        members = {member.member_id for member in self.members}
        documents = {item.document_id: item for item in self.documents}
        if len(documents) != len(self.documents):
            raise PreservationError("duplicate PMF document identity")
        if any(
            item.member_id is not None and item.member_id not in members
            for item in self.documents
        ):
            raise PreservationError("PMF document references a missing member")
        profiles = {(item.profile_id, item.member_id) for item in self.profiles}
        if len(profiles) != len(self.profiles):
            raise PreservationError("duplicate PMF profile")
        for profile in self.profiles:
            if profile.member_id is not None and profile.member_id not in members:
                raise PreservationError("PMF profile references a missing member")
            if len(set(profile.documents)) != len(profile.documents):
                raise PreservationError("duplicate PMF profile document")
            for name in profile.documents:
                if name not in documents:
                    raise PreservationError("PMF profile references a missing document")
                if documents[name].member_id != profile.member_id:
                    raise PreservationError(
                        "PMF profile/document member binding differs"
                    )
        if self.total_bytes > MAX_TOTAL_BYTES:
            raise PreservationError("PMF logical size exceeds supported total")
        sizes: dict[str, int] = {}
        for file in pmf_objects(self):
            previous = sizes.setdefault(file.sha256, file.size)
            if previous != file.size:
                raise PreservationError("conflicting PMF object sizes")
        return self

    @property
    def total_bytes(self) -> int:
        """Return logical file and document bytes, before deduplication."""
        return sum(file.size for member in self.members for file in member.files) + sum(
            item.size for item in self.documents
        )


def pmf_objects(manifest: PMFManifest) -> tuple[FileIdentity, ...]:
    """Enumerate common file/document objects without interpreting any profile."""
    return (
        *(file for member in manifest.members for file in member.files),
        *(
            FileIdentity(
                path="documents/" + document.document_id,
                size=document.size,
                sha256=document.sha256,
            )
            for document in manifest.documents
        ),
    )


def require_pmf_features(manifest: PMFManifest) -> None:
    """Refuse unsupported required semantics; optional profiles remain retainable."""
    if manifest.required_features or any(
        profile.required for profile in manifest.profiles
    ):
        raise PreservationError("unsupported required PMF feature or profile")
