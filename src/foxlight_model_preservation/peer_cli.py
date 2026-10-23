"""Explicit standalone IPFS peer commands; Skulk and its SDK are not imported."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Literal, cast

from pydantic import JsonValue

from .discovery import read_source_document, refresh_source_from_directory, select_entry
from .discovery_models import SourceBootstrap
from .models import PreservationError
from .operations import OperationControl
from .peer import (
    PeerSource,
    fetch_peer_model,
    follow_peer_source,
    inspect_peer_source,
    peer_source_identity,
    publish_peer_model,
    publish_peer_source,
    refresh_peer_source,
)
from .peer_invitations import (
    fetch_peer_invitation,
    inspect_peer_model,
    read_peer_invitation,
)
from .peer_runtime import (
    PeerClient,
    executable,
    initialize_peer,
    run_peer,
    set_bootstrap,
)
from .signing import encode_record, load_trust


class PeerArguments(argparse.Namespace):
    """Typed finite peer effects; imported files cannot supply local permissions."""

    command: str
    peer_repo: Path
    ipfs: str | None = None
    peer_mode: Literal["receive", "mirror"]
    bootstrap_address: list[str]
    allow_private_peers: bool
    allow_peer_network: bool
    share_public: bool
    peer_seconds: int
    peer_max_upload_bytes: int
    peer_deadline_seconds: float
    source_file: Path
    output: Path
    ipns_name: str
    snapshot_cid: list[str]
    expected_peer_id: str
    cache: Path
    source_id: str | None = None
    historical: bool
    public_metadata: bool
    metadata_directory: Path | None = None
    public_model: bool
    store: Path
    manifest: str | list[str]
    trust: Path | None = None
    bundle_trust: Path | None = None
    entry: str
    expected_manifest_id: str
    max_bytes: int
    peer_invitation: Path
    operation_id: str | None = None


def register_peer_commands(add_parser: Callable[..., argparse.ArgumentParser]) -> None:
    """Register finite and separately consented peer commands in the public CLI."""
    for command in (
        "peer-init",
        "peer-bootstrap-set",
        "peer-run",
        "peer-status",
        "peer-source-file",
        "peer-inspect",
        "peer-follow",
        "peer-refresh",
        "peer-source-publish",
        "peer-source-mirror",
        "peer-model-publish",
        "peer-fetch",
        "peer-model-metadata",
        "peer-invitation-inspect",
        "peer-invitation-fetch",
    ):
        parser = add_parser(
            command, help="optional IPFS peer discovery and public distribution"
        )
        if command not in {
            "peer-source-file",
            "peer-inspect",
            "peer-follow",
            "peer-invitation-inspect",
        }:
            parser.add_argument("--repo", dest="peer_repo", type=Path, required=True)
            parser.add_argument("--ipfs")
        if command not in {
            "peer-init",
            "peer-bootstrap-set",
            "peer-source-file",
            "peer-inspect",
            "peer-follow",
            "peer-invitation-inspect",
        }:
            parser.add_argument("--allow-peer-network", action="store_true")
            parser.add_argument(
                "--deadline-seconds",
                dest="peer_deadline_seconds",
                type=float,
                default=86400
                if command in {"peer-fetch", "peer-invitation-fetch"}
                else 3600
                if command == "peer-model-publish"
                else 120,
            )
        if command in {
            "peer-follow",
            "peer-refresh",
            "peer-source-publish",
            "peer-source-mirror",
            "peer-fetch",
            "peer-model-metadata",
        }:
            parser.add_argument(
                "--cache",
                type=Path,
                default=Path.home() / ".local/state/foxlight-preservation/discovery",
            )
        if command in {
            "peer-refresh",
            "peer-source-publish",
            "peer-source-mirror",
            "peer-fetch",
            "peer-model-metadata",
        }:
            parser.add_argument("--source", dest="source_id", required=True)
        if command in {
            "peer-source-file",
            "peer-inspect",
            "peer-follow",
        }:
            parser.add_argument("--source-file", type=Path, required=True)
        if command == "peer-init":
            parser.add_argument(
                "--mode",
                dest="peer_mode",
                choices=("receive", "mirror"),
                default="receive",
            )
            parser.add_argument("--allow-private-peers", action="store_true")
        if command in {"peer-init", "peer-bootstrap-set"}:
            parser.add_argument("--bootstrap-address", action="append", default=[])
        if command == "peer-run":
            parser.add_argument("--share-public", action="store_true")
            parser.add_argument(
                "--seconds", dest="peer_seconds", type=int, default=3600
            )
            parser.add_argument(
                "--max-upload-bytes",
                dest="peer_max_upload_bytes",
                type=int,
                default=1024**3,
            )
        if command == "peer-source-file":
            parser.add_argument("--ipns-name", required=True)
            parser.add_argument("--snapshot-cid", action="append", default=[])
            parser.add_argument("--output", type=Path, required=True)
        if command == "peer-follow":
            parser.add_argument("--expected-peer-id", required=True)
        if command == "peer-refresh":
            parser.add_argument("--historical", action="store_true")
        if command in {"peer-source-publish", "peer-source-mirror"}:
            parser.add_argument("--public-metadata", action="store_true")
            parser.add_argument("--metadata-directory", type=Path)
        if command in {"peer-fetch", "peer-model-publish", "peer-invitation-fetch"}:
            parser.add_argument("--store", type=Path, required=True)
        if command == "peer-model-publish":
            parser.add_argument("--manifest", required=True)
            parser.add_argument("--trust", type=Path, required=True)
            parser.add_argument("--public-model", action="store_true")
        if command in {"peer-fetch", "peer-model-metadata"}:
            parser.add_argument("--entry", required=True)
            parser.add_argument("--expected-manifest-id", required=True)
            parser.add_argument("--bundle-trust", type=Path, required=True)
        if command in {"peer-fetch", "peer-invitation-fetch"}:
            parser.add_argument("--max-bytes", type=int, default=20 * 1024**3)
        if command == "peer-model-metadata":
            parser.add_argument("--output", type=Path, required=True)
        if command in {"peer-invitation-inspect", "peer-invitation-fetch"}:
            parser.add_argument(
                "--invitation", dest="peer_invitation", type=Path, required=True
            )
        if command == "peer-invitation-fetch":
            parser.add_argument("--bundle-trust", type=Path, required=True)
            parser.add_argument("--operation-id")


def _read(path: Path, maximum: int) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise PreservationError("peer input exceeds its byte bound")
    return data


class _PublicationControl:
    def __init__(self, parent: OperationControl):
        self.parent = parent

    def checkpoint(self) -> None:
        self.parent.checkpoint()

    def advance(
        self, *, hashed: int = 0, copied: int = 0, received: int = 0, reused: int = 0
    ) -> None:
        self.parent.advance(
            hashed=hashed, copied=copied, received=received, reused=reused
        )

    def mark_committed(self) -> None:
        # This intermediate cache commit does not finish the public operation.
        pass


def run_peer_command(
    arguments: PeerArguments, control: OperationControl
) -> dict[str, JsonValue]:
    """Keep peer enrollment offline and require separate permission for network work."""
    command = arguments.command
    if command == "peer-invitation-inspect":
        invitation = read_peer_invitation(_read(arguments.peer_invitation, 8 * 1024**2))
        return {
            "manifest": invitation.manifest_id,
            "members": len(invitation.manifest.members),
            "network_requests": 0,
            "trusted": False,
        }
    if command == "peer-source-file":
        source = read_source_document(_read(arguments.source_file, 256 * 1024))
        if not isinstance(source, SourceBootstrap):
            raise PreservationError(
                "peer source needs the independently trusted original bootstrap"
            )
        record = PeerSource(
            source=source,
            ipns=arguments.ipns_name,
            snapshots=tuple(arguments.snapshot_cid),
        )
        with arguments.output.open("xb") as stream:
            _ = stream.write(encode_record(record))
        return {"peer_id": peer_source_identity(record), "network_requests": 0}
    if command == "peer-inspect":
        record = inspect_peer_source(_read(arguments.source_file, 256 * 1024))
        return {
            "peer_id": peer_source_identity(record),
            "ipns": record.ipns,
            "source_name": record.source.name,
            "snapshot_cids": list(record.snapshots),
            "authority": "discovery only; no model keys, registry or code permission",
            "network_requests": 0,
        }
    if command == "peer-follow":
        return follow_peer_source(
            arguments.cache,
            _read(arguments.source_file, 256 * 1024),
            arguments.expected_peer_id,
        ).model_dump(mode="json")
    if command == "peer-bootstrap-set":
        contacts = set_bootstrap(
            arguments.peer_repo, tuple(arguments.bootstrap_address)
        )
        return {"bootstrap": list(contacts), "network_requests": 0}
    binary = executable(arguments.ipfs)
    if command == "peer-init":
        return initialize_peer(
            arguments.peer_repo,
            binary,
            mode=arguments.peer_mode,
            bootstrap=tuple(arguments.bootstrap_address),
            allow_private_peers=arguments.allow_private_peers,
        ).model_dump(mode="json")
    if command == "peer-run":
        return run_peer(
            arguments.peer_repo,
            binary,
            allow_network=arguments.allow_peer_network,
            share_public=arguments.share_public,
            seconds=arguments.peer_seconds,
            max_upload_bytes=arguments.peer_max_upload_bytes,
            control=control,
        )
    client = PeerClient(
        arguments.peer_repo,
        binary,
        allow_network=arguments.allow_peer_network,
        control=control,
        seconds=arguments.peer_deadline_seconds,
    )
    if command == "peer-status":
        value = cast(dict[str, JsonValue], json.loads(client.command("id")))
        return {
            "peer_id": value.get("ID"),
            "ipns_name": client.publisher_name(),
            "addresses": value.get("Addresses"),
            "mode": client.profile.mode,
            "block_serving": client.profile.mode == "mirror",
        }
    if command == "peer-invitation-fetch" and arguments.bundle_trust is not None:
        invitation = read_peer_invitation(_read(arguments.peer_invitation, 8 * 1024**2))
        manifest = fetch_peer_invitation(
            invitation,
            load_trust(_read(arguments.bundle_trust, 8 * 1024**2)),
            arguments.store,
            client,
            maximum_bytes=arguments.max_bytes,
            operation_id=arguments.operation_id,
        )
        return {
            "manifest": invitation.manifest_id,
            "verified_bytes": manifest.total_bytes,
            "serving": False,
        }
    if (
        command == "peer-model-metadata"
        and arguments.source_id is not None
        and arguments.bundle_trust is not None
    ):
        entry = select_entry(
            arguments.cache,
            arguments.source_id,
            arguments.entry,
            arguments.expected_manifest_id,
        )
        invitation = inspect_peer_model(
            entry.manifest,
            load_trust(_read(arguments.bundle_trust, 8 * 1024**2)),
            entry.mirrors,
            client,
        )
        with arguments.output.open("xb") as stream:
            _ = stream.write(encode_record(invitation))
        return {"manifest": invitation.manifest_id, "weights_downloaded": 0}
    if arguments.source_id is None and command != "peer-model-publish":
        raise PreservationError("exact followed peer source required")
    if command == "peer-refresh" and arguments.source_id is not None:
        return refresh_peer_source(
            arguments.cache,
            arguments.source_id,
            client,
            historical=arguments.historical,
        ).model_dump(mode="json")
    if (
        command in {"peer-source-publish", "peer-source-mirror"}
        and arguments.source_id is not None
    ):
        if arguments.metadata_directory is not None:
            refresh_source_from_directory(
                arguments.cache,
                arguments.source_id,
                arguments.metadata_directory,
                control=_PublicationControl(control),
            )
        return dict(
            publish_peer_source(
                arguments.cache,
                arguments.source_id,
                client,
                public_metadata=arguments.public_metadata,
                publish_name=command == "peer-source-publish",
            )
        )
    if (
        command == "peer-model-publish"
        and arguments.trust is not None
        and isinstance(arguments.manifest, str)
    ):
        return dict(
            publish_peer_model(
                arguments.store,
                arguments.manifest,
                load_trust(_read(arguments.trust, 8 * 1024**2)),
                client,
                public_model=arguments.public_model,
            )
        )
    if (
        command == "peer-fetch"
        and arguments.bundle_trust is not None
        and arguments.source_id is not None
    ):
        manifest = fetch_peer_model(
            arguments.cache,
            arguments.source_id,
            arguments.entry,
            arguments.expected_manifest_id,
            load_trust(_read(arguments.bundle_trust, 8 * 1024**2)),
            arguments.store,
            client,
            maximum_bytes=arguments.max_bytes,
        )
        return {
            "manifest": arguments.expected_manifest_id,
            "verified_bytes": manifest.total_bytes,
            "exported_peer_bytes": client.exported_bytes,
            "serving": False,
        }
    raise PreservationError("unknown or incomplete peer action")
