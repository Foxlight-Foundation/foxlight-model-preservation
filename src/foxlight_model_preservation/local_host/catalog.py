"""Built-in registry discovery with pinned TUF trust and local-only search.

The preset supplies discovery, not executable model authorization. Original
catalog bytes and proof roles are retained for independent offline verification.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator
from importlib.resources import files
from pathlib import Path
from typing import cast

from pydantic import JsonValue
from tuf.api.exceptions import DownloadHTTPError
from tuf.ngclient import Updater
from tuf.ngclient.config import UpdaterConfig
from tuf.ngclient.fetcher import FetcherInterface

from ..cards import ModelCardEnvelope, identity_json, load_card
from ..models import MAX_METADATA_BYTES, PreservationError
from ..operations import OperationControl
from ..registry_proof import (
    RegistryProof,
    create_registry_proof,
    verify_registry_proof,
)
from ..signing import digest, encode_record, strict_json
from ..storage import atomic_bytes, directory, read_metadata
from ..transfer import MirrorHTTPError, TransferPolicy, response

REGISTRY_URL = "https://registry.foxlight.ai/"
VIRTUAL_URL = "https://registry.sovereign.invalid/"
CATALOG_PATH = "v1/catalog.json"


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
            r"(?:metadata/(?:[1-9][0-9]{0,15}\.)?(?:root|timestamp|snapshot|targets)\.json|targets/v1/(?:[0-9a-f]{64}\.)?catalog\.json)",
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
        with directory(self.cache) as root:
            atomic_bytes(root, "catalogs/" + identity + ".json", raw)
            atomic_bytes(root, "proofs/" + identity + ".json", encode_record(proof))
            atomic_bytes(root, "accepted.json", identity_json({"catalog_id": identity}))
        return self.search()

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

    def search(self, query: str = "", offset: int = 0) -> dict[str, JsonValue]:
        """Search accepted local data only; queries never go to suppliers."""
        try:
            raw, proof = self.snapshot()
        except FileNotFoundError:
            return {"catalog_id": None, "results": [], "total": 0, "next_offset": None}
        rows: list[dict[str, JsonValue]] = []
        terms = query.casefold().split()
        for card in self._cards(raw):
            haystack = identity_json(card.model_dump(mode="json")).decode().casefold()
            if not all(term in haystack for term in terms):
                continue
            bundle = card.artifact.bundle
            tasks = card.card.get("tasks", [])
            from .selection import registry_recipe

            try:
                registry_recipe(card, proof)
                support = "ready"
            except PreservationError as error:
                support = str(error)
            rows.append(
                {
                    "card_id": card.card_id,
                    "name": card.artifact.repository.rsplit("/", 1)[-1].replace(
                        "-", " "
                    ),
                    "selection": card.alias,
                    "publisher": card.artifact.repository.split("/", 1)[0],
                    "download_support": support,
                    "format": card.artifact.format,
                    "quantization": card.artifact.quantization,
                    "size_bytes": bundle.download_size if bundle else None,
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
        rows.sort(
            key=lambda row: (
                row["download_support"] != "ready",
                cast(int | None, row["size_bytes"]) or 2**53,
                str(row["name"]),
            )
        )
        return {
            "catalog_id": digest(raw),
            "results": cast(list[JsonValue], rows[offset : offset + 24]),
            "total": len(rows),
            "next_offset": offset + 24 if offset + 24 < len(rows) else None,
            "source": "Foxlight public registry",
            "anchor_sha256": proof.anchor_sha256,
            "freshness": "saved",
            "note": "Saved signed metadata. Download review checks freshness.",
        }
