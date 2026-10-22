"""Real public-only copies, explicit terms and refusal of private evidence."""

from pathlib import Path

import pytest
from conftest import Capture
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from foxlight_model_preservation.models import PreservationError, Recipe, TrustProfile
from foxlight_model_preservation.operations import OperationCancelled, OperationControl
from foxlight_model_preservation.publication import (
    PublicationRequest,
    PublicMemberTerms,
    plan_publication,
    publish_public,
)
from foxlight_model_preservation.seed import SeedServer
from foxlight_model_preservation.signing import digest, encode_record, public_record
from foxlight_model_preservation.storage import pack, restore, verify_store


def private_capture(capture: Capture, tmp_path: Path) -> Capture:
    """Retain an intentionally private evidence member beside ordinary model data."""
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "LICENSE").write_text("Private owner observations.\n")
    (evidence / "model.bin").write_text("private installed paths and old grants\n")
    members = [
        item.model_copy(
            update={
                "retained_card": None,
                "intake": item.intake.model_copy(update={"audience": "private"}),
                "dependencies": (*item.dependencies, "source-evidence")
                if item.member_id == capture.recipe.root_member
                else item.dependencies,
            }
        )
        for item in capture.recipe.members
    ]
    members.append(
        members[-1].model_copy(
            update={"member_id": "source-evidence", "role": "notices"}
        )
    )
    recipe = Recipe.model_validate(
        {**capture.recipe.model_dump(exclude={"members"}), "members": tuple(members)}
    )
    sources = {**capture.sources, "source-evidence": evidence}
    store = tmp_path / "private"
    identity = pack(recipe, sources, store, (capture.key,))
    return Capture(recipe, sources, capture.key, capture.trust, store, identity)


def request_for(capture: Capture) -> PublicationRequest:
    """Declare the public name and per-data-member terms separately."""
    return PublicationRequest(
        name="Public test model",
        workloads=("test-only",),
        members=tuple(
            PublicMemberTerms(
                member_id=item.member_id,
                license_id="test-only",
                terms_reference="LICENSE",
                decision="Explicit public synthetic test publication",
                notice_paths=("LICENSE",),
            )
            for item in capture.recipe.members
            if item.role != "notices"
        ),
    )


def test_public_copy_omits_private_evidence_and_preserves_data(
    capture: Capture, tmp_path: Path
) -> None:
    private = private_capture(capture, tmp_path)
    request = request_for(private)
    planned = plan_publication(private.store, private.identity, private.trust, request)
    identity = digest(encode_record(planned))
    assert identity != private.identity
    assert planned.runtime_requirements == ()
    assert {item.member_id for item in planned.members} == {"weights", "tokenizer"}
    assert all(item.intake.audience == "public" for item in planned.members)
    publisher = Ed25519PrivateKey.generate()
    trust = private.trust.model_copy(update={"keys": (public_record(publisher),)})
    destination = tmp_path / "public"
    assert (
        publish_public(
            private.store,
            private.identity,
            private.trust,
            request,
            destination,
            (publisher,),
            expected_manifest_id=identity,
        )
        == identity
    )
    assert verify_store(destination, identity, trust) == planned
    restored = tmp_path / "restored"
    restore(destination, identity, trust, restored)
    assert (restored / "members/weights/model.bin").read_bytes() == (
        private.sources["weights"] / "model.bin"
    ).read_bytes()
    assert not (restored / "members/source-evidence").exists()
    assert not (destination / "manifests" / (private.identity + ".json")).exists()
    assert b"old grants" not in b"".join(
        path.read_bytes() for path in destination.rglob("*") if path.is_file()
    )
    assert (
        verify_store(private.store, private.identity, private.trust).members[-1].role
        == "notices"
    )
    server = SeedServer(("127.0.0.1", 0), destination, (identity,), trust, public=True)
    try:
        assert all(".operations" not in path for path in server.allowed)
        assert all(private.identity not in path for path in server.allowed)
    finally:
        server.server_close()


def test_publication_requires_reviewed_identity_and_separate_key(
    capture: Capture, tmp_path: Path
) -> None:
    private = private_capture(capture, tmp_path)
    request = request_for(private)
    planned = plan_publication(private.store, private.identity, private.trust, request)
    identity = digest(encode_record(planned))
    for keys, expected, reason in (
        ((capture.key,), identity, "separate publishing"),
        ((Ed25519PrivateKey.generate(),), "f" * 64, "reviewed plan"),
    ):
        with pytest.raises(PreservationError, match=reason):
            publish_public(
                private.store,
                private.identity,
                private.trust,
                request,
                tmp_path / "public",
                keys,
                expected_manifest_id=expected,
            )
    assert not (tmp_path / "public").exists()


def test_publication_requires_complete_terms_and_original_notices(
    capture: Capture, tmp_path: Path
) -> None:
    private = private_capture(capture, tmp_path)
    request = request_for(private)
    invalid = (
        request.model_copy(update={"members": request.members[:-1]}),
        request.model_copy(
            update={"members": (request.members[0], request.members[0])}
        ),
        request.model_copy(
            update={
                "members": (
                    request.members[0].model_copy(
                        update={"license_id": "unresolved-owner-capture"}
                    ),
                    request.members[1],
                )
            }
        ),
        request.model_copy(
            update={
                "members": (
                    request.members[0].model_copy(
                        update={"notice_paths": ("model.bin",)}
                    ),
                    request.members[1],
                )
            }
        ),
    )
    for choice in invalid:
        with pytest.raises(PreservationError):
            plan_publication(private.store, private.identity, private.trust, choice)


def test_an_unused_capture_trust_key_is_not_a_public_publisher(
    capture: Capture, tmp_path: Path
) -> None:
    private = private_capture(capture, tmp_path)
    unused_capture_key = Ed25519PrivateKey.generate()
    trust = private.trust.model_copy(
        update={
            "keys": (*private.trust.keys, public_record(unused_capture_key)),
        }
    )
    request = request_for(private)
    planned = plan_publication(private.store, private.identity, trust, request)
    with pytest.raises(PreservationError, match="separate publishing identity"):
        publish_public(
            private.store,
            private.identity,
            trust,
            request,
            tmp_path / "public",
            (unused_capture_key,),
            expected_manifest_id=digest(encode_record(planned)),
        )
    assert not (tmp_path / "public").exists()


def test_unknown_retained_metadata_is_not_silently_published(capture: Capture) -> None:
    # The original fixture's legacy verification label is not a public-card profile.
    with pytest.raises(ValueError):
        plan_publication(
            capture.store, capture.identity, capture.trust, request_for(capture)
        )


def test_accidentally_captured_credentials_cannot_be_published(
    capture: Capture, tmp_path: Path
) -> None:
    from foxlight_model_preservation.models import SourceFile

    private = private_capture(capture, tmp_path)
    (private.sources["weights"] / ".env.local").write_text("TEST_ONLY_NOT_A_SECRET=1\n")
    member = private.recipe.members[0]
    member = member.model_copy(
        update={"files": (*member.files, SourceFile(path=".env.local"))}
    )
    recipe = private.recipe.model_copy(
        update={"members": (member, *private.recipe.members[1:])}
    )
    identity = pack(recipe, private.sources, private.store, (private.key,))
    with pytest.raises(PreservationError, match="private operational"):
        plan_publication(private.store, identity, private.trust, request_for(private))


def test_publication_honors_cancellation_before_writes(
    capture: Capture, tmp_path: Path
) -> None:
    private = private_capture(capture, tmp_path)
    request = request_for(private)
    planned = plan_publication(private.store, private.identity, private.trust, request)
    control = OperationControl()
    control.cancel()
    with pytest.raises(OperationCancelled):
        publish_public(
            private.store,
            private.identity,
            private.trust,
            request,
            tmp_path / "public",
            (Ed25519PrivateKey.generate(),),
            expected_manifest_id=digest(encode_record(planned)),
            control=control,
        )
    assert not (tmp_path / "public").exists()


def test_original_separate_notice_members_are_retained(
    capture: Capture, tmp_path: Path
) -> None:
    private = private_capture(capture, tmp_path)
    upstream = private.recipe.members[-1].model_copy(
        update={"member_id": "upstream-notices"}
    )
    root = private.recipe.members[0].model_copy(
        update={
            "dependencies": (
                *private.recipe.members[0].dependencies,
                upstream.member_id,
            )
        }
    )
    recipe = private.recipe.model_copy(
        update={"members": (root, *private.recipe.members[1:], upstream)}
    )
    sources = {**private.sources, upstream.member_id: private.sources["weights"]}
    identity = pack(recipe, sources, private.store, (private.key,))
    request = request_for(private)
    request = request.model_copy(
        update={
            "members": (
                *request.members,
                request.members[0].model_copy(update={"member_id": upstream.member_id}),
            )
        }
    )
    planned = plan_publication(private.store, identity, private.trust, request)
    assert upstream.member_id in {member.member_id for member in planned.members}
    assert upstream.member_id in planned.members[0].dependencies


def test_cli_review_then_publish_uses_the_same_exact_public_plan(
    capture: Capture, tmp_path: Path
) -> None:
    import json

    from test_cli_catalog import command

    private = private_capture(capture, tmp_path)
    trust_file = tmp_path / "source-trust.json"
    trust_file.write_bytes(encode_record(private.trust))
    request_file = tmp_path / "request.json"
    request_file.write_bytes(encode_record(request_for(private)))
    base = (
        private.identity,
        "--store",
        str(private.store),
        "--trust",
        str(trust_file),
        "--request",
        str(request_file),
    )
    plan = command("plan-publication", *base)
    assert plan.returncode == 0, plan.stderr
    identity = json.loads(plan.stdout)["manifest_id"]
    key_file, public_trust = (
        tmp_path / "publisher.pem",
        tmp_path / "publisher-trust.json",
    )
    generated = command(
        "keygen",
        "--key",
        str(key_file),
        "--trust-out",
        str(public_trust),
        "--unencrypted",
    )
    assert generated.returncode == 0, generated.stderr
    destination = tmp_path / "public"
    published = command(
        "publish",
        *base,
        "--output",
        str(destination),
        "--key",
        str(key_file),
        "--unencrypted",
        "--expected-manifest-id",
        identity,
    )
    assert published.returncode == 0, published.stderr
    assert json.loads(published.stdout)["manifest"] == identity
    trust = TrustProfile.model_validate_json(public_trust.read_bytes())
    assert verify_store(destination, identity, trust).name == "Public test model"


@pytest.mark.parametrize("existing", ["empty", "private", "symlink"])
def test_publication_never_reuses_an_existing_static_mirror_root(
    capture: Capture, tmp_path: Path, existing: str
) -> None:
    private = private_capture(capture, tmp_path)
    request = request_for(private)
    planned = plan_publication(private.store, private.identity, private.trust, request)
    destination = tmp_path / "public"
    if existing == "symlink":
        destination.symlink_to(private.store, target_is_directory=True)
    else:
        destination.mkdir()
        if existing == "private":
            (destination / "credentials.txt").write_text("unrelated private fixture")
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    with pytest.raises(PreservationError, match="fresh directory"):
        publish_public(
            private.store,
            private.identity,
            private.trust,
            request,
            destination,
            (Ed25519PrivateKey.generate(),),
            expected_manifest_id=digest(encode_record(planned)),
        )
    assert (
        sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
        == before
    )
    if existing == "private":
        assert (
            destination / "credentials.txt"
        ).read_text() == "unrelated private fixture"


def test_metadata_review_is_pure_and_full_publish_still_rechecks_bytes(
    capture: Capture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from foxlight_model_preservation import publication
    from foxlight_model_preservation.storage import object_path

    private = private_capture(capture, tmp_path)
    source = verify_store(private.store, private.identity, private.trust)
    request = request_for(private)
    expected = plan_publication(private.store, private.identity, private.trust, request)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("metadata review read weight bytes")

    with monkeypatch.context() as patch:
        patch.setattr(publication, "verify_store", forbidden)
        assert publication.review_publication(source, request) == expected
    selected = next(
        file
        for member in source.members
        for file in member.files
        if file.path == "model.bin"
    )
    (private.store / object_path(selected.sha256)).write_bytes(b"changed source")
    with pytest.raises(PreservationError):
        publish_public(
            private.store,
            private.identity,
            private.trust,
            request,
            tmp_path / "refused",
            (Ed25519PrivateKey.generate(),),
            expected_manifest_id=digest(encode_record(expected)),
        )
    assert not (tmp_path / "refused").exists()


def test_owner_notice_enables_publication_without_inventing_upstream_evidence(
    capture: Capture, tmp_path: Path
) -> None:
    import hashlib
    import json

    from foxlight_model_preservation.publication import (
        PUBLISHER_NOTICE_PATH,
        PublisherNotice,
    )
    from foxlight_model_preservation.storage import object_path

    body = capture.recipe.model_dump(mode="json")
    for member in body["members"]:
        member["retained_card"] = None
        member["intake"].update(
            audience="private",
            license_id="unresolved-owner-capture",
            notice_paths=[".foxlight-capture-notice.txt"],
        )
        member["files"] = [
            {"path": "model.bin"},
            {"path": ".foxlight-capture-notice.txt"},
        ]
        (
            capture.sources[member["member_id"]] / ".foxlight-capture-notice.txt"
        ).write_text("Private capture bookkeeping, not a public license.")
    private_recipe = Recipe.model_validate_json(json.dumps(body))
    store = tmp_path / "private-without-source-notices"
    source_id = pack(private_recipe, capture.sources, store, (capture.key,))
    before = {
        p.relative_to(store): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in store.rglob("*")
        if p.is_file()
    }
    notice = "Synthetic publisher-supplied test-only license and attribution.\n"
    request = PublicationRequest(
        name="Public synthetic model",
        workloads=("test-only",),
        members=tuple(
            PublicMemberTerms(
                member_id=m.member_id,
                license_id="test-only",
                terms_reference="Publisher-supplied synthetic notice",
                decision="Synthetic public choice; no upstream authorship claimed",
                notice_paths=(PUBLISHER_NOTICE_PATH,),
            )
            for m in private_recipe.members
        ),
        notices=tuple(
            PublisherNotice(member_id=m.member_id, text=notice)
            for m in private_recipe.members
        ),
    )
    planned = plan_publication(store, source_id, capture.trust, request)
    identity = digest(encode_record(planned))
    key = Ed25519PrivateKey.generate()
    trust = TrustProfile(
        schema_version="foxlight.model-preservation.trust.v1",
        keys=(public_record(key),),
        threshold=1,
        as_of="independently selected publisher",
    )
    public = tmp_path / "public-added-notices"
    assert (
        publish_public(
            store,
            source_id,
            capture.trust,
            request,
            public,
            (key,),
            expected_manifest_id=identity,
        )
        == identity
    )
    assert verify_store(public, identity, trust) == planned
    assert (
        public / object_path(digest(notice.encode()))
    ).read_bytes() == notice.encode()
    assert planned.attestation == "owner-capture"
    assert all(
        m.retained_card is None and m.intake.notice_paths == (PUBLISHER_NOTICE_PATH,)
        for m in planned.members
    )
    assert all(m.origin_revision == "a" * 40 for m in planned.members)
    assert not any(
        f.path == ".foxlight-capture-notice.txt"
        for m in planned.members
        for f in m.files
    )
    assert before == {
        p.relative_to(store): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in store.rglob("*")
        if p.is_file()
    }
    changed = request.model_copy(
        update={
            "notices": (
                PublisherNotice(member_id="weights", text=notice + "changed"),
                *request.notices[1:],
            )
        }
    )
    with pytest.raises(PreservationError, match="reviewed plan"):
        publish_public(
            store,
            source_id,
            capture.trust,
            changed,
            tmp_path / "changed-notice",
            (key,),
            expected_manifest_id=identity,
        )
    assert not (tmp_path / "changed-notice").exists()


def test_added_notice_cannot_displace_original_notices_or_target_another_member(
    capture: Capture, tmp_path: Path
) -> None:
    from foxlight_model_preservation.publication import (
        PUBLISHER_NOTICE_PATH,
        PublisherNotice,
    )

    private = private_capture(capture, tmp_path)
    request = request_for(private)
    bad = request.model_copy(
        update={
            "members": tuple(
                term.model_copy(update={"notice_paths": (PUBLISHER_NOTICE_PATH,)})
                for term in request.members
            ),
            "notices": tuple(
                PublisherNotice(member_id=t.member_id, text="Synthetic extra notice")
                for t in request.members
            ),
        }
    )
    with pytest.raises(PreservationError, match="original notices"):
        plan_publication(private.store, private.identity, private.trust, bad)
    for notices in [
        (PublisherNotice(member_id="missing", text="notice"),),
        (
            PublisherNotice(member_id="weights", text="one"),
            PublisherNotice(member_id="weights", text="two"),
        ),
        (PublisherNotice(member_id="weights", text=" \n"),),
        (PublisherNotice(member_id="weights", text="binary\x00text"),),
    ]:
        with pytest.raises(PreservationError):
            plan_publication(
                private.store,
                private.identity,
                private.trust,
                request.model_copy(update={"notices": notices}),
            )


@pytest.mark.parametrize(
    "source_path",
    ["NOTICE.publisher.txt", "notice.PUBLISHER.txt", "NOTICE.publisher.txt/child"],
)
def test_supplied_notice_cannot_alias_an_original_file(
    capture: Capture, tmp_path: Path, source_path: str
) -> None:
    from foxlight_model_preservation.models import FileIdentity, Manifest
    from foxlight_model_preservation.publication import (
        PublisherNotice,
        review_publication,
    )

    private = private_capture(capture, tmp_path)
    source = verify_store(private.store, private.identity, private.trust)
    first = source.members[0]
    changed = first.model_copy(
        update={
            "files": (
                *first.files,
                FileIdentity(path=source_path, size=5, sha256=digest(b"prior")),
            )
        }
    )
    source = Manifest.model_validate(
        {
            **source.model_dump(exclude={"members"}),
            "members": (changed, *source.members[1:]),
        }
    )
    request = request_for(private)
    request = request.model_copy(
        update={
            "members": tuple(
                t.model_copy(
                    update={"notice_paths": (*t.notice_paths, "NOTICE.publisher.txt")}
                )
                if t.member_id == first.member_id
                else t
                for t in request.members
            ),
            "notices": (PublisherNotice(member_id=first.member_id, text="New notice"),),
        }
    )
    with pytest.raises(ValueError):
        review_publication(source, request)


def test_utf8_notice_allowance_is_aggregate_bytes_not_characters(
    capture: Capture, tmp_path: Path
) -> None:
    from foxlight_model_preservation.publication import (
        PublisherNotice,
        review_publication,
    )

    private = private_capture(capture, tmp_path)
    source = verify_store(private.store, private.identity, private.trust)
    request = request_for(private).model_copy(
        update={
            "notices": tuple(
                PublisherNotice(member_id=f"member{i}", text="\U0001f98a" * 65536)
                for i in range(5)
            )
        }
    )
    with pytest.raises(PreservationError, match="notices exceed metadata allowance"):
        review_publication(source, request)
