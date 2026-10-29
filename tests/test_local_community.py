"""Help keep models available: one switch, one licence policy, no per-model steps."""

from __future__ import annotations

import http.client
import os
import socket
import time
from pathlib import Path

import pytest
from conftest import Capture
from test_local_backups import saved
from test_publication import private_capture

from foxlight_model_preservation.local_host.community import (
    DEFAULT_LICENSES,
    CommunityPolicy,
)
from foxlight_model_preservation.local_host.owner import LocalOwner
from foxlight_model_preservation.mirror_state import MirrorLedger
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.signing import digest
from foxlight_model_preservation.storage import manifest_path, object_path


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait_for(predicate, seconds: float = 30.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition not reached")


def status(owner: LocalOwner, copy_id: str) -> str:
    rows = owner.control("community-read", {})["copies"]
    return next(row["status"] for row in rows if row["copy_id"] == copy_id)


def served(port: int, identity: str) -> int:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request("GET", "/" + manifest_path(identity))
        response = connection.getresponse()
        response.read()
        return response.status
    except OSError:
        return 0
    finally:
        connection.close()


def enable(owner: LocalOwner, port: int) -> dict:
    return owner.control(
        "community-configure",
        {
            "enabled": True,
            "consent": True,
            "port": port,
            "licenses": [*DEFAULT_LICENSES, "test-only"],
        },
    )


def test_policy_is_permissive_by_default_and_refuses_unidentified_licences() -> None:
    policy = CommunityPolicy()
    assert not policy.enabled and policy.share == "eligible"
    assert {"apache-2.0", "mit", "bsd-3-clause", "openrail-m"} <= set(policy.licenses)
    assert CommunityPolicy(licenses=("Apache-2.0", "apache-2.0")).licenses == (
        "apache-2.0",
    )
    for unidentified in ("unknown", "other", "", "see-original-card"):
        with pytest.raises(ValueError):
            CommunityPolicy(licenses=(unidentified,))


def test_licence_policy_decides_eligibility_without_per_model_steps(
    tmp_path: Path, capture: Capture
) -> None:
    owner = LocalOwner(tmp_path / "owner")
    try:
        copy = saved(owner, tmp_path, capture)
        # The fixture's synthetic licence is not in the permissive default set.
        assert status(owner, copy["copy_id"]) == "license_not_selected"
        with pytest.raises(PreservationError, match="community_consent_required"):
            owner.control("community-configure", {"enabled": True})
        owner.control(
            "community-configure", {"licenses": [*DEFAULT_LICENSES, "test-only"]}
        )
        assert status(owner, copy["copy_id"]) == "eligible"
        owner.control(
            "community-choose",
            {"copy_id": copy["copy_id"], "chosen": False, "withheld": True},
        )
        assert status(owner, copy["copy_id"]) == "withheld"
        owner.control("community-configure", {"share": "chosen"})
        owner.control(
            "community-choose",
            {"copy_id": copy["copy_id"], "chosen": False, "withheld": False},
        )
        assert status(owner, copy["copy_id"]) == "not_chosen"
        # Nothing is published or served while the switch is off.
        assert not (owner.home / "sharing/community/mirror").exists()
    finally:
        owner.close()


def test_switch_shares_every_eligible_backup_across_restarts(
    tmp_path: Path, capture: Capture
) -> None:
    port = free_port()
    home = tmp_path / "owner"
    owner = LocalOwner(home)
    try:
        first = saved(owner, tmp_path, capture)
        view = enable(owner, port)
        assert view["consented"] and view["policy"]["consent_expires"] > time.time()
        identity = wait_for(
            lambda: next(
                (
                    row["public_manifest_id"]
                    for row in owner.control("community-read", {})["copies"]
                    if row["status"] == "sharing"
                ),
                None,
            )
        )
        wait_for(lambda: served(port, identity) == 200)
        # Sharing a backup does not copy its bytes: the mirror links them.
        ledger = MirrorLedger(home / "sharing/community/mirror")
        held = next(iter(ledger.read().models))
        assert held == identity
        weights = digest((capture.sources["weights"] / "model.bin").read_bytes())
        sample = home / "copies" / first["copy_id"] / object_path(weights)
        linked = home / "sharing/community/mirror/store" / object_path(weights)
        assert os.stat(linked).st_ino == os.stat(sample).st_ino

        # A new backup joins on its own, with no per-model step.
        (tmp_path / "second").mkdir()
        saved(owner, tmp_path / "second", private_capture(capture, tmp_path))
        second = next(
            item
            for item in owner.read()["copies"]
            if item["copy_id"] != first["copy_id"]
        )
        joined = wait_for(
            lambda: next(
                (
                    row["public_manifest_id"]
                    for row in owner.control("community-read", {})["copies"]
                    if row["copy_id"] == second["copy_id"]
                    and row["status"] == "sharing"
                ),
                None,
            ),
            seconds=60,
        )
        assert joined != identity
        wait_for(lambda: served(port, joined) == 200)
        # Withholding one backup stops serving it and keeps every byte.
        owner.control(
            "community-choose",
            {"copy_id": first["copy_id"], "chosen": False, "withheld": True},
        )
        wait_for(lambda: served(port, identity) == 404)
        wait_for(lambda: served(port, joined) == 200)
        assert ledger.read().models[identity].blocked
    finally:
        owner.close()
    assert served(port, joined) == 0

    # The standing consent resumes serving after a restart, with no new step.
    owner = LocalOwner(home)
    try:
        wait_for(lambda: served(port, joined) == 200)
        view = owner.control("community-configure", {"enabled": False})
        assert not view["consented"] and view["policy"]["consent_expires"] is None
        wait_for(lambda: served(port, joined) == 0)
    finally:
        owner.close()
    owner = LocalOwner(home)
    try:
        time.sleep(0.5)
        assert served(port, joined) == 0
        assert MirrorLedger(home / "sharing/community/mirror").read().restart is None
    finally:
        owner.close()


def test_backup_without_identifiable_licence_is_never_served(
    tmp_path: Path, capture: Capture
) -> None:
    unresolved = capture.recipe.model_copy(
        update={
            "members": tuple(
                member.model_copy(
                    update={
                        "intake": member.intake.model_copy(
                            update={"license_id": "unknown", "audience": "private"}
                        ),
                        "retained_card": None,
                    }
                )
                for member in capture.recipe.members
            )
        }
    )
    from foxlight_model_preservation.storage import pack

    store = tmp_path / "unresolved"
    identity = pack(unresolved, capture.sources, store, (capture.key,))
    port = free_port()
    owner = LocalOwner(tmp_path / "owner")
    try:
        copy = saved(
            owner,
            tmp_path,
            Capture(
                unresolved, capture.sources, capture.key, capture.trust, store, identity
            ),
        )
        enable(owner, port)
        assert status(owner, copy["copy_id"]) == "no_license"
        time.sleep(0.5)
        ledger = owner.home / "sharing/community/mirror"
        assert not ledger.exists() or not MirrorLedger(ledger).read().models
        assert owner.control("community-read", {})["sharing_count"] == 0
    finally:
        owner.close()


@pytest.mark.parametrize(
    ("card_license", "expected"),
    [
        ({"name": "Apache License 2.0", "spdx_id": "Apache-2.0"}, "eligible"),
        ({"name": "MIT License"}, "eligible"),
        ({"name": "Llama 3 Community License"}, "license_not_selected"),
        (None, "no_license"),
    ],
)
def test_registry_backup_licence_comes_from_its_original_model_card(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    card_license: object,
    expected: str,
) -> None:
    from test_local_owner import accepted_catalog, finish, submit

    catalog, repo, data = accepted_catalog(
        tmp_path,
        companion=False,
        metadata={} if card_license is None else {"license": card_license},
    )

    def download(repository, revision, file, destination, control, **kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    monkeypatch.setattr(
        "foxlight_model_preservation.local_host.owner.download_file", download
    )
    owner = LocalOwner(tmp_path / "owner", catalog=catalog)
    try:
        listing = owner.control("search", {})
        received = finish(
            owner,
            submit(
                owner,
                owner.control(
                    "plan",
                    {
                        "kind": "receive",
                        "catalog_id": listing["catalog_id"],
                        "card_id": repo.card.card_id,
                    },
                ),
            ),
        )
        assert received["state"] == "succeeded", received
        copy = owner.read()["copies"][0]
        # The permissive default set applies with no per-model review.
        assert status(owner, copy["copy_id"]) == expected
        if expected == "eligible":
            port = free_port()
            owner.control(
                "community-configure", {"enabled": True, "consent": True, "port": port}
            )
            identity = wait_for(
                lambda: next(
                    (
                        row["public_manifest_id"]
                        for row in owner.control("community-read", {})["copies"]
                        if row["status"] == "sharing"
                    ),
                    None,
                )
            )
            wait_for(lambda: served(port, identity) == 200)
    finally:
        owner.close()


def test_one_damaged_backup_never_takes_healthy_models_offline(
    tmp_path: Path, capture: Capture
) -> None:
    port = free_port()
    owner = LocalOwner(tmp_path / "owner")
    try:
        healthy = saved(owner, tmp_path, capture)
        (tmp_path / "second").mkdir()
        saved(owner, tmp_path / "second", private_capture(capture, tmp_path))
        damaged = next(
            item
            for item in owner.read()["copies"]
            if item["copy_id"] != healthy["copy_id"]
        )
        # Corrupt the damaged copy's private evidence object, which only it has.
        evidence = digest(b"private installed paths and old grants\n")
        target = owner.home / "copies" / damaged["copy_id"] / object_path(evidence)
        target.chmod(0o600)
        target.write_bytes(b"damaged")
        enable(owner, port)
        wait_for(lambda: status(owner, damaged["copy_id"]) == "failed", seconds=60)
        identity = wait_for(
            lambda: next(
                (
                    row["public_manifest_id"]
                    for row in owner.control("community-read", {})["copies"]
                    if row["copy_id"] == healthy["copy_id"]
                    and row["status"] == "sharing"
                ),
                None,
            )
        )
        wait_for(lambda: served(port, identity) == 200)
    finally:
        owner.close()
