"""A client's own record of mirrors: the network is the list.

The directory is filled from any bootstrap (a handoff's mirrors, a mirror's
peers document, a rendezvous, an optional seed list) and survives anything
that happens to any one organization. Choosing from untrusted lists is safe
because each mirror is sampled at random, every byte is verified, and a local
score remembers which mirrors actually delivered correct bytes. A fake or dead
mirror is dropped after a few failures; a flood of them costs bounded attempts.
"""

from __future__ import annotations

import http.client
import random
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import Field

from .archives import ArchiveManifest
from .mirror_cards import MAX_PEERS, MAX_PEERS_BYTES, PEERS_PATH, read_peers
from .mirror_cards import verify_card as _verify_card
from .models import PreservationError, Record, TrustProfile
from .operations import OperationControl, OperationError
from .signing import Envelope, decode_base64, digest, encode_record
from .storage import atomic_bytes, directory, manifest_path, read_metadata
from .transfer import (
    DEFAULT_POLICY,
    TransferPolicy,
    fetch,
    inspect_envelope,
    response,
)

MAX_MIRRORS = 2048
DROP_AFTER = 3


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


def _read_document(base: str, policy: TransferPolicy, maximum: int) -> bytes:
    with response(base, PEERS_PATH, policy) as received:
        if received.status != 200:
            raise PreservationError("partial peers document is not accepted")
        data = received.read(maximum + 1)
    if len(data) > maximum:
        raise PreservationError("peers document exceeds size limit")
    return data


class MirrorDirectory:
    """Remember, sample and score mirrors without trusting any of them."""

    def __init__(self, path: Path, *, rng: random.Random | None = None) -> None:
        self.path = path
        self._rng = rng or random.SystemRandom()

    def read(self) -> DirectoryState:
        """Read the cached directory; an absent file is an empty directory."""
        try:
            with directory(self.path) as root:
                return DirectoryState.model_validate_json(
                    read_metadata(root, "directory.json")
                )
        except FileNotFoundError:
            return DirectoryState()

    def _write(self, state: DirectoryState) -> None:
        with directory(self.path, create=True) as root:
            atomic_bytes(root, "directory.json", encode_record(state))

    def ingest(self, cards: Iterable[bytes], *, now: int | None = None) -> int:
        """Keep each verified card, newest per mirror key; return new mirrors."""
        instant = int(time.time()) if now is None else now
        state = self.read()
        mirrors = dict(state.mirrors)
        added = 0
        for raw in cards:
            try:
                card = _verify_card(raw, now=instant)
            except (ValueError, PreservationError):
                continue
            key = card.mirror_key.key_id
            previous = mirrors.get(key)
            envelope = Envelope.model_validate_json(raw)
            if previous is None:
                if len(mirrors) >= MAX_MIRRORS:
                    continue
                added += 1
                mirrors[key] = SeenMirror(card=envelope, last_seen=instant)
                continue
            older = _verify_issue(previous.card)
            if card.issued_at > older:
                mirrors[key] = previous.model_copy(
                    update={"card": envelope, "last_seen": instant}
                )
        self._write(state.model_copy(update={"mirrors": mirrors}))
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
        kept. Discovery never fetches models and never trusts what it reads.
        With ``expand`` false, only the given seeds are asked, so a caller can
        learn from mirrors a user already approved without contacting others.
        """
        active = control or OperationControl()
        queue = list(dict.fromkeys(seeds))
        if expand:
            for item in self._known_addresses():
                if item not in queue:
                    queue.append(item)
        visited: set[str] = set()
        added = 0
        while queue and len(visited) < max_documents:
            active.checkpoint()
            base = queue.pop(0)
            if base in visited:
                continue
            visited.add(base)
            try:
                cards = read_peers(_read_document(base, policy, MAX_PEERS_BYTES))
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
        return [
            address
            for seen in self.read().mirrors.values()
            if seen.failures < DROP_AFTER or seen.successes > seen.failures
            for address in _verify_addresses(encode_record(seen.card))
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
            if seen.failures >= DROP_AFTER and seen.failures > seen.successes:
                continue
            try:
                card = _verify_card(encode_record(seen.card), now=instant)
            except (ValueError, PreservationError):
                continue
            if not card.covers(manifest_id):
                continue
            score = (seen.successes + 1) / (seen.failures + 1) + self._rng.random()
            scored.extend((score, address) for address in card.addresses[:2])
        scored.sort(reverse=True)
        return tuple(dict.fromkeys(address for _score, address in scored))[:limit]

    def record(self, address: str, *, delivered: bool) -> None:
        """Remember whether a mirror delivered correct bytes."""
        state = self.read()
        mirrors = dict(state.mirrors)
        for key, seen in mirrors.items():
            if address in _verify_addresses(encode_record(seen.card)):
                mirrors[key] = seen.model_copy(
                    update={
                        "successes": seen.successes + 1
                        if delivered
                        else seen.successes,
                        "failures": seen.failures if delivered else seen.failures + 1,
                    }
                )
        self._write(state.model_copy(update={"mirrors": mirrors}))

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
        Returns the number of mirrors confirmed.
        """
        active = control or OperationControl()
        identities = list(dict.fromkeys(known))
        state = self.read()
        mirrors = dict(state.mirrors)
        confirmed = 0
        for key, seen in mirrors.items():
            active.checkpoint()
            if seen.checked:
                continue
            raw = encode_record(seen.card)
            try:
                card = _verify_card(raw)
            except (ValueError, PreservationError):
                continue
            claimed = [item for item in identities if card.covers(item)]
            if not claimed:
                continue
            identity = self._rng.choice(claimed)
            ok = False
            for address in card.addresses[:2]:
                try:
                    with response(address, manifest_path(identity), policy) as got:
                        data = got.read(4 * 1024 * 1024 + 1)
                    envelope = Envelope.model_validate_json(data)
                    ok = digest(decode_base64(envelope.payload)) == identity
                except OperationError:
                    raise
                except (OSError, ValueError, http.client.HTTPException):
                    ok = False
                if ok:
                    break
            mirrors[key] = seen.model_copy(
                update={
                    "checked": ok,
                    "failures": seen.failures if ok else seen.failures + 1,
                }
            )
            confirmed += ok
        self._write(state.model_copy(update={"mirrors": mirrors}))
        return confirmed

    def gossip(
        self, *, limit: int = MAX_PEERS, now: int | None = None
    ) -> tuple[bytes, ...]:
        """Return checked, live cards worth vouching for, most reliable first."""
        instant = int(time.time()) if now is None else now
        ranked: list[tuple[int, bytes]] = []
        for seen in self.read().mirrors.values():
            raw = encode_record(seen.card)
            if not seen.checked:
                continue
            try:
                _verify_card(raw, now=instant)
            except (ValueError, PreservationError):
                continue
            ranked.append((seen.successes - seen.failures, raw))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return tuple(raw for _score, raw in ranked[:limit])


def _verify_issue(envelope: Envelope) -> int:
    try:
        return _verify_card(encode_record(envelope), now=0).issued_at
    except (ValueError, PreservationError):
        return -1


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
    any weights move; mirrors that fail are scored down, and the ones that
    succeed share the download. Integrity never depends on any of them.
    """
    active = control or OperationControl()
    candidates = tuple(
        dict.fromkeys(
            (*extra_sources, *mirrors.candidates(manifest_id, limit=policy.max_sources))
        )
    )[: policy.max_sources]
    if not candidates:
        raise PreservationError("no known mirror lists this model yet")
    working: list[str] = []
    envelope: bytes | None = None
    for source in candidates:
        active.checkpoint()
        try:
            found = inspect_envelope(
                manifest_id, trust, (source,), policy, control=active.fork()
            )
        except OperationError:
            raise
        except (OSError, ValueError, http.client.HTTPException):
            mirrors.record(source, delivered=False)
            continue
        envelope = envelope or found
        working.append(source)
    if not working or envelope is None:
        raise PreservationError("no discovered mirror served this exact model")
    try:
        manifest = fetch(
            manifest_id,
            trust,
            tuple(working),
            destination,
            policy,
            control=active.fork(),
            envelope=envelope,
        )
    except OperationError:
        raise
    except (OSError, ValueError):
        for source in working:
            mirrors.record(source, delivered=False)
        raise
    for source in working:
        mirrors.record(source, delivered=True)
    return manifest
