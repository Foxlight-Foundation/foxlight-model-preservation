"""Independent model trust, bounded I2P-only routing and explicit effect grants."""

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_cli_catalog import command

from foxlight_model_preservation.i2p import (
    authenticate_offer,
    check_metainfo,
    export_i2p,
    read_offer,
    transfer_i2p,
)
from foxlight_model_preservation.i2p_runtime import SnarkRPC
from foxlight_model_preservation.kits import import_kit, inspect_kit
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationCancelled, OperationControl
from foxlight_model_preservation.pmf_preservation import import_preservation
from foxlight_model_preservation.signing import digest, encode_record, public_record
from foxlight_model_preservation.storage import manifest_path, verify_store
from foxlight_model_preservation.torrent_binding import torrent_engine

pytest.importorskip("libtorrent", reason="optional bounded metainfo parser")


@pytest.fixture
def offered(capture, tmp_path):
    target = tmp_path / "i2p-export"
    offer = export_i2p(capture.store, capture.identity, capture.trust, target)
    return offer, target


def test_offline_public_export_has_complete_original_kit_without_network(
    capture, offered, tmp_path
):
    offer, exported = offered
    assert read_offer((exported / "offer.json").read_bytes()) == offer
    assert (
        base64.b64decode(offer.envelope_base64)
        == (capture.store / manifest_path(capture.identity)).read_bytes()
    )
    assert authenticate_offer(offer, capture.trust, capture.identity) == verify_store(
        capture.store, capture.identity, capture.trust
    )
    kit = exported / offer.kit.path
    assert inspect_kit(kit).manifest_id == capture.identity
    assert digest(kit.read_bytes()) == offer.kit.sha256
    assert kit.stat().st_size == offer.kit.size
    assert check_metainfo(offer) == (exported / "model.torrent").read_bytes()
    imported = tmp_path / "offline-recovery"
    import_kit(
        kit,
        imported,
        trust=capture.trust,
        expected_manifest_id=capture.identity,
        expected_envelope=base64.b64decode(offer.envelope_base64),
    )
    assert verify_store(imported, capture.identity, capture.trust)


def test_wrong_signer_or_identity_precedes_metainfo_and_native_effects(
    capture, offered, monkeypatch
):
    import foxlight_model_preservation.i2p as adapter

    def forbidden(*args, **kwargs):
        raise AssertionError("native parser invoked before authenticating")

    monkeypatch.setattr(adapter, "check_metainfo", forbidden)
    wrong = capture.trust.model_copy(
        update={"keys": (public_record(Ed25519PrivateKey.generate()),)}
    )
    for trust, identity in ((wrong, capture.identity), (capture.trust, "0" * 64)):
        with pytest.raises(ValueError):
            authenticate_offer(offered[0], trust, identity)


@pytest.mark.parametrize("i2p,sharing", [(False, False), (True, False), (False, True)])
def test_receiving_never_implies_upload_permission(
    capture, offered, tmp_path, monkeypatch, i2p, sharing
):
    import foxlight_model_preservation.i2p as adapter

    def forbidden(*args, **kwargs):
        raise AssertionError("ungranted native session")

    monkeypatch.setattr(adapter, "owned_snark", forbidden)
    profile = tmp_path / "uncreated-profile"
    with pytest.raises(PreservationError, match="separate explicit grants"):
        transfer_i2p(
            offered[0],
            profile,
            capture.trust,
            capture.identity,
            allow_i2p=i2p,
            allow_sharing=sharing,
            destination=tmp_path / "refused",
        )
    assert not profile.exists()


@pytest.mark.parametrize(
    "change",
    [
        "public-tracker",
        "credential",
        "query",
        "web-seed",
        "httpseed",
        "peer-address",
        "path-alias",
        "multifile",
        "wrong-size",
        "hybrid",
        "wrong-infohash",
        "trailing",
        "list",
        "private",
    ],
)
def test_routing_and_path_confusion_fail_closed(offered, change):
    offer = offered[0]
    engine = torrent_engine()
    decoded = engine.bdecode(base64.b64decode(offer.metainfo_base64))
    if change == "public-tracker":
        decoded[b"announce"] = b"https://public.invalid/announce"
    elif change == "credential":
        decoded[b"announce"] = (
            "http://user:pass@" + "a" * 52 + ".b32.i2p/announce"
        ).encode()
    elif change == "query":
        decoded[b"announce"] = (
            "http://" + "a" * 52 + ".b32.i2p/?token=secret"
        ).encode()
    elif change == "web-seed":
        decoded[b"url-list"] = [b"http://public.invalid/model"]
    elif change == "httpseed":
        decoded[b"httpseeds"] = [b"http://public.invalid/model"]
    elif change == "peer-address":
        decoded[b"nodes"] = [[b"127.0.0.1", 6881]]
    elif change == "path-alias":
        decoded[b"info"][b"name.utf-8"] = b"../../escape"
    elif change == "multifile":
        decoded[b"info"][b"files"] = []
    elif change == "wrong-size":
        decoded[b"info"][b"length"] += 1
    elif change == "hybrid":
        decoded[b"info"][b"meta version"] = 2
    elif change == "list":
        decoded = []
    elif change == "private":
        decoded[b"info"][b"private"] = 1
    elif change == "wrong-infohash":
        offer = offer.model_copy(update={"infohash": "0" * 40})
    metainfo = engine.bencode(decoded) + (b"garbage" if change == "trailing" else b"")
    changed = offer.model_copy(
        update={"metainfo_base64": base64.b64encode(metainfo).decode()}
    )
    with pytest.raises(ValueError):
        check_metainfo(changed)


def test_kit_requires_exact_original_envelope_before_publishing(
    capture, offered, tmp_path
):
    offer, exported = offered
    same_manifest_different_wrapper = json.dumps(
        json.loads(base64.b64decode(offer.envelope_base64)), indent=2
    ).encode()
    with pytest.raises(PreservationError, match="original envelope"):
        import_kit(
            exported / offer.kit.path,
            tmp_path / "refused",
            trust=capture.trust,
            expected_manifest_id=capture.identity,
            expected_envelope=same_manifest_different_wrapper,
        )
    assert not (tmp_path / "refused").exists()


def test_pmf_cards_and_original_signatures_survive_i2p_kit(capture, tmp_path):
    conversion = import_preservation(
        capture.store, capture.identity, capture.trust, tmp_path / "pmf", (capture.key,)
    )
    offer = export_i2p(
        tmp_path / "pmf",
        conversion.output_identity,
        capture.trust,
        tmp_path / "pmf-offer",
    )
    original = (
        tmp_path / "pmf" / manifest_path(conversion.output_identity)
    ).read_bytes()
    imported = tmp_path / "pmf-imported"
    info = import_kit(
        tmp_path / "pmf-offer" / offer.kit.path,
        imported,
        trust=capture.trust,
        expected_manifest_id=conversion.output_identity,
        expected_envelope=original,
    )
    assert len(info.manifest.members) == 2
    assert info.manifest.documents
    assert (
        imported / manifest_path(conversion.output_identity)
    ).read_bytes() == original


def test_cancelled_export_never_creates_completed_offer(capture, tmp_path):
    control = OperationControl()
    control.cancel()
    with pytest.raises(OperationCancelled):
        export_i2p(
            capture.store,
            capture.identity,
            capture.trust,
            tmp_path / "refused",
            control=control,
        )
    assert not (tmp_path / "refused").exists()


def test_rpc_refuses_global_and_deleting_methods_without_connecting():
    client = SnarkRPC(1, "a" * 64, OperationControl())
    for method in (
        "torrent-remove",
        "torrent-stop-all",
        "torrent-start-all",
        "session-set",
        "torrent-set",
    ):
        with pytest.raises(PreservationError, match="unsupported"):
            client.call(method)


def test_cli_exports_and_inspects_an_untrusted_route_without_joining(capture, tmp_path):
    trust = tmp_path / "trust.json"
    trust.write_bytes(encode_record(capture.trust))
    target = tmp_path / "cli-offer"
    result = command(
        "i2p",
        "export",
        capture.identity,
        "--store",
        str(capture.store),
        "--trust",
        str(trust),
        "--output",
        str(target),
    )
    assert result.returncode == 0, result.stderr
    assert not json.loads(result.stdout)["network_started"]
    inspected = command("i2p", "inspect", "--offer", str(target / "offer.json"))
    assert inspected.returncode == 0, inspected.stderr
    assert json.loads(inspected.stdout)["authority"] == "untrusted-routing"


@pytest.mark.parametrize("kind", ["bytes", "existing", "lifetime"])
def test_refused_limits_never_start_native(
    capture, offered, tmp_path, monkeypatch, kind
):
    import foxlight_model_preservation.i2p as adapter

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid request reached native software")

    monkeypatch.setattr(adapter, "owned_snark", forbidden)
    profile = tmp_path / "profile"
    profile.mkdir()
    destination = tmp_path / "destination"
    if kind == "existing":
        destination.mkdir()
    kwargs = {"max_bytes": 1} if kind == "bytes" else {}
    if kind == "lifetime":
        kwargs["seconds"] = 0
    with pytest.raises(ValueError):
        transfer_i2p(
            offered[0],
            profile,
            capture.trust,
            capture.identity,
            allow_i2p=True,
            allow_sharing=True,
            destination=destination,
            **kwargs,
        )


def test_one_file_handoff_keeps_authority_and_does_not_start_a_session(
    capture, offered, tmp_path, monkeypatch
):
    import foxlight_model_preservation.i2p_cli as cli
    from foxlight_model_preservation.cli import Arguments, argument_parser
    from foxlight_model_preservation.i2p_cli import run_i2p_command
    from foxlight_model_preservation.i2p_models import I2PStatus

    monkeypatch.setattr(
        cli,
        "native_status",
        lambda _profile: I2PStatus(
            rpc_port=12345,
            pid=123,
            connected=True,
            dht_nodes=1,
            destination="A" * 516,
            dht_port=6881,
        ),
    )
    trust = tmp_path / "trust.json"
    trust.write_bytes(encode_record(capture.trust))
    output = tmp_path / "model.i2p-offer"
    arguments = argument_parser().parse_args(
        [
            "i2p",
            "handoff",
            "--dir",
            str(tmp_path / "profile"),
            "--offer",
            str(offered[1] / "offer.json"),
            "--trust",
            str(trust),
            "--expected-manifest-id",
            capture.identity,
            "--output",
            str(output),
        ],
        namespace=Arguments(),
    )
    receipt = run_i2p_command(arguments, OperationControl())
    assert not receipt["network_started"]
    result = read_offer(output.read_bytes())
    assert result.envelope_base64 == offered[0].envelope_base64
    assert result.kit == offered[0].kit
    assert result.infohash == offered[0].infohash
    assert result.contacts[0].destination == "A" * 516
    with pytest.raises(FileExistsError):
        run_i2p_command(arguments, OperationControl())


def test_offer_schema_is_packaged_exactly():
    from importlib.resources import files

    from foxlight_model_preservation.i2p_models import I2POffer

    assert (
        json.loads(
            files("foxlight_model_preservation")
            .joinpath("resources/i2p_offer_v1.json")
            .read_bytes()
        )
        == I2POffer.model_json_schema()
    )


def test_registry_evidence_keeps_separate_root_after_i2p_kit(capture, tmp_path):
    from test_cards_proofs import NOW, Repository, selected_member

    from foxlight_model_preservation.assurance import inspect_member_evidence
    from foxlight_model_preservation.models import Recipe
    from foxlight_model_preservation.storage import pack

    content = b"x" * 20
    member, registry = selected_member(
        object_id="sha256:" + digest(content), sha256=digest(content), registry=True
    )
    source = tmp_path / "model"
    source.mkdir()
    (source / "model.bin").write_bytes(content)
    recipe_member = member.model_dump(mode="json")
    recipe_member["files"] = [{"path": "model.bin"}]
    recipe_member["intake"]["audience"] = "public"
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
    original = tmp_path / "original"
    identity = pack(recipe, {"primary": source}, original, (capture.key,))
    offer = export_i2p(original, identity, capture.trust, tmp_path / "offered")
    target = tmp_path / "imported"
    imported = import_kit(
        tmp_path / "offered" / offer.kit.path,
        target,
        trust=capture.trust,
        expected_manifest_id=identity,
        expected_envelope=base64.b64decode(offer.envelope_base64),
    ).manifest.members[0]
    assert imported.retained_card == member.retained_card
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
    assert report.qualification == "not-assessed"
