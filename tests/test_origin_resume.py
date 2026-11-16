"""Hugging Face downloads resume from a verified prefix; saves report their sources."""

from __future__ import annotations

import io
import os
import time
from pathlib import Path

import pytest
from test_catalog_mirror_fallback import _save, _seeds, _store
from test_local_owner import accepted_catalog

from foxlight_model_preservation.cards import ArtifactFile
from foxlight_model_preservation.local_host.owner import LocalOwner
from foxlight_model_preservation.local_host.selection import (
    OriginHTTPError,
    download_file,
)
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationControl
from foxlight_model_preservation.signing import digest

DATA = bytes(range(256)) * 64  # 16 KiB


class Hub:
    """A fake origin: honours or ignores Range, can drop mid-transfer or refuse."""

    def __init__(
        self,
        data: bytes,
        *,
        drop_after: int | None = None,
        honour_range: bool = True,
        status: int = 200,
    ) -> None:
        self.data, self.drop_after = data, drop_after
        self.honour_range, self.status = honour_range, status
        self.requests: list[dict[str, str]] = []

    def connection(self, *args, **kwargs):
        hub = self

        class Reply(io.BytesIO):
            def __init__(self, headers: dict[str, str]) -> None:
                start = 0
                ranged = headers.get("Range")
                self.status = hub.status
                self.headers = {"Content-Length": str(len(hub.data))}
                if hub.status == 200 and ranged and hub.honour_range:
                    start = int(ranged.removeprefix("bytes=").rstrip("-"))
                    self.status = 206
                    size = len(hub.data)
                    self.headers = {
                        "Content-Length": str(size - start),
                        "Content-Range": f"bytes {start}-{size - 1}/{size}",
                    }
                super().__init__(hub.data[start:] if hub.status == 200 else b"")
                self.limit = hub.drop_after
                hub.drop_after = None  # Only the first transfer drops.

            def getheader(self, name, default=None):
                return self.headers.get(name, default)

            def read(self, size=-1):
                if self.limit is not None and self.tell() >= self.limit:
                    raise ConnectionResetError("dropped")
                if self.limit is not None:
                    size = min(size, self.limit - self.tell())
                return super().read(size)

        class Connection:
            def __init__(self, host, *a, **k):
                assert host == "huggingface.co"
                self.headers: dict[str, str] = {}

            def request(self, method, path, headers=None):
                self.headers = dict(headers or {})
                hub.requests.append(self.headers)

            def getresponse(self):
                return Reply(self.headers)

            def close(self):
                pass

        return Connection(*args, **kwargs)


def _file(data: bytes = DATA) -> ArtifactFile:
    return ArtifactFile(
        path="model.bin", size_bytes=len(data), object_id="sha256:" + digest(data)
    )


def _download(hub: Hub, destination, monkeypatch, *, data: bytes = DATA) -> None:
    monkeypatch.setattr(
        "foxlight_model_preservation.local_host.selection.PinnedConnection",
        hub.connection,
    )
    download_file(
        "example/model",
        "a" * 40,
        _file(data),
        destination,
        OperationControl(),
        deadline=time.monotonic() + 30,
        partial=destination.parent / "staging" / (digest(data) + ".partial"),
    )


def _partial(tmp_path, data: bytes = DATA):
    return tmp_path / "staging" / (digest(data) + ".partial")


def test_a_dropped_download_resumes_from_its_verified_prefix(tmp_path, monkeypatch):
    hub = Hub(DATA, drop_after=6000)
    destination = tmp_path / "model.bin"
    with pytest.raises(ConnectionResetError):
        _download(hub, destination, monkeypatch)
    assert not destination.exists()
    assert _partial(tmp_path).stat().st_size == 6000
    _download(hub, destination, monkeypatch)
    assert hub.requests[-1]["Range"] == "bytes=6000-"
    assert destination.read_bytes() == DATA
    assert not _partial(tmp_path).exists()


def test_an_origin_that_ignores_the_range_restarts_cleanly(tmp_path, monkeypatch):
    hub = Hub(DATA, drop_after=6000, honour_range=False)
    destination = tmp_path / "model.bin"
    with pytest.raises(ConnectionResetError):
        _download(hub, destination, monkeypatch)
    _download(hub, destination, monkeypatch)
    assert destination.read_bytes() == DATA


def test_a_corrupt_prefix_is_never_kept(tmp_path, monkeypatch):
    destination = tmp_path / "model.bin"
    _partial(tmp_path).parent.mkdir()
    _partial(tmp_path).write_bytes(b"\0" * 6000)
    with pytest.raises(PreservationError, match="differ from signed"):
        _download(Hub(DATA), destination, monkeypatch)
    assert not destination.exists()
    # The next attempt starts over rather than trusting the bad prefix again.
    assert not _partial(tmp_path).exists()
    _download(Hub(DATA), destination, monkeypatch)
    assert destination.read_bytes() == DATA


def test_a_refusal_carries_its_status(tmp_path, monkeypatch):
    with pytest.raises(OriginHTTPError) as raised:
        _download(Hub(DATA, status=404), tmp_path / "model.bin", monkeypatch)
    assert raised.value.status_code == 404


def _catalog_hub(tmp_path, monkeypatch, hub: Hub):
    catalog, repo, data = accepted_catalog(tmp_path, companion=False)
    hub.data = data
    monkeypatch.setattr(
        "foxlight_model_preservation.local_host.selection.PinnedConnection",
        hub.connection,
    )
    return catalog, repo, data


def test_a_save_retries_the_origin_once_and_reports_it(tmp_path, monkeypatch):
    hub = Hub(b"")
    catalog, repo, data = _catalog_hub(tmp_path, monkeypatch, hub)
    hub.drop_after = len(data) // 2
    owner = LocalOwner(tmp_path / "owner", catalog=catalog)
    try:
        job = _save(owner, repo)
        assert job["state"] == "succeeded", job
        assert hub.requests[-1]["Range"] == f"bytes={len(data) // 2}-"
        assert job["sources"] == {"origin_files": 1, "mirror_files": 0, "mirrors": []}
    finally:
        owner.close()


def test_a_refused_file_goes_to_mirrors_without_an_origin_retry(tmp_path, monkeypatch):
    hub = Hub(b"", status=404)
    catalog, repo, data = _catalog_hub(tmp_path, monkeypatch, hub)
    _seeds(monkeypatch, _store(tmp_path / "seed", data))
    owner = LocalOwner(tmp_path / "owner", catalog=catalog)
    try:
        job = _save(owner, repo)
        assert job["state"] == "succeeded", job
        assert len(hub.requests) == 1
        assert job["sources"]["origin_files"] == 0
        assert job["sources"]["mirror_files"] == 1
    finally:
        owner.close()


def test_a_listed_file_named_like_a_resume_buffer_is_left_alone(tmp_path, monkeypatch):
    """`model.bin.partial` and `model.bin` can both be selected files."""
    first, second = DATA[:5000], DATA
    _download(Hub(first), tmp_path / "model.bin.partial", monkeypatch, data=first)
    _download(Hub(second), tmp_path / "model.bin", monkeypatch, data=second)
    assert (tmp_path / "model.bin.partial").read_bytes() == first
    assert (tmp_path / "model.bin").read_bytes() == second


def _observed(tmp_path, monkeypatch, hub: Hub, *, cancel_after: int | None = None):
    """Download with a control that records (stage, hashed) and can stop midway."""
    seen: list[tuple[str, int]] = []
    control: OperationControl

    def observe(snapshot) -> None:
        seen.append((snapshot.stage, snapshot.bytes_hashed))
        if cancel_after is not None and snapshot.bytes_hashed >= cancel_after:
            control.cancel()

    control = OperationControl(observe, interval_seconds=0)
    monkeypatch.setattr(
        "foxlight_model_preservation.local_host.selection.PinnedConnection",
        hub.connection,
    )
    download_file(
        "example/model",
        "a" * 40,
        _file(),
        tmp_path / "model.bin",
        control,
        deadline=time.monotonic() + 30,
        partial=_partial(tmp_path),
    )
    return seen


DATA_BIG = bytes(range(256)) * 16384  # 4 MiB: re-read in several chunks


def test_a_complete_kept_download_is_placed_with_no_network(tmp_path, monkeypatch):
    _partial(tmp_path).parent.mkdir()
    _partial(tmp_path).write_bytes(DATA)
    hub = Hub(DATA)
    _download(hub, tmp_path / "model.bin", monkeypatch)
    assert hub.requests == []  # nothing fetched again
    assert (tmp_path / "model.bin").read_bytes() == DATA
    assert not _partial(tmp_path).exists()


def test_a_complete_kept_download_that_differs_is_fetched_again(tmp_path, monkeypatch):
    _partial(tmp_path).parent.mkdir()
    _partial(tmp_path).write_bytes(b"\0" * len(DATA))
    hub = Hub(DATA)
    _download(hub, tmp_path / "model.bin", monkeypatch)
    assert len(hub.requests) == 1 and "Range" not in hub.requests[0]
    assert (tmp_path / "model.bin").read_bytes() == DATA


def test_re_reading_a_kept_download_is_shown_as_resuming(tmp_path, monkeypatch):
    _partial(tmp_path).parent.mkdir()
    _partial(tmp_path).write_bytes(DATA[:6000])
    seen = _observed(tmp_path, monkeypatch, Hub(DATA))
    stages = [stage for stage, _ in seen]
    first = stages.index("resuming")
    back = stages.index("downloading", first)
    # Every kept byte is counted while resuming, then the rest downloads.
    assert seen[back - 1] == ("resuming", 6000)
    assert seen[-1][0] == "downloading"
    assert (tmp_path / "model.bin").read_bytes() == DATA


def test_stop_interrupts_the_re_read_of_a_large_kept_download(tmp_path, monkeypatch):
    global DATA
    original, DATA = DATA, DATA_BIG
    try:
        _partial(tmp_path).parent.mkdir()
        _partial(tmp_path).write_bytes(DATA[: 3 * 1024 * 1024])
        hub = Hub(DATA)
        with pytest.raises(PreservationError):
            _observed(tmp_path, monkeypatch, hub, cancel_after=1024 * 1024)
        assert hub.requests == []  # stopped before any network
        # The kept bytes stay for the next attempt.
        assert _partial(tmp_path).stat().st_size == 3 * 1024 * 1024
    finally:
        DATA = original


def test_an_empty_file_with_no_buffer_is_downloaded(tmp_path, monkeypatch):
    hub = Hub(b"")
    _download(hub, tmp_path / "model.bin", monkeypatch, data=b"")
    assert (tmp_path / "model.bin").read_bytes() == b""
    assert len(hub.requests) == 1


def test_an_empty_kept_buffer_goes_back_to_downloading(tmp_path, monkeypatch):
    _partial(tmp_path).parent.mkdir()
    _partial(tmp_path).write_bytes(b"")
    seen = _observed(tmp_path, monkeypatch, Hub(DATA))
    stages = [stage for stage, _ in seen]
    assert "resuming" in stages
    assert stages[-1] == "downloading"
    assert (tmp_path / "model.bin").read_bytes() == DATA


def test_progress_never_waits_for_a_slow_drive_to_size_the_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On exFAT under macOS, sizing a file being written waits for the write
    (seconds at a time); the window's progress requests must not."""
    import threading

    from foxlight_model_preservation.local_host import owner as module

    buffer = tmp_path / "file.partial"
    buffer.write_bytes(b"x" * 1000)
    release = threading.Event()
    reads: list[Path] = []

    def slow(path: Path) -> int:
        reads.append(path)
        release.wait(10)
        return os.stat(path).st_size

    monkeypatch.setattr(module, "_size", slow)
    sizes = module._BufferSizes()
    started = time.monotonic()
    # First sight: waits only briefly, then answers with nothing known yet.
    assert sizes.size(buffer) == 0
    # Asking again while the read is still waiting neither blocks nor piles up.
    assert sizes.size(buffer) == 0
    assert time.monotonic() - started < 2 and len(reads) == 1
    release.set()
    end = time.monotonic() + 5
    while sizes.size(buffer) != 1000 and time.monotonic() < end:
        time.sleep(0.01)
    assert sizes.size(buffer) == 1000
    # As the download grows, later requests catch up.
    buffer.write_bytes(b"x" * 2500)
    end = time.monotonic() + 5
    while sizes.size(buffer) != 2500 and time.monotonic() < end:
        time.sleep(0.01)
    assert sizes.size(buffer) == 2500
