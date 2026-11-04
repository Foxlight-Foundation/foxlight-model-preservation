"""Receive-side mirror discovery options and the private ``mirrors`` directory.

These commands only read, learn and score. None of them announces, listens,
seeds or publishes. Only ``--look-further`` contacts the BitTorrent DHT or Nostr
relays, and only when it is given.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from pydantic import JsonValue

from .mirror_cards import MAX_PEERS_BYTES, build_peers, read_peers
from .mirror_directory import (
    MirrorDirectory,
    default_directory,
    load_seeds,
    look_further,
    permitted,
)
from .models import PreservationError
from .operations import OperationControl
from .transfer import TransferPolicy

LOOK_FURTHER_HELP = (
    "also ask the public BitTorrent DHT and Nostr relays for mirrors before "
    "receiving. PRIVACY: this shows your IP address to DHT nodes and relay "
    "operators (strangers) and briefly joins the DHT; nothing is announced. Off "
    "by default"
)
NO_DISCOVERY_HELP = (
    "use only the given mirrors: do not read their peers documents, do not add "
    "mirrors from your private directory or seed list, and record nothing"
)


class DirectoryArguments(argparse.Namespace):
    """Discovery choices shared by every direct HTTP receive command."""

    discovered_mirrors: bool = True
    mirror_directory: Path | None = None
    seed_mirrors: Path | None = None
    look_further: bool = False
    directory_action: str
    directory_file: Path
    directory_output: Path
    directory_seed: list[str]
    directory_allow_http: bool = False
    directory_allow_private: bool = False
    directory_max_documents: int = 32
    directory_expand: bool = True


def add_receive_options(parser: argparse.ArgumentParser) -> None:
    """Add discovery opt-out, directory, seed and opt-in lookup flags."""
    parser.add_argument(
        "--no-discovered-mirrors",
        dest="discovered_mirrors",
        action="store_false",
        help=NO_DISCOVERY_HELP,
    )
    parser.add_argument(
        "--mirror-directory",
        type=Path,
        help="private mirror directory (default: $SOVEREIGN_MIRROR_DIRECTORY or "
        "~/.local/state/foxlight-preservation/mirror-directory)",
    )
    parser.add_argument(
        "--seed-mirrors",
        type=Path,
        help="an extra untrusted sovereign.seed-mirrors.v1 file "
        "(default: $SOVEREIGN_SEED_MIRRORS)",
    )
    parser.add_argument("--look-further", action="store_true", help=LOOK_FURTHER_HELP)


def receive_discovery(
    arguments: DirectoryArguments,
    policy: TransferPolicy,
    control: OperationControl | None,
) -> tuple[MirrorDirectory | None, tuple[str, ...]]:
    """Open the private directory and seeds for one receive, or opt out.

    ``--look-further`` runs the explicit DHT and relay lookup here, before the
    receive; it is refused together with ``--no-discovered-mirrors``.
    """
    if not arguments.discovered_mirrors:
        if arguments.look_further:
            raise PreservationError(
                "--look-further needs discovered mirrors; drop --no-discovered-mirrors"
            )
        return None, ()
    mirrors = MirrorDirectory(arguments.mirror_directory or default_directory())
    seeds = load_seeds(arguments.seed_mirrors)
    if arguments.look_further:
        look_further(mirrors, policy, control=control)
    return mirrors, seeds


def register_directory_commands(
    add_parser: Callable[..., argparse.ArgumentParser],
) -> None:
    """Register ``mirrors list|import|export|crawl`` for the private directory."""
    parser = add_parser(
        "mirrors",
        help="your private, untrusted mirror directory (never shared as-is)",
    )
    actions = parser.add_subparsers(dest="directory_action", required=True)
    listing = actions.add_parser("list", help="show known mirrors and scores")
    importing = actions.add_parser(
        "import", help="add the verified cards in a peers.json document"
    )
    importing.add_argument("directory_file", type=Path, metavar="PEERS_JSON")
    exporting = actions.add_parser(
        "export", help="write live cards as a peers.json document (no scores)"
    )
    exporting.add_argument(
        "--output", dest="directory_output", type=Path, required=True
    )
    crawl = actions.add_parser(
        "crawl", help="follow peers documents from known mirrors and seeds"
    )
    crawl.add_argument(
        "--seed",
        dest="directory_seed",
        action="append",
        default=[],
        help="an extra mirror base URL to start from",
    )
    crawl.add_argument("--allow-http", dest="directory_allow_http", action="store_true")
    crawl.add_argument(
        "--allow-private", dest="directory_allow_private", action="store_true"
    )
    crawl.add_argument(
        "--max-documents",
        dest="directory_max_documents",
        type=int,
        default=32,
        help="peers documents to read at most (1-256)",
    )
    crawl.add_argument(
        "--no-expand",
        dest="directory_expand",
        action="store_false",
        help="ask only known mirrors and seeds, not mirrors they name",
    )
    crawl.add_argument("--look-further", action="store_true", help=LOOK_FURTHER_HELP)
    for command in (listing, importing, exporting, crawl):
        command.add_argument("--mirror-directory", type=Path)
        if command is crawl:
            command.add_argument("--seed-mirrors", type=Path)


def _summary(mirrors: MirrorDirectory) -> dict[str, JsonValue]:
    return {
        "directory": str(mirrors.path),
        "mirrors": [dict(item) for item in mirrors.listing()],
        "seed_scores": {
            address: {"successes": score.successes, "failures": score.failures}
            for address, score in sorted(mirrors.read_seeds().seeds.items())
        },
        "trusted": False,
    }


def run_directory_command(
    arguments: DirectoryArguments, control: OperationControl | None
) -> dict[str, JsonValue]:
    """Execute one private-directory action; only ``crawl`` uses the network."""
    mirrors = MirrorDirectory(arguments.mirror_directory or default_directory())
    action = arguments.directory_action
    if action == "list":
        return {**_summary(mirrors), "network_requests": 0}
    if action == "import":
        with arguments.directory_file.open("rb") as stream:
            data = stream.read(MAX_PEERS_BYTES + 1)
        cards = read_peers(data)
        added = mirrors.ingest(cards)
        return {"verified_cards": len(cards), "added": added, "network_requests": 0}
    if action == "export":
        cards = mirrors.export_cards()
        document = build_peers(None, cards)
        with arguments.directory_output.open("xb") as stream:
            stream.write(document)
        return {
            "exported_cards": len(read_peers(document)),
            "scores_included": False,
            "network_requests": 0,
        }
    if action != "crawl":
        raise PreservationError("unknown mirrors action")
    if not 1 <= arguments.directory_max_documents <= 256:
        raise PreservationError("--max-documents must be between 1 and 256")
    policy = TransferPolicy(
        allow_http=arguments.directory_allow_http,
        allow_private=arguments.directory_allow_private,
        timeout=10,
        deadline_seconds=300,
    )
    seeds = tuple(
        item
        for item in (*arguments.directory_seed, *load_seeds(arguments.seed_mirrors))
        if permitted(item, policy)
    )
    further: dict[str, int | bool] | None = None
    if arguments.look_further:
        further = look_further(mirrors, policy, control=control)
    added = mirrors.crawl(
        seeds,
        policy,
        max_documents=arguments.directory_max_documents,
        expand=arguments.directory_expand,
        control=control,
    )
    result: dict[str, JsonValue] = {
        "added": added,
        "known": len(mirrors.read().mirrors),
        "looked_further": further is not None,
    }
    if further is not None:
        result["look_further"] = dict(further)
    return result
