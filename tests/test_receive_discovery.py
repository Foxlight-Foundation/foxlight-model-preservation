"""Receives use discovered mirrors after their hints, under the receiver's policy."""

from __future__ import annotations

import http.server
import random
import shutil
import threading
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from conftest import Capture
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_invitations import invitation

from foxlight_model_preservation import mirror_rendezvous, nostr
from foxlight_model_preservation.cli import Arguments, argument_parser, run
from foxlight_model_preservation.handoffs import Handoff, write_handoff
from foxlight_model_preservation.mirror_cards import (
    build_peers,
    issue_card,
    verify_card,
)
from foxlight_model_preservation.mirror_directory import (
    MirrorDirectory,
    fetch_with_mirrors,
    load_seeds,
    permitted,
    plan_sources,
    read_seed_list,
)
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.seed import SeedServer
from foxlight_model_preservation.signing import encode_record
from foxlight_model_preservation.storage import object_path, verify_store
from foxlight_model_preservation.transfer import DEFAULT_POLICY, TransferPolicy

LAB = TransferPolicy(allow_http=True, allow_private=True, timeout=3)
PEERS = "/.well-known/sovereign/peers.json"


def arguments(*values: object) -> Arguments:
    return argument_parser().parse_args(list(map(str, values)), namespace=Arguments())


@contextmanager
def peers_only(document: Path) -> Generator[tuple[str, list[str]]]:
    """A host that serves only a peers document and holds no model."""
    requests: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            requests.append(self.path)
            if self.path != PEERS or not document.exists():
                self.send_error(404)
                return
            data = document.read_bytes()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@contextmanager
def holding(store: Path, capture: Capture) -> Iterator[str]:
    server = SeedServer(("127.0.0.1", 0), store, (capture.identity,), capture.trust)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def scores(directory: MirrorDirectory) -> dict[str, tuple[int, int]]:
    return {
        verify_card(encode_record(seen.card)).addresses[0]: (
            seen.successes,
            seen.failures,
        )
        for seen in directory.read().mirrors.values()
    }


def forbid_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("a default receive contacted the DHT or a Nostr relay")

    monkeypatch.setattr(mirror_rendezvous, "Rendezvous", forbidden)
    monkeypatch.setattr(mirror_rendezvous, "rendezvous_available", forbidden)
    monkeypatch.setattr(nostr, "find_cards", forbidden)
    monkeypatch.setattr(nostr, "Relay", forbidden)


def test_fresh_client_receives_from_a_mirror_learned_only_by_crawling(
    tmp_path: Path, capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hint holds nothing but vouches for B; a fresh client still receives."""
    forbid_lookups(monkeypatch)
    trust = tmp_path / "trust.json"
    trust.write_bytes(encode_record(capture.trust))
    document = tmp_path / "a-peers.json"
    directory = tmp_path / "client-directory"
    with (
        holding(capture.store, capture) as b,
        peers_only(document) as (a, requests),
    ):
        card = issue_card(Ed25519PrivateKey.generate(), (b,), {capture.identity})
        document.write_bytes(build_peers(None, (card,)))
        common = (
            "fetch",
            capture.identity,
            "--trust",
            trust,
            "--mirror",
            a,
            "--allow-http",
            "--allow-private",
            "--mirror-directory",
            directory,
        )
        with pytest.raises(PreservationError):
            run(
                arguments(
                    *common, "--store", tmp_path / "plain", "--no-discovered-mirrors"
                )
            )
        # Opting out reads no peers document and keeps no directory.
        assert PEERS not in requests and not directory.exists()
        receipt = run(arguments(*common, "--store", tmp_path / "received"))
    assert (
        receipt["verified_bytes"]
        == verify_store(capture.store, capture.identity, capture.trust).total_bytes
    )
    assert verify_store(tmp_path / "received", capture.identity, capture.trust)
    assert requests.count(PEERS) == 1
    # Only the hint already in use was crawled, and the mirror it vouched for
    # is scored by what it actually delivered.
    assert scores(MirrorDirectory(directory)) == {b: (1, 0)}


def test_default_receives_never_contact_the_dht_or_relays(
    tmp_path: Path, capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    forbid_lookups(monkeypatch)
    trust = tmp_path / "trust.json"
    trust.write_bytes(encode_record(capture.trust))
    handoff = tmp_path / "copy.foxhandoff"
    with holding(capture.store, capture) as endpoint:
        run(
            arguments(
                "fetch",
                capture.identity,
                "--trust",
                trust,
                "--mirror",
                endpoint,
                "--store",
                tmp_path / "direct",
                "--allow-http",
                "--allow-private",
            )
        )
        write_handoff(handoff, Handoff(reference=invitation(capture, endpoint)))
        received = run(
            arguments(
                "handoff-fetch",
                "--handoff",
                handoff,
                "--store",
                tmp_path / "handoff",
                "--bundle-trust",
                trust,
                "--allow-http",
                "--allow-private",
            )
        )
    assert received["serving"] is False
    for store in ("direct", "handoff"):
        assert verify_store(tmp_path / store, capture.identity, capture.trust)


def test_look_further_is_the_only_way_to_reach_the_dht_and_relays(
    tmp_path: Path, capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    class Node:
        def lookup(self, **_kwargs: object) -> tuple[str, ...]:
            calls.append("dht")
            return ("http://127.0.0.1:9",)

        def close(self) -> None:
            calls.append("closed")

    def find_cards(
        relays: tuple[str, ...], *_args: object, **_kwargs: object
    ) -> list[bytes]:
        calls.append("relays")
        return []

    monkeypatch.setattr(mirror_rendezvous, "rendezvous_available", lambda: True)
    monkeypatch.setattr(mirror_rendezvous, "Rendezvous", Node)
    monkeypatch.setattr(nostr, "find_cards", find_cards)
    trust = tmp_path / "trust.json"
    trust.write_bytes(encode_record(capture.trust))
    with holding(capture.store, capture) as endpoint:
        base = (
            "fetch",
            capture.identity,
            "--trust",
            trust,
            "--mirror",
            endpoint,
            "--allow-http",
            "--allow-private",
        )
        with pytest.raises(PreservationError, match="look-further"):
            run(
                arguments(
                    *base,
                    "--store",
                    tmp_path / "refused",
                    "--look-further",
                    "--no-discovered-mirrors",
                )
            )
        assert calls == []
        run(arguments(*base, "--store", tmp_path / "received", "--look-further"))
    # The DHT session is closed at once; nothing was announced.
    assert calls == ["dht", "closed", "relays"]


def test_look_further_help_discloses_what_it_reveals(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for command in ("fetch", "fetch-invitation", "source-fetch", "handoff-fetch"):
        with pytest.raises(SystemExit):
            argument_parser().parse_args([command, "--help"])
        text = " ".join(capsys.readouterr().out.split())
        assert "PRIVACY" in text and "IP address" in text, command
        assert "--no-discovered-mirrors" in text, command


def test_receiver_policy_holds_for_discovered_candidates(tmp_path: Path) -> None:
    identity = "a" * 64
    directory = MirrorDirectory(tmp_path / "d", rng=random.Random(3))  # noqa: S311 - test sampling
    plain = "http://mirror.example"
    private = "https://10.0.0.8"
    loopback = "http://127.0.0.1:8080"
    good = "https://good.example"
    directory.ingest(
        issue_card(Ed25519PrivateKey.generate(), (address,), {identity})
        for address in (plain, private, loopback, good)
    )
    hints = ("https://hint.example",)
    # HTTP is refused by default, as are private and loopback routes.
    found = plan_sources(identity, hints, directory, DEFAULT_POLICY, learn=False)
    assert found.sources == (*hints, good) and found.discovered == (good,)
    http_only = TransferPolicy(allow_http=True)
    assert set(
        plan_sources(identity, hints, directory, http_only, learn=False).discovered
    ) == {plain, good}
    everything = TransferPolicy(allow_http=True, allow_private=True)
    assert set(
        plan_sources(identity, hints, directory, everything, learn=False).discovered
    ) == {plain, private, loopback, good}
    # Hints plus discovered sources never exceed the receiver's bound.
    bounded = TransferPolicy(allow_http=True, allow_private=True, max_sources=3)
    many = ("https://h1.example", "https://h2.example")
    assert (
        len(plan_sources(identity, many, directory, bounded, learn=False).sources) == 3
    )
    full = tuple(f"https://h{index}.example" for index in range(3))
    assert plan_sources(identity, full, directory, bounded, learn=False).sources == full
    # A mirror whose card does not claim the manifest is never offered.
    assert (
        plan_sources("b" * 64, hints, directory, everything, learn=False).discovered
        == ()
    )
    for address, allowed in (
        ("https://mirror.example", True),
        ("http://mirror.example", False),
        ("https://localhost", False),
        ("https://box.local", False),
        ("https://[::1]", False),
        ("https://192.168.1.4", False),
        ("https://abc.onion", False),
        ("https://user:pw@mirror.example", False),
    ):
        assert permitted(address, DEFAULT_POLICY) is allowed, address


def test_http_seeds_are_refused_by_default(tmp_path: Path) -> None:
    directory = MirrorDirectory(tmp_path / "d")
    seeds = ("http://seed.example", "https://seed.example")
    found = plan_sources(
        "a" * 64, ("https://hint.example",), directory, learn=False, seeds=seeds
    )
    assert found.discovered == ("https://seed.example",)
    assert found.seeds == ("https://seed.example",)


def test_outcomes_are_recorded_for_success_failure_and_seeds(
    tmp_path: Path, capture: Capture
) -> None:
    corrupt = tmp_path / "corrupt"
    shutil.copytree(capture.store, corrupt)
    weights = verify_store(capture.store, capture.identity, capture.trust).members[0]
    directory = MirrorDirectory(tmp_path / "d", rng=random.Random(1))  # noqa: S311 - test sampling
    dead = "http://127.0.0.1:9"
    with holding(capture.store, capture) as good, holding(corrupt, capture) as bad:
        target = corrupt / object_path(weights.files[0].sha256)
        target.chmod(0o600)
        target.write_bytes(b"x" * weights.files[0].size)
        directory.ingest(
            issue_card(Ed25519PrivateKey.generate(), (address,), {capture.identity})
            for address in (dead, bad)
        )
        # The corrupt mirror is a hint, so it is tried first for every object.
        fetch_with_mirrors(
            capture.identity,
            capture.trust,
            (bad,),
            tmp_path / "received",
            LAB,
            mirrors=directory,
            seeds=(good,),
        )
        recorded = scores(directory)
        assert recorded[bad][0] == 0 and recorded[bad][1] == 1
        assert directory.read_seeds().seeds[good].successes == 1
        # A scored seed is no longer offered as new.
        assert directory.unscored_seeds((good,)) == ()
        # Failures are recorded even when the whole receive fails.
        with pytest.raises(PreservationError):
            fetch_with_mirrors(
                capture.identity,
                capture.trust,
                (dead,),
                tmp_path / "never",
                TransferPolicy(allow_http=True, allow_private=True, timeout=2),
                mirrors=directory,
            )
    assert scores(directory)[bad][1] == 2


def test_a_resumed_operation_keeps_its_discovered_sources(tmp_path: Path) -> None:
    identity = "c" * 64
    directory = MirrorDirectory(tmp_path / "d")
    first = issue_card(
        Ed25519PrivateKey.generate(), ("https://one.example",), {identity}
    )
    directory.ingest((first,))
    hints = ("https://hint.example",)
    chosen = plan_sources(
        identity, hints, directory, learn=False, operation_id="resume"
    )
    directory.ingest(
        (
            issue_card(
                Ed25519PrivateKey.generate(), ("https://two.example",), {identity}
            ),
        )
    )
    again = plan_sources(identity, hints, directory, learn=False, operation_id="resume")
    assert again == chosen and again.discovered == ("https://one.example",)
    fresh = plan_sources(identity, hints, directory, learn=False)
    assert set(fresh.discovered) == {"https://one.example", "https://two.example"}


def test_seed_lists_are_untrusted_bounded_and_user_extendable(tmp_path: Path) -> None:
    assert load_seeds() == ()  # The shipped list is a placeholder for now.
    own = tmp_path / "seeds.json"
    own.write_text(
        '{"schema_version":"sovereign.seed-mirrors.v1",'
        '"mirrors":["https://seed.example/","https://seed.example"]}'
    )
    assert load_seeds(own) == ("https://seed.example",)
    with pytest.raises(ValueError):
        read_seed_list(
            b'{"schema_version":"sovereign.seed-mirrors.v1","mirrors":["ftp://x"]}'
        )


def test_private_directory_commands_round_trip(
    tmp_path: Path, capture: Capture
) -> None:
    identity = capture.identity
    document = tmp_path / "peers.json"
    cards = tuple(
        issue_card(
            Ed25519PrivateKey.generate(), (f"https://m{index}.example",), {identity}
        )
        for index in range(3)
    )
    document.write_bytes(build_peers(None, cards))
    home = tmp_path / "home"
    imported = run(arguments("mirrors", "import", document, "--mirror-directory", home))
    assert imported == {"verified_cards": 3, "added": 3, "network_requests": 0}
    listed = run(arguments("mirrors", "list", "--mirror-directory", home))
    assert len(listed["mirrors"]) == 3 and listed["network_requests"] == 0  # pyright: ignore[reportArgumentType]
    exported = tmp_path / "export.json"
    receipt = run(
        arguments("mirrors", "export", "--output", exported, "--mirror-directory", home)
    )
    assert receipt["exported_cards"] == 3 and receipt["scores_included"] is False
    other = tmp_path / "other"
    run(arguments("mirrors", "import", exported, "--mirror-directory", other))
    assert len(MirrorDirectory(other).read().mirrors) == 3
    vouched = tmp_path / "vouched.json"
    learned = issue_card(
        Ed25519PrivateKey.generate(), ("https://far.example",), {identity}
    )
    vouched.write_bytes(build_peers(None, (learned,)))
    with peers_only(vouched) as (address, requests):
        crawled = run(
            arguments(
                "mirrors",
                "crawl",
                "--seed",
                address,
                "--allow-http",
                "--allow-private",
                "--no-expand",
                "--mirror-directory",
                other,
            )
        )
    assert crawled["added"] == 1 and crawled["looked_further"] is False
    assert requests == [PEERS]
