"""Pirate Face as a Find source: search, torrent entries and rescue requests.

The explicit CLI lookup and offline import live in ``pirateface`` and
``pirateface_import``; this module is what Find uses.

Pirate Face (pirateface.co) indexes Hugging Face models as community torrents
and hosts no files. Sovereign uses only the interfaces Pirate Face documents or
its own MIT client uses: the plain-text search ``/s/<words>``, one model's entry
``/api/torrents?repo=``, that entry's ``.torrent`` file and ``/api/rescue``.

Searching sends the search words to pirateface.co, so it is a one-time choice,
as for Hugging Face. A rescue request is sent only when the person asks.
Everything here is hostile input: responses are size-bounded, parsed strictly,
and a torrent file is accepted only when its v1 infohash matches the entry.
Nothing here grants trust; files are checked against fingerprints before they
are kept, like any other save.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast
from urllib.parse import quote, unquote, urlencode, urlsplit

from .hub import repository_id
from .models import PreservationError
from .pirateface import DEFAULT_SOURCE as PIRATE_FACE
from .transfer import (
    MirrorHTTPError,
    PinnedConnection,
    TransferPolicy,
    response,
)

SEARCH_LIMIT = 24
MAX_SEARCH_BYTES = 512 * 1024
MAX_ENTRY_BYTES = 4 * 1024 * 1024
MAX_TORRENT_BYTES = 16 * 1024 * 1024
MAX_TORRENT_FILES = 10_000
POLICY = TransferPolicy(timeout=15, deadline_seconds=60)

_INFOHASH = re.compile(r"[0-9a-f]{40}")
_REVISION = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_LINE = re.compile(
    r"(?P<repository>\S+)\s+(?P<size>\d+(?:\.\d+)?)\s(?P<unit>[KMGT]?B)\s+"
    r"(?P<seeds>\d+) seeds?\s+(?P<magnet>magnet:\?\S+)"
)
_UNITS = {"B": 1, "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12}


def _read(
    source: str | None, relative: str, bound: int, policy: TransferPolicy | None
) -> bytes:
    try:
        with response(source or PIRATE_FACE, relative, policy or POLICY) as received:
            data = received.read(bound + 1)
    except MirrorHTTPError as error:
        if error.status_code == 404:
            raise PreservationError("pirate_face_not_listed") from error
        if error.status_code == 429:
            raise PreservationError("pirate_face_rate_limited") from error
        raise PreservationError("pirate_face_unavailable") from error
    except OSError as error:
        raise PreservationError("pirate_face_unavailable") from error
    if len(data) > bound:
        raise PreservationError("pirate_face_response_too_large")
    return data


def _web_seed_revision(url: str, repository: str) -> str | None:
    """The exact commit a Pirate Face web-seed URL names, if it names one."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "pirateface.co":
        return None
    prefix = "/api/ws/" + repository + "/"
    path = unquote(parsed.path)
    if not path.startswith(prefix):
        return None
    revision = path[len(prefix) :].strip("/")
    return revision if _REVISION.fullmatch(revision) else None


@dataclass(frozen=True)
class SwarmResult:
    """One Pirate Face search hit; descriptive only."""

    repository: str
    size_bytes: int
    seeds: int
    infohash: str
    revision: str | None

    def view(self) -> dict[str, object]:
        """Return the JSON shape Find shows."""
        return {
            "source": "pirateface",
            "repository": self.repository,
            "name": self.repository.split("/", 1)[1],
            "owner": self.repository.split("/", 1)[0],
            "size_bytes": self.size_bytes,
            "seeds": self.seeds,
            "infohash": self.infohash,
            "revision": self.revision,
        }


def _result(line: str) -> SwarmResult | None:
    match = _LINE.fullmatch(line.strip())
    if match is None:
        return None
    try:
        repository = repository_id(match["repository"])
    except PreservationError:
        return None
    query = urlsplit(match["magnet"]).query
    infohash, revision = None, None
    for part in query.split("&"):
        key, _, value = part.partition("=")
        if key == "xt" and value.lower().startswith("urn:btih:"):
            candidate = value[9:].lower()
            infohash = candidate if _INFOHASH.fullmatch(candidate) else infohash
        elif key == "ws":
            revision = _web_seed_revision(unquote(value), repository)
    if infohash is None:
        return None
    return SwarmResult(
        repository=repository,
        size_bytes=int(float(match["size"]) * _UNITS[match["unit"]]),
        seeds=int(match["seeds"]),
        infohash=infohash,
        revision=revision,
    )


def search(
    query: str,
    *,
    limit: int = SEARCH_LIMIT,
    source: str | None = None,
    policy: TransferPolicy | None = None,
) -> list[SwarmResult]:
    """Search Pirate Face's listed torrents; the words go to pirateface.co."""
    words = [word for word in " ".join(query.split())[:200].split(" ") if word]
    if not words:
        return []
    path = "s/" + "+".join(quote(word, safe="") for word in words)
    try:
        text = _read(source, path, MAX_SEARCH_BYTES, policy).decode("utf-8")
    except UnicodeDecodeError as error:
        raise PreservationError("pirate_face_response_invalid") from error
    except PreservationError as error:
        if str(error) == "pirate_face_not_listed":
            return []
        raise
    results: list[SwarmResult] = []
    seen: set[str] = set()
    for line in text.splitlines():
        found = _result(line)
        if found is not None and found.repository not in seen:
            seen.add(found.repository)
            results.append(found)
        if len(results) >= max(1, min(limit, 100)):
            break
    return results


@dataclass(frozen=True)
class SwarmEntry:
    """Pirate Face's entry for one repository (or one per-quant torrent)."""

    repository: str
    revision: str | None
    on_hub: bool
    seeders: int
    leechers: int
    infohash: str
    torrent_path: str
    # SHA-256s Pirate Face recorded from Hugging Face, by path.
    hashes: tuple[tuple[str, str], ...]

    def view(self) -> dict[str, object]:
        """Return the JSON shape Find shows."""
        return {
            "repository": self.repository,
            "revision": self.revision,
            "on_hub": self.on_hub,
            "seeders": self.seeders,
            "leechers": self.leechers,
            "infohash": self.infohash,
        }


def _count(value: object) -> int:
    return value if type(value) is int and value >= 0 else 0


def lookup(
    repository: str,
    *,
    quant: str | None = None,
    source: str | None = None,
    policy: TransferPolicy | None = None,
) -> SwarmEntry | None:
    """One repository's torrent entry, or None when Pirate Face doesn't list it."""
    repository = repository_id(repository)
    params = {"repo": repository, **({"quant": quant} if quant else {})}
    try:
        data = _read(
            source, "api/torrents?" + urlencode(params), MAX_ENTRY_BYTES, policy
        )
    except PreservationError as error:
        if str(error) == "pirate_face_not_listed":
            return None
        raise
    try:
        parsed = json.loads(data)
    except (ValueError, UnicodeDecodeError) as error:
        raise PreservationError("pirate_face_response_invalid") from error
    if not isinstance(parsed, dict):
        raise PreservationError("pirate_face_response_invalid")
    entry = cast(dict[str, object], parsed)
    infohash, torrent = entry.get("infohash"), entry.get("torrent")
    if entry.get("repoId") != repository:
        raise PreservationError("pirate_face_response_invalid")
    if not isinstance(infohash, str) or not _INFOHASH.fullmatch(infohash):
        # Listed, but no magnet is served (e.g. licence terms to accept first).
        return None
    if torrent != f"/api/torrents/{infohash}.torrent":
        raise PreservationError("pirate_face_response_invalid")
    revision = entry.get("revision")
    hashes: dict[str, str] = {}
    files = entry.get("files")
    for item in (
        cast(list[object], files)[:MAX_TORRENT_FILES] if isinstance(files, list) else []
    ):
        if not isinstance(item, dict):
            continue
        record = cast(dict[str, object], item)
        path, sha256 = record.get("path"), record.get("sha256")
        if (
            isinstance(path, str)
            and isinstance(sha256, str)
            and _SHA256.fullmatch(sha256)
        ):
            hashes[path] = sha256
    return SwarmEntry(
        repository=repository,
        revision=revision
        if isinstance(revision, str) and _REVISION.fullmatch(revision)
        else None,
        on_hub=entry.get("hf") is not False,
        seeders=_count(entry.get("seeders")),
        leechers=_count(entry.get("leechers")),
        infohash=infohash,
        torrent_path=f"/api/torrents/{infohash}.torrent",
        hashes=tuple(sorted(hashes.items())),
    )


def _bdecode(data: bytes, index: int, depth: int) -> tuple[object, int]:
    """Strict bencode; returns (value, next index). Dict keys must be sorted."""
    if depth > 64:
        raise PreservationError("pirate_face_torrent_invalid")
    try:
        lead = data[index : index + 1]
        if lead == b"i":
            end = data.index(b"e", index)
            digits = data[index + 1 : end]
            if not re.fullmatch(rb"-?(0|[1-9][0-9]{0,18})", digits) or digits == b"-0":
                raise PreservationError("pirate_face_torrent_invalid")
            return int(digits), end + 1
        if lead == b"l":
            items: list[object] = []
            index += 1
            while data[index : index + 1] != b"e":
                value, index = _bdecode(data, index, depth + 1)
                items.append(value)
            return items, index + 1
        if lead == b"d":
            mapping: dict[bytes, object] = {}
            index += 1
            previous: bytes | None = None
            while data[index : index + 1] != b"e":
                key, index = _bdecode(data, index, depth + 1)
                if not isinstance(key, bytes) or (
                    previous is not None and key <= previous
                ):
                    raise PreservationError("pirate_face_torrent_invalid")
                start = index
                mapping[key], index = _bdecode(data, index, depth + 1)
                if key == b"info" and depth == 0:
                    mapping[b"\0info-span"] = (start, index)
                previous = key
            return mapping, index + 1
        colon = data.index(b":", index)
        length = data[index:colon]
        if not re.fullmatch(rb"0|[1-9][0-9]{0,9}", length):
            raise PreservationError("pirate_face_torrent_invalid")
        end = colon + 1 + int(length)
        if end > len(data):
            raise PreservationError("pirate_face_torrent_invalid")
        return data[colon + 1 : end], end
    except ValueError as error:
        raise PreservationError("pirate_face_torrent_invalid") from error


@dataclass(frozen=True)
class TorrentFile:
    """One file in a torrent, by its path inside the model repository."""

    path: str
    size_bytes: int


@dataclass(frozen=True)
class SwarmTorrent:
    """A torrent file whose v1 infohash matched the entry it came from."""

    infohash: str
    data: bytes
    name: str
    files: tuple[TorrentFile, ...]
    # The exact commit its Pirate Face web-seed names, when it has one.
    revision: str | None


def _text(value: object) -> str:
    if not isinstance(value, bytes):
        raise PreservationError("pirate_face_torrent_invalid")
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PreservationError("pirate_face_torrent_invalid") from error


def parse_torrent(data: bytes, infohash: str, repository: str) -> SwarmTorrent:
    """Accept a torrent only when its v1 infohash is the expected one."""
    decoded, end = _bdecode(data, 0, 0)
    if end != len(data) or not isinstance(decoded, dict):
        raise PreservationError("pirate_face_torrent_invalid")
    top = cast(dict[bytes, object], decoded)
    span, info = top.get(b"\0info-span"), top.get(b"info")
    if not isinstance(span, tuple) or not isinstance(info, dict):
        raise PreservationError("pirate_face_torrent_invalid")
    start, stop = cast(tuple[int, int], span)
    if hashlib.sha1(data[start:stop], usedforsecurity=False).hexdigest() != infohash:
        raise PreservationError("pirate_face_torrent_differs")
    fields = cast(dict[bytes, object], info)
    name = _text(fields.get(b"name"))
    listed = fields.get(b"files")
    if not isinstance(listed, list) or not listed:
        raise PreservationError("pirate_face_torrent_invalid")
    files: list[TorrentFile] = []
    for item in cast(list[object], listed)[: MAX_TORRENT_FILES + 1]:
        if not isinstance(item, dict):
            raise PreservationError("pirate_face_torrent_invalid")
        record = cast(dict[bytes, object], item)
        length, parts = record.get(b"length"), record.get(b"path")
        if type(length) is not int or length < 0 or not isinstance(parts, list):
            raise PreservationError("pirate_face_torrent_invalid")
        if record.get(b"attr") is not None and b"p" in cast(bytes, record[b"attr"]):
            continue  # BEP 47 padding, never a model file
        path = "/".join(_text(part) for part in cast(list[object], parts))
        if (
            not path
            or len(path) > 1024
            or path.startswith("/")
            or "\\" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or any(ord(char) < 32 for char in path)
        ):
            raise PreservationError("pirate_face_torrent_invalid")
        files.append(TorrentFile(path, length))
    if len(files) > MAX_TORRENT_FILES or len({item.path for item in files}) != len(
        files
    ):
        raise PreservationError("pirate_face_torrent_invalid")
    seeds = top.get(b"url-list")
    urls = (
        [seeds]
        if isinstance(seeds, bytes)
        else cast(list[object], seeds)
        if isinstance(seeds, list)
        else []
    )
    revision = None
    for url in urls:
        if isinstance(url, bytes):
            try:
                revision = revision or _web_seed_revision(url.decode(), repository)
            except UnicodeDecodeError:
                continue
    return SwarmTorrent(
        infohash=infohash,
        data=data,
        name=name,
        files=tuple(files),
        revision=revision,
    )


def torrent(
    entry: SwarmEntry,
    *,
    source: str | None = None,
    policy: TransferPolicy | None = None,
) -> SwarmTorrent:
    """Fetch an entry's torrent file and check it is the one the entry names."""
    data = _read(source, entry.torrent_path.lstrip("/"), MAX_TORRENT_BYTES, policy)
    return parse_torrent(data, entry.infohash, entry.repository)


def request_rescue(
    repository: str,
    *,
    host: str = "pirateface.co",
    port: int = 443,
    policy: TransferPolicy | None = None,
    tls: bool = True,
) -> None:
    """Ask Pirate Face to add a model; sent only when the person asks."""
    body = json.dumps({"repoId": repository_id(repository)}).encode()
    connection = PinnedConnection(host, port, policy or POLICY, tls=tls)
    try:
        connection.request(
            "POST",
            "/api/rescue",
            body=body,
            headers={"Content-Type": "application/json", "Host": host},
        )
        answer = connection.getresponse()
        answer.read(64 * 1024)
    except (OSError, http.client.HTTPException) as error:
        raise PreservationError("pirate_face_unavailable") from error
    finally:
        connection.close()
    if answer.status == 429:
        raise PreservationError("pirate_face_rate_limited")
    if not 200 <= answer.status < 300:
        raise PreservationError("pirate_face_unavailable")


def match_files(
    swarm: SwarmTorrent, wanted: Sequence[tuple[str, int]]
) -> dict[str, str]:
    """Map each wanted (path, size) to its torrent path when the swarm has it.

    Torrent paths are relative to the repository (Pirate Face names the torrent
    folder after it). A file the torrent lacks, or holds at another size, is
    simply not a swarm source; it still comes from Hugging Face or mirrors.
    """
    sizes = {item.path: item.size_bytes for item in swarm.files}
    return {path: path for path, size in wanted if sizes.get(path) == size}
