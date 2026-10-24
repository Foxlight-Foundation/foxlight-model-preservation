"""Explicit PirateFace lookup: untrusted routing candidates, never model authority."""

from __future__ import annotations

import re
import time
from dataclasses import replace
from pathlib import Path
from typing import Literal, Self
from urllib.parse import parse_qs, urlencode, urlsplit

from pydantic import Field, model_validator

from .mirror_native import write_reference
from .models import Digest, PreservationError, Record
from .operations import OperationControl
from .signing import canonical_bytes, digest, encode_record, strict_json
from .transfer import TransferPolicy, response

MAX_CANDIDATE_BYTES = 256 * 1024
DEFAULT_SOURCE = "https://pirateface.co"
LOOKUP_POLICY = TransferPolicy(deadline_seconds=60)


def repository_id(value: str) -> str:
    """Accept one exact owner/repository identifier, without URL or path aliases."""
    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", value
    ):
        raise PreservationError("PirateFace requires an exact OWNER/REPOSITORY")
    return value


def _source(value: str) -> str:
    parsed = urlsplit(value)
    if (
        len(value) > 2048
        or parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(ord(char) < 33 or char == "\\" for char in value)
    ):
        raise PreservationError("candidate source must be credential-free HTTP(S)")
    _ = parsed.port
    return value


class PirateFaceCandidate(Record):
    """A bounded lookup observation; no signer, execution or sharing grants."""

    schema_version: Literal["foxlight.pirateface-candidate.v1"] = (
        "foxlight.pirateface-candidate.v1"
    )
    authority: Literal["untrusted-candidate"] = "untrusted-candidate"
    repo_id: str
    source_base: str
    revision_hint: str | None = Field(default=None, min_length=1, max_length=512)
    infohash: str = Field(pattern=r"^[0-9a-f]{40}$")
    magnet: str = Field(max_length=80)
    response_sha256: Digest
    observed_at: int = Field(ge=0, le=2**53 - 1)

    @model_validator(mode="after")
    def check_candidate(self) -> Self:
        """Keep hints bounded and remove all active tracker/web-seed instructions."""
        repository_id(self.repo_id)
        _source(self.source_base)
        if self.magnet != "magnet:?xt=urn:btih:" + self.infohash:
            raise PreservationError("candidate magnet must contain only its infohash")
        if self.revision_hint and any(
            ord(char) < 32 or ord(char) == 127 for char in self.revision_hint
        ):
            raise PreservationError("invalid candidate revision hint")
        return self


def read_candidate(data: bytes) -> PirateFaceCandidate:
    """Read a strict local candidate receipt, without network or key enrollment."""
    if len(data) > MAX_CANDIDATE_BYTES:
        raise PreservationError("PirateFace candidate exceeds its metadata bound")
    return PirateFaceCandidate.model_validate_json(canonical_bytes(strict_json(data)))


def lookup(
    repo_id: str,
    *,
    source: str = DEFAULT_SOURCE,
    policy: TransferPolicy = LOOKUP_POLICY,
    control: OperationControl | None = None,
    output: Path | None = None,
) -> PirateFaceCandidate:
    """GET one exact repository; never rescue, refresh, download weights or seed.

    The remote service observes this query and the direct client's address.
    Response hashes pin observations only; original signed captures and separately
    supplied trust are still required for import. This API needs no native client.
    An optional output publishes the receipt atomically without replacing a file.
    """
    repository_id(repo_id)
    _source(source)
    policy.validate()
    with (control or OperationControl()).operation("pirateface_lookup") as active:
        if output is not None and (output.exists() or output.is_symlink()):
            raise PreservationError("candidate output already exists")
        active.limit_deadline(time.monotonic() + policy.deadline_seconds)
        active.phase("downloading")
        request_policy = replace(
            policy, timeout=min(policy.timeout, policy.deadline_seconds)
        )
        with response(
            source, "api/torrents?" + urlencode({"repo": repo_id}), request_policy
        ) as received:
            if received.status != 200:
                raise PreservationError("partial candidate metadata is not accepted")
            length = received.getheader("Content-Length")
            if length is not None and (
                not length.isdigit() or int(length) > MAX_CANDIDATE_BYTES
            ):
                raise PreservationError(
                    "PirateFace response exceeds its metadata bound"
                )
            data = bytearray()
            while len(data) <= MAX_CANDIDATE_BYTES:
                active.checkpoint()
                chunk = received.read1(min(65536, MAX_CANDIDATE_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            active.checkpoint()
        if len(data) > MAX_CANDIDATE_BYTES:
            raise PreservationError("PirateFace response exceeds its metadata bound")
        if length is not None and len(data) != int(length):
            raise PreservationError("PirateFace response length differs")
        raw = strict_json(bytes(data))
        if not isinstance(raw, dict) or raw.get("repoId") != repo_id:
            raise PreservationError("PirateFace repository binding differs")
        infohash = raw.get("infohash")
        magnet = raw.get("magnet")
        if not isinstance(infohash, str) or not re.fullmatch(r"[0-9a-f]{40}", infohash):
            raise PreservationError("PirateFace requires a v1 hexadecimal infohash")
        if not isinstance(magnet, str):
            raise PreservationError("PirateFace response has no matching magnet")
        parsed = urlsplit(magnet)
        if parsed.scheme != "magnet" or parsed.netloc or parsed.path or parsed.fragment:
            raise PreservationError("invalid PirateFace magnet")
        query = parse_qs(parsed.query, max_num_fields=64)
        if query.get("xt") != ["urn:btih:" + infohash]:
            raise PreservationError("PirateFace magnet/infohash binding differs")
        candidate = PirateFaceCandidate.model_validate_json(
            canonical_bytes(
                {
                    "repo_id": repo_id,
                    "source_base": source,
                    "revision_hint": raw.get("revision"),
                    "infohash": infohash,
                    "magnet": "magnet:?xt=urn:btih:" + infohash,
                    "response_sha256": digest(bytes(data)),
                    "observed_at": int(time.time()),
                }
            )
        )
        if output is not None:
            active.phase("publishing")
            write_reference(output, encode_record(candidate))
            active.mark_committed()
        return candidate
