"""Large Hugging Face files keep resuming while each attempt makes progress."""

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


def test_a_buffer_on_another_drive_is_not_shared(owner, tmp_path, monkeypatch):
    seen = []

    def once(repository, revision, file, destination, control, *, partial, **kwargs):
        seen.append(partial)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"done")

    monkeypatch.setattr(owner_module, "download_file", once)
    real = owner_module.os.stat

    def other_drive(path, *args, **kwargs):
        value = real(path, *args, **kwargs)
        if str(path).endswith("partials"):
            return type("Stat", (), {"st_dev": value.st_dev + 1})()
        return value

    monkeypatch.setattr(owner_module.os, "stat", other_drive)
    assert save(owner, tmp_path) == "origin"
    assert "partials" not in str(seen[0])  # job-local: same drive as the file


def test_a_kept_buffer_counts_as_progress_and_running_saves_report_size(
    owner, tmp_path, monkeypatch
):
    import uuid

    from foxlight_model_preservation.local_host.owner import Job
    from foxlight_model_preservation.storage import atomic_bytes

    kept = owner._data() / "partials" / ("a" * 64 + ".partial")
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_bytes(b"x" * 400)

    def finish(repository, revision, file, destination, control, *, partial, **kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"done")

    monkeypatch.setattr(owner_module, "download_file", finish)
    control = OperationControl()
    owner._save_file(
        "org/model",
        "b" * 40,
        FILE,
        tmp_path / "work" / "model.gguf",
        tmp_path / "work" / ".staging",
        control,
        deadline=time.monotonic() + 60,
    )
    assert control.progress.bytes_reused == 400

    job = Job(
        job_id="c" * 64,
        operation_id=str(uuid.uuid4()),
        plan_id="d" * 64,
        kind="receive",
        state="running",
        created_at=time.time(),
        name="big",
    )
    atomic_bytes(owner._root, "plans/" + "d" * 64 + ".json", b'{"size_bytes":30000}')
    assert owner._job_total(job) == 30000
    assert owner._job_total(job.model_copy(update={"state": "succeeded"})) is None


def test_progress_reads_what_is_kept(owner, tmp_path, monkeypatch):
    import uuid

    from foxlight_model_preservation.local_host.owner import Job

    job = Job(
        job_id="c" * 64,
        operation_id=str(uuid.uuid4()),
        plan_id="d" * 64,
        kind="receive",
        state="running",
        created_at=time.time(),
        name="big",
    )
    seen = []

    def restart(repository, revision, file, destination, control, *, partial, **kwargs):
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_bytes(b"x" * 300)  # 300 kept after a restart
        seen.append(owner._job_done(job))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"done")

    monkeypatch.setattr(owner_module, "download_file", restart)
    owner._kept[job.job_id] = (5000, None)  # earlier files of this save
    owner._save_file(
        "org/model",
        "b" * 40,
        FILE,
        tmp_path / "work" / "model.gguf",
        tmp_path / "work" / ".staging",
        OperationControl(),
        deadline=time.monotonic() + 60,
        job_id=job.job_id,
    )
    assert seen == [5300]
