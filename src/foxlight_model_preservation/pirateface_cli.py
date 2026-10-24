"""Standalone PirateFace candidate lookup and explicitly selected offline import."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from pydantic import JsonValue

from .models import MAX_METADATA_BYTES, PreservationError
from .operations import OperationControl
from .pirateface import DEFAULT_SOURCE, MAX_CANDIDATE_BYTES, lookup, read_candidate
from .pirateface_import import import_candidate
from .signing import digest, encode_record, load_trust
from .transfer import TransferPolicy


class PirateFaceArguments(argparse.Namespace):
    """Only explicit PirateFace effects; no implicit client or rescue submission."""

    pirateface_action: str
    repository: str
    source: str
    output: Path
    allow_http: bool
    allow_private: bool
    deadline_seconds: float
    candidate: Path
    metainfo: Path
    envelope: Path
    trust: Path | None = None
    candidate_member: str
    member: list[str]
    document: list[str]
    store: Path
    manifest: str | list[str]
    max_bytes: int


def register_pirateface_commands(
    add_parser: Callable[..., argparse.ArgumentParser],
) -> None:
    """Register separate metadata lookup and offline signed-archive intake."""
    parser = add_parser(
        "pirateface", help="untrusted lookup and verified offline intake"
    )
    commands = parser.add_subparsers(dest="pirateface_action", required=True)
    query = commands.add_parser(
        "lookup", help="GET one exact repository; no swarm participation"
    )
    query.add_argument("repository", metavar="OWNER/REPOSITORY")
    query.add_argument("--source", default=DEFAULT_SOURCE)
    query.add_argument("--output", type=Path, required=True)
    query.add_argument("--allow-http", action="store_true")
    query.add_argument("--allow-private", action="store_true")
    query.add_argument("--deadline-seconds", type=float, default=60)
    receive = commands.add_parser(
        "import", help="verify existing downloaded files; no network"
    )
    receive.add_argument("manifest", metavar="MANIFEST_SHA256")
    for name in ("candidate", "metainfo", "envelope", "trust", "store"):
        receive.add_argument("--" + name, type=Path, required=True)
    receive.add_argument("--candidate-member", required=True)
    receive.add_argument(
        "--member", action="append", required=True, metavar="ID=DIRECTORY"
    )
    receive.add_argument("--document", action="append", default=[], metavar="ID=FILE")
    receive.add_argument("--max-bytes", type=int, default=20 * 1024**3)
    for command in (query, receive):
        command.add_argument("--progress", action="store_true")


def _read(path: Path, bound: int = MAX_METADATA_BYTES) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(bound + 1)
    if len(data) > bound:
        raise PreservationError("adapter input exceeds its metadata bound")
    return data


def _paths(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        name, separator, path = value.partition("=")
        if not separator or not name or not path or name in result:
            raise PreservationError("require unique ID=PATH inputs")
        result[name] = Path(path)
    return result


def run_pirateface_command(
    arguments: PirateFaceArguments, control: OperationControl
) -> dict[str, JsonValue]:
    """Run only the selected effect; neither action can start a peer session."""
    if arguments.pirateface_action == "lookup":
        candidate = lookup(
            arguments.repository,
            source=arguments.source,
            policy=TransferPolicy(
                deadline_seconds=arguments.deadline_seconds,
                allow_http=arguments.allow_http,
                allow_private=arguments.allow_private,
            ),
            control=control,
            output=arguments.output,
        )
        return {
            "candidate_id": digest(encode_record(candidate)),
            "authority": candidate.authority,
            "peer_network_started": False,
            "sharing_started": False,
        }
    if arguments.trust is None or not isinstance(arguments.manifest, str):
        raise PreservationError("independent trust and one exact manifest are required")
    candidate = read_candidate(_read(arguments.candidate, MAX_CANDIDATE_BYTES))
    manifest = import_candidate(
        candidate,
        _read(arguments.metainfo),
        _read(arguments.envelope),
        arguments.manifest,
        load_trust(_read(arguments.trust)),
        arguments.candidate_member,
        _paths(arguments.member),
        arguments.store,
        documents=_paths(arguments.document),
        max_bytes=arguments.max_bytes,
        control=control,
    )
    return {
        "manifest_id": arguments.manifest,
        "candidate_id": digest(encode_record(candidate)),
        "infohash": candidate.infohash,
        "member_count": len(manifest.members),
        "verification": "complete",
        "network_started": False,
        "sharing_started": False,
        "registry_authority_granted": False,
    }
