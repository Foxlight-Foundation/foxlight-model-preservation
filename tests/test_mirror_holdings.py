"""Signed holdings: what a mirror keeps, searchable on the device."""

from __future__ import annotations

import random
import shutil
import threading
import time
import urllib.request
from pathlib import Path

import pytest
from conftest import Capture
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from foxlight_model_preservation.mirror_cards import build_peers, issue_card
from foxlight_model_preservation.mirror_directory import MirrorDirectory
from foxlight_model_preservation.mirror_holdings import (
    HOLDINGS_PATH,
    MAX_HOLDINGS_BYTES,
    Holding,
    issue_holdings,
    store_holdings,
    verify_holdings,
)
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.seed import SeedServer
from foxlight_model_preservation.signing import digest, public_record
from foxlight_model_preservation.transfer import TransferPolicy

LAB = TransferPolicy(allow_http=True, allow_private=True, timeout=3)


def _entry(index: int, name: str = "model") -> Holding:
    return Holding(
        manifest=digest(f"{name} {index}".encode()),
        name=f"{name} {index}",
        origin_repository="example/model",
        origin_revision="a" * 40,
        format="gguf",
        size_bytes=1000,
        file_count=2,
        licence="apache-2.0",
        workloads=("chat",),
    )


def test_holdings_are_self_signed_short_lived_and_bounded() -> None:
    key = Ed25519PrivateKey.generate()
    document = issue_holdings(key, [_entry(1), _entry(1), _entry(2)])
    held = verify_holdings(document, public_record(key))
    assert sorted(entry.name for entry in held.entries) == ["model 1", "model 2"]
    with pytest.raises(PreservationError, match="another mirror key"):
        verify_holdings(document, public_record(Ed25519PrivateKey.generate()))
    expired = issue_holdings(key, [_entry(1)], lifetime=60, now=int(time.time()) - 3600)
    with pytest.raises(PreservationError, match="expired"):
        verify_holdings(expired, public_record(key))
    # A list larger than the budget drops entries rather than fail.
    many = issue_holdings(key, [_entry(index) for index in range(5000)])
    assert len(many) <= MAX_HOLDINGS_BYTES
    assert 0 < len(verify_holdings(many, public_record(key)).entries) <= 2000


def test_a_store_is_described_from_its_own_manifests(capture: Capture) -> None:
    entries = store_holdings(capture.store, [capture.identity, digest(b"absent")])
    assert [entry.manifest for entry in entries] == [capture.identity]
    assert entries[0].size_bytes > 0 and entries[0].file_count >= 1


def _serve(store: Path, capture: Capture, documents: dict[str, Path]):
    server = SeedServer(("127.0.0.1", 0), store, (capture.identity,), capture.trust)
    server.documents = documents
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}"


def test_a_crawl_keeps_holdings_signed_by_the_card_key_and_searches_them(
    tmp_path: Path, capture: Capture
) -> None:
    store = tmp_path / "served"
    shutil.copytree(capture.store, store)
    key = Ed25519PrivateKey.generate()
    peers, holdings = tmp_path / "peers.json", tmp_path / "holdings.json"
    server, thread, address = _serve(
        store,
        capture,
        {"/.well-known/sovereign/peers.json": peers, "/" + HOLDINGS_PATH: holdings},
    )
    try:
        peers.write_bytes(
            build_peers(issue_card(key, (address,), {capture.identity}), ())
        )
        held = store_holdings(store, [capture.identity])
        name = held[0].name
        # A list signed by some other key is never kept.
        holdings.write_bytes(issue_holdings(Ed25519PrivateKey.generate(), held))
        client = MirrorDirectory(tmp_path / "client", rng=random.Random(1))  # noqa: S311
        client.crawl((address,), LAB, expand=False)
        assert client.held_models(policy=LAB) == []
        holdings.write_bytes(issue_holdings(key, held))
        client.crawl((address,), LAB, expand=False)
        found = client.held_models(name.split()[0], policy=LAB)
        assert [row["manifest"] for row in found] == [capture.identity]
        assert found[0]["mirrors"] == [address] and found[0]["mirror_count"] == 1
        assert client.held_models("no such model", policy=LAB) == []
        assert client.holders(capture.identity, policy=LAB) == [address]
        # The default HTTPS-only policy never offers a plain-HTTP mirror.
        assert client.held_models(policy=TransferPolicy()) == []
        # The served document is the one the mirror signed.
        with urllib.request.urlopen(address + "/" + HOLDINGS_PATH, timeout=5) as reply:  # noqa: S310
            assert verify_holdings(reply.read(), public_record(key)).entries == tuple(
                held
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_one_copy_kept_by_two_mirrors_is_one_result(
    tmp_path: Path, capture: Capture
) -> None:
    from foxlight_model_preservation.mirror_directory import _objects_name  # noqa: PLC2701
    from foxlight_model_preservation.storage import atomic_bytes, directory

    client = MirrorDirectory(tmp_path / "client", rng=random.Random(3))  # noqa: S311
    keys = [Ed25519PrivateKey.generate() for _ in range(2)]
    addresses = ["https://one.example", "https://two.example"]
    cards = [
        issue_card(key, (url,), {capture.identity})
        for key, url in zip(keys, addresses, strict=True)
    ]
    client.ingest(cards)
    held = [_entry(7, "shared")]
    with directory(tmp_path / "client" / "holdings", create=True) as root:
        for key in keys:
            atomic_bytes(
                root,
                _objects_name(public_record(key).key_id),
                issue_holdings(key, held),
            )
    rows = client.held_models("shared")
    assert len(rows) == 1 and rows[0]["mirror_count"] == 2
    assert sorted(rows[0]["mirrors"]) == addresses


def test_a_held_copy_is_found_and_saved_from_mirrors(
    tmp_path: Path, capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hugging Face is gone; a mirror's holdings make the copy findable and savable."""
    import uuid

    from test_catalog_mirror_fallback import _origin_down, _seeds

    from foxlight_model_preservation import hub
    from foxlight_model_preservation.local_host.owner import LocalOwner
    from foxlight_model_preservation.mirror_objects import (
        OBJECTS_PATH,
        issue_objects,
        served_hashes,
    )

    store = tmp_path / "served"
    shutil.copytree(capture.store, store)
    key = Ed25519PrivateKey.generate()
    peers, holdings, objects = (
        tmp_path / "peers.json",
        tmp_path / "holdings.json",
        tmp_path / "objects.json",
    )
    server, thread, address = _serve(
        store,
        capture,
        {
            "/.well-known/sovereign/peers.json": peers,
            "/" + HOLDINGS_PATH: holdings,
            "/" + OBJECTS_PATH: objects,
        },
    )
    try:
        peers.write_bytes(
            build_peers(issue_card(key, (address,), {capture.identity}), ())
        )
        held = store_holdings(store, [capture.identity])
        holdings.write_bytes(issue_holdings(key, held))
        objects.write_bytes(issue_objects(key, served_hashes(store)))
        # Hugging Face no longer has anything: origin downloads and Hub
        # metadata both fail, so the bytes can only come from mirrors.
        _origin_down(monkeypatch)

        def gone(*_a: object, **_k: object) -> object:
            raise PreservationError("hub_not_found")

        monkeypatch.setattr(hub, "snapshot", gone)
        _seeds(monkeypatch, store)
        monkeypatch.setattr(LocalOwner, "_held_policy", LAB)
        owner = LocalOwner(tmp_path / "owner")
        try:
            owner.community.directory.crawl((address,), LAB, expand=False)
            found = owner.control("held-search", {"query": held[0].name.split()[0]})
            assert [row["manifest"] for row in found["results"]] == [capture.identity]
            plan = owner.control(
                "plan",
                {"kind": "receive", "origin": "mirror", "manifest": capture.identity},
            )
            assert plan["origin"] == "mirror"
            assert plan["holders"] == [address]  # asked first for every file
            assert plan["details"]["origin_check"] == "unconfirmed"
            assert plan["file_count"] == held[0].file_count
            start = {
                "plan_id": plan["plan_id"],
                "operation_id": str(uuid.uuid4()),
                "confirmed": True,
                **(
                    {"accepted_terms": plan["terms"]["accept"]}
                    if plan["terms"]["accept"]
                    else {}
                ),
            }
            # Nothing independent vouches for these bytes: an explicit
            # acknowledgement is required before anything downloads.
            with pytest.raises(
                PreservationError, match="held_origin_acknowledgement_required"
            ):
                owner.control("start", start)
            assert owner.read()["jobs"] == []
            receipt = owner.control(
                "start", {**start, "accept_unconfirmed_origin": True}
            )
            end = time.monotonic() + 60
            while time.monotonic() < end:
                job = next(
                    j for j in owner.read()["jobs"] if j["job_id"] == receipt["job_id"]
                )
                if job["state"] not in {"queued", "running"}:
                    break
                time.sleep(0.05)
            assert job["state"] == "succeeded", job
            assert job["sources"]["origin_files"] == 0
            assert job["sources"]["mirror_files"] >= 1
            assert len(owner.read()["copies"]) == 1
        finally:
            owner.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_a_held_pmf_copy_keeps_its_original_documents(capture: Capture) -> None:
    from foxlight_model_preservation.local_host.selection import (
        HELD_DOCUMENTS,
        held_recipe,
    )
    from foxlight_model_preservation.pmf_models import (
        PMFDocument,
        PMFManifest,
        PMFMember,
        PMFOrigin,
    )
    from foxlight_model_preservation.storage import verify_store

    original = verify_store(capture.store, capture.identity, capture.trust)
    card = b'{"card": "original"}'
    manifest = PMFManifest(
        schema_version="pmf.manifest.v0.1",
        name="Held model",
        root_member=original.root_member,
        members=tuple(
            PMFMember(
                member_id=item.member_id,
                role=item.role,
                format=item.format,
                origin=PMFOrigin(
                    repository=item.origin_repository,
                    revision=item.origin_revision,
                    revision_scheme="git-sha1",
                ),
                dependencies=item.dependencies,
                files=item.files,
                intake=item.intake,
            )
            for item in original.members
        ),
        documents=(
            PMFDocument(
                document_id="original-card",
                member_id=original.root_member,
                role="source-card",
                media_type="application/json",
                sha256=digest(card),
                size=len(card),
            ),
        ),
    )
    recipe, files = held_recipe(manifest, "Kept from community mirrors.")
    root = next(m for m in recipe.members if m.member_id == recipe.root_member)
    path = HELD_DOCUMENTS + "original-card"
    assert any(f.path == path and f.expected_sha256 == digest(card) for f in root.files)
    assert files[(root.origin_repository, root.origin_revision, path)].object_id == (
        "sha256:" + digest(card)
    )


def test_an_origin_matches_only_when_every_member_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from foxlight_model_preservation import hub
    from foxlight_model_preservation.local_host.owner import LocalOwner
    from foxlight_model_preservation.models import FileIdentity
    from foxlight_model_preservation.pmf_models import PMFManifest, PMFMember, PMFOrigin

    def member(member_id: str, repo: str, data: bytes, deps: tuple[str, ...] = ()):
        from foxlight_model_preservation.models import Intake

        return PMFMember(
            member_id=member_id,
            role="base" if member_id == "base" else "companion",
            format="gguf",
            dependencies=deps,
            origin=PMFOrigin(
                repository=repo, revision="a" * 40, revision_scheme="git-sha1"
            ),
            files=(FileIdentity(path="w.gguf", size=len(data), sha256=digest(data)),),
            intake=Intake(
                audience="private",
                license_id="apache-2.0",
                terms_reference="https://example.invalid",
                decision="test",
                notice_paths=("w.gguf",),
            ),
        )

    good, hub_has = b"base weights", b"projector weights"
    manifest = PMFManifest(
        schema_version="pmf.manifest.v0.1",
        name="Two members",
        root_member="base",
        members=(
            member("base", "org/base", good, ("proj",)),
            member("proj", "org/proj", b"tampered"),
        ),
    )
    hub_bytes = {"org/base": good, "org/proj": hub_has}

    def snapshot(repository: str, revision: str, **_k: object) -> hub.HubSnapshot:
        data = hub_bytes[repository]
        return hub.HubSnapshot(
            repository,
            revision,
            (hub.HubFile("w.gguf", len(data), digest(data), None),),
            None,
            False,
            None,
            None,
        )

    monkeypatch.setattr(hub, "snapshot", snapshot)
    owner = LocalOwner(tmp_path / "owner")
    try:
        assert owner._origin_check(manifest) == "differs"  # noqa: SLF001
        hub_bytes["org/proj"] = b"tampered"
        assert owner._origin_check(manifest) == "matches"  # noqa: SLF001
    finally:
        owner.close()


def test_documents_of_a_copy_repacked_before_do_not_collide(capture: Capture) -> None:
    from foxlight_model_preservation.local_host.selection import (
        HELD_DOCUMENTS,
        held_recipe,
    )
    from foxlight_model_preservation.models import FileIdentity
    from foxlight_model_preservation.pmf_models import (
        PMFDocument,
        PMFManifest,
        PMFMember,
        PMFOrigin,
    )
    from foxlight_model_preservation.storage import verify_store

    original = verify_store(capture.store, capture.identity, capture.trust)
    earlier, card = b"earlier generation", b"this generation"
    manifest = PMFManifest(
        schema_version="pmf.manifest.v0.1",
        name="Held twice",
        root_member=original.root_member,
        members=tuple(
            PMFMember(
                member_id=item.member_id,
                role=item.role,
                format=item.format,
                origin=PMFOrigin(
                    repository=item.origin_repository,
                    revision=item.origin_revision,
                    revision_scheme="git-sha1",
                ),
                dependencies=item.dependencies,
                files=(
                    *item.files,
                    *(
                        (
                            FileIdentity(
                                path=HELD_DOCUMENTS + "card",
                                size=len(earlier),
                                sha256=digest(earlier),
                            ),
                        )
                        if item.member_id == original.root_member
                        else ()
                    ),
                ),
                intake=item.intake,
            )
            for item in original.members
        ),
        documents=(
            PMFDocument(
                document_id="card",
                member_id=original.root_member,
                role="source-card",
                media_type="application/json",
                sha256=digest(card),
                size=len(card),
            ),
        ),
    )
    recipe, _ = held_recipe(manifest, "Kept from community mirrors.")
    root = next(m for m in recipe.members if m.member_id == recipe.root_member)
    paths = {f.path: f.expected_sha256 for f in root.files}
    assert paths[HELD_DOCUMENTS + "card"] == digest(earlier)
    assert paths[HELD_DOCUMENTS + "2/card"] == digest(card)
