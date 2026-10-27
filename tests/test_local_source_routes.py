"""Followed suppliers may suggest routes, never inherit model signing authority."""

from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_discovery import Publisher, mirror
from test_local_owner import finish, submit
from test_local_sources import enroll, source_plan

from foxlight_model_preservation.local_host.owner import LocalOwner
from foxlight_model_preservation.models import TrustProfile
from foxlight_model_preservation.signing import encode_record, public_record
from foxlight_model_preservation.storage import manifest_path


def follow(owner, publisher, endpoint):
    selected = enroll(owner, encode_record(publisher.bootstrap(endpoint)))
    result = finish(
        owner, submit(owner, source_plan(owner, "source_refresh", selected))
    )
    assert result["state"] == "succeeded", result
    return selected


@pytest.mark.parametrize(
    "change",
    ["none", "remove_secondary", "deny_private", "no_direct_primary", "bad_alternate"],
)
def test_explicit_followed_route_fallback_preserves_primary_keys_and_envelope(
    tmp_path, capture, change
):
    first = Publisher(tmp_path / "first", capture)
    foreign_key = public_record(Ed25519PrivateKey.generate())
    foreign_trust = TrustProfile(
        schema_version="foxlight.model-preservation.trust.v1",
        keys=(foreign_key,),
        threshold=1,
        as_of=capture.trust.as_of,
    )
    second = Publisher(tmp_path / "second", replace(capture, trust=foreign_trust))
    unrelated = Publisher(tmp_path / "unrelated", capture, name=first.name)
    empty = tmp_path / "empty"
    empty.mkdir()
    with (
        mirror(first.path) as (index_a, _),
        mirror(second.path) as (index_b, _),
        mirror(unrelated.path) as (index_c, _),
        mirror(empty) as (dead, dead_requests),
        mirror(capture.store) as (healthy, model_requests),
    ):
        first.mirrors = (
            ("ipfs://opaque-peer-hint",) if change == "no_direct_primary" else (dead,)
        )
        first.publish()
        second.mirrors = (
            (
                healthy,
                "https://secondary.invalid:invalid",
                "https://secondary.invalid/?not-a-base",
            )
            if change == "bad_alternate"
            else (healthy,)
        )
        second.publish()
        unrelated.mirrors = ("https://unrelated.invalid",)
        unrelated.publish(manifest="f" * 64)
        owner = LocalOwner(tmp_path / "owner")
        try:
            a, b = follow(owner, first, index_a), follow(owner, second, index_b)
            follow(owner, unrelated, index_c)
            listed = owner.control("source-search", {"query": capture.identity})
            assert listed["total"] == 2
            assert all(row["listed_source_count"] == 2 for row in listed["results"])
            assert all(
                row["suggested_mirror_count"]
                == (1 if change == "no_direct_primary" else 2)
                for row in listed["results"]
            )
            base = source_plan(
                owner,
                "source_metadata",
                a,
                entry_id="small_text",
                manifest_id=capture.identity,
            )
            assert base["include_followed_mirrors"] is False
            assert base["mirrors"] == ([] if change == "no_direct_primary" else [dead])
            assert base["additional_mirror_count"] == 1
            assert "additional_locations" not in base
            expanded = source_plan(
                owner,
                "source_metadata",
                a,
                entry_id="small_text",
                manifest_id=capture.identity,
                include_followed_mirrors=True,
                allow_http=True,
                allow_private=change != "deny_private",
            )
            expected_routes = (
                [healthy] if change == "no_direct_primary" else [dead, healthy]
            )
            assert expanded["mirrors"] == expected_routes
            assert dead_requests == model_requests == []
            if change == "remove_secondary":
                owner.control("source-remove", {"source_id": b["source_id"]})
            details = finish(owner, submit(owner, expanded))
            if change in {"remove_secondary", "deny_private"}:
                assert details["state"] == "failed", details
                assert dead_requests == model_requests == []
                return
            assert details["state"] == "succeeded", details
            reference = owner.control("source-reference", {"job_id": details["job_id"]})
            assert reference["keys"] == [key.key_id for key in capture.trust.keys]
            assert foreign_key.key_id not in reference["keys"]
            assert reference["mirrors"] == expected_routes
            assert model_requests == ["/" + manifest_path(capture.identity)]
            preserved = finish(
                owner,
                submit(
                    owner,
                    owner.control(
                        "plan",
                        {
                            "kind": "source_receive",
                            "reference_job_id": details["job_id"],
                            "keys": reference["keys"],
                            "allow_http": True,
                            "allow_private": True,
                            "max_bytes": reference["size_bytes"] * 2,
                        },
                    ),
                ),
            )
            assert preserved["state"] == "succeeded", preserved
            _, raw, _, _ = owner.sources.reference(details["job_id"])
            assert raw == (capture.store / manifest_path(capture.identity)).read_bytes()
            assert (
                owner.sources.reference(details["job_id"])[0].selection.source_id
                == a["source_id"]
            )
            assert (
                owner.sources.reference(details["job_id"])[0]
                .additional_locations[0]
                .source_id
                == b["source_id"]
            )
        finally:
            owner.close()


def test_malformed_and_protected_hints_do_not_break_offline_source_browsing(
    tmp_path, capture
):
    publisher = Publisher(tmp_path / "publisher", capture)
    publisher.mirrors = (
        "http://[broken",
        "ipfs://opaque-future-hint",
        "https://valid.example",
        "https://hidden.onion./",
        "https://hidden.i2p.alt/",
    )
    publisher.publish()
    with mirror(publisher.path) as (endpoint, _):
        owner = LocalOwner(tmp_path / "owner")
        try:
            source = follow(owner, publisher, endpoint)
            result = owner.control("source-search", {})
            assert result["results"][0]["suggested_mirror_count"] == 1
            planned = source_plan(
                owner,
                "source_metadata",
                source,
                entry_id="small_text",
                manifest_id=capture.identity,
            )
            assert planned["mirrors"] == ["https://valid.example"]
            original = owner.sources.plan(
                {
                    "kind": "source_metadata",
                    "source_id": source["source_id"],
                    "entry_id": "small_text",
                    "manifest_id": capture.identity,
                }
            )
            assert original["selection"]["mirrors"] == list(publisher.mirrors)
        finally:
            owner.close()


def test_equivalent_bases_do_not_become_duplicate_alternate_mirrors(tmp_path, capture):
    first = Publisher(tmp_path / "first", capture)
    second = Publisher(tmp_path / "second", capture)
    with (
        mirror(first.path) as (index_a, _),
        mirror(second.path) as (index_b, _),
        mirror(capture.store) as (healthy, requests),
    ):
        first.mirrors = (healthy, healthy + "/")
        second.mirrors = (
            healthy + "///",
            "https://secondary.invalid:invalid",
            "https://secondary.invalid:0",
            "https://secondary.invalid:65536",
            "https://secondary.invalid/?query=not-a-base",
            "https://secondary.invalid/#fragment",
            "https://user:password@secondary.invalid/",
            "https://secondary.invalid/path with spaces",
        )
        first.publish()
        second.publish()
        owner = LocalOwner(tmp_path / "owner")
        try:
            a, _ = follow(owner, first, index_a), follow(owner, second, index_b)
            listed = owner.control("source-search", {})
            assert all(row["suggested_mirror_count"] == 1 for row in listed["results"])
            planned = source_plan(
                owner,
                "source_metadata",
                a,
                entry_id="small_text",
                manifest_id=capture.identity,
                include_followed_mirrors=True,
                allow_http=True,
                allow_private=True,
            )
            assert planned["mirrors"] == [healthy]
            assert planned["additional_mirror_count"] == 0
            assert requests == []
            result = finish(owner, submit(owner, planned))
            assert result["state"] == "succeeded", result
            record, raw, _, _ = owner.sources.reference(result["job_id"])
            assert record.mirrors == (healthy,)
            assert record.selection.mirrors == first.mirrors
            assert raw == (capture.store / manifest_path(capture.identity)).read_bytes()
            assert requests == ["/" + manifest_path(capture.identity)]
        finally:
            owner.close()


@pytest.mark.parametrize("suffix", ["/", "///"])
def test_source_enrollment_keeps_valid_bootstrap_endpoint_spelling(
    tmp_path, capture, suffix
):
    publisher = Publisher(tmp_path / "publisher", capture)
    with mirror(publisher.path) as (endpoint, requests):
        owner = LocalOwner(tmp_path / "owner")
        try:
            document = encode_record(publisher.bootstrap(endpoint + suffix))
            reviewed = owner.sources.review(document)
            assert reviewed["endpoints"] == [endpoint + suffix]
            assert requests == []
            selected = follow(owner, publisher, endpoint + suffix)
            assert selected["endpoints"] == [endpoint + suffix]
            assert owner.control("source-search", {})["total"] == 1
        finally:
            owner.close()
