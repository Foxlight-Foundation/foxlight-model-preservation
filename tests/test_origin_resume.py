"""Hugging Face downloads resume from a verified prefix; saves report their sources."""

from __future__ import annotations

import io
import time

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


def _download(hub: Hub, destination, monkeypatch) -> None:
    monkeypatch.setattr(
        "foxlight_model_preservation.local_host.selection.PinnedConnection",
        hub.connection,
    )
    download_file(
        "example/model",
        "a" * 40,
        _file(),
        destination,
        OperationControl(),
        deadline=time.monotonic() + 30,
    )


def test_a_dropped_download_resumes_from_its_verified_prefix(tmp_path, monkeypatch):
    hub = Hub(DATA, drop_after=6000)
    destination = tmp_path / "model.bin"
    with pytest.raises(ConnectionResetError):
        _download(hub, destination, monkeypatch)
    assert not destination.exists()
    assert (tmp_path / "model.bin.partial").stat().st_size == 6000
    _download(hub, destination, monkeypatch)
    assert hub.requests[-1]["Range"] == "bytes=6000-"
    assert destination.read_bytes() == DATA
    assert not (tmp_path / "model.bin.partial").exists()


def test_an_origin_that_ignores_the_range_restarts_cleanly(tmp_path, monkeypatch):
    hub = Hub(DATA, drop_after=6000, honour_range=False)
    destination = tmp_path / "model.bin"
    with pytest.raises(ConnectionResetError):
        _download(hub, destination, monkeypatch)
    _download(hub, destination, monkeypatch)
    assert destination.read_bytes() == DATA


def test_a_corrupt_prefix_is_never_kept(tmp_path, monkeypatch):
    destination = tmp_path / "model.bin"
    (tmp_path / "model.bin.partial").write_bytes(b"\0" * 6000)
    with pytest.raises(PreservationError, match="differ from signed"):
        _download(Hub(DATA), destination, monkeypatch)
    assert not destination.exists()
    # The next attempt starts over rather than trusting the bad prefix again.
    assert not (tmp_path / "model.bin.partial").exists()
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
