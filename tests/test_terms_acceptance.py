"""Keeping a model under use-restricted terms needs an explicit acceptance."""

from __future__ import annotations

import json
import uuid

import pytest
from test_local_owner import accepted_catalog, finish

from foxlight_model_preservation.cards import identity_json
from foxlight_model_preservation.licences import (
    MemberTerms,
    TermsAcceptance,
    terms_review,
)
from foxlight_model_preservation.local_host.owner import LocalOwner
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.signing import digest

REVISION = "a" * 40


def member(license_id: str, **changes: str) -> MemberTerms:
    values = {
        "member": "weights",
        "license_id": license_id,
        "reference": "LICENSE",
        "repository": "example/model",
        "revision": REVISION,
    }
    values.update(changes)
    return MemberTerms.model_validate(values)


@pytest.mark.parametrize(
    ("license_id", "family"),
    [
        ("Llama3.1", "custom"),
        ("gemma", "custom"),
        ("openrail-m", "responsible-ai"),
        ("cc-by-nc-4.0", "non-commercial"),
        ("qwen-research", "non-commercial"),
    ],
)
def test_use_restricted_terms_need_acceptance_with_a_plain_summary(
    license_id: str, family: str
):
    review = terms_review([member(license_id)])
    (row,) = review["licences"]  # type: ignore[misc]
    assert row["family"] == family and row["requires_acceptance"] is True
    assert row["summary"] and row["name"]
    assert review["accept"] == [license_id.lower()]
    # The full text is linked: the same licence file the bundle carries.
    assert row["references"] == [
        {
            "members": ["weights"],
            "reference": "LICENSE",
            "url": f"https://huggingface.co/example/model/blob/{REVISION}/LICENSE",
        }
    ]


def test_plain_permissive_licences_need_no_acceptance_and_unknown_ones_do():
    review = terms_review(
        [member("Apache-2.0"), member("MIT", member="tokenizer"), member("GPL-3.0")]
    )
    assert review["accept"] == []
    unknown = terms_review([member("other"), member("my-own-terms", member="b")])
    assert unknown["accept"] == ["my-own-terms", "other"]
    names = {row["license_id"]: row["name"] for row in unknown["licences"]}  # type: ignore[index,union-attr]
    assert names["other"] == "No identified licence"


def test_non_commercial_summary_and_llama_attribution_are_plain():
    review = terms_review([member("cc-by-nc-4.0"), member("llama3.1", member="b")])
    summaries = {
        row["license_id"]: row["summary"]  # type: ignore[index]
        for row in review["licences"]  # type: ignore[union-attr]
    }
    assert summaries["cc-by-nc-4.0"].startswith("Non-commercial use only")
    assert "Built with Llama" in summaries["llama3.1"]
    assert "acceptable use policy" in summaries["llama3.1"]


@pytest.mark.parametrize(
    "reference",
    ["../LICENSE", "/etc/passwd", "LICENSE?x=1", "Model card: a/b@c", "has space"],
)
def test_links_are_only_made_for_plain_bundle_paths(reference: str):
    review = terms_review([member("llama3", reference=reference)])
    assert review["licences"][0]["references"][0]["url"] is None  # type: ignore[index]


def test_receiving_records_the_acceptance_before_any_download(tmp_path, monkeypatch):
    catalog, repo, _ = accepted_catalog(tmp_path)
    owner = LocalOwner(tmp_path / "owner", catalog=catalog)
    try:
        plan = owner.control(
            "plan",
            {
                "kind": "receive",
                "catalog_id": digest(repo.target),
                "card_id": repo.card.card_id,
            },
        )
        terms = plan["terms"]
        assert terms["definition"] == 1
        accept = terms["accept"]
        # The fixture card's licence is not one Sovereign knows, so it is gated.
        assert accept
        operation = str(uuid.uuid4())
        body = {
            "plan_id": plan["plan_id"],
            "operation_id": operation,
            "confirmed": True,
        }
        for wrong in (None, [], ["mit"], [*accept, "mit"], "yes"):
            with pytest.raises(PreservationError, match="terms_acceptance_required"):
                owner.control(
                    "start",
                    body if wrong is None else {**body, "accepted_terms": wrong},
                )
        assert not owner.read()["jobs"]
        receipt = owner.control("start", {**body, "accepted_terms": list(accept)})
        job_id = digest(
            identity_json({"operation_id": operation, "plan_id": plan["plan_id"]})
        )
        assert receipt["job_id"] == job_id
        record = TermsAcceptance.model_validate_json(
            (owner.home / "terms-acceptance" / (job_id + ".json")).read_bytes()
        )
        assert record.accepted == tuple(sorted(accept))
        assert record.plan_id == plan["plan_id"] and record.definition == 1
        # A replay of the started job needs no second acceptance.
        assert owner.control("start", body)["job_id"] == job_id
        finish(owner, receipt)
        assert (
            json.loads(
                (owner.home / "terms-acceptance" / (job_id + ".json")).read_text()
            )["job_id"]
            == job_id
        )
    finally:
        owner.close()


@pytest.mark.parametrize(
    ("license_id", "accept"), [("MIT", []), ("llama3.1", ["llama3.1"])]
)
def test_every_supported_member_count_has_a_terms_review(
    license_id: str, accept: list[str]
):
    """Archives hold up to 256 members; identical references are listed once."""
    members = [member(license_id, member=f"member-{index}") for index in range(256)]
    review = terms_review(members)
    assert review["accept"] == accept
    (row,) = review["licences"]  # type: ignore[misc]
    (reference,) = row["references"]  # type: ignore[index]
    assert len(reference["members"]) == 256  # type: ignore[index]
    # Distinct references stay distinct.
    varied = [
        member(license_id, member=f"m{index}", reference=f"LICENSE-{index}")
        for index in range(256)
    ]
    assert len(terms_review(varied)["licences"][0]["references"]) == 256  # type: ignore[index]
    with pytest.raises(PreservationError, match="too_many_members"):
        terms_review([*members, member(license_id, member="one-more")])


def test_a_256_member_receive_plan_records_its_acceptance(tmp_path):
    from foxlight_model_preservation.licences import LicenceTerms

    review = terms_review(
        [member("llama3.1", member=f"member-{index}") for index in range(256)]
    )
    rows = [
        LicenceTerms.model_validate_json(json.dumps(row)) for row in review["licences"]
    ]  # type: ignore[union-attr]
    assert rows[0].references[0].members[255] == "member-255"
    record = TermsAcceptance(
        plan_id="a" * 64,
        job_id="b" * 64,
        name="large archive",
        definition=1,
        accepted=tuple(f"licence-{index}" for index in range(256)),
        accepted_at=0,
    )
    assert len(record.accepted) == 256
