"""Standalone source discovery commands, independent of Skulk and model admission."""

from __future__ import annotations

import argparse
import base64
import getpass
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import JsonValue, TypeAdapter
from tuf.api.exceptions import RepositoryError

from .discovery import (
    accept_source_clock_rollback,
    export_source,
    fetch_selection,
    follow_source,
    inspect_source,
    list_sources,
    refresh_source,
    remove_source,
    search_sources,
)
from .discovery_models import (
    MAX_ROOT_BYTES,
    MAX_STATE_BYTES,
    DiscoveryCatalogTarget,
    DiscoveryDescription,
    SourceBootstrap,
)
from .discovery_publish import DiscoverySigningKeys, publish_catalog
from .models import PreservationError
from .operations import OperationControl
from .signing import canonical_bytes, encode_record, load_key, load_trust, strict_json
from .transfer import TransferPolicy


class DiscoveryArguments(argparse.Namespace):
    """Discovery-only arguments shared with the existing command parser."""

    cache: Path
    source_file: Path
    source_id: str | None = None
    expected_bootstrap_id: str
    query: str
    limit: int
    allow_local: bool
    command: str
    allow_private: bool
    allow_http: bool
    grant_private: bool | None = None
    grant_http: bool | None = None
    grant_local: bool | None = None
    output: Path
    entry: str
    expected_manifest_id: str
    bundle_trust: Path | None = None
    store: Path
    max_bytes: int
    deadline_seconds: float = 3600
    accept_clock_rollback: bool = False
    operation_id: str | None = None
    root: Path | None = None
    endpoint: list[str]
    name: str
    index: Path
    catalog_id: str
    catalog_trust: Path
    descriptions: Path | None = None
    targets_key: Path
    snapshot_key: Path
    timestamp_key: Path
    passphrase_file: Path | None = None
    unencrypted: bool = False
    version: int
    expires: str | None = None


class _RefreshControl:
    """Keep each source's commit from consuming the batch's cancellation fence."""

    def __init__(self, parent: OperationControl):
        self._child = OperationControl()
        self._parent = parent

    def checkpoint(self) -> None:
        """Honor the still-cancellable batch before each source effect."""
        if not self._child.progress.committed:
            self._parent.checkpoint()
        self._child.checkpoint()

    def advance(
        self, *, hashed: int = 0, copied: int = 0, received: int = 0, reused: int = 0
    ) -> None:
        """Report aggregate bytes while retaining per-source commit state."""
        self._parent.advance(
            hashed=hashed, copied=copied, received=received, reused=reused
        )
        self._child.advance(
            hashed=hashed, copied=copied, received=received, reused=reused
        )

    def mark_committed(self) -> None:
        """Commit this source without consuming the batch cancellation fence."""
        self._child.mark_committed()


def register_commands(add_parser: Callable[..., argparse.ArgumentParser]) -> None:
    """Register effect-specific source commands without changing legacy commands."""
    inspect = add_parser("source-inspect", help="inspect a source/cache file offline")
    inspect.add_argument("--source-file", type=Path, required=True)
    bootstrap = add_parser(
        "source-bootstrap", help="create a portable source file from a public TUF root"
    )
    bootstrap.add_argument("--root", type=Path, required=True)
    bootstrap.add_argument("--name", required=True)
    bootstrap.add_argument("--endpoint", action="append", required=True)
    bootstrap.add_argument("--output", type=Path, required=True)
    publish = add_parser(
        "source-publish", help="publish an explicit catalog to static TUF storage"
    )
    publish.add_argument("--root", type=Path, required=True)
    publish.add_argument("--index", type=Path, required=True)
    publish.add_argument("--catalog-id", required=True)
    publish.add_argument("--catalog-trust", type=Path, required=True)
    publish.add_argument("--descriptions", type=Path)
    for role in ("targets", "snapshot", "timestamp"):
        publish.add_argument("--" + role + "-key", type=Path, required=True)
    passwords = publish.add_mutually_exclusive_group()
    passwords.add_argument("--passphrase-file", type=Path)
    passwords.add_argument("--unencrypted", action="store_true")
    publish.add_argument("--version", type=int, required=True)
    publish.add_argument(
        "--expires", help="UTC-aware ISO-8601 expiry; default seven days"
    )
    publish.add_argument("--output", type=Path, required=True)
    for name in (
        "source-follow",
        "source-list",
        "source-refresh",
        "source-search",
        "source-export",
        "source-remove",
        "source-fetch",
    ):
        command = add_parser(name, help="standalone authenticated source discovery")
        command.add_argument(
            "--cache",
            type=Path,
            default=Path.home() / ".local/state/foxlight-preservation/discovery",
        )
        if name == "source-follow":
            command.add_argument("--source-file", type=Path, required=True)
            command.add_argument("--expected-bootstrap-id", required=True)
            # Omitted keeps a re-followed source's current grant (none for a new
            # source); --no-allow-* revokes it explicitly.
            for grant in ("private", "http", "local"):
                command.add_argument(
                    "--allow-" + grant,
                    dest="grant_" + grant,
                    action=argparse.BooleanOptionalAction,
                    default=None,
                )
        if name in {"source-refresh", "source-export", "source-remove", "source-fetch"}:
            command.add_argument(
                "--source", dest="source_id", required=name != "source-refresh"
            )
        if name == "source-search":
            command.add_argument("query", nargs="?", default="")
            command.add_argument("--limit", type=int, default=100)
        if name == "source-export":
            command.add_argument("--output", type=Path, required=True)
        if name in {"source-refresh", "source-fetch"}:
            command.add_argument("--progress", action="store_true")
        if name == "source-refresh":
            command.add_argument(
                "--accept-clock-rollback",
                action="store_true",
                help="after correcting a clock that ran ahead, clear the recorded "
                "attempt time for --source (update floors are retained)",
            )
        if name == "source-fetch":
            command.add_argument("--entry", required=True)
            command.add_argument("--expected-manifest-id", required=True)
            command.add_argument("--bundle-trust", type=Path, required=True)
            command.add_argument("--store", type=Path, required=True)
            command.add_argument("--max-bytes", type=int, default=20 * 1024**3)
            command.add_argument(
                "--deadline-seconds",
                type=float,
                default=3600,
                help="overall transfer deadline for this invocation (max 86400)",
            )
            command.add_argument("--allow-private", action="store_true")
            command.add_argument("--allow-http", action="store_true")
            command.add_argument("--allow-local", action="store_true")
            command.add_argument("--operation-id")


def _input(path: Path, maximum: int = MAX_STATE_BYTES) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise PreservationError("source input exceeds its size bound")
    return data


def run_command(
    arguments: DiscoveryArguments, control: OperationControl | None
) -> dict[str, JsonValue]:
    """Execute a source operation without granting model trust."""
    if arguments.command == "source-inspect":
        return inspect_source(_input(arguments.source_file))
    if arguments.command == "source-bootstrap":
        if arguments.root is None:
            raise PreservationError("a public TUF root is required")
        bootstrap = SourceBootstrap(
            name=arguments.name,
            root=base64.b64encode(_input(arguments.root, MAX_ROOT_BYTES)).decode(
                "ascii"
            ),
            endpoints=tuple(arguments.endpoint),
        )
        data = encode_record(bootstrap)
        report = inspect_source(data)
        with arguments.output.open("xb") as stream:
            stream.write(data)
        return report
    if arguments.command == "source-publish":
        return _publish(arguments)
    if arguments.command == "source-follow":
        status = follow_source(
            arguments.cache,
            _input(arguments.source_file),
            arguments.expected_bootstrap_id,
            allow_private=arguments.grant_private,
            allow_http=arguments.grant_http,
            allow_local=arguments.grant_local,
        )
        return {"source": status.model_dump(mode="json")}
    if arguments.command == "source-list":
        return {
            "sources": [
                status.model_dump(mode="json")
                for status in list_sources(arguments.cache)
            ]
        }
    if arguments.command == "source-search":
        return {
            "results": [
                result.model_dump(mode="json")
                for result in search_sources(
                    arguments.cache, arguments.query, limit=arguments.limit
                )
            ],
            "sources": [
                status.model_dump(mode="json")
                for status in list_sources(arguments.cache)
            ],
            "network_requests": 0,
        }
    if arguments.command == "source-refresh":
        if arguments.accept_clock_rollback:
            if arguments.source_id is None:
                raise PreservationError("accepting a clock rollback needs --source")
            accept_source_clock_rollback(arguments.cache, arguments.source_id)
        identities = (
            [arguments.source_id]
            if arguments.source_id is not None
            else [
                status.source_id
                for status in list_sources(arguments.cache)
                if status.enabled
            ]
        )
        failures: dict[str, JsonValue] = {}
        parent_control = control or OperationControl()
        for identity in identities:
            try:
                refresh_source(
                    arguments.cache, identity, control=_RefreshControl(parent_control)
                )
            except (RepositoryError, ValueError, OSError) as error:
                # A cancelled refresh must not continue with other sources.
                if control is not None:
                    control.checkpoint()
                failures[identity] = str(error)
        return {
            "sources": [
                status.model_dump(mode="json")
                for status in list_sources(arguments.cache)
            ],
            "failures": failures,
        }
    if arguments.source_id is None:
        raise PreservationError("select one exact followed source")
    if arguments.command == "source-export":
        data = export_source(arguments.cache, arguments.source_id)
        with arguments.output.open("xb") as stream:
            stream.write(data)
        return {"source_id": arguments.source_id, "network_requests": 0}
    if arguments.command == "source-remove":
        remove_source(arguments.cache, arguments.source_id)
        return {
            "source_id": arguments.source_id,
            "following": False,
            "update_floors_retained": True,
        }
    if arguments.command != "source-fetch" or arguments.bundle_trust is None:
        raise PreservationError("source fetch needs independent bundle trust")
    trust = load_trust(_input(arguments.bundle_trust, 8 * 1024 * 1024))
    manifest = fetch_selection(
        arguments.cache,
        arguments.source_id,
        arguments.entry,
        arguments.expected_manifest_id,
        trust,
        arguments.store,
        TransferPolicy(
            max_bytes=arguments.max_bytes,
            deadline_seconds=arguments.deadline_seconds,
            allow_private=arguments.allow_private,
            allow_http=arguments.allow_http,
        ),
        control=control,
        operation_id=arguments.operation_id,
        allow_local=arguments.allow_local,
    )
    return {
        "manifest": arguments.expected_manifest_id,
        "verified_bytes": manifest.total_bytes,
        "trust_as_of": trust.as_of,
        "attestation": manifest.attestation,
    }


def _publish(arguments: DiscoveryArguments) -> dict[str, JsonValue]:
    if arguments.root is None:
        raise PreservationError("a public TUF root is required")
    output = arguments.output.resolve()
    secrets = [arguments.targets_key, arguments.snapshot_key, arguments.timestamp_key]
    if arguments.passphrase_file is not None:
        secrets.append(arguments.passphrase_file)
    if any(path.resolve().is_relative_to(output) for path in secrets):
        raise PreservationError(
            "keep role keys and passphrases outside public source storage"
        )
    password = None
    if not arguments.unencrypted:
        password = (
            _input(arguments.passphrase_file, 8192).rstrip(b"\r\n")
            if arguments.passphrase_file is not None
            else getpass.getpass("Discovery role key passphrase: ").encode()
        )
        if not password:
            raise PreservationError("role keys require a nonempty passphrase")
    descriptions = (
        ()
        if arguments.descriptions is None
        else TypeAdapter(tuple[DiscoveryDescription, ...]).validate_json(
            canonical_bytes(strict_json(_input(arguments.descriptions)))
        )
    )
    profile = DiscoveryCatalogTarget(
        catalog_id=arguments.catalog_id,
        catalog_trust=load_trust(_input(arguments.catalog_trust)),
        descriptions=descriptions,
    )
    expires = (
        datetime.fromisoformat(arguments.expires)
        if arguments.expires is not None
        else datetime.now(UTC) + timedelta(days=7)
    )
    identity = publish_catalog(
        arguments.output,
        _input(arguments.root, MAX_ROOT_BYTES),
        _input(arguments.index),
        profile,
        DiscoverySigningKeys(
            load_key(arguments.targets_key, password),
            load_key(arguments.snapshot_key, password),
            load_key(arguments.timestamp_key, password),
        ),
        version=arguments.version,
        expires=expires,
    )
    return {
        "catalog": identity,
        "version": arguments.version,
        "expires": expires.isoformat(),
        "serving_started": False,
    }
