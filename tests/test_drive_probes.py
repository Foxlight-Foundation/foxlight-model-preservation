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
    monkeypatch.setattr(library_module, "_probing", {})
    monkeypatch.setattr(library_module, "_probed", {})
    yield entered, release, calls
    release.set()


def test_a_stuck_free_space_probe_reports_unknown_and_never_piles_up(
    tmp_path: Path, stuck_drive
) -> None:
    entered, release, calls = stuck_drive
    started = time.monotonic()
    assert library_module.free_space(tmp_path, 0.2) is None
    assert entered.is_set() and time.monotonic() - started < 2
    # Asking again while the first probe is stuck starts no second probe and
    # doesn't wait: the screen keeps refreshing.
    started = time.monotonic()
    assert library_module.free_space(tmp_path, 5) is None
    assert time.monotonic() - started < 0.5
    assert calls == [str(tmp_path)]
    release.set()
    for _ in range(50):
        value = library_module.free_space(tmp_path, 0.2)
        if value is not None:
            break
        time.sleep(0.05)
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


def test_a_probe_in_flight_reports_the_last_recent_answer(tmp_path: Path) -> None:
    library_module._probing.clear()
    library_module._probed.clear()
    first = library_module.free_space(tmp_path)
    assert isinstance(first, int)
    key = str(tmp_path)
    library_module._probing[key] = threading.Thread()
    try:
        assert library_module.free_space(tmp_path) == first
        library_module._probed[key] = (first, time.monotonic() - 120)
        assert library_module.free_space(tmp_path) is None
    finally:
        library_module._probing.pop(key, None)


def test_a_space_check_waits_for_a_probe_already_in_flight(
    tmp_path: Path, monkeypatch
) -> None:
    """A screen refresh may answer from its cache; enforcing space never does."""
    monkeypatch.setattr(library_module, "_probing", {})
    monkeypatch.setattr(library_module, "_probed", {})
    entered, release = threading.Event(), threading.Event()

    def slow(_path):
        entered.set()
        release.wait(10)
        return shutil._ntuple_diskusage(1000, 999, 1)

    monkeypatch.setattr(library_module.shutil, "disk_usage", slow)
    background = threading.Thread(target=library_module.free_space, args=(tmp_path, 5))
    background.start()
    assert entered.wait(5)
    # A refresh doesn't wait: nothing recent is known yet.
    assert library_module.free_space(tmp_path) is None
    answers: list[int | None] = []
    check = threading.Thread(
        target=lambda: answers.append(library_module.free_space(tmp_path, 5, wait=True))
    )
    check.start()
    time.sleep(0.1)
    release.set()
    check.join(5)
    background.join(5)
    assert answers == [1]
