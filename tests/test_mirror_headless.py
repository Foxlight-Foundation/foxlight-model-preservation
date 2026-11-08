"""Headless mirror quickstart, configuration, deferral, withdrawal and announce."""

from __future__ import annotations

import http.client
import json
import os
import queue
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import Capture
from test_discovery import Publisher
from test_mirror import cycle, mirror

from foxlight_model_preservation import mirror as mirror_module
from foxlight_model_preservation.discovery import bootstrap_identity
from foxlight_model_preservation.mirror import (
    configure_mirror,
    derived_model_limit,
    follow,
    model_action,
    public_selection,
    retry_selection,
    run_mirror,
)
from foxlight_model_preservation.mirror_announce import (
    KEY_FILE,
    Announcer,
    dht_port,
)
from foxlight_model_preservation.mirror_cards import PEERS_PATH, read_peers, verify_card
from foxlight_model_preservation.mirror_models import (
    AnnouncePolicy,
    MirrorState,
    SourceGrant,
    Terms,
    WithdrawalAdvisory,
)
from foxlight_model_preservation.mirror_quickstart import (
    QuickstartSettings,
    apply_quickstart,
    format_size,
    load_collection,
    parse_duration,
    parse_size,
    review,
)
from foxlight_model_preservation.mirror_state import (
    HEADROOM,
    MirrorLedger,
    encode_state,
    grant_restart,
    policy_id,
    restart_allowed,
)
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationControl
from foxlight_model_preservation.signing import encode_record
from foxlight_model_preservation.storage import manifest_path, verify_store
from foxlight_model_preservation.transfer import TransferPolicy

ROOT = Path(__file__).resolve().parents[1]
TERMS = (Terms(license_id="test-only", terms_reference="LICENSE"),)


# A mirror verifies every retained byte before it serves; on a loaded CI runner
# that, plus the listener coming up, has taken longer than 10 s. Each wait is a
# readiness condition bounded by SERVE_WAIT, and a bounded run outlasts two.
SERVE_WAIT = 45.0
SERVE_WINDOW = 120


def cli(*arguments: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - isolated test CLI, no shell
        [sys.executable, "-m", "foxlight_model_preservation.cli", *arguments],
        capture_output=True,
        text=True,
        timeout=60,
        input=stdin,
    )


def public(path: Path, identity: str) -> Path:
    return path / "public" / manifest_path(identity)


# Sizes, durations and init defaults ------------------------------------------


def test_human_sizes_are_binary_and_bounded():
    assert parse_size("100G") == 100 * 1024**3
    assert parse_size("1T") == 1024**4
    assert parse_size("1tib") == parse_size("1TB") == 1024**4
    assert parse_size("512M") == 512 * 1024**2
    assert parse_size("4096") == 4096
    for bad in ("", "1.5G", "-1G", "1E", "10GG", "0", "9999999P"):
        with pytest.raises(PreservationError):
            parse_size(bad)
    assert format_size(100 * 1024**3) == "100 GiB"
    assert format_size(1536 * 1024**2) == "1.5 GiB"
    assert parse_duration("365d") == 365 * 86400
    assert parse_duration("12h") == 43200
    assert parse_duration("90") == 90
    with pytest.raises(PreservationError):
        parse_duration("1y")


def test_init_defaults_name_to_host_and_accepts_sizes_and_ipv6(tmp_path):
    import socket

    result = cli(
        "mirror",
        "init",
        "--dir",
        str(tmp_path / "m"),
        "--disk-bytes",
        "2G",
        "--ingress-daily",
        "1G",
        "--egress-daily",
        "1G",
        "--egress-monthly",
        "10G",
        "--bind",
        "::1",
    )
    assert result.returncode == 0, result.stderr
    state = MirrorLedger(tmp_path / "m").read()
    assert state.name == (socket.gethostname().strip()[:200] or "sovereign-mirror")
    assert state.disk_bytes == 2 * 1024**3 and state.egress_monthly == 10 * 1024**3
    assert state.bind == "::1" and state.announce is None
    assert not restart_allowed(state)
    bad = cli("mirror", "init", "--dir", str(tmp_path / "n"), "--disk-bytes", "lots")
    assert bad.returncode == 2 and not (tmp_path / "n").exists()


def test_follow_derives_per_model_limit_from_disk_budget(tmp_path, capture):
    publisher = Publisher(tmp_path / "publisher", capture)
    bootstrap = publisher.bootstrap()
    (tmp_path / "source.json").write_bytes(encode_record(bootstrap))
    (tmp_path / "trust.json").write_bytes(encode_record(capture.trust))
    (tmp_path / "terms.json").write_text(
        json.dumps([item.model_dump() for item in TERMS])
    )
    initialized = cli(
        "mirror", "init", "--dir", str(tmp_path / "m"), "--disk-bytes", "3T"
    )
    assert initialized.returncode == 0, initialized.stderr
    arguments = (
        "mirror",
        "follow",
        "--dir",
        str(tmp_path / "m"),
        "--source-file",
        str(tmp_path / "source.json"),
        "--expected-bootstrap-id",
        bootstrap_identity(bootstrap),
        "--bundle-trust",
        str(tmp_path / "trust.json"),
        "--terms-file",
        str(tmp_path / "terms.json"),
        "--confirm-redistribution",
    )
    assert cli(*arguments).returncode == 0
    (grant,) = MirrorLedger(tmp_path / "m").read().sources.values()
    assert grant.max_bytes == 3 * 1024**4 - HEADROOM
    assert cli(*arguments, "--max-bytes", "40G").returncode == 0
    (grant,) = MirrorLedger(tmp_path / "m").read().sources.values()
    assert grant.max_bytes == 40 * 1024**3


def test_grant_resume_for_duration_keeps_the_366_day_cap(tmp_path):
    MirrorLedger.initialize(
        tmp_path / "m",
        name="Grant",
        disk_bytes=1024**3,
        ingress_daily=1,
        egress_daily=1,
        egress_monthly=1,
    )
    base = ("mirror", "grant-resume", "--dir", str(tmp_path / "m"))
    assert cli(*base, "--for", "365d").returncode == 2  # confirmation required
    ok = cli(*base, "--for", "365d", "--confirm-resume")
    assert ok.returncode == 0, ok.stderr
    expires = json.loads(ok.stdout)["restart_expires"]
    assert abs(expires - (time.time() + 365 * 86400)) < 120
    assert restart_allowed(MirrorLedger(tmp_path / "m").read())
    refused = cli(*base, "--for", "367d", "--confirm-resume")
    assert refused.returncode == 1 and "366 days" in refused.stderr
    both = cli(*base, "--for", "1d", "--expires", "1", "--confirm-resume")
    assert both.returncode == 2


# Configuration -----------------------------------------------------------------


def test_config_changes_policy_and_clears_restart_grant(tmp_path, capture):
    ledger, _ = mirror(tmp_path, capture)
    grant_restart(ledger, int(time.time()) + 600)
    before = policy_id(ledger.read())
    # An unchanged configuration keeps the grant.
    configure_mirror(ledger, {"port": ledger.read().port})
    assert restart_allowed(ledger.read())
    result = cli(
        "mirror",
        "config",
        "--dir",
        str(ledger.path),
        "--port",
        "9090",
        "--bind",
        "::",
        "--egress-monthly",
        "2T",
        "--name",
        "Renamed",
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["restart_grant_invalidated"] and not receipt["network_started"]
    state = ledger.read()
    assert (state.port, state.bind, state.name) == (9090, "::", "Renamed")
    assert state.egress_monthly == 2 * 1024**4
    assert policy_id(state) != before and state.restart is None
    with pytest.raises(PreservationError, match="restart authority"):
        run_mirror(ledger, resume=True)
    with ledger.service_lock(), pytest.raises(PreservationError, match="stop"):
        configure_mirror(ledger, {"port": 1})
    with pytest.raises(PreservationError, match="unsupported"):
        configure_mirror(ledger, {"sources": {}})
    with pytest.raises(ValueError):
        configure_mirror(ledger, {"disk_bytes": 1})
    with pytest.raises(ValueError):
        configure_mirror(ledger, {"bind": "192.0.2.1"})


def test_disk_change_moves_derived_limits_but_keeps_explicit(tmp_path, capture):
    ledger, _ = mirror(tmp_path, capture)
    (source,) = ledger.read().sources
    derived = derived_model_limit(ledger.read().disk_bytes)
    ledger.update(
        lambda s: s.model_copy(
            update={
                "sources": {
                    source: s.sources[source].model_copy(update={"max_bytes": derived})
                }
            }
        )
    )
    configure_mirror(ledger, {"disk_bytes": 8 * 1024**3})
    assert ledger.read().sources[source].max_bytes == 8 * 1024**3 - HEADROOM
    ledger.update(
        lambda s: s.model_copy(
            update={
                "sources": {
                    source: s.sources[source].model_copy(update={"max_bytes": 12345})
                }
            }
        )
    )
    configure_mirror(ledger, {"disk_bytes": 4 * 1024**3})
    assert ledger.read().sources[source].max_bytes == 12345


def test_announce_options_need_url_and_public_disclosure_consent(tmp_path, capture):
    ledger, _ = mirror(tmp_path, capture)
    base = ("mirror", "config", "--dir", str(ledger.path))
    no_url = cli(*base, "--announce-nostr", "--confirm-public-announcement")
    assert no_url.returncode == 1 and "--public-url" in no_url.stderr
    no_consent = cli(*base, "--public-url", "https://m.example", "--announce-dht")
    assert no_consent.returncode == 1 and "not anonymous" in no_consent.stderr
    assert ledger.read().announce is None
    plain = cli(*base, "--public-url", "http://m.example/")
    assert plain.returncode == 0
    assert json.loads(plain.stdout)["warnings"]
    assert ledger.read().announce == AnnouncePolicy(public_url="http://m.example")
    on = cli(*base, "--announce-nostr", "--confirm-public-announcement")
    assert on.returncode == 0 and ledger.read().announce.nostr
    assert cli(*base, "--contact", "ops@m.example").returncode == 0
    announce = ledger.read().announce
    assert announce.contact == "ops@m.example" and announce.nostr
    (ledger.path / "peers.json").write_bytes(b"{}")
    assert cli(*base, "--clear-public-url").returncode == 0
    assert ledger.read().announce is None
    assert not (ledger.path / "peers.json").exists()
    with pytest.raises(ValueError):
        AnnouncePolicy(public_url="ftp://m.example")


def test_new_fields_keep_released_encoding_and_policy_identity(tmp_path, capture):
    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    raw = (ledger.path / "mirror.json").read_bytes()
    assert b"deferred" not in raw and b"withdraw" not in raw
    assert b"announce" not in raw
    state = ledger.read()
    held = state.models[capture.identity]
    receipts = state.model_copy(
        update={
            "models": {
                capture.identity: held.model_copy(
                    update={
                        "deferred": "later",
                        "withdrawal": WithdrawalAdvisory(observed_at=1),
                    }
                )
            }
        }
    )
    assert policy_id(receipts) == policy_id(state)
    assert b"withdrawal" in encode_state(receipts)
    announced = state.model_copy(
        update={"announce": AnnouncePolicy(public_url="https://m.example")}
    )
    assert policy_id(announced) != policy_id(state)
    MirrorState.model_validate_json(encode_state(announced))
    # Following withdrawals is reviewed policy; off keeps the released bytes.
    following = state.model_copy(update={"follow_withdrawals": True})
    assert policy_id(following) != policy_id(state)
    assert b"follow_withdrawals" in encode_state(following)
    MirrorState.model_validate_json(encode_state(following))


# Deferred budget refusals ---------------------------------------------------------


def test_ingress_refusal_defers_and_retries_automatically(
    tmp_path, capture, monkeypatch
):
    ledger, _ = mirror(tmp_path, capture)
    # Enough for the source refresh, not the model's metadata reservation.
    ledger.update(lambda s: s.model_copy(update={"ingress_daily": 64 * 1024**2}))
    cycle(ledger)
    held = ledger.read().models[capture.identity]
    assert held.state == "pending" and held.error is None
    assert held.deferred and "ingress" in held.deferred

    # While the budget still cannot admit it, the next cycle stays offline.
    def forbidden(*_args, **_kwargs):
        raise AssertionError("deferred intake contacted a supplier")

    monkeypatch.setattr(mirror_module, "inspect_envelope", forbidden)
    cycle(ledger)
    monkeypatch.undo()
    held = ledger.read().models[capture.identity]
    assert held.deferred and held.error is None
    used = ledger.read().ingress_used

    # A restarted process (fresh ledger) retries once the operator raises it.
    restarted = MirrorLedger(ledger.path)
    configure_mirror(restarted, {"ingress_daily": 10 * 1024**3})
    cycle(restarted)
    held = restarted.read().models[capture.identity]
    assert held.state == "retained" and held.deferred is None and held.error is None
    assert restarted.read().ingress_used > used
    verify_store(ledger.path / "store", capture.identity, capture.trust)


def test_disk_refusal_defers_keeps_reservation_and_survives_kill(
    tmp_path, capture, monkeypatch
):
    ledger, _ = mirror(tmp_path, capture)
    real_usage = MirrorLedger.usage
    # The intake check sees a full disk; the refresh check before it does not.
    calls = {"n": 0}

    def crowded(self: MirrorLedger) -> int:
        calls["n"] += 1
        return real_usage(self) + (self.read().disk_bytes if calls["n"] > 1 else 0)

    monkeypatch.setattr(MirrorLedger, "usage", crowded)
    cycle(ledger)
    monkeypatch.setattr(MirrorLedger, "usage", real_usage)
    held = ledger.read().models[capture.identity]
    assert held.deferred and "disk" in held.deferred and held.error is None
    assert held.operation_id in ledger.read().reserved_operations
    charged = ledger.read().ingress_used
    assert charged >= held.transfer_bytes

    # Kill between a retry's reservations and its outcome: nothing is recorded.
    def killed(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(mirror_module, "fetch", killed)
    with pytest.raises(KeyboardInterrupt):
        cycle(ledger)
    monkeypatch.undo()
    assert not ledger.running()
    restarted = MirrorLedger(ledger.path)
    assert restarted.read().models[capture.identity].deferred
    before = restarted.read().ingress_used
    cycle(restarted)
    held = restarted.read().models[capture.identity]
    assert held.state == "retained" and held.deferred is None
    # Only the source refresh is charged: the model's allowance and pinned
    # metadata reservations are never charged twice (nor refunded).
    assert restarted.read().ingress_used - before == 32 * 1024**2
    assert restarted.read().reserved_operations.count(held.operation_id) == 1


def test_real_failures_stay_sticky_until_explicit_retry(tmp_path, capture):
    ledger, _ = mirror(tmp_path, capture)
    (source,) = ledger.read().sources
    ledger.update(
        lambda s: s.model_copy(
            update={
                "sources": {
                    source: s.sources[source].model_copy(
                        update={"terms": (Terms(license_id="x", terms_reference="y"),)}
                    )
                }
            }
        )
    )
    cycle(ledger)
    cycle(ledger)
    held = ledger.read().models[capture.identity]
    assert held.error and held.deferred is None


# Retry adopting the current source grant -----------------------------------------


def _refollow(ledger: MirrorLedger, publisher: Publisher, grant: SourceGrant) -> None:
    bootstrap = publisher.bootstrap()
    follow(ledger, encode_record(bootstrap), bootstrap_identity(bootstrap), grant)


def test_retry_adopts_the_refollowed_source_grant(tmp_path, capture):
    ledger, publisher = mirror(tmp_path, capture)
    (source,) = ledger.read().sources
    good = ledger.read().sources[source]
    _refollow(
        ledger,
        publisher,
        good.model_copy(
            update={"terms": (Terms(license_id="x", terms_reference="y"),)}
        ),
    )
    cycle(ledger)
    assert ledger.read().models[capture.identity].error
    _refollow(ledger, publisher, good)
    model_action(ledger, capture.identity, "retry")
    cycle(ledger)
    # A plain retry keeps the selection's original (wrong) authority.
    assert ledger.read().models[capture.identity].error
    result = cli(
        "mirror",
        "retry",
        "--dir",
        str(ledger.path),
        "--manifest",
        capture.identity,
        "--adopt-source-grant",
    )
    assert result.returncode == 0, result.stderr
    held = ledger.read().models[capture.identity]
    assert held.grant == good and held.error is None
    cycle(ledger)
    assert ledger.read().models[capture.identity].state == "retained"


def test_adoption_rewrites_pinned_authority_and_resumes_after_kill(
    tmp_path, capture, monkeypatch
):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from foxlight_model_preservation.signing import public_record

    ledger, publisher = mirror(tmp_path, capture)
    cycle(ledger)
    held = ledger.read().models[capture.identity]
    assert held.envelope_sha256 is not None
    (source,) = ledger.read().sources
    extra = public_record(Ed25519PrivateKey.generate())
    wider = held.grant.model_copy(
        update={
            "trust": held.grant.trust.model_copy(
                update={"keys": (*held.grant.trust.keys, extra)}
            ),
            "terms": (*held.grant.terms, Terms(license_id="b", terms_reference="c")),
        }
    )
    _refollow(ledger, publisher, wider)
    real = mirror_module.atomic_bytes

    def crash(*_args, **_kwargs):
        raise OSError("injected crash before the authority receipt")

    monkeypatch.setattr(mirror_module, "atomic_bytes", crash)
    with pytest.raises(OSError, match="injected"):
        retry_selection(ledger, capture.identity, adopt_source_grant=True)
    monkeypatch.setattr(mirror_module, "atomic_bytes", real)
    # The committed grant no longer matches the old receipt: fail closed...
    with ledger.service_lock():
        public_selection(ledger, OperationControl())
    assert ledger.read().models[capture.identity].error
    # ...until the same command is repeated.
    retry_selection(ledger, capture.identity, adopt_source_grant=True)
    with ledger.service_lock():
        allowed, _ = public_selection(ledger, OperationControl())
    assert "/" + manifest_path(capture.identity) in allowed
    adopted = ledger.read().models[capture.identity]
    assert adopted.grant == wider and adopted.state == "retained"
    assert adopted.envelope_sha256 == held.envelope_sha256
    # The adopted trust is bound by the transfer receipt: a new operation.
    assert adopted.operation_id != held.operation_id
    # Its pinned closure fixes the allowance, recorded with the new authority.
    assert adopted.transfer_bytes == held.transfer_bytes


def test_adopting_changed_trust_with_equal_limit_starts_a_new_transfer(
    tmp_path, capture
):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from foxlight_model_preservation.signing import public_record

    ledger, publisher = mirror(tmp_path, capture)
    # Damage one supplied object: the download starts, writes its transfer
    # receipt under the selection's operation, then fails on the hash.
    damaged = next((capture.store / "objects/sha256").iterdir())
    original = damaged.read_bytes()
    damaged.write_bytes(b"x" * len(original))
    cycle(ledger)
    held = ledger.read().models[capture.identity]
    assert held.error and held.transfer_bytes is not None
    damaged.write_bytes(original)
    # Re-follow with changed trust and the same byte limit.
    wider = held.grant.model_copy(
        update={
            "trust": held.grant.trust.model_copy(
                update={
                    "keys": (
                        *held.grant.trust.keys,
                        public_record(Ed25519PrivateKey.generate()),
                    )
                }
            )
        }
    )
    assert wider.max_bytes == held.grant.max_bytes
    _refollow(ledger, publisher, wider)
    retry_selection(ledger, capture.identity, adopt_source_grant=True)
    adopted = ledger.read().models[capture.identity]
    assert adopted.operation_id != held.operation_id
    assert adopted.grant == wider and adopted.error is None
    # Repeating the adoption keeps the new operation (interruption-safe).
    retry_selection(ledger, capture.identity, adopt_source_grant=True)
    assert ledger.read().models[capture.identity].operation_id == (adopted.operation_id)
    cycle(ledger)
    held = ledger.read().models[capture.identity]
    assert held.state == "retained" and held.error is None, held.error
    verify_store(ledger.path / "store", capture.identity, capture.trust)


def test_adoption_refuses_keys_that_do_not_sign_the_pinned_wrapper(tmp_path, capture):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from foxlight_model_preservation.signing import public_record

    ledger, publisher = mirror(tmp_path, capture)
    cycle(ledger)
    held = ledger.read().models[capture.identity]
    other = held.grant.model_copy(
        update={
            "trust": held.grant.trust.model_copy(
                update={"keys": (public_record(Ed25519PrivateKey.generate()),)}
            )
        }
    )
    _refollow(ledger, publisher, other)
    with pytest.raises(ValueError):
        retry_selection(ledger, capture.identity, adopt_source_grant=True)
    assert ledger.read().models[capture.identity] == held


# Withdrawal: advisory by default, followed only by operator choice --------------


def _served(ledger: MirrorLedger, identity: str) -> bool:
    with ledger.service_lock():
        allowed, _ = public_selection(ledger, OperationControl())
    return "/" + manifest_path(identity) in allowed


def _follow_withdrawals(ledger: MirrorLedger, value: str = "on") -> dict:
    result = cli(
        "mirror",
        "config",
        "--dir",
        str(ledger.path),
        "--follow-withdrawals",
        value,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _status(ledger: MirrorLedger) -> dict:
    result = cli("mirror", "status", "--dir", str(ledger.path))
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _get(port: int, identity: str) -> int:
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    client.request("GET", "/" + manifest_path(identity))
    reply = client.getresponse()
    reply.read()
    client.close()
    return reply.status


def test_withdrawal_is_advisory_and_keeps_serving_by_default(tmp_path, capture):
    from foxlight_model_preservation.discovery import list_sources

    ledger, publisher = mirror(tmp_path, capture)
    cycle(ledger)
    assert _served(ledger, capture.identity)
    before = int(time.time())
    publisher.publish(version=2, manifest="b" * 64)
    cycle(ledger)
    held = ledger.read().models[capture.identity]
    (source,) = list_sources(ledger.path / "discovery")
    advisory = held.withdrawal
    assert advisory is not None and not advisory.acknowledged
    assert advisory.source_id == held.source_id == source.source_id
    assert advisory.catalog_id == source.catalog_id is not None
    assert advisory.observed_at is not None and advisory.observed_at >= before
    assert held.state == "retained" and not held.blocked and not held.error
    # The publisher's catalog is not a takedown authority: still served.
    assert _served(ledger, capture.identity)
    assert public(ledger.path, capture.identity).exists()
    status = _status(ledger)
    assert status["follow_withdrawals"] is False
    (shown,) = [
        item
        for item in status["withdrawal_advisories"]
        if item["manifest"] == capture.identity
    ]
    assert shown["manifest"] == capture.identity
    assert shown["source_id"] == source.source_id
    assert shown["catalog_id"] == source.catalog_id
    assert shown["serving_withheld"] is False
    assert "you decide" in shown["advice"]
    assert "mirror block" in shown["advice"] and capture.identity in shown["advice"]
    # A later catalog that still omits it keeps the first observation.
    publisher.publish(version=3, manifest="c" * 64)
    cycle(ledger)
    assert ledger.read().models[capture.identity].withdrawal == advisory
    # Relisted by a current catalog: the advisory clears.
    publisher.publish(version=4)
    cycle(ledger)
    assert ledger.read().models[capture.identity].withdrawal is None
    assert capture.identity not in {
        item["manifest"] for item in _status(ledger)["withdrawal_advisories"]
    }
    assert _served(ledger, capture.identity)


def test_followed_withdrawal_stops_serving_but_keeps_bytes(tmp_path, capture):
    ledger, publisher = mirror(tmp_path, capture)
    cycle(ledger)
    grant_restart(ledger, int(time.time()) + 600)
    granted = policy_id(ledger.read())
    receipt = _follow_withdrawals(ledger)
    # The operator's choice is reviewed policy: fresh consent is needed.
    assert receipt["configured"] == ["follow_withdrawals"]
    assert receipt["restart_grant_invalidated"] is True
    assert ledger.read().follow_withdrawals and ledger.read().restart is None
    assert policy_id(ledger.read()) != granted
    assert _served(ledger, capture.identity)
    publisher.publish(version=2, manifest="b" * 64)
    cycle(ledger)
    held = ledger.read().models[capture.identity]
    assert held.withdrawal is not None and held.state == "retained"
    assert not held.error and not held.blocked
    with ledger.service_lock():
        allowed, owners = public_selection(ledger, OperationControl())
    assert "/" + manifest_path(capture.identity) not in allowed
    assert not public(ledger.path, capture.identity).exists()
    assert not any(capture.identity in item for item in owners.values())
    (shown,) = _status(ledger)["withdrawal_advisories"]
    assert shown["serving_withheld"] is True
    # Retention rules are unchanged: the copy is kept and verifies.
    verify_store(ledger.path / "store", capture.identity, capture.trust)
    # Listed again by a current catalog: served again.
    publisher.publish(version=3)
    cycle(ledger)
    assert ledger.read().models[capture.identity].withdrawal is None
    assert _served(ledger, capture.identity)
    # Turning it off again serves withdrawn copies, as advisories.
    publisher.publish(version=4, manifest="b" * 64)
    cycle(ledger)
    assert not _served(ledger, capture.identity)
    assert _follow_withdrawals(ledger, "off")["restart_grant_invalidated"] is False
    assert not ledger.read().follow_withdrawals
    assert b"follow_withdrawals" not in (ledger.path / "mirror.json").read_bytes()
    assert _served(ledger, capture.identity)


def test_followed_withdrawal_survives_restart_before_public_selection(
    tmp_path, capture
):
    ledger, publisher = mirror(tmp_path, capture)
    configure_mirror(ledger, {"follow_withdrawals": True})
    cycle(ledger)
    with ledger.service_lock():
        public_selection(ledger, OperationControl())
    publisher.publish(version=2, manifest="b" * 64)
    cycle(ledger)
    # Killed before unlinking: the persisted receipt still withholds service.
    restarted = MirrorLedger(ledger.path)
    assert restarted.read().models[capture.identity].withdrawal is not None
    grant_restart(restarted, int(time.time()) + 600)
    ready: queue.Queue[int] = queue.Queue()
    thread = threading.Thread(
        target=lambda: run_mirror(restarted, resume=True, ready=ready.put)
    )
    thread.start()
    try:
        port = ready.get(timeout=SERVE_WAIT)
        end = time.monotonic() + SERVE_WAIT
        while public(ledger.path, capture.identity).exists():
            assert time.monotonic() < end
            time.sleep(0.05)
        assert _get(port, capture.identity) != 200
    finally:
        restarted.update(lambda s: s.model_copy(update={"paused": True}))
        thread.join(30)


@pytest.mark.parametrize("following", [False, True])
def test_expired_catalog_is_availability_not_withdrawal(tmp_path, capture, following):
    ledger, publisher = mirror(tmp_path, capture)
    configure_mirror(ledger, {"follow_withdrawals": following})
    expiry = datetime.now(UTC) + timedelta(seconds=4)
    publisher.publish(version=2, expires=expiry)
    cycle(ledger)
    assert ledger.read().models[capture.identity].state == "retained"
    time.sleep(max(0.0, (expiry - datetime.now(UTC)).total_seconds()) + 0.5)
    # The source now serves only expired metadata; the cached catalog is stale.
    cycle(ledger)
    from foxlight_model_preservation.discovery import list_sources

    (status,) = list_sources(ledger.path / "discovery")
    assert status.freshness == "stale"
    held = ledger.read().models[capture.identity]
    assert held.withdrawal is None and not held.error
    assert _served(ledger, capture.identity)


def test_publisher_loss_is_not_withdrawal(tmp_path, capture):
    ledger, publisher = mirror(tmp_path, capture)
    configure_mirror(ledger, {"follow_withdrawals": True})
    cycle(ledger)
    shutil.rmtree(publisher.path)
    cycle(ledger)
    assert ledger.read().models[capture.identity].withdrawal is None
    assert _served(ledger, capture.identity)


def test_withdrawal_persists_when_the_withdrawing_catalog_expires(tmp_path, capture):
    ledger, publisher = mirror(tmp_path, capture)
    configure_mirror(ledger, {"follow_withdrawals": True})
    cycle(ledger)
    assert _served(ledger, capture.identity)
    expiry = datetime.now(UTC) + timedelta(seconds=4)
    publisher.publish(version=2, manifest="b" * 64, expires=expiry)
    cycle(ledger)
    advisory = ledger.read().models[capture.identity].withdrawal
    assert advisory is not None
    time.sleep(max(0.0, (expiry - datetime.now(UTC)).total_seconds()) + 0.5)
    # The withdrawing catalog is now stale: neither a relisting nor a reason
    # to drop the advisory.
    cycle(ledger)
    from foxlight_model_preservation.discovery import list_sources

    (status,) = list_sources(ledger.path / "discovery")
    assert status.freshness == "stale"
    restarted = MirrorLedger(ledger.path)
    cycle(restarted)
    assert restarted.read().models[capture.identity].withdrawal == advisory
    assert not _served(restarted, capture.identity)
    assert not public(ledger.path, capture.identity).exists()
    # A current catalog listing it again resumes serving.
    publisher.publish(version=3)
    cycle(restarted)
    assert restarted.read().models[capture.identity].withdrawal is None
    assert _served(restarted, capture.identity)


def test_acknowledgement_is_the_operators_per_copy_exception(tmp_path, capture):
    ledger, publisher = mirror(tmp_path, capture)
    configure_mirror(ledger, {"follow_withdrawals": True})
    cycle(ledger)
    with pytest.raises(PreservationError, match="no followed catalog"):
        model_action(ledger, capture.identity, "acknowledge")
    publisher.publish(version=2, manifest="b" * 64)
    cycle(ledger)
    assert not _served(ledger, capture.identity)
    # The deprecated name still works, acknowledges, and says so.
    result = cli(
        "mirror",
        "unwithdraw",
        "--dir",
        str(ledger.path),
        "--manifest",
        capture.identity,
    )
    assert result.returncode == 0, result.stderr
    assert "acknowledge" in json.loads(result.stdout)["deprecated"]
    advisory = ledger.read().models[capture.identity].withdrawal
    assert advisory is not None and advisory.acknowledged
    assert _served(ledger, capture.identity)
    # Unlike the old unwithdraw, the next cycle does not undo the decision.
    cycle(ledger)
    assert ledger.read().models[capture.identity].withdrawal == advisory
    assert _served(ledger, capture.identity)
    # A relisting ends it; a later withdrawal is a new, unacknowledged advisory.
    publisher.publish(version=3)
    cycle(ledger)
    publisher.publish(version=4, manifest="b" * 64)
    cycle(ledger)
    renewed = ledger.read().models[capture.identity].withdrawal
    assert renewed is not None and not renewed.acknowledged
    assert not _served(ledger, capture.identity)
    result = cli(
        "mirror",
        "acknowledge",
        "--dir",
        str(ledger.path),
        "--manifest",
        capture.identity,
    )
    assert result.returncode == 0, result.stderr
    assert "deprecated" not in json.loads(result.stdout)
    assert _served(ledger, capture.identity)


def test_block_is_immediate_persistent_and_independent_of_catalogs(tmp_path, capture):
    ledger, publisher = mirror(tmp_path, capture)
    cycle(ledger)
    publisher.publish(version=2, manifest="b" * 64)
    cycle(ledger)
    assert ledger.read().models[capture.identity].withdrawal is not None
    grant_restart(ledger, int(time.time()) + 600)

    def serve(target: MirrorLedger) -> tuple[threading.Thread, int]:
        ready: queue.Queue[int] = queue.Queue()
        thread = threading.Thread(
            target=lambda: run_mirror(target, resume=True, ready=ready.put)
        )
        thread.start()
        return thread, ready.get(timeout=SERVE_WAIT)

    thread, port = serve(ledger)
    try:
        end = time.monotonic() + SERVE_WAIT
        while _get(port, capture.identity) != 200:
            assert time.monotonic() < end
            time.sleep(0.05)
        # The operator acts on a notice: refused on the very next request.
        result = cli(
            "mirror",
            "block",
            "--dir",
            str(ledger.path),
            "--manifest",
            capture.identity,
        )
        assert result.returncode == 0, result.stderr
        assert _get(port, capture.identity) == 503
    finally:
        ledger.update(lambda s: s.model_copy(update={"paused": True}))
        thread.join(30)
    # Blocking is not policy: the standing grant still covers a restart.
    restarted = MirrorLedger(ledger.path)
    restarted.update(lambda s: s.model_copy(update={"paused": False}))
    assert restarted.read().models[capture.identity].blocked
    # Neither a relisting nor an acknowledgement unblocks it.
    publisher.publish(version=3)
    cycle(restarted)
    assert restarted.read().models[capture.identity].withdrawal is None
    assert restarted.read().models[capture.identity].blocked
    thread, port = serve(restarted)
    try:
        end = time.monotonic() + SERVE_WAIT
        while public(ledger.path, capture.identity).exists():
            assert time.monotonic() < end
            time.sleep(0.05)
        assert _get(port, capture.identity) != 200
    finally:
        restarted.update(lambda s: s.model_copy(update={"paused": True}))
        thread.join(30)
    assert not _served(restarted, capture.identity)


def _legacy(ledger: MirrorLedger, identity: str) -> None:
    """Rewrite the ledger as an earlier release recorded a withdrawal."""
    path = ledger.path / "mirror.json"
    value = json.loads(path.read_bytes())
    value["models"][identity].pop("withdrawal", None)
    value["models"][identity]["withdrawn"] = True
    path.write_bytes(json.dumps(value, sort_keys=True).encode())


def test_upgrade_reads_old_withdrawn_receipts_as_advisories(tmp_path, capture, caplog):
    ledger, publisher = mirror(tmp_path, capture)
    cycle(ledger)
    publisher.publish(version=2, manifest="b" * 64)
    # An earlier release wrote its receipt after accepting the withdrawing
    # catalog, so the cache holds it too. Planted beside the older catalog that
    # still lists the copy, the receipt was cleared whenever the running
    # mirror's first cycle (cut short by the pause below on a slow runner)
    # recorded what that cached catalog said.
    cycle(ledger)
    _legacy(ledger, capture.identity)
    upgraded = MirrorLedger(ledger.path)
    assert upgraded.legacy_withdrawals() == (capture.identity,)
    held = upgraded.read().models[capture.identity]
    # Old ledgers stay readable; the facts an old release never kept are unknown.
    assert held.withdrawal is not None and held.withdrawal.observed_at is None
    assert held.withdrawal.source_id == held.source_id
    (shown,) = _status(upgraded)["withdrawal_advisories"]
    assert shown["serving_withheld"] is False and shown["observed_at"] is None
    # Reading (status) converts nothing on disk.
    assert upgraded.legacy_withdrawals() == (capture.identity,)
    granted = policy_id(upgraded.read())
    caplog.set_level("WARNING", logger="foxlight_model_preservation.mirror_state")
    ready: queue.Queue[int] = queue.Queue()
    thread = threading.Thread(
        target=lambda: run_mirror(
            upgraded, public=True, seconds=SERVE_WINDOW, ready=ready.put
        )
    )
    thread.start()
    try:
        port = ready.get(timeout=SERVE_WAIT)
        end = time.monotonic() + SERVE_WAIT
        while _get(port, capture.identity) != 200:
            assert time.monotonic() < end
            time.sleep(0.05)
    finally:
        upgraded.update(lambda s: s.model_copy(update={"paused": True}))
        thread.join(30)
    (record,) = [r for r in caplog.records if "advisory" in r.getMessage()]
    message = record.getMessage()
    assert "served again" in message and capture.identity in message
    assert "mirror block" in message and "--follow-withdrawals on" in message
    # Converted once: the ledger now holds the advisory form, and the receipt
    # rewrite did not change policy identity.
    raw = (ledger.path / "mirror.json").read_bytes()
    assert b'"withdrawn"' not in raw and b'"withdrawal"' in raw
    assert upgraded.legacy_withdrawals() == ()
    assert policy_id(upgraded.read()) == granted
    # A cycle fills in the withdrawing catalog it can now see.
    upgraded.update(lambda s: s.model_copy(update={"paused": False}))
    cycle(upgraded)
    advisory = upgraded.read().models[capture.identity].withdrawal
    assert advisory is not None and advisory.observed_at is not None
    assert advisory.catalog_id is not None
    # Two receipts for one selection are ambiguous: refused, not guessed.
    value = json.loads(raw)
    value["models"][capture.identity]["withdrawn"] = True
    (ledger.path / "mirror.json").write_bytes(json.dumps(value).encode())
    with pytest.raises(ValueError, match="two withdrawal receipts"):
        MirrorLedger(ledger.path)


def test_upgrade_with_followed_withdrawals_keeps_old_copies_withheld(
    tmp_path, capture, caplog
):
    ledger, publisher = mirror(tmp_path, capture)
    configure_mirror(ledger, {"follow_withdrawals": True})
    cycle(ledger)
    _legacy(ledger, capture.identity)
    caplog.set_level("WARNING", logger="foxlight_model_preservation.mirror_state")
    upgraded = MirrorLedger(ledger.path)
    # Whatever writes first after upgrade converts and logs, e.g. a CLI action.
    result = cli(
        "mirror",
        "grant-resume",
        "--dir",
        str(ledger.path),
        "--for",
        "1d",
        "--confirm-resume",
    )
    assert result.returncode == 0, result.stderr
    assert "still withheld" in result.stderr and capture.identity in result.stderr
    assert upgraded.legacy_withdrawals() == ()
    assert mirror_module.convert_legacy_withdrawals(upgraded) == ()
    assert not caplog.records
    _legacy(ledger, capture.identity)
    assert mirror_module.convert_legacy_withdrawals(upgraded) == (capture.identity,)
    (record,) = caplog.records
    assert "still withheld" in record.getMessage()
    assert not _served(upgraded, capture.identity)


def test_withdrawn_entry_is_not_newly_admitted(tmp_path, capture):
    ledger, publisher = mirror(tmp_path, capture)
    cycle(ledger)
    publisher.publish(version=2, manifest="b" * 64)
    # A mirror that never held it follows the same publisher after withdrawal.
    late = MirrorLedger.initialize(
        tmp_path / "late",
        name="Late mirror",
        disk_bytes=2 * 1024**3,
        ingress_daily=10 * 1024**3,
        egress_daily=1024**3,
        egress_monthly=10 * 1024**3,
        port=0,
    )
    bootstrap = publisher.bootstrap()
    follow(
        late,
        encode_record(bootstrap),
        bootstrap_identity(bootstrap),
        next(iter(ledger.read().sources.values())),
    )
    cycle(late)
    cycle(ledger)
    # Discovery is unchanged: the withdrawn entry is not admitted anew ...
    assert capture.identity not in late.read().models
    assert not (late.path / "store" / manifest_path(capture.identity)).exists()
    # ... while the mirror that already held it keeps serving its own copy.
    assert ledger.read().models[capture.identity].withdrawal is not None
    assert _served(ledger, capture.identity)


def test_withdrawn_pending_selection_is_not_fetched(tmp_path, capture):
    ledger, publisher = mirror(tmp_path, capture)
    # Selected but not yet retained (no bytes): a withdrawal stops the intake.
    now = time.gmtime()
    ledger.update(
        lambda s: s.model_copy(
            update={
                "ingress_used": s.ingress_daily - 40 * 1024**2,
                "day": time.strftime("%Y-%m-%d", now),
                "month": time.strftime("%Y-%m", now),
            }
        )
    )
    cycle(ledger)
    held = ledger.read().models[capture.identity]
    assert held.state == "pending" and held.deferred is not None
    ledger.update(lambda s: s.model_copy(update={"ingress_used": 0}))
    publisher.publish(version=2, manifest="b" * 64)
    cycle(ledger)
    held = ledger.read().models[capture.identity]
    assert held.state == "pending" and held.withdrawal is not None
    assert not (ledger.path / "store" / manifest_path(capture.identity)).exists()
    assert not _served(ledger, capture.identity)


def test_native_transports_follow_the_same_withdrawal_rule(tmp_path, capture):
    from foxlight_model_preservation.mirror_native import NativeMirrors

    ledger, publisher = mirror(tmp_path, capture)
    cycle(ledger)
    native = NativeMirrors(ledger, lambda _state: True)
    assert native._allowed(ledger.read(), capture.identity)
    publisher.publish(version=2, manifest="b" * 64)
    cycle(ledger)
    # Advisory by default: IPFS/BitTorrent keep serving the retained copy.
    assert native._allowed(ledger.read(), capture.identity)
    configure_mirror(ledger, {"follow_withdrawals": True})
    assert not native._allowed(ledger.read(), capture.identity)
    model_action(ledger, capture.identity, "acknowledge")
    assert native._allowed(ledger.read(), capture.identity)
    model_action(ledger, capture.identity, "block")
    assert not native._allowed(ledger.read(), capture.identity)
    model_action(ledger, capture.identity, "unblock")
    publisher.publish(version=3)
    cycle(ledger)
    publisher.publish(version=4, manifest="b" * 64)
    cycle(ledger)
    assert not native._allowed(ledger.read(), capture.identity)
    publisher.publish(version=5)
    cycle(ledger)
    assert native._allowed(ledger.read(), capture.identity)


def test_one_source_budget_failure_does_not_delay_another_withdrawal(tmp_path, capture):
    from foxlight_model_preservation.discovery import source_identity

    # Sources refresh in identity order, so the withdrawing source must sort
    # first. Identities are random: pick a first source in the lower half, then
    # a second above it. Each draw succeeds at least half the time; searching
    # only for the second fails whenever the first lands near the top.
    for attempt in range(64):
        root = tmp_path / f"fixture{attempt}"
        root.mkdir()
        ledger, publisher = mirror(root, capture)
        (first,) = ledger.read().sources
        if first < "8":
            break
    else:
        pytest.fail("no low first source identity drawn")
    for attempt in range(64):
        second = Publisher(tmp_path / f"second{attempt}", capture, name="Second")
        bootstrap = second.bootstrap()
        if source_identity(bootstrap) > first:
            break
    else:
        pytest.fail("no higher second source identity drawn")
    second.publish(manifest="c" * 64)
    follow(
        ledger,
        encode_record(bootstrap),
        bootstrap_identity(bootstrap),
        next(iter(ledger.read().sources.values())),
    )
    configure_mirror(ledger, {"follow_withdrawals": True})
    cycle(ledger)
    assert _served(ledger, capture.identity)
    publisher.publish(version=2, manifest="b" * 64)
    # Only the first source's refresh fits today's remaining budget.
    ledger.update(
        lambda s: s.model_copy(update={"ingress_used": s.ingress_daily - 40 * 1024**2})
    )
    with pytest.raises(PreservationError, match="ingress"):
        cycle(ledger)
    assert ledger.read().models[capture.identity].withdrawal is not None
    assert not _served(ledger, capture.identity)


def test_gossip_round_reads_stay_within_its_reservation(tmp_path, capture, monkeypatch):
    from foxlight_model_preservation import mirror_announce, nostr
    from foxlight_model_preservation.mirror_cards import MAX_PEERS_BYTES, issue_card
    from foxlight_model_preservation.mirror_directory import MirrorDirectory
    from foxlight_model_preservation.models import MAX_METADATA_BYTES

    assert mirror_announce.ROUND_BODY_BYTES <= mirror_announce.GOSSIP_RESERVATION

    # spot_check contacts at most max_checks mirrors (two addresses each).
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    directory = MirrorDirectory(tmp_path / "directory")
    directory.ingest(
        issue_card(
            Ed25519PrivateKey.generate(),
            (f"https://m{index}.example", f"https://n{index}.example"),
            {capture.identity},
        )
        for index in range(10)
    )
    reads: list[int] = []

    def bounded(_base, _relative, _policy, maximum, _deadline, _control):
        reads.append(maximum)
        raise OSError("unreachable")

    monkeypatch.setattr(
        "foxlight_model_preservation.mirror_directory._bounded_read", bounded
    )
    directory.spot_check({capture.identity}, max_checks=2)
    assert len(reads) <= 4 and sum(reads) <= 4 * MAX_METADATA_BYTES
    reads.clear()
    directory.spot_check({capture.identity})
    assert len(reads) > 4  # The default keeps today's unbounded behavior.

    # Relay reads stop before another message could exceed max_bytes.
    class Hostile:
        def __init__(self, *_args, **_kwargs):
            self.received = 0

        def send(self, _message):
            return None

        def receive(self, _deadline, _control=None):
            self.received += nostr.MAX_MESSAGE_BYTES
            return ["EVENT", "x", {}]

        def close(self):
            return None

    relays = tuple(f"wss://r{index}.example" for index in range(6))
    limit = 5 * 1024**2
    monkeypatch.setattr(nostr, "Relay", Hostile)
    totals: list[int] = []
    real = Hostile.close

    def close(self):
        totals.append(self.received)
        real(self)

    monkeypatch.setattr(Hostile, "close", close)
    nostr.find_cards(relays, TransferPolicy(timeout=5), max_bytes=limit)
    assert 0 < sum(totals) <= limit
    totals.clear()
    card = issue_card(
        Ed25519PrivateKey.generate(), ("https://m.example",), {capture.identity}
    )
    nostr.publish_card(
        card, b"\x01" * 32, relays, TransferPolicy(timeout=5), max_bytes=limit
    )
    assert 0 < sum(totals) <= limit

    # The round passes every bound to the shared discovery functions.
    ledger, _ = mirror(tmp_path / "round", capture)
    configure_mirror(
        ledger,
        {"announce": AnnouncePolicy(public_url="https://m.example", nostr=True)},
    )
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        mirror_announce,
        "find_cards",
        lambda *a, **kw: seen.setdefault("find", kw["max_bytes"]) and [],
    )
    monkeypatch.setattr(
        MirrorDirectory,
        "crawl",
        lambda self, *a, **kw: seen.setdefault("crawl", kw["max_documents"]) and 0,
    )
    monkeypatch.setattr(
        MirrorDirectory,
        "spot_check",
        lambda self, *a, **kw: seen.setdefault("check", kw["max_checks"]) and 0,
    )
    assert Announcer(ledger).round(frozenset(), OperationControl())
    assert seen == {
        "find": mirror_announce.RELAY_FIND_BYTES,
        "crawl": mirror_announce.CRAWL_DOCUMENTS,
        "check": mirror_announce.SPOT_CHECKS,
    }
    assert (
        mirror_announce.CRAWL_DOCUMENTS * (MAX_PEERS_BYTES + 1)
        + mirror_announce.SPOT_CHECKS * 2 * (MAX_METADATA_BYTES + 1)
        + mirror_announce.RELAY_FIND_BYTES
        + mirror_announce.RELAY_PUBLISH_BYTES
        == mirror_announce.ROUND_BODY_BYTES
    )


# Announcement -------------------------------------------------------------------


def test_card_names_public_url_and_is_signed_by_its_own_mirror_key(tmp_path, capture):
    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    configure_mirror(
        ledger,
        {
            "announce": AnnouncePolicy(
                public_url="https://mirror.example", contact="ops@mirror.example"
            )
        },
    )
    card = Announcer(ledger).publish(frozenset({capture.identity}))
    assert card is not None
    claimed = verify_card(card)
    assert claimed.addresses == ("https://mirror.example",)
    assert claimed.covers(capture.identity) and claimed.contact
    assert claimed.mirror_key.key_id not in {k.key_id for k in capture.trust.keys}
    assert stat.S_IMODE((ledger.path / KEY_FILE).stat().st_mode) == 0o600
    (raw,) = read_peers((ledger.path / "peers.json").read_bytes())
    assert raw == card
    # Nothing served: no card, but the document stays valid.
    assert Announcer(ledger).publish(frozenset()) is None
    assert read_peers((ledger.path / "peers.json").read_bytes()) == []


def test_dht_only_expresses_plain_http_roots():
    assert dht_port(AnnouncePolicy(public_url="https://m.example", dht=True)) is None
    assert dht_port(AnnouncePolicy(public_url="http://m.example:8080", dht=True)) == (
        8080
    )
    assert dht_port(AnnouncePolicy(public_url="http://m.example")) is None


@pytest.mark.parametrize("withdrawn", [False, True])
def test_card_renews_when_reconcile_is_budget_blocked(tmp_path, capture, withdrawn):
    ledger, publisher = mirror(tmp_path, capture)
    cycle(ledger)
    if withdrawn:
        # Only the operator's choice to follow withdrawals withholds it.
        configure_mirror(ledger, {"follow_withdrawals": True})
        publisher.publish(version=2, manifest="b" * 64)
        cycle(ledger)
        assert ledger.read().models[capture.identity].withdrawal is not None
    configure_mirror(
        ledger, {"announce": AnnouncePolicy(public_url="https://m.example")}
    )
    # Below the 32 MiB refresh reservation: every reconcile raises on budget.
    configure_mirror(ledger, {"ingress_daily": 1024**2})
    ready: queue.Queue[int] = queue.Queue()
    thread = threading.Thread(
        target=lambda: run_mirror(
            ledger, public=True, seconds=SERVE_WINDOW, ready=ready.put
        )
    )
    thread.start()
    try:
        ready.get(timeout=SERVE_WAIT)
        end = time.monotonic() + SERVE_WAIT
        while not (ledger.path / "peers.json").exists() or not ledger.read().last_error:
            assert time.monotonic() < end
            time.sleep(0.1)
        cards = read_peers((ledger.path / "peers.json").read_bytes())
        if withdrawn:
            # A withheld model never appears in the card: no card at all.
            assert cards == []
            assert not public(ledger.path, capture.identity).exists()
        else:
            (card,) = cards
            assert verify_card(card).covers(capture.identity)
        assert ledger.read().last_cycle is not None  # From the earlier cycles.
        assert "invalid_or_unsafe" in (ledger.read().last_error or "")
    finally:
        ledger.update(lambda s: s.model_copy(update={"paused": True}))
        thread.join(30)
    assert not thread.is_alive()


def test_running_mirror_serves_its_peers_document_over_ipv6(tmp_path, capture):
    ledger, _ = mirror(tmp_path, capture)
    cycle(ledger)
    configure_mirror(
        ledger,
        {"bind": "::1", "announce": AnnouncePolicy(public_url="https://m.example")},
    )
    ready: queue.Queue[int] = queue.Queue()
    thread = threading.Thread(
        target=lambda: run_mirror(
            ledger, public=True, seconds=SERVE_WINDOW, ready=ready.put
        )
    )
    thread.start()
    try:
        port = ready.get(timeout=SERVE_WAIT)
        end = time.monotonic() + SERVE_WAIT
        while True:
            client = http.client.HTTPConnection("::1", port, timeout=3)
            client.request("GET", "/" + PEERS_PATH)
            reply = client.getresponse()
            body = reply.read()
            client.close()
            if reply.status == 200 and read_peers(body):
                break
            assert time.monotonic() < end
            time.sleep(0.1)
        (card,) = read_peers(body)
        assert verify_card(card).covers(capture.identity)
        assert ledger.read().last_error is None
    finally:
        ledger.update(lambda s: s.model_copy(update={"paused": True}))
        thread.join(30)
    assert not thread.is_alive()


# Quickstart -----------------------------------------------------------------------


def collection(tmp_path: Path, capture: Capture, **changes: object) -> Path:
    base = Path(tempfile.mkdtemp(dir=tmp_path))
    publisher = Publisher(base / "publisher", capture)
    bootstrap = publisher.bootstrap()
    folder = base / "collection"
    folder.mkdir()
    (folder / "source.json").write_bytes(encode_record(bootstrap))
    value: dict[str, object] = {
        "schema_version": "sovereign.mirror-collection.v1",
        "title": "Fixture collection",
        "source": {"file": "source.json"},
        "expected_bootstrap_id": bootstrap_identity(bootstrap),
        "trust": capture.trust.model_dump(mode="json"),
        "terms": [item.model_dump() for item in TERMS],
    }
    value.update(changes)
    path = folder / "collection.json"
    path.write_text(json.dumps(value))
    return path


def settings(**changes: object) -> QuickstartSettings:
    values: dict[str, object] = {
        "name": "Quick",
        "disk_bytes": 2 * 1024**3,
        "ingress_daily": 1024**3,
        "egress_daily": 1024**3,
        "egress_monthly": 8 * 1024**3,
    }
    values.update(changes)
    return QuickstartSettings(**values)  # type: ignore[arg-type]


def test_collection_schema_is_strict(tmp_path, capture):
    path = collection(tmp_path, capture)
    loaded, document = load_collection(path)
    assert loaded.title == "Fixture collection" and document
    raw = path.read_text()
    path.write_text(raw[:-1] + ', "title": "Twice"}')
    with pytest.raises(PreservationError):
        load_collection(path)
    for change in (
        {"unexpected": True},
        {"schema_version": "sovereign.mirror-collection.v2"},
        {"source": {"file": "../outside.json"}},
        {"source": {"file": "/etc/hosts"}},
        {"source": {}},
        {"terms": []},
    ):
        with pytest.raises(ValueError):
            load_collection(collection(tmp_path, capture, **change))
    loaded, document = load_collection(
        collection(tmp_path, capture, expected_bootstrap_id="c" * 64)
    )
    with pytest.raises(PreservationError, match="bootstrap"):
        review(loaded, document, settings())


def test_review_shows_everything_consented_to(tmp_path, capture):
    loaded, document = load_collection(collection(tmp_path, capture))
    text, summary = review(
        loaded,
        document,
        settings(
            announce=AnnouncePolicy(public_url="http://m.example", nostr=True),
            bind="0.0.0.0",  # noqa: S104 - reviewed choice
        ),
    )
    for key in capture.trust.keys:
        assert key.key_id in text
    assert "test-only: LICENSE" in text and "bootstrap id" in text
    assert "2 GiB" in text and "8 GiB per UTC month" in text
    assert re.search(r"^Public URL: +http://m\.example$", text, re.MULTILINE)
    assert "Nostr" in text
    assert "not anonymous" in text and "plain http://" in text
    assert "directly reachable" in text
    assert summary["anonymous"] is False and summary["warnings"]


def test_quickstart_requires_consent_and_writes_nothing_without_it(
    tmp_path, capture, monkeypatch
):
    path = collection(tmp_path, capture)
    target = tmp_path / "mirror"

    def forbidden(*_args, **_kwargs):
        raise AssertionError("quickstart review connected or listened")

    base = ("mirror", "quickstart", "--dir", str(target), "--collection", str(path))
    plan_only = cli(*base, "--plan-only")
    assert plan_only.returncode == 0, plan_only.stderr
    assert "Type 'serve'" not in plan_only.stderr
    assert "not anonymous" in plan_only.stderr
    assert not json.loads(plan_only.stdout)["consented"] and not target.exists()
    refused = cli(*base, stdin="")
    assert refused.returncode == 1 and "consent" in refused.stderr
    assert not target.exists()
    loaded, document = load_collection(path)
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.socket.bind", forbidden)
    with pytest.raises(PreservationError, match="consent"):
        apply_quickstart(target, loaded, document, settings(), consented=False)
    assert not target.exists()


def test_quickstart_sets_up_follow_and_grant_from_one_file(tmp_path, capture):
    path = collection(tmp_path, capture)
    target = tmp_path / "mirror"
    result = cli(
        "mirror",
        "quickstart",
        "--dir",
        str(target),
        "--collection",
        str(path),
        "--name",
        "Quick",
        "--disk-bytes",
        "2G",
        "--public-url",
        "https://quick.example",
        "--resume-for",
        "30d",
        "--yes-i-consent-to-public-serving",
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["consented"] and receipt["initialized"]
    assert not receipt["network_started"] and receipt["mirror_key"]
    state = MirrorLedger(target).read()
    assert state.name == "Quick" and state.bind == "127.0.0.1"
    assert state.announce == AnnouncePolicy(public_url="https://quick.example")
    (grant,) = state.sources.values()
    assert grant.trust == capture.trust and grant.terms == TERMS
    assert grant.max_bytes == derived_model_limit(2 * 1024**3)
    assert not grant.allow_http and not grant.allow_private and not grant.allow_local
    assert restart_allowed(state)
    assert abs(state.restart.expires - (time.time() + 30 * 86400)) < 120
    # Re-running with another policy updates it and grants consent afresh.
    again = cli(
        "mirror",
        "quickstart",
        "--dir",
        str(target),
        "--collection",
        str(path),
        "--name",
        "Quick",
        "--disk-bytes",
        "3G",
        "--yes-i-consent-to-public-serving",
    )
    assert again.returncode == 0, again.stderr
    state = MirrorLedger(target).read()
    assert state.disk_bytes == 3 * 1024**3 and state.announce is None
    assert not json.loads(again.stdout)["initialized"] and restart_allowed(state)


def test_interactive_consent_prompt(tmp_path, capture, monkeypatch):
    import io

    from foxlight_model_preservation.mirror_cli import (
        MirrorArguments,
        run_mirror_command,
    )

    path = collection(tmp_path, capture)

    class Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    arguments = MirrorArguments(
        mirror_action="quickstart",
        mirror_dir=tmp_path / "mirror",
        collection=path,
        mirror_name="Prompted",
        disk_bytes=None,
        ingress_daily=None,
        egress_daily=None,
        egress_monthly=None,
        mirror_bind=None,
        mirror_port=None,
        public_url=None,
        contact=None,
        announce_nostr=None,
        announce_dht=None,
        confirm_public_announcement=False,
        resume_for=86400,
        model_max_bytes=None,
        plan_only=False,
        consent_public_serving=False,
        run_after=False,
    )
    monkeypatch.setattr(sys, "stdin", Terminal("no\n"))
    with pytest.raises(PreservationError, match="consent"):
        run_mirror_command(arguments, OperationControl())
    assert not (tmp_path / "mirror").exists()
    monkeypatch.setattr(sys, "stdin", Terminal("serve\n"))
    assert run_mirror_command(arguments, OperationControl())["consented"]
    assert restart_allowed(MirrorLedger(tmp_path / "mirror").read())


def _granted_quickstart_mirror(tmp_path: Path, capture: Capture) -> Path:
    target = tmp_path / "granted"
    loaded, document = load_collection(collection(tmp_path, capture))
    apply_quickstart(target, loaded, document, settings(), consented=True)
    assert restart_allowed(MirrorLedger(target).read())
    return target


def test_refused_quickstart_keeps_the_existing_mirror_and_grant(tmp_path, capture):
    target = _granted_quickstart_mirror(tmp_path, capture)
    before = (target / "mirror.json").read_bytes()
    state = MirrorLedger(target).read()
    # A different source with a disk budget below retained usage plus headroom.
    other, document = load_collection(collection(tmp_path, capture))
    assert other.expected_bootstrap_id not in str(state.model_dump())
    tight = settings(disk_bytes=HEADROOM + 1, max_bytes=1024, name="Changed")
    with pytest.raises(PreservationError, match="disk admission"):
        apply_quickstart(target, other, document, tight, consented=True)
    after = MirrorLedger(target).read()
    assert (target / "mirror.json").read_bytes() == before
    assert policy_id(after) == policy_id(state) and after.restart == state.restart
    assert restart_allowed(after)


def test_unforeseen_quickstart_refusal_restores_the_ledger(
    tmp_path, capture, monkeypatch
):
    target = _granted_quickstart_mirror(tmp_path, capture)
    before = (target / "mirror.json").read_bytes()
    other, document = load_collection(collection(tmp_path, capture))

    def refuse(*_args: object, **_kwargs: object) -> str:
        raise PreservationError("cache import would roll back trusted roots")

    monkeypatch.setattr("foxlight_model_preservation.mirror_quickstart.follow", refuse)
    changed = settings(disk_bytes=4 * 1024**3, name="Changed")
    with pytest.raises(PreservationError, match="roll back"):
        apply_quickstart(target, other, document, changed, consented=True)
    assert (target / "mirror.json").read_bytes() == before
    assert restart_allowed(MirrorLedger(target).read())


# Restart idempotency (the container re-runs quickstart on every start) --------


def _bundle_collection(
    tmp_path: Path, capture: Capture
) -> tuple[Path, Publisher, Path]:
    """A collection holding a discovery-cache snapshot taken at catalog v1."""
    from foxlight_model_preservation.discovery import (
        export_source,
        follow_source,
        refresh_source,
    )

    publisher = Publisher(tmp_path / "publisher", capture)
    bootstrap = publisher.bootstrap()
    client = tmp_path / "client"
    status = follow_source(
        client,
        encode_record(bootstrap),
        bootstrap_identity(bootstrap),
        allow_local=True,
    )
    refresh_source(client, status.source_id)
    folder = tmp_path / "collection"
    folder.mkdir()
    (folder / "source.json").write_bytes(export_source(client, status.source_id))
    path = folder / "collection.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "sovereign.mirror-collection.v1",
                "title": "Snapshot collection",
                "source": {"file": "source.json"},
                "expected_bootstrap_id": bootstrap_identity(bootstrap),
                "trust": capture.trust.model_dump(mode="json"),
                "terms": [item.model_dump() for item in TERMS],
            }
        )
    )
    return path, publisher, client


def _restart(target: Path, path: Path, **changes: object) -> dict[str, object]:
    loaded, document = load_collection(path)
    _ledger, receipt = apply_quickstart(
        target, loaded, document, settings(**changes), consented=True
    )
    return receipt


def test_restart_after_refresh_does_not_reimport_an_older_snapshot(tmp_path, capture):
    from foxlight_model_preservation.discovery import (
        export_source,
        list_sources,
        refresh_source,
    )

    path, publisher, client = _bundle_collection(tmp_path, capture)
    target = tmp_path / "mirror"
    first = _restart(target, path)
    assert first["initialized"] and first["source_reimported"]
    # A later refresh advances the mirror's cached evidence past the snapshot.
    publisher.publish(version=2)
    refresh_source(client, str(first["source_id"]))
    loaded, _ = load_collection(path)
    ledger = MirrorLedger(target)
    follow(
        ledger,
        export_source(client, str(first["source_id"])),
        loaded.expected_bootstrap_id,
        ledger.read().sources[str(first["source_id"])],
    )
    # Reimporting the original snapshot would be refused as a rollback...
    with pytest.raises(PreservationError, match="roll back"):
        follow(
            ledger,
            (path.parent / "source.json").read_bytes(),
            loaded.expected_bootstrap_id,
            ledger.read().sources[str(first["source_id"])],
        )
    before = list_sources(target / "discovery")
    # ...so a restart applies the reviewed grant without reimporting it.
    again = _restart(target, path)
    assert not again["source_reimported"] and not again["policy_changed"]
    assert list_sources(target / "discovery") == before
    assert restart_allowed(MirrorLedger(target).read())


def test_restart_keeps_policy_and_renews_the_grant(tmp_path, capture):
    path = collection(tmp_path, capture)
    target = tmp_path / "mirror"
    _restart(target, path)
    ledger = MirrorLedger(target)
    grant_restart(ledger, int(time.time()) + 365 * 86400)
    policy = policy_id(ledger.read())
    sources = ledger.read().sources
    receipt = _restart(target, path)
    state = ledger.read()
    assert not receipt["policy_changed"] and not receipt["source_reimported"]
    assert policy_id(state) == policy and state.sources == sources
    assert restart_allowed(state)
    # The collection's settings define the standing grant on every start.
    assert abs(state.restart.expires - (time.time() + 180 * 86400)) < 120


def test_restart_with_changed_environment_applies_it(tmp_path, capture):
    path = collection(tmp_path, capture)
    target = tmp_path / "mirror"
    _restart(target, path)
    policy = policy_id(MirrorLedger(target).read())
    receipt = _restart(target, path, disk_bytes=3 * 1024**3, name="Renamed")
    state = MirrorLedger(target).read()
    assert receipt["policy_changed"] and policy_id(state) != policy
    assert state.disk_bytes == 3 * 1024**3 and state.name == "Renamed"
    # The derived per-model limit followed the new disk budget.
    (grant,) = state.sources.values()
    assert grant.max_bytes == derived_model_limit(3 * 1024**3)
    assert restart_allowed(state)


@pytest.mark.parametrize("action", ["pause", "leave"])
def test_restart_of_a_paused_or_departed_mirror_refuses_clearly(
    tmp_path, capture, action
):
    path = collection(tmp_path, capture)
    target = tmp_path / "mirror"
    _restart(target, path)
    assert cli("mirror", action, "--dir", str(target)).returncode == 0
    before = (target / "mirror.json").read_bytes()
    with pytest.raises(PreservationError, match="paused|left"):
        _restart(target, path)
    assert (target / "mirror.json").read_bytes() == before
    refused = cli("mirror", "run", "--dir", str(target), "--resume")
    assert refused.returncode == 1 and "paused/left" in refused.stderr
    if action == "pause":
        assert cli("mirror", "resume", "--dir", str(target)).returncode == 0
        _restart(target, path)
        assert restart_allowed(MirrorLedger(target).read())


def test_restart_with_an_expired_grant_renews_it(tmp_path, capture):
    from foxlight_model_preservation.mirror_models import RestartGrant

    path = collection(tmp_path, capture)
    target = tmp_path / "mirror"
    _restart(target, path)
    ledger = MirrorLedger(target)
    ledger.update(
        lambda s: s.model_copy(
            update={
                "restart": RestartGrant(
                    policy_id=s.restart.policy_id, expires=int(time.time()) - 1
                )
            }
        )
    )
    refused = cli("mirror", "run", "--dir", str(target), "--resume")
    assert refused.returncode == 1 and "restart authority" in refused.stderr
    _restart(target, path)
    assert restart_allowed(ledger.read())


def test_restart_with_a_full_disk_still_starts(tmp_path, capture, monkeypatch):
    path = collection(tmp_path, capture)
    target = tmp_path / "mirror"
    _restart(target, path)
    monkeypatch.setattr(MirrorLedger, "usage", lambda self: self.read().disk_bytes)
    receipt = _restart(target, path)
    assert not receipt["source_reimported"]
    assert restart_allowed(MirrorLedger(target).read())


def test_restart_after_an_interrupted_init_refuses_clearly(tmp_path, capture):
    path = collection(tmp_path, capture)
    target = tmp_path / "mirror"
    target.mkdir()
    with pytest.raises(PreservationError, match="without mirror.json"):
        _restart(target, path)


# Packaging ------------------------------------------------------------------------


def test_container_without_consent_is_plan_only(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls"
    stub = bin_dir / "model-preserve"
    stub.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n')
    stub.chmod(0o755)
    environment = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "SOVEREIGN_MIRROR_DIR": str(tmp_path / "data"),
        "SOVEREIGN_MIRROR_COLLECTION": str(tmp_path / "collection.json"),
        "SOVEREIGN_MIRROR_DOMAIN": "mirror.example",
        "SOVEREIGN_MIRROR_BUDGET": "500G",
        "SOVEREIGN_MIRROR_IDLE": "0",
    }
    script = ROOT / "deploy/mirror/entrypoint.sh"
    result = subprocess.run(  # noqa: S603 - repository script, fixed arguments
        ["/bin/sh", str(script)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    assert len(calls) == 1 and "--plan-only" in calls[0]
    assert "--yes-i-consent-to-public-serving" not in calls[0]
    arguments = calls[0].split()
    assert arguments[arguments.index("--public-url") + 1] == "https://mirror.example"
    assert arguments[arguments.index("--disk-bytes") + 1] == "500G"
    # Only the exact consent value enables serving.
    log.unlink()
    environment["SOVEREIGN_MIRROR_CONSENT"] = "yes"
    subprocess.run(  # noqa: S603 - repository script, fixed arguments
        ["/bin/sh", str(script)], env=environment, capture_output=True, timeout=30
    )
    assert "--plan-only" in log.read_text()
    log.unlink()
    environment["SOVEREIGN_MIRROR_CONSENT"] = "yes-i-consent-to-public-serving"
    served = subprocess.run(  # noqa: S603 - repository script, fixed arguments
        ["/bin/sh", str(script)], env=environment, capture_output=True, timeout=30
    )
    assert served.returncode == 0, served.stderr
    setup, run = log.read_text().splitlines()
    assert "--yes-i-consent-to-public-serving" in setup and "--run" not in setup
    assert "--bind 127.0.0.1" in setup
    assert run.split() == ["mirror", "run", "--dir", str(tmp_path / "data"), "--resume"]
    # A refused setup is reported and idles: it never starts serving or loops.
    log.unlink()
    stub.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexit 1\n')
    refused = subprocess.run(  # noqa: S603 - repository script, fixed arguments
        ["/bin/sh", str(script)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert refused.returncode == 0 and "Setup refused" in refused.stderr
    (only,) = log.read_text().splitlines()
    assert "mirror quickstart" in only


def test_deploy_files_match_the_installer_and_proxy_model():
    deploy = ROOT / "deploy/mirror"
    unit = (deploy / "model-preserve-mirror@.service").read_text()
    assert "/opt/sovereign" not in unit and "/srv/sovereign" not in unit
    assert "%h/sovereign/bin/model-preserve" in unit and "--resume" in unit
    compose = (deploy / "compose.yaml").read_text()
    assert "network_mode: service:caddy" in compose
    assert "SOVEREIGN_MIRROR_CONSENT" in compose
    # The published, attested release image is the default; a digest can pin it.
    image = "${SOVEREIGN_MIRROR_IMAGE:-ghcr.io/foxlight-foundation/sovereign-mirror:"
    assert image in compose
    workflow = (ROOT / ".github/workflows/mirror-image.yml").read_text()
    assert "attest-build-provenance" in workflow
    assert "push-to-registry: true" in workflow
    caddyfile = (deploy / "Caddyfile").read_text()
    assert "reverse_proxy 127.0.0.1:8080" in caddyfile
    assert not re.search(r"^\s*log\b", caddyfile, re.MULTILINE)
    dockerfile = (deploy / "Dockerfile").read_text()
    assert "USER" in dockerfile and "bittorrent" in dockerfile
    # The optional system unit: a dedicated account, owner-only state, hardening.
    system = (deploy / "sovereign-mirror@.service").read_text()
    directives = {
        line.partition("=")[0]: line.partition("=")[2]
        for line in system.splitlines()
        if "=" in line and not line.startswith("#")
    }
    run = "/opt/sovereign/current/bin/model-preserve mirror run"
    assert directives["ExecStart"] == (
        run + " --dir /var/lib/sovereign/mirrors/%i --resume"
    )
    assert directives["User"] == "sovereign"
    assert directives["StateDirectory"] == "sovereign/mirrors/%i"
    assert directives["StateDirectoryMode"] == "0700"
    assert directives["ReadWritePaths"] == "/var/lib/sovereign/mirrors/%i"
    for key, value in {
        "NoNewPrivileges": "yes",
        "CapabilityBoundingSet": "",
        "UMask": "0077",
        "ProtectSystem": "strict",
        "ProtectHome": "yes",
        "PrivateTmp": "yes",
        "PrivateDevices": "yes",
        "ProtectKernelTunables": "yes",
        "ProtectKernelModules": "yes",
        "ProtectKernelLogs": "yes",
        "ProtectControlGroups": "yes",
        "ProtectClock": "yes",
        "ProtectHostname": "yes",
        "RestrictNamespaces": "yes",
        "RestrictRealtime": "yes",
        "RestrictSUIDSGID": "yes",
        "LockPersonality": "yes",
        "MemoryDenyWriteExecute": "yes",
        "PrivateUsers": "yes",
        "SystemCallArchitectures": "native",
        "Restart": "on-failure",
        "KillSignal": "SIGTERM",
    }.items():
        assert directives[key] == value, key
    assert "SystemCallFilter=@system-service" in system
    families = set(directives["RestrictAddressFamilies"].split())
    assert families <= {"AF_UNIX", "AF_INET", "AF_INET6", "AF_NETLINK"}
    assert "%h" not in system and "WantedBy=multi-user.target" in system
    # Its setup script refuses unauthenticated archives and is opt-in.
    setup = (deploy / "install-system-service.sh").read_text()
    assert "gh attestation verify" in setup and "--deny-self-hosted-runners" in setup
    assert "refusing an unverified archive" in setup
    assert "curl" not in setup and "runuser -u sovereign" in setup
    # Every refusal, the review and consent come before the first change.
    first_change = setup.index("# --- Changes")
    for guard in ("refusing an unverified archive", "--plan-only", "Plan only:"):
        assert setup.index(guard) < first_change, guard
    for change in ("useradd --system", 'systemctl stop "', "mv -Tf", "install -o root"):
        assert setup.index(change) > first_change, change


def test_no_anonymity_claims_in_mirror_text():
    paths = [
        ROOT / "docs/persistent-mirrors.md",
        *sorted((ROOT / "docs/mirror-hosting").glob("*.md")),
        *sorted((ROOT / "deploy/mirror").iterdir()),
        ROOT / "src/foxlight_model_preservation/mirror_quickstart.py",
        ROOT / "src/foxlight_model_preservation/mirror_announce.py",
    ]
    for path in paths:
        text = path.read_text().lower()
        for match in re.finditer(r"anonym\w*", text):
            context = text[max(0, match.start() - 24) : match.start()]
            receipt = text[match.end() :].startswith('": false')
            assert receipt or re.search(r"\b(not|no|never)\b", context), (
                path,
                context,
            )


def test_owner_only_paths_are_kept(tmp_path, capture):
    ledger, _ = mirror(tmp_path, capture)
    configure_mirror(
        ledger, {"announce": AnnouncePolicy(public_url="https://m.example")}
    )
    Announcer(ledger).publish(frozenset())
    assert not os.stat(ledger.path / "peers.json").st_mode & 0o077


# Withdrawal advisories with intake, quickstart and all-or-nothing config ---------


def test_withheld_or_blocked_copy_stays_unserved_whatever_the_advisory(
    tmp_path, capture
):
    from foxlight_model_preservation.mirror import configure_intake, withhold
    from foxlight_model_preservation.mirror_models import IntakePolicy
    from foxlight_model_preservation.mirror_native import NativeMirrors

    ledger, publisher = mirror(tmp_path, capture)
    cycle(ledger)
    configure_intake(
        ledger, IntakePolicy(share="chosen", licenses=("test-only",), definition=1)
    )
    withhold(ledger, capture.identity, withheld=True)
    publisher.publish(version=2, manifest="b" * 64)
    cycle(ledger)
    model_action(ledger, capture.identity, "acknowledge")
    native = NativeMirrors(ledger, lambda _state: True)
    for following in (False, True):
        configure_mirror(ledger, {"follow_withdrawals": following})
        held = ledger.read().models[capture.identity]
        assert held.blocked and held.withdrawal is not None
        assert held.withdrawal.acknowledged
        assert not _served(ledger, capture.identity)
        assert not native._allowed(ledger.read(), capture.identity)
    # Releasing it (the owner's choice) serves the acknowledged copy again.
    withhold(ledger, capture.identity, withheld=False)
    assert _served(ledger, capture.identity)
    assert native._allowed(ledger.read(), capture.identity)


def test_quickstart_reviews_preflights_and_keeps_follow_withdrawals(tmp_path, capture):
    path = collection(tmp_path, capture)
    loaded, document = load_collection(path)
    text, summary = review(loaded, document, settings())
    assert "withdrawal is advisory" in text and summary["follow_withdrawals"] is False
    text, summary = review(loaded, document, settings(follow_withdrawals=True))
    assert "withdrawal is followed (your choice)" in text
    assert summary["follow_withdrawals"] is True
    target = tmp_path / "quick"
    apply_quickstart(target, loaded, document, settings(), consented=True)
    assert not MirrorLedger(target).read().follow_withdrawals
    # A refused quickstart (disk below what is retained) writes nothing,
    # including the withdrawal choice.
    other, other_document = load_collection(collection(tmp_path, capture))
    before = (target / "mirror.json").read_bytes()
    tight = settings(disk_bytes=HEADROOM + 1, max_bytes=1024, follow_withdrawals=True)
    with pytest.raises(PreservationError, match="disk admission"):
        apply_quickstart(target, other, other_document, tight, consented=True)
    assert (target / "mirror.json").read_bytes() == before
    # Choosing it is reviewed policy: the grant is renewed for the new policy.
    _ledger, receipt = apply_quickstart(
        target, loaded, document, settings(follow_withdrawals=True), consented=True
    )
    state = MirrorLedger(target).read()
    assert receipt["policy_changed"] and state.follow_withdrawals
    assert restart_allowed(state)
    # The CLI keeps an existing mirror's choice unless one is given.
    base = (
        "mirror",
        "quickstart",
        "--dir",
        str(target),
        "--collection",
        str(path),
        "--name",
        "Quick",
        "--disk-bytes",
        "2G",
        "--ingress-daily",
        "1G",
        "--egress-daily",
        "1G",
        "--egress-monthly",
        "8G",
    )
    kept = cli(*base, "--plan-only")
    assert kept.returncode == 0, kept.stderr
    assert json.loads(kept.stdout)["review"]["follow_withdrawals"] is True
    off = cli(*base, "--follow-withdrawals", "off", "--yes-i-consent-to-public-serving")
    assert off.returncode == 0, off.stderr
    assert not MirrorLedger(target).read().follow_withdrawals


def test_config_validates_follow_withdrawals_with_everything_else(tmp_path, capture):
    ledger, _ = mirror(tmp_path, capture)
    before = (ledger.path / "mirror.json").read_bytes()
    refused = cli(
        "mirror",
        "config",
        "--dir",
        str(ledger.path),
        "--follow-withdrawals",
        "on",
        "--serve-start",
        "25",
    )
    assert refused.returncode != 0
    assert (ledger.path / "mirror.json").read_bytes() == before
    assert not ledger.read().follow_withdrawals
