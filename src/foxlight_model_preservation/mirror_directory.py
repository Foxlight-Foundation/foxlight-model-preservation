"""A client's own record of mirrors: the network is the list.

The directory is filled from any bootstrap (a handoff's mirrors, a mirror's
peers document, a rendezvous, an optional seed list) and survives anything
that happens to any one organization. Choosing from untrusted lists is safe
because each mirror is sampled at random, every byte is verified, and a local
score remembers which mirrors actually delivered correct bytes. A fake or dead
mirror is dropped after a few failures; a flood of them costs bounded attempts.
"""

from __future__ import annotations

import fcntl
import http.client
import random
import threading
import time
from collections.abc import Callable, Generator, Iterable
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Literal

from pydantic import Field

from .archives import ArchiveManifest
from .mirror_cards import (
    MAX_PEERS,
    MAX_PEERS_BYTES,
    PEERS_PATH,
    MirrorCard,
    read_peers,
)
from .mirror_cards import verify_card as _verify_card
from .models import MAX_METADATA_BYTES, PreservationError, Record, TrustProfile
from .operations import DeadlineExceeded, OperationControl, OperationError
from .signing import (
    Envelope,
    canonical_bytes,
    decode_base64,
    digest,
    encode_record,
    strict_json,
)
from .storage import atomic_bytes, directory, manifest_path, open_file, read_metadata
from .transfer import (
    DEFAULT_POLICY,
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
        deadline = time.monotonic() + policy.deadline_seconds
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
        self, manifest_id: str, *, limit: int = 8, now: int | None = None
    ) -> tuple[str, ...]:
        """Sample live mirrors that probably hold a manifest, best first.

        Sampling is random within score bands, so no single list order and no
        flood of fake cards can decide which mirrors a client tries.
        """
        instant = int(time.time()) if now is None else now
        scored: list[tuple[float, str]] = []
        for seen in self.read().mirrors.values():
            if _dropped(seen):
                continue
            card = _live(seen, instant)
            if card is None or not card.covers(manifest_id):
                continue
            score = (seen.successes + 1) / (seen.failures + 1) + self._rng.random()
            scored.extend((score, address) for address in card.addresses[:2])
        scored.sort(reverse=True)
        return tuple(dict.fromkeys(address for _score, address in scored))[:limit]

    def record(self, address: str, *, delivered: bool) -> None:
        """Remember whether a mirror delivered correct bytes."""
        self.record_many({address: delivered})

    def record_many(self, outcomes: dict[str, bool]) -> None:
        """Remember several delivery outcomes in one locked update."""
        if not outcomes:
            return

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

    def spot_check(
        self,
        known: Iterable[str],
        policy: TransferPolicy = DEFAULT_POLICY,
        *,
        control: OperationControl | None = None,
    ) -> int:
        """Confirm mirrors really serve bytes they claim before vouching for them.

        For each unchecked mirror, fetch one manifest it claims from those this
        checker knows and confirm its signed payload hashes to that identity.
        No trust is needed for this; it proves only that the bytes are there.
        Results are merged into the current directory, and only for the exact
        card that was checked. Returns the number of mirrors confirmed.
        """
        active = control or OperationControl()
        deadline = time.monotonic() + policy.deadline_seconds
        identities = list(dict.fromkeys(known))
        results: dict[str, tuple[Envelope, bool]] = {}
        # Dropped mirrors are never retried, and the rest are checked in random
        # order, so unresponsive entries cannot starve newly learned mirrors.
        entries = list(self.read().mirrors.items())
        self._rng.shuffle(entries)
        for key, seen in entries:
            active.checkpoint()
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
            (*extra_sources, *mirrors.candidates(manifest_id, limit=policy.max_sources))
        )
    )[: policy.max_sources]
    if not candidates:
        raise PreservationError("no known mirror lists this model yet")
    # One absolute deadline covers every probe and the download itself.
    deadline = time.monotonic() + policy.deadline_seconds

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
