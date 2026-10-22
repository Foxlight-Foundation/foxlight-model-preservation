"""PMF common-core independence, exact signed round trips and fail-closed gates."""

import base64
import copy
import json
import shutil
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jsonschema import Draft202012Validator
from test_card_evolution import future_body
from test_cards_proofs import NOW, selected_member
from test_cli_catalog import command

from foxlight_model_preservation.assurance import inspect_member_evidence, retain_card
from foxlight_model_preservation.cards import create_card, identity_json
from foxlight_model_preservation.kits import export_kit, import_kit
from foxlight_model_preservation.models import (
    MAX_METADATA_BYTES,
    PreservationError,
    Recipe,
    TrustProfile,
)
from foxlight_model_preservation.operations import OperationCancelled, OperationControl
from foxlight_model_preservation.pmf import (
    load_pmf,
    pack_pmf,
    restore_pmf,
    sign_pmf_manifest,
    verify_pmf,
    verify_pmf_manifest,
)
from foxlight_model_preservation.pmf_models import (
    PMF_TYPE,
    PMF_VERSION,
    PMFDocument,
    PMFManifest,
    PMFMember,
    PMFOrigin,
    PMFProfile,
)
from foxlight_model_preservation.pmf_preservation import (
    export_preservation,
    import_preservation,
)
from foxlight_model_preservation.signing import (
    canonical_bytes,
    digest,
    encode_record,
    public_record,
    verify_manifest,
)
from foxlight_model_preservation.storage import (
    manifest_path,
    object_path,
    pack,
    restore,
    verify_store,
)


def new_trust(key):
    return TrustProfile(
        schema_version="foxlight.model-preservation.trust.v1",
        keys=(public_record(key),),
        threshold=1,
        as_of=NOW.isoformat(),
    )


def common_manifest(capture):
    """Use exact synthetic files while requiring no repository/card/runtime."""
    original = verify_store(capture.store, capture.identity, capture.trust)
    return PMFManifest(
        schema_version=PMF_VERSION,
        name="Independent model",
        root_member=original.root_member,
        members=tuple(
            PMFMember(
                member_id=item.member_id,
                role=item.role,
                format="synthetic",
                dependencies=item.dependencies,
                files=item.files,
                intake=item.intake,
            )
            for item in original.members
        ),
    )


def test_common_pack_restore_without_origin_or_skulk(capture, tmp_path):
    source_record = b'{"contract":99,"strength":1.0,"future":[null,"\xce\xb1"]}\n'
    source = tmp_path / "foreign-card.json"
    source.write_bytes(source_record)
    record = PMFDocument(
        document_id="foreign-card",
        member_id="weights",
        role="source-card",
        media_type="application/json",
        schema_version="other-project.card.v99",
        sha256=digest(source_record),
        size=len(source_record),
    )
    manifest = common_manifest(capture).model_copy(
        update={
            "documents": (record,),
            "profiles": (
                PMFProfile(
                    profile_id="other-project.card.v99",
                    member_id="weights",
                    documents=(record.document_id,),
                ),
            ),
        }
    )
    store = tmp_path / "pmf"
    identity = pack_pmf(
        manifest, capture.sources, {record.document_id: source}, store, (capture.key,)
    )
    assert verify_pmf(store, identity, capture.trust) == manifest
    assert all(item.origin is None for item in manifest.members)
    shutil.rmtree(capture.store)
    for path in capture.sources.values():
        shutil.rmtree(path)
    source.unlink()
    destination = tmp_path / "restored"
    assert restore_pmf(store, identity, capture.trust, destination) == manifest
    assert (destination / "documents/foreign-card").read_bytes() == source_record
    assert (destination / "members/tokenizer/model.bin").read_bytes() == (
        b"tokenizer\0" * 1000
    )
    assert not list(store.rglob("secret-not-selected"))
    assert not list(store.rglob("*.pem"))
    with pytest.raises(FileExistsError):
        restore_pmf(store, identity, capture.trust, destination)
    with pytest.raises(PreservationError, match="no supported original"):
        export_preservation(
            store,
            identity,
            capture.trust,
            tmp_path / "legacy",
            source_trust=capture.trust,
            expected_source_manifest_id=capture.identity,
        )


def test_common_reader_does_not_import_ecosystem_adapters():
    result = subprocess.run(  # noqa: S603 - fixed interpreter and program
        [
            sys.executable,
            "-c",
            """
import importlib.abc
import sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        forbidden = ('skulk', 'huggingface_hub', 'torch',
            'foxlight_model_preservation.cards',
            'foxlight_model_preservation.assurance',
            'foxlight_model_preservation.registry_proof',
            'foxlight_model_preservation.pmf_preservation')
        if any(fullname == name or fullname.startswith(name + '.')
               for name in forbidden):
            raise AssertionError('common PMF imported ' + fullname)
sys.meta_path.insert(0, Block())
from foxlight_model_preservation.pmf import load_pmf, pack_pmf, verify_pmf, restore_pmf
from foxlight_model_preservation.pmf_models import PMFManifest
assert 'PMFManifest' in str(PMFManifest.model_json_schema())
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_signed_skulk_round_trip_preserves_every_field_and_proof(capture, tmp_path):
    content = b"x" * 20
    member, registry = selected_member(
        object_id="sha256:" + digest(content), sha256=digest(content)
    )
    selected = {
        **registry.card.card,
        **future_body(),
        "future_numeric": {"int": 1, "float": 1.0, "negative_zero": -0.0},
    }
    registry.card = create_card(selected)
    registry.path = f"v1/cards/{registry.card.card_id}.json"
    registry.target = identity_json(registry.card.model_dump(mode="json"))
    registry.refresh()
    retained = retain_card(registry.card, registry_proof=registry.proof())
    # Original card and DSSE wrapper whitespace are deliberately noncanonical.
    card_bytes = json.dumps(
        registry.card.model_dump(mode="json"), ensure_ascii=False, indent=2
    ).encode()
    retained = retained.model_copy(
        update={"card_base64": base64.b64encode(card_bytes).decode()}
    )
    member = member.model_copy(
        update={
            "artifact_identity": registry.card.card_id,
            "retained_card": retained.model_dump(mode="json"),
            "dependencies": ("tokenizer",),
        }
    )
    source = tmp_path / "primary"
    source.mkdir()
    (source / "model.bin").write_bytes(content)
    primary = member.model_dump(mode="json")
    primary["files"] = [{"path": "model.bin"}]
    recipe = Recipe.model_validate_json(
        identity_json(
            {
                "schema_version": "foxlight.model-preservation.v1",
                "name": "Round trip",
                "root_member": "primary",
                "workloads": ["synthetic"],
                "runtime_requirements": [],
                "members": [primary, capture.recipe.members[1].model_dump(mode="json")],
            }
        )
    )
    legacy = tmp_path / "original"
    original_id = pack(
        recipe,
        {"primary": source, "tokenizer": capture.sources["tokenizer"]},
        legacy,
        (capture.key,),
    )
    envelope_file = legacy / manifest_path(original_id)
    envelope_file.write_text(
        json.dumps(json.loads(envelope_file.read_bytes()), indent=2)
    )
    original_envelope = envelope_file.read_bytes()
    before = tmp_path / "before.foxkit"
    export_kit(legacy, original_id, capture.trust, before)
    converter_key = Ed25519PrivateKey.generate()
    converter_trust = new_trust(converter_key)
    pmf = tmp_path / "pmf"
    converted = import_preservation(
        legacy, original_id, capture.trust, pmf, (converter_key,)
    )
    assert converted.source_envelope_sha256 == digest(original_envelope)
    manifest = verify_pmf(pmf, converted.output_identity, converter_trust)
    assert manifest.members[0].dependencies == ("tokenizer",)
    assert any(
        item.profile_id == "skulk.retained-card.v1" for item in manifest.profiles
    )
    # Everything below succeeds after the original model/archive are gone.
    shutil.rmtree(legacy)
    shutil.rmtree(source)
    shutil.rmtree(capture.sources["tokenizer"])
    recovered = tmp_path / "recovered"
    with pytest.raises(PreservationError, match="threshold"):
        export_preservation(
            pmf,
            converted.output_identity,
            converter_trust,
            recovered,
            source_trust=converter_trust,
            expected_source_manifest_id=original_id,
        )
    assert not recovered.exists()
    exported = export_preservation(
        pmf,
        converted.output_identity,
        converter_trust,
        recovered,
        source_trust=capture.trust,
        expected_source_manifest_id=original_id,
    )
    assert exported.output_identity == original_id
    assert (recovered / manifest_path(original_id)).read_bytes() == original_envelope
    restored = restore(recovered, original_id, capture.trust, tmp_path / "skulk-input")
    exact = restored.members[0].retained_card
    assert exact == retained.model_dump(mode="json")
    assert base64.b64decode(exact["card_base64"]) == card_bytes
    assert (
        inspect_member_evidence(
            restored.members[0],
            trusted_root=registry.root.to_bytes(),
            reference_time=NOW,
        ).publication
        == "registry-fresh"
    )
    assert (
        inspect_member_evidence(
            restored.members[0],
            trusted_root=registry.root.to_bytes(),
            reference_time=NOW,
        ).file_coverage
        == "complete-sha256"
    )
    after = tmp_path / "after.foxkit"
    export_kit(recovered, original_id, capture.trust, after)
    assert after.read_bytes() == before.read_bytes()
    import_kit(
        after,
        tmp_path / "kit-roundtrip",
        trust=capture.trust,
        expected_manifest_id=original_id,
    )


@pytest.mark.parametrize(
    "damage", ["object", "document", "signature", "domain", "identity"]
)
def test_tampering_never_restores(capture, tmp_path, damage):
    target = tmp_path / "pmf"
    report = import_preservation(
        capture.store, capture.identity, capture.trust, target, (capture.key,)
    )
    identity = report.output_identity
    manifest = verify_pmf(target, identity, capture.trust)
    if damage in {"object", "document"}:
        file = (
            manifest.members[0].files[0]
            if damage == "object"
            else manifest.documents[0]
        )
        path = target / object_path(file.sha256)
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)
    elif damage == "identity":
        identity = "0" * 64
        (target / manifest_path(identity)).write_bytes(
            (target / manifest_path(report.output_identity)).read_bytes()
        )
    else:
        path = target / manifest_path(identity)
        raw = json.loads(path.read_bytes())
        if damage == "domain":
            raw["payloadType"] = "application/vnd.foxlight.model-preservation.v1+json"
        else:
            raw["signatures"][0]["sig"] = base64.b64encode(bytes(64)).decode()
        path.write_text(json.dumps(raw))
    destination = tmp_path / "refused"
    with pytest.raises(ValueError):
        restore_pmf(target, identity, capture.trust, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob(".pmf-*"))


@pytest.mark.parametrize("change", ["name", "annotations", "files", "profile"])
def test_lossy_or_contradictory_reverse_conversion_refuses(capture, tmp_path, change):
    target = tmp_path / "pmf"
    report = import_preservation(
        capture.store, capture.identity, capture.trust, target, (capture.key,)
    )
    manifest = verify_pmf(target, report.output_identity, capture.trust)
    raw = manifest.model_dump(mode="json")
    if change == "name":
        raw["name"] = "Different native name"
    elif change == "annotations":
        raw["annotations"]["new.source"] = {"must_survive": True}
    elif change == "profile":
        raw["profiles"][0]["profile_id"] = "unknown.source.v1"
    else:
        raw["members"][0]["format"] = "different-format"
    changed = load_pmf(canonical_bytes(raw))
    identity, envelope = sign_pmf_manifest(changed, (capture.key,))
    (target / manifest_path(identity)).write_bytes(envelope)
    assert verify_pmf(target, identity, capture.trust) == changed
    with pytest.raises(PreservationError, match="losslessly|no supported original"):
        export_preservation(
            target,
            identity,
            capture.trust,
            tmp_path / "refused",
            source_trust=capture.trust,
            expected_source_manifest_id=capture.identity,
        )
    assert not (tmp_path / "refused").exists()


@pytest.mark.parametrize(
    "change",
    [
        "missing_companion",
        "cycle",
        "unreachable",
        "duplicate_path",
        "extra_field",
        "version",
        "feature",
        "profile",
        "missing_document",
        "conflicting_size",
    ],
)
def test_schema_and_required_semantics_fail_closed(capture, change):
    raw = common_manifest(capture).model_dump(mode="json")
    if change == "missing_companion":
        raw["members"].pop()
    elif change == "cycle":
        raw["members"][1]["dependencies"] = [raw["root_member"]]
    elif change == "unreachable":
        raw["members"][0]["dependencies"] = []
    elif change == "duplicate_path":
        raw["members"][0]["files"].append(raw["members"][0]["files"][0])
    elif change == "extra_field":
        raw["new_required_interpretation"] = True
    elif change == "version":
        raw["schema_version"] = "pmf.manifest.v99"
    elif change == "feature":
        raw["required_features"] = ["unknown-execution-semantics"]
    else:
        file = raw["members"][0]["files"][0]
        raw["documents"] = [
            {
                "document_id": "record",
                "role": "source",
                "media_type": "application/octet-stream",
                "sha256": file["sha256"],
                "size": file["size"],
            }
        ]
        if change == "conflicting_size":
            raw["documents"][0]["size"] += 1
        else:
            raw["profiles"] = [
                {
                    "profile_id": "future",
                    "documents": [
                        "missing" if change == "missing_document" else "record"
                    ],
                    "required": change == "profile",
                }
            ]
    with pytest.raises(ValueError):
        load_pmf(canonical_bytes(raw))


def test_origin_values_are_honest_and_metadata_bounded(capture):
    assert (
        PMFOrigin(
            repository="local instrument", revision="7", revision_scheme="release-label"
        ).revision
        == "7"
    )
    with pytest.raises(ValueError):
        PMFOrigin(revision="untyped value")
    with pytest.raises(ValueError):
        load_pmf(b" " * (MAX_METADATA_BYTES + 1))
    with pytest.raises(ValueError):
        load_pmf(b'{"schema_version":"pmf.manifest.v0.1","schema_version":"other"}')


def test_cancellation_existing_output_and_bad_source_leave_no_generation(
    capture, tmp_path
):
    manifest = common_manifest(capture)
    control = OperationControl()
    control.cancel()
    with pytest.raises(OperationCancelled):
        pack_pmf(
            manifest,
            capture.sources,
            {},
            tmp_path / "cancel",
            (capture.key,),
            control=control,
        )
    (capture.sources["weights"] / "model.bin").write_bytes(b"corrupt")
    with pytest.raises(PreservationError):
        pack_pmf(manifest, capture.sources, {}, tmp_path / "bad", (capture.key,))
    assert not (tmp_path / "bad").exists()
    assert not list(tmp_path.glob(".pmf-*"))
    link = tmp_path / "link"
    link.symlink_to(capture.store, target_is_directory=True)
    with pytest.raises(FileExistsError):
        import_preservation(
            capture.store, capture.identity, capture.trust, link, (capture.key,)
        )
    assert verify_store(capture.store, capture.identity, capture.trust)


def test_published_pmf_schema_is_packaged_and_matches_runtime(capture):
    schema = PMFManifest.model_json_schema()
    assert schema == json.loads(Path("docs/schemas/PMFManifestV0_1.json").read_bytes())
    assert schema == json.loads(
        files("foxlight_model_preservation")
        .joinpath("resources/pmf_manifest_v0_1.json")
        .read_bytes()
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(
        common_manifest(capture).model_dump(mode="json")
    )


def test_future_card_contract_stays_opaque_through_bridge(capture, tmp_path):
    raw_card = (
        b'{"new_contract":99,"required_semantics":"future","int64":9223372036854775807}'
    )
    retained = {
        "schema_version": "other-project.card.v99",
        "card_base64": base64.b64encode(raw_card).decode(),
        "future_signed_record": {"exact_bytes": "opaque", "version": 99},
    }
    member = capture.recipe.members[0].model_copy(update={"retained_card": retained})
    recipe = capture.recipe.model_copy(
        update={"members": (member, *capture.recipe.members[1:])}
    )
    original = tmp_path / "future-source"
    identity = pack(recipe, capture.sources, original, (capture.key,))
    pmf = tmp_path / "future-pmf"
    converted = import_preservation(
        original, identity, capture.trust, pmf, (capture.key,)
    )
    manifest = restore_pmf(
        pmf, converted.output_identity, capture.trust, tmp_path / "future-restored"
    )
    assert not any(
        item.profile_id == "skulk.retained-card.v1" for item in manifest.profiles
    )
    recovered = tmp_path / "future-original"
    export_preservation(
        pmf,
        converted.output_identity,
        capture.trust,
        recovered,
        source_trust=capture.trust,
        expected_source_manifest_id=identity,
    )
    assert (
        verify_store(recovered, identity, capture.trust).members[0].retained_card
        == retained
    )


def test_mid_copy_cancellation_never_publishes(capture, tmp_path):
    def cancel(progress):
        if progress.bytes_copied:
            control.cancel()

    control = OperationControl(cancel, interval_seconds=0)
    target = tmp_path / "cancelled"
    with pytest.raises(OperationCancelled):
        import_preservation(
            capture.store,
            capture.identity,
            capture.trust,
            target,
            (capture.key,),
            control=control,
        )
    assert control.progress.committed is False
    assert not target.exists()
    assert not list(tmp_path.glob(".pmf-*"))


def test_cli_pmf_pack_verify_restore_and_bridge(capture, tmp_path):
    key, trust_file = tmp_path / "converter.pem", tmp_path / "trust.json"
    result = command(
        "keygen", "--key", str(key), "--trust-out", str(trust_file), "--unencrypted"
    )
    assert result.returncode == 0, result.stderr
    original_trust = tmp_path / "source-trust.json"
    original_trust.write_bytes(encode_record(capture.trust))
    target = tmp_path / "converted"
    result = command(
        "pmf-import-preservation",
        capture.identity,
        "--store",
        str(capture.store),
        "--trust",
        str(original_trust),
        "--output",
        str(target),
        "--key",
        str(key),
        "--unencrypted",
    )
    assert result.returncode == 0, result.stderr
    identity = json.loads(result.stdout)["output_identity"]
    assert (
        command(
            "pmf-verify", identity, "--store", str(target), "--trust", str(trust_file)
        ).returncode
        == 0
    )
    result = command(
        "pmf-export-preservation",
        identity,
        "--store",
        str(target),
        "--trust",
        str(trust_file),
        "--source-trust",
        str(original_trust),
        "--expected-source-manifest-id",
        capture.identity,
        "--output",
        str(tmp_path / "original-format"),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["output_identity"] == capture.identity
    descriptor = tmp_path / "descriptor.json"
    descriptor.write_bytes(encode_record(common_manifest(capture)))
    generic = tmp_path / "generic"
    result = command(
        "pmf-pack",
        "--descriptor",
        str(descriptor),
        "--member",
        "weights=" + str(capture.sources["weights"]),
        "--member",
        "tokenizer=" + str(capture.sources["tokenizer"]),
        "--store",
        str(generic),
        "--key",
        str(key),
        "--unencrypted",
    )
    assert result.returncode == 0, result.stderr
    identity = json.loads(result.stdout)["manifest"]
    result = command(
        "pmf-restore",
        identity,
        "--store",
        str(generic),
        "--trust",
        str(trust_file),
        "--output",
        str(tmp_path / "generic-files"),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["authority"] == "owner-capture"


def test_public_pmf_vector_and_signature_domain(tmp_path):
    vector = json.loads(Path("examples/conformance/pmf-v0.1.json").read_bytes())
    trust = TrustProfile.model_validate_json(json.dumps(vector["trust"]))
    envelope = base64.b64decode(vector["envelope_base64"])
    manifest = verify_pmf_manifest(envelope, trust, vector["manifest_id"])
    with pytest.raises(PreservationError, match="payload type"):
        verify_manifest(envelope, trust, vector["manifest_id"])
    assert (
        json.loads(base64.b64decode(json.loads(envelope)["payload"]))
        == vector["manifest"]
    )
    assert json.loads(envelope)["payloadType"] == PMF_TYPE
    store = tmp_path / "vector"
    (store / "manifests").mkdir(parents=True)
    (store / "objects/sha256").mkdir(parents=True)
    (store / manifest_path(vector["manifest_id"])).write_bytes(envelope)
    for sha256, content in vector["objects_base64"].items():
        data = base64.b64decode(content)
        assert digest(data) == sha256
        (store / object_path(sha256)).write_bytes(data)
    assert verify_pmf(store, vector["manifest_id"], trust) == manifest
    for case in vector["negative_cases"]:
        raw = copy.deepcopy(vector["manifest"])
        if case == "missing_companion":
            raw["members"].pop()
        elif case == "unknown_required_feature":
            raw["required_features"] = ["unimplemented.v1"]
        else:
            raw["schema_version"] = "pmf.manifest.v99"
        with pytest.raises(ValueError):
            load_pmf(canonical_bytes(raw))
