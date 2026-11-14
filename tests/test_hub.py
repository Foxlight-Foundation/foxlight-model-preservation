"""Hugging Face as a Find source: search, exact snapshots and saves."""

from __future__ import annotations

import hashlib
import http.server
import json
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from test_catalog_mirror_fallback import _origin_down, _seeds

from foxlight_model_preservation import hub
from foxlight_model_preservation.local_host import owner as owner_module
from foxlight_model_preservation.local_host.owner import LocalOwner
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationControl
from foxlight_model_preservation.signing import digest
from foxlight_model_preservation.transfer import TransferPolicy

LAB = TransferPolicy(allow_http=True, allow_private=True, timeout=5)
REPO = "example-org/tiny-model"
REVISION = "a" * 40
WEIGHTS = b"weights " * 4096
CONFIG = b'{"model_type": "tiny"}'


def _sibling(path: str, data: bytes, *, lfs: bool) -> dict[str, object]:
    entry: dict[str, object] = {
        "rfilename": path,
        "size": len(data),
        "blobId": hub.git_blob_id(data),
    }
    if lfs:
        entry["lfs"] = {"sha256": digest(data), "size": len(data)}
    return entry


class FakeHub:
    """A minimal Hub: search, model info with blobs, and raw small files."""

    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, bool]] = {
            "config.json": (CONFIG, False),
            "model.safetensors": (WEIGHTS, True),
            "pytorch_model.bin": (b"same weights again", True),
            "README.md": (b"# tiny\n", False),
        }
        self.raw_override: dict[str, bytes] = {}
        self.requests: list[str] = []
        hub_state = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def do_GET(self) -> None:
                hub_state.requests.append(self.path)
                parts = urlsplit(self.path)
                path = unquote(parts.path)
                if path == "/api/models":
                    query = parse_qs(parts.query)["search"][0]
                    body = (
                        [
                            {
                                "id": REPO,
                                "downloads": 10,
                                "likes": 1,
                                "private": False,
                                "pipeline_tag": "text-generation",
                                "library_name": "transformers",
                            },
                            {"id": "x/private", "private": True},
                            {"id": "../escape"},
                        ]
                        if "tiny" in query
                        else []
                    )
                    return self._send(json.dumps(body).encode())
                if path == f"/api/models/{REPO}" or path.startswith(
                    f"/api/models/{REPO}/revision/"
                ):
                    info = {
                        "id": REPO,
                        "sha": REVISION,
                        "private": False,
                        "gated": False,
                        "pipeline_tag": "text-generation",
                        "library_name": "transformers",
                        "cardData": {"license": "apache-2.0"},
                        "siblings": [
                            _sibling(name, data, lfs=lfs)
                            for name, (data, lfs) in hub_state.files.items()
                        ],
                    }
                    return self._send(json.dumps(info).encode())
                prefix = f"/{REPO}/raw/{REVISION}/"
                if path.startswith(prefix):
                    name = path[len(prefix) :]
                    if name in hub_state.raw_override:
                        return self._send(hub_state.raw_override[name])
                    if name in hub_state.files:
                        return self._send(hub_state.files[name][0])
                self.send_response(404)
                self.end_headers()

            def _send(self, body: bytes) -> None:
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)


@pytest.fixture
def fake_hub(monkeypatch: pytest.MonkeyPatch):
    server = FakeHub()
    monkeypatch.setattr(hub, "HUB", server.url)
    monkeypatch.setattr(hub, "HUB_POLICY", LAB)
    yield server
    server.close()


def test_search_keeps_only_public_exact_repositories(fake_hub: FakeHub) -> None:
    results = hub.search("tiny model")
    assert [item.repository for item in results] == [REPO]
    assert results[0].view()["source"] == "huggingface"
    assert hub.search("   ") == []


def test_snapshot_pins_the_commit_and_names_every_file(fake_hub: FakeHub) -> None:
    pinned = hub.snapshot(REPO)
    assert pinned.revision == REVISION
    assert pinned.licence == "apache-2.0"
    assert pinned.file("model.safetensors").sha256 == digest(WEIGHTS)
    assert pinned.file("config.json").sha256 is None
    # Safetensors present: the .bin copy of the same weights is not taken.
    assert pinned.default_selection() == [
        "README.md",
        "config.json",
        "model.safetensors",
    ]
    with pytest.raises(PreservationError, match="hub_revision_mismatch"):
        hub.snapshot(REPO, "b" * 40)


def test_small_files_must_match_their_git_blob(fake_hub: FakeHub) -> None:
    pinned = hub.snapshot(REPO)
    exact = hub.exact_hashes(
        pinned, ["config.json", "model.safetensors"], OperationControl()
    )
    assert exact["config.json"] == (len(CONFIG), hashlib.sha256(CONFIG).hexdigest())
    fake_hub.raw_override["config.json"] = b'{"model_type": "evil"}'
    with pytest.raises(PreservationError, match="hub_file_changed"):
        hub.exact_hashes(pinned, ["config.json"], OperationControl())


def test_gguf_defaults_take_one_quant_its_parts_and_one_projector() -> None:
    files = tuple(
        hub.HubFile(path, 1, "0" * 64, None)
        for path in (
            "README.md",
            "m-Q8_0.gguf",
            "Q4_K_M/m-Q4_K_M-00001-of-00002.gguf",
            "Q4_K_M/m-Q4_K_M-00002-of-00002.gguf",
            "mmproj-F32.gguf",
            "mmproj-F16.gguf",
        )
    )
    pinned = hub.HubSnapshot(REPO, REVISION, files, None, False, None, None)
    assert pinned.variants() == ["Q4_K_M/m-Q4_K_M-00001-of-00002.gguf", "m-Q8_0.gguf"]
    assert pinned.default_selection() == [
        "Q4_K_M/m-Q4_K_M-00001-of-00002.gguf",
        "Q4_K_M/m-Q4_K_M-00002-of-00002.gguf",
        "README.md",
        "mmproj-F16.gguf",
    ]
    assert "m-Q8_0.gguf" in pinned.default_selection("m-Q8_0.gguf")


def _finish(owner: LocalOwner, receipt: dict[str, object], seconds: float = 60) -> dict:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        for job in owner.read()["jobs"]:
            if job["job_id"] == receipt["job_id"] and job["state"] not in {
                "queued",
                "running",
            }:
                return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _serve_origin(monkeypatch: pytest.MonkeyPatch, fake: FakeHub) -> None:
    def from_fake(
        repository, revision, file, destination, control, *, deadline, partial, token
    ):
        assert (repository, revision) == (REPO, REVISION)
        destination.write_bytes(fake.files[file.path][0])

    monkeypatch.setattr(owner_module, "download_file", from_fake)


def test_search_waits_for_consent_and_a_hub_save_completes(
    tmp_path: Path, fake_hub: FakeHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve_origin(monkeypatch, fake_hub)
    owner = LocalOwner(tmp_path / "owner")
    try:
        assert owner.read()["hub_search"] is False
        with pytest.raises(PreservationError, match="hub_search_not_enabled"):
            owner.control("hub-search", {"query": "tiny"})
        assert not fake_hub.requests  # nothing left this computer
        owner.control("hub-consent", {"enabled": True})
        found = owner.control("hub-search", {"query": "tiny"})["results"]
        assert [item["repository"] for item in found] == [REPO]
        review = owner.control("hub-review", {"repository": REPO})
        plan = owner.control(
            "plan",
            {
                "kind": "receive",
                "origin": "hub",
                "repository": REPO,
                "revision": review["revision"],
                "files": review["default_files"],
            },
        )
        assert plan["file_count"] == 3 and plan["details"]["license"] == "apache-2.0"
        job = _finish(
            owner,
            owner.control(
                "start",
                {
                    "plan_id": plan["plan_id"],
                    "operation_id": str(uuid.uuid4()),
                    "confirmed": True,
                },
            ),
        )
        assert job["state"] == "succeeded", job
        assert job["sources"]["origin_files"] == 3
        assert owner.read()["copies"][0]["name"] == REPO
    finally:
        owner.close()


def test_a_hub_save_falls_back_to_mirrors_by_hash(
    tmp_path: Path, fake_hub: FakeHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    _origin_down(monkeypatch)
    seed = tmp_path / "seed"
    for data in (WEIGHTS, CONFIG, b"# tiny\n"):
        _store_more(seed, data)
    _seeds(monkeypatch, seed)
    owner = LocalOwner(tmp_path / "owner")
    try:
        review = owner.control("hub-review", {"repository": REPO})
        plan = owner.control(
            "plan",
            {
                "kind": "receive",
                "origin": "hub",
                "repository": REPO,
                "revision": review["revision"],
                "files": review["default_files"],
            },
        )
        job = _finish(
            owner,
            owner.control(
                "start",
                {
                    "plan_id": plan["plan_id"],
                    "operation_id": str(uuid.uuid4()),
                    "confirmed": True,
                },
            ),
        )
        assert job["state"] == "succeeded", job
        assert job["sources"]["mirror_files"] == 3
    finally:
        owner.close()


def _store_more(root: Path, data: bytes) -> Path:
    path = root / "objects/sha256" / digest(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return root


def test_a_hub_plan_refuses_files_the_commit_does_not_have(
    tmp_path: Path, fake_hub: FakeHub
) -> None:
    owner = LocalOwner(tmp_path / "owner")
    try:
        with pytest.raises(PreservationError, match="hub_file_unknown"):
            owner.control(
                "plan",
                {
                    "kind": "receive",
                    "origin": "hub",
                    "repository": REPO,
                    "revision": REVISION,
                    "files": ["missing.bin"],
                },
            )
    finally:
        owner.close()


def test_defaults_keep_weights_that_are_not_duplicate_encodings() -> None:
    files = tuple(
        hub.HubFile(path, 1, "0" * 64, None)
        for path in (
            "config.json",
            "model.safetensors",
            "pytorch_model.bin",
            "rust_model.ot",
            "onnx/model.onnx",
            "voices/af.pt",
            "unet/diffusion_pytorch_model.bin",
            "unet/diffusion_pytorch_model.safetensors",
        )
    )
    pinned = hub.HubSnapshot(REPO, REVISION, files, None, False, None, None)
    assert pinned.default_selection() == [
        "config.json",
        "model.safetensors",
        "unet/diffusion_pytorch_model.safetensors",
        "voices/af.pt",
    ]


def test_a_legacy_listing_keeps_its_own_licence_over_hub_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime, timedelta

    from test_cards_proofs import body
    from test_local_owner import accepted_catalog

    from foxlight_model_preservation.cards import create_card, identity_json
    from foxlight_model_preservation.signing import encode_record
    from foxlight_model_preservation.storage import atomic_bytes, directory

    catalog, repo, _ = accepted_catalog(tmp_path, companion=False)
    legacy = create_card({**body(), "license": {"name": "listing-own-licence"}})
    repo.target = identity_json(
        {
            "cards": [legacy.model_dump(mode="json")],
            "card_metadata": {legacy.card_id: {"provenance": "community"}},
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
    pinned = hub.HubSnapshot(
        legacy.artifact.repository,
        legacy.artifact.revision,
        (hub.HubFile("model.safetensors", len(WEIGHTS), digest(WEIGHTS), None),),
        "apache-2.0",
        False,
        None,
        None,
    )
    monkeypatch.setattr(hub, "snapshot", lambda *a, **k: pinned)
    owner = LocalOwner(tmp_path / "owner", catalog=catalog)
    try:
        listing = owner.control("search", {})
        plan = owner.control(
            "plan",
            {
                "kind": "receive",
                "catalog_id": listing["catalog_id"],
                "card_id": legacy.card_id,
            },
        )
        assert plan["origin"] == "hub"
        assert plan["details"]["license"] == "listing-own-licence"
    finally:
        owner.close()


def _legacy_catalog(tmp_path: Path, card_body: dict):
    from datetime import UTC, datetime, timedelta

    from test_local_owner import accepted_catalog

    from foxlight_model_preservation.cards import create_card, identity_json
    from foxlight_model_preservation.signing import encode_record
    from foxlight_model_preservation.storage import atomic_bytes, directory

    catalog, repo, _ = accepted_catalog(tmp_path, companion=False)
    legacy = create_card(card_body)
    repo.target = identity_json(
        {
            "cards": [legacy.model_dump(mode="json")],
            "card_metadata": {legacy.card_id: {"provenance": "community"}},
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
    return catalog, legacy


def test_a_legacy_listing_with_a_companion_elsewhere_is_not_saveable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_cards_proofs import body

    catalog, legacy = _legacy_catalog(
        tmp_path, {**body(), "vision": {"weights_repo": "other/vision-tower"}}
    )
    asked: list[object] = []
    monkeypatch.setattr(hub, "snapshot", lambda *a, **k: asked.append(a))
    owner = LocalOwner(tmp_path / "owner", catalog=catalog)
    try:
        listing = owner.control("search", {})
        assert listing["results"][0]["download_support"] == "companion_adapter_required"
        with pytest.raises(PreservationError, match="companion_adapter_required"):
            owner.control(
                "plan",
                {
                    "kind": "receive",
                    "catalog_id": listing["catalog_id"],
                    "card_id": legacy.card_id,
                },
            )
        assert asked == []  # Hugging Face was never contacted
    finally:
        owner.close()


def test_a_legacy_listing_saves_the_projector_it_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_cards_proofs import body

    catalog, legacy = _legacy_catalog(
        tmp_path,
        {
            **body(),
            "gguf_file": "m-Q4_K_M.gguf",
            "vision": {"projector_file": "mmproj-F32.gguf"},
        },
    )
    pinned = hub.HubSnapshot(
        legacy.artifact.repository,
        legacy.artifact.revision,
        tuple(
            hub.HubFile(path, 10, "0" * 64, None)
            for path in ("m-Q4_K_M.gguf", "mmproj-F16.gguf", "mmproj-F32.gguf")
        ),
        None,
        False,
        None,
        None,
    )
    monkeypatch.setattr(hub, "snapshot", lambda *a, **k: pinned)
    owner = LocalOwner(tmp_path / "owner", catalog=catalog)
    try:
        listing = owner.control("search", {})
        assert listing["results"][0]["download_support"] == "ready"
        plan = owner.control(
            "plan",
            {
                "kind": "receive",
                "catalog_id": listing["catalog_id"],
                "card_id": legacy.card_id,
            },
        )
        assert sorted(plan["files"]) == ["m-Q4_K_M.gguf", "mmproj-F32.gguf"]
    finally:
        owner.close()


def test_a_hub_save_names_each_finishing_step_after_the_download(
    tmp_path: Path, fake_hub: FakeHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve_origin(monkeypatch, fake_hub)
    owner = LocalOwner(tmp_path / "owner")
    seen: list[object] = []

    def watched(name: str):
        original = getattr(owner_module, name)

        def call(*args, **kwargs):
            running = [job for job in owner.read()["jobs"] if job["state"] == "running"]
            stages = running[0]["stages"] if running else None
            seen.append(
                [(stage["name"], stage["state"]) for stage in stages]
                if stages
                else None
            )
            active = [stage for stage in stages or [] if stage["state"] == "active"]
            # Each stage's own work starts at zero, against a known total.
            assert active and active[0]["done"] == 0 and active[0]["total"] > 0
            return original(*args, **kwargs)

        monkeypatch.setattr(owner_module, name, call)

    for name in ("pack", "import_preservation", "verify_archive"):
        watched(name)
    try:
        owner.control("hub-consent", {"enabled": True})
        review = owner.control("hub-review", {"repository": REPO})
        plan = owner.control(
            "plan",
            {
                "kind": "receive",
                "origin": "hub",
                "repository": REPO,
                "revision": review["revision"],
                "files": review["default_files"],
            },
        )
        job = _finish(
            owner,
            owner.control(
                "start",
                {
                    "plan_id": plan["plan_id"],
                    "operation_id": str(uuid.uuid4()),
                    "confirmed": True,
                },
            ),
        )
        assert job["state"] == "succeeded", job
        assert job["stages"] is None
    finally:
        owner.close()
    order = ("download", "sign", "store", "check")

    def at(active: int) -> list[tuple[str, str]]:
        return [
            (name, "done" if i < active else "active" if i == active else "waiting")
            for i, name in enumerate(order)
        ]

    assert seen == [at(1), at(2), at(3)]


def test_a_download_without_kept_bytes_counts_what_arrived(tmp_path: Path) -> None:
    from foxlight_model_preservation.local_host.owner import Job
    from foxlight_model_preservation.operations import OperationControl

    owner = LocalOwner(tmp_path / "owner")
    try:
        job = Job(
            job_id="a" * 64,
            operation_id="op",
            plan_id="b" * 64,
            kind="source_receive",
            state="running",
            created_at=0.0,
            name="catalog model",
        )
        owner._progress[job.job_id] = OperationControl().progress.model_copy(
            update={"bytes_received": 5_000, "bytes_reused": 1_000}
        )
        stages = owner._stages_view(job)
        assert isinstance(stages, list)
        assert [stage["name"] for stage in stages] == ["download", "store", "check"]
        assert stages[0]["state"] == "active" and stages[0]["done"] == 6_000
        assert all(stage["state"] == "waiting" for stage in stages[1:])
    finally:
        owner.close()
