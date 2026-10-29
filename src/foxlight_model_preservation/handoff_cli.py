"""Standalone one-file review/receive and finite, explicitly opted-in sharing."""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import JsonValue

from .archives import verify_archive_contents
from .discovery import source_identity
from .handoffs import (
    Handoff,
    handoff_identity,
    read_handoff,
    read_reference,
    reference_contacts,
    write_handoff,
)
from .invitations import (
    Invitation,
    accept_invitation_keys,
    authenticate_invitation,
    fetch_invitation,
)
from .models import MAX_METADATA_BYTES, PreservationError, TrustProfile
from .operations import DeadlineExceeded, OperationControl
from .peer import (
    PeerSource,
    follow_peer_source,
    peer_source_identity,
    refresh_peer_source,
)
from .peer_invitations import (
    PeerInvitation,
    accept_peer_invitation_keys,
    authenticate_peer_invitation,
    fetch_peer_invitation,
)
from .peer_runtime import bootstrap_address, executable
from .peer_session import owned_peer
from .peer_sharing import prepare_peer_handoff
from .signing import encode_record, load_trust, strict_json
from .transfer import TransferPolicy

COMMANDS = (
    "handoff-create",
    "handoff-inspect",
    "handoff-fetch",
    "handoff-follow",
    "handoff-refresh",
    "peer-share",
)


class HandoffArguments(argparse.Namespace):
    """Caller-owned paths and permissions; handoffs cannot provide these values."""

    command: str
    handoff_file: Path
    reference_file: Path
    handoff_output: Path
    handoff_contact: list[str]
    handoff_repo: Path | None = None
    handoff_binary: str | None = None
    handoff_network: bool = False
    handoff_private: bool = False
    handoff_seconds: int = 3600
    handoff_trust: Path | None = None
    handoff_keys: list[str]
    handoff_store: Path
    handoff_max_bytes: int
    handoff_operation: str | None = None
    handoff_http: bool = False
    handoff_http_private: bool = False
    handoff_cache: Path
    handoff_peer_id: str
    handoff_historical: bool = False
    handoff_manifest: str
    handoff_public: bool = False
    handoff_public_model: bool = False
    handoff_upload: int


def register_handoff_commands(
    add_parser: Callable[..., argparse.ArgumentParser],
) -> None:
    """Expose offline review separately from independently authorized effects."""
    for command in COMMANDS:
        parser = add_parser(
            command, help="portable one-file handoff and explicit sharing"
        )
        if command != "peer-share":
            parser.add_argument(
                "--reference" if command == "handoff-create" else "--handoff",
                dest="reference_file"
                if command == "handoff-create"
                else "handoff_file",
                type=Path,
                required=True,
            )
        if command in {"handoff-create", "peer-share"}:
            parser.add_argument(
                "--output", dest="handoff_output", type=Path, required=True
            )
            parser.add_argument(
                "--contact" if command == "handoff-create" else "--bootstrap-address",
                dest="handoff_contact",
                action="append",
                default=[],
            )
        if command in {"handoff-fetch", "handoff-refresh", "peer-share"}:
            parser.add_argument("--repo", dest="handoff_repo", type=Path)
            parser.add_argument("--ipfs", dest="handoff_binary")
            parser.add_argument(
                "--allow-peer-network", dest="handoff_network", action="store_true"
            )
            parser.add_argument(
                "--allow-private-peers", dest="handoff_private", action="store_true"
            )
            parser.add_argument(
                "--seconds", dest="handoff_seconds", type=int, default=3600
            )
        if command in {"handoff-fetch", "peer-share"}:
            parser.add_argument(
                "--store", dest="handoff_store", type=Path, required=True
            )
            if command == "peer-share":
                parser.add_argument(
                    "--trust", dest="handoff_trust", type=Path, required=True
                )
            else:
                trust = parser.add_mutually_exclusive_group(required=True)
                trust.add_argument("--bundle-trust", dest="handoff_trust", type=Path)
                trust.add_argument(
                    "--accept-key", dest="handoff_keys", action="append", default=[]
                )
                parser.add_argument(
                    "--max-bytes",
                    dest="handoff_max_bytes",
                    type=int,
                    default=20 * 1024**3,
                )
                parser.add_argument("--operation-id", dest="handoff_operation")
                parser.add_argument(
                    "--allow-http", dest="handoff_http", action="store_true"
                )
                parser.add_argument(
                    "--allow-private", dest="handoff_http_private", action="store_true"
                )
        if command in {"handoff-follow", "handoff-refresh"}:
            parser.add_argument(
                "--cache",
                dest="handoff_cache",
                type=Path,
                default=Path.home() / ".local/state/foxlight-preservation/discovery",
            )
            if command == "handoff-follow":
                parser.add_argument(
                    "--expected-peer-id", dest="handoff_peer_id", required=True
                )
            else:
                parser.add_argument(
                    "--historical", dest="handoff_historical", action="store_true"
                )
        if command == "peer-share":
            parser.add_argument("--manifest", dest="handoff_manifest", required=True)
            parser.add_argument(
                "--share-public", dest="handoff_public", action="store_true"
            )
            parser.add_argument(
                "--public-model", dest="handoff_public_model", action="store_true"
            )
            parser.add_argument(
                "--max-upload-bytes", dest="handoff_upload", type=int, default=1024**3
            )


def _read(path: Path) -> bytes:
    with path.open("rb") as source:
        value = source.read(MAX_METADATA_BYTES + 1)
    if len(value) > MAX_METADATA_BYTES:
        raise PreservationError("handoff metadata exceeds bound")
    return value


def _trust(
    args: HandoffArguments, reference: Invitation | PeerInvitation
) -> TrustProfile:
    if args.handoff_trust is not None:
        return load_trust(_read(args.handoff_trust))
    if isinstance(reference, PeerInvitation):
        return accept_peer_invitation_keys(reference, tuple(args.handoff_keys))
    return accept_invitation_keys(reference, tuple(args.handoff_keys))


def _inspect(data: bytes) -> dict[str, JsonValue]:
    reference = read_reference(data)
    value = strict_json(data)
    identity = (
        handoff_identity(read_handoff(data))
        if isinstance(value, dict)
        and value.get("schema_version") == "foxlight.model-preservation.handoff.v1"
        else None
    )
    common: dict[str, JsonValue] = {
        "handoff_id": identity,
        "contacts": list(reference_contacts(data)),
        "network_requests": 0,
        "trusted": False,
        "serving": False,
    }
    if isinstance(reference, PeerSource):
        return {
            **common,
            "kind": "source",
            "peer_id": peer_source_identity(reference),
            "source_id": source_identity(reference.source),
            "name": reference.source.name,
            "snapshot_cids": list(reference.snapshots),
            "authority": "discovery only; no model, registry or code grant",
        }
    return {
        **common,
        "kind": "model",
        "manifest": reference.manifest_id,
        "name": reference.manifest.name,
        "candidate_key_ids": [key.key_id for key in reference.candidate_trust.keys],
        "candidate_threshold": reference.candidate_trust.threshold,
        "mirrors": list(reference.mirrors),
        "authority": "candidate exact-copy keys; no registry or code grant",
    }


def run_handoff_command(
    args: HandoffArguments, control: OperationControl
) -> dict[str, JsonValue]:
    """Execute only caller-approved effects; inspection/export stays offline."""
    if args.command == "peer-share":
        return _share(args, control)
    data = _read(
        args.reference_file if args.command == "handoff-create" else args.handoff_file
    )
    reference = read_reference(data)
    if args.command == "handoff-create":
        handoff = Handoff(reference=reference, contacts=tuple(args.handoff_contact))
        write_handoff(args.handoff_output, handoff)
        return _inspect(encode_record(handoff))
    if args.command == "handoff-inspect":
        return _inspect(data)
    if args.command == "handoff-follow":
        if not isinstance(reference, PeerSource):
            raise PreservationError("source handoff required")
        return follow_peer_source(
            args.handoff_cache, encode_record(reference), args.handoff_peer_id
        ).model_dump(mode="json")
    trust: TrustProfile | None = None
    if isinstance(reference, (Invitation, PeerInvitation)):
        if args.command != "handoff-fetch":
            raise PreservationError("model handoff needs handoff-fetch")
        trust = _trust(args, reference)
        # Authenticate the selected exact copy before native startup, DNS or bytes.
        if isinstance(reference, Invitation):
            authenticate_invitation(reference, trust)
            if args.handoff_network or args.handoff_private:
                raise PreservationError(
                    "HTTP receiving cannot grant peer participation"
                )
            manifest = fetch_invitation(
                reference,
                trust,
                args.handoff_store,
                TransferPolicy(
                    max_bytes=args.handoff_max_bytes,
                    allow_http=args.handoff_http,
                    allow_private=args.handoff_http_private,
                    deadline_seconds=args.handoff_seconds,
                ),
                operation_id=args.handoff_operation,
                control=control,
            )
            return {
                "manifest": reference.manifest_id,
                "verified_bytes": manifest.total_bytes,
                "serving": False,
            }
        authenticate_peer_invitation(reference, trust)
    elif args.command != "handoff-refresh":
        raise PreservationError(
            "source handoff needs handoff-follow or handoff-refresh"
        )
    if not args.handoff_network or args.handoff_repo is None:
        raise PreservationError(
            "peer receiving needs --allow-peer-network and a fresh --repo"
        )
    contacts = reference_contacts(data)
    if not contacts:
        raise PreservationError("peer handoff needs starting contacts")
    for contact in contacts:
        bootstrap_address(contact, args.handoff_private)
    if not 1 <= args.handoff_seconds <= 86400:
        raise PreservationError("finite receiving lifetime required")
    with owned_peer(
        args.handoff_repo,
        executable(args.handoff_binary),
        mode="receive",
        contacts=contacts,
        allow_network=True,
        allow_private=args.handoff_private,
        seconds=args.handoff_seconds,
        control=control,
    ) as client:
        if isinstance(reference, PeerSource):
            return refresh_peer_source(
                args.handoff_cache,
                source_identity(reference.source),
                client,
                historical=args.handoff_historical,
            ).model_dump(mode="json")
        if trust is None:
            raise PreservationError("model handoff needs independent trust")
        manifest = fetch_peer_invitation(
            reference,
            trust,
            args.handoff_store,
            client,
            maximum_bytes=args.handoff_max_bytes,
            operation_id=args.handoff_operation,
        )
        return {
            "manifest": reference.manifest_id,
            "verified_bytes": manifest.total_bytes,
            "serving": False,
        }


def _share(args: HandoffArguments, control: OperationControl) -> dict[str, JsonValue]:
    if not (args.handoff_network and args.handoff_public and args.handoff_public_model):
        raise PreservationError(
            "peer sharing needs explicit network/public/model grants"
        )
    if args.handoff_repo is None or args.handoff_trust is None:
        raise PreservationError(
            "peer sharing needs a fresh --repo and independent --trust"
        )
    if args.handoff_output.exists() or args.handoff_output.is_symlink():
        raise PreservationError("handoff output must be a new file")
    trust = load_trust(_read(args.handoff_trust))
    with control.operation("seed"):
        manifest = verify_archive_contents(
            args.handoff_store, args.handoff_manifest, trust, control
        )
        if any(member.intake.audience != "public" for member in manifest.members):
            raise PreservationError("public complete model required")
        contacts = tuple(args.handoff_contact)
        with owned_peer(
            args.handoff_repo,
            executable(args.handoff_binary),
            mode="mirror",
            contacts=contacts,
            allow_network=True,
            allow_private=args.handoff_private,
            share_public=True,
            seconds=args.handoff_seconds,
            max_upload_bytes=args.handoff_upload,
            control=control,
        ) as client:
            handoff = prepare_peer_handoff(
                args.handoff_store,
                args.handoff_manifest,
                trust,
                client,
                public_model=True,
                bootstrap=contacts,
            )
            write_handoff(args.handoff_output, handoff)
            print(
                json.dumps(
                    {
                        "ready": True,
                        "handoff_id": handoff_identity(handoff),
                        "manifest": args.handoff_manifest,
                        "reachability_verified": False,
                    }
                ),
                flush=True,
            )
            control.phase("sharing")
            while time.monotonic() < client.deadline:
                control.checkpoint()
                try:
                    if client.wire_usage() >= args.handoff_upload:
                        break
                except (OSError, DeadlineExceeded):
                    break
                time.sleep(0.2)
        return {
            "stopped": True,
            "manifest": args.handoff_manifest,
            "archive_retained": True,
        }
