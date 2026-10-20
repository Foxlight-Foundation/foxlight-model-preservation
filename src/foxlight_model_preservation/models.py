"""Strict version-one manifest, intake, and independently supplied trust models."""

from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath
from typing import Annotated, Literal, final

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
KeyId = Annotated[str, Field(pattern=r"^ed25519:[0-9a-f]{64}$")]
MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_FILES = 100_000
MAX_TOTAL_BYTES = 2**53 - 1


class PreservationError(ValueError):
    """Reject invalid identity, unsafe storage, or an incomplete transfer."""


def canonical_path(value: str) -> str:
    """Accept only portable relative file names with no traversal or aliases."""
    path = PurePosixPath(value)
    if (
        not value
        or not path.parts
        or len(value) > 2048
        or len(path.parts) > 64
        or path.is_absolute()
        or path.as_posix() != value
        or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
        or unicodedata.normalize("NFC", value) != value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise PreservationError("file path is not canonical and relative")
    for part in path.parts:
        if (
            any(char in part for char in ':*?"<>|')
            or part.endswith((".", " "))
            or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?", part)
        ):
            raise PreservationError("file path is not portable")
    return value


class Record(BaseModel):
    """Reject unknown fields and mutable, coercive in-memory records."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


@final
class Intake(Record):
    """Operator declaration retained per member; it is not a grant of rights."""

    audience: Literal["private", "public", "restricted"]
    license_id: str = Field(min_length=1, max_length=200)
    terms_reference: str = Field(min_length=1, max_length=2048)
    decision: str = Field(min_length=1, max_length=4096)
    notice_paths: tuple[str, ...] = Field(min_length=1, max_length=100)

    @field_validator("notice_paths")
    @classmethod
    def validate_notices(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Bind required notices to actual canonical member files."""
        for value in values:
            canonical_path(value)
        if len(set(values)) != len(values):
            raise PreservationError("duplicate notice paths")
        return values


@final
class SourceFile(Record):
    """An explicitly selected local file and optional previously trusted hash."""

    path: str
    expected_sha256: Digest | None = None

    _canonical_path = field_validator("path")(canonical_path)


@final
class FileIdentity(Record):
    """Exact file bytes inside a member namespace."""

    path: str
    size: int = Field(ge=0, le=MAX_TOTAL_BYTES)
    sha256: Digest

    _canonical_path = field_validator("path")(canonical_path)


class MemberBase(Record):
    """Declared origin and required dependency closure for one artifact member."""

    member_id: Identifier
    role: str = Field(min_length=1, max_length=100)
    format: str = Field(min_length=1, max_length=100)
    origin_repository: str = Field(min_length=1, max_length=512)
    origin_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    artifact_identity: str = Field(min_length=1, max_length=200)
    loader_root: str | None = None
    dependencies: tuple[Identifier, ...] = ()
    retained_card: dict[str, JsonValue] | None = None
    intake: Intake

    @field_validator("loader_root")
    @classmethod
    def validate_root(cls, value: str | None) -> str | None:
        """Constrain the loader root within its member directory."""
        return canonical_path(value) if value is not None else None


@final
class RecipeMember(MemberBase):
    """Selected inputs; source directories are supplied separately by the owner."""

    files: tuple[SourceFile, ...] = Field(min_length=1, max_length=MAX_FILES)


@final
class ManifestMember(MemberBase):
    """Signed exact files, with retained origin statements kept as statements."""

    files: tuple[FileIdentity, ...] = Field(min_length=1, max_length=MAX_FILES)


def check_members(
    members: tuple[RecipeMember, ...] | tuple[ManifestMember, ...], root: str
) -> None:
    """Require a complete acyclic reachable member graph and unambiguous layouts."""
    by_id = {member.member_id: member for member in members}
    if len(by_id) != len(members) or root not in by_id:
        raise PreservationError("duplicate member identity or missing root member")
    if sum(len(member.files) for member in members) > MAX_FILES:
        raise PreservationError("too many files in preservation set")
    for member in members:
        paths = [file.path for file in member.files]
        folded = {path.casefold() for path in paths}
        if len(set(folded)) != len(paths):
            raise PreservationError("duplicate or case-colliding file paths")
        directories: dict[str, str] = {}
        for path in paths:
            for parent in PurePosixPath(path).parents:
                value = parent.as_posix()
                previous = directories.setdefault(value.casefold(), value)
                if previous != value:
                    raise PreservationError("case-colliding directory paths")
            if any(
                parent.as_posix().casefold() in folded
                for parent in PurePosixPath(path).parents
                if parent.as_posix() != "."
            ):
                raise PreservationError("file/directory path collision")
        if any(path not in paths for path in member.intake.notice_paths):
            raise PreservationError("required notice missing from declared files")
        if member.loader_root is not None and not any(
            path.startswith(member.loader_root + "/") for path in paths
        ):
            raise PreservationError("loader root contains no declared files")
        if len(set(member.dependencies)) != len(member.dependencies):
            raise PreservationError("duplicate dependency")
        if any(dependency not in by_id for dependency in member.dependencies):
            raise PreservationError("required companion is missing")
    active: set[str] = set()
    visited: set[str] = set()

    def visit(member_id: str) -> None:
        if member_id in active:
            raise PreservationError("cyclic member dependencies")
        if member_id in visited:
            return
        active.add(member_id)
        for dependency in by_id[member_id].dependencies:
            visit(dependency)
        active.remove(member_id)
        visited.add(member_id)

    visit(root)
    if visited != set(by_id):
        raise PreservationError("unreachable members are outside the selected closure")


class BundleBase(Record):
    """Identity-bearing fields shared by recipes and signed manifests."""

    schema_version: Literal["foxlight.model-preservation.v1"]
    name: str = Field(min_length=1, max_length=200)
    root_member: Identifier
    workloads: tuple[str, ...] = Field(min_length=1, max_length=100)
    runtime_requirements: tuple[str, ...] = Field(max_length=100)
    attestation: Literal["owner-capture"] = "owner-capture"


@final
class Recipe(BundleBase):
    """Explicit complete selection used to create a preservation generation."""

    members: tuple[RecipeMember, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_graph(self) -> Recipe:
        """Reject missing companions and ambiguous layouts before reading bytes."""
        check_members(self.members, self.root_member)
        return self


@final
class Manifest(BundleBase):
    """Signed complete owner capture; original card trust is never upgraded."""

    members: tuple[ManifestMember, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_graph(self) -> Manifest:
        """Enforce dependency, file-layout and bounded-total invariants."""
        check_members(self.members, self.root_member)
        if self.total_bytes > MAX_TOTAL_BYTES:
            raise PreservationError("preservation set exceeds supported total size")
        return self

    @property
    def total_bytes(self) -> int:
        """Return total logical member size, before optional deduplication."""
        return sum(file.size for member in self.members for file in member.files)


@final
class TrustedKey(Record):
    """One independently enrolled Ed25519 public key."""

    key_id: KeyId
    public_key: str = Field(min_length=40, max_length=100)


@final
class TrustProfile(Record):
    """Explicit historical trust snapshot, never automatically imported from a peer."""

    schema_version: Literal["foxlight.model-preservation.trust.v1"]
    keys: tuple[TrustedKey, ...] = Field(min_length=1, max_length=32)
    threshold: int = Field(ge=1, le=32)
    revoked_keys: tuple[KeyId, ...] = ()
    as_of: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_threshold(self) -> TrustProfile:
        """Require a satisfiable unique-key threshold after revocation."""
        ids = [key.key_id for key in self.keys]
        if len(set(ids)) != len(ids) or len(set(self.revoked_keys)) != len(
            self.revoked_keys
        ):
            raise PreservationError("duplicate trust key")
        if self.threshold > len(set(ids) - set(self.revoked_keys)):
            raise PreservationError("trust threshold cannot be satisfied")
        return self


@final
class CatalogEntry(Record):
    """An exact preservation identity and replaceable transport hints."""

    entry_id: Identifier
    manifest: Digest
    mirrors: tuple[str, ...] = Field(min_length=1, max_length=16)


@final
class Catalog(Record):
    """Pinned portable discovery; no freshness or model-authorship claim."""

    schema_version: Literal["foxlight.model-preservation.catalog.v1"]
    description: str = Field(min_length=1, max_length=2000)
    entries: tuple[CatalogEntry, ...] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_entries(self) -> Catalog:
        """Keep stable entry selections unique and transport hints bounded."""
        if len({entry.entry_id for entry in self.entries}) != len(self.entries):
            raise PreservationError("duplicate catalog entry")
        if any(
            not mirror or len(mirror) > 2048
            for entry in self.entries
            for mirror in entry.mirrors
        ):
            raise PreservationError("invalid catalog mirror hint")
        return self
