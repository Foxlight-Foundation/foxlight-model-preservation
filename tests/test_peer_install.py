"""Acquisition trust, atomic visibility and local-only runtime selection."""

import hashlib
import io
import os
import stat
import subprocess
import tarfile
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from foxlight_model_preservation import peer_install
from foxlight_model_preservation.cli import argument_parser
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationCancelled, OperationControl
from foxlight_model_preservation.peer_install import (
    RuntimeArtifact,
    RuntimePlan,
    install_runtime,
    managed_executable,
    runtime_plan,
)
from foxlight_model_preservation.storage import directory, writer_lock


@pytest.fixture
def artifact(tmp_path, monkeypatch):
    binary = b"fixture executable that must never run"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        entry = tarfile.TarInfo("kubo/ipfs")
        entry.size = len(binary)
        bundle.addfile(entry, io.BytesIO(binary))
        ignored = tarfile.TarInfo("../../unselected")
        ignored.size = 4
        bundle.addfile(ignored, io.BytesIO(b"nope"))
    data = buffer.getvalue()
    archive = tmp_path / "fixture.tar.gz"
    archive.write_bytes(data)
    plan = RuntimePlan(
        version="0.43.1",
        target="test-arm64",
        filename="test.tar.gz",
        artifact=RuntimeArtifact(
            archive_sha512=hashlib.sha512(data).hexdigest(),
            archive_bytes=len(data),
            executable_sha256=hashlib.sha256(binary).hexdigest(),
            executable_bytes=len(binary),
        ),
        download_urls=("https://dist.ipfs.tech/fixture", "https://github.com/fixture"),
    )
    monkeypatch.setattr(peer_install, "runtime_plan", lambda: plan)
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **kw: pytest.fail("installer executed a child")
    )
    return archive, tmp_path / "runtime", plan


def test_plan_offline_platforms(monkeypatch):
    for system in ("Darwin", "Linux"):
        for architecture in ("arm64", "aarch64", "x86_64", "amd64"):
            monkeypatch.setattr(
                peer_install.platform, "system", lambda selected=system: selected
            )
            monkeypatch.setattr(
                peer_install.platform, "machine", lambda selected=architecture: selected
            )
            plan = runtime_plan()
            assert plan.network_requests == 0
            assert plan.artifact.archive_bytes < 128 * 1024**2
    monkeypatch.setattr(peer_install.platform, "system", lambda: "Windows")
    with pytest.raises(PreservationError, match="supports"):
        runtime_plan()


def test_offline_atomic_reusable_without_extraction_or_execution(artifact):
    archive, root, _ = artifact
    assert managed_executable(root) is None
    binary = Path(install_runtime(root, archive=archive))
    assert binary.read_bytes() == b"fixture executable that must never run"
    assert stat.S_IMODE(binary.stat().st_mode) == 0o700
    assert (binary.parent / "NOTICE.txt").is_file()
    assert not (root.parent / "unselected").exists()
    assert not list(root.glob(".native-pending-*"))
    archive.unlink()
    assert install_runtime(root, archive=archive) == str(binary)


def test_grant_required_before_creating_storage(artifact):
    _, root, _ = artifact
    with pytest.raises(PreservationError, match="permission"):
        install_runtime(root)
    assert not root.exists()


def test_concurrent_installation_refuses_under_kernel_lock(artifact):
    archive, root, _ = artifact
    with directory(root, create=True) as descriptor, writer_lock(descriptor):
        with pytest.raises(PreservationError, match="another writer"):
            install_runtime(root, archive=archive)
    assert managed_executable(root) is None


@pytest.mark.parametrize("kind", ["duplicate", "symlink", "wrong-binary"])
def test_bad_executable_entries_cannot_publish(artifact, monkeypatch, kind):
    archive, root, plan = artifact
    with tarfile.open(archive, "w:gz") as bundle:
        entry = tarfile.TarInfo("kubo/ipfs")
        binary = b"fixture executable that must never run"
        entry.size = len(binary)
        if kind == "symlink":
            entry.type = tarfile.SYMTYPE
            entry.linkname = str(root.parent / "other")
            entry.size = 0
            bundle.addfile(entry)
        else:
            bundle.addfile(
                entry, io.BytesIO(binary if kind == "duplicate" else b"X" * len(binary))
            )
            if kind == "duplicate":
                bundle.addfile(entry, io.BytesIO(binary))
    data = archive.read_bytes()
    artifact_pin = plan.artifact.model_copy(
        update={
            "archive_bytes": len(data),
            "archive_sha512": hashlib.sha512(data).hexdigest(),
        }
    )
    monkeypatch.setattr(
        peer_install,
        "runtime_plan",
        lambda: plan.model_copy(update={"artifact": artifact_pin}),
    )
    with pytest.raises(PreservationError, match="executable"):
        install_runtime(root, archive=archive)
    assert managed_executable(root) is None


@pytest.mark.parametrize("damage", ["truncated", "extra", "tampered"])
def test_archive_tampering_never_publishes(artifact, damage):
    archive, root, _ = artifact
    data = archive.read_bytes()
    archive.write_bytes(
        {"truncated": data[:-1], "extra": data + b"!", "tampered": b"!" + data[1:]}[
            damage
        ]
    )
    with pytest.raises(PreservationError, match="length|checksum"):
        install_runtime(root, archive=archive)
    assert managed_executable(root) is None
    assert not list(root.glob(".native-pending-*"))


def test_cancellation_during_copy_leaves_no_generation(artifact):
    archive, root, _ = artifact
    control = OperationControl(interval_seconds=0, observer=lambda _: control.cancel())
    with pytest.raises(OperationCancelled):
        install_runtime(root, archive=archive, control=control)
    assert managed_executable(root) is None
    assert not list(root.glob(".native-pending-*"))


@pytest.mark.parametrize("damage", ["bytes", "permissions", "link"])
def test_installed_tampering_refuses_before_execution(artifact, damage):
    archive, root, _ = artifact
    binary = Path(install_runtime(root, archive=archive))
    if damage == "bytes":
        binary.write_bytes(b"changed")
    elif damage == "permissions":
        binary.chmod(0o755)
    else:
        binary.unlink()
        binary.symlink_to(archive)
    with pytest.raises((PreservationError, OSError)):
        managed_executable(root)


def test_private_storage_and_offline_archive_links_refuse(artifact):
    archive, root, _ = artifact
    root.mkdir(mode=0o755)
    with pytest.raises(PreservationError, match="private"):
        install_runtime(root, archive=archive)
    root.chmod(0o700)
    link = archive.with_suffix(".link")
    link.symlink_to(archive)
    with pytest.raises(OSError):
        install_runtime(root, archive=link)
    assert managed_executable(root) is None


def test_network_origin_fallback_and_installed_reuse(artifact, monkeypatch):
    archive, root, plan = artifact
    calls = []

    @contextmanager
    def response(url, end):
        calls.append(url)
        assert end > time.monotonic()
        if len(calls) == 1:
            raise peer_install._DownloadUnavailable("offline")
        with archive.open("rb") as stream:
            yield stream

    monkeypatch.setattr(peer_install, "_response", response)
    result = install_runtime(root, allow_download=True)
    assert calls == list(plan.download_urls)
    assert install_runtime(root, allow_download=True) == result
    assert len(calls) == 2


def test_bad_download_does_not_fall_back(artifact, monkeypatch):
    _, root, _ = artifact
    calls = []

    @contextmanager
    def response(url, end):
        calls.append(url)
        yield io.BytesIO(b"invalid")

    monkeypatch.setattr(peer_install, "_response", response)
    with pytest.raises(PreservationError, match="checksum"):
        install_runtime(root, allow_download=True)
    assert len(calls) == 1
    assert managed_executable(root) is None


def test_redirect_refuses_arbitrary_hosts_before_connection(monkeypatch):
    monkeypatch.setattr(
        peer_install,
        "PinnedConnection",
        lambda *a, **kw: pytest.fail("unexpected socket"),
    )
    for url in (
        "http://github.com/a",
        "https://evil.example/a",
        "https://github.com:444/a",
        "https://user@github.com/a",
    ):
        with pytest.raises(PreservationError, match="origins"):
            with peer_install._response(url, time.monotonic() + 10):
                pytest.fail("unexpected response")


def test_cli_install_needs_no_repo_or_peer_grant():
    parsed = argument_parser().parse_args(["peer-runtime-plan"])
    assert parsed.command == "peer-runtime-plan"
    parsed = argument_parser().parse_args(
        ["peer-runtime-install", "--allow-runtime-download"]
    )
    assert parsed.allow_runtime_download
    assert not hasattr(parsed, "peer_repo")


def test_real_offline_archive_when_available(tmp_path):
    selected = os.environ.get("KUBO_ARCHIVE")
    if selected is None:
        pytest.skip("set KUBO_ARCHIVE for actual pinned native acquisition")
    binary = install_runtime(tmp_path / "runtime", archive=Path(selected))
    output = subprocess.check_output([binary, "version", "--number"], timeout=10)  # noqa: S603
    assert output.strip() == b"0.43.1"
