"""Standalone app adapter over the public publication and persistent mirror core.

A public copy, a listening session and a recipient's independent trust are three
separate choices. Stop never removes retained archives; restarting the owner
never silently opens a listener. No Skulk, account, router or swarm is required.
"""

from __future__ import annotations

import ipaddress
import os
import re
import shutil
import socket
import stat
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal, cast

from pydantic import Field, JsonValue

from ..assurance import load_retained_card
from ..cards import identity_json
from ..handoffs import Handoff
from ..invitations import create_invitation
from ..mirror import run_mirror
from ..mirror_intake import retain_local
from ..mirror_models import MirrorObservation, MirrorState, SourceGrant, Terms
from ..mirror_state import HEADROOM, MirrorLedger, policy_id
from ..models import (
    MAX_METADATA_BYTES,
    Digest,
    Manifest,
    PreservationError,
    Record,
    TrustProfile,
)
from ..operations import OperationControl
from ..pmf import verify_pmf_manifest
from ..pmf_preservation import (
    SOURCE_KEYS_DOCUMENT,
    export_preservation,
    inspect_preservation_pmf,
)
from ..publication import (
    PUBLISHER_NOTICE_PATH,
    PublicationRequest,
    PublicMemberTerms,
    PublisherNotice,
    publish_public,
    review_publication,
)
from ..signing import create_key, digest, encode_record, load_key
from ..storage import atomic_bytes, directory, manifest_path, object_path, read_metadata
from .presentation import metadata_page


class PublicCopy(Record):
    """Complete independently retained public bytes, not a reachability claim."""

    schema_version: Literal["sovereign.public-copy.v1"] = "sovereign.public-copy.v1"
    public_id: Digest
    copy_id: Digest
    manifest_id: Digest
    name: str
    size_bytes: int
    member_count: int
    trust: TrustProfile


class MemberDecision(Record):
    """Operator-declared redistribution terms for one selected data member."""

    member_id: str
    license_id: str = Field(min_length=1, max_length=200)
    terms_reference: str = Field(min_length=1, max_length=2048)


class Sharing:
    """Keep public owner state separate from private Library captures and keys."""

    def __init__(self, home: Path) -> None:
        self.home = home

    def read(self, offset: int = 0) -> dict[str, JsonValue]:
        """List completed public selections locally; never probe or start serving."""
        with directory(self.home / "sharing/copies", create=True) as root:
            rows = [
                PublicCopy.model_validate_json(read_metadata(root, name))
                for name in sorted(os.listdir(root))
                if len(name) == 69 and name.endswith(".json")
            ]
        selected = metadata_page(
            (self._view(row) for row in rows[offset:]),
            max_rows=16,
            byte_limit=16_000,
        )
        return {
            "public_copies": cast(list[JsonValue], selected),
            "total": len(rows),
            "next_offset": offset + len(selected)
            if offset + len(selected) < len(rows)
            else None,
        }

    def _view(self, copy: PublicCopy) -> dict[str, JsonValue]:
        available = False
        try:
            state = MirrorLedger(self.home / "sharing/mirrors" / copy.public_id).read()
            held = state.models.get(copy.manifest_id)
            available = bool(
                held
                and held.state == "retained"
                and not held.blocked
                and not held.error
            )
        except (ValueError, OSError):
            pass
        return {
            **copy.model_dump(mode="json", exclude={"trust"}),
            "available_for_sharing": available,
        }

    def get(self, identifier: str) -> PublicCopy:
        """Resolve an opaque public selection under this private owner's control."""
        if len(identifier) != 64 or any(
            c not in "0123456789abcdef" for c in identifier
        ):
            raise PreservationError("invalid_control")
        with directory(self.home) as root:
            result = PublicCopy.model_validate_json(
                read_metadata(root, "sharing/copies/" + identifier + ".json")
            )
        if result.public_id != identifier:
            raise PreservationError("public_copy_review_changed")
        return result

    @staticmethod
    def _origin(
        store: Path, identity: str, trust: TrustProfile
    ) -> tuple[str, Manifest, TrustProfile]:
        with directory(store) as source:
            manifest = verify_pmf_manifest(
                read_metadata(source, manifest_path(identity)), trust, identity
            )

            def document(name: str) -> bytes:
                selected = next(
                    (item for item in manifest.documents if item.document_id == name),
                    None,
                )
                if selected is None:
                    raise PreservationError("sharing_projection_unavailable")
                data = read_metadata(source, object_path(selected.sha256))
                if len(data) != selected.size or digest(data) != selected.sha256:
                    raise PreservationError("public_copy_review_changed")
                return data

            envelope = document("preservation-envelope")
            original_trust = trust
            if any(
                item.document_id == SOURCE_KEYS_DOCUMENT for item in manifest.documents
            ):
                original_trust = TrustProfile.model_validate_json(
                    document(SOURCE_KEYS_DOCUMENT)
                )
            original_id, original = inspect_preservation_pmf(
                manifest, envelope, source_trust=original_trust
            )
            return original_id, original, original_trust

    def review(
        self, store: Path, identity: str, trust: TrustProfile
    ) -> dict[str, JsonValue]:
        """Inspect signed metadata offline, without publishing or creating a key."""
        _, source, _ = self._origin(store, identity, trust)
        value: dict[str, JsonValue] = {
            "name": source.name,
            "already_public": all(
                member.intake.audience == "public" for member in source.members
            ),
            "members": [
                {
                    "member_id": member.member_id,
                    "repository": member.origin_repository,
                    "license_id": ""
                    if member.intake.license_id == "unresolved-owner-capture"
                    else member.intake.license_id,
                    "terms_reference": ""
                    if member.intake.terms_reference == ".foxlight-capture-notice.txt"
                    else member.intake.terms_reference,
                }
                for member in source.members
                if not (
                    member.role == "notices" and member.member_id == "source-evidence"
                )
            ],
        }
        self._bound(value)
        return value

    @staticmethod
    def preview(plan: dict[str, JsonValue]) -> bytes:
        """Decode every original card property beside the unchanged public manifest.

        This metadata travels separately from compact controls, allowing ordinary
        signed registry proofs without truncating fields or exceeding IPC limits.
        """
        manifest = Manifest.model_validate_json(identity_json(plan["public_manifest"]))
        cards: list[JsonValue] = []
        for member in manifest.members:
            if member.retained_card is not None:
                if (
                    member.retained_card.get("schema_version")
                    != "foxlight.retained-card.v1"
                ):
                    cards.append(
                        {"member_id": member.member_id, "card": member.retained_card}
                    )
                    continue
                retained = load_retained_card(identity_json(member.retained_card))
                cards.append(
                    {
                        "member_id": member.member_id,
                        "card": retained.envelope.model_dump(mode="json"),
                    }
                )
        result = identity_json(
            {"manifest": manifest.model_dump(mode="json"), "cards": cards}
        )
        if len(result) > MAX_METADATA_BYTES:
            raise PreservationError("metadata_preview_too_large")
        return result

    @staticmethod
    def _bound(value: dict[str, JsonValue]) -> None:

        if len(identity_json(value)) > 48_000:
            raise PreservationError("metadata_preview_too_large")

    def plan(
        self, store: Path, identity: str, trust: TrustProfile, decisions: JsonValue
    ) -> dict[str, JsonValue]:
        """Bind the complete public descriptor and every explicit member decision."""
        original_id, source, _ = self._origin(store, identity, trust)
        already_public = all(
            member.intake.audience == "public" for member in source.members
        )
        request = None
        if already_public:
            if decisions != []:
                raise PreservationError("invalid_control")
            public = source
        else:
            if not isinstance(decisions, list):
                raise PreservationError("invalid_control")
            selected = tuple(
                MemberDecision.model_validate_json(identity_json(item))
                for item in decisions
            )
            by_id = {member.member_id: member for member in source.members}
            terms: list[PublicMemberTerms] = []
            notices: list[PublisherNotice] = []
            for choice in selected:
                member = by_id.get(choice.member_id)
                if member is None:
                    raise PreservationError("invalid_control")
                notice_paths = tuple(
                    path
                    for path in member.intake.notice_paths
                    if path != ".foxlight-capture-notice.txt"
                )
                terms.append(
                    PublicMemberTerms(
                        member_id=choice.member_id,
                        license_id=choice.license_id,
                        terms_reference=choice.terms_reference,
                        decision="Operator reviewed redistribution terms.",
                        notice_paths=(*notice_paths, PUBLISHER_NOTICE_PATH),
                    )
                )
                notices.append(
                    PublisherNotice(
                        member_id=choice.member_id,
                        text=(
                            "Publisher declaration, not an upstream license grant.\n"
                            f"License: {choice.license_id}\n"
                            f"Terms: {choice.terms_reference}\n"
                            f"Source: {member.origin_repository}@"
                            f"{member.origin_revision}\n"
                        ),
                    )
                )
            request = PublicationRequest(
                name=source.name,
                workloads=source.workloads,
                runtime_requirements=source.runtime_requirements,
                members=tuple(terms),
                notices=tuple(notices),
            )
            public = review_publication(source, request)
        value: dict[str, JsonValue] = {
            "original_id": original_id,
            "public_manifest_id": original_id
            if already_public
            else digest(encode_record(public)),
            "public_manifest": public.model_dump(mode="json"),
            "publication_request": request.model_dump(mode="json") if request else None,
            "decisions": decisions,
            "name": public.name,
            "size_bytes": public.total_bytes,
            "member_count": len(public.members),
            "already_public": already_public,
        }
        if len(identity_json(value)) > MAX_METADATA_BYTES:
            raise PreservationError("metadata_preview_too_large")
        value["public_review_sha256"] = digest(self.preview(value))
        return value

    def publish(
        self,
        job_id: str,
        copy_id: str,
        store: Path,
        identity: str,
        trust: TrustProfile,
        plan: dict[str, JsonValue],
        control: OperationControl,
    ) -> None:
        """Verify all source bytes, publish separately, then retain an exact mirror."""
        current = self.plan(store, identity, trust, plan["decisions"])
        if cast(int, self.read()["total"]) >= 256:
            raise PreservationError("public_copy_capacity")
        if any(current[key] != plan[key] for key in current):
            raise PreservationError("public_copy_review_changed")
        if (
            shutil.disk_usage(self.home).free
            < cast(int, plan["size_bytes"]) * 3 + HEADROOM
        ):
            raise PreservationError("insufficient_space")
        original_id, _, original_trust = self._origin(store, identity, trust)
        work = self.home / "sharing/work" / job_id
        with directory(work, create=True):
            pass
        original = work / "original"
        export_preservation(
            store,
            identity,
            trust,
            original,
            source_trust=original_trust,
            expected_source_manifest_id=original_id,
            control=control.fork(),
        )
        public_id = str(plan["public_manifest_id"])
        public_store, public_trust = original, original_trust
        if plan["publication_request"] is not None:
            key_path = self.home / "sharing/publisher-key.pem"
            trust_path = "sharing/publisher-trust.json"
            with directory(self.home) as owner:
                try:
                    public_trust = TrustProfile.model_validate_json(
                        read_metadata(owner, trust_path)
                    )
                except FileNotFoundError:
                    if key_path.exists():
                        raise PreservationError(
                            "sovereign_capture_trust_missing"
                        ) from None
                    public_trust = create_key(key_path, None)
                    atomic_bytes(owner, trust_path, encode_record(public_trust))
            public_store = work / "public"
            request = PublicationRequest.model_validate_json(
                identity_json(plan["publication_request"])
            )
            publish_public(
                original,
                original_id,
                original_trust,
                request,
                public_store,
                (load_key(key_path, None),),
                expected_manifest_id=public_id,
                control=control.fork(),
            )

        public_manifest = Manifest.model_validate_json(
            identity_json(plan["public_manifest"])
        )
        with directory(self.home / "sharing/mirrors", create=True):
            pass
        ledger = MirrorLedger.initialize(
            self.home / "sharing/mirrors" / job_id,
            name=public_manifest.name,
            disk_bytes=max(
                public_manifest.total_bytes * 3 + 2 * HEADROOM, 512 * 1024**2
            ),
            ingress_daily=1,
            egress_daily=max(public_manifest.total_bytes * 2, 64 * 1024**2),
            egress_monthly=max(public_manifest.total_bytes * 62, 64 * 1024**2),
            bind="0.0.0.0",  # noqa: S104 - explicit later public start required
            port=0,
        )
        terms = tuple(
            {
                (member.intake.license_id, member.intake.terms_reference)
                for member in public_manifest.members
            }
        )
        retain_local(
            ledger,
            public_store,
            public_id,
            SourceGrant(
                trust=public_trust,
                terms=tuple(
                    Terms(license_id=license_id, terms_reference=reference)
                    for license_id, reference in sorted(terms)
                ),
                max_bytes=max(public_manifest.total_bytes, 1),
            ),
            control=control.fork(),
        )
        result = PublicCopy(
            public_id=job_id,
            copy_id=copy_id,
            manifest_id=public_id,
            name=public_manifest.name,
            size_bytes=public_manifest.total_bytes,
            member_count=len(public_manifest.members),
            trust=public_trust,
        )
        with directory(self.home) as owner:
            atomic_bytes(
                owner, "sharing/copies/" + job_id + ".json", encode_record(result)
            )
        # Only disposable publication work is reclaimed after the complete
        # retained mirror and its receipt are durable. Failed attempts remain
        # available for a separate operator cleanup choice in Activity.
        try:
            self.remove_work(job_id)
        except OSError:
            pass  # Activity exposes retained staging for explicit cleanup.

    def work_exists(self, identifier: str) -> bool:
        """Inspect only this owner's publication scratch, never a retained copy."""
        if re.fullmatch(r"[0-9a-f]{64}", identifier) is None:
            raise PreservationError("invalid_control")
        try:
            with directory(self.home / "sharing/work") as root:
                value = os.stat(identifier, dir_fd=root, follow_symlinks=False)
                return stat.S_ISDIR(value.st_mode)
        except FileNotFoundError:
            return False

    def remove_work(self, identifier: str) -> None:
        """Remove one owned scratch tree; callers fence active publication jobs."""
        if not self.work_exists(identifier):
            return
        if not shutil.rmtree.avoids_symlink_attacks:
            raise PreservationError("safe_temporary_cleanup_unavailable")
        with directory(self.home / "sharing/work") as root:
            shutil.rmtree(identifier, dir_fd=root)
            os.fsync(root)

    def session_plan(
        self, identifier: str, address: JsonValue = None
    ) -> dict[str, JsonValue]:
        """Review retained bytes, finite duration and durable upload allowances."""
        copy = self.get(identifier)
        ledger = MirrorLedger(self.home / "sharing/mirrors" / identifier)
        state = ledger.read()
        self._http_only(state)
        self._single_selection(state, copy.manifest_id)
        if address is not None:
            if not isinstance(address, str):
                raise PreservationError("invalid_control")
            try:
                selected_address = ipaddress.IPv4Address(address)
            except ValueError as error:
                raise PreservationError("sharing_address_invalid") from error
            if selected_address.is_unspecified or selected_address.is_multicast:
                raise PreservationError("invalid_control")
            address = str(selected_address)
        held = state.models.get(copy.manifest_id)
        if held is None or held.state != "retained" or held.blocked or held.error:
            raise PreservationError("public_copy_unavailable")
        return {
            "kind": "share",
            "public_id": identifier,
            "manifest_id": copy.manifest_id,
            "name": copy.name,
            "size_bytes": copy.size_bytes,
            "seconds": 3600,
            "daily_upload_bytes": state.egress_daily,
            "monthly_upload_bytes": state.egress_monthly,
            "ledger_sha256": digest(encode_record(state)),
            "mirror_policy_id": policy_id(state),
            "advertised_address": address,
        }

    @staticmethod
    def _http_only(state: MirrorState) -> None:
        if (
            not state.http_enabled
            or state.native_policy is not None
            or state.native_models
        ):
            raise PreservationError("sharing_http_only_required")

    @staticmethod
    def _single_selection(state: MirrorState, identity: str) -> None:
        if state.sources or set(state.models) != {identity}:
            raise PreservationError("sharing_single_selection_required")

    def require_session(self, plan: dict[str, JsonValue]) -> None:
        """Refuse a handoff whose original exact selection or policy is withdrawn."""
        copy = self.get(str(plan["public_id"]))
        state = MirrorLedger(self.home / "sharing/mirrors" / copy.public_id).read()
        self._http_only(state)
        self._single_selection(state, copy.manifest_id)
        held = state.models.get(copy.manifest_id)
        if (
            state.paused
            or state.left
            or policy_id(state) != plan["mirror_policy_id"]
            or held is None
            or held.state != "retained"
            or held.blocked
            or held.error
        ):
            raise PreservationError("sharing_not_ready")

    def serve(
        self,
        job_id: str,
        plan: dict[str, JsonValue],
        control: OperationControl,
        observe: Callable[[MirrorObservation], None],
        handoff_ready: Callable[[str | None], None],
    ) -> None:
        """Start only the separately approved HTTP mirror and drain it on stop."""
        copy = self.get(str(plan["public_id"]))
        ledger = MirrorLedger(self.home / "sharing/mirrors" / copy.public_id)
        if digest(encode_record(ledger.read())) != plan["ledger_sha256"]:
            raise PreservationError("public_copy_review_changed")
        self.require_session(plan)

        lock = threading.Lock()
        selected = False
        http_port: int | None = None
        written = False

        def prepare_handoff() -> None:
            nonlocal written
            if written or not selected or http_port is None:
                return
            # UDP connect selects a local route without sending a packet or
            # contacting a peer. It is used only after the explicit serve grant.
            address = plan.get("advertised_address")
            if address is None:
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
                        route.connect(("192.0.2.1", 9))
                        address = str(route.getsockname()[0])
                except OSError as error:
                    raise PreservationError("sharing_address_required") from error
            invitation = create_invitation(
                ledger.path / "store",
                copy.manifest_id,
                copy.trust,
                (f"http://{address}:{http_port}",),
            )
            document = encode_record(Handoff(reference=invitation))
            with directory(self.home) as owner:
                atomic_bytes(
                    owner,
                    "sharing/handoffs/" + job_id + ".foxhandoff",
                    document,
                )
            written = True
            handoff_ready(digest(document))

        def ready(port: int) -> None:
            nonlocal http_port
            with lock:
                http_port = port
                prepare_handoff()

        def selection(identities: tuple[str, ...]) -> None:
            nonlocal selected, written
            with lock:
                selected = copy.manifest_id in identities
                if not selected:
                    written = False
                    handoff_ready(None)
                prepare_handoff()

        run_mirror(
            ledger,
            public=True,
            seconds=cast(int, plan["seconds"]),
            control=control,
            ready=ready,
            observe=observe,
            serving_selection=selection,
            expected_policy_id=str(plan["mirror_policy_id"]),
        )
