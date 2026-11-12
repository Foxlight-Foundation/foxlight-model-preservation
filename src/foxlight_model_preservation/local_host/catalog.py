"""Built-in registry discovery with pinned TUF trust and local-only search.

The preset supplies discovery, not executable model authorization. Original
catalog bytes and proof roles are retained for independent offline verification.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterator
from importlib.resources import files
from pathlib import Path
from typing import cast

from pydantic import JsonValue
from tuf.api.exceptions import DownloadError, DownloadHTTPError, RepositoryError
from tuf.ngclient import Updater
from tuf.ngclient.config import UpdaterConfig
from tuf.ngclient.fetcher import FetcherInterface

from ..cards import ModelCardEnvelope, identity_json, load_card
from ..models import MAX_METADATA_BYTES, PreservationError
from ..operations import OperationControl, OperationError
from ..registry_proof import (
    RegistryProof,
    VersionFloor,
    create_registry_proof,
    verify_registry_proof,
)
from ..signing import digest, encode_record, strict_json
from ..storage import atomic_bytes, directory, read_metadata
from ..transfer import MirrorHTTPError, TransferPolicy, response

REGISTRY_URL = "https://registry.foxlight.ai/"
VIRTUAL_URL = "https://registry.sovereign.invalid/"
CATALOG_PATH = "v1/catalog.json"
# Card licences ride in their own signed target so registry readers that
# validate the catalog strictly (older Skulk) never see an unknown field.
LICENCES_PATH = "v1/card-licences.json"
MAX_LICENCES_BYTES = 1024**2
_LICENCE_ID = re.compile(r"[a-z0-9][a-z0-9.+-]{0,63}")


class RegistryFetcher(FetcherInterface):
    """Fetch only bounded top-level registry roles and the selected catalog."""

    def __init__(self, endpoint: str, control: OperationControl | None = None) -> None:
        self.control = control or OperationControl()
        self.endpoint = endpoint
        self.remaining = 32 * 1024**2
        self.requests = 40
        self.deadline = time.monotonic() + 90

    def _fetch(self, url: str) -> Iterator[bytes]:
        self.control.checkpoint()
        relative = url.removeprefix(VIRTUAL_URL)
        if not url.startswith(VIRTUAL_URL) or not re.fullmatch(
            r"(?:metadata/(?:[1-9][0-9]{0,15}\.)?(?:root|timestamp|snapshot|targets)\.json|targets/v1/(?:[0-9a-f]{64}\.)?(?:catalog|card-licences)\.json)",
            relative,
        ):
            raise PreservationError("unsupported registry metadata route")
        if self.requests <= 0 or time.monotonic() >= self.deadline:
            raise PreservationError("registry update allowance exhausted")
        self.requests -= 1
        policy = TransferPolicy(timeout=min(15, self.deadline - time.monotonic()))
        try:
            with response(self.endpoint, relative, policy) as stream:
                count = 0
                while chunk := stream.read(64 * 1024):
                    self.control.checkpoint()
                    count += len(chunk)
                    self.remaining -= len(chunk)
                    if count > MAX_METADATA_BYTES or self.remaining < 0:
                        raise PreservationError("registry metadata exceeds allowance")
                    if time.monotonic() >= self.deadline:
                        raise PreservationError("registry update deadline exceeded")
                    yield chunk
        except MirrorHTTPError as error:
            raise DownloadHTTPError(
                "registry metadata unavailable", error.status_code
            ) from error


def with_licence(
    metadata: dict[str, JsonValue] | None, licence: str | None
) -> dict[str, JsonValue] | None:
    """Add the signed registry licence to catalog metadata that names none."""
    if licence is None or (metadata is not None and metadata.get("license")):
        return metadata
    return {**(metadata or {}), "license": licence}


def result_order(
    row: dict[str, JsonValue], *, browsing: bool
) -> tuple[bool, bool, int, str]:
    """Order search results: saveable models first, then smallest first.

    When browsing without a search or a chosen use, chat models come before
    the rest, since that is what most newcomers look for first.
    """
    details = row.get("details")
    purposes: JsonValue = details.get("purposes") if isinstance(details, dict) else None
    chat = isinstance(purposes, list) and "chat" in purposes
    size = row.get("size_bytes")
    return (
        row.get("download_support") != "ready",
        browsing and not chat,
        size if type(size) is int else 2**53,
        str(row.get("name")),
    )


class RegistryCatalog:
    """One optional preset with authenticated updates and retained offline browse."""

    def __init__(
        self, cache: Path, *, anchor: bytes | None = None, endpoint: str = REGISTRY_URL
    ) -> None:
        self.cache = cache
        self.anchor = (
            files("foxlight_model_preservation")
            .joinpath("resources/foxlight_registry_root.json")
            .read_bytes()
            if anchor is None
            else anchor
        )
        self.endpoint = endpoint
        with directory(cache, create=True):
            pass

    def refresh(
        self, *, control: OperationControl | None = None
    ) -> dict[str, JsonValue]:
        """Explicitly update metadata; keep the last accepted snapshot on failure."""
        metadata = self.cache / "metadata"
        targets = self.cache / "targets"
        with directory(metadata, create=True) as root:
            try:
                os.stat("root.json", dir_fd=root, follow_symlinks=False)
            except FileNotFoundError:
                atomic_bytes(root, "root.json", self.anchor)
        with directory(targets, create=True):
            pass
        updater = Updater(
            str(metadata),
            VIRTUAL_URL + "metadata/",
            str(targets),
            VIRTUAL_URL + "targets/",
            fetcher=RegistryFetcher(self.endpoint, control),
            config=UpdaterConfig(max_root_rotations=32),
        )
        updater.refresh()
        target = updater.get_targetinfo(CATALOG_PATH)
        if target is None or target.length > MAX_METADATA_BYTES:
            raise PreservationError("registry catalog missing or too large")
        path = Path(updater.download_target(target))
        raw = path.read_bytes()
        target.verify_length_and_hashes(raw)
        with directory(metadata) as root:
            root_name = os.readlink("root.json", dir_fd=root)
            if not re.fullmatch(
                r"root_history/[1-9][0-9]{0,15}\.root\.json", root_name
            ):
                raise PreservationError("unsafe TUF root history")
            current = strict_json(read_metadata(root, root_name))
            if not isinstance(current, dict) or not isinstance(current["signed"], dict):
                raise PreservationError("invalid root")
            version = current["signed"]["version"]
            anchor = strict_json(self.anchor)
            if not isinstance(anchor, dict) or not isinstance(
                anchor.get("signed"), dict
            ):
                raise PreservationError("invalid registry anchor")
            initial = cast(dict[str, JsonValue], anchor["signed"]).get("version")
            if (
                type(initial) is not int
                or type(version) is not int
                or not initial <= version <= initial + 32
            ):
                raise PreservationError("unsupported root rotation count")
            roots = tuple(
                read_metadata(root, f"root_history/{index}.root.json")
                for index in range(initial + 1, version + 1)
            )
            proof = create_registry_proof(
                trusted_root=self.anchor,
                root_chain=roots,
                timestamp=read_metadata(root, "timestamp.json"),
                snapshot=read_metadata(root, "snapshot.json"),
                targets=read_metadata(root, "targets.json"),
                target_path=CATALOG_PATH,
                target=raw,
            )
        cards = self._cards(raw)
        if not cards:
            raise PreservationError("registry catalog contains no cards")
        # Validate the complete proof before publishing a browser-visible head.
        verify_registry_proof(encode_record(proof), self.anchor, cards[0].card_id)
        identity = digest(raw)
        licences = self._download_licences(updater, raw, proof)
        with directory(self.cache) as root:
            atomic_bytes(root, "catalogs/" + identity + ".json", raw)
            atomic_bytes(root, "proofs/" + identity + ".json", encode_record(proof))
            if licences is not None:
                atomic_bytes(root, "licences/" + identity + ".json", licences)
            atomic_bytes(root, "accepted.json", identity_json({"catalog_id": identity}))
        return self.search()

    @staticmethod
    def _download_licences(
        updater: Updater, catalog: bytes, proof: RegistryProof
    ) -> bytes | None:
        """Fetch the signed card-licence target, or None when this head has none.

        A missing or unusable licence file never blocks a catalog update:
        listings then simply name no licence, as before the file existed.
        """
        target = updater.get_targetinfo(LICENCES_PATH)
        if target is None or target.length > MAX_LICENCES_BYTES:
            return None
        try:
            raw = Path(updater.download_target(target)).read_bytes()
            target.verify_length_and_hashes(raw)
            RegistryCatalog._licences(raw, catalog, proof)
        except OperationError:
            # Cancellation and operation budgets still stop the whole update.
            raise
        except (
            PreservationError,
            ValueError,
            OSError,
            DownloadError,
            RepositoryError,
        ):
            return None
        return raw

    @staticmethod
    def _licences(raw: bytes, catalog: bytes, proof: RegistryProof) -> dict[str, str]:
        """Check retained licence bytes offline against the proof's signed targets.

        The proof already carries the complete signed ``targets.json``, so the
        file's length and hash are re-checked without contacting the registry.
        It must describe the same snapshot as the catalog it sits beside.
        """
        from tuf.api.metadata import (
            Metadata,
            Targets,  # pyright: ignore[reportPrivateImportUsage]
        )

        from ..signing import decode_base64

        targets = Metadata[Targets].from_bytes(decode_base64(proof.targets)).signed
        info = targets.targets.get(LICENCES_PATH)
        if info is None or "sha256" not in info.hashes:
            raise PreservationError("card licences are not signed")
        info.verify_length_and_hashes(raw)
        value = strict_json(raw)
        listing = strict_json(catalog)
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != 1
            or not isinstance(listing, dict)
            or value.get("snapshot_id") != listing.get("snapshot_id")
            or not isinstance(value.get("licences"), dict)
        ):
            raise PreservationError("invalid card licences")
        return {
            str(card_id): licence
            for card_id, licence in cast(
                dict[str, JsonValue], value["licences"]
            ).items()
            if isinstance(licence, str) and _LICENCE_ID.fullmatch(licence)
        }

    def licences(self, identity: str | None = None) -> dict[str, str]:
        """Each card's signed registry licence for an accepted snapshot, offline."""
        # The catalog itself must authenticate; only the licence file is optional.
        raw, proof = self.snapshot(identity)
        try:
            with directory(self.cache) as root:
                licences = read_metadata(root, "licences/" + digest(raw) + ".json")
            return self._licences(licences, raw, proof)
        except (PreservationError, ValueError, OSError, RepositoryError):
            return {}

    @staticmethod
    def _cards(raw: bytes) -> tuple[ModelCardEnvelope, ...]:
        value = strict_json(raw)
        if not isinstance(value, dict) or not isinstance(value.get("cards"), list):
            raise PreservationError("invalid registry catalog")
        cards: list[ModelCardEnvelope] = []
        for card in cast(list[JsonValue], value["cards"]):
            try:
                cards.append(load_card(identity_json(card)))
            except ValueError:
                # Unknown profiles remain in the exact retained catalog. They do
                # not become downloadable selections through an older adapter.
                continue
        if len({card.card_id for card in cards}) != len(cards):
            raise PreservationError("ambiguous registry cards")
        return tuple(cards)

    def snapshot(self, identity: str | None = None) -> tuple[bytes, RegistryProof]:
        """Load an accepted exact snapshot without contacting the registry."""
        with directory(self.cache) as root:
            if identity is None:
                selected = strict_json(read_metadata(root, "accepted.json"))
                if not isinstance(selected, dict):
                    raise PreservationError("invalid registry cache")
                identity = str(selected.get("catalog_id"))
            if not re.fullmatch(r"[0-9a-f]{64}", identity):
                raise PreservationError("invalid catalog identity")
            raw = read_metadata(root, "catalogs/" + identity + ".json")
            proof = RegistryProof.model_validate_json(
                read_metadata(root, "proofs/" + identity + ".json")
            )
        if digest(raw) != identity:
            raise PreservationError("registry cache changed")
        cards = self._cards(raw)
        if not cards:
            raise PreservationError("registry cache contains no supported cards")
        verified = verify_registry_proof(
            encode_record(proof), self.anchor, cards[0].card_id, mode="historical"
        )
        if verified.report.target_sha256 != identity:
            raise PreservationError("registry cache proof changed")
        return raw, proof

    def select(
        self, identity: str, card_id: str
    ) -> tuple[ModelCardEnvelope, RegistryProof]:
        """Authenticate a selected original card against the application's anchor."""
        _raw, proof = self.snapshot(identity)
        card = verify_registry_proof(
            encode_record(proof), self.anchor, card_id, mode="historical"
        ).card
        return card, proof

    def verify_current(self, proof: RegistryProof, card_id: str) -> None:
        """Check fresh publication against this owner's already observed TUF floors.

        A retained proof cannot regain current assurance after a newer verified
        root or role was observed, including an update whose catalog was rejected.
        Historical inspection remains separately available for exact old data.
        """
        floors: dict[str, int] = {}
        metadata = self.cache / "metadata"
        if metadata.exists():
            with directory(metadata) as root:
                for role in ("root", "timestamp", "snapshot", "targets"):
                    name = role + ".json"
                    try:
                        if role == "root":
                            name = os.readlink(name, dir_fd=root)
                            if not re.fullmatch(
                                r"root_history/[1-9][0-9]{0,15}\.root\.json", name
                            ):
                                raise PreservationError("unsafe TUF root history")
                        value = strict_json(read_metadata(root, name))
                    except FileNotFoundError:
                        continue
                    signed = value.get("signed") if isinstance(value, dict) else None
                    version = (
                        signed.get("version") if isinstance(signed, dict) else None
                    )
                    if type(version) is not int or version < 1:
                        raise PreservationError("invalid retained update floor")
                    floors[role] = version
        verify_registry_proof(
            encode_record(proof),
            self.anchor,
            card_id,
            minimum_versions=VersionFloor(**floors),
        )

    def search(
        self,
        query: str = "",
        offset: int = 0,
        purpose: str = "",
        ready_only: bool = False,
    ) -> dict[str, JsonValue]:
        """Search accepted local data only; queries never go to suppliers."""
        try:
            raw, proof = self.snapshot()
        except FileNotFoundError:
            return {"catalog_id": None, "results": [], "total": 0, "next_offset": None}
        rows: list[dict[str, JsonValue]] = []
        terms = query.casefold().split()
        parsed = strict_json(raw)
        captured_metadata = (
            parsed.get("card_metadata") if isinstance(parsed, dict) else None
        )
        licences = self.licences(digest(raw))
        for card in self._cards(raw):
            from .presentation import card_summary, search_words
            from .selection import registry_file_map, registry_recipe

            # Display the same selected base-origin paths as download review.
            # Unsupported selections remain browsable with their original map.
            try:
                selected = registry_file_map(card)
            except PreservationError:
                selected = None

            metadata = (
                captured_metadata.get(card.card_id)
                if isinstance(captured_metadata, dict)
                else None
            )
            metadata = with_licence(
                metadata if isinstance(metadata, dict) else None,
                licences.get(card.card_id),
            )
            details = card_summary(
                card,
                metadata=metadata if isinstance(metadata, dict) else None,
                selected_files=(
                    path
                    for repository, revision, path in selected
                    if (repository, revision)
                    == (card.artifact.repository, card.artifact.revision)
                )
                if selected is not None
                else None,
            )
            if purpose and purpose not in cast(list[str], details.get("purposes", [])):
                continue
            haystack = (
                identity_json(card.model_dump(mode="json")).decode()
                + " "
                + identity_json(metadata).decode()
                + " "
                + search_words(details)
            ).casefold()
            if not all(term in haystack for term in terms):
                continue
            bundle = card.artifact.bundle
            tasks = details.get("workloads", [])
            file_list = "registry"
            try:
                registry_recipe(card, proof)
                support = "ready"
            except PreservationError as error:
                support = str(error)
                # A listing without its own file list is saved from the exact
                # revision it pins, with Hugging Face's list for that commit.
                if support == "complete_signed_file_map_required" and not card.card.get(
                    "image"
                ):
                    support, file_list = "ready", "huggingface"
            rows.append(
                {
                    "card_id": card.card_id,
                    "name": card.artifact.repository.rsplit("/", 1)[-1].replace(
                        "-", " "
                    ),
                    "selection": card.alias,
                    "file_label": (
                        card.artifact.selected_file.rsplit("/", 1)[-1][:240]
                        if card.artifact.selected_file
                        else None
                    ),
                    "publisher": card.artifact.repository.split("/", 1)[0],
                    "download_support": support,
                    "file_list": file_list,
                    "details": details,
                    "format": card.artifact.format,
                    "quantization": card.artifact.quantization,
                    "size_bytes": sum(file.size_bytes for file in selected.values())
                    if selected is not None
                    else bundle.download_size
                    if bundle
                    else _storage_size(card.card),
                    "workloads": tasks,
                    "complete_file_map": bool(bundle)
                    and all(
                        file.object_id is not None
                        and file.object_id.startswith("sha256:")
                        for file in bundle.files
                    )
                    if bundle
                    else False,
                }
            )
        browsing = not terms and not purpose
        rows.sort(key=lambda row: result_order(row, browsing=browsing))
        matching_total = len(rows)
        ready_total = sum(row["download_support"] == "ready" for row in rows)
        if ready_only:
            rows = [row for row in rows if row["download_support"] == "ready"]
        page: list[dict[str, JsonValue]] = []
        page_bytes = 0
        for row in rows[offset : offset + 24]:
            size = len(json.dumps(row, separators=(",", ":"))) + 1
            if page_bytes + size > 58_000:
                break
            page.append(row)
            page_bytes += size
        if not page and rows[offset:]:
            raise PreservationError("metadata_preview_too_large")
        return {
            "catalog_id": digest(raw),
            "results": cast(list[JsonValue], page),
            "total": len(rows),
            "matching_total": matching_total,
            "ready_total": ready_total,
            "next_offset": offset + len(page)
            if offset + len(page) < len(rows)
            else None,
            "source": "Foxlight public registry",
            "anchor_sha256": proof.anchor_sha256,
            "freshness": "saved",
            "note": "Saved signed metadata. Download review checks freshness.",
        }


def _storage_size(card: dict[str, JsonValue]) -> int | None:
    """A legacy card's declared size, when it states one."""
    storage = card.get("storage_size")
    size = storage.get("in_bytes") if isinstance(storage, dict) else None
    return size if type(size) is int and size > 0 else None
