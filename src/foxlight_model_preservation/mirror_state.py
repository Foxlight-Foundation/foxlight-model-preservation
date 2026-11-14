"""Crash-durable mirror policy, lock ownership and response-body reservations."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import secrets
import stat
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar, cast

from pydantic import JsonValue

from .licences import licence_status
from .mirror_models import (
    HeldModel,
    MirrorState,
    RestartGrant,
    at_least,
    reached,
    upgrade_profile,
)
from .models import PreservationError
from .signing import digest, strict_json
from .storage import atomic_bytes, directory, open_file, writer_lock

T = TypeVar("T")
MAX_STATE = 16 * 1024**2
HEADROOM = 256 * 1024**2
DIRECTORIES = (".", "store", "public", "discovery", "scratch")
LOG = logging.getLogger(__name__)


def encode_state(state: MirrorState) -> bytes:
    """Preserve released legacy wire state while encoding explicit owner profiles."""
    value = state.model_dump(mode="json")
    # Optional receipts/policy introduced after v5 are omitted at their default,
    # so unchanged released ledgers keep identical bytes and policy identity.
    # A release that predates them refuses (strict fields) rather than ignores.
    for held in value["models"].values():
        if held.get("deferred") is None:
            held.pop("deferred", None)
        if held.get("withdrawal") is None:
            held.pop("withdrawal", None)
    if state.announce is None:
        value.pop("announce")
    if not state.follow_withdrawals:
        value.pop("follow_withdrawals")
    if state.intake is None:
        value.pop("intake")
    if not reached(state.schema_version, "foxlight.mirror-state.v5"):
        for held in value["models"].values():
            held.pop("catalog_id")
    if state.schema_version in {"foxlight.mirror-state.v1", "foxlight.mirror-state.v2"}:
        for held in value["models"].values():
            held.pop("origin")
            held.pop("envelope_sha256")
    if not reached(state.schema_version, "foxlight.mirror-state.v4"):
        value.pop("http_enabled")
    if state.native_policy is None and not state.native_models:
        value.pop("native_policy")
        value.pop("native_models")
    elif state.native_policy is not None and not state.native_policy.pirate_face:
        value["native_policy"].pop("pirate_face")
    if not state.pirate_face:
        value.pop("pirate_face")
    from .signing import canonical_bytes

    return canonical_bytes(value)


def _legacy_withdrawn(value: JsonValue) -> dict[str, dict[str, JsonValue]]:
    """Find selections carrying an earlier release's ``withdrawn`` receipt."""
    models = value.get("models") if isinstance(value, dict) else None
    if not isinstance(models, dict):
        return {}
    return {
        identity: cast(dict[str, JsonValue], held)
        for identity, held in models.items()
        if isinstance(held, dict) and "withdrawn" in held
    }


def decode_state(raw: bytes) -> tuple[MirrorState, tuple[str, ...]]:
    """Parse a bounded ledger, reading older ``withdrawn`` receipts as advisories.

    Earlier releases recorded ``withdrawn: true`` and stopped serving the copy.
    It is read as a withdrawal advisory whose source is the selection's own
    and whose catalog and time are unknown. Serving then follows the current
    rule: advisory unless the operator follows withdrawals. The next write
    stores the advisory form, which those releases refuse as an unknown field.
    Also returns the selections such a receipt had withheld from serving.
    """
    value = strict_json(raw, maximum_bytes=MAX_STATE)
    legacy = _legacy_withdrawn(value)
    withheld = tuple(
        sorted(
            identity
            for identity, held in legacy.items()
            if held.get("withdrawn") is True
        )
    )
    for held in legacy.values():
        withdrawn = held.pop("withdrawn")
        if withdrawn is True:
            if held.get("withdrawal") is not None:
                raise PreservationError("selection has two withdrawal receipts")
            held["withdrawal"] = {"source_id": held.get("source_id")}
        elif withdrawn is not False:
            raise PreservationError("legacy withdrawal receipt must be a boolean")
    if legacy:
        raw = json.dumps(value).encode()
    return MirrorState.model_validate_json(raw), withheld


def _log_conversion(path: Path, state: MirrorState, legacy: tuple[str, ...]) -> None:
    """Say, once, what reading old withdrawn receipts as advisories changes."""
    LOG.warning(
        "Catalog withdrawal is now advisory: a publisher's catalog cannot take "
        "down this mirror's copies. %d copies an earlier release stopped serving "
        "because a followed catalog withdrew them are %s: %s. To stop serving "
        "one, run 'model-preserve mirror block --dir %s --manifest <id>'; to "
        "stop serving withdrawn copies automatically, run 'model-preserve mirror "
        "config --dir %s --follow-withdrawals on'.",
        len(legacy),
        "still withheld (follow-withdrawals is on)"
        if state.follow_withdrawals
        else "served again",
        ", ".join(legacy),
        path,
        path,
    )


# One in-process lock per ledger directory, shared by every MirrorLedger made
# for it. The file lock below refuses a second writer at once, which is right
# across processes, but threads of one app (the mirror runner, serving, bucket
# uploads) must take turns rather than record a spurious failure.
_LEDGER_LOCKS: dict[Path, threading.RLock] = {}
_LEDGER_LOCKS_GUARD = threading.Lock()


def reenroll(path: Path) -> None:
    """Record a moved mirror's own directories as its enrolled identities.

    Only for a mirror tree its owner has just copied to a new folder and
    verified, before that folder is put in place; every other open still
    refuses changed directory identities.
    """
    with directory(path) as root:
        with open_file(root, "mirror.json") as stream:
            state, _legacy = decode_state(stream.read(MAX_STATE + 1))
        identities: dict[str, tuple[int, int]] = {}
        for name in DIRECTORIES:
            info = os.stat(name, dir_fd=root, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode):
                raise PreservationError("mirror directories must be owner-only")
            identities[name] = (info.st_dev, info.st_ino)
        moved = state.model_copy(update={"identity": identities})
        atomic_bytes(root, "mirror.json", encode_state(moved))


class MirrorLedger:
    """Bound a controller to enrolled directory identities and an atomic ledger."""

    def __init__(self, path: Path):
        self.path = path.absolute()
        with _LEDGER_LOCKS_GUARD:
            self._mutex = _LEDGER_LOCKS.setdefault(self.path, threading.RLock())
        self._content_mutex = threading.RLock()
        self._identity = self.read().identity

    @contextmanager
    def content_lock(self) -> Generator[None]:
        """Serialize this owner's ctime-sensitive reads and public hardlink writes."""
        with self._content_mutex:
            yield

    @staticmethod
    def initialize(path: Path, **config: object) -> MirrorLedger:
        """Create a new empty mirror offline; refuse occupied or replaced roots."""
        path.mkdir(mode=0o700)
        identities: dict[str, tuple[int, int]] = {}
        with directory(path) as root:
            for name in DIRECTORIES:
                if name != ".":
                    os.mkdir(name, 0o700, dir_fd=root)
                info = os.stat(name, dir_fd=root, follow_symlinks=False)
                identities[name] = (info.st_dev, info.st_ino)
            fields = dict(
                config,
                identity=identities,
                salt=secrets.token_hex(32),
                clock_floor=int(time.time()),
            )
            if "schema_version" not in fields and (
                fields.get("egress_daily") is None
                or fields.get("egress_monthly") is None
                or fields.get("intake") is not None
            ):
                # No upload cap (or a standing intake policy) is a v6 choice.
                fields["schema_version"] = "foxlight.mirror-state.v6"
            state = MirrorState.model_validate(fields)
            atomic_bytes(root, "mirror.json", encode_state(state))
        return MirrorLedger(path)

    @contextmanager
    def root(self) -> Generator[int]:
        """Reject unsafe owners, symlinks and replaced enrolled directories."""
        with directory(self.path) as root:
            for name in DIRECTORIES:
                info = os.stat(name, dir_fd=root, follow_symlinks=False)
                if (
                    not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_mode & 0o077
                ):
                    raise PreservationError("mirror directories must be owner-only")
                expected = getattr(self, "_identity", {}).get(name)
                if expected and (info.st_dev, info.st_ino) != expected:
                    raise PreservationError("mirror directory identity changed")
            yield root

    @contextmanager
    def child(self, name: str) -> Generator[int]:
        """Pin a known enrolled child without allowing a path replacement race."""
        if name not in DIRECTORIES or name == ".":
            raise PreservationError("unknown mirror child directory")
        with self.root() as root:
            fd = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root
            )
            try:
                info = os.fstat(fd)
                if (info.st_dev, info.st_ino) != self._identity[name]:
                    raise PreservationError("mirror directory identity changed")
                yield fd
            finally:
                os.close(fd)

    def read(self) -> MirrorState:
        """Read offline state without connecting, serving or granting authority."""
        return self._read()[0]

    def _read(self) -> tuple[MirrorState, tuple[str, ...]]:
        with self._mutex, self.root() as root, open_file(root, "mirror.json") as stream:
            state, legacy = decode_state(stream.read(MAX_STATE + 1))
            if hasattr(self, "_identity") and state.identity != self._identity:
                raise PreservationError("mirror identity changed")
            # Check disk identities even on the first open after process restart.
            for name, expected in state.identity.items():
                if name not in DIRECTORIES:
                    raise PreservationError("unknown mirror directory identity")
                info = os.stat(name, dir_fd=root, follow_symlinks=False)
                if (info.st_dev, info.st_ino) != expected:
                    raise PreservationError("mirror directory identity changed")
            if set(state.identity) != set(DIRECTORIES):
                raise PreservationError("incomplete mirror directory identities")
            return state, legacy

    def legacy_withdrawals(self) -> tuple[str, ...]:
        """List selections an earlier release recorded as withdrawn (unserved).

        Such receipts are read as advisories, so those copies serve again under
        the default policy. The first write stores the advisory form and logs
        what changed, so this is empty after it.
        """
        return self._read()[1]

    def update(self, action: Callable[[MirrorState], MirrorState]) -> MirrorState:
        """Fsync a complete transaction; contention refuses safely."""
        with self._mutex, self.root() as root, writer_lock(root):
            current, legacy = self._read()
            state = MirrorState.model_validate(
                upgrade_profile(action(current)).model_dump()
            )
            raw = encode_state(state)
            if len(raw) > MAX_STATE:
                raise PreservationError("mirror state exceeds its bound")
            atomic_bytes(root, "mirror.json", raw)
            if legacy:
                _log_conversion(self.path, state, legacy)
            return state

    @contextmanager
    def service_lock(self) -> Generator[None]:
        """Fence controllers and offline deletion; never nest a store writer lock."""
        with (
            self.root() as root,
            open_file(root, ".service.lock", write=True) as stream,
        ):
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise PreservationError(
                    "stop the active mirror controller first"
                ) from error
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def running(self) -> bool:
        """Probe the existing kernel service lock offline without creating files."""
        try:
            with self.root() as root, open_file(root, ".service.lock") as stream:
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return True
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except FileNotFoundError:
            pass
        return False

    def usage(self) -> int:
        """Count regular-file logical bytes once per inode, including partial work."""
        with self.root() as root:
            seen: set[tuple[int, int]] = set()
            count = total = 0

            def walk(fd: int, depth: int) -> None:
                nonlocal count, total
                if depth > 64:
                    raise PreservationError("mirror tree exceeds depth bound")
                for name in os.listdir(fd):
                    count += 1
                    if count > 200000:
                        raise PreservationError("mirror tree exceeds inventory bound")
                    try:
                        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    except FileNotFoundError:
                        # Owned exporters remove stages concurrently. Missing
                        # files consume no current disk; verification is separate.
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        try:
                            child = os.open(
                                name, os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                            )
                        except FileNotFoundError:
                            continue
                        try:
                            pinned = os.fstat(child)
                            if (pinned.st_dev, pinned.st_ino) != (
                                info.st_dev,
                                info.st_ino,
                            ):
                                raise PreservationError(
                                    "mirror inventory directory changed"
                                )
                            walk(child, depth + 1)
                        finally:
                            os.close(child)
                    elif stat.S_ISREG(info.st_mode):
                        identity = info.st_dev, info.st_ino
                        if identity not in seen:
                            total += info.st_size
                            seen.add(identity)
                    else:
                        raise PreservationError("unsafe entry in mirror storage")

            walk(root, 0)
            return total

    def reserve(
        self,
        count: int,
        *,
        ingress: bool = False,
        operation: str | None = None,
        now: int | None = None,
        authorized: Callable[[MirrorState], bool] | None = None,
    ) -> bool:
        """Reserve conservative bytes durably; rollback/pause/expiry fail closed."""
        accepted = False
        instant = int(time.time()) if now is None else now

        def charge(state: MirrorState) -> MirrorState:
            nonlocal accepted
            if instant < state.clock_floor or state.paused or state.left or count < 0:
                return state
            if authorized and not authorized(state):
                return state
            moment = datetime.fromtimestamp(instant, UTC)
            if not ingress and not serving_hour(state, moment.hour):
                return state
            day, month = moment.strftime("%Y-%m-%d"), moment.strftime("%Y-%m")
            state = state.model_copy(
                update={
                    "clock_floor": instant,
                    "day": day,
                    "month": month,
                    "ingress_used": state.ingress_used if day == state.day else 0,
                    "egress_day_used": state.egress_day_used if day == state.day else 0,
                    "egress_month_used": state.egress_month_used
                    if month == state.month
                    else 0,
                }
            )
            if ingress:
                if operation is not None and operation in state.reserved_operations:
                    accepted = True
                    return state
                if state.ingress_used + count > state.ingress_daily:
                    return state
                accepted = True
                return state.model_copy(
                    update={
                        "ingress_used": state.ingress_used + count,
                        "reserved_operations": state.reserved_operations
                        + ((operation,) if operation else ()),
                    }
                )
            # An uncapped allowance (None) still counts bytes for status, but
            # never refuses them.
            if (
                state.egress_daily is not None
                and state.egress_day_used + count > state.egress_daily
            ) or (
                state.egress_monthly is not None
                and state.egress_month_used + count > state.egress_monthly
            ):
                return state
            accepted = True
            return state.model_copy(
                update={
                    "egress_day_used": state.egress_day_used + count,
                    "egress_month_used": state.egress_month_used + count,
                }
            )

        self.update(charge)
        return accepted


def serving_hour(state: MirrorState, hour: int) -> bool:
    """Interpret equal start/end as all day; other UTC windows are half-open."""
    a, b = state.serve_start, state.serve_end
    return a == b or (a <= hour < b if a < b else hour >= a or hour < b)


def withdrawal_withholds(state: MirrorState, held: HeldModel) -> bool:
    """Whether a catalog withdrawal stops serving this copy: only by operator choice.

    Withdrawal is advisory unless the operator turned on ``follow_withdrawals``;
    even then, a copy whose advisory the operator acknowledged keeps serving.
    Blocking (including withheld copies, which are always blocked) is the
    operator's separate, unconditional serving control; no advisory or
    acknowledgement overrides it.
    """
    return bool(
        state.follow_withdrawals
        and held.withdrawal is not None
        and not held.withdrawal.acknowledged
    )


def intake_covers(state: MirrorState, held: HeldModel) -> bool:
    """Whether the standing intake policy already covers one local selection.

    Only an "all eligible" policy covers anything, and only a local public
    selection whose every reviewed licence is identified and selected. Being
    withheld does not change coverage: withholding narrows serving and never
    re-scopes the grant.
    """
    intake = state.intake
    return bool(
        intake is not None
        and intake.share == "eligible"
        and held.origin == "local"
        and licence_status(
            [terms.license_id for terms in held.grant.terms], intake.licenses
        )
        == "eligible"
    )


def policy_id(state: MirrorState) -> str:
    """Bind restart authority to directory, sources, model trust and resource policy.

    A standing intake policy is bound as a whole (less its withheld list, which
    only narrows). Local selections it covers are then not bound one by one, so
    admitting a newly matching public copy keeps the grant; any other local
    selection is bound exactly, as before. A ledger without an intake policy
    hashes exactly as earlier releases did.
    """
    return digest(
        encode_state(
            state.model_copy(
                update={
                    "intake": state.intake.model_copy(update={"withheld": ()})
                    if state.intake is not None
                    else None,
                    # Source grants already bind dynamic catalog intake. Local
                    # selections instead bind their exact identity and accepted
                    # policy; live block/pin/verification receipts grant nothing.
                    "models": {
                        identity: held.model_copy(
                            update={
                                "pinned": False,
                                "blocked": False,
                                "state": "pending",
                                "error": None,
                                "verified_at": None,
                                "deferred": None,
                                "withdrawal": None,
                            }
                        )
                        for identity, held in state.models.items()
                        if held.origin == "local" and not intake_covers(state, held)
                    },
                    "restart": None,
                    "paused": False,
                    "left": False,
                    "clock_floor": 0,
                    "day": "",
                    "month": "",
                    "ingress_used": 0,
                    "egress_day_used": 0,
                    "egress_month_used": 0,
                    "reserved_operations": (),
                    "last_cycle": None,
                    "last_error": None,
                    "native_models": {},
                    # Pirate Face lookups and checks are receipts, not policy.
                    "pirate_face": {},
                }
            )
        )
    )


def restart_allowed(state: MirrorState, now: int | None = None) -> bool:
    """A separate unexpired exact-policy grant is required for unattended serving."""
    instant = int(time.time()) if now is None else now
    grant = state.restart
    return bool(
        not state.paused
        and not state.left
        and instant >= state.clock_floor
        and grant
        and grant.expires > instant
        and grant.policy_id == policy_id(state)
    )


def grant_restart(ledger: MirrorLedger, expires: int) -> MirrorState:
    """Record a separately confirmed bounded standing service grant offline."""
    now = int(time.time())
    if not now < expires <= now + 366 * 86400:
        raise PreservationError("resume expiry must be within the next 366 days")

    def grant(state: MirrorState) -> MirrorState:
        # Explicit fresh consent may upgrade a legacy source owner. An unattended
        # old-profile restart may not silently acquire a new policy identity.
        if state.sources:
            state = state.model_copy(
                update={
                    "schema_version": at_least(
                        state.schema_version, "foxlight.mirror-state.v5"
                    )
                }
            )
        return state.model_copy(
            update={
                "restart": RestartGrant(policy_id=policy_id(state), expires=expires)
            }
        )

    return ledger.update(grant)
