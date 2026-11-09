"""The registry's separate card-licence file, from listing to a shareable backup."""

from __future__ import annotations

import shutil

import pytest
from test_local_community import status
from test_local_owner import accepted_catalog, finish, submit
from tuf.api.exceptions import DownloadHTTPError
from tuf.ngclient.fetcher import FetcherInterface

from foxlight_model_preservation.local_host.catalog import (
    VIRTUAL_URL,
    RegistryCatalog,
    RegistryFetcher,
)
from foxlight_model_preservation.local_host.owner import LocalOwner
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.signing import digest


def _owner(tmp_path, monkeypatch, catalog, data):
    def download(repository, revision, file, destination, control, **kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    monkeypatch.setattr(
        "foxlight_model_preservation.local_host.owner.download_file", download
    )
    return LocalOwner(tmp_path / "owner", catalog=catalog)


def _plan(owner, repo):
    listing = owner.control("search", {})
    plan = owner.control(
        "plan",
        {
            "kind": "receive",
            "catalog_id": listing["catalog_id"],
            "card_id": repo.card.card_id,
        },
    )
    return listing, plan


def test_the_licence_file_names_the_licence_and_travels_with_the_backup(
    tmp_path, monkeypatch
):
    """A licence-less card shows its signed registry licence and stays shareable.

    The licence is written into the saved copy's own signed record, so the
    sharing decision still holds after the registry cache is gone.
    """
    catalog, repo, data = accepted_catalog(
        tmp_path, companion=False, licences="apache-2.0"
    )
    owner = _owner(tmp_path, monkeypatch, catalog, data)
    try:
        listing, plan = _plan(owner, repo)
        assert listing["results"][0]["details"]["license"] == "apache-2.0"
        # Apache-2.0 is named and asks for no acceptance.
        assert plan["terms"]["accept"] == []
        assert [row["license_id"] for row in plan["terms"]["licences"]] == [
            "apache-2.0"
        ]
        assert finish(owner, submit(owner, plan))["state"] == "succeeded"
        copy = owner.read()["copies"][0]
        shutil.rmtree(catalog.cache)
        assert status(owner, copy["copy_id"]) == "eligible"
    finally:
        owner.close()


def test_a_companion_from_another_repository_never_inherits_the_licence(
    tmp_path, monkeypatch
):
    catalog, repo, data = accepted_catalog(tmp_path, licences="apache-2.0")
    owner = _owner(tmp_path, monkeypatch, catalog, data)
    try:
        _listing, plan = _plan(owner, repo)
        rows = {
            row["license_id"]: row["requires_acceptance"]
            for row in plan["terms"]["licences"]
        }
        assert rows["apache-2.0"] is False
        # The tokenizer from test/tokenizer names nothing and still needs a yes.
        assert rows["see-original-card"] is True
    finally:
        owner.close()


def test_a_licence_the_card_names_itself_wins_over_the_file(tmp_path, monkeypatch):
    catalog, repo, data = accepted_catalog(
        tmp_path,
        companion=False,
        metadata={"license": {"name": "llama3.1"}},
        licences="apache-2.0",
    )
    owner = _owner(tmp_path, monkeypatch, catalog, data)
    try:
        _listing, plan = _plan(owner, repo)
        assert [row["license_id"] for row in plan["terms"]["licences"]] == ["llama3.1"]
    finally:
        owner.close()


def test_a_licence_file_that_no_longer_matches_its_signature_is_ignored(tmp_path):
    catalog, repo, _data = accepted_catalog(
        tmp_path, companion=False, licences="apache-2.0"
    )
    path = catalog.cache / "licences" / (digest(repo.target) + ".json")
    path.write_bytes(repo.licences.replace(b"apache-2.0", b"mit-0000000"))
    assert catalog.licences() == {}
    assert catalog.search()["results"][0]["details"]["license"] is None


def test_refresh_downloads_the_signed_licence_file(tmp_path, monkeypatch):
    _catalog, repo, _data = accepted_catalog(
        tmp_path, companion=False, licences="apache-2.0"
    )
    routes = {
        "metadata/timestamp.json": repo.timestamp.to_bytes(),
        "metadata/3.snapshot.json": repo.snapshot.to_bytes(),
        "metadata/4.targets.json": repo.targets.to_bytes(),
        "targets/v1/" + digest(repo.target) + ".catalog.json": repo.target,
        "targets/v1/" + digest(repo.licences) + ".card-licences.json": repo.licences,
    }

    class MemoryFetcher(FetcherInterface):
        def __init__(self, *args):
            pass

        def _fetch(self, url):
            path = url.removeprefix(VIRTUAL_URL)
            if path not in routes:
                raise DownloadHTTPError("not found", 404)
            yield routes[path]

    monkeypatch.setattr(
        "foxlight_model_preservation.local_host.catalog.RegistryFetcher", MemoryFetcher
    )
    catalog = RegistryCatalog(tmp_path / "fresh", anchor=repo.root.to_bytes())
    assert catalog.refresh()["results"][0]["details"]["license"] == "apache-2.0"
    # A licence file that can't be fetched never blocks the catalog update.
    del routes["targets/v1/" + digest(repo.licences) + ".card-licences.json"]
    plain = RegistryCatalog(tmp_path / "plain", anchor=repo.root.to_bytes())
    assert plain.refresh()["results"][0]["details"]["license"] is None


def test_only_the_catalog_and_licence_targets_may_be_fetched():
    fetcher = RegistryFetcher("http://127.0.0.1:9")
    with pytest.raises(PreservationError, match="unsupported registry"):
        next(fetcher._fetch(VIRTUAL_URL + "targets/v1/advisories.json"))
    fetcher.deadline = 0  # stop before any connection is attempted
    with pytest.raises(PreservationError, match="allowance exhausted"):
        next(
            fetcher._fetch(
                VIRTUAL_URL + "targets/v1/" + "a" * 64 + ".card-licences.json"
            )
        )


def test_catalog_metadata_and_the_file_resolve_to_one_licence(tmp_path, monkeypatch):
    """What the listing shows is what the review checks and the copy records."""
    catalog, repo, data = accepted_catalog(
        tmp_path,
        companion=False,
        catalog_metadata={"license": "llama3.1"},
        licences="apache-2.0",
    )
    owner = _owner(tmp_path, monkeypatch, catalog, data)
    try:
        listing, plan = _plan(owner, repo)
        assert listing["results"][0]["details"]["license"] == "llama3.1"
        assert plan["details"]["license"] == "llama3.1"
        # Terms come from the recorded intake, so the copy records llama3.1 too.
        assert plan["terms"]["accept"] == ["llama3.1"]
    finally:
        owner.close()


def test_cancelling_during_the_licence_download_stops_the_update(tmp_path, monkeypatch):
    from foxlight_model_preservation.operations import OperationCancelled

    _catalog, repo, _data = accepted_catalog(
        tmp_path, companion=False, licences="apache-2.0"
    )
    routes = {
        "metadata/timestamp.json": repo.timestamp.to_bytes(),
        "metadata/3.snapshot.json": repo.snapshot.to_bytes(),
        "metadata/4.targets.json": repo.targets.to_bytes(),
        "targets/v1/" + digest(repo.target) + ".catalog.json": repo.target,
    }

    class MemoryFetcher(FetcherInterface):
        def __init__(self, *args):
            pass

        def _fetch(self, url):
            path = url.removeprefix(VIRTUAL_URL)
            if path.endswith(".card-licences.json"):
                raise OperationCancelled("cancelled")
            if path not in routes:
                raise DownloadHTTPError("not found", 404)
            yield routes[path]

    monkeypatch.setattr(
        "foxlight_model_preservation.local_host.catalog.RegistryFetcher", MemoryFetcher
    )
    catalog = RegistryCatalog(tmp_path / "fresh", anchor=repo.root.to_bytes())
    with pytest.raises(OperationCancelled):
        catalog.refresh()
    # Nothing was published as the accepted head.
    assert catalog.search()["catalog_id"] is None


def test_an_unreadable_cached_licence_file_leaves_the_catalog_browsable(tmp_path):
    catalog, repo, _data = accepted_catalog(
        tmp_path, companion=False, licences="apache-2.0"
    )
    path = catalog.cache / "licences" / (digest(repo.target) + ".json")
    path.unlink()
    path.mkdir()
    assert catalog.licences() == {}
    found = catalog.search()
    assert found["total"] == 1
    assert found["results"][0]["details"]["license"] is None
