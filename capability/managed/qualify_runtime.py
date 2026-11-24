"""Qualify an installed managed Library against real Core-owned byte work.

Only explicit disposable paths are used. The pinned Core's own synthetic
installation fixture supplies tiny local files; no model or mirror is fetched.
The real owner, child, HTTP operator gate, journal and byte worker execute.
"""

import argparse
import asyncio
import hashlib
import importlib
import json
import socket
import sys
from importlib.machinery import EXTENSION_SUFFIXES
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI
from pydantic import JsonValue, TypeAdapter
from skulk.api.main import API
from skulk.api.preservation import create_preservation_router
from skulk.extensions.runtime_artifacts import RuntimeTrust, measure_host
from skulk.extensions.runtime_install import RuntimeInstaller
from skulk.extensions.runtime_selection import RuntimeSelector
from skulk.extensions.runtime_service import RuntimeService
from skulk.store.model_store import ModelStore
from skulk.store.preservation_host import PreservationHost
from skulk.store.tests.test_preservation_export import make_preservation_installation
from skulk_capability_nodes.configuration import Settings
from skulk_capability_nodes.management import (
    ControlRequest,
    DescribeExtendedRequest,
    InvokeRequest,
    request,
)
from skulk_capability_nodes.owner import OwnerSettings
from skulk_capability_nodes.runtime_release import SignedRuntimeRelease
from skulk_capability_sdk.contracts import Descriptor, Invoke
from skulk_capability_sdk.storage import atomic_private, read_private, secure_directory

OBJECT = TypeAdapter(dict[str, JsonValue])
OBJECTS = TypeAdapter(list[dict[str, JsonValue]])
STRING = TypeAdapter(str)
BUNDLE = "foxlight.model-preservation"
COPY = "foxlight.preservation.copy"
LIBRARY = "foxlight.preservation.library"
OPERATION = "sdk1-preservation-once"


def object_value(value: JsonValue) -> dict[str, JsonValue]:
    """Decode an object before relying on private control-channel fields."""
    return OBJECT.validate_python(value)


async def invoke(
    root: Path,
    node: dict[str, JsonValue],
    capability: str,
    payload: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    """Use the protected ordinary owner channel and exact installed descriptor."""
    descriptor = next(
        item
        for item in OBJECTS.validate_python(node["descriptors"])
        if item["id"] == capability
    )
    response = await request(
        root,
        InvokeRequest(
            node_id=STRING.validate_python(node["node_id"]),
            invoke=Invoke(
                call_id="qualification-"
                + hashlib.sha256(json.dumps(payload).encode()).hexdigest()[:16],
                capability_id=capability,
                version=STRING.validate_python(descriptor["version"]),
                descriptor_revision=Descriptor.model_validate_json(
                    json.dumps(descriptor)
                ).revision,
                remaining_seconds=20.0,
                payload=payload,
            ),
        ),
    )
    # The authenticated management API unwraps Result after validating it.
    return object_value(response["result"])


async def owner_phase(
    root: Path,
    core_url: str,
    retained: dict[str, JsonValue] | None,
) -> tuple[dict[str, JsonValue], str]:
    """Open the actual child, configure once, preserve and reopen its Core job."""
    stop = asyncio.Event()
    service = asyncio.create_task(RuntimeService(root).serve(stop))
    try:
        async with asyncio.timeout(60):
            while True:
                assert not service.done(), "installed owner stopped before discovery"
                try:
                    response = await request(root, DescribeExtendedRequest())
                except OSError:
                    await asyncio.sleep(0.1)
                    continue
                nodes = OBJECTS.validate_python(
                    object_value(response["result"])["nodes"]
                )
                if nodes:
                    break
                await asyncio.sleep(0.1)
        assert len(nodes) == 1
        node = nodes[0]
        assert node["bundle_id"] == BUNDLE and node["version"] == "0.34.1"
        node_id = STRING.validate_python(node["node_id"])
        if retained is None:
            for operation in ("edit", "enable"):
                response = await request(
                    root,
                    ControlRequest(operation="get", bundle_id=BUNDLE, node_id=node_id),
                )
                settings = Settings.model_validate_json(
                    json.dumps(object_value(response["result"])["settings"])
                )
                await request(
                    root,
                    ControlRequest(
                        operation=operation,
                        bundle_id=BUNDLE,
                        node_id=node_id,
                        values={"skulk_api_url": core_url}
                        if operation == "edit"
                        else None,
                        expected_revision=settings.revision,
                        expected_schema_digest=settings.schema_digest,
                    ),
                )
        async with asyncio.timeout(30):
            while node["status"] != "ready":
                response = await request(root, DescribeExtendedRequest())
                node = OBJECTS.validate_python(
                    object_value(response["result"])["nodes"]
                )[0]
                await asyncio.sleep(0.1)
        surfaces = OBJECTS.validate_python(node["surfaces"])
        url = STRING.validate_python(surfaces[0]["url"])
        async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
            page = await client.get(url)
            assert page.status_code == 200 and "<html" in page.text.lower()
        if retained is None:
            report = await invoke(root, node, LIBRARY, {"action": "setup_device"})
            assert report["report"] is not None
            report = object_value(
                (await invoke(root, node, LIBRARY, {"action": "read"}))["report"]
            )
            model = OBJECTS.validate_python(report["models"])[0]
            plan = await invoke(
                root,
                node,
                COPY,
                {
                    "op": "plan",
                    "input": {
                        "kind": "preserve",
                        "location_id": "device",
                        "model_id": model["model_id"],
                        "generation_id": model["generation_id"],
                    },
                },
            )
            result = await invoke(
                root,
                node,
                COPY,
                {"op": "start", "operation_id": OPERATION, "input": plan["input"]},
            )
            assert result["operation_id"] == OPERATION
        else:
            result = await invoke(
                root, node, COPY, {"op": "status", "operation_id": OPERATION}
            )
            assert result["state"] == "succeeded"
        child = root / ("bundle-" + hashlib.sha256(BUNDLE.encode()).hexdigest()[:24])
        snapshot: dict[str, JsonValue] = {
            "host_identity": hashlib.sha256(
                read_private(root / "identity")
            ).hexdigest(),
            "node_id": node_id,
            "settings": hashlib.sha256(
                read_private(child / "settings.json")
            ).hexdigest(),
            "operation_id": OPERATION,
        }
        if retained is not None:
            assert snapshot == retained
        return snapshot, url
    finally:
        stop.set()
        assert await asyncio.wait_for(service, 45)


async def qualify(feed: Path, work: Path) -> None:
    """Install signed bytes, preserve local companions, restart and reopen Core."""
    importlib.import_module("skulk_pyo3_bindings")
    assert any(
        module is not None
        and module.__name__.startswith("skulk_pyo3_bindings.")
        and any(
            (module.__file__ or "").endswith(suffix) for suffix in EXTENSION_SUFFIXES
        )
        for module in tuple(sys.modules.values())
    ), "real Core bindings required"
    assert not work.exists()
    secure_directory(work)
    trust = RuntimeTrust.model_validate_json(
        (feed / "throwaway-trust.json").read_bytes()
    )
    assert set(trust.publishers) == {"qualification"}
    platform = measure_host().platform
    (candidate,) = feed.glob(f"{BUNDLE}/*/{platform}/release.json")
    signed = SignedRuntimeRelease.model_validate_json(candidate.read_bytes())
    installed = make_preservation_installation(work / "synthetic", "assistant")
    store = ModelStore(installed.primary.parent)
    preservation = PreservationHost(
        store, work / "byte-owner", custom_cards=work / "custom-cards"
    )

    async def provider() -> PreservationHost:
        return preservation

    app = FastAPI(openapi_url="/api/openapi.json")
    app.include_router(
        create_preservation_router(provider, API.require_preservation_operator)
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(64)
    port = listener.getsockname()[1]
    web = uvicorn.Server(
        uvicorn.Config(app, access_log=False, log_level="critical", lifespan="off")
    )
    web_task = asyncio.create_task(web.serve(sockets=[listener]))
    root = work / "manager"
    secure_directory(root)
    atomic_private(root / "publisher-trust.json", trust.model_dump_json().encode())
    atomic_private(
        root / "owner.json",
        OwnerSettings(transport_node_id="sdk1-preservation-fixture")
        .model_dump_json()
        .encode(),
    )
    selector = RuntimeSelector(root)
    phases: list[JsonValue] = []
    try:
        async with asyncio.timeout(15):
            while not web.started:
                assert not web_task.done()
                await asyncio.sleep(0.05)
        staged = await RuntimeInstaller(root).stage(
            candidate.read_bytes(), candidate.parent
        )
        assert staged.state == "staged"
        selected = await selector.activate(
            signed.runtime.digest, expected_revision=0, accept_permissions=True
        )
        assert selected.state == "complete"
        retained, _ = await owner_phase(root, f"http://127.0.0.1:{port}", None)
        phases.append("fresh:real-governed-preserve")
        node = STRING.validate_python(retained["node_id"])
        owner = hashlib.sha256(node.encode()).hexdigest()
        job_id = hashlib.sha256((owner + "\x00" + OPERATION).encode()).hexdigest()
        async with asyncio.timeout(30):
            while preservation.journal.job(job_id).state in {
                "queued",
                "running",
                "cancelling",
            }:
                await asyncio.sleep(0.05)
        job = preservation.journal.job(job_id)
        assert job.state == "succeeded" and job.result is not None, (
            job.model_dump_json()
        )
        expected = job.model_dump_json()
        copy = next(
            item
            for item in (await preservation.page()).copies
            if item.manifest_id == job.result["manifest_id"]
        )
        assert (
            copy.member_count == 3
        )  # Base, assistant and private provenance evidence.
        retained, _ = await owner_phase(root, f"http://127.0.0.1:{port}", retained)
        assert preservation.journal.job(job_id).model_dump_json() == expected
        phases.append("owner-restart:same-complete-job")
        await preservation.close()
        preservation = PreservationHost(
            store, work / "byte-owner", custom_cards=work / "custom-cards"
        )
        assert preservation.journal.job(job_id).model_dump_json() == expected
        retained, _ = await owner_phase(root, f"http://127.0.0.1:{port}", retained)
        assert preservation.journal.job(job_id).model_dump_json() == expected
        assert len((await preservation.page()).jobs) == 1
        phases.append("core-reopen:no-replay")
        assert selector.current() is not None
        receipt: dict[str, JsonValue] = {
            "passed": True,
            "platform": platform,
            "phases": phases,
            "compiled_core_bindings": True,
            "candidate_sequence": signed.runtime.release.sequence,
            "real_core_preservation_job": True,
            "complete_members": 3,
            "model_members": 2,
            "job_identity_result_settings_retained": True,
            "old_managed_store_baseline": False,
            "external_downloads": 0,
        }
        atomic_private(
            work / "qualification.json", (json.dumps(receipt, indent=2) + "\n").encode()
        )
        print(json.dumps(receipt))
    finally:
        current = selector.current()
        if current is not None:
            assert (
                selector.disable(expected_revision=current.revision).state == "complete"
            )
        web.should_exit = True
        await asyncio.wait_for(web_task, 15)
        await preservation.close()
        listener.close()


def main() -> None:
    """Require explicit isolated paths; never inspect a user's installed service."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feed", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(qualify(args.feed, args.work))


if __name__ == "__main__":
    main()
