#!/usr/bin/env python3
"""Exercise a relocated Mac bundle's real CLI offline using public PMF vectors.

This creates only a new owned test directory. Apple's per-process sandbox denies
network access; no firewall, Keychain, operator model store or trust is changed.
This is CLI/runtime acceptance, not Developer ID or clean-host qualification.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SANDBOX = [
    "/usr/bin/sandbox-exec",
    "-p",
    "(version 1) (allow default) (deny network*)",
]


def require(condition: bool, message: str) -> None:
    """Keep qualification checks active even when Python optimization is enabled."""
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    """Check real installed commands, original evidence and tamper refusal."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    root = args.directory.resolve()
    if root.exists() or not args.app.is_dir():
        parser.error("an existing reviewed app and new test directory are required")
    root.mkdir(parents=True)
    app = root / "User apps Ω/Relocated app.app"
    app.parent.mkdir()
    shutil.copytree(args.app, app, symlinks=True)
    resources = app / "Contents/Resources"
    outside = root / "working-directory"
    outside.mkdir()
    flag = root / "ambient-code-ran"
    (outside / "sitecustomize.py").write_text(
        "import os, pathlib\npathlib.Path(os.environ['TEST_FLAG']).touch()\n"
    )
    environment = {
        **os.environ,
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(outside),
        "PYTHONHOME": str(outside / "not-a-runtime"),
        "__PYVENV_LAUNCHER__": str(outside / "not-a-runtime"),
        "TEST_FLAG": str(flag),
    }

    def run(command: list[str], *, succeeds: bool = True) -> str:
        result = subprocess.run(  # noqa: S603 - explicit reviewed test bundle, no shell
            [*SANDBOX, *command],
            cwd=outside,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if (succeeds and result.returncode != 0) or (
            not succeeds and result.returncode != 1
        ):
            raise RuntimeError(result.stdout + result.stderr)
        return result.stdout if succeeds else result.stderr

    native = resources / "bin/model-preserve"
    alias = root / "preserve-command"
    alias.symlink_to(native)
    run([str(alias), "--help"])
    run([str(resources / "site-packages/bin/model-preserve"), "--help"])
    run([str(resources / "bin/foxlight-sovereign"), "--help"])
    run(
        [
            str(resources / "python/bin/python3"),
            "-I",
            "-B",
            "-c",
            "import importlib.util, sys; sys.path.insert(0, sys.argv[1]); "
            "assert importlib.util.find_spec('skulk') is None; "
            "assert importlib.util.find_spec('skulk_capability_sdk') is None",
            str(resources / "site-packages"),
        ]
    )
    run(
        [
            str(resources / "python/bin/python3"),
            "-I",
            "-B",
            "-c",
            "import socket\n"
            "try:\n"
            " with socket.socket() as connection:\n"
            "  connection.connect(('127.0.0.1', 9))\n"
            "except PermissionError:\n"
            " pass\n"
            "else:\n"
            " raise SystemExit('child network sandbox did not refuse the probe')\n",
        ]
    )
    vector = json.loads((PROJECT / "examples/conformance/pmf-v0.1.json").read_bytes())
    store = root / "store"
    (store / "manifests").mkdir(parents=True)
    (store / "objects/sha256").mkdir(parents=True)
    envelope = base64.b64decode(vector["envelope_base64"])
    (store / "manifests" / (vector["manifest_id"] + ".json")).write_bytes(envelope)
    for identity, encoded in vector["objects_base64"].items():
        (store / "objects/sha256" / identity).write_bytes(base64.b64decode(encoded))
    trust = root / "fixture-public-trust.json"
    trust.write_text(json.dumps(vector["trust"]))
    common = [vector["manifest_id"], "--store", str(store), "--trust", str(trust)]
    verified = json.loads(run([str(alias), "pmf-verify", *common]))
    require(verified["verification"] == "complete", "CLI did not verify the PMF")
    require(verified["authority"] == "owner-capture", "fixture authority changed")
    restored = root / "restored"
    run([str(native), "pmf-restore", *common, "--output", str(restored)])
    manifest = vector["manifest"]
    expected = {
        "members/" + member["member_id"] + "/" + item["path"]: base64.b64decode(
            vector["objects_base64"][item["sha256"]]
        )
        for member in manifest["members"]
        for item in member["files"]
    }
    expected.update(
        {
            "documents/" + item["document_id"]: base64.b64decode(
                vector["objects_base64"][item["sha256"]]
            )
            for item in manifest["documents"]
        }
    )
    expected[".pmf/envelope.json"] = envelope
    actual = {
        str(path.relative_to(restored)): path.read_bytes()
        for path in restored.rglob("*")
        if path.is_file()
    }
    require(actual == expected, "original files/cards/signed envelope changed")
    identity = manifest["members"][0]["files"][0]["sha256"]
    (store / "objects/sha256" / identity).write_bytes(b"tampered fixture")
    refused = root / "refused-restore"
    failure = json.loads(
        run(
            [str(alias), "pmf-restore", *common, "--output", str(refused)],
            succeeds=False,
        )
    )
    require(bool(failure["code"]) and not refused.exists(), "tampered PMF restored")
    require(
        not flag.exists() and not list(app.rglob("__pycache__")),
        "ambient Python executed or the app received bytecode writes",
    )
    subprocess.run(  # noqa: S603 - fixed tool, only our relocated app copy
        ["/usr/bin/codesign", "--verify", "--strict", str(app)], check=True
    )
    record = {
        "schema_version": "sovereign.macos-cli-qualification.v1",
        "members": len(manifest["members"]),
        "restored_files": len(expected),
        "original_signed_envelope_and_opaque_cards": True,
        "network_denied_by_child_sandbox": True,
        "socket_probe_refused": True,
        "relocated_unicode_and_command_alias": True,
        "ambient_python_ignored": True,
        "tampered_copy_refused": True,
        "app_resource_seal_after_use": True,
        "skulk_or_sdk_required": False,
        "clean_host_or_full_real_model_qualification": False,
    }
    (root / "qualification.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record))


if __name__ == "__main__":
    main()
