"""Explicit HTTP opt-out must not bind a hidden listener or expand restart grants."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from test_mirror_intake import local_grant, local_mirror

from foxlight_model_preservation.mirror import configure_http, run_mirror
from foxlight_model_preservation.mirror_cli import MirrorArguments, run_mirror_command
from foxlight_model_preservation.mirror_intake import retain_local
from foxlight_model_preservation.mirror_models import MirrorState
from foxlight_model_preservation.mirror_native import configure
from foxlight_model_preservation.mirror_state import (
    encode_state,
    grant_restart,
    policy_id,
    restart_allowed,
)
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationCancelled, OperationControl


def test_released_legacy_state_and_restart_identity_vectors():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/mirror-policy-v1-v3.json").read_bytes()
    )
    for record in fixture["records"]:
        state = MirrorState.model_validate_json(json.dumps(record["state"]))
        assert state.http_enabled
        assert json.loads(encode_state(state)) == record["state"]
        assert policy_id(state) == record["policy_id"]


def test_http_policy_is_offline_explicit_and_does_not_downgrade_local_or_native(
    tmp_path, capture
):
    ledger = local_mirror(tmp_path)
    grant_restart(ledger, int(time.time()) + 600)
    before = policy_id(ledger.read())
    configure_http(ledger, enabled=False)
    state = ledger.read()
    assert not state.http_enabled and state.schema_version == "foxlight.mirror-state.v4"
    assert state.restart is None and policy_id(state) != before
    assert not ledger.running()
    retain_local(ledger, capture.store, capture.identity, local_grant(capture))
    configure(ledger)
    assert ledger.read().schema_version == "foxlight.mirror-state.v4"
    assert not ledger.read().http_enabled
    with pytest.raises(PreservationError, match="at least one"):
        run_mirror(ledger, public=True)
    grant_restart(ledger, int(time.time()) + 600)
    assert restart_allowed(ledger.read())
    configure_http(ledger, enabled=True)
    assert ledger.read().restart is None and ledger.read().http_enabled
    invalid = ledger.read().model_dump(mode="json")
    invalid.update(schema_version="foxlight.mirror-state.v3", http_enabled=False)
    with pytest.raises(ValueError, match="requires state v4"):
        MirrorState.model_validate_json(json.dumps(invalid))


def test_startup_reads_policy_only_after_acquiring_owner_lock(tmp_path, monkeypatch):
    ledger = local_mirror(tmp_path)
    service_lock = ledger.service_lock

    def forbidden(*args, **kwargs):
        raise AssertionError("startup used policy read before owner lock")

    monkeypatch.setattr("foxlight_model_preservation.mirror.MirrorServer", forbidden)

    # Configuration needs the real lock, so replace the method only for startup.
    @contextmanager
    def startup_lock():
        monkeypatch.setattr(ledger, "service_lock", service_lock)
        # A completed configuration change before startup wins the owner lock.
        configure_http(ledger, enabled=False)
        with service_lock():
            yield

    monkeypatch.setattr(ledger, "service_lock", startup_lock)
    with pytest.raises(PreservationError, match="at least one"):
        run_mirror(ledger, public=True)
    assert not ledger.running() and not ledger.read().http_enabled


@pytest.mark.parametrize("enabled", [False, True])
def test_cli_first_receipt_uses_locked_policy_after_configuration_race(
    tmp_path, monkeypatch, capsys, enabled
):
    import foxlight_model_preservation.mirror_cli as cli

    ledger = local_mirror(tmp_path)
    configure_http(ledger, enabled=not enabled)
    monkeypatch.setattr(
        "foxlight_model_preservation.mirror_native.torrent_engine", lambda: None
    )
    configure(ledger, torrent=True)
    service_lock = ledger.service_lock

    @contextmanager
    def startup_lock():
        monkeypatch.setattr(ledger, "service_lock", service_lock)
        configure_http(ledger, enabled=enabled)
        with service_lock():
            yield

    monkeypatch.setattr(ledger, "service_lock", startup_lock)
    monkeypatch.setattr(cli, "MirrorLedger", lambda path: ledger)
    control = OperationControl()
    observation = cli._observation

    def observe(status):
        observation(status)
        if status.running:
            control.cancel()

    monkeypatch.setattr(cli, "_observation", observe)
    arguments = MirrorArguments(
        mirror_action="run",
        mirror_dir=ledger.path,
        public=True,
        resume=False,
        seconds=60,
        observe_mirror=True,
    )
    with pytest.raises(OperationCancelled):
        run_mirror_command(arguments, control)
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(records) == 3 and records[0]["ready"] is True
    if enabled:
        assert 1 <= records[0]["port"] <= 65535
        assert "http_enabled" not in records[0]  # Preserve released HTTP receipt.
    else:
        assert records[0]["port"] is None and records[0]["http_enabled"] is False
    assert records[1]["event"] == records[2]["event"] == "mirror_status"
    assert records[1]["status"]["http_port"] == records[0]["port"]
    assert records[2]["status"] == {
        "running": False,
        "http_port": None,
        "native_workers": 0,
    }
    assert not ledger.running()


@pytest.mark.parametrize("observer_failure", [False, True])
def test_native_only_owner_never_constructs_http_listener_and_drains_on_pause(
    tmp_path, capture, monkeypatch, observer_failure
):
    ledger = local_mirror(tmp_path)
    retain_local(ledger, capture.store, capture.identity, local_grant(capture))
    configure_http(ledger, enabled=False)
    monkeypatch.setattr(
        "foxlight_model_preservation.mirror_native.torrent_engine", lambda: None
    )
    configure(ledger, torrent=True, session_seconds=60)
    prepared, native_started, native_stopped = (threading.Event() for _ in range(3))
    failures = []
    observations = []

    def forbidden(*args, **kwargs):
        raise AssertionError("disabled HTTP transport attempted to create a listener")

    def native(ledger, identity, policy, control):
        native_started.set()
        try:
            while True:
                control.checkpoint()
                time.sleep(0.01)
        finally:
            native_stopped.set()

    monkeypatch.setattr("foxlight_model_preservation.mirror.MirrorServer", forbidden)
    monkeypatch.setattr("foxlight_model_preservation.mirror_native._torrent", native)
    grant_restart(ledger, int(time.time()) + 600)

    def observe(status):
        observations.append(status)
        if observer_failure and status.running and native_started.is_set():
            raise RuntimeError("observer failed")

    def run():
        try:
            run_mirror(
                ledger,
                resume=True,
                prepared=lambda _port: prepared.set(),
                ready=forbidden,
                observe=observe,
            )
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert prepared.wait(10) and native_started.wait(10)
        if not observer_failure:
            with pytest.raises(PreservationError, match="stop the active"):
                configure_http(ledger, enabled=True)
            assert not ledger.read().http_enabled
            ledger.update(lambda state: state.model_copy(update={"paused": True}))
        thread.join(15)
        assert not thread.is_alive()
        if observer_failure:
            assert len(failures) == 1 and str(failures[0]) == "observer failed"
        else:
            assert not failures
        assert native_stopped.is_set() and not ledger.running()
        assert ledger.read().models[capture.identity].state == "retained"
        assert (
            observations[-1].running is False and observations[-1].native_workers == 0
        )
        assert any(status.native_workers > 0 for status in observations)
        assert all(status.http_port is None for status in observations)
    finally:
        if thread.is_alive():
            ledger.update(lambda state: state.model_copy(update={"paused": True}))
            thread.join(30)


def test_cli_http_transport_requires_one_deliberate_choice(tmp_path):
    ledger = local_mirror(tmp_path)
    command = [
        sys.executable,
        "-m",
        "foxlight_model_preservation.cli",
        "mirror",
        "http",
        "--dir",
        str(ledger.path),
    ]
    denied = subprocess.run(command, capture_output=True, timeout=10)  # noqa: S603 - test CLI
    assert denied.returncode != 0 and ledger.read().http_enabled
    result = subprocess.run(  # noqa: S603 - test CLI
        [*command, "--disable"], capture_output=True, timeout=10
    )
    assert result.returncode == 0, result.stderr.decode()
    receipt = json.loads(result.stdout)
    assert not receipt["http_enabled"] and not receipt["network_started"]
    assert receipt["restart_grant_invalidated"] and not ledger.running()
