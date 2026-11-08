"""Signed one-time starter lists: verification, consent, budget and ownership."""

from __future__ import annotations

import base64
import hashlib
import http.client
import importlib.util
import json
import queue
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from foxlight_model_preservation import mirror_intake as intake_module
from foxlight_model_preservation import starter as starter_module
from foxlight_model_preservation.mirror import (
    model_action,
    retry_selection,
    run_mirror,
    withdrawn_selections,
)
from foxlight_model_preservation.mirror_models import Terms
from foxlight_model_preservation.mirror_state import (
    HEADROOM,
    MirrorLedger,
    restart_allowed,
    withdrawal_withholds,
)
from foxlight_model_preservation.models import (
    PreservationError,
    Recipe,
    TrustProfile,
)
from foxlight_model_preservation.signing import (
    canonical_bytes,
    create_key,
    encode_record,
    public_record,
    sign_record,
)
from foxlight_model_preservation.starter import (
    STARTER_LIST_TYPE,
    ImportOptions,
    StarterEntry,
    StarterLicence,
    StarterList,
    import_starter,
    list_identity,
    parse_formats,
    plan_import,
    review_import,
    select_tier,
    sign_starter_list,
    verify_starter_list,
)
from foxlight_model_preservation.storage import manifest_path, object_path, pack


def _script(name: str):
    path = Path(__file__).parents[1] / "scripts" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


starter_build = _script("build_starter_collection")

LICENCE = StarterLicence(
    license_id="Apache-2.0",
    summary="Permissive test licence summary.",
    base_model="test/base",
    base_revision="b" * 40,
    conversion="test/conversion",
    conversion_revision="c" * 40,
)


def fixture_recipe(name: str, licence: str = "Apache-2.0") -> dict:
    return {
        "schema_version": "foxlight.model-preservation.v1",
        "name": name,
        "root_member": "weights",
        "workloads": ["test-only"],
        "runtime_requirements": ["not an inference fixture"],
        "members": [
            {
                "member_id": "weights",
                "role": "primary",
                "format": "test-fixture",
                "origin_repository": "test/conversion",
                "origin_revision": "c" * 40,
                "artifact_identity": f"{name}:weights",
                "dependencies": ["notices"],
                "intake": {
                    "audience": "public",
                    "license_id": licence,
                    "terms_reference": "https://example.test/README.md",
                    "decision": "synthetic fixture",
                    "notice_paths": ["README.md"],
                },
                "files": [{"path": "model.bin"}, {"path": "README.md"}],
            },
            {
                "member_id": "notices",
                "role": "notices",
                "format": "text",
                "origin_repository": "test/base",
                "origin_revision": "b" * 40,
                "artifact_identity": f"{name}:notices",
                "dependencies": [],
                "intake": {
                    "audience": "public",
                    "license_id": licence,
                    "terms_reference": "https://example.test/LICENSE",
                    "decision": "synthetic fixture",
                    "notice_paths": ["LICENSE"],
                },
                "files": [{"path": "LICENSE"}],
            },
        ],
    }


class Collection:
    """Tiny packed bundles and a signer for building starter lists."""

    def __init__(
        self,
        tmp_path: Path,
        names: tuple[str, ...],
        licences: dict[str, str] | None = None,
        formats: dict[str, str] | None = None,
        payloads: dict[str, bytes] | None = None,
    ):
        self.key = Ed25519PrivateKey.generate()
        self.trust = TrustProfile(
            schema_version="foxlight.model-preservation.trust.v1",
            keys=(public_record(self.key),),
            threshold=1,
            as_of="2026-11-07",
        )
        self.store = tmp_path / "published-store"
        self.entries: dict[str, StarterEntry] = {}
        for index, name in enumerate(names):
            licence = (licences or {}).get(name, "Apache-2.0")
            recipe = Recipe.model_validate_json(
                json.dumps(fixture_recipe(name, licence))
            )
            weights = tmp_path / "inputs" / name / "weights"
            notices = tmp_path / "inputs" / name / "notices"
            weights.mkdir(parents=True)
            notices.mkdir(parents=True)
            (weights / "model.bin").write_bytes(
                (payloads or {}).get(name, name.encode() * (1000 + index))
            )
            (weights / "README.md").write_text(f"{name} conversion card\n")
            (notices / "LICENSE").write_text("Apache License fixture text\n")
            identity = pack(
                recipe,
                {"weights": weights, "notices": notices},
                self.store,
                (self.key,),
            )
            size = sum(
                path.stat().st_size
                for path in (
                    weights / "model.bin",
                    weights / "README.md",
                    notices / "LICENSE",
                )
            )
            self.entries[name] = StarterEntry(
                entry_id=name,
                tier="small" if index == 0 else "standard",
                format=(formats or {}).get(name, "gguf"),
                title=name.title(),
                role="Test role",
                manifest=identity,
                recipe=name,
                recipe_sha256="0" * 64,
                size_bytes=size,
                licence=LICENCE.model_copy(update={"license_id": licence}),
                terms=starter_build.unique_terms(recipe),
                mirrors=("https://starter.placeholder.invalid",),
            )

    def listing(self, *names: str, title: str = "Test starter") -> StarterList:
        return StarterList(
            schema_version="sovereign.starter-list.v1",
            title=title,
            description="Fixture starter list.",
            issued="2026-11-07",
            bundle_trust=self.trust,
            entries=tuple(self.entries[name] for name in names),
        )

    def signed(self, starter: StarterList) -> tuple[bytes, str]:
        return sign_starter_list(starter, (self.key,)), list_identity(starter)

    def options(self, **values: object) -> ImportOptions:
        return ImportOptions(mirrors=(str(self.store),), **values)  # type: ignore[arg-type]


def mirror(tmp_path: Path, disk: int = 2 * 1024**3) -> MirrorLedger:
    return MirrorLedger.initialize(
        tmp_path / "mirror",
        name="Starter fixture",
        disk_bytes=disk,
        ingress_daily=1024**3,
        egress_daily=1024**3,
        egress_monthly=1024**3,
        port=0,
    )


@pytest.fixture
def collection(tmp_path: Path) -> Collection:
    return Collection(tmp_path, ("alpha", "beta"))


# Verification ----------------------------------------------------------------


def test_list_verifies_only_with_its_exact_identity_and_signature(collection):
    starter = collection.listing("alpha", "beta")
    data, list_id = collection.signed(starter)
    assert verify_starter_list(data, list_id) == starter
    assert verify_starter_list(data, list_id, trust=collection.trust) == starter
    with pytest.raises(PreservationError, match="expected list ID"):
        verify_starter_list(data, "0" * 64)
    other = Ed25519PrivateKey.generate()
    stranger = TrustProfile(
        schema_version="foxlight.model-preservation.trust.v1",
        keys=(public_record(other),),
        threshold=1,
        as_of="2026-11-07",
    )
    with pytest.raises(PreservationError, match="threshold"):
        verify_starter_list(data, list_id, trust=stranger)


def test_bad_signature_and_foreign_signer_refuse(collection):
    starter = collection.listing("alpha")
    data, list_id = collection.signed(starter)
    envelope = json.loads(data)
    signature = bytearray(base64.b64decode(envelope["signatures"][0]["sig"]))
    signature[0] ^= 1
    envelope["signatures"][0]["sig"] = base64.b64encode(signature).decode()
    with pytest.raises(PreservationError, match="threshold"):
        verify_starter_list(json.dumps(envelope).encode(), list_id)
    # A signature by a key outside the list's own trust profile never counts.
    foreign = encode_record(
        sign_record(starter, (Ed25519PrivateKey.generate(),), STARTER_LIST_TYPE)
    )
    with pytest.raises(PreservationError, match="threshold"):
        verify_starter_list(foreign, list_id)
    with pytest.raises(PreservationError, match="not in its bundle trust"):
        sign_starter_list(starter, (Ed25519PrivateKey.generate(),))
    catalog = encode_record(sign_record(starter, (collection.key,), "other/type"))
    with pytest.raises(PreservationError, match="not a starter list"):
        verify_starter_list(catalog, list_id)


def _resign(collection: Collection, payload: dict) -> tuple[bytes, str]:
    raw = canonical_bytes(payload)
    message = (
        b"DSSEv1 %d " % len(STARTER_LIST_TYPE)
        + STARTER_LIST_TYPE.encode()
        + b" %d " % len(raw)
        + raw
    )
    envelope = {
        "payloadType": STARTER_LIST_TYPE,
        "payload": base64.b64encode(raw).decode(),
        "signatures": [
            {
                "keyid": collection.trust.keys[0].key_id,
                "sig": base64.b64encode(collection.key.sign(message)).decode(),
            }
        ],
    }
    return json.dumps(envelope).encode(), hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(follow=True),
        lambda p: p["entries"][0].update(withdraw_url="https://example.test"),
        lambda p: p.update(schema_version="sovereign.starter-list.v2"),
        lambda p: p["entries"][0].update(tier="huge"),
    ],
)
def test_unknown_fields_and_versions_refuse_even_when_signed(collection, change):
    payload = json.loads(encode_record(collection.listing("alpha")))
    change(payload)
    data, list_id = _resign(collection, payload)
    with pytest.raises(ValueError):
        verify_starter_list(data, list_id)


def test_duplicate_json_keys_refuse(collection):
    data, list_id = collection.signed(collection.listing("alpha"))
    text = data.decode()
    duplicated = text[:-1] + ',"payloadType":"' + STARTER_LIST_TYPE + '"}'
    with pytest.raises(PreservationError, match="invalid bounded JSON"):
        verify_starter_list(duplicated.encode(), list_id)


def test_tiers_nest(collection):
    starter = collection.listing("alpha", "beta")
    assert [e.entry_id for e in select_tier(starter, "small")] == ["alpha"]
    assert [e.entry_id for e in select_tier(starter, "standard")] == [
        "alpha",
        "beta",
    ]


# Import --------------------------------------------------------------------


def test_import_needs_consent_and_changes_nothing_without_it(tmp_path, collection):
    ledger = mirror(tmp_path)
    starter = collection.listing("alpha", "beta")
    list_id = list_identity(starter)
    before = ledger.read()
    with pytest.raises(PreservationError, match="consent"):
        import_starter(
            ledger,
            starter,
            list_id,
            "standard",
            consented=False,
            options=collection.options(),
        )
    assert ledger.read() == before
    assert not (tmp_path / "mirror.starter-import").exists()


def test_import_is_idempotent_and_keeps_no_publisher_relationship(
    tmp_path, collection, monkeypatch
):
    ledger = mirror(tmp_path)
    starter = collection.listing("alpha", "beta")
    list_id = list_identity(starter)

    def offline(*args, **kwargs):
        raise AssertionError("a local starter source contacted the network")

    monkeypatch.setattr("socket.create_connection", offline)
    monkeypatch.setattr("socket.getaddrinfo", offline)
    first = import_starter(
        ledger,
        starter,
        list_id,
        "standard",
        consented=True,
        options=collection.options(),
    )
    assert first["imported"] == ["alpha", "beta"] and not first["failed"]
    assert first["follows_publisher"] is False
    state = ledger.read()
    for entry in starter.entries:
        held = state.models[entry.manifest]
        assert held.origin == "local" and held.state == "retained"
        assert held.source_id is None and held.catalog_id is None
        assert held.mirrors == ()
        assert held.grant.trust == collection.trust
        assert (ledger.path / "store" / manifest_path(entry.manifest)).is_file()
    assert not state.sources
    assert not (tmp_path / "mirror.starter-import").exists()
    second = import_starter(
        ledger,
        starter,
        list_id,
        "standard",
        consented=True,
        options=collection.options(),
    )
    assert second["imported"] == []
    assert [item["reason"] for item in second["skipped"]] == ["held", "held"]
    assert ledger.read().models == state.models


def test_removed_selection_is_never_reimported(tmp_path, collection):
    ledger = mirror(tmp_path)
    starter = collection.listing("alpha")
    list_id = list_identity(starter)
    import_starter(
        ledger, starter, list_id, "small", consented=True, options=collection.options()
    )
    manifest = starter.entries[0].manifest
    ledger.update(
        lambda state: state.model_copy(
            update={
                "models": {
                    **state.models,
                    manifest: state.models[manifest].model_copy(
                        update={"state": "removed"}
                    ),
                }
            }
        )
    )
    again = import_starter(
        ledger, starter, list_id, "small", consented=True, options=collection.options()
    )
    assert again["imported"] == [] and again["skipped"][0]["reason"] == "removed"
    assert ledger.read().models[manifest].state == "removed"


def test_disk_budget_is_enforced_before_any_download(tmp_path, collection):
    starter = collection.listing("alpha", "beta")
    list_id = list_identity(starter)
    (tmp_path / "roomy").mkdir(mode=0o700)
    roomy = mirror(tmp_path / "roomy")
    assert plan_import(roomy, starter, list_id, "standard").fits
    # A budget the two small bundles cannot fit, even counted exactly.
    ledger = mirror(tmp_path, disk=HEADROOM + 4096)
    plan = plan_import(ledger, starter, list_id, "standard")
    assert not plan.fits
    with pytest.raises(PreservationError, match="disk budget"):
        import_starter(
            ledger,
            starter,
            list_id,
            "standard",
            consented=True,
            options=collection.options(),
        )
    assert not ledger.read().models
    assert not (tmp_path / "mirror.starter-import").exists()


def test_shared_objects_count_once_in_disk_admission(tmp_path):
    shared = b"S" * 200_000
    collection = Collection(
        tmp_path, ("one", "two"), payloads={"one": shared, "two": shared}
    )
    starter = collection.listing("one", "two")
    list_id = list_identity(starter)
    ledger = mirror(tmp_path, disk=HEADROOM + 2 * 1024**2)
    # Fit one copy of the shared weights (plus metadata), not two.
    budget = ledger.usage() + HEADROOM + 200_000 + 64 * 1024
    ledger.update(lambda state: state.model_copy(update={"disk_bytes": budget}))
    plan = plan_import(ledger, starter, list_id, "standard")
    assert plan.new_bytes > 400_000 and not plan.fits  # the upper bound
    receipt = import_starter(
        ledger,
        starter,
        list_id,
        "standard",
        consented=True,
        options=collection.options(),
    )
    assert receipt["imported"] == ["one", "two"]
    assert ledger.usage() <= budget - HEADROOM


def test_resume_needs_disk_only_for_the_missing_objects(tmp_path, monkeypatch):
    big = "resume" + "x" * 54
    collection = Collection(tmp_path, (big,))
    starter = collection.listing(big)
    list_id = list_identity(starter)
    manifest = starter.entries[0].manifest
    ledger = mirror(tmp_path)
    real_copy = intake_module.copy_verified_object
    copied: list[str] = []

    def interrupted(source, path, target, file, *args, **kwargs):
        if copied:
            raise OSError("interrupted after the first object")
        copied.append(file.path)
        return real_copy(source, path, target, file, *args, **kwargs)

    with monkeypatch.context() as broken:
        broken.setattr(intake_module, "copy_verified_object", interrupted)
        with pytest.raises(OSError):
            import_starter(
                ledger,
                starter,
                list_id,
                "small",
                consented=True,
                options=collection.options(),
            )
    retry_selection(ledger, manifest)
    size = starter.entries[0].size_bytes
    plan = plan_import(ledger, starter, list_id, "small")
    assert plan.status[big] == "resume"
    remainder = plan.disk_needed
    assert remainder is not None and remainder < size
    # A budget that fits the remainder (plus metadata) but not the whole bundle.
    budget = ledger.usage() + HEADROOM + remainder + 32 * 1024
    assert ledger.usage() + size + HEADROOM > budget
    ledger.update(lambda state: state.model_copy(update={"disk_bytes": budget}))
    assert plan_import(ledger, starter, list_id, "small").fits
    resumed = import_starter(
        ledger,
        starter,
        list_id,
        "small",
        consented=True,
        options=collection.options(),
    )
    assert resumed["imported"] == [big]
    assert ledger.read().models[manifest].state == "retained"


def test_size_mismatch_with_the_list_refuses(tmp_path, collection):
    ledger = mirror(tmp_path)
    starter = collection.listing("alpha")
    lying = starter.model_copy(
        update={
            "entries": (
                starter.entries[0].model_copy(
                    update={"size_bytes": starter.entries[0].size_bytes + 1}
                ),
            )
        }
    )
    with pytest.raises(PreservationError, match="size"):
        import_starter(
            ledger,
            lying,
            list_identity(lying),
            "small",
            consented=True,
            options=collection.options(),
        )
    assert not ledger.read().models


def _served(ledger: MirrorLedger, manifest: str) -> bool:
    ready: queue.Queue[int] = queue.Queue()
    failures: list[BaseException] = []

    def run() -> None:
        try:
            run_mirror(ledger, public=True, seconds=30, ready=ready.put)
        except BaseException as error:  # noqa: BLE001 - reported below
            failures.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        port = ready.get(timeout=10)
        end = time.monotonic() + 10
        while True:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            connection.request("GET", "/" + manifest_path(manifest))
            response = connection.getresponse()
            response.read()
            connection.close()
            if response.status == 200:
                return True
            if time.monotonic() > end:
                return False
            time.sleep(0.05)
    finally:
        ledger.update(lambda state: state.model_copy(update={"paused": True}))
        thread.join(15)
        ledger.update(lambda state: state.model_copy(update={"paused": False}))
        assert not thread.is_alive() and not failures


def test_imported_copies_stay_served_when_a_later_list_omits_them(tmp_path, collection):
    ledger = mirror(tmp_path)
    first = collection.listing("alpha", "beta")
    later = collection.listing("alpha", title="A later starter list")
    assert list_identity(first) != list_identity(later)
    import_starter(
        ledger,
        first,
        list_identity(first),
        "standard",
        consented=True,
        options=collection.options(),
    )
    receipt = import_starter(
        ledger,
        later,
        list_identity(later),
        "standard",
        consented=True,
        options=collection.options(),
    )
    assert receipt["imported"] == []
    omitted = collection.entries["beta"].manifest
    state = ledger.read()
    held = state.models[omitted]
    assert held.state == "retained" and not held.blocked and not held.error
    assert held.withdrawal is None and not withdrawal_withholds(state, held)
    assert omitted not in withdrawn_selections(ledger)
    assert _served(ledger, omitted)
    # Only the operator stops serving a copy.
    model_action(ledger, omitted, "block")
    assert ledger.read().models[omitted].blocked


# CLI and quickstart -----------------------------------------------------------


def _cli(*arguments: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - local CLI
        [sys.executable, "-m", "foxlight_model_preservation.cli", *arguments],
        capture_output=True,
        timeout=60,
        input=stdin.encode() if stdin is not None else None,
    )


def test_cli_import_reviews_then_needs_consent(tmp_path, collection):
    ledger = mirror(tmp_path)
    data, list_id = collection.signed(collection.listing("alpha", "beta"))
    listing = tmp_path / "starter.json"
    listing.write_bytes(data)
    base = [
        "mirror",
        "import-starter",
        "--dir",
        str(ledger.path),
        "--list",
        str(listing),
        "--tier",
        "small",
        "--expected-list-id",
        list_id,
        "--mirror",
        str(collection.store),
    ]
    planned = _cli(*base, "--plan-only")
    assert planned.returncode == 0, planned.stderr.decode()
    review = planned.stderr.decode()
    assert "one-time import" in review and "Apache-2.0" in review
    assert "not legal advice" in review and "not anonymous" in review
    assert json.loads(planned.stdout)["review"]["follows_publisher"] is False
    refused = _cli(*base, stdin="")
    assert refused.returncode != 0 and not ledger.read().models
    wrong = _cli(*[a if a != list_id else "1" * 64 for a in base], "--plan-only")
    assert wrong.returncode != 0
    done = _cli(*base, "--yes-i-accept-the-starter-list")
    assert done.returncode == 0, done.stderr.decode()
    assert json.loads(done.stdout)["imported"] == ["alpha"]
    again = _cli(*base, "--yes-i-accept-the-starter-list")
    assert json.loads(again.stdout)["imported"] == []


def test_quickstart_with_only_a_starter_list_grants_over_the_import(
    tmp_path, collection
):
    data, list_id = collection.signed(collection.listing("alpha", "beta"))
    listing = tmp_path / "starter.json"
    listing.write_bytes(data)
    directory = tmp_path / "quick"
    base = [
        "mirror",
        "quickstart",
        "--dir",
        str(directory),
        "--disk-bytes",
        "2G",
        "--port",
        "0",
        "--starter-list",
        str(listing),
        "--expected-starter-list-id",
        list_id,
        "--starter-tier",
        "standard",
        "--starter-mirror",
        str(collection.store),
    ]
    planned = _cli(*base, "--plan-only")
    assert planned.returncode == 0, planned.stderr.decode()
    assert "Collection: none" in planned.stderr.decode()
    assert "one-time import" in planned.stderr.decode()
    assert not directory.exists()
    done = _cli(*base, "--yes-i-consent-to-public-serving")
    assert done.returncode == 0, done.stderr.decode()
    receipt = json.loads(done.stdout)
    assert receipt["starter"]["imported"] == ["alpha", "beta"]
    state = MirrorLedger(directory).read()
    assert len(state.models) == 2 and not state.sources
    assert restart_allowed(state)
    rerun = _cli(*base, "--yes-i-consent-to-public-serving")
    assert rerun.returncode == 0, rerun.stderr.decode()
    assert json.loads(rerun.stdout)["starter"]["imported"] == []
    assert restart_allowed(MirrorLedger(directory).read())
    too_small = _cli(*[*base[:4], "--disk-bytes", "1M", *base[6:]], "--plan-only")
    # The upper estimate does not fit; the exact check decides after consent.
    assert too_small.returncode == 0, too_small.stderr.decode()
    assert "not by the upper estimate" in too_small.stderr.decode()


# Licence resolution and the packing pipeline ---------------------------------


def _card(licence: object, base: object = None) -> dict:
    data: dict = {"license": licence}
    if base is not None:
        data["base_model"] = base
    return {"cardData": data}


def test_licence_resolves_from_the_base_model_never_the_conversion_tag():
    resolve = starter_build.resolve_licence
    refused = starter_build.LicenceRefused
    assert resolve(_card("apache-2.0"), _card("apache-2.0")) == "Apache-2.0"
    assert resolve(_card("MIT"), _card("mit")) == "MIT"
    assert resolve(_card("mit"), None) == "MIT"
    # A permissive conversion tag never launders a restricted base model.
    with pytest.raises(refused, match="base model licence 'qwen-research'"):
        resolve(_card("qwen-research"), _card("apache-2.0"))
    with pytest.raises(refused, match="base model licence 'llama3.1'"):
        resolve(_card("llama3.1"), _card("mit"))
    # A conversion tagged differently from its base is not clearly the same.
    with pytest.raises(refused, match="does not match"):
        resolve(_card("apache-2.0"), _card("mit"))
    with pytest.raises(refused, match="missing"):
        resolve(_card("apache-2.0"), {"cardData": {}})
    with pytest.raises(refused, match="'other'"):
        resolve(_card("other"), _card("apache-2.0"))
    with pytest.raises(refused, match="unidentified"):
        resolve({"cardData": {}}, _card("apache-2.0"))
    with pytest.raises(refused):
        resolve(_card(["apache-2.0", "mit"]), None)


class FakeHub(starter_build.Hub):
    """An in-memory Hub: card data, trees with LFS oids, and file bytes."""

    def __init__(self, cards: dict, files: dict, lfs: set):
        super().__init__()
        self.cards, self.files, self.lfs = cards, files, lfs
        self.downloads: list[str] = []

    def info(self, repository, revision):
        return self.cards[repository]

    def tree(self, repository, revision):
        listing = {}
        for (origin, rev, path), data in self.files.items():
            if origin == repository and rev == revision:
                item = {"path": path, "type": "file", "size": len(data)}
                if path in self.lfs:
                    item["lfs"] = {"oid": hashlib.sha256(data).hexdigest()}
                listing[path] = item
        return listing

    @contextmanager
    def open(self, origin, revision, path):
        self.downloads.append(path)
        yield BytesIO(self.files[(origin, revision, path)])


def _pipeline(tmp_path: Path, conversion_licence: str = "apache-2.0"):
    files = {
        ("test/conversion", "c" * 40, "model.bin"): b"weights" * 4096,
        ("test/conversion", "c" * 40, "README.md"): b"conversion card\n",
        ("test/base", "b" * 40, "LICENSE"): b"Apache License fixture text\n",
    }
    recipe = fixture_recipe("piped")
    for member in recipe["members"]:
        for file in member["files"]:
            key = (member["origin_repository"], member["origin_revision"], file["path"])
            file["expected_sha256"] = hashlib.sha256(files[key]).hexdigest()
    folder = tmp_path / "recipes"
    folder.mkdir()
    (folder / "piped.json").write_text(json.dumps(recipe))
    plan = {
        "schema_version": "sovereign.starter-plan.v1",
        "title": "Pipeline fixture",
        "description": "Fixture plan.",
        "recipes": "recipes",
        "mirrors": ["https://starter.placeholder.invalid"],
        "entries": [
            {
                "entry_id": "piped",
                "tier": "small",
                "title": "Piped",
                "role": "Test role",
                "recipe": "piped.json",
                "licence": LICENCE.model_dump(mode="json"),
            }
        ],
    }
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    hub = FakeHub(
        {
            "test/base": _card("apache-2.0", ["test/ancestor"]),
            "test/ancestor": _card("mit"),
            "test/conversion": _card(conversion_licence),
        },
        files,
        {"model.bin"},
    )
    return starter_build.load_plan(tmp_path / "plan.json"), hub


def test_pipeline_checks_without_weights_then_builds_an_importable_list(tmp_path):
    (plan, recipes), hub = _pipeline(tmp_path)
    report = starter_build.check(plan, recipes, hub, say=lambda _line: None)
    assert report[0]["ok"] and report[0]["licence"] == "Apache-2.0"
    assert report[0]["ancestors"] == {"test/ancestor": "MIT"}
    assert "model.bin" not in hub.downloads  # LFS files are checked by oid
    key = tmp_path / "starter.pem"
    trust = create_key(key, None)
    output = tmp_path / "starter-list.json"
    with pytest.raises(PreservationError, match="placeholders"):
        starter_build.build(
            plan,
            recipes,
            hub,
            work=tmp_path / "work",
            key_path=key,
            password=None,
            mirrors=plan.mirrors,
            issued="2026-11-07",
            output=output,
            say=lambda _line: None,
        )
    list_id = starter_build.build(
        plan,
        recipes,
        hub,
        work=tmp_path / "work",
        key_path=key,
        password=None,
        mirrors=("https://starter.example.org",),
        issued="2026-11-07",
        output=output,
        say=lambda _line: None,
    )
    starter = verify_starter_list(output.read_bytes(), list_id)
    assert starter.bundle_trust.keys == trust.keys
    entry = starter.entries[0]
    assert entry.mirrors == ("https://starter.example.org",)
    assert entry.terms == (
        Terms(
            license_id="Apache-2.0", terms_reference="https://example.test/README.md"
        ),
        Terms(license_id="Apache-2.0", terms_reference="https://example.test/LICENSE"),
    )
    assert entry.recipe_sha256 == starter_build.recipe_sha256(recipes["piped"])
    ledger = mirror(tmp_path)
    receipt = import_starter(
        ledger,
        starter,
        list_id,
        "small",
        consented=True,
        options=ImportOptions(mirrors=(str(tmp_path / "work" / "store"),)),
    )
    assert receipt["imported"] == ["piped"]
    for written in (output, tmp_path / "work" / "trust.json"):
        assert b"PRIVATE KEY" not in written.read_bytes()


def test_pipeline_check_refuses_a_conversion_tagged_differently(tmp_path):
    (plan, recipes), hub = _pipeline(tmp_path, conversion_licence="mit")
    with pytest.raises(PreservationError, match="does not match"):
        starter_build.check(plan, recipes, hub, say=lambda _line: None)


def test_pipeline_check_refuses_a_changed_file(tmp_path):
    (plan, recipes), hub = _pipeline(tmp_path)
    hub.files[("test/conversion", "c" * 40, "model.bin")] = b"swapped"
    with pytest.raises(PreservationError, match="SHA-256 differs"):
        starter_build.check(plan, recipes, hub, say=lambda _line: None)


def test_committed_starter_plan_and_recipes_are_pinned_with_notices():
    plan, recipes = starter_build.load_plan(starter_build.DEFAULT_PLAN)
    assert plan.licence_policy == "known"
    tiers = {entry.entry_id: entry.tier for entry in plan.entries}
    formats = {entry.entry_id: entry.format for entry in plan.entries}
    assert {k for k, v in formats.items() if v == "mlx"} == {
        "gemma-4-e4b-it-mlx",
        "qwen3-8-27b-mlx",
        "parakeet-tdt-0-6b-v3",
    }
    # Apple-only builds are exactly the add-on: no MLX member in a default entry.
    for entry in plan.entries:
        apple = any(m.format == "mlx" for m in recipes[entry.entry_id].members)
        assert apple == (entry.format == "mlx"), entry.entry_id
    # Without the add-on, standard still has speech to text (Whisper, small).
    default = {e.entry_id for e in plan.entries if e.format == "gguf"}
    assert "whisper-large-v3-turbo" in default
    assert "parakeet-tdt-0-6b-v3" not in default
    assert {k for k, v in tiers.items() if v == "small"} == {
        "qwen3-5-4b",
        "gemma-4-e4b-it",
        "gemma-4-e4b-it-mlx",
        "whisper-large-v3-turbo",
        "kokoro-82m",
        "nomic-embed-text-v1-5",
    }
    assert {v for v in tiers.values()} == {"small", "standard", "large"}
    gated = {
        "llama3.2",
        "llama3.1",
        "gemma",
        "openrail++",
        "coqui-public-model-license",
    }
    for entry in plan.entries:
        recipe = recipes[entry.entry_id]
        for member in recipe.members:
            assert member.intake.audience == "public"
            assert all(file.expected_sha256 for file in member.files)
        assert any(
            starter_build.licence_text(member.origin_repository, file.path)
            for member in recipe.members
            for file in member.files
        ), entry.entry_id
        licence = starter_build.normalize_license(entry.licence.license_id)
        if licence in gated:
            assert starter_build.requires_acceptance(licence) or entry.custom_licence
    paths = {
        file.path
        for recipe in recipes.values()
        for member in recipe.members
        for file in member.files
    }
    # Gemma 3 carries its terms, policy and NOTICE; Llama its NOTICE.
    for path in (
        "examples/starter/notices/gemma/NOTICE",
        "examples/starter/notices/gemma/GEMMA_TERMS_OF_USE.md",
        "examples/starter/notices/gemma/PROHIBITED_USE_POLICY.md",
        "examples/starter/notices/llama3.2/NOTICE",
        "models/llama3_2/LICENSE",
        "USAGE_POLICY.md",
        "LICENSE.txt",
        "examples/starter/notices/parakeet/ATTRIBUTION.md",
    ):
        assert path in paths, path
    notice = (
        Path(__file__).parents[1] / "examples/starter/notices/gemma/NOTICE"
    ).read_text()
    assert notice.startswith(
        "Gemma is provided under and subject to the Gemma Terms of Use found at "
        "ai.google.dev/gemma/terms"
    )
    for version in ("3.1", "3.2"):
        text = (
            Path(__file__).parents[1]
            / f"examples/starter/notices/llama{version}/NOTICE"
        ).read_text()
        assert "Built with Llama" in text and f"Llama {version} is licensed" in text
    kokoro = next(e for e in plan.entries if e.entry_id == "kokoro-82m")
    assert "synthesized" in kokoro.licence.summary
    assert "yl4579/StyleTTS2-LJSpeech" in kokoro.ancestor_waivers
    excluded = " ".join(item.model for item in plan.excluded)
    for name in ("Llama-3.2-11B-Vision", "Llama 4", "Muse-Spark-Open", "bge-m3"):
        assert name in excluded


def test_container_opts_into_the_starter_list(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls"
    stub = bin_dir / "model-preserve"
    stub.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n')
    stub.chmod(0o755)
    environment = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "SOVEREIGN_MIRROR_DIR": str(tmp_path / "data"),
        "SOVEREIGN_MIRROR_COLLECTION": str(tmp_path / "collection.json"),
        "SOVEREIGN_MIRROR_DOMAIN": "mirror.example",
        "SOVEREIGN_MIRROR_IDLE": "0",
    }
    script = Path(__file__).parents[1] / "deploy/mirror/entrypoint.sh"

    def plan_call() -> list[str]:
        if log.exists():
            log.unlink()
        subprocess.run(  # noqa: S603 - repository script, fixed arguments
            ["/bin/sh", str(script)], env=environment, capture_output=True, timeout=30
        )
        (call,) = log.read_text().splitlines()
        return call.split()

    # Without the opt-in nothing about a starter list is passed.
    default = plan_call()
    assert "--starter-list" not in default and "--collection" in default
    environment.update(
        SOVEREIGN_MIRROR_STARTER_LIST="/config/starter-list.json",
        SOVEREIGN_MIRROR_STARTER_LIST_ID="a" * 64,
        SOVEREIGN_MIRROR_STARTER_TIER="standard",
    )
    opted = plan_call()
    assert opted[opted.index("--starter-list") + 1] == "/config/starter-list.json"
    assert opted[opted.index("--expected-starter-list-id") + 1] == "a" * 64
    assert opted[opted.index("--starter-tier") + 1] == "standard"
    # The collection file is optional with a starter list.
    assert "--collection" not in opted and "--plan-only" in opted
    (tmp_path / "collection.json").write_text("{}")
    assert "--collection" in plan_call()


# Gated licences -------------------------------------------------------------


@pytest.fixture
def gated_collection(tmp_path: Path) -> Collection:
    return Collection(
        tmp_path, ("open", "gemma-three"), licences={"gemma-three": "gemma"}
    )


def test_review_shows_the_gate_and_import_needs_exact_acceptance(
    tmp_path, gated_collection
):
    ledger = mirror(tmp_path)
    starter = gated_collection.listing("open", "gemma-three")
    list_id = list_identity(starter)
    small = plan_import(ledger, starter, list_id, "small")
    assert small.accept == ()  # The small tier here has no gated model.
    plan = plan_import(ledger, starter, list_id, "standard")
    assert plan.accept == ("gemma",)
    text, summary = review_import(starter, plan)
    assert "Terms you must accept" in text and "--accept-terms gemma" in text
    assert "terms to accept: gemma" in text
    assert "https://example.test/LICENSE" in text
    assert summary["accept"] == ["gemma"]
    entries = {item["entry_id"]: item for item in summary["entries"]}
    assert entries["gemma-three"]["requires_acceptance"] == ["gemma"]
    assert entries["open"]["requires_acceptance"] == []
    options = gated_collection.options()
    for accepted in (None, (), ("apache-2.0",), ("gemma", "llama3.1")):
        with pytest.raises(PreservationError, match="terms_acceptance_required"):
            import_starter(
                ledger,
                starter,
                list_id,
                "standard",
                consented=True,
                accepted=accepted,
                options=options,
            )
    assert not ledger.read().models
    assert not (ledger.path / "scratch" / "starter-acceptance").exists()
    receipt = import_starter(
        ledger,
        starter,
        list_id,
        "standard",
        consented=True,
        accepted=("Gemma",),
        options=options,
    )
    assert receipt["imported"] == ["open", "gemma-three"]
    assert receipt["accepted_terms"] == ["gemma"]
    (record,) = (ledger.path / "scratch" / "starter-acceptance").iterdir()
    saved = json.loads(record.read_text())
    assert saved["accepted"] == ["gemma"] and saved["list_id"] == list_id
    assert saved["entries"] == ["open", "gemma-three"]
    # Nothing new to keep: nothing to accept on a rerun.
    again = import_starter(
        ledger, starter, list_id, "standard", consented=True, options=options
    )
    assert again["imported"] == [] and again["accepted_terms"] == []


def test_cli_refuses_gated_models_without_accepted_terms(tmp_path, gated_collection):
    ledger = mirror(tmp_path)
    data, list_id = gated_collection.signed(
        gated_collection.listing("open", "gemma-three")
    )
    listing = tmp_path / "starter.json"
    listing.write_bytes(data)
    base = [
        "mirror",
        "import-starter",
        "--dir",
        str(ledger.path),
        "--list",
        str(listing),
        "--tier",
        "standard",
        "--expected-list-id",
        list_id,
        "--mirror",
        str(gated_collection.store),
        "--yes-i-accept-the-starter-list",
    ]
    refused = _cli(*base)
    assert refused.returncode != 0 and not ledger.read().models
    assert "terms_acceptance_required" in refused.stderr.decode()
    done = _cli(*base, "--accept-terms", "gemma")
    assert done.returncode == 0, done.stderr.decode()
    assert json.loads(done.stdout)["accepted_terms"] == ["gemma"]


def test_quickstart_refuses_gated_models_before_setup(tmp_path, gated_collection):
    data, list_id = gated_collection.signed(
        gated_collection.listing("open", "gemma-three")
    )
    listing = tmp_path / "starter.json"
    listing.write_bytes(data)
    directory = tmp_path / "quick"
    base = [
        "mirror",
        "quickstart",
        "--dir",
        str(directory),
        "--disk-bytes",
        "2G",
        "--port",
        "0",
        "--starter-list",
        str(listing),
        "--expected-starter-list-id",
        list_id,
        "--starter-tier",
        "standard",
        "--starter-mirror",
        str(gated_collection.store),
        "--yes-i-consent-to-public-serving",
    ]
    refused = _cli(*base)
    assert refused.returncode != 0 and not directory.exists()
    done = _cli(*base, "--accept-starter-terms", "gemma")
    assert done.returncode == 0, done.stderr.decode()
    assert json.loads(done.stdout)["starter"]["accepted_terms"] == ["gemma"]


def test_known_licence_policy_admits_gated_terms_from_the_base_only():
    resolve = starter_build.resolve_licence
    refused = starter_build.LicenceRefused
    with pytest.raises(refused, match="Apache-2.0 or MIT"):
        resolve(_card("gemma"), _card("gemma"))
    assert resolve(_card("gemma"), _card("gemma"), "known") == "gemma"
    assert resolve(_card("apache-2.0"), None, "known") == "Apache-2.0"
    assert starter_build.requires_acceptance("gemma")
    assert not starter_build.requires_acceptance("apache-2.0")
    # The base decides: a permissive conversion tag never hides Gemma's terms.
    with pytest.raises(refused, match="does not match"):
        resolve(_card("gemma"), _card("apache-2.0"), "known")
    with pytest.raises(refused, match="known licence"):
        resolve(_card("other"), _card("other"), "known")
    with pytest.raises(refused, match="known licence"):
        resolve(_card("my-own-terms"), None, "known")


def test_container_passes_accepted_starter_terms(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls"
    stub = bin_dir / "model-preserve"
    stub.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n')
    stub.chmod(0o755)
    environment = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "SOVEREIGN_MIRROR_DIR": str(tmp_path / "data"),
        "SOVEREIGN_MIRROR_COLLECTION": str(tmp_path / "collection.json"),
        "SOVEREIGN_MIRROR_IDLE": "0",
        "SOVEREIGN_MIRROR_STARTER_LIST": "/config/starter-list.json",
        "SOVEREIGN_MIRROR_STARTER_LIST_ID": "a" * 64,
        "SOVEREIGN_MIRROR_STARTER_ACCEPT_TERMS": "gemma,llama3.1",
    }
    script = Path(__file__).parents[1] / "deploy/mirror/entrypoint.sh"
    subprocess.run(  # noqa: S603 - repository script, fixed arguments
        ["/bin/sh", str(script)], env=environment, capture_output=True, timeout=30
    )
    (call,) = log.read_text().splitlines()
    arguments = call.split()
    assert arguments[arguments.index("--accept-starter-terms") + 1] == (
        "gemma,llama3.1"
    )


def test_pipeline_ancestor_waivers_and_named_custom_licences(tmp_path):
    (plan, recipes), hub = _pipeline(tmp_path)
    hub.cards["test/ancestor"] = {"cardData": {}}
    with pytest.raises(PreservationError, match="declared base test/ancestor"):
        starter_build.check(plan, recipes, hub, say=lambda _line: None)
    entry = plan.entries[0].model_copy(
        update={"ancestor_waivers": {"test/ancestor": "reviewed exception"}}
    )
    waived = plan.model_copy(update={"entries": (entry,)})
    report = starter_build.check(waived, recipes, hub, say=lambda _line: None)
    assert report[0]["ancestors"] == {"test/ancestor": "waived: reviewed exception"}
    # A named custom licence ("other" + license_name) is read from the card.
    named = {
        "cardData": {"license": "other", "license_name": "coqui-public-model-license"}
    }
    assert starter_build._card_licence(named) == "coqui-public-model-license"
    with pytest.raises(starter_build.LicenceRefused, match="known licence"):
        starter_build.resolve_licence(named, None, "known")


# Formats: the Apple MLX add-on ----------------------------------------------


@pytest.fixture
def mlx_collection(tmp_path: Path) -> Collection:
    return Collection(
        tmp_path,
        ("chat", "chat-mlx", "big"),
        formats={"chat-mlx": "mlx"},
    )


def test_formats_parse_strictly():
    assert parse_formats(None) == ("gguf",)
    assert parse_formats("mlx, GGUF") == ("gguf", "mlx")
    for bad in ("", "onnx", "gguf,exl2"):
        with pytest.raises(PreservationError, match="gguf and/or mlx"):
            parse_formats(bad)


def test_default_import_excludes_mlx_and_gguf_mlx_includes_it(tmp_path, mlx_collection):
    # "chat" is small; "chat-mlx" and "big" are standard (the fixture's tiers).
    starter = mlx_collection.listing("chat", "chat-mlx", "big")
    list_id = list_identity(starter)
    mlx_bytes = mlx_collection.entries["chat-mlx"].size_bytes
    assert [e.entry_id for e in select_tier(starter, "standard")] == ["chat", "big"]
    assert [e.entry_id for e in select_tier(starter, "small", ("gguf", "mlx"))] == [
        "chat"
    ]
    ledger = mirror(tmp_path)
    plan = plan_import(ledger, starter, list_id, "standard")
    assert plan.addon_bytes == mlx_bytes
    text, summary = review_import(starter, plan)
    assert "Apple MLX add-on" in text and "not included" in text
    assert summary["mlx_addon_bytes"] == mlx_bytes and summary["formats"] == ["gguf"]
    first = import_starter(
        ledger,
        starter,
        list_id,
        "standard",
        consented=True,
        options=mlx_collection.options(),
    )
    assert first["imported"] == ["chat", "big"] and first["formats"] == ["gguf"]
    assert mlx_collection.entries["chat-mlx"].manifest not in ledger.read().models
    with_mlx = plan_import(ledger, starter, list_id, "standard", ("gguf", "mlx"))
    assert "included)" in review_import(starter, with_mlx)[0]
    second = import_starter(
        ledger,
        starter,
        list_id,
        "standard",
        consented=True,
        formats=("gguf", "mlx"),
        options=mlx_collection.options(),
    )
    assert second["imported"] == ["chat-mlx"]
    assert mlx_collection.entries["chat-mlx"].manifest in ledger.read().models


def test_cli_formats_flag_and_container_env(tmp_path, mlx_collection):
    ledger = mirror(tmp_path)
    data, list_id = mlx_collection.signed(
        mlx_collection.listing("chat", "chat-mlx", "big")
    )
    listing = tmp_path / "starter.json"
    listing.write_bytes(data)
    base = [
        "mirror",
        "import-starter",
        "--dir",
        str(ledger.path),
        "--list",
        str(listing),
        "--tier",
        "standard",
        "--expected-list-id",
        list_id,
        "--mirror",
        str(mlx_collection.store),
        "--yes-i-accept-the-starter-list",
    ]
    assert _cli(*base, "--formats", "onnx").returncode != 0
    done = _cli(*base, "--formats", "gguf,mlx")
    assert done.returncode == 0, done.stderr.decode()
    assert json.loads(done.stdout)["imported"] == ["chat", "chat-mlx", "big"]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls"
    stub = bin_dir / "model-preserve"
    stub.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n')
    stub.chmod(0o755)
    environment = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "SOVEREIGN_MIRROR_DIR": str(tmp_path / "data"),
        "SOVEREIGN_MIRROR_IDLE": "0",
        "SOVEREIGN_MIRROR_STARTER_LIST": "/config/starter-list.json",
        "SOVEREIGN_MIRROR_STARTER_LIST_ID": "a" * 64,
    }
    script = Path(__file__).parents[1] / "deploy/mirror/entrypoint.sh"
    for value, expected in ((None, "gguf"), ("gguf,mlx", "gguf,mlx")):
        if log.exists():
            log.unlink()
        if value:
            environment["SOVEREIGN_MIRROR_STARTER_FORMATS"] = value
        subprocess.run(  # noqa: S603 - repository script, fixed arguments
            ["/bin/sh", str(script)], env=environment, capture_output=True, timeout=30
        )
        arguments = log.read_text().split()
        assert arguments[arguments.index("--starter-formats") + 1] == expected


def test_pipeline_check_refuses_an_mlx_build_outside_the_add_on(tmp_path):
    (plan, recipes), hub = _pipeline(tmp_path)
    recipe = recipes["piped"]
    members = tuple(
        m.model_copy(update={"format": "mlx"}) if m.member_id == "weights" else m
        for m in recipe.members
    )
    mislabelled = {"piped": recipe.model_copy(update={"members": members})}
    with pytest.raises(PreservationError, match="format mlx"):
        starter_build.check(plan, mislabelled, hub, say=lambda _line: None)
    entry = plan.entries[0].model_copy(update={"format": "mlx"})
    labelled = plan.model_copy(update={"entries": (entry,)})
    assert starter_build.check(labelled, mislabelled, hub, say=lambda _l: None)[0]["ok"]


# Review fixes: credentials, idempotence, evidence binding, resume -------------


def test_hub_token_goes_only_to_the_hub_and_never_across_redirects():
    hub = starter_build.Hub(token="secret-token")
    request = hub._request(
        "https://huggingface.co/org/model/resolve/" + "a" * 40 + "/x"
    )
    assert request.unredirected_hdrs.get("Authorization") == "Bearer secret-token"
    assert "Authorization" not in request.headers  # never copied to a redirect
    for elsewhere in (
        "https://raw.githubusercontent.com/o/r/" + "a" * 40 + "/LICENSE",
        "https://huggingface.co.evil.example/x",
        "https://evil.example/huggingface.co/x",
    ):
        other = hub._request(elsewhere)
        assert not other.has_header("Authorization")
        assert "Authorization" not in other.unredirected_hdrs
    with pytest.raises(PreservationError, match="HTTPS"):
        hub._request("http://huggingface.co/x")
    handler = starter_build._HttpsRedirects()
    request.add_header("Authorization", "Bearer leaked-if-copied")
    moved = handler.redirect_request(
        request, None, 302, "Found", {}, "https://cdn-lfs.example/blob"
    )
    assert moved is not None
    assert not moved.has_header("Authorization")
    assert not any(name.casefold() == "authorization" for name in moved.headers)
    assert not moved.unredirected_hdrs
    with pytest.raises(PreservationError, match="HTTPS"):
        handler.redirect_request(
            request, None, 302, "Found", {}, "http://cdn.example/b"
        )


def test_origins_and_placeholders_parse_exactly():
    parse = starter_build.parse_origin
    assert parse("github.com/spdx/license-list-data") == (
        "github",
        "spdx/license-list-data",
    )
    assert parse("Qwen/Qwen3.5-4B") == ("hub", "Qwen/Qwen3.5-4B")
    for bad in (
        "evil.example/github.com/x",
        "github.com/owner/repo/extra",
        "github.com/owner",
        "https://huggingface.co/x/y",
        "../x",
    ):
        with pytest.raises(PreservationError, match="origin"):
            parse(bad)
    hub = starter_build.Hub()
    with pytest.raises(PreservationError):
        hub.url("org/model", "a" * 40, "../../etc/passwd")
    assert starter_build.is_placeholder("https://starter.placeholder.invalid/")
    assert not starter_build.is_placeholder("https://invalid.example.org/")
    assert not starter_build.is_placeholder("https://example.org/x.invalid/")


def test_rerun_with_unchanged_acceptance_succeeds_without_downloading(
    tmp_path, gated_collection, monkeypatch
):
    ledger = mirror(tmp_path)
    starter = gated_collection.listing("open", "gemma-three")
    list_id = list_identity(starter)
    options = gated_collection.options()
    import_starter(
        ledger,
        starter,
        list_id,
        "standard",
        consented=True,
        accepted=("gemma",),
        options=options,
    )
    fetched: list[str] = []
    real_fetch = starter_module.fetch
    monkeypatch.setattr(
        starter_module,
        "fetch",
        lambda manifest, *a, **k: (
            fetched.append(manifest) or real_fetch(manifest, *a, **k)
        ),
    )
    again = import_starter(
        ledger,
        starter,
        list_id,
        "standard",
        consented=True,
        accepted=("gemma",),
        options=options,
    )
    assert again["imported"] == [] and not fetched
    # A licence nothing in this import uses is still refused.
    with pytest.raises(PreservationError, match="terms_acceptance_required"):
        import_starter(
            ledger,
            starter,
            list_id,
            "standard",
            consented=True,
            accepted=("gemma", "llama3.1"),
            options=options,
        )


def test_container_restarts_cleanly_with_the_same_accepted_terms(
    tmp_path, gated_collection
):
    data, list_id = gated_collection.signed(
        gated_collection.listing("open", "gemma-three")
    )
    config = tmp_path / "config"
    config.mkdir()
    (config / "starter-list.json").write_bytes(data)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "model-preserve"
    wrapper.write_text(
        "#!/bin/sh\n"
        'if [ "$1 $2" = "mirror run" ]; then echo "would serve"; exit 0; fi\n'
        f'exec "{sys.executable}" -m foxlight_model_preservation.cli "$@"\n'
    )
    wrapper.chmod(0o755)
    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o700)
    environment = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "SOVEREIGN_MIRROR_DIR": str(data_dir / "mirror"),
        "SOVEREIGN_MIRROR_COLLECTION": str(config / "collection.json"),
        "SOVEREIGN_MIRROR_BUDGET": "2G",
        "SOVEREIGN_MIRROR_IDLE": "0",
        "SOVEREIGN_MIRROR_CONSENT": "yes-i-consent-to-public-serving",
        "SOVEREIGN_MIRROR_STARTER_LIST": str(config / "starter-list.json"),
        "SOVEREIGN_MIRROR_STARTER_LIST_ID": list_id,
        "SOVEREIGN_MIRROR_STARTER_TIER": "standard",
        "SOVEREIGN_MIRROR_STARTER_ACCEPT_TERMS": "gemma",
        "SOVEREIGN_MIRROR_STARTER_MIRROR": str(gated_collection.store),
    }
    script = Path(__file__).parents[1] / "deploy/mirror/entrypoint.sh"
    for _attempt in range(2):
        started = subprocess.run(  # noqa: S603 - repository script
            ["/bin/sh", str(script)],
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert started.returncode == 0, started.stderr
        assert "Setup refused" not in started.stderr, started.stderr
        assert "would serve" in started.stdout
    state = MirrorLedger(data_dir / "mirror").read()
    assert len(state.models) == 2 and restart_allowed(state)


def _gemma_pipeline(tmp_path, recipe_licence: str):
    (plan, recipes), hub = _pipeline(tmp_path)
    for card in ("test/base", "test/conversion"):
        hub.cards[card] = _card("gemma")
    hub.cards["test/ancestor"] = _card("gemma")
    recipe = recipes["piped"]
    members = tuple(
        m.model_copy(
            update={
                "intake": m.intake.model_copy(update={"license_id": recipe_licence})
            }
        )
        for m in recipe.members
    )
    entry = plan.entries[0].model_copy(
        update={
            "licence": plan.entries[0].licence.model_copy(
                update={"license_id": "gemma"}
            )
        }
    )
    plan = plan.model_copy(update={"licence_policy": "known", "entries": (entry,)})
    return plan, {"piped": recipe.model_copy(update={"members": members})}, hub


def test_recipe_member_licences_must_match_the_resolved_licence(tmp_path):
    plan, recipes, hub = _gemma_pipeline(tmp_path, "MIT")
    with pytest.raises(PreservationError, match="resolves to gemma"):
        starter_build.check(plan, recipes, hub, say=lambda _line: None)
    (tmp_path / "ok").mkdir()
    plan, recipes, hub = _gemma_pipeline(tmp_path / "ok", "gemma")
    report = starter_build.check(plan, recipes, hub, say=lambda _line: None)
    assert report[0]["requires_acceptance"] == ["gemma"]


def test_licence_evidence_is_bound_to_the_recipe_revision(tmp_path):
    (plan, recipes), hub = _pipeline(tmp_path)
    recipe = recipes["piped"]
    for (origin, _rev, path), data in list(hub.files.items()):
        if origin == "test/conversion":
            hub.files[(origin, "d" * 40, path)] = data
    members = tuple(
        m.model_copy(update={"origin_revision": "d" * 40})
        if m.member_id == "weights"
        else m
        for m in recipe.members
    )
    moved = {"piped": recipe.model_copy(update={"members": members})}
    with pytest.raises(PreservationError, match="its licence was resolved at"):
        starter_build.check(plan, moved, hub, say=lambda _line: None)


def test_interrupted_import_resumes_after_an_explicit_retry(
    tmp_path, collection, monkeypatch
):
    ledger = mirror(tmp_path)
    starter = collection.listing("alpha")
    list_id = list_identity(starter)
    manifest = starter.entries[0].manifest

    def interrupted(*args, **kwargs):
        raise OSError("disk went away")

    with monkeypatch.context() as broken:
        broken.setattr(intake_module, "copy_verified_object", interrupted)
        with pytest.raises(OSError):
            import_starter(
                ledger,
                starter,
                list_id,
                "small",
                consented=True,
                options=collection.options(),
            )
    assert ledger.read().models[manifest].error
    assert plan_import(ledger, starter, list_id, "small").status["alpha"] == "error"
    retry_selection(ledger, manifest)
    held = ledger.read().models[manifest]
    assert held.state == "pending" and not held.error
    plan = plan_import(ledger, starter, list_id, "small")
    assert plan.status["alpha"] == "resume" and plan.new_bytes > 0
    resumed = import_starter(
        ledger,
        starter,
        list_id,
        "small",
        consented=True,
        options=collection.options(),
    )
    assert resumed["imported"] == ["alpha"]
    assert ledger.read().models[manifest].state == "retained"


def test_quickstart_import_reports_an_exact_budget_refusal(tmp_path, collection):
    ledger = mirror(tmp_path, disk=HEADROOM + 4096)
    starter = collection.listing("alpha", "beta")
    receipt = import_starter(
        ledger,
        starter,
        list_identity(starter),
        "standard",
        consented=True,
        options=collection.options(keep_going=True),
    )
    assert receipt["imported"] == []
    assert {item["error"] for item in receipt["failed"]} == {"budget_exceeded"}
    assert not ledger.read().models


def test_shared_objects_are_linked_from_the_store_not_fetched_again(tmp_path):
    shared = b"Q" * 300_000
    collection = Collection(
        tmp_path, ("one", "two"), payloads={"one": shared, "two": shared}
    )
    starter = collection.listing("one", "two")
    list_id = list_identity(starter)
    ledger = mirror(tmp_path)
    first = collection.listing("one")
    import_starter(
        ledger,
        first,
        list_identity(first),
        "small",
        consented=True,
        options=collection.options(),
    )
    # The source no longer has the shared object: only the store does.
    shared_sha = hashlib.sha256(shared).hexdigest()
    (collection.store / object_path(shared_sha)).unlink()
    # No room for a second copy of the shared weights.
    budget = ledger.usage() + HEADROOM + 64 * 1024
    ledger.update(lambda state: state.model_copy(update={"disk_bytes": budget}))
    receipt = import_starter(
        ledger,
        starter,
        list_id,
        "standard",
        consented=True,
        options=collection.options(),
    )
    assert receipt["imported"] == ["two"], receipt
    assert ledger.usage() <= budget - HEADROOM
    assert not (tmp_path / "mirror.starter-import").exists()


def test_keep_going_reports_an_unavailable_bundle_and_imports_the_rest(
    tmp_path, collection
):
    ledger = mirror(tmp_path)
    starter = collection.listing("alpha", "beta")
    (collection.store / manifest_path(collection.entries["beta"].manifest)).unlink()
    receipt = import_starter(
        ledger,
        starter,
        list_identity(starter),
        "standard",
        consented=True,
        options=collection.options(keep_going=True),
    )
    assert receipt["imported"] == ["alpha"]
    assert [item["entry_id"] for item in receipt["failed"]] == ["beta"]
    with pytest.raises(ValueError):
        import_starter(
            ledger,
            starter,
            list_identity(starter),
            "standard",
            consented=True,
            options=collection.options(),
        )


def test_container_serves_what_it_holds_when_one_bundle_is_unavailable(
    tmp_path, collection
):
    data, list_id = collection.signed(collection.listing("alpha", "beta"))
    (collection.store / manifest_path(collection.entries["beta"].manifest)).unlink()
    config = tmp_path / "config"
    config.mkdir()
    (config / "starter-list.json").write_bytes(data)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "model-preserve"
    wrapper.write_text(
        "#!/bin/sh\n"
        'if [ "$1 $2" = "mirror run" ]; then echo "would serve"; exit 0; fi\n'
        f'exec "{sys.executable}" -m foxlight_model_preservation.cli "$@"\n'
    )
    wrapper.chmod(0o755)
    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o700)
    environment = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "SOVEREIGN_MIRROR_DIR": str(data_dir / "mirror"),
        "SOVEREIGN_MIRROR_COLLECTION": str(config / "collection.json"),
        "SOVEREIGN_MIRROR_BUDGET": "2G",
        "SOVEREIGN_MIRROR_IDLE": "0",
        "SOVEREIGN_MIRROR_CONSENT": "yes-i-consent-to-public-serving",
        "SOVEREIGN_MIRROR_STARTER_LIST": str(config / "starter-list.json"),
        "SOVEREIGN_MIRROR_STARTER_LIST_ID": list_id,
        "SOVEREIGN_MIRROR_STARTER_TIER": "standard",
        "SOVEREIGN_MIRROR_STARTER_MIRROR": str(collection.store),
    }
    script = Path(__file__).parents[1] / "deploy/mirror/entrypoint.sh"
    started = subprocess.run(  # noqa: S603 - repository script
        ["/bin/sh", str(script)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert started.returncode == 0, started.stderr
    assert "Setup refused" not in started.stderr, started.stderr
    assert "would serve" in started.stdout
    receipt = json.loads(started.stdout[: started.stdout.rindex("would serve")])
    assert receipt["starter"]["imported"] == ["alpha"]
    assert [f["entry_id"] for f in receipt["starter"]["failed"]] == ["beta"]
    state = MirrorLedger(data_dir / "mirror").read()
    assert list(state.models) == [collection.entries["alpha"].manifest]
    assert restart_allowed(state)
