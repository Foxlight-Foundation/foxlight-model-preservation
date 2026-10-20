"""Public model-card envelopes; content identity never grants registry authority."""

from __future__ import annotations

import base64
import hashlib
import json
from functools import lru_cache
from importlib.resources import files
from typing import Annotated, Literal, Self, cast, final

from jsonschema import Draft202012Validator, ValidationError
from jsonschema.protocols import Validator
from pydantic import Field, JsonValue, field_validator, model_validator

from .models import (
    MAX_FILES,
    MAX_TOTAL_BYTES,
    PreservationError,
    Record,
    canonical_path,
)
from .signing import strict_json

CardId = Annotated[str, Field(pattern=r"^card_[a-z2-7]{52}$")]
BundleId = Annotated[str, Field(pattern=r"^bundle_[a-z2-7]{52}$")]
Repository = Annotated[
    str,
    Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._+@-]*$",
        max_length=512,
    ),
]
Revision = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
ObjectId = Annotated[
    str, Field(pattern=r"^(sha256:[0-9a-f]{64}|git-sha1:[0-9a-f]{40})$")
]


def _optional_path(value: str | None) -> str | None:
    return canonical_path(value) if value is not None else None


def identity_json(value: JsonValue) -> bytes:
    """Encode the existing registry identity format, distinct from bundle RFC 8785.

    Preserve JSON integer/float representation. Registry card/bundle identities
    use sorted UTF-8 JSON with ensure_ascii=False and compact separators.
    """
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _identity(prefix: str, value: JsonValue) -> str:
    suffix = base64.b32encode(hashlib.sha256(identity_json(value)).digest())
    return prefix + suffix.decode("ascii").lower().rstrip("=")


@final
class ArtifactFile(Record):
    """One declared repository file; a missing hash is an explicit evidence gap."""

    path: str
    size_bytes: int = Field(ge=0, le=MAX_TOTAL_BYTES)
    object_id: ObjectId | None = None
    _path = field_validator("path")(canonical_path)


@final
class AlternateLocation(Record):
    """A complete alternate path mapping, not a new artifact authority."""

    root: str | None = None
    paths: tuple[str, ...] = Field(min_length=1, max_length=MAX_FILES)

    _root = field_validator("root")(_optional_path)

    @field_validator("paths")
    @classmethod
    def safe_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject nonportable, duplicate and case-aliased alternate locations."""
        for path in value:
            canonical_path(path)
        if len({path.casefold() for path in value}) != len(value):
            raise PreservationError("duplicate alternate paths")
        return value


@final
class ArtifactBundle(Record):
    """Existing content-derived selected-file identity with optional hash evidence."""

    bundle_id: BundleId
    root: str | None = None
    files: tuple[ArtifactFile, ...] = Field(min_length=1, max_length=MAX_FILES)
    download_size: int = Field(gt=0, le=MAX_TOTAL_BYTES)
    alternate_locations: tuple[AlternateLocation, ...] = Field(
        default=(), max_length=64
    )
    _root = field_validator("root")(_optional_path)

    @model_validator(mode="after")
    def consistent_identity(self) -> Self:
        """Bind exact totals, roots and the existing normalized bundle identity."""
        paths = [file.path for file in self.files]
        if len(set(paths)) != len(paths):
            raise PreservationError("duplicate artifact paths")
        if sum(file.size_bytes for file in self.files) != self.download_size:
            raise PreservationError("artifact sizes differ from declared total")
        if self.root is not None and any(
            not path.startswith(self.root + "/") for path in paths
        ):
            raise PreservationError("artifact files must lie beneath the loader root")
        for alternate in self.alternate_locations:
            if len(alternate.paths) != len(paths):
                raise PreservationError("alternate location must map every file")
            if alternate.root is not None and any(
                not path.startswith(alternate.root + "/") for path in alternate.paths
            ):
                raise PreservationError("alternate files must lie beneath their root")
        if self.bundle_id != bundle_identity(self.files, self.root):
            raise PreservationError("artifact bundle identity does not match its files")
        return self


def bundle_identity(files: tuple[ArtifactFile, ...], root: str | None = None) -> str:
    """Derive the existing bundle ID without treating it as publisher evidence."""
    prefix = root + "/" if root is not None else ""
    rows: list[JsonValue] = [
        {
            "relative_path": file.path.removeprefix(prefix),
            "size_bytes": file.size_bytes,
            "object_id": file.object_id,
        }
        for file in sorted(files, key=lambda item: item.path)
    ]
    return _identity("bundle_", rows)


@final
class Artifact(Record):
    """Pinned origin and selected artifact; locations are not provenance grants."""

    repository: Repository
    revision: Revision
    selected_file: str | None = None
    format: str = Field(min_length=1, max_length=100)
    quantization: str = Field(default="", max_length=200)
    bundle: ArtifactBundle | None = None
    _selected = field_validator("selected_file")(_optional_path)


@lru_cache(maxsize=1)
def _body_validator() -> Validator:
    source = files("foxlight_model_preservation").joinpath(
        "resources", "skulk_model_card_v1.json"
    )
    # The trusted, packaged schema contains int64 bounds beyond RFC 8785's
    # number domain. Incoming card/bundle JSON still uses the bounded reader.
    schema = cast(JsonValue, json.loads(source.read_bytes()))
    if not isinstance(schema, dict):
        raise PreservationError("packaged card schema is invalid")
    return Draft202012Validator(schema)


def validate_card_body(body: dict[str, JsonValue]) -> None:
    """Validate the published structural body schema without a Skulk import.

    This does not qualify an engine, resolve runtime semantic dependencies or
    accept registry/operator projections as authority.
    """
    try:
        _body_validator().validate(body)
    except ValidationError as error:
        raise PreservationError(
            "card body does not match the public structural schema"
        ) from error


@final
class ModelCardEnvelope(Record):
    """Existing v1/v2 envelope, usable without enrolling a registry publisher."""

    schema_version: Literal[1, 2]
    card_id: CardId
    alias: Repository
    model_ref: Repository
    artifact: Artifact
    card: dict[str, JsonValue]

    @model_validator(mode="after")
    def bind_identity(self) -> Self:
        """Require card/body/artifact agreement and a genuine content-derived ID."""
        validate_card_body(self.card)
        if self.model_ref != self.alias:
            raise PreservationError("model reference must match the selected alias")
        if self.schema_version != (2 if self.artifact.bundle is not None else 1):
            raise PreservationError("card version disagrees with artifact selection")
        repository = self.card.get("source_repository") or self.card.get("model_id")
        if repository != self.artifact.repository:
            raise PreservationError("card origin differs from its artifact")
        if self.card.get("source_revision") != self.artifact.revision:
            raise PreservationError("card revision differs from its artifact")
        if self.card.get("gguf_file") != self.artifact.selected_file:
            raise PreservationError("card selected file differs from its artifact")
        if self.card.get("quantization", "") != self.artifact.quantization:
            raise PreservationError("card quantization differs from its artifact")
        bundle = self.artifact.bundle
        raw = self.card.get("artifact_bundle")
        if bundle is None and raw is not None:
            raise PreservationError("card bundle is absent from its artifact")
        if bundle is not None:
            if not isinstance(raw, dict):
                raise PreservationError("card bundle differs from its artifact")
            body_bundle = ArtifactBundle.model_validate_json(identity_json(raw))
            if any(
                getattr(body_bundle, key) != getattr(bundle, key)
                for key in ("bundle_id", "root", "files", "download_size")
            ):
                raise PreservationError("card bundle differs from its artifact")
        unsigned = self.model_dump(mode="json", exclude={"card_id"})
        identities = {card_identity(unsigned)}
        if self.schema_version == 1:
            # Before v2 introduced bundle selection, v1 identity seeds did not
            # contain the subsequently defaulted artifact.bundle=null field.
            legacy_artifact = self.artifact.model_dump(mode="json", exclude={"bundle"})
            identities.add(card_identity({**unsigned, "artifact": legacy_artifact}))
        if self.card_id not in identities:
            raise PreservationError("card identity does not match its content")
        return self


def card_identity(unsigned: dict[str, JsonValue]) -> str:
    """Derive a registry-compatible card ID; its prefix confers no trust."""
    return _identity("card_", unsigned)


def create_card(
    body: dict[str, JsonValue], *, alias: str | None = None
) -> ModelCardEnvelope:
    """Build a public-format card from pinned metadata without enrolling its key."""
    validate_card_body(body)
    repository = body.get("source_repository") or body.get("model_id")
    revision = body.get("source_revision")
    if not isinstance(repository, str) or not isinstance(revision, str):
        raise PreservationError("a portable card requires an exact origin and revision")
    raw_bundle = body.get("artifact_bundle")
    bundle = (
        ArtifactBundle.model_validate_json(identity_json(raw_bundle))
        if raw_bundle is not None
        else None
    )
    selected = body.get("gguf_file")
    if selected is not None and not isinstance(selected, str):
        raise PreservationError("selected artifact must be a file path")
    quantization = body.get("quantization", "")
    if not isinstance(quantization, str):
        raise PreservationError("quantization must be a string")
    placement = body.get("placement")
    backends = (
        placement.get("compatible_backends") if isinstance(placement, dict) else None
    )
    format_name = (
        "gguf"
        if selected
        else "mlx-audio"
        if isinstance(backends, list) and "mlx_audio" in backends
        else "mlx"
        if backends is None or isinstance(backends, list) and "mlx" in backends
        else "safetensors"
    )
    artifact = Artifact(
        repository=repository,
        revision=revision,
        selected_file=selected,
        format=format_name,
        quantization=quantization,
        bundle=bundle,
    )
    reference = alias or body.get("model_id")
    if not isinstance(reference, str):
        raise PreservationError("a model alias is required")
    unsigned: dict[str, JsonValue] = {
        "schema_version": 2 if bundle else 1,
        "alias": reference,
        "model_ref": reference,
        "artifact": artifact.model_dump(mode="json"),
        "card": body,
    }
    return ModelCardEnvelope.model_validate_json(
        identity_json(
            {
                **unsigned,
                "card_id": card_identity(unsigned),
            }
        )
    )


def load_card(data: bytes) -> ModelCardEnvelope:
    """Parse bounded, duplicate-key-free data and check identity and structure."""
    return ModelCardEnvelope.model_validate_json(identity_json(strict_json(data)))
