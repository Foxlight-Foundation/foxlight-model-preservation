"""Synthetic original-repository BitTorrent pilot and adversarial offline intake."""

import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_cards_proofs import NOW, Repository, selected_member
from test_cli_catalog import command

from foxlight_model_preservation.archives import verify_archive
from foxlight_model_preservation.assurance import inspect_member_evidence
from foxlight_model_preservation.models import PreservationError, Recipe
from foxlight_model_preservation.operations import OperationCancelled, OperationControl
from foxlight_model_preservation.pirateface import PirateFaceCandidate
from foxlight_model_preservation.pirateface_import import import_candidate
from foxlight_model_preservation.pmf import restore_pmf
from foxlight_model_preservation.pmf_preservation import (
    export_preservation,
    import_preservation,
)
from foxlight_model_preservation.signing import digest, encode_record, public_record
from foxlight_model_preservation.storage import (
    manifest_path,
    object_path,
    pack,
    restore,
)
from foxlight_model_preservation.torrent_binding import torrent_engine

pytest.importorskip("libtorrent", reason="optional native BitTorrent extra")


def repo_torrent(root):
    engine = torrent_engine()
    files = engine.file_storage()
    for name in ("LICENSE", "model.bin"):
        files.add_file(root.name + "/" + name, (root / name).stat().st_size)
    creator = engine.create_torrent(files, 16384, engine.create_torrent.v1_only)
    engine.set_piece_hashes(creator, str(root.parent), lambda _piece: None)
    return engine.bencode(creator.generate())


def candidate_for(metainfo, **changes):
    engine = torrent_engine()
    infohash = str(engine.torrent_info(engine.bdecode(metainfo)).info_hashes().v1)
    return PirateFaceCandidate(
        repo_id="test/weights",
        source_base="https://pirateface.co",
        infohash=infohash,
        magnet="magnet:?xt=urn:btih:" + infohash,
        response_sha256=digest(b"synthetic listing"),
        observed_at=1792713600,
        revision_hint="main",
        **changes,
    )


@pytest.fixture
def inputs(capture):
    metainfo = repo_torrent(capture.sources["weights"])
    return (
        candidate_for(metainfo),
        metainfo,
        (capture.store / manifest_path(capture.identity)).read_bytes(),
    )


def intake(capture, inputs, destination, **kwargs):
    candidate, metainfo, envelope = inputs
    return import_candidate(
        candidate,
        metainfo,
        envelope,
        capture.identity,
        capture.trust,
        "weights",
        capture.sources,
        destination,
        **kwargs,
    )


def test_offline_import_preserves_original_complete_archive_without_session(
    capture, inputs, tmp_path, monkeypatch
):
    import libtorrent

    def forbidden(*args, **kwargs):
        raise AssertionError("offline import started a peer session")

    monkeypatch.setattr(libtorrent, "session", forbidden)
    target = tmp_path / "imported"
    manifest = intake(capture, inputs, target)
    assert verify_archive(target, capture.identity, capture.trust) == manifest
    assert (target / manifest_path(capture.identity)).read_bytes() == inputs[2]
    assert {p.name for p in (target / "objects/sha256").iterdir()} == {
        f.sha256 for m in manifest.members for f in m.files
    }
    restored = tmp_path / "restored"
    restore(target, capture.identity, capture.trust, restored)
    for member in manifest.members:
        assert member.retained_card == {"verification_class": "legacy"}
        for file in member.files:
            assert (
                restored / "members" / member.member_id / file.path
            ).read_bytes() == (
                capture.sources[member.member_id] / file.path
            ).read_bytes()
        assert not (
            restored / "members" / member.member_id / "secret-not-selected"
        ).exists()


def test_authentication_precedes_metainfo_and_weight_access(
    capture, inputs, tmp_path, monkeypatch
):
    import foxlight_model_preservation.pirateface_import as adapter

    def forbidden(*args):
        raise AssertionError("metainfo was inspected before authentication")

    monkeypatch.setattr(adapter, "repository_inventory", forbidden)
    trust = capture.trust.model_copy(
        update={"keys": (public_record(Ed25519PrivateKey.generate()),)}
    )
    candidate, metainfo, envelope = inputs
    for expected, enrolled, signed in (
        ("0" * 64, capture.trust, envelope),
        (capture.identity, trust, envelope),
        (capture.identity, capture.trust, b"{}"),
    ):
        with pytest.raises(ValueError):
            import_candidate(
                candidate,
                metainfo,
                signed,
                expected,
                enrolled,
                "weights",
                {"weights": tmp_path / "must-not-read"},
                tmp_path / "refused",
            )
        assert not (tmp_path / "refused").exists()


@pytest.mark.parametrize(
    "change", ["truncated", "changed", "missing", "symlink", "reused-companion"]
)
def test_invalid_local_bytes_never_publish_and_inputs_remain_operator_owned(
    capture, inputs, tmp_path, change
):
    path = capture.sources["weights"] / "model.bin"
    if change == "truncated":
        path.write_bytes(path.read_bytes()[:-1])
    elif change == "changed":
        path.write_bytes(b"x" * path.stat().st_size)
    elif change == "missing":
        path.unlink()
    elif change == "symlink":
        path.unlink()
        path.symlink_to(capture.sources["tokenizer"] / "model.bin")
    else:
        # Deduplicated notices still require verification of each supplied source.
        path = capture.sources["tokenizer"] / "LICENSE"
        path.write_bytes(b"x" * path.stat().st_size)
    with pytest.raises((ValueError, OSError)):
        intake(capture, inputs, tmp_path / "refused")
    assert not (tmp_path / "refused").exists()
    assert all(root.is_dir() for root in capture.sources.values())
    assert not list(tmp_path.glob(".pmf-*"))


def test_missing_companion_budget_cancellation_and_existing_output_refuse(
    capture, inputs, tmp_path
):
    candidate, metainfo, envelope = inputs
    with pytest.raises(PreservationError, match="all members"):
        import_candidate(
            candidate,
            metainfo,
            envelope,
            capture.identity,
            capture.trust,
            "weights",
            {"weights": capture.sources["weights"]},
            tmp_path / "missing",
        )
    with pytest.raises(PreservationError, match="byte allowance"):
        intake(capture, inputs, tmp_path / "small", max_bytes=1)
    target = tmp_path / "existing"
    target.mkdir()
    sentinel = target / "operator-file"
    sentinel.write_bytes(b"keep")
    with pytest.raises(PreservationError, match="new destination"):
        intake(capture, inputs, target)
    assert sentinel.read_bytes() == b"keep"

    def cancel(progress):
        if progress.bytes_copied:
            control.cancel()

    control = OperationControl(cancel, interval_seconds=0)
    with pytest.raises(OperationCancelled):
        intake(capture, inputs, tmp_path / "cancelled", control=control)
    assert not (tmp_path / "cancelled").exists()
    assert not list(tmp_path.glob(".pmf-*"))


@pytest.mark.parametrize(
    "field,value", [("repo_id", "other/weights"), ("revision_hint", "b" * 40)]
)
def test_candidate_cannot_substitute_signed_repository_or_revision(
    capture, inputs, tmp_path, field, value
):
    candidate, metainfo, envelope = inputs
    replaced = candidate.model_copy(update={field: value})
    with pytest.raises(PreservationError, match="signed"):
        intake(capture, (replaced, metainfo, envelope), tmp_path / "refused")


def test_infohash_and_selected_file_inventory_must_match(capture, inputs, tmp_path):
    candidate, metainfo, envelope = inputs
    changed = candidate.model_copy(
        update={"infohash": "0" * 40, "magnet": "magnet:?xt=urn:btih:" + "0" * 40}
    )
    with pytest.raises(PreservationError, match="infohash"):
        intake(capture, (changed, metainfo, envelope), tmp_path / "refused")
    engine = torrent_engine()
    decoded = engine.bdecode(metainfo)
    decoded[b"info"][b"files"][0][b"length"] += 1
    modified = engine.bencode(decoded)
    with pytest.raises(PreservationError, match="cover"):
        intake(
            capture, (candidate_for(modified), modified, envelope), tmp_path / "refused"
        )


@pytest.mark.parametrize(
    "change",
    [
        "traversal",
        "component-slash",
        "symlink",
        "case-alias",
        "overlap",
        "utf8-alias",
        "v2",
        "trailing",
    ],
)
def test_unsafe_metainfo_is_rejected_before_any_copy(capture, inputs, tmp_path, change):
    _candidate, metainfo, envelope = inputs
    engine = torrent_engine()
    decoded = engine.bdecode(metainfo)
    info = decoded[b"info"]
    files = info[b"files"]
    if change == "traversal":
        files[0][b"path"] = [b"..", b"escape"]
    elif change == "component-slash":
        files[0][b"path"] = [b"sub/LICENSE"]
    elif change == "symlink":
        files[0][b"symlink path"] = [b"elsewhere"]
        files[0][b"attr"] = b"l"
    elif change == "case-alias":
        files[1][b"path"] = [b"license"]
    elif change == "overlap":
        files[1][b"path"] = [b"LICENSE", b"child"]
    elif change == "utf8-alias":
        files[0][b"path.utf-8"] = [b"other"]
    elif change == "v2":
        info[b"meta version"] = 2
    changed = engine.bencode(decoded) + (b"garbage" if change == "trailing" else b"")
    # Deliberately keep the original candidate: path rejection must precede copying,
    # even before its eventual mismatched infohash is considered.
    with pytest.raises(ValueError):
        intake(capture, (inputs[0], changed, envelope), tmp_path / "refused")
    assert not (tmp_path / "refused").exists()


def test_pmf_import_retains_all_source_records_and_original_signed_round_trip(
    capture, inputs, tmp_path
):
    converted = tmp_path / "pmf"
    result = import_preservation(
        capture.store, capture.identity, capture.trust, converted, (capture.key,)
    )
    identity = result.output_identity
    materialized = tmp_path / "materialized"
    manifest = restore_pmf(converted, identity, capture.trust, materialized)
    documents = {
        d.document_id: materialized / "documents" / d.document_id
        for d in manifest.documents
    }
    envelope = (converted / manifest_path(identity)).read_bytes()
    imported = tmp_path / "pmf-imported"
    import_candidate(
        inputs[0],
        inputs[1],
        envelope,
        identity,
        capture.trust,
        "weights",
        capture.sources,
        imported,
        documents=documents,
    )
    assert verify_archive(imported, identity, capture.trust) == manifest
    assert (imported / manifest_path(identity)).read_bytes() == envelope
    for document in manifest.documents:
        assert (imported / object_path(document.sha256)).read_bytes() == documents[
            document.document_id
        ].read_bytes()
    recovered = tmp_path / "recovered"
    export_preservation(
        imported,
        identity,
        capture.trust,
        recovered,
        source_trust=capture.trust,
        expected_source_manifest_id=capture.identity,
    )
    assert (recovered / manifest_path(capture.identity)).read_bytes() == inputs[2]


def test_retained_registry_proof_still_requires_its_independent_root(capture, tmp_path):
    content = b"x" * 20
    member, registry = selected_member(
        object_id="sha256:" + digest(content), sha256=digest(content), registry=True
    )
    source = tmp_path / "registry-model"
    source.mkdir()
    (source / "model.bin").write_bytes(content)
    (source / "LICENSE").write_bytes(b"unselected torrent file")
    recipe_member = member.model_dump(mode="json")
    recipe_member["files"] = [{"path": "model.bin"}]
    recipe = Recipe.model_validate_json(
        json.dumps(
            {
                "schema_version": "foxlight.model-preservation.v1",
                "name": "signed proof",
                "root_member": "primary",
                "workloads": ["synthetic"],
                "runtime_requirements": [],
                "members": [recipe_member],
            }
        )
    )
    original = tmp_path / "registry-original"
    identity = pack(recipe, {"primary": source}, original, (capture.key,))
    envelope = (original / manifest_path(identity)).read_bytes()
    metainfo = repo_torrent(source)
    candidate = candidate_for(metainfo).model_copy(
        update={"repo_id": member.origin_repository}
    )
    target = tmp_path / "registry-imported"
    manifest = import_candidate(
        candidate,
        metainfo,
        envelope,
        identity,
        capture.trust,
        "primary",
        {"primary": source},
        target,
    )
    imported = manifest.members[0]
    assert imported.retained_card == member.retained_card
    assert (target / manifest_path(identity)).read_bytes() == envelope
    with pytest.raises(PreservationError, match="enrolled root"):
        inspect_member_evidence(imported, reference_time=NOW)
    with pytest.raises(PreservationError):
        inspect_member_evidence(
            imported, trusted_root=Repository().root.to_bytes(), reference_time=NOW
        )
    report = inspect_member_evidence(
        imported, trusted_root=registry.root.to_bytes(), reference_time=NOW
    )
    assert report.publication == "registry-fresh"
    assert report.file_coverage == "complete-sha256"
    assert report.qualification == "not-assessed"


def test_import_cli_retains_independent_trust_and_starts_no_sharing(
    capture, inputs, tmp_path
):
    candidate, metainfo, envelope = inputs
    paths = {
        "candidate": encode_record(candidate),
        "metainfo": metainfo,
        "envelope": envelope,
        "trust": encode_record(capture.trust),
    }
    arguments = [
        "pirateface",
        "import",
        capture.identity,
        "--candidate-member",
        "weights",
        "--store",
        str(tmp_path / "cli-imported"),
        "--progress",
    ]
    for name, data in paths.items():
        path = tmp_path / (name + ".input")
        path.write_bytes(data)
        arguments.extend(["--" + name, str(path)])
    for name, root in capture.sources.items():
        arguments.extend(["--member", name + "=" + str(root)])
    result = command(*arguments)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["verification"] == "complete"
    assert not receipt["network_started"]
    assert not receipt["sharing_started"]
    assert not receipt["registry_authority_granted"]
    progress = [json.loads(line) for line in result.stderr.splitlines()]
    assert sum(item["stage"] == "complete" for item in progress) == 1


def test_real_standard_repository_torrent_then_independent_signed_import(
    capture, inputs, tmp_path
):
    """Opt in only to isolated synthetic loopback sharing; no public peers/services."""
    engine = torrent_engine()
    settings = {
        "listen_interfaces": "127.0.0.1:0",
        "enable_dht": False,
        "enable_lsd": False,
        "enable_upnp": False,
        "enable_natpmp": False,
        "enable_incoming_utp": False,
        "enable_outgoing_utp": False,
        "alert_mask": 0,
    }
    seeder, receiver = engine.session(settings), engine.session(settings)
    target = tmp_path / "received"
    target.mkdir()
    metainfo = inputs[1]
    seed = seeder.add_torrent(
        {
            "ti": engine.torrent_info(engine.bdecode(metainfo)),
            "save_path": str(capture.sources["weights"].parent),
            "flags": 0,
        }
    )
    receive = receiver.add_torrent(
        {
            "ti": engine.torrent_info(engine.bdecode(metainfo)),
            "save_path": str(target),
            "flags": 0,
        }
    )
    try:
        deadline = time.monotonic() + 20
        while not seed.status().is_seeding and time.monotonic() < deadline:
            time.sleep(0.05)
        assert seed.status().is_seeding
        receive.connect_peer(("127.0.0.1", seeder.listen_port()))
        manifest = None
        while time.monotonic() < deadline:
            if receive.status().is_seeding:
                try:
                    manifest = import_candidate(
                        inputs[0],
                        metainfo,
                        inputs[2],
                        capture.identity,
                        capture.trust,
                        "weights",
                        {**capture.sources, "weights": target / "weights"},
                        tmp_path / "from-standard-torrent",
                    )
                except (FileNotFoundError, PreservationError):
                    pass  # Native completion may precede filesystem flush.
                else:
                    break
            time.sleep(0.05)
        assert manifest is not None, (
            "standard peer did not yield independently verified files"
        )
        assert (
            verify_archive(
                tmp_path / "from-standard-torrent", capture.identity, capture.trust
            )
            == manifest
        )
        assert seed.status().total_payload_upload > 0
    finally:
        receiver.remove_torrent(receive)
        seeder.remove_torrent(seed)
        receiver.pause()
        seeder.pause()
        del receive, seed, receiver, seeder
