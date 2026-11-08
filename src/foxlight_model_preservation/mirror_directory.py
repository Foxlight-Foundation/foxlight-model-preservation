"""A client's own record of mirrors: the network is the list.

The directory is filled from any bootstrap (a handoff's mirrors, a mirror's
peers document, an optional untrusted seed list, and only on explicit request a
DHT or Nostr lookup) and survives anything that happens to any one
organization. Choosing from untrusted lists is safe because each mirror is
sampled at random, every byte is verified, and a local score remembers which
mirrors actually delivered correct bytes. A fake or dead mirror is dropped after
a few failures; a flood of them costs bounded attempts.

Receiving uses the directory through :func:`plan_sources`,
:func:`plan_fallback` and :func:`fetch_with_mirrors`: discovered mirrors are
appended after the explicit or signed hints, filtered by the receiver's own
policy, and scored afterwards. Starter (seed) mirrors are a fallback stage,
contacted only after those sources failed. Receiving never announces, listens,
seeds or publishes, and by default never contacts the DHT or Nostr relays.
"""

from __future__ import annotations

import fcntl
import http.client
import ipaddress
import math
import os
import random
import threading
import time
import uuid
from collections.abc import Callable, Generator, Iterable
from contextlib import contextmanager
from dataclasses import dataclass, replace
from importlib.resources import files
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, JsonValue, field_validator

from .archives import ArchiveManifest
from .ledger import TransferBudget, receipt_path
from .mirror_cards import (
    MAX_PEERS,
    MAX_PEERS_BYTES,
    PEERS_PATH,
    MirrorCard,
    base_url,
    read_peers,
)
from .mirror_cards import verify_card as _verify_card
from .models import (
    MAX_METADATA_BYTES,
    Digest,
    PreservationError,
    Record,
    TrustProfile,
)
from .operations import (
    DeadlineExceeded,
    OperationControl,
    OperationError,
    RequestChanged,
)
from .signing import (
    Envelope,
    canonical_bytes,
    decode_base64,
    digest,
    encode_record,
    strict_json,
)
from .storage import (
    atomic_bytes,
    directory,
    manifest_path,
    open_file,
    parent_directory,
    read_metadata,
)
from .transfer import (
    DEFAULT_POLICY,
    I2PTransferPolicy,
    SourcesExhausted,
    TransferPolicy,
    fetch,
    inspect_envelope,
    response,
)

MAX_MIRRORS = 2048
DROP_AFTER = 3
MAX_CARD_BYTES = 64 * 1024
# Comfortably below the 8 MiB metadata reader limit, so a full directory can
# always be read back.
MAX_DIRECTORY_BYTES = 6 * 1024 * 1024
# A receive adds at most this many discovered sources after its hints.
DISCOVERED_LIMIT = 8
# At most this many not-yet-scored seeds are offered to one receive.
SEED_TRIES = 4
MAX_SEEDS = 256
MAX_SEED_SCORES = 1024
MAX_OPERATIONS = 256
# The private record of receives that fell back to starter mirrors.
MAX_FALLBACKS = 64
# Learning from hint and seed mirrors before a receive is short and bounded.
RECEIVE_CRAWL_SECONDS = 20.0
RECEIVE_CRAWL_TIMEOUT = 5.0
DIRECTORY_ENV = "SOVEREIGN_MIRROR_DIRECTORY"
SEEDS_ENV = "SOVEREIGN_SEED_MIRRORS"
SEED_RESOURCE = "resources/seed-mirrors.json"

_LOCKS: dict[Path, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class SeenMirror(Record):
    """One mirror card and this client's own experience with that mirror."""

    card: Envelope
    successes: int = Field(default=0, ge=0)
    failures: int = Field(default=0, ge=0)
    checked: bool = False
    last_seen: int = Field(ge=0)


class DirectoryState(Record):
    """Owner-private, bounded and never shared as-is with anyone."""

    schema_version: Literal["sovereign.mirror-directory.v1"] = (
        "sovereign.mirror-directory.v1"
    )
    mirrors: dict[str, SeenMirror] = Field(default_factory=dict, max_length=MAX_MIRRORS)


class SeedList(Record):
    """An untrusted bootstrap list of mirror base URLs; it grants nothing.

    A seed is only a place to ask first. Its bytes are verified like any other
    mirror's, and once it publishes a card the card speaks for it.
    """

    schema_version: Literal["sovereign.seed-mirrors.v1"] = "sovereign.seed-mirrors.v1"
    mirrors: tuple[str, ...] = Field(default=(), max_length=MAX_SEEDS)

    @field_validator("mirrors")
    @classmethod
    def plain_mirrors(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Accept only credential-free HTTP(S) base URLs, once each."""
        return tuple(dict.fromkeys(base_url(value) for value in values))


class SeedScore(Record):
    """What one seed address delivered to this client, before any card exists."""

    successes: int = Field(default=0, ge=0)
    failures: int = Field(default=0, ge=0)


class SeedState(Record):
    """Owner-private outcomes for seed addresses; a scored seed is no longer new."""

    schema_version: Literal["sovereign.seed-scores.v1"] = "sovereign.seed-scores.v1"
    seeds: dict[str, SeedScore] = Field(
        default_factory=dict, max_length=MAX_SEED_SCORES
    )


class RememberedSources(Record):
    """The discovered sources chosen for one resumable receive."""

    key: Digest
    sources: tuple[str, ...] = Field(max_length=64)


class RememberedFallback(Record):
    """The starter-mirror stage of one resumable receive, fixed when first used.

    ``sources`` follow the primary stage's sources, ``seeds`` are those of
    them scored as seed addresses, and ``asked`` are the starter mirrors this
    stage contacted or offered.
    """

    key: Digest
    sources: tuple[str, ...] = Field(max_length=64)
    seeds: tuple[str, ...] = Field(max_length=64)
    asked: tuple[str, ...] = Field(max_length=SEED_TRIES)


class StarterFallback(Record):
    """One receive whose own sources failed, so it asked starter mirrors."""

    at: int = Field(ge=0)
    manifest_id: Digest
    asked: tuple[str, ...] = Field(max_length=SEED_TRIES)


class FallbackLog(Record):
    """Owner-private, bounded record of when starter mirrors were contacted."""

    schema_version: Literal["sovereign.starter-fallbacks.v1"] = (
        "sovereign.starter-fallbacks.v1"
    )
    entries: tuple[StarterFallback, ...] = Field(default=(), max_length=MAX_FALLBACKS)


class OperationState(Record):
    """Keeps a resumed operation's source list, so its allowance still binds."""

    schema_version: Literal["sovereign.mirror-operations.v1"] = (
        "sovereign.mirror-operations.v1"
    )
    entries: tuple[RememberedSources, ...] = Field(
        default=(), max_length=MAX_OPERATIONS
    )


def default_directory() -> Path:
    """Return the CLI's private directory path (``SOVEREIGN_MIRROR_DIRECTORY``)."""
    configured = os.environ.get(DIRECTORY_ENV)
    if configured:
        return Path(configured)
    return Path.home() / ".local/state/foxlight-preservation/mirror-directory"


def read_seed_list(data: bytes) -> tuple[str, ...]:
    """Parse a bounded ``sovereign.seed-mirrors.v1`` document."""
    if len(data) > MAX_METADATA_BYTES:
        raise PreservationError("seed list exceeds size limit")
    return SeedList.model_validate_json(canonical_bytes(strict_json(data))).mirrors


def load_seeds(path: Path | None = None, *, packaged: bool = True) -> tuple[str, ...]:
    """Return the shipped seed list plus an optional user-supplied one.

    Both are untrusted. A user file replaces nothing: its entries are added
    after the packaged ones. ``SOVEREIGN_SEED_MIRRORS`` names a default file.
    """
    seeds: list[str] = []
    if packaged:
        seeds.extend(
            read_seed_list(
                files("foxlight_model_preservation")
                .joinpath(SEED_RESOURCE)
                .read_bytes()
            )
        )
    selected = path
    if selected is None and os.environ.get(SEEDS_ENV):
        selected = Path(os.environ[SEEDS_ENV])
    if selected is not None:
        with selected.open("rb") as stream:
            seeds.extend(read_seed_list(stream.read(MAX_METADATA_BYTES + 1)))
    return tuple(dict.fromkeys(seeds))[:MAX_SEEDS]


_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")


def permitted(address: str, policy: TransferPolicy) -> bool:
    """Report whether a discovered address is allowed by the receiver's policy.

    HTTPS only unless the policy allows HTTP; no loopback, private or
    link-local literal or local-only name unless it allows private routes;
    never a private-network name. A DNS name is pinned again at connect time,
    where a private resolution is refused under the same policy.
    """
    if isinstance(policy, I2PTransferPolicy):
        return False  # Discovered direct routes never ride an I2P grant.
    try:
        parsed = urlsplit(base_url(address))
    except PreservationError:
        return False
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host or host.endswith((".i2p", ".i2p.alt", ".onion")):
        return False
    if parsed.scheme != "https" and not policy.allow_http:
        return False
    if policy.allow_private:
        return True
    if host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True


def _band(seen: SeenMirror) -> int:
    """Coarse reliability: equal within a band, so order inside it is random."""
    return math.floor(math.log2((seen.successes + 1) / (seen.failures + 1)))


def _card(envelope: Envelope) -> MirrorCard | None:
    """Read a card that was authenticated when admitted, without freshness."""
    try:
        return MirrorCard.model_validate_json(
            canonical_bytes(strict_json(decode_base64(envelope.payload)))
        )
    except (ValueError, PreservationError):
        return None


def _live(seen: SeenMirror, instant: int) -> MirrorCard | None:
    try:
        return _verify_card(encode_record(seen.card), now=instant)
    except (ValueError, PreservationError):
        return None


def _dropped(seen: SeenMirror) -> bool:
    return seen.failures >= DROP_AFTER and seen.failures > seen.successes


def _bounded_read(
    base: str,
    relative: str,
    policy: TransferPolicy,
    maximum: int,
    deadline: float,
    control: OperationControl,
) -> bytes:
    """Read a whole small document in chunks under an absolute deadline."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DeadlineExceeded("discovery deadline exceeded")
    bounded = TransferPolicy(
        allow_http=policy.allow_http,
        allow_private=policy.allow_private,
        timeout=min(policy.timeout, remaining),
    )
    with response(base, relative, bounded) as received:
        if received.status != 200:
            raise PreservationError("partial document is not accepted")
        data = b""
        while len(data) <= maximum:
            control.checkpoint()
            if time.monotonic() > deadline:
                raise DeadlineExceeded("discovery deadline exceeded")
            chunk = received.read1(min(65536, maximum + 1 - len(data)))
            if not chunk:
                break
            data += chunk
    if len(data) > maximum:
        raise PreservationError("document exceeds size limit")
    return data


class _NoCard:
    addresses: tuple[str, ...] = ()


_NO_CARD = _NoCard()


class MirrorDirectory:
    """Remember, sample and score mirrors without trusting any of them.

    Every change is one read-modify-write under a process and file lock, so
    concurrent discovery, downloads and scoring never lose each other's updates.
    """

    def __init__(self, path: Path, *, rng: random.Random | None = None) -> None:
        self.path = path
        self._rng = rng or random.SystemRandom()
        key = path.absolute()
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(key, threading.Lock())

    def read(self) -> DirectoryState:
        """Read the cached directory; an absent file is an empty directory."""
        try:
            with directory(self.path) as root:
                return DirectoryState.model_validate_json(
                    read_metadata(root, "directory.json")
                )
        except FileNotFoundError:
            return DirectoryState()

    @contextmanager
    def _exclusive(self) -> Generator[int]:
        with self._lock, directory(self.path, create=True) as root:
            with open_file(root, ".directory.lock", write=True) as stream:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    yield root
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _update(
        self, change: Callable[[dict[str, SeenMirror]], None], *, now: int
    ) -> None:
        with self._exclusive() as root:
            state = self.read()
            mirrors = dict(state.mirrors)
            change(mirrors)
            state = state.model_copy(update={"mirrors": _fit(mirrors, now)})
            atomic_bytes(root, "directory.json", encode_record(state))

    def ingest(self, cards: Iterable[bytes], *, now: int | None = None) -> int:
        """Keep each verified card, newest per mirror key; return new mirrors."""
        instant = int(time.time()) if now is None else now
        admitted: list[tuple[MirrorCard, Envelope]] = []
        for raw in cards:
            if len(raw) > MAX_CARD_BYTES:
                continue
            try:
                card = _verify_card(raw, now=instant)
            except (ValueError, PreservationError):
                continue
            admitted.append((card, Envelope.model_validate_json(raw)))
        added = 0

        def change(mirrors: dict[str, SeenMirror]) -> None:
            nonlocal added
            for card, envelope in admitted:
                key = card.mirror_key.key_id
                previous = mirrors.get(key)
                if previous is None:
                    if len(mirrors) >= MAX_MIRRORS:
                        _reclaim(mirrors, instant)
                    if len(mirrors) >= MAX_MIRRORS:
                        continue
                    added += 1
                    mirrors[key] = SeenMirror(card=envelope, last_seen=instant)
                    continue
                older = _card(previous.card)
                if older is None or card.issued_at > older.issued_at:
                    # A new announcement must pass its own spot-check before
                    # this client vouches for it again.
                    mirrors[key] = previous.model_copy(
                        update={
                            "card": envelope,
                            "last_seen": instant,
                            "checked": False,
                        }
                    )

        self._update(change, now=instant)
        return added

    def crawl(
        self,
        seeds: tuple[str, ...],
        policy: TransferPolicy = DEFAULT_POLICY,
        *,
        max_documents: int = 32,
        expand: bool = True,
        control: OperationControl | None = None,
    ) -> int:
        """Follow peers documents outward from known mirrors; return new mirrors.

        Each request is bounded and unauthenticated; only verified cards are
        kept. The whole crawl honors the policy's deadline and cancellation.
        Discovery never fetches models and never trusts what it reads. With
        ``expand`` false, only the given seeds are asked, so a caller can learn
        from mirrors a user already approved without contacting others.
        """
        active = control or OperationControl()
        deadline = active.deadline_after(policy.deadline_seconds)
        queue = list(dict.fromkeys(seeds))
        if expand:
            # Known mirrors are visited in random order each round, so a bounded
            # crawl eventually reaches every one, whatever sits at the front.
            known = self._known_addresses()
            self._rng.shuffle(known)
            for item in known:
                if item not in queue:
                    queue.append(item)
        visited: set[str] = set()
        added = 0
        while queue and len(visited) < max_documents:
            active.checkpoint()
            if time.monotonic() > deadline:
                break
            base = queue.pop(0)
            if base in visited:
                continue
            visited.add(base)
            try:
                cards = read_peers(
                    _bounded_read(
                        base, PEERS_PATH, policy, MAX_PEERS_BYTES, deadline, active
                    )
                )
            except DeadlineExceeded:
                break
            except OperationError:
                raise
            except (OSError, ValueError, http.client.HTTPException):
                continue
            added += self.ingest(cards)
            if not expand:
                continue
            for raw in cards:
                for address in _verify_addresses(raw):
                    if address not in visited:
                        queue.append(address)
        return added

    def _known_addresses(self) -> list[str]:
        instant = int(time.time())
        return [
            address
            for seen in self.read().mirrors.values()
            if not _dropped(seen)
            for address in (_live(seen, instant) or _NO_CARD).addresses
        ]

    def candidates(
        self,
        manifest_id: str,
        *,
        limit: int = 8,
        now: int | None = None,
        policy: TransferPolicy | None = None,
    ) -> tuple[str, ...]:
        """Sample live mirrors whose cards claim a manifest, best band first.

        Mirrors are grouped into coarse score bands (successes against
        failures) and ordered at random inside each band, so no single list
        order and no flood of fake cards can decide which mirrors a client
        tries. With a policy, only addresses it permits are offered; each card
        contributes at most two addresses.
        """
        instant = int(time.time()) if now is None else now
        ranked: list[tuple[int, float, str]] = []
        for seen in self.read().mirrors.values():
            if _dropped(seen):
                continue
            card = _live(seen, instant)
            if card is None or not card.covers(manifest_id):
                continue
            addresses = [
                address
                for address in card.addresses
                if policy is None or permitted(address, policy)
            ][:2]
            band = _band(seen)
            order = self._rng.random()
            ranked.extend((band, order, address) for address in addresses)
        ranked.sort(reverse=True)
        return tuple(dict.fromkeys(address for _band, _order, address in ranked))[
            :limit
        ]

    def listing(self, *, now: int | None = None) -> list[dict[str, JsonValue]]:
        """Describe every remembered mirror and this client's score, privately."""
        instant = int(time.time()) if now is None else now
        result: list[dict[str, JsonValue]] = []
        for key, seen in sorted(self.read().mirrors.items()):
            card = _card(seen.card)
            result.append(
                {
                    "mirror_key": key,
                    "addresses": list(card.addresses) if card else [],
                    "holds_about": card.coverage_count if card else 0,
                    "expires_at": card.expires_at if card else 0,
                    "live": _live(seen, instant) is not None,
                    "successes": seen.successes,
                    "failures": seen.failures,
                    "spot_checked": seen.checked,
                    "dropped": _dropped(seen),
                }
            )
        return result

    def export_cards(
        self, *, limit: int = MAX_PEERS, now: int | None = None
    ) -> tuple[bytes, ...]:
        """Return live, undropped cards, most reliable first, without scores.

        Unlike :meth:`gossip`, unchecked cards are included: this is the owner
        moving their own directory, not a mirror vouching for others.
        """
        instant = int(time.time()) if now is None else now
        ranked = sorted(
            (
                (seen.successes - seen.failures, encode_record(seen.card))
                for seen in self.read().mirrors.values()
                if not _dropped(seen) and _live(seen, instant) is not None
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        return tuple(raw for _score, raw in ranked[:limit])

    def shuffle(self, items: list[str]) -> None:
        """Shuffle in place with this directory's sampler."""
        self._rng.shuffle(items)

    def card_addresses(self, *, now: int | None = None) -> frozenset[str]:
        """Every address a live card in this directory names."""
        instant = int(time.time()) if now is None else now
        return frozenset(
            address
            for seen in self.read().mirrors.values()
            for address in (_live(seen, instant) or _NO_CARD).addresses
        )

    def read_seeds(self) -> SeedState:
        """Read this client's outcomes for seed addresses."""
        try:
            with directory(self.path) as root:
                return SeedState.model_validate_json(read_metadata(root, "seeds.json"))
        except FileNotFoundError:
            return SeedState()

    def unscored_seeds(self, seeds: Iterable[str]) -> tuple[str, ...]:
        """Seeds this client has never received anything from, in given order."""
        scored = self.read_seeds().seeds
        return tuple(seed for seed in dict.fromkeys(seeds) if seed not in scored)

    def remembered(self, key: str) -> tuple[str, ...] | None:
        """Return the sources chosen earlier for one resumable receive."""
        try:
            with directory(self.path) as root:
                state = OperationState.model_validate_json(
                    read_metadata(root, "operations.json")
                )
        except FileNotFoundError:
            return None
        for entry in state.entries:
            if entry.key == key:
                return entry.sources
        return None

    def remember(self, key: str, sources: tuple[str, ...]) -> None:
        """Keep one receive's chosen sources so a resume binds the same request."""
        with self._exclusive() as root:
            try:
                state = OperationState.model_validate_json(
                    read_metadata(root, "operations.json")
                )
            except FileNotFoundError:
                state = OperationState()
            entries = [entry for entry in state.entries if entry.key != key]
            entries.append(RememberedSources(key=key, sources=sources))
            state = OperationState(entries=tuple(entries[-MAX_OPERATIONS:]))
            atomic_bytes(root, "operations.json", encode_record(state))

    def read_fallbacks(self) -> FallbackLog:
        """Read the private record of receives that fell back to starter mirrors."""
        try:
            with directory(self.path) as root:
                return FallbackLog.model_validate_json(
                    read_metadata(root, "fallbacks.json")
                )
        except FileNotFoundError:
            return FallbackLog()

    def note_fallback(
        self, manifest_id: str, asked: tuple[str, ...], *, now: int | None = None
    ) -> None:
        """Record that a receive fell back to starter mirrors, keeping the latest."""
        entry = StarterFallback(
            at=int(time.time()) if now is None else now,
            manifest_id=manifest_id,
            asked=asked,
        )
        with self._exclusive() as root:
            try:
                entries = self.read_fallbacks().entries
            except (OSError, ValueError):
                entries = ()  # A damaged record is replaced, never blocking.
            state = FallbackLog(entries=(*entries, entry)[-MAX_FALLBACKS:])
            atomic_bytes(root, "fallbacks.json", encode_record(state))

    def record(self, address: str, *, delivered: bool) -> None:
        """Remember whether a mirror delivered correct bytes."""
        self.record_many({address: delivered})

    def record_many(
        self, outcomes: dict[str, bool], *, seeds: Iterable[str] = ()
    ) -> None:
        """Remember several delivery outcomes in one locked update.

        Outcomes score every card naming the address. Addresses listed in
        ``seeds`` are also scored as seeds, so they are no longer offered as new.
        """
        if not outcomes:
            return
        seed_outcomes = {
            address: outcomes[address] for address in seeds if address in outcomes
        }

        def change(mirrors: dict[str, SeenMirror]) -> None:
            for key, seen in list(mirrors.items()):
                card = _card(seen.card)
                if card is None:
                    continue
                for address in card.addresses:
                    if address in outcomes:
                        ok = outcomes[address]
                        seen = seen.model_copy(
                            update={
                                "successes": seen.successes + ok,
                                "failures": seen.failures + (not ok),
                            }
                        )
                mirrors[key] = seen

        self._update(change, now=int(time.time()))
        if seed_outcomes:
            with self._exclusive() as root:
                scores = dict(self.read_seeds().seeds)
                for address, ok in seed_outcomes.items():
                    previous = scores.get(address)
                    if previous is None and len(scores) >= MAX_SEED_SCORES:
                        continue
                    previous = previous or SeedScore()
                    scores[address] = SeedScore(
                        successes=previous.successes + ok,
                        failures=previous.failures + (not ok),
                    )
                atomic_bytes(root, "seeds.json", encode_record(SeedState(seeds=scores)))

    def spot_check(
        self,
        known: Iterable[str],
        policy: TransferPolicy = DEFAULT_POLICY,
        *,
        control: OperationControl | None = None,
        max_checks: int | None = None,
    ) -> int:
        """Confirm mirrors really serve bytes they claim before vouching for them.

        For each unchecked mirror, fetch one manifest it claims from those this
        checker knows and confirm its signed payload hashes to that identity.
        No trust is needed for this; it proves only that the bytes are there.
        Results are merged into the current directory, and only for the exact
        card that was checked. Returns the number of mirrors confirmed.
        ``max_checks`` optionally bounds how many mirrors one call contacts, so
        a caller can bound its ingress (each check reads at most two manifests).
        """
        active = control or OperationControl()
        deadline = active.deadline_after(policy.deadline_seconds)
        identities = list(dict.fromkeys(known))
        results: dict[str, tuple[Envelope, bool]] = {}
        # Dropped mirrors are never retried, and the rest are checked in random
        # order, so unresponsive entries cannot starve newly learned mirrors.
        entries = list(self.read().mirrors.items())
        self._rng.shuffle(entries)
        for key, seen in entries:
            active.checkpoint()
            if max_checks is not None and len(results) >= max_checks:
                break
            if seen.checked or _dropped(seen) or time.monotonic() > deadline:
                continue
            card = _live(seen, int(time.time()))
            if card is None:
                continue
            claimed = [item for item in identities if card.covers(item)]
            if not claimed:
                continue
            identity = self._rng.choice(claimed)
            ok = False
            for address in card.addresses[:2]:
                try:
                    data = _bounded_read(
                        address,
                        manifest_path(identity),
                        policy,
                        MAX_METADATA_BYTES,
                        deadline,
                        active,
                    )
                    envelope = Envelope.model_validate_json(
                        canonical_bytes(strict_json(data))
                    )
                    ok = digest(decode_base64(envelope.payload)) == identity
                except DeadlineExceeded:
                    break
                except OperationError:
                    raise
                except (OSError, ValueError, http.client.HTTPException):
                    ok = False
                if ok:
                    break
            results[key] = (seen.card, ok)

        def change(mirrors: dict[str, SeenMirror]) -> None:
            for key, (checked_card, ok) in results.items():
                current = mirrors.get(key)
                if current is None or current.card != checked_card:
                    continue  # Replaced meanwhile: the new card needs its own check.
                mirrors[key] = current.model_copy(
                    update={
                        "checked": ok,
                        "failures": current.failures if ok else current.failures + 1,
                    }
                )

        if results:
            self._update(change, now=int(time.time()))
        return sum(ok for _card, ok in results.values())

    def gossip(
        self, *, limit: int = MAX_PEERS, now: int | None = None
    ) -> tuple[bytes, ...]:
        """Return checked, live cards worth vouching for, most reliable first."""
        instant = int(time.time()) if now is None else now
        ranked: list[tuple[int, bytes]] = []
        for seen in self.read().mirrors.values():
            if not seen.checked or _dropped(seen) or _live(seen, instant) is None:
                continue
            ranked.append((seen.successes - seen.failures, encode_record(seen.card)))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return tuple(raw for _score, raw in ranked[:limit])


def _reclaim(mirrors: dict[str, SeenMirror], instant: int) -> None:
    """Forget expired and dropped mirrors so turnover never blocks learning."""
    for key in [
        key
        for key, seen in mirrors.items()
        if _dropped(seen) or _live(seen, instant) is None
    ]:
        del mirrors[key]


def _fit(mirrors: dict[str, SeenMirror], instant: int) -> dict[str, SeenMirror]:
    """Keep the serialized directory within its byte budget.

    The least useful entries go first: expired or dropped mirrors, then the
    least reliable and least recently seen.
    """
    if len(encode_record(DirectoryState(mirrors=mirrors))) <= MAX_DIRECTORY_BYTES:
        return mirrors
    _reclaim(mirrors, instant)
    order = sorted(
        mirrors,
        key=lambda key: (
            mirrors[key].successes - mirrors[key].failures,
            mirrors[key].last_seen,
        ),
    )
    while order and (
        len(encode_record(DirectoryState(mirrors=mirrors))) > MAX_DIRECTORY_BYTES
    ):
        del mirrors[order.pop(0)]
    return mirrors


def _verify_addresses(raw: bytes) -> tuple[str, ...]:
    try:
        return _verify_card(raw).addresses
    except (ValueError, PreservationError):
        return ()


def fetch_discovered(
    manifest_id: str,
    trust: TrustProfile,
    destination: Path,
    mirrors: MirrorDirectory,
    policy: TransferPolicy = DEFAULT_POLICY,
    *,
    extra_sources: tuple[str, ...] = (),
    control: OperationControl | None = None,
) -> ArchiveManifest:
    """Fetch an exact model from whichever discovered mirrors can deliver it.

    Candidates are checked one at a time for the authenticated manifest before
    any weights move. Each mirror is then scored by what it actually delivered:
    a mirror that served a corrupt or missing object is scored down even when
    another mirror completed the download. Integrity never depends on any of them.
    """
    active = control or OperationControl()
    candidates = tuple(
        dict.fromkeys(
            (
                *extra_sources,
                *mirrors.candidates(
                    manifest_id, limit=policy.max_sources, policy=policy
                ),
            )
        )
    )[: policy.max_sources]
    if not candidates:
        raise PreservationError("no known mirror lists this model yet")
    # One absolute deadline covers every probe and the download itself.
    deadline = active.deadline_after(policy.deadline_seconds)

    def remaining() -> TransferPolicy:
        left = deadline - time.monotonic()
        if left <= 0:
            raise DeadlineExceeded("discovered download deadline exceeded")
        return replace(policy, deadline_seconds=left, timeout=min(policy.timeout, left))

    working: list[str] = []
    outcomes: dict[str, bool] = {}
    for source in candidates:
        active.checkpoint()
        try:
            inspect_envelope(
                manifest_id, trust, (source,), remaining(), control=active.fork()
            )
        except OperationError:
            raise
        except (OSError, ValueError, http.client.HTTPException):
            outcomes[source] = False
            continue
        working.append(source)
    if not working:
        mirrors.record_many(outcomes)
        raise PreservationError("no discovered mirror served this exact model")
    delivered: dict[str, bool] = {}

    def observe(source: str, ok: bool) -> None:
        # Any failed object marks the mirror down; only clean delivery counts.
        delivered[source] = delivered.get(source, True) and ok

    try:
        # The ordinary path keeps signature merging: different mirrors may
        # serve differently signed wrappers of the same exact payload.
        return fetch(
            manifest_id,
            trust,
            tuple(working),
            destination,
            remaining(),
            control=active.fork(),
            source_outcome=observe,
        )
    finally:
        mirrors.record_many({**outcomes, **delivered})


@dataclass(frozen=True)
class SourcePlan:
    """Hints first, then bounded discovered sources; none of them is trusted.

    ``discovered`` lists what was appended after the hints, and ``seeds`` the
    subset of those that came from a seed list rather than a mirror card.
    ``starters`` are the starter (seed) mirrors a fallback stage contacted or
    offered; a primary stage has none.
    """

    sources: tuple[str, ...]
    discovered: tuple[str, ...] = ()
    seeds: tuple[str, ...] = ()
    starters: tuple[str, ...] = ()


def _operation_key(operation_id: str, manifest_id: str, hints: tuple[str, ...]) -> str:
    return digest(
        canonical_bytes(
            {"operation": operation_id, "manifest": manifest_id, "hints": list(hints)}
        )
    )


def _fallback_key(key: str) -> str:
    """Name an operation's fallback stage in the directory's bounded history."""
    return digest(canonical_bytes({"fallback": key}))


def _sources_path(operation_id: str) -> str:
    """Keep a receive's chosen sources next to its transfer receipt."""
    return receipt_path(operation_id).removesuffix(".json") + ".sources.json"


def _fallback_path(operation_id: str) -> str:
    """Keep a receive's starter-mirror stage next to its primary plan."""
    return receipt_path(operation_id).removesuffix(".json") + ".fallback.json"


def _read_plan(destination: Path, operation_id: str) -> RememberedSources | None:
    try:
        with directory(destination) as root:
            return RememberedSources.model_validate_json(
                read_metadata(root, _sources_path(operation_id))
            )
    except FileNotFoundError:
        return None


def _read_fallback(destination: Path, operation_id: str) -> RememberedFallback | None:
    try:
        with directory(destination) as root:
            return RememberedFallback.model_validate_json(
                read_metadata(root, _fallback_path(operation_id))
            )
    except FileNotFoundError:
        return None


def _bound(stored: RememberedSources, key: str) -> tuple[str, ...]:
    if stored.key != key:
        raise RequestChanged("operation ID belongs to different transfer inputs")
    return stored.sources


def _claim(destination: Path, path: str, data: bytes) -> bytes:
    """Durably create one record only if none exists; return whichever won.

    The record is written to a private temporary file, fsynced and then hard
    linked into place, which fails rather than replaces when another attempt
    got there first. A record is therefore never overwritten, never partial,
    and two concurrent first attempts end up using the same one.
    """
    with directory(destination, create=True) as root:
        with parent_directory(root, path, create=True) as (parent, name):
            temporary = ".pending-" + uuid.uuid4().hex
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
                except FileExistsError:
                    pass
                os.fsync(parent)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
        return read_metadata(root, path)


def _claim_plan(
    destination: Path, operation_id: str, key: str, sources: tuple[str, ...]
) -> tuple[str, ...]:
    """Create the primary plan once beside the receipt; return the plan that won."""
    stored = RememberedSources.model_validate_json(
        _claim(
            destination,
            _sources_path(operation_id),
            encode_record(RememberedSources(key=key, sources=sources)),
        )
    )
    return _bound(stored, key)


def _claim_fallback(
    destination: Path, operation_id: str, record: RememberedFallback
) -> RememberedFallback:
    """Create the fallback stage once beside the plan; return the stage that won."""
    stored = RememberedFallback.model_validate_json(
        _claim(destination, _fallback_path(operation_id), encode_record(record))
    )
    if stored.key != record.key:
        raise RequestChanged("operation ID belongs to different transfer inputs")
    return stored


def _plain(value: str) -> bool:
    try:
        base_url(value)
    except PreservationError:
        return False
    return True


def _learn(
    mirrors: MirrorDirectory,
    targets: tuple[str, ...],
    policy: TransferPolicy,
    control: OperationControl | None,
) -> None:
    """Read only these hosts' peers documents, briefly; learning is optional."""
    if not targets or isinstance(policy, I2PTransferPolicy):
        return
    bounded = replace(
        policy,
        timeout=min(policy.timeout, RECEIVE_CRAWL_TIMEOUT),
        deadline_seconds=min(policy.deadline_seconds, RECEIVE_CRAWL_SECONDS),
    )
    try:
        mirrors.crawl(
            targets,
            bounded,
            max_documents=len(targets),
            expand=False,
            control=control,
        )
    except OperationError:
        raise
    except (OSError, ValueError, http.client.HTTPException):
        pass  # The receive proceeds with what it already has.


def plan_sources(
    manifest_id: str,
    hints: tuple[str, ...],
    mirrors: MirrorDirectory,
    policy: TransferPolicy = DEFAULT_POLICY,
    *,
    seeds: tuple[str, ...] = (),
    limit: int = DISCOVERED_LIMIT,
    learn: bool = True,
    operation_id: str | None = None,
    destination: Path | None = None,
    control: OperationControl | None = None,
) -> SourcePlan:
    """Return the primary stage: the hints, then bounded known mirrors.

    The extras are mirrors whose card Bloom filter claims the manifest, random
    within score bands. Every extra must pass the receiver's own policy
    (:func:`permitted`), at most ``limit`` are added, and hints plus extras
    never exceed ``policy.max_sources``. Hints are kept exactly as given, in
    front. Starter (seed) mirrors are never contacted or offered here; they
    form :func:`plan_fallback`'s stage. ``seeds`` only marks seed entries in a
    plan stored by an earlier release, which offered them up front.

    With ``learn``, the peers documents of the remote hints are read first (no
    outward expansion, short deadline), so mirrors those already-used hosts
    vouch for can be offered. Nothing else is contacted: no seed, no DHT, no
    relay, no announcement. A resumed ``operation_id`` reuses the sources
    chosen the first time, so its allowance still binds. With a
    ``destination``, that choice is created once, durably, beside the
    operation's transfer receipt and never replaced, so it lasts exactly as
    long as the receipt; reusing the operation ID with other hints or another
    manifest raises :class:`RequestChanged` before anything is written.
    Without one, a bounded recent history in the directory keeps it.
    Discovered sources never affect which keys are trusted.
    """
    hints = tuple(dict.fromkeys(hints))
    room = max(0, min(limit, policy.max_sources - len(hints)))
    seed_set = frozenset(seeds)

    def result(extras: tuple[str, ...]) -> SourcePlan:
        extras = tuple(item for item in extras if item not in hints)[:room]
        return SourcePlan(
            hints + extras, extras, tuple(item for item in extras if item in seed_set)
        )

    key = (
        _operation_key(operation_id, manifest_id, hints)
        if operation_id is not None
        else None
    )
    if key is not None and operation_id is not None:
        if destination is not None:
            # A plan beside the receipt is authoritative and never replaced:
            # other inputs under the same operation ID are refused at once.
            stored = _read_plan(destination, operation_id)
            if stored is not None:
                return result(_bound(stored, key))
        else:
            try:
                earlier = mirrors.remembered(key)
            except (OSError, ValueError):
                earlier = None
            if earlier is not None:
                return result(earlier)
    extras = (
        _choose_primary(
            manifest_id,
            hints,
            mirrors,
            policy,
            room=room,
            learn=learn,
            control=control,
        )
        if room > 0
        else ()
    )
    if key is not None and operation_id is not None:
        if destination is not None:
            extras = _claim_plan(destination, operation_id, key, extras)
        else:
            try:
                mirrors.remember(key, extras)
            except (OSError, ValueError):
                pass
    return result(extras)


def _choose_primary(
    manifest_id: str,
    hints: tuple[str, ...],
    mirrors: MirrorDirectory,
    policy: TransferPolicy,
    *,
    room: int,
    learn: bool,
    control: OperationControl | None,
) -> tuple[str, ...]:
    """Sample known mirrors; a damaged directory yields none, not an error."""
    if learn:
        _learn(
            mirrors,
            tuple(dict.fromkeys(h for h in hints if "://" in h and _plain(h))),
            policy,
            control,
        )
    try:
        # Ask for enough that hints already in the directory cannot crowd out
        # usable fallbacks, then drop them before taking ``room``.
        found = mirrors.candidates(manifest_id, limit=room + len(hints), policy=policy)
    except (OSError, ValueError):
        return ()
    return tuple(item for item in found if item not in hints)[:room]


def _staged(
    primary: SourcePlan,
    extras: tuple[str, ...],
    seeds: tuple[str, ...],
    starters: tuple[str, ...],
) -> SourcePlan:
    extras = tuple(
        item for item in dict.fromkeys(extras) if item not in primary.sources
    )
    return SourcePlan(
        primary.sources + extras,
        primary.discovered + extras,
        tuple(dict.fromkeys((*primary.seeds, *(s for s in seeds if s in extras)))),
        starters,
    )


def _fallback_room(primary: SourcePlan, policy: TransferPolicy, limit: int) -> int:
    """Discovered sources across both stages stay within one receive's bounds."""
    return max(
        0,
        min(
            limit - len(primary.discovered),
            policy.max_sources - len(primary.sources),
        ),
    )


def _recorded_fallback(
    primary: SourcePlan,
    mirrors: MirrorDirectory,
    seed_set: frozenset[str],
    key: str | None,
    operation_id: str | None,
    destination: Path | None,
) -> SourcePlan | None:
    """Return a resumed operation's fallback stage, if one was ever fixed."""
    if key is None or operation_id is None:
        return None
    if destination is not None:
        stored = _read_fallback(destination, operation_id)
        if stored is None:
            return None
        if stored.key != key:
            raise RequestChanged("operation ID belongs to different transfer inputs")
        return _staged(primary, stored.sources, stored.seeds, stored.asked)
    try:
        earlier = mirrors.remembered(_fallback_key(key))
    except (OSError, ValueError):
        earlier = None
    if earlier is None:
        return None
    return _staged(primary, earlier, tuple(i for i in earlier if i in seed_set), ())


def plan_fallback(
    manifest_id: str,
    hints: tuple[str, ...],
    primary: SourcePlan,
    mirrors: MirrorDirectory,
    policy: TransferPolicy = DEFAULT_POLICY,
    *,
    seeds: tuple[str, ...] = (),
    limit: int = DISCOVERED_LIMIT,
    learn: bool = True,
    operation_id: str | None = None,
    destination: Path | None = None,
    control: OperationControl | None = None,
) -> SourcePlan:
    """Return the fallback stage, for when the primary stage's sources failed.

    ``primary`` is what :func:`plan_sources` returned for the same inputs. The
    stage is the primary sources again, followed by mirrors that now claim
    the manifest and were not offered before (typically learned from starter
    mirrors), then not-yet-scored seeds that no card speaks for (at most
    ``SEED_TRIES``, sampled at random). With ``learn``, only those starter
    mirrors' peers documents are read first, briefly and without expansion.
    Every extra passes the receiver's policy, discovered sources across both
    stages stay within ``limit``, and all sources within
    ``policy.max_sources``. With no room left or no starter mirror to offer,
    the stage adds nothing and contacts nobody.

    A resumed ``operation_id`` reuses the stage fixed the first time. With a
    ``destination`` it is created once, durably, beside the primary plan
    (``<operation-id>.fallback.json``) and never replaced, so the resume binds
    the same sources; other inputs raise :class:`RequestChanged`. A newly
    planned stage that asks starter mirrors is noted in the directory's
    private fallback record (:meth:`MirrorDirectory.read_fallbacks`).
    """
    hints = tuple(dict.fromkeys(hints))
    seed_set = frozenset(seeds)
    key = (
        _operation_key(operation_id, manifest_id, hints)
        if operation_id is not None
        else None
    )
    recorded = _recorded_fallback(
        primary, mirrors, seed_set, key, operation_id, destination
    )
    if recorded is not None:
        return recorded
    room = _fallback_room(primary, policy, limit)
    extras, asked = (
        _choose_fallback(
            manifest_id,
            primary,
            mirrors,
            policy,
            offered_seeds=seeds,
            room=room,
            learn=learn,
            control=control,
        )
        if room > 0
        else ((), ())
    )
    chosen = tuple(item for item in extras if item in seed_set)
    if key is not None and operation_id is not None:
        if destination is not None:
            record = RememberedFallback(
                key=key, sources=extras, seeds=chosen, asked=asked
            )
            stored = _claim_fallback(destination, operation_id, record)
            # A concurrent first attempt may have fixed the stage first; adopt it.
            extras, chosen = stored.sources, stored.seeds
        else:
            try:
                mirrors.remember(_fallback_key(key), extras)
            except (OSError, ValueError):
                pass
    if asked:
        try:
            mirrors.note_fallback(manifest_id, asked)
        except (OSError, ValueError):
            pass  # The record is informative; it never changes the receive.
    return _staged(primary, extras, chosen, asked)


def _choose_fallback(
    manifest_id: str,
    primary: SourcePlan,
    mirrors: MirrorDirectory,
    policy: TransferPolicy,
    *,
    offered_seeds: tuple[str, ...],
    room: int,
    learn: bool,
    control: OperationControl | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Sample the starter stage; return its extras and the starters it asks."""
    used = frozenset(primary.sources)
    try:
        offered = [
            seed
            for seed in mirrors.unscored_seeds(offered_seeds)
            if seed not in used and permitted(seed, policy)
        ]
    except (OSError, ValueError):
        return (), ()
    if not offered:
        return (), ()
    mirrors.shuffle(offered)
    starters = tuple(offered[:SEED_TRIES])
    if learn:
        _learn(mirrors, starters, policy, control)
    try:
        found = tuple(
            item
            for item in mirrors.candidates(
                manifest_id, limit=room + len(used), policy=policy
            )
            if item not in used
        )
        carded = mirrors.card_addresses()
    except (OSError, ValueError):
        found, carded = (), frozenset[str]()
    fresh = tuple(seed for seed in starters if seed not in carded)
    extras = tuple(dict.fromkeys((*found, *fresh)))[:room]
    asked = starters if learn else tuple(s for s in starters if s in extras)
    return extras, asked


def fetch_with_mirrors(
    manifest_id: str,
    trust: TrustProfile,
    hints: tuple[str, ...],
    destination: Path,
    policy: TransferPolicy = DEFAULT_POLICY,
    *,
    mirrors: MirrorDirectory | None = None,
    seeds: tuple[str, ...] = (),
    control: OperationControl | None = None,
    operation_id: str | None = None,
    envelope: bytes | None = None,
) -> ArchiveManifest:
    """Fetch exact trusted content from hints, known mirrors, then starters.

    Without a directory this is exactly :func:`transfer.fetch`. With one, the
    receive has up to two stages. The primary stage (:func:`plan_sources`) is
    the hints and the mirrors this client already knows. Only when every one
    of those failed for this manifest
    (:class:`~foxlight_model_preservation.transfer.SourcesExhausted`), or there
    were none, does the fallback stage (:func:`plan_fallback`) contact starter
    (seed) mirrors, and then the primary sources are tried again ahead of what
    the starters offered. Cancellation, deadlines, budgets and local failures
    never fall back. One deadline covers both stages.

    With ``operation_id`` the receipt binds the primary stage's sources, and a
    fallback stage, fixed beside the receipt when first used, extends that
    same request: a resume after it goes straight to the recorded fallback
    stage and never trips :class:`RequestChanged`. Without one, both stages
    draw on one in-memory allowance of ``policy.max_bytes``. Each remote
    source's outcome is recorded afterwards, success or failure, even when the
    fetch fails. The
    manifest and every object are still verified against ``trust``, which the
    caller chose independently; no discovered mirror can change it.
    """
    if mirrors is None:
        return fetch(
            manifest_id,
            trust,
            hints,
            destination,
            policy,
            control=control,
            operation_id=operation_id,
            envelope=envelope,
        )
    active = control or OperationControl()
    # A damaged directory yields no discovered sources rather than an error;
    # a plan that cannot be read or kept beside the receipt fails the receive,
    # as the receipt itself would.
    primary = plan_sources(
        manifest_id,
        hints,
        mirrors,
        policy,
        seeds=seeds,
        operation_id=operation_id,
        destination=destination,
        control=active,
    )
    delivered: dict[str, bool] = {}
    discovered = set(primary.discovered)
    scored_seeds = list(primary.seeds)

    def observe(source: str, ok: bool) -> None:
        # Any failed object marks the source down; only clean delivery counts.
        if "://" in source:
            delivered[source] = delivered.get(source, True) and ok

    def refused(source: str) -> None:
        # A discovered mirror or seed that was asked for the manifest and could
        # not supply an authentic one failed. Hints keep today's object-only
        # scoring, and sources never asked are not penalized.
        if source in discovered:
            observe(source, False)

    # A named operation's durable receipt spans both stages; an unnamed
    # receive shares one in-memory allowance, so together they never move
    # more than ``policy.max_bytes``.
    allowance = TransferBudget(policy.max_bytes) if operation_id is None else None

    def attempt(
        plan: SourcePlan, stage: OperationControl, request: tuple[str, ...] | None
    ) -> ArchiveManifest:
        return fetch(
            manifest_id,
            trust,
            plan.sources,
            destination,
            policy,
            control=stage,
            operation_id=operation_id,
            envelope=envelope,
            source_outcome=observe,
            manifest_failure=refused,
            request_sources=request,
            allowance=allowance,
        )

    key = (
        _operation_key(operation_id, manifest_id, tuple(dict.fromkeys(hints)))
        if operation_id is not None
        else None
    )
    try:
        staged = _recorded_fallback(
            primary, mirrors, frozenset(seeds), key, operation_id, destination
        )
        if staged is None:
            exhausted: SourcesExhausted | None = None
            if primary.sources:
                if not seeds or _fallback_room(primary, policy, DISCOVERED_LIMIT) < 1:
                    return attempt(primary, active, None)  # Nothing to fall back to.
                # One deadline covers both stages. The first runs on a
                # provisional child control, so observers never see its failure
                # as terminal when the fallback stage follows under this one.
                active.limit_deadline(time.monotonic() + policy.deadline_seconds)
                first = active.fork(provisional=True)
                try:
                    return attempt(primary, first, None)
                except SourcesExhausted as error:
                    exhausted = error
                    active.resume_after(first)
                except BaseException:
                    first.settle()
                    raise
            try:
                # The fallback gets only what is left of the receive's deadline:
                # none left means no starter mirror is contacted at all.
                left = active.remaining_seconds()
                if left is not None and left <= 0:
                    raise DeadlineExceeded("receive deadline exceeded before fallback")
                staged = plan_fallback(
                    manifest_id,
                    hints,
                    primary,
                    mirrors,
                    policy
                    if left is None
                    else replace(
                        policy,
                        deadline_seconds=left,
                        timeout=min(policy.timeout, left),
                    ),
                    seeds=seeds,
                    operation_id=operation_id,
                    destination=destination,
                    control=active,
                )
                if exhausted is not None and staged.sources == primary.sources:
                    raise exhausted
            except BaseException as error:
                if exhausted is not None:
                    active.conclude(error)  # The receive's one terminal outcome.
                raise
        discovered.update(staged.discovered)
        scored_seeds.extend(staged.seeds)
        return attempt(staged, active, primary.sources)
    finally:
        try:
            mirrors.record_many(delivered, seeds=tuple(dict.fromkeys(scored_seeds)))
        except (OSError, ValueError):
            pass  # Scoring is advisory; it never changes the receive's result.


def look_further(
    mirrors: MirrorDirectory,
    policy: TransferPolicy = DEFAULT_POLICY,
    *,
    relays: tuple[str, ...] | None = None,
    dht_seconds: float = 20.0,
    control: OperationControl | None = None,
) -> dict[str, int | bool]:
    """Explicitly ask the BitTorrent DHT and Nostr relays for more mirrors.

    Privacy: this shows this computer's network address to DHT nodes and to
    each relay, which are strangers, and joining the DHT briefly opens a UDP
    port and routes a little DHT traffic for others. Nothing is announced or
    published. Callers must only run this on an explicit user request; no
    receive does it by default. Found cards are verified and grant nothing.
    """
    from . import mirror_rendezvous, nostr

    active = control or OperationControl()
    dht_addresses: tuple[str, ...] = ()
    dht_used = mirror_rendezvous.rendezvous_available()
    if dht_used:
        node = mirror_rendezvous.Rendezvous()
        try:
            dht_addresses = node.lookup(seconds=dht_seconds, control=active)
        finally:
            node.close()
    cards = nostr.find_cards(
        nostr.DEFAULT_RELAYS if relays is None else relays,
        TransferPolicy(timeout=10, deadline_seconds=120),
        control=active,
    )
    added = mirrors.ingest(cards)
    reachable = tuple(item for item in dht_addresses if permitted(item, policy))
    if reachable:
        added += mirrors.crawl(
            reachable,
            replace(
                policy,
                timeout=min(policy.timeout, 10.0),
                deadline_seconds=min(policy.deadline_seconds, 120.0),
            ),
            max_documents=min(len(reachable), 32),
            expand=False,
            control=active,
        )
    return {
        "dht_used": dht_used,
        "dht_addresses": len(dht_addresses),
        "dht_permitted": len(reachable),
        "relay_cards": len(cards),
        "added": added,
    }
