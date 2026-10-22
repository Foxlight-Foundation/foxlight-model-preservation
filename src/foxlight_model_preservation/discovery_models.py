"""Portable source bootstrap, authenticated catalog descriptions and local cache.

Source authority covers discovery metadata only. No record in this module enrolls
model signers, changes original cards, or authorizes runtime execution.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, JsonValue, model_validator

from .models import Digest, Identifier, PreservationError, Record, TrustProfile
from .signing import decode_base64, key_identity

SOURCE_VERSION = "foxlight.discovery-source.v1"
TARGET_VERSION = "foxlight.discovery-catalog.v1"
CACHE_VERSION = "foxlight.discovery-cache.v1"
MAX_SOURCES = 16
MAX_STATE_BYTES = 32 * 1024 * 1024
MAX_ROOT_BYTES = 64 * 1024
MAX_ROLE_BYTES = 1024 * 1024
MAX_CATALOG_BYTES = 2 * 1024 * 1024
MAX_ROOTS = 128


class SourceBootstrap(Record):
    """An independently authenticated file, not endpoint-provided trust discovery."""

    schema_version: Literal["foxlight.discovery-source.v1"] = SOURCE_VERSION
    name: str = Field(min_length=1, max_length=200)
    root: str = Field(min_length=1, max_length=MAX_ROOT_BYTES * 2)
    endpoints: tuple[str, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def bounded_endpoints(self) -> SourceBootstrap:
        """Bound replaceable hints; enrollment itself performs no network reads."""
        if len(set(self.endpoints)) != len(self.endpoints) or any(
            not value or len(value) > 2048 for value in self.endpoints
        ):
            raise PreservationError("source endpoints must be unique bounded hints")
        for value in self.endpoints:
            if (
                value != value.strip()
                or "\\" in value
                or any(
                    ord(character) < 32 or ord(character) == 127 for character in value
                )
            ):
                raise PreservationError("invalid discovery endpoint")
            if "://" not in value:
                if not Path(value).is_absolute():
                    raise PreservationError(
                        "local discovery endpoints must be absolute"
                    )
                continue
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or parsed.hostname is None
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                raise PreservationError(
                    "discovery endpoints must be credential-free HTTP(S)"
                )
            if parsed.port is not None and parsed.port == 0:
                raise PreservationError("discovery endpoint port must be positive")
        return self


class DiscoveryDescription(Record):
    """Publisher discovery statements, bound to an exact manifest and entry.

    Name/workloads follow PMF concepts. These optional display/search properties
    neither replace complete archived cards nor assert tested compatibility.
    """

    entry_id: Identifier
    manifest: Digest
    name: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=2000)
    workloads: tuple[str, ...] = Field(default=(), max_length=32)
    formats: tuple[str, ...] = Field(default=(), max_length=32)
    licenses: tuple[str, ...] = Field(default=(), max_length=32)
    candidate_bundle_trust: TrustProfile | None = None
    annotations: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounded_terms(self) -> DiscoveryDescription:
        """Keep unfamiliar vocabulary as text without inferring runtime support."""
        if any(
            not term or len(term) > 200
            for term in (*self.workloads, *self.formats, *self.licenses)
        ):
            raise PreservationError("discovery terms must be bounded nonempty text")
        if self.candidate_bundle_trust is not None:
            for key in self.candidate_bundle_trust.keys:
                raw = decode_base64(key.public_key)
                if len(raw) != 32 or key_identity(raw) != key.key_id:
                    raise PreservationError("candidate bundle key fingerprint mismatch")
        return self


class DiscoveryCatalogTarget(Record):
    """The TUF-signed custom profile for the unchanged signed catalog v1 target."""

    schema_version: Literal["foxlight.discovery-catalog.v1"] = TARGET_VERSION
    catalog_id: Digest
    catalog_trust: TrustProfile
    descriptions: tuple[DiscoveryDescription, ...] = Field(default=(), max_length=1000)


class SourceState(Record):
    """One atomic protected local record; failed updates retain accepted evidence."""

    schema_version: Literal["foxlight.discovery-state.v1"] = (
        "foxlight.discovery-state.v1"
    )
    bootstrap: SourceBootstrap
    enabled: bool = True
    allow_private: bool = False
    allow_http: bool = False
    allow_local: bool = False
    roots: tuple[str, ...] = Field(default=(), max_length=MAX_ROOTS)
    metadata: dict[str, str] = Field(default_factory=dict)
    accepted_metadata: dict[str, str] = Field(default_factory=dict)
    accepted_root_version: int | None = Field(default=None, ge=1)
    catalog: str | None = None
    accepted_at: str | None = None
    last_attempt_at: str | None = None
    last_error: str | None = Field(default=None, max_length=1000)
    imported: bool = False


class DiscoveryCacheBundle(Record):
    """Portable signed evidence without receiver policy or private error details."""

    schema_version: Literal["foxlight.discovery-cache.v1"] = CACHE_VERSION
    bootstrap: SourceBootstrap
    roots: tuple[str, ...] = Field(default=(), max_length=MAX_ROOTS)
    metadata: dict[str, str]
    accepted_root_version: int = Field(ge=1)
    catalog: str


class SourceStatus(Record):
    """Observable local age and update status without an online availability claim."""

    source_id: Digest
    name: str
    enabled: bool
    bootstrap_id: Digest | None
    root_version: int | None
    catalog_id: Digest | None
    accepted_at: str | None
    expires: str | None
    freshness: Literal[
        "empty", "fresh", "stale", "imported", "superseded-root", "invalid", "retained"
    ]
    last_attempt_at: str | None
    last_error: str | None


class SearchLocation(Record):
    """Attributed supplier hints without custody or reachability assertions."""

    source_id: Digest
    source_name: str
    entry_id: Identifier
    mirrors: tuple[str, ...]
    freshness: str
    accepted_at: str | None
    description: DiscoveryDescription | None


class SearchResult(Record):
    """An exact manifest, grouping aliases without merging conflicting statements."""

    manifest: Digest
    locations: tuple[SearchLocation, ...]
