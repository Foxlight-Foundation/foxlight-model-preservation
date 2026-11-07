"""The one canonical, versioned licence-family definition for public sharing.

Sharing follows a model's licence, never whether its original download was
gated. Licences are grouped into families the owner turns on or off; each
family lists exact identifiers, spelled as Hugging Face model cards and
Sovereign manifests carry them. Matching is exact and case-insensitive on a
member's licence identifier (spaces count as dashes, and a few spelled-out
names map to their identifiers). An identifier that names no licence, such as
``unknown``, ``other`` or blank, never matches, whatever the owner selects.

The definition lives in ``resources/licence_families_v1.json``. The Library
capability carries a byte-identical copy (it does not install this package),
and a test keeps the two equal.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from functools import cache
from importlib.resources import files
from typing import Literal

from pydantic import Field, JsonValue

from .models import PreservationError, Record

DEFINITION_RESOURCE = "licence_families_v1.json"


class KnownLicence(Record):
    """One exact licence identifier and the words the app shows for it."""

    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=300)


class LicenceFamily(Record):
    """A group of licences with the same kind of sharing obligations."""

    id: str = Field(pattern=r"^[a-z][a-z-]{0,39}$")
    name: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=300)
    default: bool
    licences: tuple[KnownLicence, ...] = Field(min_length=1, max_length=64)


class LicenceDefinition(Record):
    """The versioned family definition; a new version never widens old choices."""

    schema_version: Literal["foxlight.licence-families.v1"]
    version: int = Field(ge=1)
    families: tuple[LicenceFamily, ...] = Field(min_length=1, max_length=16)
    unidentified: tuple[str, ...] = Field(min_length=1, max_length=64)
    aliases: dict[str, str] = Field(max_length=64)


def definition_bytes() -> bytes:
    """The exact definition document, as shipped."""
    return (
        files("foxlight_model_preservation")
        .joinpath("resources", DEFINITION_RESOURCE)
        .read_bytes()
    )


@cache
def definition() -> LicenceDefinition:
    """Parse and check the shipped definition once."""
    value = LicenceDefinition.model_validate_json(definition_bytes())
    seen: set[str] = set()
    for family in value.families:
        for licence in family.licences:
            if licence.id != licence.id.casefold() or licence.id in seen:
                raise PreservationError("licence definition is not canonical")
            if licence.id in value.unidentified:
                raise PreservationError("licence definition lists no licence")
            seen.add(licence.id)
    return value


def definition_json() -> dict[str, JsonValue]:
    """The definition as the sharing screens receive it."""
    loaded: JsonValue = json.loads(definition_bytes())
    if not isinstance(loaded, dict):
        raise PreservationError("licence definition is not an object")
    return loaded


LICENCE_DEFINITION_VERSION = definition().version
UNIDENTIFIED = frozenset(definition().unidentified)
_ALIASES = dict(definition().aliases)
# Families on for a new setup: every family except the makers' custom terms.
DEFAULT_LICENSES = tuple(
    licence.id
    for family in definition().families
    if family.default
    for licence in family.licences
)
KNOWN_LICENSES = frozenset(
    licence.id for family in definition().families for licence in family.licences
)


def normalize_license(value: str) -> str:
    """Compare licence identifiers case-insensitively, with spaces as dashes.

    A few common spelled-out names map to their identifiers.
    """
    folded = "-".join(value.strip().casefold().split())
    return _ALIASES.get(folded, folded)


def identified_licences(values: Iterable[str]) -> tuple[str, ...]:
    """Normalize a selection once each; refuse any entry that names no licence."""
    result = tuple(dict.fromkeys(normalize_license(value) for value in values))
    if any(value in UNIDENTIFIED or len(value) > 200 for value in result):
        raise PreservationError("community_license_unidentified")
    return result


def licence_status(
    licences: Iterable[str], allowed: Iterable[str]
) -> Literal["eligible", "license_not_selected", "no_license"]:
    """Every member needs an identified licence that the owner selected."""
    normalized = [normalize_license(item) for item in licences]
    selected = {normalize_license(item) for item in allowed} - UNIDENTIFIED
    if not normalized or any(item in UNIDENTIFIED for item in normalized):
        return "no_license"
    if any(item not in selected for item in normalized):
        return "license_not_selected"
    return "eligible"
