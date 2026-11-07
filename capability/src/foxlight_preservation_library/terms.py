"""Hugging Face-style terms acceptance before Core keeps a received model.

A Core that reports each member's licence on a receive plan (``member_terms``)
gets a terms review on that plan. Licences with use restrictions or conditions
to pass on, and any that can't be identified, then need an explicit "I accept
these terms" before the download starts; the Library forwards exactly the
accepted licence identifiers so Core can record the acceptance with the copy.
A Core that reports no member terms is unchanged: nothing is added or sent.
This describes licences; it is not legal advice.
"""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from .client import CoreRefused
from .licences import terms_review


def _members(plan: dict[str, JsonValue]) -> list[dict[str, JsonValue]] | None:
    members = plan.get("member_terms")
    if not isinstance(members, list):
        return None
    if len(members) > 100 or not all(isinstance(item, dict) for item in members):
        raise CoreRefused("invalid_host_response")
    return [cast(dict[str, JsonValue], item) for item in members]


def with_terms(plan: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Add the terms review to a receive plan whose members Core describes."""
    members = _members(plan) if plan.get("kind") == "receive" else None
    if members is None:
        return plan
    return {**plan, "terms": terms_review(members)}


def accepted_terms(
    plan: dict[str, JsonValue], body: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    """Require exactly the reviewed acceptance, and forward only that.

    Refuses a start that omits a needed acceptance, accepts other licences,
    or sends an acceptance a plan without member terms cannot carry.
    """
    accepted = body.get("accepted_terms")
    members = _members(plan) if plan.get("kind") == "receive" else None
    if members is None:
        if accepted is not None:
            raise CoreRefused("invalid_submission")
        return body
    wanted = terms_review(members)["accept"]
    required = sorted(str(item) for item in cast(list[JsonValue], wanted))
    if accepted is None:
        if required:
            raise CoreRefused("terms_acceptance_required")
        return body
    if (
        not isinstance(accepted, list)
        or len(accepted) > 100
        or not all(isinstance(item, str) for item in accepted)
        or sorted(cast(list[str], accepted)) != required
    ):
        raise CoreRefused("terms_acceptance_required")
    if not required:
        return {key: value for key, value in body.items() if key != "accepted_terms"}
    return {**body, "accepted_terms": cast(list[JsonValue], required)}
