"""Receive plans that name member licences need Hugging Face-style acceptance."""

from __future__ import annotations

import importlib.util

import pytest
from pydantic import JsonValue

from foxlight_preservation_library.client import CoreRefused
from foxlight_preservation_library.licences import terms_review
from foxlight_preservation_library.terms import accepted_terms, with_terms

REVISION = "b" * 40
MEMBERS: list[JsonValue] = [
    {
        "member": "weights",
        "license_id": "Llama3.1",
        "reference": "LICENSE",
        "repository": "meta-llama/Llama-3.1-8B",
        "revision": REVISION,
    },
    {"member": "tokenizer", "license_id": "MIT", "reference": "LICENSE"},
]
BODY: dict[str, JsonValue] = {"plan_id": "a" * 64, "operation_id": "op"}


def plan(**changes: JsonValue) -> dict[str, JsonValue]:
    return {"kind": "receive", "plan_id": "a" * 64, **changes}


def test_an_older_core_plan_is_unchanged_and_carries_no_acceptance() -> None:
    assert with_terms(plan()) == plan()
    assert accepted_terms(plan(), BODY) == BODY
    with pytest.raises(CoreRefused, match="invalid_submission"):
        accepted_terms(plan(), {**BODY, "accepted_terms": []})


def test_restricted_terms_are_described_and_must_be_accepted_exactly() -> None:
    reviewed = with_terms(plan(member_terms=MEMBERS))
    terms = reviewed["terms"]
    assert isinstance(terms, dict) and terms["accept"] == ["llama3.1"]
    rows = {row["license_id"]: row for row in terms["licences"]}  # type: ignore[index,union-attr]
    assert rows["llama3.1"]["requires_acceptance"] is True
    assert "Built with Llama" in rows["llama3.1"]["summary"]
    assert rows["llama3.1"]["references"][0]["url"] == (
        f"https://huggingface.co/meta-llama/Llama-3.1-8B/blob/{REVISION}/LICENSE"
    )
    assert rows["mit"]["requires_acceptance"] is False
    selected = plan(member_terms=MEMBERS)
    for wrong in (None, [], ["mit"], ["llama3.1", "mit"], "yes"):
        with pytest.raises(CoreRefused, match="terms_acceptance_required"):
            accepted_terms(
                selected, BODY if wrong is None else {**BODY, "accepted_terms": wrong}
            )
    assert accepted_terms(selected, {**BODY, "accepted_terms": ["llama3.1"]}) == {
        **BODY,
        "accepted_terms": ["llama3.1"],
    }


def test_plain_permissive_terms_need_no_acceptance() -> None:
    selected = plan(
        member_terms=[{"member": "m", "license_id": "Apache-2.0", "reference": "L"}]
    )
    assert accepted_terms(selected, BODY) == BODY
    assert accepted_terms(selected, {**BODY, "accepted_terms": []}) == BODY


@pytest.mark.skipif(
    importlib.util.find_spec("foxlight_model_preservation") is None,
    reason="the core package is optional for the managed Library",
)
def test_terms_review_matches_the_core() -> None:
    from foxlight_model_preservation.licences import MemberTerms
    from foxlight_model_preservation.licences import terms_review as core_review

    samples: list[list[JsonValue]] = [
        MEMBERS,
        [{"member": "a", "license_id": "other", "reference": "../x"}],
        [{"member": "a", "license_id": "cc-by-nc-4.0", "reference": "has space"}],
        [{"member": "a", "license_id": "my-terms", "reference": "LICENSE"}],
    ]
    for sample in samples:
        members = [
            MemberTerms.model_validate(
                {"repository": None, "revision": None, **item}  # type: ignore[dict-item]
            )
            for item in sample
        ]
        assert terms_review(sample) == core_review(members)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("license_id", "accept"), [("MIT", []), ("llama3", ["llama3"])]
)
def test_receive_plans_with_256_members_are_reviewed(
    license_id: str, accept: list[str]
) -> None:
    members: list[JsonValue] = [
        {"member": f"m{index}", "license_id": license_id, "reference": "LICENSE"}
        for index in range(256)
    ]
    reviewed = with_terms(plan(member_terms=members))
    terms = reviewed["terms"]
    assert isinstance(terms, dict) and terms["accept"] == accept
    (row,) = terms["licences"]  # type: ignore[misc]
    (reference,) = row["references"]  # type: ignore[index]
    assert len(reference["members"]) == 256  # type: ignore[index]
    body = {**BODY, "accepted_terms": accept} if accept else BODY
    assert accepted_terms(plan(member_terms=members), body) == body
    with pytest.raises(CoreRefused):
        with_terms(plan(member_terms=[*members, members[0]]))
