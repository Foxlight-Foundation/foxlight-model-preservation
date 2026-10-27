"""Independent desktop presentation of the public authenticated source tools.

Source enrollment, local search, explicit metadata networking and copy-scoped
model-key acceptance are separate decisions. No method starts sharing or grants
registry/runtime authority. The public discovery and archive verifiers remain
the only implementations of those contracts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlsplit

from pydantic import Field, JsonValue

from ..cards import identity_json
from ..discovery import (
    follow_source,
    inspect_selection,
    inspect_source,
    list_sources,
    refresh_source,
    remove_source,
    search_sources,
)
from ..discovery_models import MAX_STATE_BYTES, SearchLocation
from ..handoffs import read_reference
from ..invitations import Invitation, accept_model_keys, invitation_from_envelope
from ..models import Digest, Manifest, PreservationError, Record, TrustProfile
from ..operations import OperationControl
from ..signing import Envelope, digest, encode_record, strict_json, verify_manifest
from ..storage import atomic_bytes, directory, open_file, read_metadata
from ..transfer import TransferPolicy, inspect_envelope


def _direct_routes(values: tuple[str, ...]) -> tuple[str, ...]:
    routes: list[str] = []
    for value in values:
        try:
            parsed = urlsplit(value)
            host = (parsed.hostname or "").rstrip(".").lower()
            if (
                parsed.scheme in {"http", "https"}
                and host
                and not host.endswith((".i2p", ".i2p.alt", ".onion"))
            ):
                routes.append(value)
        except ValueError:
            # Signed supplier hints are still untrusted transport data. A bad
            # hint must not make the other saved listings unsearchable.
            continue
    return tuple(routes)


def _reference_routes(
    primary: SearchLocation, additional: tuple[SearchLocation, ...]
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            route
            for location in (primary, *additional)
            for route in _direct_routes(location.mirrors)
        )
    )[:16]


class SourceChoice(Record):
    """One exact cached selection with a separate direct-connection decision."""

    kind: Literal["source_refresh", "source_metadata"]
    source_id: Digest
    entry_id: str | None = Field(default=None, max_length=512)
    manifest_id: Digest | None = None
    allow_http: bool = False
    allow_private: bool = False
    include_followed_mirrors: bool = False


class SourceFollow(Record):
    """Offline trust in reviewed listings, never permission to refresh them."""

    review_id: Digest
    confirmed: Literal[True]
    allow_http: bool = False
    allow_private: bool = False


class SourceQuery(Record):
    """A bounded local query with supplier statements paged independently."""

    query: str = Field(default="", max_length=500)
    offset: int = Field(default=0, ge=0, le=16000)


class ModelReference(Record):
    """Immutable original metadata retained before any copy-key acceptance."""

    manifest_id: Digest
    selection: SearchLocation
    envelope_sha256: Digest
    additional_locations: tuple[SearchLocation, ...] = Field(default=(), max_length=16)

    @property
    def mirrors(self) -> tuple[str, ...]:
        """Return reviewed hints without merging their separate source statements."""
        return _reference_routes(self.selection, self.additional_locations)

    @property
    def candidates(self) -> TrustProfile | None:
        """Read candidate model keys without inheriting source authority."""
        description = self.selection.description
        return description.candidate_bundle_trust if description else None


class HandoffReference(Record):
    """An offline handoff with no fabricated followed-source attribution."""

    schema_version: Literal["sovereign.handoff-reference.v1"] = (
        "sovereign.handoff-reference.v1"
    )
    manifest_id: Digest
    envelope_sha256: Digest
    invitation: Invitation

    @property
    def mirrors(self) -> tuple[str, ...]:
        """Return unsigned hints; opening the handoff starts no connections."""
        return self.invitation.mirrors

    @property
    def candidates(self) -> TrustProfile:
        """Expose candidates for a separate, exact-copy recipient decision."""
        return self.invitation.candidate_trust


class SourceLibrary:
    """Keep independent source state under the enclosing local owner's lock."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.cache = home / "sources"

    def review_handoff(self, document: bytes) -> dict[str, JsonValue]:
        """Inspect and retain one HTTP handoff offline, without enrolling keys.

        Peer handoffs refuse explicitly in this direct-connection screen. They
        are never converted into HTTP gateways or implicit swarm participation.
        The entire original document remains alongside its signed envelope.
        """
        if len(document) > MAX_STATE_BYTES:
            raise PreservationError("source_document_too_large")
        reference = read_reference(document)
        if not isinstance(reference, Invitation):
            raise PreservationError("handoff_transport_unavailable")
        self.http_mirrors(reference.mirrors)
        raw = encode_record(reference.envelope)
        identifier = digest(document)
        record = HandoffReference(
            manifest_id=reference.manifest_id,
            envelope_sha256=digest(raw),
            invitation=reference,
        )
        with directory(self.home, create=True) as root:
            atomic_bytes(root, "model-references/" + identifier + ".handoff", document)
            atomic_bytes(root, "model-references/" + identifier + ".envelope", raw)
            atomic_bytes(
                root, "model-references/" + identifier + ".json", encode_record(record)
            )
        return self.preview(identifier)

    def preview(self, identifier: str) -> dict[str, JsonValue]:
        """Show bounded signed metadata before independently accepting any keys."""
        record, _, manifest, candidates = self.reference(identifier)
        return {
            "reference_job_id": identifier,
            "manifest_id": record.manifest_id,
            "name": manifest.name,
            "size_bytes": manifest.total_bytes,
            "member_count": len(manifest.members),
            "members": [member.origin_repository for member in manifest.members[:8]],
            "keys": [key.key_id for key in candidates.keys],
            "threshold": candidates.threshold,
            "revoked_keys": list(candidates.revoked_keys),
            "mirrors": list(self.http_mirrors(record.mirrors)),
            "from_handoff": isinstance(record, HandoffReference),
        }

    def review(self, document: bytes) -> dict[str, JsonValue]:
        """Inspect a completed bounded upload offline, pinning its exact bytes."""
        if len(document) > MAX_STATE_BYTES:
            raise PreservationError("source_document_too_large")
        info = inspect_source(document)
        endpoints = cast(list[str], info["endpoints"])
        if self.http_mirrors(tuple(endpoints)) != tuple(endpoints):
            raise PreservationError("source_transport_unavailable")
        identity = digest(document)
        with directory(self.home, create=True) as root:
            atomic_bytes(root, "source-reviews/" + identity + ".json", document)
        return {**info, "review_id": identity}

    def follow(self, body: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Enroll exactly the reviewed source without DNS, updates or model trust."""
        choice = SourceFollow.model_validate(body)
        if body.get("confirmed") is not True:
            raise PreservationError("source_trust_confirmation_required")
        with directory(self.home) as root:
            with open_file(
                root, "source-reviews/" + choice.review_id + ".json"
            ) as stream:
                raw = stream.read(MAX_STATE_BYTES + 1)
        if digest(raw) != choice.review_id:
            raise PreservationError("source_review_changed")
        info = inspect_source(raw)
        status = follow_source(
            self.cache,
            raw,
            str(info["bootstrap_id"]),
            allow_http=choice.allow_http,
            allow_private=choice.allow_private,
            allow_local=False,
        )
        with directory(self.home) as root:
            atomic_bytes(
                root,
                "source-policies/" + status.source_id + ".json",
                encode_record(choice),
            )
        return status.model_dump(mode="json")

    def read(self) -> dict[str, JsonValue]:
        """Read bounded source statuses; no refresh occurs when opening the view."""
        return {
            "sources": [
                item.model_dump(mode="json") for item in list_sources(self.cache)
            ]
        }

    def remove(self, source_id: str) -> dict[str, JsonValue]:
        """Stop following locally, retaining rollback floors and all saved copies."""
        remove_source(self.cache, source_id)
        return {"removed": True}

    def search(self, body: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Page exact supplier statements without conflating names or authorities."""
        query = SourceQuery.model_validate(body)
        rows: list[JsonValue] = []
        for model in search_sources(self.cache, query.query, limit=16000):
            mirror_count = len(
                {
                    route
                    for location in model.locations
                    for route in _direct_routes(location.mirrors)
                }
            )
            source_count = len({location.source_id for location in model.locations})
            for location in model.locations:
                description = location.description
                rows.append(
                    {
                        "manifest_id": model.manifest,
                        "source_id": location.source_id,
                        "source_name": location.source_name,
                        "entry_id": location.entry_id,
                        "freshness": location.freshness,
                        "name": description.name if description else location.entry_id,
                        "summary": description.summary[:600] if description else "",
                        "formats": list(description.formats[:8]) if description else [],
                        "workloads": list(description.workloads[:8])
                        if description
                        else [],
                        "has_candidate_keys": bool(
                            description and description.candidate_bundle_trust
                        ),
                        "listed_source_count": source_count,
                        "suggested_mirror_count": mirror_count,
                    }
                )
        selected: list[JsonValue] = []
        used = 256
        for row in rows[query.offset : query.offset + 8]:
            cost = len(json.dumps(row, separators=(",", ":")).encode())
            if used + cost > 60_000:
                break
            selected.append(row)
            used += cost
        end = query.offset + len(selected)
        return {
            "results": selected,
            "total": len(rows),
            "next_offset": end if end < len(rows) else None,
        }

    def _alternatives(
        self, primary: SearchLocation, manifest: str
    ) -> tuple[SearchLocation, ...]:
        routes = set(_direct_routes(primary.mirrors))
        locations: list[SearchLocation] = []
        for result in search_sources(self.cache, manifest, limit=16000):
            if result.manifest != manifest:
                continue
            for location in result.locations:
                if len(routes) >= 16:
                    return tuple(locations)
                novel = set(_direct_routes(location.mirrors)) - routes
                if location.source_id == primary.source_id or not novel:
                    continue
                locations.append(location)
                routes.update(novel)
                if len(locations) == 16:
                    return tuple(locations)
        return tuple(locations)

    def plan(self, body: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Pin an offline selection before a finite, explicitly approved request."""
        choice = SourceChoice.model_validate(body)
        with directory(self.home) as root:
            policy = read_metadata(
                root, "source-policies/" + choice.source_id + ".json"
            )
        source = next(
            (
                item
                for item in list_sources(self.cache)
                if item.source_id == choice.source_id and item.enabled
            ),
            None,
        )
        if source is None:
            raise PreservationError("source_no_longer_followed")
        result: dict[str, JsonValue] = {
            **choice.model_dump(mode="json"),
            "name": source.name,
            "source_policy": digest(policy),
            "size_bytes": 0,
        }
        followed = SourceFollow.model_validate_json(policy)
        with directory(self.home) as root:
            with open_file(
                root, "source-reviews/" + followed.review_id + ".json"
            ) as stream:
                review = inspect_source(stream.read(MAX_STATE_BYTES + 1))
        result.update(
            {
                "mirrors": review["endpoints"],
                "metadata_allow_http": followed.allow_http,
                "metadata_allow_private": followed.allow_private,
            }
        )
        if choice.kind == "source_metadata":
            if choice.entry_id is None or choice.manifest_id is None:
                raise PreservationError("invalid_control")
            selection = inspect_selection(
                self.cache, choice.source_id, choice.entry_id, choice.manifest_id
            )
            if (
                selection.description is None
                or selection.description.candidate_bundle_trust is None
            ):
                raise PreservationError("candidate_bundle_keys_required")
            alternatives = self._alternatives(selection, choice.manifest_id)
            included = alternatives if choice.include_followed_mirrors else ()
            available = {
                route for item in alternatives for route in _direct_routes(item.mirrors)
            } - set(_direct_routes(selection.mirrors))
            result.update(
                {
                    "name": selection.description.name,
                    "selection": selection.model_dump(mode="json"),
                    "mirrors": list(_reference_routes(selection, included)),
                    "additional_locations": [
                        item.model_dump(mode="json") for item in included
                    ],
                    "additional_mirror_count": min(
                        len(available), 16 - len(_direct_routes(selection.mirrors))
                    ),
                    "mirror_sources": [
                        {"name": item.source_name, "freshness": item.freshness}
                        for item in (selection, *included)
                    ],
                }
            )
        elif (
            choice.entry_id is not None
            or choice.manifest_id is not None
            or choice.allow_http
            or choice.allow_private
            or choice.include_followed_mirrors
        ):
            raise PreservationError("invalid_control")
        return result

    def run(
        self, job_id: str, plan: dict[str, JsonValue], control: OperationControl
    ) -> None:
        """Run bounded metadata only, retaining exact original signed envelopes."""
        source_id = str(plan["source_id"])
        with directory(self.home) as root:
            policy = read_metadata(root, "source-policies/" + source_id + ".json")
        if digest(policy) != plan["source_policy"]:
            raise PreservationError("source_review_changed")
        if plan["kind"] == "source_refresh":
            status = refresh_source(self.cache, source_id, control=control)
            if status.last_error is not None:
                raise PreservationError("source_update_failed")
            return
        selection = inspect_selection(
            self.cache, source_id, str(plan["entry_id"]), str(plan["manifest_id"])
        )
        if selection.model_dump(mode="json") != plan["selection"]:
            raise PreservationError("source_model_reference_changed")
        additional = tuple(
            SearchLocation.model_validate_json(identity_json(item))
            for item in cast(list[JsonValue], plan.get("additional_locations", []))
        )
        for item in additional:
            try:
                current = inspect_selection(
                    self.cache, item.source_id, item.entry_id, str(plan["manifest_id"])
                )
            except (ValueError, OSError) as error:
                raise PreservationError("source_model_reference_changed") from error
            if current != item:
                raise PreservationError("source_model_reference_changed")
        routes = _reference_routes(selection, additional)
        if list(routes) != plan["mirrors"]:
            raise PreservationError("source_model_reference_changed")
        candidates = (
            selection.description.candidate_bundle_trust
            if selection.description
            else None
        )
        if candidates is None:
            raise PreservationError("candidate_bundle_keys_required")
        raw = inspect_envelope(
            str(plan["manifest_id"]),
            candidates,
            self.http_mirrors(routes),
            TransferPolicy(
                max_bytes=2**53 - 1,
                deadline_seconds=120,
                allow_http=plan["allow_http"] is True,
                allow_private=plan["allow_private"] is True,
            ),
            control=control.fork(),
        )
        # A source's candidate signatures are inspected here, not accepted as
        # independent model authority. Public audience and mirror syntax also
        # fail closed.
        invitation = invitation_from_envelope(
            raw,
            str(plan["manifest_id"]),
            candidates,
            self.http_mirrors(routes),
        )
        record = ModelReference(
            manifest_id=invitation.manifest_id,
            selection=selection,
            envelope_sha256=digest(raw),
            additional_locations=additional,
        )
        with directory(self.home) as root:
            atomic_bytes(root, "model-references/" + job_id + ".envelope", raw)
            atomic_bytes(
                root, "model-references/" + job_id + ".json", encode_record(record)
            )

    def reference(
        self, job_id: str
    ) -> tuple[ModelReference | HandoffReference, bytes, Manifest, TrustProfile]:
        """Reauthenticate retained candidate metadata, never granting model trust."""
        if len(job_id) != 64 or any(char not in "0123456789abcdef" for char in job_id):
            raise PreservationError("invalid_job_identity")
        with directory(self.home) as root:
            data = read_metadata(root, "model-references/" + job_id + ".json")
            value = strict_json(data)
            record = (
                HandoffReference.model_validate_json(data)
                if isinstance(value, dict)
                and value.get("schema_version") == "sovereign.handoff-reference.v1"
                else ModelReference.model_validate_json(data)
            )
            raw = read_metadata(root, "model-references/" + job_id + ".envelope")
            if isinstance(record, HandoffReference):
                document = read_metadata(
                    root, "model-references/" + job_id + ".handoff"
                )
                original = read_reference(document)
                if (
                    digest(document) != job_id
                    or not isinstance(original, Invitation)
                    or original != record.invitation
                    or encode_record(original.envelope) != raw
                    or original.manifest_id != record.manifest_id
                ):
                    raise PreservationError("source_model_reference_changed")
        candidates = record.candidates
        if digest(raw) != record.envelope_sha256 or candidates is None:
            raise PreservationError("source_model_reference_changed")
        manifest = verify_manifest(raw, candidates, record.manifest_id)
        return record, raw, manifest, candidates

    def receive_plan(self, body: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Accept copy-scoped fingerprints, separately from source authority."""
        if set(body) != {
            "kind",
            "reference_job_id",
            "keys",
            "allow_http",
            "allow_private",
            "max_bytes",
        }:
            raise PreservationError("invalid_control")
        keys = body["keys"]
        maximum = body["max_bytes"]
        if (
            not isinstance(keys, list)
            or not all(isinstance(key, str) for key in keys)
            or type(maximum) is not int
            or not 0 < maximum <= 2**53 - 1
            or type(body["allow_http"]) is not bool
            or type(body["allow_private"]) is not bool
        ):
            raise PreservationError("invalid_control")
        record, raw, manifest, candidates = self.reference(
            str(body["reference_job_id"])
        )
        trust = accept_model_keys(
            Envelope.model_validate_json(raw),
            record.manifest_id,
            candidates,
            cast(tuple[str, ...], tuple(keys)),
        )
        if maximum < manifest.total_bytes:
            raise PreservationError("receiving_allowance_too_small")
        return {
            **body,
            "name": manifest.name,
            "manifest_id": record.manifest_id,
            "envelope_sha256": record.envelope_sha256,
            "reference_sha256": digest(encode_record(record)),
            "size_bytes": manifest.total_bytes,
            "members": [member.origin_repository for member in manifest.members[:8]],
            "member_count": len(manifest.members),
            "trust": trust.model_dump(mode="json"),
            "mirrors": list(self.http_mirrors(record.mirrors)),
        }

    @staticmethod
    def http_mirrors(values: tuple[str, ...]) -> tuple[str, ...]:
        """Select direct HTTP hints without routing local paths or protected names."""
        selected = _direct_routes(values)
        if not selected:
            raise PreservationError("public_model_mirror_required")
        return selected
