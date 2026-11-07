"""The one-switch flow keeps every persistent-mirror grant explicit and bounded."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import JsonValue, ValidationError

from foxlight_preservation_library import sharing_switch
from foxlight_preservation_library.client import CoreClient, CoreRefused
from foxlight_preservation_library.contracts import Settings
from foxlight_preservation_library.server import LibraryServer
from foxlight_preservation_library.sharing_switch import (
    CONSENT_SECONDS,
    DEFAULT_CONFIGURATION,
)

APACHE, OTHER, GPL, PMF, REMOVED, MIT = (c * 64 for c in "abcdef")
CAPPED = {
    **DEFAULT_CONFIGURATION,
    "egress_daily": 1024**3,
    "egress_monthly": 8 * 1024**3,
}


def contract(*, modern: bool) -> dict[str, Any]:
    """Core's generated mirror contract, optionally with the new intake fields."""
    value: dict[str, Any] = json.loads(
        (Path(__file__).parent / "fixtures/core-mirror-contract-v1.json").read_text()
    )
    if modern:
        schemas = value["components"]["schemas"]
        schemas["MirrorRequest"]["properties"]["kind"]["enum"].append("intake")
        schemas["MirrorRequest"]["properties"]["intake"] = {
            "anyOf": [{}, {"type": "null"}]
        }
        schemas["MirrorPage"]["properties"]["intake"] = {
            "anyOf": [{}, {"type": "null"}]
        }
        for field in ("egress_daily", "egress_monthly"):
            schemas["MirrorConfiguration"]["properties"][field] = {
                "anyOf": [{"type": "integer"}, {"type": "null"}]
            }
    return value


LICENCES = {APACHE: "Apache 2.0", OTHER: "other", GPL: "GPL-3.0", REMOVED: "MIT"}
ALL = (APACHE, OTHER, GPL, PMF, REMOVED, MIT)


def copy(manifest: str) -> dict[str, JsonValue]:
    return {
        "copy_id": "copy-" + manifest[0],
        "manifest_id": manifest,
        "member_count": 1,
    }


def review(manifest: str) -> dict[str, JsonValue]:
    return {
        "schema_version": "skulk.preservation-public-review.v1",
        "copy_id": "copy-" + manifest[0],
        "manifest_id": manifest,
        "archive_format": "pmf-v0.1" if manifest == PMF else "preservation-v1",
        "name": "Fixture " + manifest[0],
        "size_bytes": 7,
        "workloads": [],
        "runtime_requirements": [],
        "members": [
            {
                "member_id": "primary",
                "role": "primary",
                "repository": "example/fixture",
                "revision": "b" * 40,
                "format": "test",
                "size_bytes": 7,
                "file_count": 1,
                "license_id": LICENCES.get(manifest, "MIT"),
                "terms_reference": "LICENSE",
                "notice_paths": ["LICENSE"],
            }
        ],
    }


class FakeCore:
    """A small stateful stand-in for Skulk Core's persistent-mirror routes."""

    def __init__(self, *, modern: bool = True) -> None:
        self.modern = modern
        self.page: dict[str, Any] = {
            "schema_version": "skulk.preservation-mirror.v1",
            "configured": False,
            "available": False,
            "policy_id": None,
            "configuration": None,
            "paused": True,
            "restart_expires": None,
            "restart_allowed": False,
            "controller_job_id": None,
            "activity": None,
            "models": [],
            "model_count": 0,
            "next_offset": None,
        }
        self.jobs: dict[str, dict[str, Any]] = {}
        self.decisions: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str]] = []
        self.hold_retain = False
        # Something outside this setup changes the standing intake policy.
        self.tamper_intake = False
        # When set, mirror reads wait so a Stop can overtake an admission.
        self.gate: asyncio.Event | None = None
        # POSTs of these kinds wait, so a Stop can land while one is in flight.
        self.post_gates: dict[str, asyncio.Event] = {}
        self.posts_waiting: list[str] = []
        self.before: dict[str, Any] = {}
        self.after: dict[str, Any] = {}
        self.sequence = 0

    def other_tab_configures(self, **changes: Any) -> None:
        """Another Library tab saves different mirror settings."""
        self.page["configuration"] = {**self.page["configuration"], **changes}
        self.page["restart_expires"] = None
        self._policy()

    def _policy(self) -> None:
        self.sequence += 1
        self.page["policy_id"] = f"{self.sequence:064x}"

    def _job(self, decision: dict[str, Any]) -> dict[str, Any]:
        # Core derives the receipt identity from the client's operation.
        job_id = hashlib.sha256(
            f"mirror\x00{decision['owner_id']}\x00{decision['operation_id']}".encode()
        ).hexdigest()
        job = {
            "job_id": job_id,
            "owner_id": decision["owner_id"],
            "operation_id": decision["operation_id"],
            "kind": decision["kind"],
            "state": "succeeded",
            "manifest_id": decision.get("manifest_id"),
            "error_code": None,
        }
        self.jobs[job_id] = job
        return job

    async def handle_async(self, request: httpx.Request) -> httpx.Response:
        if self.gate is not None and request.url.path == "/preservation/mirror":
            await self.gate.wait()
        if request.method == "POST" and request.url.path.endswith("/mirror/jobs"):
            kind = json.loads(request.content)["kind"]
            if kind in self.post_gates:
                self.posts_waiting.append(kind)
                await self.post_gates[kind].wait()
        return self.handle(request)

    def served(self) -> set[str]:
        return {
            m["manifest_id"]
            for m in self.page["models"]
            if m["state"] in {"retained", "pending"} and not m["blocked"]
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/preservation/")
        self.calls.append((request.method, path))
        if request.method == "GET" and path == "library":
            return httpx.Response(200, json={"jobs": []})
        if request.method == "GET" and request.url.path == "/api/openapi.json":
            return httpx.Response(200, json=contract(modern=self.modern))
        if request.method == "GET" and path == "mirror":
            if self.modern:
                return httpx.Response(200, json={"intake": None, **self.page})
            return httpx.Response(200, json=self.page)
        if request.method == "GET" and path.startswith("public-copies/"):
            manifest = next(m for m in ALL if m[0] == path.split("/")[1][-1])
            return httpx.Response(200, json=review(manifest))
        job_id = path.removeprefix("mirror/jobs/")
        if request.method == "GET" and job_id in self.jobs:
            return httpx.Response(200, json=self.jobs[job_id])
        if request.method == "DELETE" and job_id in self.jobs:
            job = self.jobs[job_id]
            job["state"] = "cancelled"
            if self.page["controller_job_id"] == job_id:
                self.page.update(
                    controller_job_id=None, paused=True, restart_allowed=False
                )
            return httpx.Response(200, json=job)
        if request.method == "POST" and path == "mirror/jobs":
            decision: dict[str, Any] = json.loads(request.content)
            self.decisions.append(decision)
            # Another tab or client acting just before this decision lands.
            before = self.before.pop(decision["kind"], None)
            if before is not None:
                before(self)
            job = self._job(decision)
            if decision["policy_id"] != self.page["policy_id"]:
                # Core binds every effect to the current policy (mirror_host).
                job.update(state="failed", error_code="mirror_policy_changed")
                return httpx.Response(202, json=job)
            kind = decision["kind"]
            if kind == "configure":
                self.page.update(
                    configured=True,
                    available=True,
                    configuration=decision["configuration"],
                    restart_expires=None,
                )
                self._policy()
            elif kind == "retain":
                assert decision["confirm_redistribution"] is True
                if self.tamper_intake:
                    self.page["intake"] = {
                        "share": "eligible",
                        "licenses": ["mit", "gpl-3.0"],
                        "definition": 1,
                        "withheld": [],
                    }
                self.page["models"].append(
                    {
                        "manifest_id": decision["manifest_id"],
                        "state": "retained",
                        "pinned": False,
                        "blocked": False,
                    }
                )
                self.page["restart_expires"] = None
                self._policy()
                if self.hold_retain:
                    job["state"] = "running"
            elif kind == "intake":
                assert self.modern
                self.page["intake"] = decision["intake"]
                self.page["restart_expires"] = None
                self._policy()
            elif kind == "model":
                for model in self.page["models"]:
                    if model["manifest_id"] == decision["manifest_id"]:
                        model["blocked"] = decision["action"] == "block"
            elif kind == "revoke_resume":
                self.page["restart_expires"] = None
            elif kind == "grant_resume":
                assert decision["confirm_resume"] is True
                self.page["restart_expires"] = decision["resume_expires"]
            elif kind == "run":
                assert decision["public_participation"] is True
                assert decision["mode"] == "resume"
                job["state"] = "running"
                self.page.update(controller_job_id=job["job_id"], paused=False)
            after = self.after.pop(kind, None)
            if after is not None:
                after(self)
            return httpx.Response(202, json=job)
        return httpx.Response(404, json={"detail": "not_found"})


@pytest.fixture
async def core() -> Any:
    fake = FakeCore()
    client = CoreClient(
        Settings(), "client", transport=httpx.MockTransport(fake.handle_async)
    )
    app = LibraryServer(None, client, asyncio.get_running_loop())
    app.sharing.poll_seconds = 0
    yield fake, app
    await client.close()


def start_body(fake: FakeCore, **changes: JsonValue) -> dict[str, JsonValue]:
    body: dict[str, JsonValue] = {
        "consent": True,
        "disclosure": 1,
        "policy_id": fake.page["policy_id"],
        "share": "eligible",
        "licenses": ["apache-2.0", "mit"],
        "copies": [copy(APACHE), copy(OTHER), copy(GPL), copy(PMF)],
        "configuration": dict(DEFAULT_CONFIGURATION),
    }
    return {**body, **changes}


async def finished(app: LibraryServer) -> dict[str, Any]:
    for _ in range(500):
        progress: dict[str, Any] = (await app.dispatch("mirror-share-read", {}))[
            "progress"
        ]  # type: ignore[assignment]
        if progress["state"] != "working":
            return progress
        await asyncio.sleep(0)
    raise AssertionError("switch did not settle")


async def test_reading_and_reviewing_is_receive_only(core: Any) -> None:
    fake, app = core
    state = await app.dispatch("mirror-share-read", {})
    assert state["progress"]["state"] == "idle"
    assert state["default_licenses"] == list(sharing_switch.DEFAULT_LICENSES)
    defaults = state["defaults"]
    # Storage and downloads are always bounded; upload limits are opt-in.
    assert all(
        isinstance(defaults[k], int) and defaults[k] > 0
        for k in ("disk_bytes", "ingress_daily")
    )
    assert defaults["egress_daily"] is None and defaults["egress_monthly"] is None
    assert defaults["ipfs"] is False and defaults["torrent"] is False
    families = state["licence_families"]
    assert [family["id"] for family in families["families"]] == [
        "permissive",
        "attribution",
        "copyleft",
        "responsible-ai",
        "non-commercial",
        "custom",
    ]
    assert state["licence_definition"] == families["version"] == 1
    assert fake.calls == []
    rows = await app.dispatch(
        "mirror-share-review",
        {
            "licenses": ["Apache-2.0", "MIT"],
            "copies": [copy(m) for m in (APACHE, OTHER, GPL, PMF)],
        },
    )
    assert [row["status"] for row in rows["copies"]] == [
        "eligible",
        "no_license",
        "license_not_selected",
        "unsupported",
    ]
    assert all(method == "GET" for method, _ in fake.calls)
    assert fake.decisions == []


@pytest.mark.parametrize(
    "change",
    [
        {"consent": False},
        {"consent": None},
        {"disclosure": 0},
        {"disclosure": None},
        {"copies": []},
    ],
)
async def test_serving_needs_explicit_consent_after_the_disclosure(
    core: Any, change: dict[str, JsonValue]
) -> None:
    fake, app = core
    body = {k: v for k, v in start_body(fake, **change).items() if v is not None}
    with pytest.raises(ValidationError):
        await app.dispatch("mirror-share-start", body)
    assert fake.decisions == []


@pytest.mark.parametrize("licence", ["other", "NOASSERTION", " ", "unknown"])
async def test_only_identified_public_licences_can_be_listed(
    core: Any, licence: str
) -> None:
    fake, app = core
    with pytest.raises(CoreRefused, match="community_license_unidentified"):
        await app.dispatch(
            "mirror-share-start", start_body(fake, licenses=["mit", licence])
        )
    with pytest.raises(CoreRefused, match="community_license_unidentified"):
        await app.dispatch("mirror-share-review", {"licenses": [licence], "copies": []})
    assert fake.decisions == []


@pytest.mark.parametrize(
    ("change", "refusal"),
    [
        ({"http": False, "torrent": False, "ipfs": False}, "mirror_transport_required"),
        (
            {"egress_daily": 10 * 1024**3, "egress_monthly": 1024**3},
            "mirror_allowance_invalid",
        ),
    ],
)
async def test_service_is_bounded_and_needs_a_transport(
    core: Any, change: dict[str, JsonValue], refusal: str
) -> None:
    fake, app = core
    with pytest.raises(CoreRefused, match=refusal):
        await app.dispatch(
            "mirror-share-start",
            start_body(fake, configuration={**DEFAULT_CONFIGURATION, **change}),
        )
    unbounded = {k: v for k, v in DEFAULT_CONFIGURATION.items() if k != "disk_bytes"}
    with pytest.raises(ValidationError):
        await app.dispatch(
            "mirror-share-start", start_body(fake, configuration=unbounded)
        )
    assert fake.decisions == []


async def test_one_consent_keeps_only_public_licences_for_a_year(core: Any) -> None:
    fake, app = core
    before = int(time.time())
    started = await app.dispatch("mirror-share-start", start_body(fake))
    assert started["progress"]["state"] == "working"
    progress = await finished(app)
    assert progress["state"] == "done", progress
    kinds = [decision["kind"] for decision in fake.decisions]
    assert kinds == ["configure", "intake", "retain", "grant_resume", "run"]
    # No upload limit unless the owner turns one on.
    configured = fake.decisions[0]["configuration"]
    assert configured["egress_daily"] is None and configured["egress_monthly"] is None
    assert configured["disk_bytes"] > 0 and configured["ingress_daily"] > 0
    # The standing intake policy is what the year's grant covers.
    assert fake.page["intake"] == {
        "share": "eligible",
        "licenses": ["apache-2.0", "mit"],
        "definition": 1,
        "withheld": [],
    }
    retained = [d["manifest_id"] for d in fake.decisions if d["kind"] == "retain"]
    assert retained == [APACHE]
    assert {row["reason"] for row in progress["skipped"]} == {
        "no_license",
        "license_not_selected",
        "unsupported",
    }
    grant = fake.decisions[3]
    assert (
        before + CONSENT_SECONDS
        <= grant["resume_expires"]
        <= int(time.time()) + CONSENT_SECONDS
    )
    # The core refuses restart grants beyond 366 days.
    assert grant["resume_expires"] < int(time.time()) + 366 * 86400
    assert progress["expires"] == grant["resume_expires"]
    assert fake.page["controller_job_id"] is not None


async def test_nothing_eligible_starts_nothing(core: Any) -> None:
    fake, app = core
    await app.dispatch(
        "mirror-share-start", start_body(fake, copies=[copy(OTHER), copy(GPL)])
    )
    progress = await finished(app)
    assert progress["state"] == "failed"
    assert progress["error_code"] == "community_nothing_to_share"
    assert [d["kind"] for d in fake.decisions] == ["configure", "intake"]
    assert fake.page["controller_job_id"] is None


async def test_existing_mirror_selections_and_grants_survive_the_upgrade(
    core: Any,
) -> None:
    fake, app = core
    fake.modern = False  # A Core from before standing intake and opt-in limits.
    existing = {
        **CAPPED,
        "name": "Older mirror",
        "port": 8080,
        "http": True,
        "exposure": "loopback",
    }
    fake.page.update(
        configured=True,
        available=True,
        policy_id="9" * 64,
        configuration=existing,
        restart_expires=int(time.time()) + 3600,
        restart_allowed=True,
        controller_job_id="f" * 64,
        paused=False,
        models=[
            {
                "manifest_id": APACHE,
                "state": "retained",
                "pinned": True,
                "blocked": False,
            },
            {
                "manifest_id": REMOVED,
                "state": "removed",
                "pinned": False,
                "blocked": False,
            },
        ],
    )
    fake.jobs["f" * 64] = {
        "job_id": "f" * 64,
        "kind": "run",
        "state": "running",
        "operation_id": "old",
        "owner_id": "core-restart",
    }
    # Reading the Library's new Sharing screen changes nothing on Core.
    await app.dispatch("mirror-share-read", {})
    await app.dispatch(
        "mirror-share-review",
        {"licenses": ["mit"], "copies": [copy(APACHE), copy(REMOVED)]},
    )
    assert fake.decisions == [] and not any(m == "DELETE" for m, _ in fake.calls)
    assert fake.page["controller_job_id"] == "f" * 64
    # A later reviewed Turn on keeps the reviewed settings and selections.
    await app.dispatch(
        "mirror-share-start",
        start_body(fake, configuration=existing, copies=[copy(APACHE), copy(REMOVED)]),
    )
    progress = await finished(app)
    assert progress["state"] == "done", progress
    assert [d["kind"] for d in fake.decisions] == ["grant_resume", "run"]
    assert progress["kept"] == 1
    # A removed selection returns only through a separate explicit re-intake.
    assert progress["skipped"] == [{"copy_id": "copy-e", "reason": "removed"}]
    assert ("DELETE", "mirror/jobs/" + "f" * 64) in fake.calls


async def test_a_changed_policy_needs_a_fresh_review(core: Any) -> None:
    fake, app = core
    body = start_body(fake)
    fake.page["policy_id"] = "8" * 64
    with pytest.raises(CoreRefused, match="mirror_policy_changed"):
        await app.dispatch("mirror-share-start", body)
    assert fake.decisions == []


async def test_stop_sharing_is_one_action(core: Any) -> None:
    fake, app = core
    fake.hold_retain = True
    await app.dispatch("mirror-share-start", start_body(fake))
    retain: dict[str, Any] = {}
    for _ in range(500):
        retain = next((j for j in fake.jobs.values() if j["kind"] == "retain"), {})
        # Wait until the Library is observing the running Core intake.
        if retain and ("GET", "mirror/jobs/" + retain["job_id"]) in fake.calls:
            break
        await asyncio.sleep(0)
    stopped = await app.dispatch("mirror-share-stop", {})
    assert stopped["progress"]["state"] == "stopped"
    assert retain["state"] == "cancelled"
    assert [d["kind"] for d in fake.decisions] == ["configure", "intake", "retain"]
    # Stopping a running controller is the same single control.
    fake.hold_retain = False
    await app.dispatch("mirror-share-start", start_body(fake))
    assert (await finished(app))["state"] == "done"
    controller = fake.page["controller_job_id"]
    stopped = await app.dispatch("mirror-share-stop", {})
    assert stopped["job"]["job_id"] == controller
    assert fake.page["controller_job_id"] is None and fake.page["paused"] is True


@pytest.mark.skipif(
    importlib.util.find_spec("foxlight_model_preservation") is None,
    reason="the core package is optional for the managed Library",
)
def test_licence_policy_matches_the_core_community_mirror() -> None:
    from foxlight_model_preservation import licences
    from foxlight_model_preservation.local_host import community

    from foxlight_preservation_library import licences as library_licences

    # The Library ships the core's definition byte for byte.
    assert library_licences.definition_bytes() == licences.definition_bytes()
    assert library_licences.LICENCE_DEFINITION_VERSION == (
        licences.LICENCE_DEFINITION_VERSION
    )
    assert sharing_switch.DEFAULT_LICENSES == community.DEFAULT_LICENSES
    assert sharing_switch.UNIDENTIFIED == community.UNIDENTIFIED
    assert sharing_switch.CONSENT_SECONDS == community.CONSENT_SECONDS
    for value in ("Apache License 2.0", " MIT ", "OpenRAIL-M License", "GPL-3.0"):
        assert sharing_switch.normalize_license(value) == community.normalize_license(
            value
        )


async def test_an_older_core_needs_an_upload_limit_and_gets_no_intake(
    core: Any,
) -> None:
    fake, app = core
    fake.modern = False
    with pytest.raises(CoreRefused, match="mirror_upload_limit_required"):
        await app.dispatch("mirror-share-start", start_body(fake))
    assert fake.decisions == []
    await app.dispatch("mirror-share-start", start_body(fake, configuration=CAPPED))
    assert (await finished(app))["state"] == "done"
    kinds = [decision["kind"] for decision in fake.decisions]
    # An older Core never sees an intake decision or an "intake" field.
    assert kinds == ["configure", "retain", "grant_resume", "run"]
    assert all("intake" not in decision for decision in fake.decisions)


async def test_the_saved_choice_is_kept_and_not_re_decided(core: Any) -> None:
    fake, app = core
    await app.dispatch(
        "mirror-share-start",
        start_body(fake, share="chosen", licenses=["Apache-2.0", "GPL-3.0"]),
    )
    assert (await finished(app))["state"] == "done"
    # Core keeps the owner's choice, so the screen can show it after a reload.
    assert fake.page["intake"]["share"] == "chosen"
    assert fake.page["intake"]["licenses"] == ["apache-2.0", "gpl-3.0"]
    before = len(fake.decisions)
    await app.dispatch(
        "mirror-share-start",
        start_body(fake, share="chosen", licenses=["apache-2.0", "gpl-3.0"]),
    )
    assert (await finished(app))["state"] == "done"
    # The same reviewed choice is not decided again; only the year is renewed.
    assert [d["kind"] for d in fake.decisions[before:]] == ["grant_resume", "run"]


async def test_an_upload_limit_is_kept_when_the_owner_sets_one(core: Any) -> None:
    fake, app = core
    await app.dispatch("mirror-share-start", start_body(fake, configuration=CAPPED))
    assert (await finished(app))["state"] == "done"
    configured = fake.decisions[0]["configuration"]
    assert configured["egress_daily"] == 1024**3
    assert configured["egress_monthly"] == 8 * 1024**3


def held(*identities: str, blocked: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    return [
        {
            "manifest_id": m,
            "state": "retained",
            "pinned": False,
            "blocked": m in blocked,
        }
        for m in identities
    ]


def existing(fake: FakeCore, *identities: str, blocked: tuple[str, ...] = ()) -> None:
    fake.page.update(
        configured=True,
        available=True,
        policy_id="9" * 64,
        configuration=dict(DEFAULT_CONFIGURATION),
        models=held(*identities, blocked=blocked),
    )


async def test_choosing_only_a_stops_serving_b_before_the_grant(core: Any) -> None:
    fake, app = core
    existing(fake, APACHE, MIT)
    await app.dispatch(
        "mirror-share-start", start_body(fake, share="chosen", copies=[copy(APACHE)])
    )
    progress = await finished(app)
    assert progress["state"] == "done", progress
    assert fake.served() == {APACHE}
    # B keeps its retained bytes; it is only blocked from serving.
    assert {m["manifest_id"] for m in fake.page["models"]} == {APACHE, MIT}
    kinds = [(d["kind"], d.get("action")) for d in fake.decisions]
    # A Core with standing intake records the reviewed choice first.
    assert kinds == [
        ("intake", None),
        ("model", "block"),
        ("grant_resume", None),
        ("run", None),
    ]
    assert fake.decisions[1]["manifest_id"] == MIT


async def test_switching_from_all_to_chosen_narrows_what_is_served(core: Any) -> None:
    fake, app = core
    await app.dispatch(
        "mirror-share-start", start_body(fake, copies=[copy(APACHE), copy(MIT)])
    )
    assert (await finished(app))["state"] == "done"
    assert fake.served() == {APACHE, MIT}
    fake.decisions.clear()
    await app.dispatch(
        "mirror-share-start",
        start_body(fake, share="chosen", copies=[copy(MIT)]),
    )
    progress = await finished(app)
    assert progress["state"] == "done", progress
    assert fake.served() == {MIT}
    assert progress["kept"] == 1
    # The share mode changed, so the standing intake policy is decided again.
    assert [d["kind"] for d in fake.decisions] == [
        "intake",
        "model",
        "grant_resume",
        "run",
    ]
    assert fake.page["controller_job_id"] is not None


async def test_all_eligible_never_serves_ineligible_or_kept_back_copies(
    core: Any,
) -> None:
    fake, app = core
    # GPL was kept under an older, wider list; MIT was kept back by the owner.
    existing(fake, APACHE, GPL, MIT, blocked=(MIT,))
    await app.dispatch(
        "mirror-share-start",
        start_body(fake, copies=[copy(APACHE), copy(GPL), copy(MIT)]),
    )
    progress = await finished(app)
    assert progress["state"] == "done", progress
    assert fake.served() == {APACHE}
    assert {"copy_id": "copy-c", "reason": "license_not_selected"} in progress[
        "skipped"
    ]
    # Choosing a kept-back copy explicitly is the consent to serve it again.
    fake.decisions.clear()
    await app.dispatch(
        "mirror-share-start",
        start_body(fake, share="chosen", copies=[copy(APACHE), copy(MIT)]),
    )
    assert (await finished(app))["state"] == "done"
    assert fake.served() == {APACHE, MIT}


async def test_holdings_outside_the_reviewed_list_are_not_served(core: Any) -> None:
    fake, app = core
    # PMF is not on this page of public copies, so it was never reviewed here.
    existing(fake, APACHE)
    fake.page["models"].append(
        {"manifest_id": "0" * 64, "state": "pending", "pinned": False, "blocked": False}
    )
    await app.dispatch("mirror-share-start", start_body(fake, copies=[copy(APACHE)]))
    assert (await finished(app))["state"] == "done"
    assert fake.served() == {APACHE}


async def test_concurrent_starts_admit_one_and_stop_fences_every_setup(
    core: Any,
) -> None:
    fake, app = core
    fake.gate = asyncio.Event()
    first = asyncio.create_task(app.dispatch("mirror-share-start", start_body(fake)))
    second = asyncio.create_task(app.dispatch("mirror-share-start", start_body(fake)))
    await asyncio.sleep(0)
    # Both are mid-admission; the second was refused before any Core read.
    with pytest.raises(CoreRefused, match="mirror_share_busy"):
        await second
    stopping = asyncio.create_task(app.dispatch("mirror-share-stop", {}))
    await asyncio.sleep(0)
    fake.gate.set()
    stopped = await stopping
    with pytest.raises(CoreRefused, match="mirror_share_stopped"):
        await first
    assert stopped["progress"]["state"] == "stopped"
    for _ in range(50):
        await asyncio.sleep(0)
    assert app.sharing._task is None  # pyright: ignore[reportPrivateUsage]
    assert fake.decisions == []
    assert fake.page["restart_expires"] is None
    assert fake.page["controller_job_id"] is None


async def test_a_stop_during_setup_prevents_any_later_grant_or_run(core: Any) -> None:
    fake, app = core
    fake.hold_retain = True
    await app.dispatch("mirror-share-start", start_body(fake))
    for _ in range(500):
        if any(d["kind"] == "retain" for d in fake.decisions):
            break
        await asyncio.sleep(0)
    task = app.sharing._task  # pyright: ignore[reportPrivateUsage]
    await app.dispatch("mirror-share-stop", {})
    assert task is not None and task.done()
    # A later Start is a new, separately admitted decision.
    for _ in range(50):
        await asyncio.sleep(0)
    assert {d["kind"] for d in fake.decisions} <= {"configure", "intake", "retain"}
    assert fake.page["restart_expires"] is None


async def wait_for(condition: Any, rounds: int = 20000) -> None:
    for _ in range(rounds):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not reached")


async def test_stop_during_an_in_flight_run_post_stops_the_job_it_created(
    core: Any,
) -> None:
    fake, app = core
    fake.post_gates["run"] = asyncio.Event()
    await app.dispatch("mirror-share-start", start_body(fake))
    await wait_for(lambda: fake.posts_waiting == ["run"])
    stopping = asyncio.create_task(app.dispatch("mirror-share-stop", {}))
    for _ in range(200):
        await asyncio.sleep(0)
    # Stop has cancelled the setup's wait and is draining the shielded POST;
    # it must not report success while Core may still accept the run.
    assert not stopping.done()
    assert fake.page["controller_job_id"] is None
    # Core accepts the run only now, after Stop began.
    fake.post_gates["run"].set()
    stopped = await stopping
    assert stopped["progress"]["state"] == "stopped"
    run = next(job for job in fake.jobs.values() if job["kind"] == "run")
    assert run["state"] == "cancelled"
    assert fake.page["controller_job_id"] is None
    # The grant that landed for this setup is withdrawn too.
    assert fake.page["restart_expires"] is None
    assert [d["kind"] for d in fake.decisions][-1] == "revoke_resume"
    assert app.sharing._task is None  # pyright: ignore[reportPrivateUsage]


async def test_stop_during_an_in_flight_grant_sends_no_run_and_revokes(
    core: Any,
) -> None:
    fake, app = core
    fake.post_gates["grant_resume"] = asyncio.Event()
    await app.dispatch("mirror-share-start", start_body(fake))
    await wait_for(lambda: fake.posts_waiting == ["grant_resume"])
    stopping = asyncio.create_task(app.dispatch("mirror-share-stop", {}))
    await asyncio.sleep(0)
    # A Start while Stop drains is refused (one setup at a time).
    with pytest.raises(CoreRefused, match="mirror_share_busy"):
        await app.dispatch("mirror-share-start", start_body(fake))
    fake.post_gates["grant_resume"].set()
    stopped = await stopping
    assert stopped["progress"]["state"] == "stopped"
    kinds = [d["kind"] for d in fake.decisions]
    assert "run" not in kinds
    assert kinds[-1] == "revoke_resume"
    assert fake.page["restart_expires"] is None


async def test_an_unanswered_post_never_reports_stopped(core: Any) -> None:
    fake, app = core
    app.sharing.drain_seconds = 0.05
    fake.post_gates["run"] = asyncio.Event()
    await app.dispatch("mirror-share-start", start_body(fake))
    await wait_for(lambda: fake.posts_waiting == ["run"])
    stopped = await app.dispatch("mirror-share-stop", {})
    assert stopped["progress"]["state"] == "failed"
    assert stopped["progress"]["error_code"] == "mirror_stop_unconfirmed"
    fake.post_gates["run"].set()


async def test_a_core_failure_mid_setup_sends_no_grant_or_run(core: Any) -> None:
    fake, app = core
    existing(fake, APACHE)
    fake.page["controller_job_id"] = "f" * 64
    fake.jobs["f" * 64] = {"job_id": "f" * 64, "kind": "run", "state": "running"}
    original = fake.handle

    def failing(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and json.loads(request.content)["kind"] == "model":
            return httpx.Response(409, json={"detail": "mirror_busy_stop_first"})
        return original(request)

    fake.handle = failing  # type: ignore[method-assign]
    existing_models = held(APACHE, MIT)
    fake.page["models"] = existing_models
    await app.dispatch(
        "mirror-share-start", start_body(fake, share="chosen", copies=[copy(APACHE)])
    )
    progress = await finished(app)
    assert progress["state"] == "failed"
    assert progress["error_code"] == "mirror_busy_stop_first"
    kinds = {d["kind"] for d in fake.decisions}
    assert "grant_resume" not in kinds and "run" not in kinds
    # Sharing is left off rather than serving the unreviewed copy B.
    assert fake.page["controller_job_id"] is None


LOOPBACK = {**DEFAULT_CONFIGURATION, "exposure": "loopback"}


def no_authorization(fake: FakeCore) -> None:
    kinds = [d["kind"] for d in fake.decisions]
    assert "grant_resume" not in kinds and "run" not in kinds
    assert fake.page["restart_expires"] is None
    assert fake.page["controller_job_id"] is None


@pytest.mark.parametrize("when", ["before", "after"])
async def test_another_tab_opening_network_http_mid_setup_aborts(
    core: Any, when: str
) -> None:
    fake, app = core
    existing(fake, MIT)
    fake.page["configuration"] = dict(LOOPBACK)
    # Codex's case: the reviewed settings are loopback-only; another tab
    # switches HTTP to the network while this setup keeps a copy.
    hook = getattr(fake, when)
    hook["retain"] = lambda core: core.other_tab_configures(exposure="network")
    await app.dispatch(
        "mirror-share-start",
        start_body(fake, configuration=LOOPBACK, copies=[copy(APACHE), copy(MIT)]),
    )
    progress = await finished(app)
    assert progress["state"] == "failed"
    assert progress["error_code"] in {
        "mirror_policy_changed",
        "mirror_changed_during_setup",
    }
    no_authorization(fake)


async def test_another_tab_changing_transports_before_the_grant_aborts(
    core: Any,
) -> None:
    fake, app = core
    existing(fake, APACHE, MIT)
    fake.before["grant_resume"] = lambda core: core.other_tab_configures(torrent=True)
    await app.dispatch(
        "mirror-share-start",
        start_body(fake, share="chosen", copies=[copy(APACHE)]),
    )
    progress = await finished(app)
    assert progress["state"] == "failed"
    # Core refuses the grant for the moved policy; no run follows.
    assert "run" not in [d["kind"] for d in fake.decisions]
    assert fake.page["restart_expires"] is None
    assert fake.page["controller_job_id"] is None


async def test_holdings_changed_by_another_client_abort_before_the_grant(
    core: Any,
) -> None:
    fake, app = core
    existing(fake, APACHE, MIT)

    def other_client_keeps(core: FakeCore) -> None:
        core.page["models"].append(
            {"manifest_id": GPL, "state": "retained", "pinned": False, "blocked": False}
        )
        core._policy()  # pyright: ignore[reportPrivateUsage]

    fake.after["model"] = other_client_keeps
    await app.dispatch(
        "mirror-share-start",
        start_body(fake, share="chosen", copies=[copy(APACHE)]),
    )
    progress = await finished(app)
    assert progress["state"] == "failed"
    assert progress["error_code"] == "mirror_changed_during_setup"
    no_authorization(fake)


async def test_an_unblock_elsewhere_is_caught_before_the_run(core: Any) -> None:
    fake, app = core
    existing(fake, APACHE, MIT)

    def other_tab_unblocks(core: FakeCore) -> None:
        # Block state is outside Core's policy hash, so only a direct check sees it.
        for model in core.page["models"]:
            model["blocked"] = False

    fake.after["grant_resume"] = other_tab_unblocks
    await app.dispatch(
        "mirror-share-start",
        start_body(fake, share="chosen", copies=[copy(APACHE)]),
    )
    progress = await finished(app)
    assert progress["state"] == "failed"
    assert progress["error_code"] == "mirror_changed_during_setup"
    assert "run" not in [d["kind"] for d in fake.decisions]
    assert fake.page["controller_job_id"] is None


async def test_a_refused_intake_aborts_instead_of_being_skipped(core: Any) -> None:
    fake, app = core
    fake.before["retain"] = lambda core: core.other_tab_configures(port=9999)
    await app.dispatch("mirror-share-start", start_body(fake))
    progress = await finished(app)
    assert progress["state"] == "failed"
    assert progress["error_code"] == "mirror_policy_changed"
    no_authorization(fake)


async def test_an_intake_policy_changed_during_setup_is_never_authorized(
    core: Any,
) -> None:
    fake, app = core
    fake.tamper_intake = True
    await app.dispatch("mirror-share-start", start_body(fake))
    progress = await finished(app)
    assert progress["state"] == "failed"
    assert progress["error_code"] == "mirror_changed_during_setup"
    kinds = [decision["kind"] for decision in fake.decisions]
    assert "grant_resume" not in kinds and "run" not in kinds
