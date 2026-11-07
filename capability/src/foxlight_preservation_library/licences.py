"""The core's canonical licence-family definition, as the Library reads it.

The managed Library does not install the core package, so it ships a
byte-identical copy of the core's ``resources/licence_families_v1.json`` next
to this module. The core's test suite checks the two files are equal, and the
Library's parity test compares the parsed values whenever the core is
installed. Nothing here decides anything the core would decide differently.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from functools import cache
from importlib.resources import files
from typing import Final, Literal, cast

from pydantic import JsonValue

DEFINITION_RESOURCE: Final = "licence_families_v1.json"


def definition_bytes() -> bytes:
    """The exact definition document, as shipped with this capability."""
    return (
        files("foxlight_preservation_library")
        .joinpath(DEFINITION_RESOURCE)
        .read_bytes()
    )


@cache
def definition_json() -> dict[str, JsonValue]:
    """The definition as the sharing sheet receives it."""
    loaded: JsonValue = json.loads(definition_bytes())
    if not isinstance(loaded, dict) or loaded.get("schema_version") != (
        "foxlight.licence-families.v1"
    ):
        raise ValueError("licence definition is not the expected version")
    return loaded


def _families() -> list[dict[str, JsonValue]]:
    families = definition_json()["families"]
    return [
        cast(dict[str, JsonValue], family)
        for family in cast(list[JsonValue], families)
        if isinstance(family, dict)
    ]


def _ids(family: dict[str, JsonValue]) -> tuple[str, ...]:
    return tuple(
        str(cast(dict[str, JsonValue], licence)["id"])
        for licence in cast(list[JsonValue], family["licences"])
    )


LICENCE_DEFINITION_VERSION: Final = cast(int, definition_json()["version"])
UNIDENTIFIED: Final = frozenset(
    str(item) for item in cast(list[JsonValue], definition_json()["unidentified"])
)
_ALIASES: Final = {
    str(key): str(value)
    for key, value in cast(dict[str, JsonValue], definition_json()["aliases"]).items()
}
DEFAULT_LICENSES: Final = tuple(
    licence
    for family in _families()
    if family.get("default") is True
    for licence in _ids(family)
)


def normalize_license(value: str) -> str:
    """Compare licence identifiers case-insensitively, with spaces as dashes."""
    folded = "-".join(value.strip().casefold().split())
    return _ALIASES.get(folded, folded)


def licence_status(
    licences: Iterable[str], allowed: Iterable[str]
) -> Literal["eligible", "license_not_selected", "no_license"]:
    """Every member must carry an identified licence that the owner listed."""
    normalized = [normalize_license(item) for item in licences]
    selected = {normalize_license(item) for item in allowed} - UNIDENTIFIED
    if not normalized or any(item in UNIDENTIFIED for item in normalized):
        return "no_license"
    if any(item not in selected for item in normalized):
        return "license_not_selected"
    return "eligible"
