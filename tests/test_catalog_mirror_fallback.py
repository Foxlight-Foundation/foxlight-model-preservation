"""Saving from Find falls back to mirrors, by each file's signed hash."""

from __future__ import annotations

import pytest
from test_local_owner import accepted_catalog, finish, submit

from foxlight_model_preservation.ledger import TransferBudget
from foxlight_model_preservation.local_host.owner import LocalOwner
from foxlight_model_preservation.models import FileIdentity, PreservationError
from foxlight_model_preservation.operations import OperationControl
from foxlight_model_preservation.signing import digest
from foxlight_model_preservation.storage import directory, object_path
from foxlight_model_preservation.transfer import (
    DEFAULT_POLICY,
    SourcesExhausted,
    fetch_object,
)


def _store(root, data: bytes, *, name: str | None = None):
    """A mirror store holding one object (or other bytes under its name)."""
    path = root / object_path(name or digest(data))
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    return root


def _origin_down(monkeypatch, calls=None):
    def unavailable(*_args, **_kwargs):
        if calls is not None:
            calls.append("origin")
        raise PreservationError("model source unavailable")

    monkeypatch.setattr(
        "foxlight_model_preservation.local_host.owner.download_file", unavailable
    )


def _seeds(monkeypatch, *sources):
    monkeypatch.setattr(
        "foxlight_model_preservation.local_host.owner.load_seeds",
        lambda *a, **k: tuple(str(source) for source in sources),
    )


def _save(owner, repo):
    listing = owner.control("search", {})
    plan = owner.control(
        "plan",
        {
            "kind": "receive",
            "catalog_id": listing["catalog_id"],
            "card_id": repo.card.card_id,
        },
    )
    return finish(owner, submit(owner, plan))


def test_a_save_completes_from_a_mirror_when_the_origin_fails(tmp_path, monkeypatch):
    catalog, repo, data = accepted_catalog(tmp_path, companion=False)
    _origin_down(monkeypatch)
    _seeds(monkeypatch, _store(tmp_path / "seed", data))
    owner = LocalOwner(tmp_path / "owner", catalog=catalog)
    try:
        job = _save(owner, repo)
        assert job["state"] == "succeeded", job
        assert len(owner.read()["copies"]) == 1
    finally:
        owner.close()


def test_a_save_tries_the_next_mirror_after_a_miss_or_wrong_bytes(
    tmp_path, monkeypatch
):
    catalog, repo, data = accepted_catalog(tmp_path, companion=False)
    _origin_down(monkeypatch)
    empty = tmp_path / "empty"
    empty.mkdir()
    wrong = _store(tmp_path / "wrong", b"not the signed bytes", name=digest(data))
    _seeds(monkeypatch, empty, wrong, _store(tmp_path / "good", data))
    owner = LocalOwner(tmp_path / "owner", catalog=catalog)
    try:
        assert _save(owner, repo)["state"] == "succeeded"
    finally:
        owner.close()


def test_same_length_wrong_bytes_leave_room_for_a_healthy_mirror(tmp_path, monkeypatch):
    """Wrong bytes of the right length are spent in full before rejection."""
    catalog, repo, data = accepted_catalog(tmp_path, companion=False)
    _origin_down(monkeypatch)
    forged = bytes(255 - byte for byte in data)
    assert len(forged) == len(data)
    wrong = _store(tmp_path / "wrong", forged, name=digest(data))
    _seeds(monkeypatch, wrong, _store(tmp_path / "good", data))
    owner = LocalOwner(tmp_path / "owner", catalog=catalog)
    try:
        assert _save(owner, repo)["state"] == "succeeded"
    finally:
        owner.close()


def test_no_mirror_with_the_file_fails_plainly(tmp_path, monkeypatch):
    catalog, repo, _data = accepted_catalog(tmp_path, companion=False)
    _origin_down(monkeypatch)
    empty = tmp_path / "empty"
    empty.mkdir()
    _seeds(monkeypatch, empty)
    owner = LocalOwner(tmp_path / "owner", catalog=catalog)
    try:
        job = _save(owner, repo)
        assert (job["state"], job["error_code"]) == ("failed", "origin_unavailable")
        assert owner.read()["copies"] == []
    finally:
        owner.close()


def test_mirrors_are_not_contacted_when_the_origin_works(tmp_path, monkeypatch):
    catalog, repo, data = accepted_catalog(tmp_path, companion=False)

    def download(repository, revision, file, destination, control, **kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    monkeypatch.setattr(
        "foxlight_model_preservation.local_host.owner.download_file", download
    )
    monkeypatch.setattr(
        "foxlight_model_preservation.local_host.owner.fetch_object",
        lambda *a, **k: pytest.fail("a mirror was contacted"),
    )
    owner = LocalOwner(tmp_path / "owner", catalog=catalog)
    try:
        assert _save(owner, repo)["state"] == "succeeded"
    finally:
        owner.close()


def test_fetch_object_skips_a_miss_and_reports_each_outcome(tmp_path):
    data = b"one signed object\n"
    empty = tmp_path / "empty"
    empty.mkdir()
    good = _store(tmp_path / "good", data)
    target = tmp_path / "target"
    outcomes: dict[str, bool] = {}
    with directory(target, create=True) as root:
        used = fetch_object(
            (str(empty), str(good)),
            FileIdentity(path="model.bin", size=len(data), sha256=digest(data)),
            root,
            DEFAULT_POLICY,
            deadline=float("inf"),
            budget=TransferBudget(len(data)),
            control=OperationControl(),
            source_outcome=outcomes.__setitem__,
        )
    assert used == str(good)
    assert outcomes == {str(empty): False, str(good): True}
    assert (target / object_path(digest(data))).read_bytes() == data


def test_fetch_object_refuses_when_no_source_has_verified_bytes(tmp_path):
    data = b"one signed object\n"
    wrong = _store(tmp_path / "wrong", b"other bytes", name=digest(data))
    with (
        directory(tmp_path / "target", create=True) as root,
        pytest.raises(SourcesExhausted),
    ):
        fetch_object(
            (str(wrong),),
            FileIdentity(path="model.bin", size=len(data), sha256=digest(data)),
            root,
            DEFAULT_POLICY,
            deadline=float("inf"),
            budget=TransferBudget(64),
            control=OperationControl(),
        )
