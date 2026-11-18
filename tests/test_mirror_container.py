"""The container entrypoint's Nostr and BitTorrent switches (no network)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from platforms import posix_shell

pytestmark = posix_shell

SCRIPT = Path(__file__).parents[1] / "deploy/mirror/entrypoint.sh"


def calls(tmp_path: Path, **changes: str) -> list[list[str]]:
    """Run the entrypoint against a stub ``model-preserve``; return its calls."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "calls.log"
    if log.exists():
        log.unlink()
    stub = bin_dir / "model-preserve"
    stub.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n')
    stub.chmod(0o755)
    environment = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "SOVEREIGN_MIRROR_DIR": str(tmp_path / "data"),
        "SOVEREIGN_MIRROR_COLLECTION": str(tmp_path / "collection.json"),
        "SOVEREIGN_MIRROR_DOMAIN": "mirror.example",
        "SOVEREIGN_MIRROR_IDLE": "0",
        "SOVEREIGN_MIRROR_CONSENT": "yes-i-consent-to-public-serving",
        **changes,
    }
    subprocess.run(  # noqa: S603 - repository script, fixed arguments
        ["/bin/sh", str(SCRIPT)], env=environment, capture_output=True, timeout=30
    )
    return [line.split() for line in log.read_text().splitlines()]


def test_the_container_announces_and_shares_by_default(tmp_path: Path) -> None:
    setup, run = calls(tmp_path)
    for flag in (
        "--no-announce-nostr",
        "--nostr-relay",
        "--no-file-torrents",
        "--no-pirate-face",
    ):
        assert flag not in setup
    assert "--public-url" in setup
    assert run[:2] == ["mirror", "run"]
    assert run[run.index("--torrent-port") + 1] == "6881"


def test_each_part_can_be_turned_off(tmp_path: Path) -> None:
    setup, run = calls(
        tmp_path,
        SOVEREIGN_MIRROR_NOSTR="off",
        SOVEREIGN_MIRROR_BITTORRENT="OFF",
        SOVEREIGN_MIRROR_PIRATE_FACE="no",
        SOVEREIGN_MIRROR_TORRENT_PORT="51413",
    )
    assert "--no-announce-nostr" in setup
    assert "--no-file-torrents" in setup and "--no-pirate-face" in setup
    assert run[run.index("--torrent-port") + 1] == "51413"
    # Anything else leaves them on.
    setup, _run = calls(
        tmp_path, SOVEREIGN_MIRROR_NOSTR="on", SOVEREIGN_MIRROR_BITTORRENT="yes"
    )
    assert "--no-announce-nostr" not in setup and "--no-file-torrents" not in setup


def test_custom_relays_replace_the_default_ones(tmp_path: Path) -> None:
    setup, _run = calls(
        tmp_path,
        SOVEREIGN_MIRROR_NOSTR_RELAYS="wss://one.example, wss://two.example,",
    )
    relays = [
        setup[index + 1]
        for index, value in enumerate(setup)
        if value == "--nostr-relay"
    ]
    assert relays == ["wss://one.example", "wss://two.example"]
