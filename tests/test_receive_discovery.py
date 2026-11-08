"""Receives use discovered mirrors after their hints, under the receiver's policy."""

from __future__ import annotations

import http.server
import random
import shutil
import threading
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import replace
from importlib.resources import files
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
    plan_fallback,
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
    hints = ("https://hint.example",)
    primary = plan_sources("a" * 64, hints, directory, learn=False, seeds=seeds)
    # Seeds are never part of the primary stage.
    assert primary.discovered == () and primary.seeds == ()
    found = plan_fallback("a" * 64, hints, primary, directory, learn=False, seeds=seeds)
    assert found.sources == (*hints, "https://seed.example")
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
    # The shipped list names Foxlight's starter bucket mirror, and only that.
    # (Tests otherwise replace it so no test contacts the real mirror.)
    shipped = (
        files("foxlight_model_preservation")
        .joinpath("resources/seed-mirrors.json")
        .read_bytes()
    )
    assert read_seed_list(shipped) == ("https://starter.foxlight.ai",)
    assert load_seeds() == ()  # Switched off for tests by conftest.
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


def test_manifest_failures_score_discovered_sources_but_not_hints(
    tmp_path: Path, capture: Capture
) -> None:
    """When no source can supply the manifest, the discovered ones still count."""
    directory = MirrorDirectory(tmp_path / "d")
    dead = "http://127.0.0.1:9"
    seed = "http://127.0.0.1:1"
    with peers_only(tmp_path / "absent.json") as (hint, _requests):
        directory.ingest(
            issue_card(Ed25519PrivateKey.generate(), (address,), {capture.identity})
            for address in (hint, dead)
        )
        with pytest.raises(PreservationError, match="manifest"):
            fetch_with_mirrors(
                capture.identity,
                capture.trust,
                (hint,),
                tmp_path / "never",
                TransferPolicy(allow_http=True, allow_private=True, timeout=2),
                mirrors=directory,
                seeds=(seed,),
            )
    recorded = scores(directory)
    assert recorded[dead] == (0, 1)
    assert recorded[hint] == (0, 0)  # Hints keep object-only scoring.
    assert directory.read_seeds().seeds[seed].failures == 1
    assert directory.unscored_seeds((seed,)) == ()


def test_hints_in_the_directory_cannot_crowd_out_a_fallback(tmp_path: Path) -> None:
    identity = "d" * 64
    hint = "https://hint.example"
    backup = "https://backup.example"
    directory = MirrorDirectory(tmp_path / "d")
    directory.ingest(
        issue_card(Ed25519PrivateKey.generate(), (address,), {identity})
        for address in (hint, backup)
    )
    for _ in range(4):
        directory.record(hint, delivered=True)  # The hint ranks first.
    assert directory.candidates(identity, limit=1) == (hint,)
    found = plan_sources(
        identity, (hint,), directory, TransferPolicy(max_sources=2), learn=False
    )
    assert found.sources == (hint, backup)


def test_a_resumable_receive_keeps_its_sources_beside_its_receipt(
    tmp_path: Path, capture: Capture
) -> None:
    """Hundreds of later receives cannot make an operation re-sample its sources."""
    from foxlight_model_preservation.mirror_directory import MAX_OPERATIONS

    directory = MirrorDirectory(tmp_path / "d")
    store = tmp_path / "received"
    with holding(capture.store, capture) as hint:
        directory.ingest(
            (
                issue_card(
                    Ed25519PrivateKey.generate(),
                    ("https://first.example",),
                    {capture.identity},
                ),
            )
        )
        fetch_with_mirrors(
            capture.identity,
            capture.trust,
            (hint,),
            store,
            LAB,
            mirrors=directory,
            operation_id="resumable",
        )
        # Many other receives fill the directory's bounded history, and a new
        # mirror appears that a fresh sample would include.
        for index in range(MAX_OPERATIONS + 10):
            plan_sources(
                capture.identity,
                (hint,),
                directory,
                LAB,
                learn=False,
                operation_id=f"other-{index}",
            )
        directory.ingest(
            (
                issue_card(
                    Ed25519PrivateKey.generate(),
                    ("https://second.example",),
                    {capture.identity},
                ),
            )
        )
        # The same operation resumes under its original, bound request.
        again = fetch_with_mirrors(
            capture.identity,
            capture.trust,
            (hint,),
            store,
            LAB,
            mirrors=directory,
            operation_id="resumable",
        )
    assert again == verify_store(store, capture.identity, capture.trust)
    assert (store / ".operations/resumable.sources.json").is_file()


def _card_for(identity: str, address: str) -> bytes:
    return issue_card(Ed25519PrivateKey.generate(), (address,), {identity})


def test_other_inputs_under_a_planned_operation_are_refused_not_overwritten(
    tmp_path: Path, capture: Capture
) -> None:
    from foxlight_model_preservation.operations import RequestChanged

    directory = MirrorDirectory(tmp_path / "d")
    directory.ingest((_card_for(capture.identity, "https://first.example"),))
    store = tmp_path / "received"
    plan_file = store / ".operations/resumable.sources.json"
    with (
        holding(capture.store, capture) as hint,
        holding(capture.store, capture) as other,
    ):
        fetch_with_mirrors(
            capture.identity,
            capture.trust,
            (hint,),
            store,
            LAB,
            mirrors=directory,
            operation_id="resumable",
        )
        original = plan_file.read_bytes()
        for hints, identity in (((other,), capture.identity), ((hint,), "e" * 64)):
            with pytest.raises(RequestChanged):
                fetch_with_mirrors(
                    identity,
                    capture.trust,
                    hints,
                    store,
                    LAB,
                    mirrors=directory,
                    operation_id="resumable",
                )
            assert plan_file.read_bytes() == original
        # The original inputs still resume.
        directory.ingest((_card_for(capture.identity, "https://second.example"),))
        fetch_with_mirrors(
            capture.identity,
            capture.trust,
            (hint,),
            store,
            LAB,
            mirrors=directory,
            operation_id="resumable",
        )
    assert plan_file.read_bytes() == original


def test_a_plan_written_before_a_crash_binds_the_retry(tmp_path: Path) -> None:
    """A crash after the plan but before the receipt keeps the same request."""
    from foxlight_model_preservation.operations import RequestChanged

    identity = "f" * 64
    directory = MirrorDirectory(tmp_path / "d")
    directory.ingest((_card_for(identity, "https://first.example"),))
    store = tmp_path / "store"
    hints = ("https://hint.example",)
    first = plan_sources(
        identity, hints, directory, learn=False, operation_id="op", destination=store
    )
    assert not (store / ".operations/op.json").exists()  # No receipt yet.
    directory.ingest((_card_for(identity, "https://second.example"),))
    again = plan_sources(
        identity, hints, directory, learn=False, operation_id="op", destination=store
    )
    assert again == first and again.discovered == ("https://first.example",)
    with pytest.raises(RequestChanged):
        plan_sources(
            identity,
            ("https://elsewhere.example",),
            directory,
            learn=False,
            operation_id="op",
            destination=store,
        )
    # No temporary files are left behind.
    assert sorted(path.name for path in (store / ".operations").iterdir()) == [
        "op.sources.json"
    ]


def test_concurrent_first_attempts_share_one_plan(tmp_path: Path) -> None:
    """Creation never replaces: the first plan wins and later ones adopt it."""
    from foxlight_model_preservation import mirror_directory as module
    from foxlight_model_preservation.operations import RequestChanged

    store = tmp_path / "store"
    key = "1" * 64
    won = module._claim_plan(store, "op", key, ("https://a.example",))  # pyright: ignore[reportPrivateUsage]
    lost = module._claim_plan(store, "op", key, ("https://b.example",))  # pyright: ignore[reportPrivateUsage]
    assert won == lost == ("https://a.example",)
    with pytest.raises(RequestChanged):
        module._claim_plan(store, "op", "2" * 64, ())  # pyright: ignore[reportPrivateUsage]
    assert module._claim_plan(store, "op", key, ()) == ("https://a.example",)  # pyright: ignore[reportPrivateUsage]


def test_a_damaged_directory_still_receives_and_binds_a_hints_only_plan(
    tmp_path: Path, capture: Capture
) -> None:
    home = tmp_path / "d"
    home.mkdir()
    (home / "directory.json").write_text("not json")
    store = tmp_path / "received"
    with holding(capture.store, capture) as hint:
        for _ in range(2):
            fetch_with_mirrors(
                capture.identity,
                capture.trust,
                (hint,),
                store,
                LAB,
                mirrors=MirrorDirectory(home),
                operation_id="damaged",
            )
    assert verify_store(store, capture.identity, capture.trust)
    assert b'"sources":[]' in (store / ".operations/damaged.sources.json").read_bytes()


def test_a_receive_that_succeeds_from_its_hints_never_contacts_a_seed(
    tmp_path: Path, capture: Capture
) -> None:
    """Starter mirrors see nobody whose named mirrors work."""
    directory = MirrorDirectory(tmp_path / "d")
    vouched = tmp_path / "seed-peers.json"
    with (
        holding(capture.store, capture) as hint,
        peers_only(vouched) as (seed, seed_requests),
    ):
        vouched.write_bytes(build_peers(None, ()))
        for operation_id in (None, "op"):
            fetch_with_mirrors(
                capture.identity,
                capture.trust,
                (hint,),
                tmp_path / f"received-{operation_id}",
                LAB,
                mirrors=directory,
                seeds=(seed,),
                operation_id=operation_id,
            )
    assert seed_requests == []
    assert directory.read_fallbacks().entries == ()
    assert directory.unscored_seeds((seed,)) == (seed,)  # Still never asked.
    assert not (tmp_path / "received-op/.operations/op.fallback.json").exists()


def test_a_receive_whose_hints_fail_falls_back_to_seeds_and_succeeds(
    tmp_path: Path, capture: Capture
) -> None:
    """Only after the hint fails is the seed asked, and what it vouches for works."""
    directory = MirrorDirectory(tmp_path / "d")
    vouched = tmp_path / "seed-peers.json"
    store = tmp_path / "received"
    with (
        holding(capture.store, capture) as holder,
        peers_only(tmp_path / "absent.json") as (hint, hint_requests),
        peers_only(vouched) as (seed, seed_requests),
    ):
        vouched.write_bytes(build_peers(None, (_card_for(capture.identity, holder),)))
        manifest = fetch_with_mirrors(
            capture.identity,
            capture.trust,
            (hint,),
            store,
            LAB,
            mirrors=directory,
            seeds=(seed,),
            operation_id="op",
        )
    assert manifest == verify_store(store, capture.identity, capture.trust)
    # The hint was learned from and asked first; the seed only afterwards.
    assert hint_requests[0] == PEERS and len(hint_requests) > 1
    assert seed_requests[0] == PEERS
    assert scores(directory)[holder] == (1, 0)
    # Contacting starter mirrors is visible: in the private directory record
    # and in the stage fixed beside the receipt.
    [entry] = directory.read_fallbacks().entries
    assert entry.manifest_id == capture.identity and entry.asked == (seed,)
    stage = (store / ".operations/op.fallback.json").read_bytes()
    assert holder.encode() in stage and seed.encode() in stage
    listed = run(arguments("mirrors", "list", "--mirror-directory", directory.path))
    assert listed["starter_fallbacks"] == [entry.model_dump(mode="json")]


def test_a_receive_with_no_room_or_no_seeds_does_not_fall_back(
    tmp_path: Path, capture: Capture
) -> None:
    directory = MirrorDirectory(tmp_path / "d")
    with (
        peers_only(tmp_path / "absent.json") as (hint, _hint_requests),
        peers_only(tmp_path / "seed.json") as (seed, seed_requests),
    ):
        for seeds, policy in (
            ((), LAB),
            (
                (seed,),
                TransferPolicy(allow_http=True, allow_private=True, max_sources=1),
            ),
        ):
            with pytest.raises(PreservationError, match="manifest"):
                fetch_with_mirrors(
                    capture.identity,
                    capture.trust,
                    (hint,),
                    tmp_path / "never",
                    policy,
                    mirrors=directory,
                    seeds=seeds,
                )
    assert seed_requests == []
    assert directory.read_fallbacks().entries == ()


def test_a_resume_after_an_interrupted_fallback_reuses_the_same_stage(
    tmp_path: Path, capture: Capture
) -> None:
    """The fallback stage is fixed when first used, so a resume binds it."""
    from foxlight_model_preservation.operations import (
        OperationCancelled,
        OperationControl,
        Progress,
    )

    corrupt = tmp_path / "corrupt"
    shutil.copytree(capture.store, corrupt)
    weights = verify_store(capture.store, capture.identity, capture.trust).members[0]
    directory = MirrorDirectory(tmp_path / "d")
    store = tmp_path / "received"
    stage_file = store / ".operations/op.fallback.json"
    vouched = tmp_path / "seed-peers.json"
    with (
        holding(corrupt, capture) as hint,
        holding(capture.store, capture) as holder,
        peers_only(vouched) as (seed, seed_requests),
        peers_only(tmp_path / "late.json") as (late, late_requests),
    ):
        target = corrupt / object_path(weights.files[0].sha256)
        target.chmod(0o600)
        target.write_bytes(b"x" * weights.files[0].size)
        vouched.write_bytes(build_peers(None, (_card_for(capture.identity, holder),)))

        def interrupt(progress: Progress) -> None:
            # Stop as soon as the fallback stage starts moving objects.
            if progress.stage == "downloading" and stage_file.exists():
                control.cancel()

        control = OperationControl(interrupt)
        # The hint serves the signed manifest but a corrupt object, so the
        # first stage creates the receipt before it fails.
        with pytest.raises(OperationCancelled):
            fetch_with_mirrors(
                capture.identity,
                capture.trust,
                (hint,),
                store,
                LAB,
                mirrors=directory,
                seeds=(seed,),
                control=control,
                operation_id="op",
            )
        assert (store / ".operations/op.json").is_file()
        fixed = stage_file.read_bytes()
        assert seed_requests == [PEERS]
        # Meanwhile the client learns of other mirrors and another seed.
        directory.ingest((_card_for(capture.identity, "http://127.0.0.1:9"),))
        manifest = fetch_with_mirrors(
            capture.identity,
            capture.trust,
            (hint,),
            store,
            LAB,
            mirrors=directory,
            seeds=(late, seed),
            operation_id="op",
        )
    assert manifest == verify_store(store, capture.identity, capture.trust)
    # The same stage was resumed: nothing re-planned, no new seed asked.
    assert stage_file.read_bytes() == fixed
    assert PEERS not in seed_requests[1:] and late_requests == []
    assert len(directory.read_fallbacks().entries) == 1


TERMINAL = {"complete", "failed", "cancelled"}


def test_a_fallback_never_shows_a_terminal_failure_before_it_succeeds(
    tmp_path: Path, capture: Capture
) -> None:
    """Observers see the first stage's failure as nothing, and one final outcome."""
    from foxlight_model_preservation.operations import OperationControl, Progress

    corrupt = tmp_path / "corrupt"
    shutil.copytree(capture.store, corrupt)
    weights = verify_store(capture.store, capture.identity, capture.trust).members[0]
    vouched = tmp_path / "seed-peers.json"
    events: list[Progress] = []
    with (
        holding(corrupt, capture) as hint,
        holding(capture.store, capture) as holder,
        peers_only(vouched) as (seed, _seed_requests),
    ):
        target = corrupt / object_path(weights.files[0].sha256)
        target.chmod(0o600)
        target.write_bytes(b"x" * weights.files[0].size)
        vouched.write_bytes(build_peers(None, (_card_for(capture.identity, holder),)))
        # The first stage moves objects and then fails on the corrupt one.
        fetch_with_mirrors(
            capture.identity,
            capture.trust,
            (hint,),
            tmp_path / "received",
            LAB,
            mirrors=MirrorDirectory(tmp_path / "d"),
            seeds=(seed,),
            control=OperationControl(events.append, interval_seconds=0),
            operation_id="op",
        )
        assert directory_fell_back(tmp_path / "d")
        # When the fallback also fails, that one failure is still reported.
        failed: list[Progress] = []
        with pytest.raises(PreservationError):
            fetch_with_mirrors(
                capture.identity,
                capture.trust,
                (hint,),
                tmp_path / "never",
                LAB,
                mirrors=MirrorDirectory(tmp_path / "other"),
                seeds=("http://127.0.0.1:9",),
                control=OperationControl(failed.append, interval_seconds=0),
            )
    stages = [event.stage for event in events]
    assert [stage for stage in stages if stage in TERMINAL] == ["complete"]
    assert stages[-1] == "complete" and stages.count("starting") == 2
    received = [event.bytes_received for event in events]
    assert received == sorted(received) and received[-1] > 0
    assert [event.stage for event in failed if event.stage in TERMINAL] == ["failed"]
    assert failed[-1].stage == "failed"


def directory_fell_back(path: Path) -> bool:
    return bool(MirrorDirectory(path).read_fallbacks().entries)


def test_cli_progress_shows_only_the_final_outcome_of_a_fallback(
    tmp_path: Path, capture: Capture
) -> None:
    import json
    import subprocess
    import sys

    trust = tmp_path / "trust.json"
    trust.write_bytes(encode_record(capture.trust))
    vouched = tmp_path / "seed-peers.json"
    seeds = tmp_path / "seeds.json"
    with (
        holding(capture.store, capture) as holder,
        peers_only(tmp_path / "absent.json") as (hint, _hint_requests),
        peers_only(vouched) as (seed, seed_requests),
    ):
        vouched.write_bytes(build_peers(None, (_card_for(capture.identity, holder),)))
        seeds.write_text(
            json.dumps(
                {"schema_version": "sovereign.seed-mirrors.v1", "mirrors": [seed]}
            )
        )
        result = subprocess.run(  # noqa: S603 - fixed CLI, no shell
            [
                sys.executable,
                "-m",
                "foxlight_model_preservation.cli",
                "fetch",
                capture.identity,
                "--trust",
                str(trust),
                "--mirror",
                hint,
                "--store",
                str(tmp_path / "received"),
                "--allow-http",
                "--allow-private",
                "--mirror-directory",
                str(tmp_path / "d"),
                "--seed-mirrors",
                str(seeds),
                "--progress",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    assert result.returncode == 0, result.stderr
    assert seed_requests[0] == PEERS
    stages = [json.loads(line)["stage"] for line in result.stderr.splitlines()]
    assert [stage for stage in stages if stage in TERMINAL] == ["complete"]
    assert stages[-1] == "complete"


def test_an_unnamed_receive_shares_one_allowance_across_both_stages(
    tmp_path: Path, capture: Capture
) -> None:
    """Bytes the primary stage used are not available again to the fallback."""
    from foxlight_model_preservation.operations import (
        BudgetExceeded,
        OperationControl,
    )
    from foxlight_model_preservation.transfer import SourcesExhausted, fetch

    corrupt = tmp_path / "corrupt"
    shutil.copytree(capture.store, corrupt)
    verified = verify_store(capture.store, capture.identity, capture.trust)
    weights = verified.members[0]
    vouched = tmp_path / "seed-peers.json"
    with (
        holding(corrupt, capture) as hint,
        holding(capture.store, capture) as holder,
        peers_only(vouched) as (seed, seed_requests),
    ):
        target = corrupt / object_path(weights.files[0].sha256)
        target.chmod(0o600)
        target.write_bytes(b"x" * weights.files[0].size)
        vouched.write_bytes(build_peers(None, (_card_for(capture.identity, holder),)))
        # Measure what the primary stage alone moves before it fails.
        probe = OperationControl()
        with pytest.raises(SourcesExhausted):
            fetch(
                capture.identity,
                capture.trust,
                (hint,),
                tmp_path / "probe",
                LAB,
                control=probe,
            )
        used = probe.progress.bytes_received
        needed = verified.total_bytes - sum(
            path.stat().st_size
            for path in (tmp_path / "probe/objects").rglob("*")
            if path.is_file()
        )
        assert used > 0 and needed > 0
        # Enough for the first stage, and for the whole model, but not for
        # the first stage's spent bytes plus what the fallback still needs.
        limit = max(used, verified.total_bytes, used + needed - 1)
        assert used + needed > limit
        bounded = TransferPolicy(
            allow_http=True, allow_private=True, timeout=3, max_bytes=limit
        )
        with pytest.raises(BudgetExceeded):
            fetch_with_mirrors(
                capture.identity,
                capture.trust,
                (hint,),
                tmp_path / "bounded",
                bounded,
                mirrors=MirrorDirectory(tmp_path / "d"),
                seeds=(seed,),
            )
        assert seed_requests == [PEERS]  # The fallback did start.
        # With room for both stages (the fallback tries the hint again first)
        # the same receive completes.
        roomy = replace(bounded, max_bytes=2 * used + needed)
        fetch_with_mirrors(
            capture.identity,
            capture.trust,
            (hint,),
            tmp_path / "roomy",
            roomy,
            mirrors=MirrorDirectory(tmp_path / "d2"),
            seeds=(seed,),
        )
    assert verify_store(tmp_path / "roomy", capture.identity, capture.trust)


def test_a_full_disk_during_the_primary_stage_never_reaches_the_seeds(
    tmp_path: Path, capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A receiver's own storage failure surfaces at once; no supplier is blamed."""
    import errno

    from foxlight_model_preservation import transfer
    from foxlight_model_preservation.operations import StorageFailed, failure_code

    real = transfer.open_file

    class Full:
        def __init__(self, stream: object) -> None:
            self._stream = stream

        def write(self, _data: bytes) -> int:
            raise OSError(errno.ENOSPC, "No space left on device")

        def __getattr__(self, name: str) -> object:
            return getattr(self._stream, name)

    @contextmanager
    def full_disk(root: int, path: str, *, write: bool = False) -> Generator[object]:
        with real(root, path, write=write) as stream:
            yield Full(stream) if write and path.startswith(".partial/") else stream

    monkeypatch.setattr(transfer, "open_file", full_disk)
    directory = MirrorDirectory(tmp_path / "d")
    with (
        holding(capture.store, capture) as hint,
        peers_only(tmp_path / "seed.json") as (seed, seed_requests),
    ):
        with pytest.raises(StorageFailed) as raised:
            fetch_with_mirrors(
                capture.identity,
                capture.trust,
                (hint,),
                tmp_path / "received",
                LAB,
                mirrors=directory,
                seeds=(seed,),
            )
    assert failure_code(raised.value) == "storage_error"
    assert isinstance(raised.value.__cause__, OSError)
    assert raised.value.__cause__.errno == errno.ENOSPC
    assert seed_requests == []
    assert directory.read_fallbacks().entries == ()


@contextmanager
def slow(delays: dict[str, float]) -> Generator[tuple[str, list[str]]]:
    """A host that waits before answering 404; delays are keyed by path prefix."""
    requests: list[str] = []
    released = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            requests.append(self.path)
            for prefix, delay in delays.items():
                if self.path.startswith(prefix):
                    released.wait(delay)
                    break
            self.send_error(404)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", requests
    finally:
        released.set()
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_the_fallback_gets_only_what_is_left_of_the_receive_deadline(
    tmp_path: Path, capture: Capture
) -> None:
    """A primary failing near the deadline leaves the stalled seed the remainder."""
    import time

    from foxlight_model_preservation.operations import DeadlineExceeded

    policy = TransferPolicy(
        allow_http=True, allow_private=True, timeout=30, deadline_seconds=5
    )
    with (
        slow({"/manifests/": 4.0}) as (hint, _hint_requests),
        slow({"/": 30.0}) as (seed, seed_requests),
    ):
        started = time.monotonic()
        with pytest.raises(DeadlineExceeded):
            fetch_with_mirrors(
                capture.identity,
                capture.trust,
                (hint,),
                tmp_path / "never",
                policy,
                mirrors=MirrorDirectory(tmp_path / "d"),
                seeds=(seed,),
            )
        elapsed = time.monotonic() - started
    # The seed was asked after the primary failed, and the whole receive ended
    # at its own deadline rather than starting a fresh one for the fallback.
    assert seed_requests == [PEERS]
    assert 3.5 < elapsed < 6.5, elapsed


def test_no_time_left_after_the_primary_stage_contacts_no_seed(
    tmp_path: Path, capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    from foxlight_model_preservation import mirror_directory
    from foxlight_model_preservation.operations import DeadlineExceeded
    from foxlight_model_preservation.transfer import SourcesExhausted

    attempts: list[tuple[str, ...]] = []

    def exhausted_at_the_deadline(
        _manifest: str,
        _trust: object,
        sources: tuple[str, ...],
        *_a: object,
        **_k: object,
    ) -> None:
        attempts.append(sources)
        time.sleep(1.2)
        raise SourcesExhausted("no authenticated manifest available")

    monkeypatch.setattr(mirror_directory, "fetch", exhausted_at_the_deadline)
    with peers_only(tmp_path / "seed.json") as (seed, seed_requests):
        with pytest.raises(DeadlineExceeded):
            fetch_with_mirrors(
                capture.identity,
                capture.trust,
                ("http://127.0.0.1:9",),
                tmp_path / "never",
                TransferPolicy(allow_http=True, allow_private=True, deadline_seconds=1),
                mirrors=MirrorDirectory(tmp_path / "d"),
                seeds=(seed,),
            )
    assert attempts == [("http://127.0.0.1:9",)]
    assert seed_requests == []
    assert MirrorDirectory(tmp_path / "d").read_fallbacks().entries == ()


def test_a_named_fallback_keeps_the_primary_stages_unused_reservation(
    tmp_path: Path, capture: Capture
) -> None:
    """A whole-allowance reservation from the first stage still serves the next."""
    from foxlight_model_preservation.ledger import read_transfer_receipt

    corrupt = tmp_path / "corrupt"
    shutil.copytree(capture.store, corrupt)
    verified = verify_store(capture.store, capture.identity, capture.trust)
    weights = verified.members[0]
    vouched = tmp_path / "seed-peers.json"
    store = tmp_path / "received"
    policy = TransferPolicy(
        allow_http=True, allow_private=True, timeout=3, max_bytes=100_000
    )
    with (
        holding(corrupt, capture) as hint,
        holding(capture.store, capture) as holder,
        peers_only(vouched) as (seed, seed_requests),
    ):
        target = corrupt / object_path(weights.files[0].sha256)
        target.chmod(0o600)
        target.write_bytes(b"x" * weights.files[0].size)
        vouched.write_bytes(build_peers(None, (_card_for(capture.identity, holder),)))
        manifest = fetch_with_mirrors(
            capture.identity,
            capture.trust,
            (hint,),
            store,
            policy,
            mirrors=MirrorDirectory(tmp_path / "d"),
            seeds=(seed,),
            operation_id="op",
        )
    assert seed_requests[0] == PEERS  # The fallback stage ran.
    assert manifest == verify_store(store, capture.identity, capture.trust)
    # The first read reserved the whole allowance on disk; it was never
    # refunded, and the fallback stage used what was left of it.
    receipt = read_transfer_receipt(store, "op")
    assert verified.total_bytes < receipt.charged_bytes == policy.max_bytes


def test_carried_credit_is_forfeit_once_the_receipt_changes(tmp_path: Path) -> None:
    """Credit is adopted only against the exact receipt the earlier stage wrote."""
    from foxlight_model_preservation.ledger import CreditCarry, TransferBudget
    from foxlight_model_preservation.storage import CHUNK_SIZE
    from foxlight_model_preservation.storage import directory as pinned

    large = 8 * CHUNK_SIZE

    with pinned(tmp_path / "store", create=True) as root:

        def budget(carry: CreditCarry | None = None) -> TransferBudget:
            return TransferBudget(
                100_000,
                root=root,
                operation_id="op",
                request_digest="1" * 64,
                carry=carry,
            )

        carry = CreditCarry()
        first = budget(carry)
        first.reserve(10)
        first.consume(10)
        assert first.remaining == 100_000 - 10
        # The next stage keeps the unused reservation.
        assert budget(carry).remaining == 100_000 - 10
        # A restart (no carry) forfeits it: reservations are never refunded.
        assert budget().remaining == 0
        # So does any charge written in between by another budget.
        carry = CreditCarry()
        stale = TransferBudget(
            large,
            root=root,
            operation_id="other",
            request_digest="2" * 64,
            carry=carry,
        )
        stale.reserve(10)
        meanwhile = TransferBudget(
            large, root=root, operation_id="other", request_digest="2" * 64
        )
        meanwhile.reserve(1)  # Charges afresh: the receipt on disk moves on.
        assert (
            TransferBudget(
                large,
                root=root,
                operation_id="other",
                request_digest="2" * 64,
                carry=carry,
            ).remaining
            == large - 2 * CHUNK_SIZE
        )


def _terminal(events: list[object]) -> list[str]:
    from foxlight_model_preservation.operations import Progress

    return [
        event.stage
        for event in events
        if isinstance(event, Progress) and event.stage in TERMINAL
    ]


def test_a_final_primary_attempt_shows_its_outcome_on_the_callers_control(
    tmp_path: Path, capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a fallback possible but not used, control.progress is the real end."""
    import errno

    from foxlight_model_preservation import transfer
    from foxlight_model_preservation.operations import (
        OperationCancelled,
        OperationControl,
        Progress,
        RequestChanged,
        StorageFailed,
    )

    seed = "https://seed.example"  # Never reached in any case below.

    def receive(
        hints: tuple[str, ...], store: str, control: OperationControl, **extra: object
    ) -> None:
        fetch_with_mirrors(
            capture.identity,
            capture.trust,
            hints,
            tmp_path / store,
            LAB,
            mirrors=MirrorDirectory(tmp_path / "d"),
            seeds=extra.pop("seeds", (seed,)),  # type: ignore[arg-type]
            control=control,
            **extra,  # type: ignore[arg-type]
        )

    with holding(capture.store, capture) as hint:
        # Success.
        events: list[Progress] = []
        control = OperationControl(events.append, interval_seconds=0)
        receive((hint,), "ok", control)
        assert control.progress.stage == "complete" and control.progress.committed
        assert control.progress.bytes_received > 0
        assert _terminal(events) == ["complete"]

        # Cancellation during the primary stage.
        events = []

        def cancel(progress: Progress) -> None:
            events.append(progress)
            if progress.stage == "downloading":
                control.cancel()

        control = OperationControl(cancel, interval_seconds=0)
        with pytest.raises(OperationCancelled):
            receive((hint,), "cancelled", control)
        assert control.progress.stage == "cancelled"
        assert control.progress.error_code == "cancelled"
        assert not control.progress.committed
        assert _terminal(events) == ["cancelled"]

        # A failure that is not the sources' (no fallback follows).
        real = transfer.open_file

        @contextmanager
        def full_disk(
            root: int, path: str, *, write: bool = False
        ) -> Generator[object]:
            if write and path.startswith(".partial/"):
                raise OSError(errno.ENOSPC, "No space left on device")
            with real(root, path, write=write) as stream:
                yield stream

        monkeypatch.setattr(transfer, "open_file", full_disk)
        events = []
        control = OperationControl(events.append, interval_seconds=0)
        with pytest.raises(StorageFailed):
            receive((hint,), "full", control)
        monkeypatch.setattr(transfer, "open_file", real)
        assert control.progress.stage == "failed"
        assert control.progress.error_code == "storage_error"
        assert _terminal(events) == ["failed"]

        # Planning refuses changed inputs before any invocation.
        receive((hint,), "planned", OperationControl(), operation_id="op")
        events = []
        control = OperationControl(events.append, interval_seconds=0)
        with pytest.raises(RequestChanged):
            receive(("https://other.example",), "planned", control, operation_id="op")
        assert control.progress.stage == "failed"
        assert control.progress.error_code == "request_changed"
        assert _terminal(events) == ["failed"]

    # The sources ran out and the fallback has nothing to add.
    with peers_only(tmp_path / "absent.json") as (empty, _requests):
        events = []
        control = OperationControl(events.append, interval_seconds=0)
        with pytest.raises(PreservationError, match="manifest"):
            receive((empty,), "exhausted", control, seeds=(empty,))
        assert control.progress.stage == "failed"
        assert control.progress.operation == "fetch"
        assert _terminal(events) == ["failed"]


def test_the_shipped_starter_list_can_be_switched_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from foxlight_model_preservation import mirror_directory

    monkeypatch.setenv("SOVEREIGN_PACKAGED_SEED_MIRRORS", "on")
    assert mirror_directory.packaged_seeds() == ("https://starter.foxlight.ai",)
    monkeypatch.setenv("SOVEREIGN_PACKAGED_SEED_MIRRORS", "OFF")
    assert mirror_directory.packaged_seeds() == ()
