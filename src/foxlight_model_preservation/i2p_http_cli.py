"""Explicit receive-only I2P HTTP source updates and trusted model downloads."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from pydantic import JsonValue

from .discovery import fetch_selection, refresh_source_i2p
from .i2p_http import I2PHTTPProxy, i2p_http_endpoint
from .models import MAX_METADATA_BYTES, PreservationError
from .operations import OperationControl
from .signing import load_trust
from .transfer import I2PTransferPolicy, fetch


class I2PHTTPArguments(argparse.Namespace):
    """Explicit local proxy and exact independent source/model selections."""

    i2p_http_action: str
    proxy_host: str
    proxy_port: int
    allow_i2p: bool
    i2p_http_endpoints: list[str] | None
    i2p_http_source: str
    i2p_http_entry: str
    i2p_http_cache: Path
    i2p_http_manifest: str
    i2p_http_trust: Path
    i2p_http_store: Path
    i2p_http_mirrors: list[str]
    i2p_http_max_bytes: int
    i2p_http_deadline: float


def register_i2p_http_commands(
    add_parser: Callable[..., argparse.ArgumentParser],
) -> None:
    """Register I2P-only HTTP actions without changing ordinary network policy."""
    parser = add_parser(
        "i2p-http", help="receive-only I2P HTTP pilot; privacy not qualified"
    )
    commands = parser.add_subparsers(dest="i2p_http_action", required=True)
    for action in ("source-refresh", "source-fetch", "fetch"):
        command = commands.add_parser(action)
        command.add_argument("--proxy-host", default="127.0.0.1")
        command.add_argument("--proxy-port", type=int, default=4444)
        command.add_argument("--allow-i2p", action="store_true")
        command.add_argument("--progress", action="store_true")
        if action in {"source-refresh", "source-fetch"}:
            command.add_argument("--source", dest="i2p_http_source", required=True)
            command.add_argument(
                "--cache",
                dest="i2p_http_cache",
                type=Path,
                default=Path.home() / ".local/state/foxlight-preservation/discovery",
            )
        if action == "source-refresh":
            command.add_argument(
                "--endpoint", dest="i2p_http_endpoints", action="append"
            )
        else:
            if action == "fetch":
                command.add_argument("i2p_http_manifest", metavar="manifest")
                command.add_argument(
                    "--mirror", dest="i2p_http_mirrors", action="append", required=True
                )
            else:
                command.add_argument("--entry", dest="i2p_http_entry", required=True)
                command.add_argument(
                    "--expected-manifest-id", dest="i2p_http_manifest", required=True
                )
            command.add_argument(
                "--trust", dest="i2p_http_trust", type=Path, required=True
            )
            command.add_argument(
                "--store", dest="i2p_http_store", type=Path, required=True
            )
            command.add_argument(
                "--max-bytes", dest="i2p_http_max_bytes", type=int, default=20 * 1024**3
            )
            command.add_argument(
                "--deadline-seconds",
                dest="i2p_http_deadline",
                type=float,
                default=3600,
            )


def run_i2p_http_command(
    arguments: I2PHTTPArguments, control: OperationControl
) -> dict[str, JsonValue]:
    """Require I2P consent and preserve all existing TUF/bundle verification."""
    if not arguments.allow_i2p:
        raise PreservationError("I2P HTTP requires explicit participation")
    proxy = I2PHTTPProxy(arguments.proxy_host, arguments.proxy_port)
    proxy.validate()
    control.checkpoint()
    if arguments.i2p_http_action == "source-refresh":
        status = refresh_source_i2p(
            arguments.i2p_http_cache,
            arguments.i2p_http_source,
            proxy=proxy,
            endpoints=tuple(arguments.i2p_http_endpoints)
            if arguments.i2p_http_endpoints is not None
            else None,
            allow_i2p=True,
            control=control,
        )
        return {
            "source": status.model_dump(mode="json"),
            "transport": "i2p-http",
            "payload_uploads": False,
        }
    with arguments.i2p_http_trust.open("rb") as stream:
        trust = load_trust(stream.read(MAX_METADATA_BYTES + 1))
    policy = I2PTransferPolicy(
        proxy=proxy,
        max_bytes=arguments.i2p_http_max_bytes,
        deadline_seconds=arguments.i2p_http_deadline,
    )
    if arguments.i2p_http_action == "source-fetch":
        manifest = fetch_selection(
            arguments.i2p_http_cache,
            arguments.i2p_http_source,
            arguments.i2p_http_entry,
            arguments.i2p_http_manifest,
            trust,
            arguments.i2p_http_store,
            policy,
            control=control,
        )
    else:
        for mirror in arguments.i2p_http_mirrors:
            i2p_http_endpoint(mirror)
        manifest = fetch(
            arguments.i2p_http_manifest,
            trust,
            tuple(arguments.i2p_http_mirrors),
            arguments.i2p_http_store,
            policy,
            control=control,
        )
    return {
        "manifest": arguments.i2p_http_manifest,
        "member_count": len(manifest.members),
        "logical_bytes": manifest.total_bytes,
        "verification": "complete",
        "transport": "i2p-http",
        "payload_uploads": False,
    }
