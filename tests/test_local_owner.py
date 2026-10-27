"""Independent custody, authenticated discovery and offline recovery journey."""

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from test_cards_proofs import Repository, body
from tuf.api.exceptions import DownloadHTTPError
from tuf.ngclient.fetcher import FetcherInterface

from foxlight_model_preservation.cards import (
    ArtifactFile,
    bundle_identity,
    create_card,
    identity_json,
)
from foxlight_model_preservation.kits import export_kit
from foxlight_model_preservation.local_host.catalog import VIRTUAL_URL, RegistryCatalog
from foxlight_model_preservation.local_host.owner import LocalOwner
from foxlight_model_preservation.local_host.selection import registry_recipe
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.signing import digest, encode_record
from foxlight_model_preservation.storage import atomic_bytes, directory


def accepted_catalog(
    tmp_path,
    *,
    companion=True,
    metadata=None,
    catalog_metadata=None,
    primary_paths=("model.bin",),
):
    data = b"Synthetic model bytes, never executable.\n"
    card_body = body()
    card_body.update(metadata or {})
    files = tuple(
        ArtifactFile(
            path=path, size_bytes=len(data), object_id="sha256:" + digest(data)
        )
        for path in primary_paths
    )
    card_body["artifact_bundle"] = {
        "bundle_id": bundle_identity(files),
        "download_size": len(data) * len(files),
        "files": [file.model_dump(mode="json") for file in files],
    }
    card_body["future_properties"] = {
        "unknown": [{"whole_float": 1.0}],
        "never_drop": True,
    }
    if companion:
        card_body["image"] = {
            "family": "moss_image",
            "default_steps": 50,
            "default_guidance": 3.0,
            "dimension_multiple": 16,
            "components": [
                {
                    "role": "tokenizer",
                    "repository": "test/tokenizer",
                    "revision": "b" * 40,
                    "entry_file": "tokenizer.json",
                    "files": [
                        {
                            "path": "tokenizer.json",
                            "size_bytes": len(data),
                            "object_id": "sha256:" + digest(data),
                        }
                    ],
                }
            ],
        }
    repo = Repository(catalog=True)
    repo.root.signed.expires = datetime.now(UTC) + timedelta(days=365)
    for signer in repo.signers:
        repo.root.sign(signer, append=True)
    repo.card = create_card(card_body)
    repo.target = identity_json(
        {
            "cards": [repo.card.model_dump(mode="json")],
            "card_metadata": {
                repo.card.card_id: {
                    "provenance": "community",
                    **(catalog_metadata or {}),
                }
            },
        }
    )
    repo.refresh(expires=datetime.now(UTC) + timedelta(days=1))
    catalog = RegistryCatalog(tmp_path / "registry", anchor=repo.root.to_bytes())
    proof = repo.proof()
    with directory(catalog.cache) as root:
        atomic_bytes(root, "catalogs/" + digest(repo.target) + ".json", repo.target)
        atomic_bytes(
            root, "proofs/" + digest(repo.target) + ".json", encode_record(proof)
        )
        atomic_bytes(
            root, "accepted.json", identity_json({"catalog_id": digest(repo.target)})
        )
    return catalog, repo, data


def finish(owner, receipt):
    for _ in range(200):
        job = next(
            item for item in owner.read()["jobs"] if item["job_id"] == receipt["job_id"]
        )
        if job["state"] not in {"queued", "running"}:
            return job
        time.sleep(0.01)
    raise AssertionError("finite job did not complete")


def submit(owner, plan, operation=None):
    return owner.control(
        "start",
        {
            "plan_id": plan["plan_id"],
            "operation_id": operation or str(uuid.uuid4()),
            "confirmed": True,
        },
    )


def test_same_origin_component_projector_is_consistent_in_find_and_review(tmp_path):
    data = b"Synthetic model bytes, never executable.\n"
    size = len(data)
    catalog, repo, _data = accepted_catalog(
        tmp_path,
        companion=False,
        metadata={
            "vision": {"projector_file": "mmproj.gguf", "projector_size": size},
            "image": {
                "family": "moss_image",
                "default_steps": 50,
                "default_guidance": 3.0,
                "dimension_multiple": 16,
                "components": [
                    {
                        "role": "tokenizer",
                        "repository": "example/synthetic",
                        "revision": "a" * 40,
                        "entry_file": "mmproj.gguf",
                        "files": [
                            {
                                "path": "mmproj.gguf",
                                "size_bytes": size,
                                "object_id": "sha256:" + digest(data),
                            }
                        ],
                    }
                ],
            },
        },
    )
    owner = LocalOwner(tmp_path / "owner", catalog=catalog)
    try:
        result = owner.control("search", {})
        assert result["results"][0]["download_support"] == "ready"
        assert result["results"][0]["size_bytes"] == size * 2
        assert result["results"][0]["details"]["artifact_notes"] == []
        plan = owner.control(
            "plan",
            {
                "kind": "receive",
                "catalog_id": result["catalog_id"],
                "card_id": repo.card.card_id,
            },
        )
        assert plan["projector_included"] is True
        assert plan["file_count"] == 2
        assert plan["details"]["artifact_notes"] == []
    finally:
        owner.close()


@pytest.mark.parametrize("extra", ["image", "vision", "video", "audio"])
def test_find_receive_kit_fresh_owner_restore_without_network(
    tmp_path, monkeypatch, extra
):
    size = len(b"Synthetic model bytes, never executable.\n")
    paths = ("model.bin",)
    metadata = {}
    if extra == "vision":
        paths += ("mmproj.gguf",)
        metadata = {"vision": {"projector_file": "mmproj.gguf", "projector_size": size}}
    elif extra == "video":
        paths += ("duration.bin",)
        metadata = {
            "video": {
                "modes": ["t2v"],
                "companions": [
                    {
                        "kind": "duration_head",
                        "name": "duration",
                        "path": "duration.bin",
                        "size_bytes": size,
                    }
                ],
            }
        }
    elif extra == "audio":
        metadata = {"audio": {"kind": "tts", "supports_reference_audio": True}}
    catalog, repo, data = accepted_catalog(
        tmp_path,
        metadata=metadata,
        primary_paths=paths,
    )
    home = tmp_path / "owner"
    requested = []

    def download(repository, revision, file, destination, control, **kwargs):
        requested.append((repository, revision, file.path))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    monkeypatch.setattr(
        "foxlight_model_preservation.local_host.owner.download_file", download
    )
    owner = LocalOwner(home, catalog=catalog)
    try:
        result = owner.control("search", {"query": "synthetic"})
        assert result["total"] == 1
        plan = owner.control(
            "plan",
            {
                "kind": "receive",
                "catalog_id": result["catalog_id"],
                "card_id": repo.card.card_id,
            },
        )
        assert len(plan["members"]) == 2
        assert plan["file_count"] == len(paths) + 1
        assert plan["projector_included"] is (extra == "vision")
        assert plan["size_bytes"] == len(data) * (len(paths) + 1)
        assert result["results"][0]["size_bytes"] == plan["size_bytes"]
        assert requested == []  # review never fetches weights
        operation = str(uuid.uuid4())
        receipt = submit(owner, plan, operation)
        assert submit(owner, plan, operation)["job_id"] == receipt["job_id"]
        received = finish(owner, receipt)
        assert received["state"] == "succeeded", received
        assert len(requested) == len(paths) + 1
        copy = owner.read()["copies"][0]
        saved_manifest_id = copy["manifest_id"]
        kit_plan = owner.control(
            "plan", {"kind": "kit_export", "copy_id": copy["copy_id"]}
        )
        exported = finish(owner, submit(owner, kit_plan))
        assert exported["state"] == "succeeded", exported
        with owner.open_kit(exported["job_id"]) as stream:
            kit = stream.read()
    finally:
        owner.close()
    # Original owner, registry and captured source are unavailable on the receiver.
    import shutil

    shutil.rmtree(home)
    shutil.rmtree(catalog.cache)
    monkeypatch.setattr(
        "socket.socket",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("network during offline recovery")
        ),
    )
    recipient = LocalOwner(tmp_path / "fresh-owner")
    try:
        upload = recipient.control("upload-create", {"size_bytes": len(kit)})
        for offset in range(0, len(kit), 53):
            recipient.upload(upload["upload_id"], offset, kit[offset : offset + 53])
        plan = recipient.control(
            "plan", {"kind": "kit_import", "upload_id": upload["upload_id"]}
        )
        with pytest.raises(PreservationError):
            recipient.control(
                "start",
                {
                    "plan_id": plan["plan_id"],
                    "operation_id": str(uuid.uuid4()),
                    "confirmed": False,
                },
            )
        entered, release = threading.Event(), threading.Event()
        original_import = recipient._import

        def blocked_import(*args):
            entered.set()
            assert release.wait(5)
            return original_import(*args)

        monkeypatch.setattr(recipient, "_import", blocked_import)
        receipt = submit(recipient, plan)
        try:
            assert entered.wait(5)
            with pytest.raises(PreservationError, match="kit_upload_in_use"):
                recipient.control("upload-remove", {"upload_id": upload["upload_id"]})
        finally:
            release.set()
        imported = finish(recipient, receipt)
        assert imported["state"] == "succeeded", imported
        recipient.control("upload-remove", {"upload_id": upload["upload_id"]})
        assert recipient.read()["uploads"] == []
        copy = recipient.read()["copies"][0]
        assert copy["manifest_id"] == saved_manifest_id
        restored = finish(
            recipient,
            submit(
                recipient,
                recipient.control(
                    "plan", {"kind": "restore", "copy_id": copy["copy_id"]}
                ),
            ),
        )
        assert restored["state"] == "succeeded", restored
        output = recipient.restored_directory(restored["job_id"])
        assert (output / "members/base/model.bin").read_bytes() == data
        for path in paths:
            assert (output / "members/base" / path).read_bytes() == data
        assert (output / "members/companion-1/tokenizer.json").read_bytes() == data
        from foxlight_model_preservation.assurance import load_retained_card
        from foxlight_model_preservation.registry_proof import verify_registry_proof

        retained = load_retained_card((output / "documents/retained-000").read_bytes())
        assert retained.envelope.card == repo.card.card
        assert encode_record(retained.registry_proof) == encode_record(repo.proof())
        verify_registry_proof(
            encode_record(retained.registry_proof),
            repo.root.to_bytes(),
            repo.card.card_id,
            mode="historical",
        )
    finally:
        recipient.close()


@pytest.mark.parametrize("anchor_version", [1, 2])
def test_signed_catalog_refresh_and_offline_search(
    tmp_path, monkeypatch, anchor_version
):
    _catalog, repo, _data = accepted_catalog(tmp_path)
    repo.root.signed.version = anchor_version
    for signer in repo.signers:
        repo.root.sign(signer, append=True)
    routes = {
        "metadata/timestamp.json": repo.timestamp.to_bytes(),
        "metadata/3.snapshot.json": repo.snapshot.to_bytes(),
        "metadata/4.targets.json": repo.targets.to_bytes(),
        "targets/v1/" + digest(repo.target) + ".catalog.json": repo.target,
    }

    class MemoryFetcher(FetcherInterface):
        def __init__(self, *args):
            pass

        def _fetch(self, url):
            path = url.removeprefix(VIRTUAL_URL)
            if path not in routes:
                raise DownloadHTTPError("not found", 404)
            yield routes[path]

    monkeypatch.setattr(
        "foxlight_model_preservation.local_host.catalog.RegistryFetcher", MemoryFetcher
    )
    catalog = RegistryCatalog(tmp_path / "fresh-catalog", anchor=repo.root.to_bytes())
    assert catalog.refresh()["total"] == 1
    routes.clear()
    assert catalog.search("synthetic")["total"] == 1
    with pytest.raises(DownloadHTTPError):
        catalog.refresh()
    assert catalog.search()["total"] == 1
    raw, _proof = catalog.snapshot()
    (catalog.cache / "catalogs" / (digest(raw) + ".json")).write_bytes(raw + b" ")
    with pytest.raises(PreservationError):
        catalog.search()


def test_missing_companions_or_hashes_refuse_before_download(tmp_path):
    catalog, repo, _data = accepted_catalog(tmp_path)
    proof = repo.proof()
    source = repo.card.card.copy()
    source["vision"] = {"processor_repo": "unknown/companion"}
    with pytest.raises(PreservationError, match="companion_adapter_required"):
        registry_recipe(create_card(source), proof)
    no_bundle = body()
    with pytest.raises(PreservationError, match="complete_signed_file_map_required"):
        registry_recipe(create_card(no_bundle), proof)


def test_owner_restart_never_replays_queued_network_and_rejects_second_writer(tmp_path):
    owner = LocalOwner(tmp_path / "owner")
    try:
        with pytest.raises(PreservationError):
            LocalOwner(tmp_path / "owner")
    finally:
        owner.close()


def test_interrupted_queued_job_is_never_replayed(tmp_path, monkeypatch):
    from foxlight_model_preservation.local_host.owner import Job, Journal

    home = tmp_path / "owner"
    owner = LocalOwner(home)
    owner.close()
    queued = Job(
        job_id="a" * 64,
        operation_id=str(uuid.uuid4()),
        plan_id="b" * 64,
        kind="receive",
        state="queued",
        created_at=time.time(),
        name="Never replay",
    )
    (home / "journal.json").write_bytes(encode_record(Journal(jobs=(queued,))))
    monkeypatch.setattr(
        "socket.socket",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unattended network replay")
        ),
    )
    restarted = LocalOwner(home)
    try:
        receipt = restarted.read()["jobs"][0]
        assert receipt["state"] == "interrupted"
        assert receipt["error_code"] == "owner_restarted"
        assert restarted.read()["copies"] == []
    finally:
        restarted.close()


@pytest.mark.parametrize(
    "redirect",
    [
        "http://example.com/model",
        "https://name:password@example.com/model",
        "file:///tmp/model",
    ],
)
def test_model_redirect_cannot_weaken_transport(tmp_path, monkeypatch, redirect):
    import io

    from foxlight_model_preservation.local_host.selection import download_file
    from foxlight_model_preservation.operations import OperationControl

    class Reply(io.BytesIO):
        status = 302

        def getheader(self, name, default=None):
            return redirect if name == "Location" else default

    class Connection:
        def __init__(self, host, *args, **kwargs):
            assert host == "huggingface.co"

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return Reply()

        def close(self):
            pass

    monkeypatch.setattr(
        "foxlight_model_preservation.local_host.selection.PinnedConnection", Connection
    )
    with pytest.raises(PreservationError, match="unsafe model download redirect"):
        download_file(
            "example/model",
            "a" * 40,
            ArtifactFile(
                path="model.bin", size_bytes=1, object_id="sha256:" + digest(b"x")
            ),
            tmp_path / "model.bin",
            OperationControl(),
            deadline=time.monotonic() + 10,
        )
    assert not (tmp_path / "model.bin").exists()


def test_corrupted_kit_has_no_completed_copy(tmp_path, capture):
    kit = tmp_path / "corrupted.foxkit"
    export_kit(capture.store, capture.identity, capture.trust, kit)
    content = bytearray(kit.read_bytes())
    content[-1] ^= 1
    owner = LocalOwner(tmp_path / "receiver")
    try:
        upload = owner.control("upload-create", {"size_bytes": len(content)})
        owner.upload(upload["upload_id"], 0, bytes(content))
        plan = owner.control(
            "plan", {"kind": "kit_import", "upload_id": upload["upload_id"]}
        )
        receipt = finish(owner, submit(owner, plan))
        assert receipt["state"] == "failed"
        assert owner.read()["copies"] == []
        assert not (owner.home / "copies" / receipt["job_id"]).exists()
    finally:
        owner.close()


@pytest.mark.parametrize("change_wrapper", [False, True])
def test_kit_review_retains_exact_signature_wrapper_and_threshold(
    tmp_path, capture, change_wrapper
):
    from foxlight_model_preservation.kits import read_kit_envelope
    from foxlight_model_preservation.storage import manifest_path

    original = capture.store / manifest_path(capture.identity)
    wrapper = b" \n" + original.read_bytes() + b"\n "
    original.write_bytes(wrapper)
    kit = tmp_path / "selected.foxkit"
    export_kit(capture.store, capture.identity, capture.trust, kit)
    assert read_kit_envelope(kit) == wrapper
    owner = LocalOwner(tmp_path / "receiver")
    try:
        content = kit.read_bytes()
        upload = owner.control("upload-create", {"size_bytes": len(content)})
        owner.upload(upload["upload_id"], 0, content)
        review = owner.control(
            "plan", {"kind": "kit_import", "upload_id": upload["upload_id"]}
        )
        assert review["threshold"] == capture.trust.threshold
        assert review["envelope_sha256"] == digest(wrapper)
        assert "trust" not in review
        if change_wrapper:
            original.write_bytes(wrapper + b" ")
            changed = tmp_path / "changed.foxkit"
            export_kit(capture.store, capture.identity, capture.trust, changed)
            (owner.home / "uploads" / upload["upload_id"]).write_bytes(
                changed.read_bytes()
            )
        result = finish(owner, submit(owner, review))
        assert result["state"] == ("failed" if change_wrapper else "succeeded")
        if change_wrapper:
            assert owner.read()["copies"] == []
    finally:
        owner.close()


def test_newer_observed_tuf_floor_cannot_be_bypassed_by_a_retained_review(
    tmp_path, monkeypatch
):
    catalog, repo, _data = accepted_catalog(tmp_path)
    owner = LocalOwner(tmp_path / "owner", catalog=catalog)
    try:
        selection = {
            "kind": "receive",
            "catalog_id": digest(repo.target),
            "card_id": repo.card.card_id,
        }
        reviewed = owner.control("plan", selection)
        assert reviewed["historical"] is False
        repo.timestamp.signed.version += 1
        for signer in repo.signers:
            repo.timestamp.sign(signer, append=True)
        with directory(catalog.cache / "metadata", create=True) as root:
            atomic_bytes(root, "timestamp.json", repo.timestamp.to_bytes())
        called = []
        monkeypatch.setattr(
            "foxlight_model_preservation.local_host.owner.download_file",
            lambda *args, **kwargs: called.append(True),
        )
        result = finish(owner, submit(owner, reviewed))
        assert result["state"] == "failed"
        assert called == []
        assert owner.control("plan", selection)["historical"] is True
        assert catalog.search()["total"] == 1
    finally:
        owner.close()


def test_transfer_slots_can_be_released_without_deleting_saved_data(tmp_path):
    owner = LocalOwner(tmp_path / "owner")
    original = tmp_path / "original.foxkit"
    original.write_bytes(b"An invalid kit, retained by its operator.")
    try:
        uploads = [owner.control("upload-create", {"size_bytes": 64}) for _ in range(8)]
        owner.upload(uploads[0]["upload_id"], 0, original.read_bytes())
        with pytest.raises(PreservationError):
            owner.control(
                "plan", {"kind": "kit_import", "upload_id": uploads[0]["upload_id"]}
            )
        with pytest.raises(PreservationError, match="remove_unused_kit_uploads"):
            owner.control("upload-create", {"size_bytes": 64})
        # An interrupted allocation can leave its owned metadata before its data file.
        (owner.home / "uploads" / uploads[1]["upload_id"]).unlink()
        assert len(owner.read()["uploads"]) == 8
        for item in uploads[:2]:
            owner.control("upload-remove", {"upload_id": item["upload_id"]})
        assert len(owner.read()["uploads"]) == 6
        owner.control("upload-create", {"size_bytes": 64})
        assert len(owner.read()["uploads"]) == 7
        assert original.read_bytes() == b"An invalid kit, retained by its operator."
    finally:
        owner.close()


def test_retained_history_does_not_exhaust_queue_and_preserves_idempotency(
    tmp_path, monkeypatch
):
    home = tmp_path / "owner"
    calls = []
    owner = LocalOwner(home)
    monkeypatch.setattr(owner.catalog, "refresh", lambda **kwargs: calls.append(True))
    first = None
    try:
        for index in range(270):
            receipt = owner.control("refresh", {})
            completed = finish(owner, receipt)
            assert completed["state"] == "succeeded"
            if index == 0:
                first = completed
        assert len(calls) == 270
        receipts, offset = [], 0
        while True:
            page = owner.read(offset)
            receipts.extend(page["jobs"])
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
        assert len(receipts) == len({item["job_id"] for item in receipts}) == 270
        assert receipts[0]["created_at"] >= receipts[-1]["created_at"]
        assert first in receipts
    finally:
        owner.close()
    restarted = LocalOwner(home)
    monkeypatch.setattr(
        restarted.catalog,
        "refresh",
        lambda **kwargs: pytest.fail("replayed archived job"),
    )
    try:
        replay = restarted.control(
            "start",
            {
                "plan_id": first["plan_id"],
                "operation_id": first["operation_id"],
                "confirmed": True,
            },
        )
        assert replay["job_id"] == first["job_id"]
        assert replay["state"] == "succeeded"
        # An archived binding must match the full request, not just its filename.
        record = (
            home
            / "history/operations"
            / (digest(first["operation_id"].encode()) + ".json")
        )
        from foxlight_model_preservation.signing import strict_json

        changed = strict_json(record.read_bytes())
        changed["operation_id"] = "changed-operation-identity"
        record.write_bytes(identity_json(changed))
        with pytest.raises(PreservationError, match="request_changed"):
            restarted.control(
                "start",
                {
                    "plan_id": first["plan_id"],
                    "operation_id": first["operation_id"],
                    "confirmed": True,
                },
            )
    finally:
        restarted.close()


def test_ready_catalog_filter_counts_matching_versions_without_network(tmp_path):
    catalog, repo, _ = accepted_catalog(tmp_path, companion=False)
    legacy = create_card(body())
    repo.target = identity_json(
        {
            "cards": [
                repo.card.model_dump(mode="json"),
                legacy.model_dump(mode="json"),
            ],
            "card_metadata": {
                item.card_id: {"provenance": "community"}
                for item in (repo.card, legacy)
            },
        }
    )
    repo.refresh(expires=datetime.now(UTC) + timedelta(days=1))
    with directory(catalog.cache) as root:
        atomic_bytes(root, "catalogs/" + digest(repo.target) + ".json", repo.target)
        atomic_bytes(
            root, "proofs/" + digest(repo.target) + ".json", encode_record(repo.proof())
        )
        atomic_bytes(
            root, "accepted.json", identity_json({"catalog_id": digest(repo.target)})
        )
    owner = LocalOwner(tmp_path / "owner", catalog=catalog)
    try:
        all_rows = owner.control("search", {})
        ready = owner.control("search", {"ready_only": True})
        assert all_rows["total"] == all_rows["matching_total"] == 2
        assert ready["total"] == ready["ready_total"] == 1
        assert ready["matching_total"] == 2
        assert ready["results"][0]["card_id"] == repo.card.card_id
        with pytest.raises(PreservationError, match="invalid_control"):
            owner.control("search", {"ready_only": "true"})
    finally:
        owner.close()
