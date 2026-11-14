"""Find saves a model Hugging Face removed, from the Pirate Face swarm."""

import functools
import hashlib
import time

import pytest
from test_local_owner import finish, submit
from test_swarm_native import SETTINGS, repository

from foxlight_model_preservation.local_host import owner as owner_module
from foxlight_model_preservation.local_host.owner import LocalOwner
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.pirateface_find import SwarmEntry
from foxlight_model_preservation.swarm import SwarmReceiver
from foxlight_model_preservation.torrent_binding import torrent_engine

pytest.importorskip("libtorrent", reason="optional native BitTorrent extra")
COMMIT = "c" * 40


@pytest.fixture
def rescued(tmp_path, monkeypatch):
    root, contents, swarm = repository(tmp_path)
    swarm = swarm.__class__(**{**swarm.__dict__, "revision": COMMIT})
    entry = SwarmEntry(
        repository="test/weights",
        revision=COMMIT,
        on_hub=False,
        seeders=1,
        leechers=0,
        infohash=swarm.infohash,
        torrent_path=f"/api/torrents/{swarm.infohash}.torrent",
        hashes=tuple(
            sorted(
                (name, hashlib.sha256(data).hexdigest())
                for name, data in contents.items()
            )
        ),
    )
    calls = []
    find = owner_module.pirateface_find
    monkeypatch.setattr(find, "lookup", lambda repo, **kw: calls.append(repo) or entry)
    monkeypatch.setattr(find, "torrent", lambda item, **kw: swarm)
    monkeypatch.setattr(
        find, "request_rescue", lambda repo, **kw: calls.append(("rescue", repo))
    )
    engine = torrent_engine()
    seeder = engine.session(SETTINGS)
    seed = seeder.add_torrent(
        {
            "ti": engine.torrent_info(engine.bdecode(swarm.data)),
            "save_path": str(root.parent),
            "flags": 0,
        }
    )
    deadline = time.monotonic() + 20
    while not seed.status().is_seeding and time.monotonic() < deadline:
        time.sleep(0.05)
    monkeypatch.setattr(
        owner_module,
        "SwarmReceiver",
        functools.partial(
            SwarmReceiver,
            settings={**SETTINGS, "unchoke_slots_limit": 0},
            peers=(("127.0.0.1", seeder.listen_port()),),
            stall_seconds=20,
        ),
    )
    try:
        yield contents, calls
    finally:
        seeder.pause()


def test_rescued_model_is_saved_from_the_swarm_only_after_both_choices(
    tmp_path, rescued
):
    contents, calls = rescued
    owner = LocalOwner(tmp_path / "home")
    try:
        with pytest.raises(PreservationError, match="pirate_face_search_not_enabled"):
            owner.control("pirate-face-review", {"repository": "test/weights"})
        owner.control("pirate-face-consent", {"enabled": True})
        review = owner.control("pirate-face-review", {"repository": "test/weights"})
        assert review["listed"] and review["on_hub"] is False
        assert review["revision"] == COMMIT
        assert {item["path"] for item in review["files"]} == set(contents)
        body = {
            "kind": "receive",
            "origin": "pirateface",
            "repository": "test/weights",
            "files": ["config.json", "model.safetensors"],
        }
        # Swarm downloads show this computer's address to peers: a separate choice.
        with pytest.raises(PreservationError, match="pirate_face_swarm_not_enabled"):
            owner.control("plan", body)
        owner.control("pirate-face-swarm", {"enabled": True})
        assert owner.read()["pirate_face_swarm"] is True
        plan = owner.control("plan", body)
        assert plan["details"]["origin_check"] == "unconfirmed"
        # Hugging Face can't vouch for it: keeping it needs an acknowledgement.
        with pytest.raises(PreservationError, match="acknowledgement_required"):
            submit(owner, plan)
        start = {
            "plan_id": plan["plan_id"],
            "operation_id": "pirate-face-rescued-save",
            "confirmed": True,
            "accept_unconfirmed_origin": True,
            # No licence is recorded: the person follows the model's own terms.
            **(
                {"accepted_terms": plan["terms"]["accept"]}
                if plan["terms"]["accept"]
                else {}
            ),
        }
        job = finish(owner, owner.control("start", start), seconds=90)
        assert job["state"] == "succeeded", job
        copy = owner.read()["copies"][0]
        assert copy["copy_id"] == job["copy_id"]
        sources = (owner.home / "job-sources" / (job["job_id"] + ".json")).read_text()
        assert "swarm" in sources
        owner.control("pirate-face-rescue", {"repository": "gone/model"})
        assert ("rescue", "gone/model") in calls
    finally:
        owner.close()
