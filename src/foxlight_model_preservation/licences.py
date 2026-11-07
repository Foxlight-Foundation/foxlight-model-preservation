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
import re
from collections.abc import Iterable
from functools import cache
from importlib.resources import files
from typing import Literal, Protocol
from urllib.parse import quote

from pydantic import Field, JsonValue

from .models import PreservationError, Record

DEFINITION_RESOURCE = "licence_families_v1.json"


class KnownLicence(Record):
    """One exact licence identifier and the words the app shows for it.

    ``summary`` says in a few plain words what a person accepts when keeping
    or downloading a model under it. ``default`` overrides the family's
    default (for example a maker's terms we could not confirm allow
    redistribution). ``warning`` is shown while the licence is on;
    ``eu_subject`` names what the terms don't license in the EU, and the
    sheet combines the licences that are on into one notice.
    """

    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=300)
    note: str | None = Field(default=None, max_length=300)
    warning: str | None = Field(default=None, max_length=300)
    eu_subject: str | None = Field(
        default=None,
        max_length=100,
        description="What the maker's terms don't license in the EU.",
    )
    short_name: str | None = Field(default=None, max_length=60)
    default: bool | None = None


class LicenceFamily(Record):
    """A group of licences with the same kind of sharing obligations."""

    id: str = Field(pattern=r"^[a-z][a-z-]{0,39}$")
    name: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=300)
    default: bool
    acceptance: bool = Field(
        description="Keeping a model under it asks the person to accept its terms."
    )
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
            if family.acceptance and not licence.summary:
                raise PreservationError("licence terms need a plain summary")
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
# On for a new setup: every family's licences, except a maker's terms that
# are not known to allow redistribution (each says why).
DEFAULT_LICENSES = tuple(
    licence.id
    for family in definition().families
    for licence in family.licences
    if (family.default if licence.default is None else licence.default)
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


# A preservation archive holds at most 256 members (v1 and PMF alike).
MAX_TERMS_MEMBERS = 256


class TermsReference(Record):
    """Where a licence's full text is (a file in the bundle, or a card), once.

    Members that share the same reference are listed together, so a large
    archive whose members all carry the same LICENSE yields one entry.
    """

    members: tuple[str, ...] = Field(min_length=1, max_length=MAX_TERMS_MEMBERS)
    reference: str = Field(min_length=1, max_length=2048)
    url: str | None = Field(default=None, max_length=4096)


class LicenceTerms(Record):
    """What a person accepts for one licence before keeping a model under it."""

    license_id: str = Field(max_length=200)
    name: str = Field(min_length=1, max_length=200)
    family: str | None = None
    summary: str = Field(min_length=1, max_length=300)
    requires_acceptance: bool
    references: tuple[TermsReference, ...] = Field(
        default=(), max_length=MAX_TERMS_MEMBERS
    )


class MemberTerms(Record):
    """One member's declared licence and where its full text is."""

    member: str = Field(min_length=1, max_length=200)
    license_id: str = Field(max_length=200)
    reference: str = Field(min_length=1, max_length=2048)
    repository: str | None = Field(default=None, max_length=400)
    revision: str | None = Field(default=None, max_length=200)


_UNKNOWN_SUMMARY = (
    "The licence couldn't be identified. Read the terms that come with the "
    "model before using it."
)
_HUB_REPOSITORY = re.compile(r"[A-Za-z0-9][\w.-]{0,95}/[\w.-]{1,96}")
_HUB_REVISION = re.compile(r"[0-9a-f]{40}")


def _reference_url(member: MemberTerms) -> str | None:
    """A public link to the same licence file upstream, when one can be named.

    The full text is also carried in the bundle itself (its notice files);
    this link only lets the person read it before downloading.
    """
    reference = member.reference
    if reference.startswith("https://huggingface.co/") and not any(
        character.isspace() for character in reference
    ):
        return reference
    if (
        member.repository is None
        or member.revision is None
        or _HUB_REPOSITORY.fullmatch(member.repository) is None
        or _HUB_REVISION.fullmatch(member.revision) is None
        or reference.startswith(("/", "."))
        or any(character.isspace() or character in "?#\\" for character in reference)
        or ":" in reference
    ):
        return None
    return (
        f"https://huggingface.co/{member.repository}/blob/{member.revision}/"
        f"{quote(reference)}"
    )


class _Intake(Protocol):
    @property
    def license_id(self) -> str: ...
    @property
    def terms_reference(self) -> str: ...


class _Member(Protocol):
    @property
    def member_id(self) -> str: ...
    @property
    def intake(self) -> _Intake: ...


def member_terms(
    members: Iterable[_Member], *, fallback: str = ""
) -> list[MemberTerms]:
    """Each member's declared licence and terms reference, for a terms review.

    A member that declares no identified licence (for example a registry
    capture, which says "see-original-card") takes ``fallback``, the licence
    its original model card names, when there is one.
    """
    result: list[MemberTerms] = []
    for member in members:
        license_id = member.intake.license_id
        if normalize_license(license_id) in UNIDENTIFIED and fallback:
            license_id = fallback
        origin = getattr(member, "origin", None)
        repository = getattr(member, "origin_repository", None) or getattr(
            origin, "repository", None
        )
        revision = getattr(member, "origin_revision", None) or getattr(
            origin, "revision", None
        )
        result.append(
            MemberTerms(
                member=str(member.member_id)[:200],
                license_id=license_id[:200],
                reference=member.intake.terms_reference,
                repository=repository if isinstance(repository, str) else None,
                revision=revision if isinstance(revision, str) else None,
            )
        )
    return result


def terms_review(members: Iterable[MemberTerms]) -> dict[str, JsonValue]:
    """Describe the terms a person accepts before keeping or downloading a model.

    This mirrors the Hugging Face gate: licences with use restrictions or
    conditions to pass on (responsible-AI, non-commercial and makers' own
    terms), and any licence that can't be identified, need an explicit "I
    accept these terms". Plain permissive, attribution and copyleft licences
    are listed but need no acceptance. This is a description, not legal advice.
    """
    families = {
        licence.id: (family, licence)
        for family in definition().families
        for licence in family.licences
    }
    grouped: dict[str, list[MemberTerms]] = {}
    members = list(members)
    if len(members) > MAX_TERMS_MEMBERS:
        raise PreservationError("terms_review_too_many_members")
    for member in members:
        grouped.setdefault(normalize_license(member.license_id), []).append(member)
    rows: list[LicenceTerms] = []
    for license_id, listed in grouped.items():
        known = families.get(license_id)
        shared: dict[tuple[str, str | None], list[str]] = {}
        for item in listed:
            sharing = shared.setdefault((item.reference, _reference_url(item)), [])
            if item.member not in sharing:
                sharing.append(item.member)
        references = tuple(
            TermsReference(members=tuple(sharing), reference=reference, url=url)
            for (reference, url), sharing in shared.items()
        )
        if known is None:
            unidentified = license_id in UNIDENTIFIED
            rows.append(
                LicenceTerms(
                    license_id=license_id,
                    name="No identified licence"
                    if unidentified
                    else listed[0].license_id.strip()[:200],
                    summary=_UNKNOWN_SUMMARY
                    if unidentified
                    else "These terms aren't in Sovereign's licence list. Read "
                    "them before using the model.",
                    requires_acceptance=True,
                    references=references,
                )
            )
            continue
        family, licence = known
        rows.append(
            LicenceTerms(
                license_id=license_id,
                name=licence.name,
                family=family.id,
                summary=licence.summary or family.summary,
                requires_acceptance=family.acceptance,
                references=references,
            )
        )
    accept = sorted(row.license_id for row in rows if row.requires_acceptance)
    return {
        "definition": LICENCE_DEFINITION_VERSION,
        "licences": [row.model_dump(mode="json") for row in rows],
        "accept": list(accept),
    }


class TermsAcceptance(Record):
    """A local record that the person accepted a model's terms before keeping it."""

    schema_version: Literal["sovereign.terms-acceptance.v1"] = (
        "sovereign.terms-acceptance.v1"
    )
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    job_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str = Field(min_length=1, max_length=1000)
    definition: int = Field(ge=1)
    accepted: tuple[str, ...] = Field(max_length=MAX_TERMS_MEMBERS)
    accepted_at: int = Field(ge=0)
