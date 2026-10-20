"""Independent public cards and offline TUF evidence, using synthetic keys only."""

import base64
import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from securesystemslib.signer import CryptoSigner
from tuf.api.metadata import (
    Metadata,
    MetaFile,
    Root,
    Snapshot,
    TargetFile,
    Targets,
    Timestamp,
)

from foxlight_model_preservation.cards import (
    ArtifactFile,
    bundle_identity,
    card_identity,
    create_card,
    identity_json,
    load_card,
)
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.registry_proof import (
    VersionFloor,
    create_registry_proof,
    verify_registry_proof,
)
from foxlight_model_preservation.signing import digest, encode_record

NOW = datetime(2026, 10, 19, tzinfo=UTC)


def body():
    """Describe a synthetic non-executable model, with no weights or credentials."""
    return {
        "model_id": "example/synthetic",
        "source_revision": "a" * 40,
        "storage_size": {"in_bytes": 20},
        "n_layers": 1,
        "hidden_size": 16,
        "supports_tensor": False,
        "tasks": ["TextGeneration"],
        "trust_remote_code": False,
    }


class Repository:
    """An isolated signed repository; callers deliberately corrupt its evidence."""

    def __init__(self, *, catalog=False, threshold=1):
        self.card = create_card(body())
        self.path = (
            "v1/catalog.json" if catalog else f"v1/cards/{self.card.card_id}.json"
        )
        self.target = identity_json(
            {
                "cards": [self.card.model_dump(mode="json")],
                "card_metadata": {self.card.card_id: {"provenance": "community"}},
            }
            if catalog
            else self.card.model_dump(mode="json")
        )
        self.signers = [CryptoSigner.generate_ed25519() for _ in range(threshold)]
        self.root = self.new_root(1, self.signers, threshold)
        self.root_chain = ()
        self.refresh()

    @staticmethod
    def new_root(version, signers, threshold=1):
        root = Metadata(Root(version=version, expires=NOW + timedelta(days=30)))
        for role in ("root", "targets", "snapshot", "timestamp"):
            root.signed.roles[role].threshold = threshold
            for signer in signers:
                root.signed.add_key(signer.public_key, role)
        for signer in signers:
            root.sign(signer, append=True)
        return root

    def refresh(self, *, expires=None, signers=None, target_sha256=True):
        expires = expires or NOW + timedelta(days=1)
        signers = signers or self.signers
        self.targets = Metadata(
            Targets(
                version=4,
                expires=expires,
                targets={
                    self.path: TargetFile.from_data(self.path, self.target)
                    if target_sha256
                    else TargetFile(
                        length=len(self.target),
                        hashes={"sha512": "a" * 128},
                        path=self.path,
                    )
                },
            )
        )
        for signer in signers:
            self.targets.sign(signer, append=True)
        self.snapshot = Metadata(
            Snapshot(
                version=3,
                expires=expires,
                meta={
                    "targets.json": MetaFile.from_data(
                        self.targets.signed.version, self.targets.to_bytes(), ["sha256"]
                    )
                },
            )
        )
        for signer in signers:
            self.snapshot.sign(signer, append=True)
        self.timestamp = Metadata(
            Timestamp(
                version=2,
                expires=expires,
                snapshot_meta=MetaFile.from_data(
                    self.snapshot.signed.version, self.snapshot.to_bytes(), ["sha256"]
                ),
            )
        )
        for signer in signers:
            self.timestamp.sign(signer, append=True)

    def proof(self):
        return create_registry_proof(
            trusted_root=self.root.to_bytes(),
            root_chain=self.root_chain,
            timestamp=self.timestamp.to_bytes(),
            snapshot=self.snapshot.to_bytes(),
            targets=self.targets.to_bytes(),
            target_path=self.path,
            target=self.target,
        )

    def verify(self, **kwargs):
        return verify_registry_proof(
            encode_record(self.proof()),
            self.root.to_bytes(),
            self.card.card_id,
            reference_time=NOW,
            **kwargs,
        )


def test_public_card_reproducible_and_projection_free():
    card = create_card(body())
    assert load_card(identity_json(card.model_dump(mode="json"))) == card
    assert create_card(body()).card_id == card.card_id
    # Existing card identity is sorted JSON, not RFC 8785 number normalization.
    assert identity_json({"x": 1}) != identity_json({"x": 1.0})
    for projection in ("is_custom", "registry_card_id", "qualification_only"):
        with pytest.raises(PreservationError, match="structural schema"):
            create_card({**body(), projection: True})


def test_card_identity_tamper_and_body_artifact_disagreement():
    raw = create_card(body()).model_dump(mode="json")
    raw["card"]["hidden_size"] = 32
    with pytest.raises(ValueError, match="identity"):
        load_card(identity_json(raw))
    raw["card"]["source_revision"] = "b" * 40
    with pytest.raises(ValueError, match="revision"):
        load_card(identity_json(raw))
    with pytest.raises(ValueError):
        create_card({**body(), "source_revision": "main"})
    with pytest.raises(ValueError, match="JSON metadata"):
        load_card(b'{"card_id":1,"card_id":2}')


def test_legacy_v1_identity_omitted_only_the_later_null_bundle_field():
    raw = create_card(body()).model_dump(mode="json")
    seed = {key: value for key, value in raw.items() if key != "card_id"}
    del seed["artifact"]["bundle"]
    raw["card_id"] = card_identity(seed)
    assert load_card(identity_json(raw)).card_id == raw["card_id"]
    raw["artifact"]["format"] = "gguf"
    with pytest.raises(ValueError, match="identity"):
        load_card(identity_json(raw))


def test_card_bundle_optional_hash_normalization_and_id():
    files = (ArtifactFile(path="pack/model.bin", size_bytes=20),)
    selected = body()
    selected["artifact_bundle"] = {
        "bundle_id": bundle_identity(files, "pack"),
        "root": "pack",
        "files": [{"path": "pack/model.bin", "size_bytes": 20}],
        "download_size": 20,
    }
    card = create_card(selected)
    assert card.schema_version == 2
    assert card.artifact.bundle.files[0].object_id is None
    bad = copy.deepcopy(selected)
    bad["artifact_bundle"]["files"][0]["size_bytes"] = 19
    with pytest.raises(ValueError, match="sizes"):
        create_card(bad)


@pytest.mark.parametrize("catalog", [False, True])
def test_offline_publication_proof(catalog, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("offline proof verifier attempted network access")

    monkeypatch.setattr("socket.socket", forbidden)
    repository = Repository(catalog=catalog)
    result = repository.verify()
    assert result.card == repository.card
    assert result.report.mode == "fresh"
    assert result.report.expired_roles == ()
    assert result.report.provenance == ("community" if catalog else None)
    assert result.report.versions == VersionFloor(
        root=1, timestamp=2, snapshot=3, targets=4
    )


def test_historical_mode_is_explicit_and_still_enforces_floors():
    repository = Repository()
    repository.refresh(expires=NOW - timedelta(days=1))
    with pytest.raises(PreservationError, match="expired"):
        repository.verify()
    historical = repository.verify(mode="historical")
    assert set(historical.report.expired_roles) == {"targets", "snapshot", "timestamp"}
    assert historical.report.revocation_scope == "supplied-root-chain-only"
    with pytest.raises(PreservationError, match="rollback"):
        repository.verify(mode="historical", minimum_versions=VersionFloor(targets=5))


def test_independent_root_and_exact_card_required():
    repository, attacker = Repository(), Repository()
    proof = encode_record(repository.proof())
    with pytest.raises(PreservationError, match="enrolled root"):
        verify_registry_proof(proof, attacker.root.to_bytes(), repository.card.card_id)
    other_card = create_card({**body(), "hidden_size": 32})
    with pytest.raises(PreservationError):
        verify_registry_proof(
            proof, repository.root.to_bytes(), other_card.card_id, reference_time=NOW
        )


def test_root_rotation_requires_old_and_new_thresholds():
    repository = Repository(threshold=2)
    successors = [CryptoSigner.generate_ed25519() for _ in range(2)]
    new_root = Repository.new_root(2, successors, 2)
    for signer in repository.signers:
        new_root.sign(signer, append=True)
    repository.root_chain = (new_root.to_bytes(),)
    repository.refresh(signers=successors)
    assert repository.verify().report.versions.root == 2
    # Removing an old signature cannot be compensated by new-root signatures.
    del new_root.signatures[repository.signers[0].public_key.keyid]
    repository.root_chain = (new_root.to_bytes(),)
    with pytest.raises(PreservationError):
        repository.verify(mode="historical")


@pytest.mark.parametrize("role", ["timestamp", "snapshot", "targets"])
def test_changed_metadata_never_downgrades(role):
    repository = Repository()
    proof = repository.proof().model_dump(mode="json")
    raw = json.loads(base64.b64decode(proof[role]))
    raw["signed"]["version"] += 1
    proof[role] = base64.b64encode(identity_json(raw)).decode()
    with pytest.raises(PreservationError):
        verify_registry_proof(
            identity_json(proof),
            repository.root.to_bytes(),
            repository.card.card_id,
            mode="historical",
            reference_time=NOW,
        )


def test_target_bytes_hash_bindings_and_thresholds():
    repository = Repository(threshold=2)
    proof = repository.proof().model_dump(mode="json")
    proof["target"] = base64.b64encode(repository.target + b" ").decode()
    with pytest.raises(PreservationError):
        verify_registry_proof(
            identity_json(proof),
            repository.root.to_bytes(),
            repository.card.card_id,
            reference_time=NOW,
        )
    repository.refresh(signers=repository.signers[:1])
    with pytest.raises(PreservationError):
        repository.verify()
    repository.refresh(target_sha256=False)
    with pytest.raises(PreservationError, match="SHA-256 target"):
        repository.verify()


@pytest.mark.parametrize("malformed", [b"{}", b'{"signed":null}', b"[]"])
def test_malformed_metadata_has_controlled_failure(malformed):
    repository = Repository()
    proof = repository.proof().model_dump(mode="json")
    proof["targets"] = base64.b64encode(malformed).decode()
    with pytest.raises(PreservationError):
        verify_registry_proof(
            identity_json(proof),
            repository.root.to_bytes(),
            repository.card.card_id,
            reference_time=NOW,
        )


def test_packaged_card_schema_is_present():
    from importlib.resources import files

    packaged = (
        files("foxlight_model_preservation")
        .joinpath("resources", "skulk_model_card_v1.json")
        .read_bytes()
    )
    published = Path("docs/schemas/SkulkModelCardBodyV1.json").read_bytes()
    assert digest(packaged) == digest(published)


def selected_member(*, object_id="sha256:" + "b" * 64, registry=False, sha256="b" * 64):
    """Retain an exact card whose only synthetic object has optional hash evidence."""
    from foxlight_model_preservation.assurance import retain_card
    from foxlight_model_preservation.models import ManifestMember

    file = ArtifactFile(path="model.bin", size_bytes=20, object_id=object_id)
    selected = body()
    selected["artifact_bundle"] = {
        "bundle_id": bundle_identity((file,)),
        "files": [file.model_dump(mode="json")],
        "download_size": 20,
    }
    card = create_card(selected)
    repository = Repository()
    repository.card = card
    repository.path = f"v1/cards/{card.card_id}.json"
    repository.target = identity_json(card.model_dump(mode="json"))
    repository.refresh()
    retained = retain_card(
        card,
        registry_proof=repository.proof() if registry else None,
    )
    member = ManifestMember.model_validate_json(
        identity_json(
            {
                "member_id": "primary",
                "role": "primary",
                "format": card.artifact.format,
                "origin_repository": card.artifact.repository,
                "origin_revision": card.artifact.revision,
                "artifact_identity": card.card_id,
                "retained_card": retained.model_dump(mode="json"),
                "intake": {
                    "audience": "private",
                    "license_id": "synthetic",
                    "terms_reference": "model.bin",
                    "decision": "synthetic test only",
                    "notice_paths": ["model.bin"],
                },
                "files": [{"path": "model.bin", "size": 20, "sha256": sha256}],
            }
        )
    )
    return member, repository


@pytest.mark.parametrize("registry", [False, True])
@pytest.mark.parametrize(
    "object_id", [None, "git-sha1:" + "a" * 40, "sha256:" + "b" * 64]
)
def test_publication_and_file_evidence_are_separate(registry, object_id):
    from foxlight_model_preservation.assurance import inspect_member_evidence

    member, repository = selected_member(object_id=object_id, registry=registry)
    report = inspect_member_evidence(
        member, trusted_root=repository.root.to_bytes(), reference_time=NOW
    )
    assert report.publication == ("registry-fresh" if registry else "owner-only")
    assert report.file_coverage == (
        "complete-sha256" if object_id == "sha256:" + "b" * 64 else "partial-or-missing"
    )
    assert report.qualification == "not-assessed"


def test_contradictory_member_and_invalid_proof_fail_instead_of_downgrade():
    from foxlight_model_preservation.assurance import inspect_member_evidence
    from foxlight_model_preservation.models import ManifestMember

    member, repository = selected_member(registry=True)
    with pytest.raises(PreservationError, match="enrolled root"):
        inspect_member_evidence(member)
    for field, value in (("sha256", "c" * 64), ("size", 21)):
        raw = member.model_dump(mode="json")
        raw["files"][0][field] = value
        changed = ManifestMember.model_validate_json(identity_json(raw))
        with pytest.raises(PreservationError, match="differs"):
            inspect_member_evidence(
                changed, trusted_root=repository.root.to_bytes(), reference_time=NOW
            )
    raw = member.model_dump(mode="json")
    raw["retained_card"]["registry_proof"]["targets"] = base64.b64encode(b"{}").decode()
    changed = ManifestMember.model_validate_json(identity_json(raw))
    with pytest.raises(PreservationError):
        inspect_member_evidence(
            changed, trusted_root=repository.root.to_bytes(), reference_time=NOW
        )


def test_extra_files_cannot_inherit_registry_sha256_coverage():
    from foxlight_model_preservation.assurance import inspect_member_evidence
    from foxlight_model_preservation.models import ManifestMember

    member, repository = selected_member(registry=True)
    raw = member.model_dump(mode="json")
    raw["files"].append({"path": "loader.py", "size": 20, "sha256": "d" * 64})
    changed = ManifestMember.model_validate_json(identity_json(raw))
    report = inspect_member_evidence(
        changed, trusted_root=repository.root.to_bytes(), reference_time=NOW
    )
    assert report.file_coverage == "partial-or-missing"
    assert report.unbound_paths == ("loader.py",)


def test_cli_card_proof_and_typed_retention(tmp_path):
    from test_cli_catalog import command

    root, card, proof, retained = [
        tmp_path / name
        for name in ("root.json", "card.json", "proof.json", "retained.json")
    ]
    repository = Repository()
    root.write_bytes(repository.root.to_bytes())
    card.write_bytes(repository.target)
    proof.write_bytes(encode_record(repository.proof()))
    checked = command("card-verify", "--card", str(card))
    assert checked.returncode == 0, checked.stderr
    assert json.loads(checked.stdout)["publication"] == "not-established"
    verified = command(
        "proof-verify",
        "--proof",
        str(proof),
        "--root",
        str(root),
        "--card-id",
        repository.card.card_id,
        "--historical",
    )
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["mode"] == "historical"
    failed = command(
        "card-retain",
        "--card",
        str(card),
        "--proof",
        str(proof),
        "--output",
        str(retained),
    )
    assert failed.returncode == 1
    assert not retained.exists()
    retained_ok = command(
        "card-retain",
        "--card",
        str(card),
        "--proof",
        str(proof),
        "--root",
        str(root),
        "--historical",
        "--output",
        str(retained),
    )
    assert retained_ok.returncode == 0, retained_ok.stderr
    assert (
        json.loads(base64.b64decode(json.loads(retained.read_bytes())["card_base64"]))[
            "card_id"
        ]
        == repository.card.card_id
    )


def test_cli_evidence_rehashes_actual_objects(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from test_cli_catalog import command

    from foxlight_model_preservation.models import Recipe, TrustProfile
    from foxlight_model_preservation.signing import public_record
    from foxlight_model_preservation.storage import pack

    content = b"x" * 20
    member, _repository = selected_member(
        object_id="sha256:" + digest(content), sha256=digest(content)
    )
    raw = member.model_dump(mode="json")
    raw["files"] = [{"path": "model.bin"}]
    recipe = Recipe.model_validate_json(
        identity_json(
            {
                "schema_version": "foxlight.model-preservation.v1",
                "name": "Synthetic open card",
                "root_member": "primary",
                "workloads": ["synthetic"],
                "runtime_requirements": [],
                "members": [raw],
            }
        )
    )
    source = tmp_path / "source"
    source.mkdir()
    (source / "model.bin").write_bytes(content)
    key = Ed25519PrivateKey.generate()
    trust = TrustProfile(
        schema_version="foxlight.model-preservation.trust.v1",
        keys=(public_record(key),),
        threshold=1,
        as_of=NOW.isoformat(),
    )
    store = tmp_path / "store"
    manifest = pack(recipe, {"primary": source}, store, (key,))
    enrolled = tmp_path / "owner-trust.json"
    enrolled.write_bytes(encode_record(trust))
    args = (
        "inspect-evidence",
        manifest,
        "--store",
        str(store),
        "--trust",
        str(enrolled),
    )
    result = command(*args)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["members"][0]["publication"] == "owner-only"
    assert report["members"][0]["file_coverage"] == "complete-sha256"
    (store / "objects/sha256" / digest(content)).write_bytes(b"y" * 20)
    rejected = command(*args)
    assert rejected.returncode == 1
    assert "Traceback" not in rejected.stderr


@pytest.mark.parametrize(
    "fault",
    [
        "missing-new-root-signature",
        "skipped-version",
        "missing-metadata-hashes",
        "metadata-version",
        "wrong-role",
        "expired-root",
        "unknown-profile",
    ],
)
def test_additional_proof_failures(fault):
    repository = Repository()
    if fault in {"missing-new-root-signature", "skipped-version"}:
        signers = [CryptoSigner.generate_ed25519()]
        successor = Repository.new_root(3 if fault == "skipped-version" else 2, signers)
        if fault == "missing-new-root-signature":
            successor.signatures.clear()
        successor.sign(repository.signers[0], append=True)
        repository.root_chain = (successor.to_bytes(),)
        repository.refresh(signers=signers)
    elif fault in {"missing-metadata-hashes", "metadata-version"}:
        info = repository.timestamp.signed.snapshot_meta
        if fault == "missing-metadata-hashes":
            info.hashes = None
        else:
            info.version += 1
        repository.timestamp.sign(repository.signers[0])
    elif fault == "expired-root":
        repository.root.signed.expires = NOW - timedelta(days=1)
        repository.root.sign(repository.signers[0])
    proof = repository.proof().model_dump(mode="json")
    if fault == "unknown-profile":
        proof["schema_version"] = "foxlight.registry-proof.v999"
    elif fault == "wrong-role":
        proof["snapshot"] = proof["targets"]
    with pytest.raises(ValueError):
        verify_registry_proof(
            identity_json(proof),
            repository.root.to_bytes(),
            repository.card.card_id,
            reference_time=NOW,
        )


def test_public_card_proof_conformance_vector():
    vector = json.loads(Path("examples/conformance/card-v1.json").read_bytes())
    verified = verify_registry_proof(
        identity_json(vector["proof"]),
        base64.b64decode(vector["enrolled_root_base64"]),
        vector["expected_card_id"],
        reference_time=NOW,
    )
    assert verified.card == load_card(identity_json(vector["card"]))
    assert verified.report.target_sha256 == vector["target_sha256"]


def test_retention_preserves_card_number_encoding_across_bundle_domain():
    from foxlight_model_preservation.assurance import load_retained_card, retain_card
    from foxlight_model_preservation.signing import canonical_bytes

    selected = body()
    selected["image"] = {
        "family": "sdxl",
        "default_steps": 1,
        "default_guidance": 1.0,
        "components": [
            {
                "role": "model",
                "repository": "example/synthetic",
                "revision": "a" * 40,
                "entry_file": "model.bin",
                "files": [
                    {
                        "path": "model.bin",
                        "size_bytes": 20,
                        "object_id": "sha256:" + "b" * 64,
                    }
                ],
            }
        ],
    }
    card = create_card(selected)
    # An ordinary nested object would silently normalize 1.0 to 1 in RFC 8785,
    # breaking the original registry-domain identity after capture/restore.
    with pytest.raises(ValueError, match="identity"):
        load_card(canonical_bytes(card.model_dump(mode="json")))
    retained = load_retained_card(encode_record(retain_card(card)))
    assert retained.envelope.card_id == card.card_id
    assert type(retained.envelope.card["image"]["default_guidance"]) is float


def test_v2_selected_file_must_belong_to_exact_bundle():
    selected = body()
    selected["gguf_file"] = "selected.gguf"
    file = ArtifactFile(path="other.gguf", size_bytes=20)
    selected["artifact_bundle"] = {
        "bundle_id": bundle_identity((file,)),
        "files": [file.model_dump(mode="json")],
        "download_size": 20,
    }
    with pytest.raises(ValueError, match="absent from its bundle"):
        create_card(selected)


def test_v1_selected_file_must_be_captured_even_without_hash_evidence():
    from foxlight_model_preservation.assurance import (
        inspect_member_evidence,
        retain_card,
    )
    from foxlight_model_preservation.models import ManifestMember

    member, _repository = selected_member()
    card = create_card({**body(), "gguf_file": "selected.gguf"})
    raw = member.model_dump(mode="json")
    raw.update(
        {
            "format": "gguf",
            "artifact_identity": card.card_id,
            "retained_card": retain_card(card).model_dump(mode="json"),
        }
    )
    with pytest.raises(PreservationError, match="selected artifact file"):
        inspect_member_evidence(ManifestMember.model_validate_json(identity_json(raw)))
    raw["files"][0]["path"] = "selected.gguf"
    report = inspect_member_evidence(
        ManifestMember.model_validate_json(identity_json(raw))
    )
    assert report.file_coverage == "partial-or-missing"
