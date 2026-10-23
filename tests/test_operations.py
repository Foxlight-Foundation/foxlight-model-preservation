"""Worker controls, read-only plans and restart-safe cumulative allowances."""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import Capture

from foxlight_model_preservation.ledger import read_transfer_receipt, receipt_path
from foxlight_model_preservation.operations import (
    BudgetExceeded,
    DeadlineExceeded,
    OperationCancelled,
    OperationControl,
    Progress,
    RequestChanged,
)
from foxlight_model_preservation.signing import encode_record
from foxlight_model_preservation.storage import (
    CHUNK_SIZE,
    manifest_path,
    object_path,
    pack,
    plan_pack,
    plan_restore,
    restore,
    unique_objects,
    verify_store,
)
from foxlight_model_preservation.transfer import TransferPolicy, fetch, plan_fetch


def invoke(kind: str, capture: Capture, target: Path, control: OperationControl):
    if kind == "pack":
        return pack(
            capture.recipe, capture.sources, target, (capture.key,), control=control
        )
    if kind == "verify":
        return verify_store(
            capture.store, capture.identity, capture.trust, control=control
        )
    if kind == "restore":
        return restore(
            capture.store, capture.identity, capture.trust, target, control=control
        )
    if kind == "fetch":
        return fetch(
            capture.identity,
            capture.trust,
            (str(capture.store),),
            target,
            control=control,
        )
    if kind == "plan_pack":
        return plan_pack(capture.recipe, capture.sources, target, control=control)
    if kind == "plan_restore":
        return plan_restore(
            capture.store, capture.identity, capture.trust, target, control=control
        )
    return plan_fetch(
        capture.identity, capture.trust, (str(capture.store),), target, control=control
    )


@pytest.mark.parametrize(
    "kind",
    ["pack", "verify", "restore", "fetch", "plan_pack", "plan_restore", "plan_fetch"],
)
def test_cancel_before_effects(capture: Capture, tmp_path: Path, kind: str) -> None:
    target = tmp_path / "absent" / "output"
    control = OperationControl()
    control.cancel()
    with pytest.raises(OperationCancelled):
        invoke(kind, capture, target, control)
    assert not target.parent.exists()
    assert control.progress.stage == "cancelled"
    assert control.progress.error_code == "cancelled"
    assert not control.progress.committed
    verify_store(capture.store, capture.identity, capture.trust)


@pytest.mark.parametrize("kind", ["pack", "verify", "restore", "fetch"])
def test_bounded_monotonic_progress(
    capture: Capture, tmp_path: Path, kind: str
) -> None:
    events: list[Progress] = []
    control = OperationControl(events.append, interval_seconds=0)
    invoke(kind, capture, tmp_path / kind, control)
    assert events[-1] == control.progress
    assert events[-1].stage == "complete"
    assert events[-1].committed == (kind != "verify")
    assert (
        sum(event.stage in {"complete", "cancelled", "failed"} for event in events) == 1
    )
    for counter in ("bytes_hashed", "bytes_copied", "bytes_received", "bytes_reused"):
        values = [getattr(event, counter) for event in events]
        assert values == sorted(values)
    assert events[-1].bytes_hashed > 0
    for event in events:
        encoded = event.model_dump_json()
        assert len(encoded) < 1024
        assert str(tmp_path) not in encoded
        assert "origin_repository" not in encoded


@pytest.mark.parametrize("kind", ["pack", "restore", "fetch"])
def test_cancel_at_publication_keeps_completion_absent(
    capture: Capture,
    tmp_path: Path,
    kind: str,
) -> None:
    def observe(progress: Progress) -> None:
        if progress.stage == "publishing":
            control.cancel()

    control = OperationControl(observe, interval_seconds=0)
    target = tmp_path / "cancelled"
    with pytest.raises(OperationCancelled):
        invoke(kind, capture, target, control)
    if kind == "restore":
        assert not target.exists()
        assert not list(tmp_path.glob(".restore-*"))
    else:
        assert not (target / manifest_path(capture.identity)).exists()
    assert control.progress.stage == "cancelled"
    assert not control.progress.committed
    verify_store(capture.store, capture.identity, capture.trust)


@pytest.mark.parametrize("kind", ["pack", "restore", "fetch"])
def test_commit_wins_late_cancel(capture: Capture, tmp_path: Path, kind: str) -> None:
    def observe(progress: Progress) -> None:
        if progress.stage == "complete":
            control.cancel()

    control = OperationControl(observe, interval_seconds=0)
    invoke(kind, capture, tmp_path / "complete", control)
    assert control.progress.stage == "complete"
    assert control.progress.committed


def test_cancel_copy_cleans_own_temporary_and_preserves_previous_capture(
    capture: Capture,
) -> None:
    (capture.sources["weights"] / "model.bin").write_bytes(b"changed" * 2000)

    def observe(progress: Progress) -> None:
        if progress.bytes_copied:
            control.cancel()

    control = OperationControl(observe, interval_seconds=0)
    with pytest.raises(OperationCancelled):
        pack(
            capture.recipe,
            capture.sources,
            capture.store,
            (capture.key,),
            control=control,
        )
    assert not list((capture.store / ".pending").iterdir())
    assert len(list((capture.store / "manifests").iterdir())) == 1
    verify_store(capture.store, capture.identity, capture.trust)


def test_cancel_hash_is_not_object_corruption(capture: Capture, tmp_path: Path) -> None:
    def observe(progress: Progress) -> None:
        if progress.bytes_hashed:
            control.cancel()

    control = OperationControl(observe, interval_seconds=0)
    with pytest.raises(OperationCancelled):
        fetch(
            capture.identity,
            capture.trust,
            (str(capture.store),),
            capture.store,
            control=control,
        )
    assert control.progress.bytes_received == 0
    verify_store(capture.store, capture.identity, capture.trust)


def test_observer_failure_does_not_obscure_commit(
    capture: Capture, tmp_path: Path
) -> None:
    def broken(_progress: Progress) -> None:
        raise RuntimeError("private observer details")

    control = OperationControl(broken)
    invoke("fetch", capture, tmp_path / "complete", control)
    assert control.progress.observer_failed
    assert control.progress.committed
    assert control.progress.stage == "complete"
    assert "private" not in control.progress.model_dump_json()


def test_control_single_use_and_deadline(capture: Capture, tmp_path: Path) -> None:
    control = OperationControl()
    control.limit_deadline(time.monotonic() - 1)
    with pytest.raises(DeadlineExceeded):
        invoke("fetch", capture, tmp_path / "absent", control)
    assert control.progress.error_code == "deadline_exceeded"
    assert not (tmp_path / "absent").exists()
    with pytest.raises(ValueError, match="fresh control"):
        invoke("fetch", capture, tmp_path / "absent", control)


def test_plans_are_read_only_and_recheck_sources(
    capture: Capture, tmp_path: Path
) -> None:
    absent = tmp_path / "absent" / "store"
    manifest = verify_store(capture.store, capture.identity, capture.trust)
    unique = sum(file.size for file in unique_objects(manifest))
    for plan in (
        plan_pack(capture.recipe, capture.sources, absent),
        plan_restore(capture.store, capture.identity, capture.trust, absent),
        plan_fetch(capture.identity, capture.trust, (str(capture.store),), absent),
    ):
        assert not absent.parent.exists()
        assert plan.manifest_id == capture.identity
        assert plan.logical_bytes == manifest.total_bytes
        assert plan.unique_bytes == unique
        assert plan.reused_bytes == plan.partial_bytes == 0
        assert len(plan.plan_id) == len(plan.request_digest) == 64
    plan = plan_pack(capture.recipe, capture.sources, absent)
    (capture.sources["weights"] / "model.bin").write_bytes(b"changed")
    with pytest.raises(RequestChanged):
        pack(
            capture.recipe,
            capture.sources,
            absent,
            (capture.key,),
            expected_manifest_id=plan.manifest_id,
        )
    assert not absent.parent.exists()


def test_plan_distinguishes_verified_reuse_from_partial(
    capture: Capture,
    tmp_path: Path,
) -> None:
    target = tmp_path / "partial"
    manifest = verify_store(capture.store, capture.identity, capture.trust)
    first, notice = manifest.members[0].files
    path = target / object_path(notice.sha256)
    path.parent.mkdir(parents=True)
    path.write_bytes((capture.store / object_path(notice.sha256)).read_bytes())
    partial = target / ".partial" / first.sha256
    partial.parent.mkdir()
    partial.write_bytes(b"untrusted prefix")
    before = {
        str(p.relative_to(target)): p.read_bytes()
        for p in target.rglob("*")
        if p.is_file()
    }
    plan = plan_fetch(capture.identity, capture.trust, (str(capture.store),), target)
    assert plan.reused_bytes == notice.size
    assert plan.partial_bytes == len(b"untrusted prefix")
    assert plan.additional_bytes == plan.unique_bytes - notice.size - plan.partial_bytes
    after = {
        str(p.relative_to(target)): p.read_bytes()
        for p in target.rglob("*")
        if p.is_file()
    }
    assert before == after
    assert not (target / ".write.lock").exists()


def test_cancelled_transfer_budget_does_not_refill(
    capture: Capture, tmp_path: Path
) -> None:
    manifest = verify_store(capture.store, capture.identity, capture.trust)
    cap = sum(file.size for file in unique_objects(manifest))
    policy = TransferPolicy(max_bytes=cap)
    target = tmp_path / "budget"

    def observe(progress: Progress) -> None:
        if progress.bytes_received:
            control.cancel()

    control = OperationControl(observe, interval_seconds=0)
    with pytest.raises(OperationCancelled):
        fetch(
            capture.identity,
            capture.trust,
            (str(capture.store),),
            target,
            policy,
            operation_id="retained",
            control=control,
        )
    initial = read_transfer_receipt(target, "retained")
    assert initial.charged_bytes == cap
    with pytest.raises(BudgetExceeded):
        fetch(
            capture.identity,
            capture.trust,
            (str(capture.store),),
            target,
            policy,
            operation_id="retained",
        )
    assert read_transfer_receipt(target, "retained") == initial
    assert not (target / manifest_path(capture.identity)).exists()
    # A deliberately new operation is a new allowance; it is never created implicitly.
    fetch(
        capture.identity,
        capture.trust,
        (str(capture.store),),
        target,
        policy,
        operation_id="explicit_new_allowance",
    )
    verify_store(target, capture.identity, capture.trust)


@pytest.mark.parametrize("changed", ["sources", "policy", "trust"])
def test_operation_identity_is_bound_to_inputs(
    capture: Capture,
    tmp_path: Path,
    changed: str,
) -> None:
    target = tmp_path / "bound"
    sources = (str(capture.store),)
    policy = TransferPolicy()
    fetch(
        capture.identity, capture.trust, sources, target, policy, operation_id="bound"
    )
    prior = read_transfer_receipt(target, "bound")
    trust = capture.trust
    if changed == "sources":
        sources = (str(capture.store), str(capture.store))
    elif changed == "policy":
        policy = TransferPolicy(max_bytes=policy.max_bytes + 1)
    else:
        trust = trust.model_copy(update={"as_of": "2026-10-19T01:00:00Z"})
    with pytest.raises(RequestChanged):
        fetch(capture.identity, trust, sources, target, policy, operation_id="bound")
    assert read_transfer_receipt(target, "bound") == prior
    verify_store(target, capture.identity, capture.trust)


def test_corrupt_accounting_never_resets(capture: Capture, tmp_path: Path) -> None:
    target = tmp_path / "corrupt"
    fetch(
        capture.identity,
        capture.trust,
        (str(capture.store),),
        target,
        operation_id="broken",
    )
    ledger = target / receipt_path("broken")
    ledger.write_bytes(b'{"operation_id":"broken","operation_id":"other"}')
    with pytest.raises(ValueError):
        fetch(
            capture.identity,
            capture.trust,
            (str(capture.store),),
            target,
            operation_id="broken",
        )
    assert ledger.read_bytes().count(b"operation_id") == 2


def test_process_crash_retains_budget_and_resumes(
    capture: Capture, tmp_path: Path
) -> None:
    (capture.sources["weights"] / "model.bin").write_bytes(b"x" * (2 * CHUNK_SIZE + 33))
    identity = pack(capture.recipe, capture.sources, capture.store, (capture.key,))
    trust_file = tmp_path / "trust.json"
    trust_file.write_bytes(encode_record(capture.trust))
    target = tmp_path / "crashed"
    cap = 5 * CHUNK_SIZE
    script = """
import os, sys
from pathlib import Path
from foxlight_model_preservation.operations import OperationControl
from foxlight_model_preservation.signing import load_trust
from foxlight_model_preservation.transfer import fetch, TransferPolicy

def observe(progress):
    if progress.bytes_received >= 2 * 65536:
        os._exit(73)

fetch(sys.argv[1], load_trust(Path(sys.argv[2]).read_bytes()), (sys.argv[3],),
      Path(sys.argv[4]), TransferPolicy(max_bytes=int(sys.argv[5])),
      operation_id="crash_test", control=OperationControl(observe, interval_seconds=0))
"""
    crashed = subprocess.run(  # noqa: S603 - fixed interpreter, no shell
        [
            sys.executable,
            "-c",
            script,
            identity,
            str(trust_file),
            str(capture.store),
            str(target),
            str(cap),
        ],
        check=False,
        timeout=15,
    )
    assert crashed.returncode == 73
    initial = read_transfer_receipt(target, "crash_test")
    assert initial.charged_bytes >= 65536
    assert any(path.stat().st_size >= 65536 for path in (target / ".partial").iterdir())
    assert not (target / manifest_path(identity)).exists()
    resumed = subprocess.run(  # noqa: S603 - fixed CLI, no shell
        [
            sys.executable,
            "-m",
            "foxlight_model_preservation.cli",
            "fetch",
            identity,
            "--trust",
            str(trust_file),
            "--mirror",
            str(capture.store),
            "--store",
            str(target),
            "--max-bytes",
            str(cap),
            "--operation-id",
            "crash_test",
            "--progress",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert resumed.returncode == 0, resumed.stderr
    assert json.loads(resumed.stdout)["manifest"] == identity
    assert json.loads(resumed.stderr.splitlines()[-1])["stage"] == "complete"
    final = read_transfer_receipt(target, "crash_test")
    assert initial.charged_bytes <= final.charged_bytes <= cap
    verify_store(target, identity, capture.trust)


def test_cli_read_only_plan(capture: Capture, tmp_path: Path) -> None:
    trust_file = tmp_path / "trust.json"
    trust_file.write_bytes(encode_record(capture.trust))
    target = tmp_path / "absent"
    result = subprocess.run(  # noqa: S603 - fixed CLI, no shell
        [
            sys.executable,
            "-m",
            "foxlight_model_preservation.cli",
            "plan-fetch",
            capture.identity,
            "--trust",
            str(trust_file),
            "--mirror",
            str(capture.store),
            "--store",
            str(target),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["manifest_id"] == capture.identity
    assert not target.exists()


def test_remote_plan_requests_only_metadata(capture: Capture, tmp_path: Path) -> None:
    import http.server
    import threading

    calls = []
    envelope = (capture.store / manifest_path(capture.identity)).read_bytes()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            calls.append(self.path)
            self.send_response(
                200 if self.path == "/" + manifest_path(capture.identity) else 404
            )
            self.send_header("Content-Length", str(len(envelope)))
            self.end_headers()
            self.wfile.write(envelope)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    target = tmp_path / "untouched"
    try:
        plan_fetch(
            capture.identity,
            capture.trust,
            (f"http://127.0.0.1:{server.server_port}",),
            target,
            TransferPolicy(allow_http=True, allow_private=True),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert calls == ["/" + manifest_path(capture.identity)]
    assert not target.exists()


def test_partial_capacity_uses_additional_space(
    capture: Capture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil

    manifest = verify_store(capture.store, capture.identity, capture.trust)
    files = unique_objects(manifest)
    target = tmp_path / "partial"
    (target / ".partial").mkdir(parents=True)
    for file in files:
        data = (capture.store / object_path(file.sha256)).read_bytes()
        (target / ".partial" / file.sha256).write_bytes(data[:-1])
    available = shutil.disk_usage(tmp_path)._replace(free=16 * CHUNK_SIZE + len(files))
    monkeypatch.setattr(shutil, "disk_usage", lambda _: available)
    fetch(capture.identity, capture.trust, (str(capture.store),), target)
    verify_store(target, capture.identity, capture.trust)


def test_reservation_failure_precedes_object_read(
    capture: Capture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import foxlight_model_preservation.ledger as ledger_module

    original = ledger_module.atomic_bytes

    def fail_reservation(root: int, path: str, data: bytes) -> None:
        if json.loads(data)["charged_bytes"]:
            raise OSError("simulated full disk")
        original(root, path, data)

    monkeypatch.setattr(ledger_module, "atomic_bytes", fail_reservation)
    control = OperationControl()
    target = tmp_path / "no-read"
    with pytest.raises(ValueError):
        fetch(
            capture.identity,
            capture.trust,
            (str(capture.store),),
            target,
            operation_id="reservation",
            control=control,
        )
    assert control.progress.bytes_received == 0
    assert read_transfer_receipt(target, "reservation").charged_bytes == 0
    assert not (target / manifest_path(capture.identity)).exists()


def test_receipt_symlink_is_not_followed(capture: Capture, tmp_path: Path) -> None:
    target = tmp_path / "symlink"
    fetch(
        capture.identity,
        capture.trust,
        (str(capture.store),),
        target,
        operation_id="protected",
    )
    receipt = target / receipt_path("protected")
    outside = tmp_path / "outside"
    outside.write_bytes(receipt.read_bytes())
    receipt.unlink()
    receipt.symlink_to(outside)
    before = outside.read_bytes()
    with pytest.raises(OSError):
        fetch(
            capture.identity,
            capture.trust,
            (str(capture.store),),
            target,
            operation_id="protected",
        )
    assert outside.read_bytes() == before


def test_forked_controls_share_stop_but_each_owns_one_invocation():
    parent = OperationControl()
    first, second = parent.fork(), parent.fork()
    with first.operation("fetch"):
        pass
    with pytest.raises(ValueError, match="fresh control"):
        with first.operation("fetch"):
            pass
    with pytest.raises(OperationCancelled):
        with second.operation("fetch"):
            parent.cancel()
            second.checkpoint()
    assert second.progress.stage == "cancelled"
    with pytest.raises(OperationCancelled):
        parent.fork().checkpoint()
