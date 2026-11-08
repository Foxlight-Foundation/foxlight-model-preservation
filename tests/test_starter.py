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

from foxlight_model_preservation.mirror import (
    model_action,
    run_mirror,
    withdrawn_selections,
)
from foxlight_model_preservation.mirror_models import Terms
from foxlight_model_preservation.mirror_state import (
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
    plan_import,
    select_tier,
    sign_starter_list,
    verify_starter_list,
)
from foxlight_model_preservation.storage import manifest_path, pack


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


def fixture_recipe(name: str) -> dict:
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
                    "license_id": "Apache-2.0",
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
                    "license_id": "Apache-2.0",
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

    def __init__(self, tmp_path: Path, names: tuple[str, ...]):
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
            recipe = Recipe.model_validate_json(json.dumps(fixture_recipe(name)))
            weights = tmp_path / "inputs" / name / "weights"
            notices = tmp_path / "inputs" / name / "notices"
            weights.mkdir(parents=True)
            notices.mkdir(parents=True)
            (weights / "model.bin").write_bytes(name.encode() * (1000 + index))
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
                title=name.title(),
                role="Test role",
                manifest=identity,
                recipe=name,
                recipe_sha256="0" * 64,
                size_bytes=size,
                licence=LICENCE,
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
        lambda p: p["entries"][0].update(tier="large"),
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
    ledger = mirror(tmp_path, disk=300 * 1024**2)
    starter = collection.listing("alpha", "beta")
    list_id = list_identity(starter)
    plan = plan_import(ledger, starter, list_id, "standard")
    big = starter.model_copy(
        update={
            "entries": tuple(
                entry.model_copy(update={"size_bytes": 200 * 1024**2})
                for entry in starter.entries
            )
        }
    )
    assert plan.fits
    assert not plan_import(ledger, big, list_identity(big), "standard").fits
    with pytest.raises(PreservationError, match="disk budget"):
        import_starter(
            ledger,
            big,
            list_identity(big),
            "standard",
            consented=True,
            options=collection.options(),
        )
    assert not ledger.read().models
    assert not (tmp_path / "mirror.starter-import").exists()


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
    assert too_small.returncode != 0


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


def test_committed_starter_plan_and_recipes_are_strict_and_pinned():
    plan, recipes = starter_build.load_plan(starter_build.DEFAULT_PLAN)
    tiers = {entry.entry_id: entry.tier for entry in plan.entries}
    assert {k for k, v in tiers.items() if v == "small"} == {
        "qwen3-5-4b",
        "whisper-large-v3-turbo",
        "all-minilm-l6-v2",
        "bge-small-en-v1-5",
    }
    for entry in plan.entries:
        recipe = recipes[entry.entry_id]
        assert entry.licence.license_id in {"Apache-2.0", "MIT"}
        for member in recipe.members:
            assert member.intake.audience == "public"
            assert member.intake.license_id in {"Apache-2.0", "MIT"}
            assert all(file.expected_sha256 for file in member.files)
        assert any(
            file.path == "LICENSE" for member in recipe.members for file in member.files
        )
    assert {item.model for item in plan.excluded} == {
        "hexgrad/Kokoro-82M",
        "BAAI/bge-m3",
    }


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
