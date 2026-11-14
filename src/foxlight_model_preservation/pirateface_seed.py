"""Seed a held copy's Pirate Face torrent straight from a mirror's stored files.

A mirror that turned this on looks up each held copy's origin repository on
Pirate Face. When every file of the listed torrent is held (the object with the
SHA-256 Pirate Face recorded for it, or else the one at the same path and
size), the torrent's files are mapped onto the stored objects
(``objects/sha256/<hash>``), so no second copy is written. libtorrent checks
every piece against those bytes before the first upload, and a copy that
differs is never seeded. The torrent stays in upload mode: nothing is ever
downloaded or written, so stored objects are only read.

The torrents a mirror seeds are listed in ``torrents.json``, signed by the
mirror key and served beside its holdings, so the swarm stays findable from
mirrors if Pirate Face's site goes away. Holdings themselves are unchanged:
earlier clients refuse entries with unknown fields.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Self, cast

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import Field, model_validator

from .mirror_cards import CLOCK_SKEW, MAX_CARD_SECONDS
from .mirror_holdings import held_manifest, holding
from .mirror_models import MirrorState, NativePolicy, PirateFaceSeed
from .mirror_state import MirrorLedger
from .models import (
    Digest,
    FileIdentity,
    PreservationError,
    Record,
    TrustedKey,
    TrustProfile,
)
from .operations import OperationControl, OperationError
from .pirateface import repository_id
from .pirateface_find import (
    MAX_TORRENT_BYTES,
    SwarmTorrent,
    lookup,
    parse_torrent,
    torrent,
)
from .signing import (
    Envelope,
    canonical_bytes,
    decode_base64,
    digest,
    encode_record,
    public_record,
    sign_record,
    strict_json,
    verify_payload,
)
from .storage import atomic_bytes, directory, object_path
from .torrent_binding import torrent_engine
from .transfer import TransferPolicy

TORRENTS_TYPE = "application/vnd.sovereign.mirror-torrents.v1+json"
TORRENTS_PATH = ".well-known/sovereign/torrents.json"
MAX_TORRENTS_BYTES = 256 * 1024
MAX_TORRENT_ENTRIES = 2000
# How long a lookup result stands before the mirror asks Pirate Face again.
LISTED_SECONDS = 86400
UNLISTED_SECONDS = 7 * 86400
ERROR_SECONDS = 3600


TORRENTS_DIRECTORY = "scratch/native/pirateface"


def held_files(manifest: Any) -> tuple[FileIdentity, ...]:
    """Every file of a held copy, across its members."""
    return tuple(file for member in manifest.members for file in member.files)


def match(
    swarm: SwarmTorrent,
    recorded: Mapping[str, str],
    files: Sequence[FileIdentity],
) -> tuple[str, ...] | None:
    """The stored object for each torrent file, in torrent order, or None.

    A file Pirate Face recorded a SHA-256 for maps to the held object with that
    hash and the same size; any other file maps to the one held file at the
    same path and size. One file that maps to nothing (or to more than one
    object) means the torrent is not this copy, so nothing is seeded.
    """
    sizes = {file.sha256: file.size for file in files}
    by_path: dict[tuple[str, int], set[str]] = {}
    for file in files:
        by_path.setdefault((file.path, file.size), set()).add(file.sha256)
    objects: list[str] = []
    for item in swarm.files:
        sha256 = recorded.get(item.path)
        if sha256 is not None:
            if sizes.get(sha256) != item.size_bytes:
                return None
            objects.append(sha256)
            continue
        candidates = by_path.get((item.path, item.size_bytes), set())
        if len(candidates) != 1:
            return None
        objects.append(next(iter(candidates)))
    return tuple(objects) if objects else None


class PirateFaceSeeder:
    """One finite seeding session for one verified mapping; never downloads.

    ``verified`` skips the full check on start (seed mode): libtorrent still
    checks each piece the first time a peer asks for it.
    """

    def __init__(
        self,
        swarm: SwarmTorrent,
        store: Path,
        mapping: Sequence[str],
        home: Path,
        *,
        verified: bool,
        upload_rate: int,
        max_seconds: float,
        stop_after_bytes: int,
        settings: dict[str, str | int | bool] | None = None,
    ) -> None:
        if len(mapping) != len(swarm.files):
            raise PreservationError("pirate_face_mapping_invalid")
        self.swarm = swarm
        self.max_seconds = max_seconds
        self.stop_after_bytes = stop_after_bytes
        engine = cast(Any, torrent_engine())
        native = engine.torrent_info(engine.bdecode(swarm.data))
        if str(native.info_hashes().v1) != swarm.infohash:
            raise PreservationError("pirate_face_torrent_differs")
        renamed = {
            item.index: str(store / object_path(sha256))
            for item, sha256 in zip(swarm.files, mapping, strict=True)
        }
        self._session = engine.session(
            {
                "listen_interfaces": "0.0.0.0:0,[::]:0",
                "enable_dht": True,
                "enable_lsd": False,
                "enable_upnp": False,
                "enable_natpmp": False,
                "alert_mask": 0,
                **(settings or {}),
                "upload_rate_limit": upload_rate,
                "download_rate_limit": 1,
            }
        )
        flags = engine.torrent_flags
        self._handle = self._session.add_torrent(
            {
                "ti": native,
                "save_path": str(home),
                "renamed_files": renamed,
                "flags": flags.upload_mode | (flags.seed_mode if verified else 0),
            }
        )
        self._handle.set_upload_limit(upload_rate)

    @property
    def port(self) -> int:
        """The listening port, for status only."""
        return int(self._session.listen_port())

    def run(
        self,
        control: OperationControl,
        *,
        verified: Callable[[], None] | None = None,
        poll: float = 0.25,
    ) -> int:
        """Check, then seed until the time or byte allowance ends; return bytes sent.

        The time allowance starts once the check passes, so a large copy is
        never cut off mid-check. A copy whose pieces differ raises.
        """
        engine = cast(Any, torrent_engine())
        checking = {
            engine.torrent_status.checking_files,
            engine.torrent_status.checking_resume_data,
        }
        started: float | None = None
        while True:
            control.checkpoint()
            status = self._handle.status()
            if status.errc.value():
                raise PreservationError("pirate_face_seed_failed")
            if started is None and status.state not in checking:
                if not status.is_seeding:
                    raise PreservationError("pirate_face_copy_differs")
                started = time.monotonic()
                if verified is not None:
                    verified()
            sent = int(status.total_payload_upload)
            if started is not None and (
                sent >= self.stop_after_bytes
                or time.monotonic() - started >= self.max_seconds
            ):
                return sent
            time.sleep(poll)

    def close(self) -> None:
        """Stop the session; stored objects are left exactly as they were."""
        session, handle = self._session, self._handle
        try:
            session.remove_torrent(handle)
        finally:
            session.pause()


def origin(ledger: MirrorLedger, manifest: str) -> str | None:
    """The Hub repository a held copy names as its origin, when it names one."""
    document = held_manifest(ledger.path / "store", manifest)
    if document is None:
        return None
    try:
        repository = holding(manifest, document).origin_repository
    except (StopIteration, ValueError, PreservationError):
        return None
    if repository is None:
        return None
    try:
        return repository_id(repository)
    except PreservationError:
        return None


def due(record: PirateFaceSeed | None, now: int) -> bool:
    """Whether a held copy's Pirate Face lookup should be made again."""
    if record is None:
        return True
    if record.lookup_error is not None:
        wait = ERROR_SECONDS
    elif record.listed:
        wait = LISTED_SECONDS
    else:
        wait = UNLISTED_SECONDS
    return now - record.checked_at >= wait


def record_seed(
    ledger: MirrorLedger,
    manifest: str,
    *,
    checked: tuple[str, tuple[str, ...]] | None = None,
    **changes: object,
) -> None:
    """Change one held copy's Pirate Face record, if it still has one.

    ``checked`` names the torrent and mapping a session checked: the change
    applies only while the record still names exactly those, so a lookup that
    replaced the torrent meanwhile is never marked checked (or refused).
    """

    def apply(state: MirrorState) -> MirrorState:
        records = dict(state.pirate_face)
        current = records.get(manifest)
        if current is None:
            return state
        if checked is not None and (current.infohash, current.objects) != checked:
            return state
        records[manifest] = PirateFaceSeed.model_validate(
            {**current.model_dump(), **changes}
        )
        return state.model_copy(update={"pirate_face": records})

    ledger.update(apply)


def refresh(
    ledger: MirrorLedger,
    manifest: str,
    repository: str,
    *,
    source: str | None = None,
    policy: TransferPolicy | None = None,
    reserve: Callable[[int], bool] | None = None,
    stopped: Callable[[], bool] = lambda: False,
) -> PirateFaceSeed:
    """Ask Pirate Face for one held copy's torrent and record what matches.

    Nothing is seeded here. A failed lookup or a delisting keeps whatever was
    already verified; a different listed torrent replaces it. ``reserve``
    charges the torrent file's bound to the mirror's download allowance
    before it is fetched (the caller charged the listing's). Once ``stopped``
    is true (the controller closed, or the lookup ran past its deadline) a
    slow response is dropped: nothing more is charged, written or recorded.
    """
    now = int(time.time())
    previous = ledger.read().pirate_face.get(manifest)
    if previous is not None and previous.repository != repository:
        previous = None
    record: PirateFaceSeed
    try:
        entry = lookup(repository, source=source, policy=policy)
        if entry is None:
            record = (
                previous.model_copy(
                    update={"checked_at": now, "listed": False, "lookup_error": None}
                )
                if previous is not None and previous.verified
                else PirateFaceSeed(repository=repository, checked_at=now)
            )
        else:
            if stopped():
                raise PreservationError("pirate_face_lookup_stopped")
            if reserve is not None and not reserve(MAX_TORRENT_BYTES):
                raise PreservationError("pirate_face_download_limit")
            swarm = torrent(entry, source=source, policy=policy)
            document = held_manifest(ledger.path / "store", manifest)
            if document is None:
                raise PreservationError("pirate_face_copy_unreadable")
            objects = match(swarm, dict(entry.hashes), held_files(document)) or ()
            if objects and not stopped():
                with directory(ledger.path / TORRENTS_DIRECTORY, create=True) as root:
                    atomic_bytes(root, swarm.infohash + ".torrent", swarm.data)
            same = (
                previous is not None
                and previous.infohash == swarm.infohash
                and previous.objects == objects
            )
            record = PirateFaceSeed(
                repository=repository,
                checked_at=now,
                listed=True,
                infohash=swarm.infohash,
                objects=objects,
                verified=bool(same and previous and previous.verified),
                # A copy whose pieces differed stays refused until the
                # listing changes; other seeding failures are retried.
                error=previous.error
                if same
                and previous is not None
                and previous.error == "pirate_face_copy_differs"
                else None,
                last_started=previous.last_started if previous else None,
                last_stopped=previous.last_stopped if previous else None,
                sessions=previous.sessions if previous else 0,
            )
    except PreservationError as error:
        record = (
            previous or PirateFaceSeed(repository=repository, checked_at=now)
        ).model_copy(update={"checked_at": now, "lookup_error": str(error)[:400]})

    stored = record

    def apply(state: MirrorState) -> MirrorState:
        nonlocal stored
        # Checked inside the ledger update, so a lookup that outlived its
        # controller never records anything.
        if manifest not in state.models or stopped():
            return state
        current = state.pirate_face.get(manifest)
        stored = record
        if (
            current is not None
            and current.repository == record.repository
            and (current.infohash, current.objects) == (record.infohash, record.objects)
        ):
            # The same torrent: keep what a session recorded meanwhile (its
            # check, a refusal, its counts); only the lookup itself is new.
            stored = current.model_copy(
                update={
                    "checked_at": record.checked_at,
                    "listed": record.listed,
                    "lookup_error": record.lookup_error,
                    "error": current.error
                    if current.error == "pirate_face_copy_differs"
                    else record.error,
                }
            )
        return state.model_copy(
            update={"pirate_face": {**state.pirate_face, manifest: stored}}
        )

    ledger.update(apply)
    return stored


def seedable(state: MirrorState, manifest: str) -> bool:
    """Whether a held copy has a matched torrent that may be seeded now."""
    policy = state.native_policy
    record = state.pirate_face.get(manifest)
    return bool(
        policy is not None
        and policy.pirate_face
        and record is not None
        and record.infohash
        and record.objects
        and record.error is None
    )


def seed(
    ledger: MirrorLedger,
    manifest: str,
    policy: NativePolicy,
    control: OperationControl,
    *,
    settings: dict[str, str | int | bool] | None = None,
) -> None:
    """One finite seeding session for a held copy's matched torrent."""
    record = ledger.read().pirate_face[manifest]
    if record.infohash is None or not record.objects:
        raise PreservationError("pirate_face_not_matched")
    home = ledger.path / TORRENTS_DIRECTORY
    with directory(home) as root:
        descriptor = os.open(
            record.infohash + ".torrent", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root
        )
        with os.fdopen(descriptor, "rb") as stream:
            data = stream.read(MAX_TORRENT_BYTES + 1)
    swarm = parse_torrent(data, record.infohash, record.repository)
    # Receipts from this session apply only to the torrent it checks.
    checked = (record.infohash, record.objects)
    record_seed(
        ledger,
        manifest,
        last_started=int(time.time()),
        sessions=record.sessions + 1,
    )
    seeder = PirateFaceSeeder(
        swarm,
        ledger.path / "store",
        record.objects,
        home,
        verified=record.verified,
        upload_rate=policy.upload_rate,
        max_seconds=policy.session_seconds,
        stop_after_bytes=policy.session_bytes,
        settings=settings,
    )
    try:
        seeder.run(
            control,
            verified=lambda: record_seed(
                ledger, manifest, checked=checked, verified=True
            ),
        )
    except OperationError:
        raise  # Stopped, not refused: the next session carries on.
    except PreservationError as error:
        record_seed(
            ledger, manifest, checked=checked, error=str(error)[:400], verified=False
        )
        raise
    finally:
        seeder.close()
        record_seed(ledger, manifest, last_stopped=int(time.time()))


def seeded(state: MirrorState, serving: Iterable[str]) -> list[SeededTorrent]:
    """The verified torrents this mirror seeds, for its signed torrents list."""
    if state.native_policy is None or not state.native_policy.pirate_face:
        return []
    return [
        SeededTorrent(manifest=manifest, infohash=record.infohash)
        for manifest in sorted(set(serving))
        if (record := state.pirate_face.get(manifest)) is not None
        and record.infohash is not None
        and record.verified
        and record.error is None
    ]


class SeededTorrent(Record):
    """One torrent a mirror seeds, and the held copy it carries."""

    manifest: Digest
    infohash: str = Field(pattern=r"^[0-9a-f]{40}$")
    source: Literal["pirateface"] = "pirateface"


class MirrorTorrents(Record):
    """The torrents one mirror seeds; never a grant of trust."""

    schema_version: Literal["sovereign.mirror-torrents.v1"] = (
        "sovereign.mirror-torrents.v1"
    )
    mirror_key: TrustedKey
    entries: tuple[SeededTorrent, ...] = Field(max_length=MAX_TORRENT_ENTRIES)
    issued_at: int = Field(ge=0)
    expires_at: int = Field(ge=0)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        """Short-lived like a card; each torrent listed once."""
        if not self.issued_at < self.expires_at <= self.issued_at + MAX_CARD_SECONDS:
            raise PreservationError("torrents lifetime must be at most 14 days")
        pairs = [(entry.manifest, entry.infohash) for entry in self.entries]
        if len(set(pairs)) != len(pairs):
            raise PreservationError("torrents list a torrent twice")
        return self


def issue_torrents(
    key: Ed25519PrivateKey,
    entries: Iterable[SeededTorrent],
    *,
    lifetime: int = 7 * 86400,
    now: int | None = None,
) -> bytes:
    """Sign the torrents this mirror seeds."""
    instant = int(time.time()) if now is None else now
    unique = {(item.manifest, item.infohash): item for item in entries}
    selected = [unique[key] for key in sorted(unique)][:MAX_TORRENT_ENTRIES]
    document = MirrorTorrents(
        mirror_key=public_record(key),
        entries=tuple(selected),
        issued_at=instant,
        expires_at=instant + lifetime,
    )
    return encode_record(sign_record(document, (key,), TORRENTS_TYPE))


def verify_torrents(
    data: bytes, mirror_key: TrustedKey, *, now: int | None = None
) -> MirrorTorrents:
    """Accept a torrents list only when the card's own mirror key signed it."""
    if len(data) > MAX_TORRENTS_BYTES:
        raise PreservationError("torrents exceed size limit")
    envelope = Envelope.model_validate_json(
        canonical_bytes(strict_json(data, maximum_bytes=MAX_TORRENTS_BYTES))
    )
    if envelope.payloadType != TORRENTS_TYPE:
        raise PreservationError("not mirror torrents")
    payload = decode_base64(envelope.payload)
    claimed = MirrorTorrents.model_validate_json(
        canonical_bytes(strict_json(payload, maximum_bytes=MAX_TORRENTS_BYTES))
    )
    if claimed.mirror_key != mirror_key:
        raise PreservationError("torrents name another mirror key")
    verify_payload(
        data,
        TrustProfile(
            schema_version="foxlight.model-preservation.trust.v1",
            keys=(mirror_key,),
            threshold=1,
            as_of="mirror-torrents-self-signature",
        ),
        digest(payload),
        TORRENTS_TYPE,
    )
    instant = int(time.time()) if now is None else now
    if claimed.issued_at > instant + CLOCK_SKEW:
        raise PreservationError("torrents are issued in the future")
    if claimed.expires_at <= instant:
        raise PreservationError("torrents have expired")
    return claimed
