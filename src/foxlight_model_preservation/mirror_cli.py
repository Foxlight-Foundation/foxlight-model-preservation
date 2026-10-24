"""Operator-only mirror controls; ordinary receiving remains receive-only."""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from .mirror import follow, model_action, plan, run_mirror
from .mirror_models import SourceGrant, Terms
from .mirror_prune import prune
from .mirror_state import MAX_STATE, MirrorLedger, grant_restart
from .models import PreservationError
from .operations import OperationControl
from .signing import load_trust


class MirrorArguments(argparse.Namespace):
    """Typed standalone mirror subcommands, with no implicit hosted dependencies."""

    mirror_action: str
    mirror_dir: Path
    name: str
    disk_bytes: int
    ingress_daily: int
    egress_daily: int
    egress_monthly: int
    bind: str
    port: int
    serve_start: int
    serve_end: int
    refresh_seconds: int
    source_file: Path
    expected_bootstrap_id: str
    mirror_trust: Path
    terms_file: Path
    confirm_redistribution: bool
    allow_http: bool
    allow_private: bool
    allow_local: bool
    max_bytes: int
    public: bool
    resume: bool
    seconds: int
    mirror_manifest: str
    confirm_manifest: str
    resume_expires: int
    confirm_resume: bool


def register_mirror_commands(
    add_parser: Callable[..., argparse.ArgumentParser],
) -> None:
    """Register explicit init/follow/run/retention and separate resume-grant actions."""
    mirror = add_parser("mirror", help="operate a standalone persistent HTTP mirror")
    actions = mirror.add_subparsers(dest="mirror_action", required=True)
    for name in (
        "init",
        "follow",
        "plan",
        "status",
        "run",
        "pause",
        "resume",
        "leave",
        "grant-resume",
        "revoke-resume",
        "pin",
        "unpin",
        "block",
        "unblock",
        "retry",
        "prune",
    ):
        command = actions.add_parser(name)
        command.add_argument("--dir", dest="mirror_dir", type=Path, required=True)
        if name == "init":
            command.add_argument("--name", required=True)
            for flag, default in (
                ("disk-bytes", 100 * 1024**3),
                ("ingress-daily", 50 * 1024**3),
                ("egress-daily", 50 * 1024**3),
                ("egress-monthly", 1024**4),
            ):
                command.add_argument("--" + flag, type=int, default=default)
            command.add_argument(
                "--bind",
                choices=["127.0.0.1", "0.0.0.0"],  # noqa: S104 - explicit public choice
                default="127.0.0.1",
            )
            command.add_argument("--port", type=int, default=8080)
            command.add_argument(
                "--serve-start", type=int, default=0, help="UTC hour inclusive"
            )
            command.add_argument(
                "--serve-end",
                type=int,
                default=0,
                help="UTC hour exclusive; equal means all day",
            )
            command.add_argument("--refresh-seconds", type=int, default=21600)
        if name == "follow":
            command.add_argument("--source-file", type=Path, required=True)
            command.add_argument("--expected-bootstrap-id", required=True)
            command.add_argument(
                "--bundle-trust", dest="mirror_trust", type=Path, required=True
            )
            command.add_argument("--terms-file", type=Path, required=True)
            command.add_argument(
                "--confirm-redistribution", action="store_true", required=True
            )
            for flag in ("allow-http", "allow-private", "allow-local"):
                command.add_argument("--" + flag, action="store_true")
            command.add_argument("--max-bytes", type=int, default=20 * 1024**3)
        if name == "run":
            choice = command.add_mutually_exclusive_group(required=True)
            choice.add_argument(
                "--public",
                action="store_true",
                help="fresh manual public grant, at most 24 hours",
            )
            choice.add_argument(
                "--resume",
                action="store_true",
                help="require the separate standing resume grant",
            )
            command.add_argument("--seconds", type=int, default=3600)
        if name in ("pin", "unpin", "block", "unblock", "retry", "prune"):
            command.add_argument("--manifest", dest="mirror_manifest", required=True)
        if name == "prune":
            command.add_argument("--confirm-manifest", required=True)
        if name == "grant-resume":
            command.add_argument(
                "--expires",
                dest="resume_expires",
                type=int,
                required=True,
                help="UTC Unix seconds, within 366 days",
            )
            command.add_argument("--confirm-resume", action="store_true", required=True)


def _input(path: Path) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(MAX_STATE + 1)
    if len(data) > MAX_STATE:
        raise PreservationError("mirror input exceeds its bound")
    return data


def run_mirror_command(
    arguments: MirrorArguments, control: OperationControl
) -> dict[str, JsonValue]:
    """Execute exactly one local mirror action; init/follow/plan never connect."""
    action = arguments.mirror_action
    if action == "init":
        ledger = MirrorLedger.initialize(
            arguments.mirror_dir,
            name=arguments.name,
            disk_bytes=arguments.disk_bytes,
            ingress_daily=arguments.ingress_daily,
            egress_daily=arguments.egress_daily,
            egress_monthly=arguments.egress_monthly,
            bind=arguments.bind,
            port=arguments.port,
            serve_start=arguments.serve_start,
            serve_end=arguments.serve_end,
            refresh_seconds=arguments.refresh_seconds,
        )
        return {"initialized": True, "network_started": False, "policy": plan(ledger)}
    ledger = MirrorLedger(arguments.mirror_dir)
    if action in ("plan", "status"):
        return plan(ledger)
    if action == "follow":
        terms = TypeAdapter(tuple[Terms, ...]).validate_json(
            _input(arguments.terms_file)
        )
        source = follow(
            ledger,
            _input(arguments.source_file),
            arguments.expected_bootstrap_id,
            SourceGrant(
                trust=load_trust(_input(arguments.mirror_trust)),
                terms=terms,
                max_bytes=arguments.max_bytes,
                allow_http=arguments.allow_http,
                allow_private=arguments.allow_private,
                allow_local=arguments.allow_local,
            ),
        )
        return {
            "source_id": source,
            "network_started": False,
            "model_authority": "mirror only",
        }
    if action == "run":
        return run_mirror(
            ledger,
            public=arguments.public,
            resume=arguments.resume,
            seconds=arguments.seconds,
            control=control,
            ready=lambda port: print(
                json.dumps({"ready": True, "port": port, "anonymous": False}),
                flush=True,
            ),
        )
    if action == "grant-resume":
        grant_restart(ledger, arguments.resume_expires)
    elif action == "revoke-resume":
        ledger.update(lambda s: s.model_copy(update={"restart": None}))
    elif action in ("pause", "resume", "leave"):
        ledger.update(
            lambda s: s.model_copy(
                update={
                    "paused": action != "resume",
                    "left": action == "leave",
                    "restart": None if action == "leave" else s.restart,
                }
            )
        )
    elif action == "prune":
        return TypeAdapter(dict[str, JsonValue]).validate_python(
            prune(ledger, arguments.mirror_manifest, arguments.confirm_manifest)
        )
    else:
        model_action(ledger, arguments.mirror_manifest, action)
    return {
        "action": action,
        "retained_bytes_deleted": action == "prune",
        "recorded_at": int(time.time()),
    }
