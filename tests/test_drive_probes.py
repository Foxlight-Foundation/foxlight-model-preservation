"""A drive that stops answering never freezes saves or the Library screen."""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import pytest

from foxlight_model_preservation.local_host import library as library_module
from foxlight_model_preservation.local_host.owner import LocalOwner


@pytest.fixture
def stuck_drive(monkeypatch):
    """shutil.disk_usage blocks (as a lost FSKit request does) until released."""
    entered, release = threading.Event(), threading.Event()
    real = shutil.disk_usage
    calls: list[str] = []

    def disk_usage(path):
        calls.append(str(path))
        entered.set()
        release.wait(30)
        return real(path)

    monkeypatch.setattr(library_module.shutil, "disk_usage", disk_usage)
    monkeypatch.setattr(library_module, "PROBE_SECONDS", 0.2)
    yield entered, release, calls
    release.set()


def test_a_stuck_free_space_probe_reports_unknown_and_never_piles_up(
    tmp_path: Path, stuck_drive
) -> None:
    entered, release, calls = stuck_drive
    started = time.monotonic()
    assert library_module.free_space(tmp_path, 0.2) is None
    assert entered.is_set() and time.monotonic() - started < 2
    # Asking again while the first probe is stuck starts no second probe.
    assert library_module.free_space(tmp_path, 0.2) is None
    assert calls == [str(tmp_path)]
    release.set()
    for _ in range(50):
        value = library_module.free_space(tmp_path, 0.2)
        if value is not None:
            break
    assert isinstance(value, int) and value > 0


def test_a_stuck_drive_never_holds_the_owner_lock(tmp_path: Path, stuck_drive) -> None:
    entered, release, _calls = stuck_drive
    owner = LocalOwner(tmp_path / "owner")
    try:
        views: list[dict] = []
        reader = threading.Thread(target=lambda: views.append(owner.read()))
        reader.start()
        assert entered.wait(10)
        # The screen's refresh is waiting on the drive, not holding the lock
        # a running save needs to report its progress.
        assert owner._lock.acquire(timeout=1)
        owner._lock.release()
        reader.join(10)
        assert views and views[0]["library"]["free_bytes"] is None
    finally:
        release.set()
        owner.close()
