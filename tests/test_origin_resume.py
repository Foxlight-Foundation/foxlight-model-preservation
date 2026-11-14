"""Large Hugging Face files keep resuming while they make progress."""

import time

import pytest

from foxlight_model_preservation.cards import ArtifactFile
from foxlight_model_preservation.local_host import owner as owner_module
from foxlight_model_preservation.local_host.owner import ORIGIN_STALLS, LocalOwner
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationControl

FILE = ArtifactFile(path="model.gguf", size_bytes=1000, object_id="sha256:" + "a" * 64)


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setattr(owner_module, "_pause", lambda *args: None)
    created = LocalOwner(tmp_path / "home")
    yield created
    created.close()


def save(owner, tmp_path):
    return owner._save_file(
        "org/model",
        "b" * 40,
        FILE,
        tmp_path / "work" / "model.gguf",
        tmp_path / "work" / ".staging",
        OperationControl(),
        deadline=time.monotonic() + 60,
    )


def test_drops_resume_until_the_file_completes(owner, tmp_path, monkeypatch):
    calls = []

    def flaky(repository, revision, file, destination, control, *, partial, **kwargs):
        calls.append(partial)
        partial.parent.mkdir(parents=True, exist_ok=True)
        with open(partial, "ab") as stream:
            stream.write(b"x" * 100)  # progress, then the connection drops
        if len(calls) < 7:
            raise ConnectionResetError("dropped")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"done")

    monkeypatch.setattr(owner_module, "download_file", flaky)
    assert save(owner, tmp_path) == "origin"
    assert len(calls) == 7  # far more than two attempts while progressing
    # The buffer lives in the library folder, keyed by the file's hash.
    assert calls[0] == owner._data() / "partials" / ("a" * 64 + ".partial")


def test_attempts_without_progress_give_up_after_a_few(owner, tmp_path, monkeypatch):
    calls = []

    def dead(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("no bytes")

    monkeypatch.setattr(owner_module, "download_file", dead)
    monkeypatch.setattr(
        owner,
        "_save_from_mirrors",
        lambda *args: (_ for _ in ()).throw(PreservationError("origin_unavailable")),
    )
    with pytest.raises(PreservationError, match="origin_unavailable"):
        save(owner, tmp_path)
    assert len(calls) == ORIGIN_STALLS


def test_a_failed_save_leaves_progress_for_the_next_save(owner, tmp_path, monkeypatch):
    seen = []

    def partial_then_fail(*args, partial, **kwargs):
        seen.append(partial.stat().st_size if partial.exists() else 0)
        partial.parent.mkdir(parents=True, exist_ok=True)
        if not seen[:-1]:
            partial.write_bytes(b"x" * 400)
        raise TimeoutError("drop")

    monkeypatch.setattr(owner_module, "download_file", partial_then_fail)
    monkeypatch.setattr(
        owner,
        "_save_from_mirrors",
        lambda *args: (_ for _ in ()).throw(PreservationError("origin_unavailable")),
    )
    with pytest.raises(PreservationError):
        save(owner, tmp_path)
    seen.clear()
    with pytest.raises(PreservationError):
        save(owner, tmp_path / "second-job")
    assert seen[0] == 400  # the next save starts from the kept bytes


def test_a_refused_file_goes_to_mirrors_at_once(owner, tmp_path, monkeypatch):
    calls = []

    def missing(*args, **kwargs):
        calls.append(1)
        raise owner_module.OriginHTTPError(404)

    monkeypatch.setattr(owner_module, "download_file", missing)
    monkeypatch.setattr(
        owner, "_save_from_mirrors", lambda *args: "https://mirror.example"
    )
    assert save(owner, tmp_path) == "https://mirror.example"
    assert len(calls) == 1
