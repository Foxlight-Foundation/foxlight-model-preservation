"""The core's canonical licence-family definition, as the Library reads it.

The managed Library does not install the core package, so it ships a
byte-identical copy of the core's ``resources/licence_families_v1.json`` next
to this module. The core's test suite checks the two files are equal, and the
Library's parity test compares the parsed values whenever the core is
installed. Nothing here decides anything the core would decide differently.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from functools import cache
from importlib.resources import files
from typing import Final, Literal, cast
from urllib.parse import quote

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


LICENCE_DEFINITION_VERSION: Final = cast(int, definition_json()["version"])
UNIDENTIFIED: Final = frozenset(
    str(item) for item in cast(list[JsonValue], definition_json()["unidentified"])
)
_ALIASES: Final = {
    str(key): str(value)
    for key, value in cast(dict[str, JsonValue], definition_json()["aliases"]).items()
}


def _licences(family: dict[str, JsonValue]) -> list[dict[str, JsonValue]]:
    return [
        cast(dict[str, JsonValue], licence)
        for licence in cast(list[JsonValue], family["licences"])
    ]


# On for a new setup, as in the core: a licence's own default overrides its
# family's (a maker's terms not known to allow redistribution stay off).
DEFAULT_LICENSES: Final = tuple(
    str(licence["id"])
    for family in _families()
    for licence in _licences(family)
    if (
        family.get("default") is True
        if licence.get("default") is None
        else licence.get("default") is True
    )
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


# A preservation archive holds at most 256 members (v1 and PMF alike).
MAX_TERMS_MEMBERS: Final = 256
# A link longer than this is left out, as in the core.
MAX_LINK: Final = 4096
_UNKNOWN_SUMMARY: Final = (
    "This listing doesn't say which licence applies. Read the model's own "
    "terms before using it."
)
_HUB_REPOSITORY: Final = re.compile(r"[A-Za-z0-9][\w.-]{0,95}/[\w.-]{1,96}")
_HUB_REVISION: Final = re.compile(r"[0-9a-f]{40}")


def _text(value: JsonValue, limit: int) -> str | None:
    return value[:limit] if isinstance(value, str) and value else None


def _reference_url(item: dict[str, JsonValue]) -> str | None:
    """The same rule as the core: link only a plain file at an exact revision."""
    reference = _text(item.get("reference"), 2048) or ""
    repository, revision = item.get("repository"), item.get("revision")
    if reference.startswith("https://huggingface.co/") and not any(
        character.isspace() for character in reference
    ):
        return reference if len(reference) <= MAX_LINK else None
    if (
        not isinstance(repository, str)
        or not isinstance(revision, str)
        or _HUB_REPOSITORY.fullmatch(repository) is None
        or _HUB_REVISION.fullmatch(revision) is None
        or not reference
        or reference.startswith(("/", "."))
        or any(character.isspace() or character in "?#\\" for character in reference)
        or ":" in reference
    ):
        return None
    url = f"https://huggingface.co/{repository}/blob/{revision}/{quote(reference)}"
    # Link metadata that can't be represented is omitted, never refused.
    return url if len(url) <= MAX_LINK else None


def terms_review(members: list[dict[str, JsonValue]]) -> dict[str, JsonValue]:
    """The core's terms review, for members Core reports on a receive plan.

    Each member is ``{member, license_id, reference, repository?, revision?}``;
    every supported archive (up to 256 members) is accepted.
    Licences with use restrictions or conditions to pass on, and any that
    can't be identified, need an explicit acceptance before the download.
    """
    known: dict[str, tuple[dict[str, JsonValue], dict[str, JsonValue]]] = {
        str(licence["id"]): (family, licence)
        for family in _families()
        for licence in _licences(family)
    }
    grouped: dict[str, list[dict[str, JsonValue]]] = {}
    if len(members) > MAX_TERMS_MEMBERS:
        raise ValueError("terms_review_too_many_members")
    for item in members:
        licence = item.get("license_id")
        grouped.setdefault(
            normalize_license(licence if isinstance(licence, str) else ""), []
        ).append(item)
    rows: list[JsonValue] = []
    accept: list[str] = []
    for license_id, listed in grouped.items():
        # Identical references are listed once, with the members sharing them.
        shared: dict[tuple[str, str | None], list[JsonValue]] = {}
        for item in listed:
            key = (
                _text(item.get("reference"), 2048) or "not stated",
                _reference_url(item),
            )
            member = _text(item.get("member"), 64) or "member"
            sharing = shared.setdefault(key, [])
            if member not in sharing:
                sharing.append(member)
        references: list[JsonValue] = [
            {"members": sharing, "reference": reference, "url": url}
            for (reference, url), sharing in shared.items()
        ]
        found = known.get(license_id)
        if found is None:
            unidentified = license_id in UNIDENTIFIED
            first = listed[0].get("license_id")
            row: dict[str, JsonValue] = {
                "license_id": license_id,
                "name": "Licence not named"
                if unidentified or not isinstance(first, str)
                else first.strip()[:200],
                "family": None,
                "summary": _UNKNOWN_SUMMARY
                if unidentified
                else "These terms aren't in Sovereign's licence list. Read them "
                "before using the model.",
                "requires_acceptance": True,
                "references": references,
            }
        else:
            family, licence = found
            row = {
                "license_id": license_id,
                "name": licence["name"],
                "family": family["id"],
                "summary": licence.get("summary") or family["summary"],
                "requires_acceptance": family.get("acceptance") is True,
                "references": references,
            }
        rows.append(row)
        if row["requires_acceptance"] is True:
            accept.append(license_id)
    return {
        "definition": LICENCE_DEFINITION_VERSION,
        "licences": rows,
        "accept": cast(list[JsonValue], sorted(accept)),
    }
