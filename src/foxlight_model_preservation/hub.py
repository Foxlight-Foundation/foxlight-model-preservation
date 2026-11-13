"""Hugging Face as a Find source: search, exact snapshots and save recipes.

Find is not tied to any one index. The Hub is searched live (the user opts in,
because search words go to huggingface.co), and a chosen repository is pinned
to one commit. Every selected file gets an exact SHA-256 before any large
download starts: large (LFS) files carry it in Hub metadata, and the few small
git files are fetched from that commit, checked against their git blob ID and
hashed. The resulting recipe is the same shape a registry card produces, so a
Hub save gets the same verification, resume and mirror fallback.

Hub metadata is hostile input: every response is size-bounded and parsed
strictly, paths are canonical, and nothing here grants trust.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import cast
from urllib.parse import quote, urlencode

from .keychain import delete_secret, load_secret, save_secret
from .models import PreservationError
from .operations import OperationControl
from .transfer import MirrorHTTPError, TransferPolicy, response

HUB = "https://huggingface.co"
SEARCH_LIMIT = 24
MAX_SEARCH_BYTES = 1024 * 1024
MAX_INFO_BYTES = 8 * 1024 * 1024
MAX_FILES = 10_000
# Git (non-LFS) files on the Hub are small; anything larger is refused.
MAX_SMALL_FILE = 10 * 1024 * 1024
MAX_SMALL_TOTAL = 64 * 1024 * 1024
HUB_POLICY = TransferPolicy(timeout=20, deadline_seconds=120)

_REPOSITORY = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}"
)
_REVISION = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_BLOB = re.compile(r"[0-9a-f]{40}")
WEIGHT_SUFFIXES = (
    ".safetensors",
    ".gguf",
    ".bin",
    ".pt",
    ".pth",
    ".ckpt",
    ".onnx",
    ".npz",
    ".h5",
    ".msgpack",
    ".mlmodel",
    ".tflite",
    ".ot",
)
# Alternative encodings of the same weights; skipped by default when
# safetensors are present, never hidden from an explicit selection.
_ALTERNATE = (
    ".bin",
    ".pt",
    ".pth",
    ".ckpt",
    ".h5",
    ".msgpack",
    ".onnx",
    ".tflite",
    ".ot",
)
# Folders holding a whole alternative runtime's copy of the weights.
_ALTERNATE_FOLDERS = ("onnx/", "openvino/", "coreml/", "tflite/", "flax/", "tf/")


def repository_id(value: str) -> str:
    """Accept one exact ``owner/name`` Hub repository identifier."""
    if not _REPOSITORY.fullmatch(value) or ".." in value:
        raise PreservationError("hub_repository_invalid")
    return value


def _canonical_path(path: object) -> str:
    if (
        not isinstance(path, str)
        or not path
        or len(path) > 1024
        or path.startswith("/")
        or "\\" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or any(ord(char) < 32 for char in path)
    ):
        raise PreservationError("hub_metadata_invalid")
    return path


def _read(
    source: str | None,
    relative: str,
    bound: int,
    policy: TransferPolicy | None,
    token: str | None = None,
) -> bytes:
    source, policy = source or HUB, policy or HUB_POLICY
    try:
        with response(
            source,
            relative,
            policy,
            authorization="Bearer " + token if token else None,
        ) as received:
            data = received.read(bound + 1)
    except MirrorHTTPError as error:
        if error.status_code in {401, 403}:
            raise PreservationError("hub_access_required") from error
        if error.status_code == 404:
            raise PreservationError("hub_not_found") from error
        if error.status_code == 429:
            raise PreservationError("hub_rate_limited") from error
        raise PreservationError("hub_unavailable") from error
    except OSError as error:
        raise PreservationError("hub_unavailable") from error
    if len(data) > bound:
        raise PreservationError("hub_metadata_too_large")
    return data


def _json(data: bytes) -> object:
    try:
        return json.loads(data)
    except (ValueError, UnicodeDecodeError) as error:
        raise PreservationError("hub_metadata_invalid") from error


@dataclass(frozen=True)
class HubResult:
    """One Hub search hit; descriptive only."""

    repository: str
    downloads: int
    likes: int
    task: str | None
    library: str | None
    gated: bool

    def view(self) -> dict[str, object]:
        """Return the JSON shape Find shows."""
        return {
            "source": "huggingface",
            "repository": self.repository,
            "name": self.repository.split("/", 1)[1],
            "owner": self.repository.split("/", 1)[0],
            "downloads": self.downloads,
            "likes": self.likes,
            "task": self.task,
            "library": self.library,
            "gated": self.gated,
        }


def search(
    query: str,
    *,
    limit: int = SEARCH_LIMIT,
    source: str | None = None,
    policy: TransferPolicy | None = None,
    token: str | None = None,
) -> list[HubResult]:
    """Search public Hub models by downloads; the query goes to the Hub."""
    words = " ".join(query.split())[:200]
    if not words:
        return []
    params = urlencode(
        {
            "search": words,
            "limit": str(max(1, min(limit, 100))),
            "sort": "downloads",
            "direction": "-1",
            "full": "false",
        }
    )
    parsed = _json(
        _read(source, "api/models?" + params, MAX_SEARCH_BYTES, policy, token)
    )
    if not isinstance(parsed, list):
        raise PreservationError("hub_metadata_invalid")
    results: list[HubResult] = []
    for item in cast(list[object], parsed)[:100]:
        if not isinstance(item, dict):
            continue
        entry = cast(dict[str, object], item)
        name = entry.get("id") or entry.get("modelId")
        if not isinstance(name, str) or not _REPOSITORY.fullmatch(name):
            continue
        if entry.get("private") is True:
            continue
        downloads, likes = entry.get("downloads"), entry.get("likes")
        task, library = entry.get("pipeline_tag"), entry.get("library_name")
        results.append(
            HubResult(
                repository=name,
                downloads=downloads if type(downloads) is int else 0,
                likes=likes if type(likes) is int else 0,
                task=task if isinstance(task, str) and len(task) <= 80 else None,
                library=library
                if isinstance(library, str) and len(library) <= 80
                else None,
                gated=entry.get("gated") not in (None, False),
            )
        )
    return results


@dataclass(frozen=True)
class HubFile:
    """One file at a pinned commit: size, and its SHA-256 or git blob ID."""

    path: str
    size_bytes: int
    sha256: str | None
    blob_id: str | None

    @property
    def is_weight(self) -> bool:
        """Report whether this looks like model weights."""
        return self.path.lower().endswith(WEIGHT_SUFFIXES)


@dataclass(frozen=True)
class HubSnapshot:
    """A repository pinned to one commit, with every file's identity."""

    repository: str
    revision: str
    files: tuple[HubFile, ...]
    licence: str | None
    gated: bool
    task: str | None
    library: str | None

    def file(self, path: str) -> HubFile:
        """Return one file by exact path."""
        for item in self.files:
            if item.path == path:
                return item
        raise PreservationError("hub_file_unknown")

    def variants(self) -> list[str]:
        """List the independently selectable GGUF weights, if any.

        Split GGUFs (``-00001-of-00003``) are one variant named by their
        first part; projector files (``mmproj``) are companions, not variants.
        """
        names: list[str] = []
        for item in self.files:
            lower = item.path.lower()
            if not lower.endswith(".gguf") or "mmproj" in lower.rsplit("/", 1)[-1]:
                continue
            split = re.search(r"-(\d{5})-of-(\d{5})\.gguf$", lower)
            if split and split.group(1) != "00001":
                continue
            names.append(item.path)
        return sorted(names)

    def default_selection(self, variant: str | None = None) -> list[str]:
        """Choose the files a save takes unless the user changes them.

        A GGUF repository takes one variant (the given one, else a mid-size
        Q4_K_M-style quant when present), its split parts, one projector
        (F16 preferred) when the repository has any, and the small non-weight
        files. Other repositories take everything except
        alternative encodings of weights that are also present as safetensors
        (same folder and name), and alternative-runtime folders such as
        ``onnx/``; other weight files are kept.
        """
        variants = self.variants()
        if variants:
            chosen = variant if variant in variants else _preferred(variants)
            stem = re.sub(r"-00001-of-\d{5}\.gguf$", "", chosen, flags=re.IGNORECASE)
            projectors = sorted(
                item.path
                for item in self.files
                if item.path.lower().endswith(".gguf")
                and "mmproj" in item.path.lower().rsplit("/", 1)[-1]
            )
            projector = next(
                (
                    path
                    for marker in ("f16", "bf16", "f32")
                    for path in projectors
                    if re.search(rf"[-_.]{marker}[-_.]", path.lower())
                ),
                projectors[0] if projectors else None,
            )
            keep: list[str] = []
            for item in self.files:
                lower = item.path.lower()
                if lower.endswith(".gguf"):
                    if item.path == chosen or (
                        stem != chosen and item.path.startswith(stem + "-")
                    ):
                        keep.append(item.path)
                    elif item.path == projector:
                        keep.append(item.path)
                elif not item.is_weight:
                    keep.append(item.path)
            return sorted(keep)
        # Weights also present as safetensors in the same folder, under the
        # same name, are an alternative encoding and skipped. Any other weight
        # file (a voice, an adapter, a component) is part of the model.
        safetensors = {
            _weight_key(item.path)
            for item in self.files
            if item.path.lower().endswith(".safetensors")
        }
        return sorted(
            item.path
            for item in self.files
            if not (
                safetensors
                and (
                    (
                        item.path.lower().endswith(_ALTERNATE)
                        and _weight_key(item.path) in safetensors
                    )
                    or item.path.lower().startswith(_ALTERNATE_FOLDERS)
                )
            )
        )

    def view(self) -> dict[str, object]:
        """Return the JSON shape the save review shows."""
        return {
            "source": "huggingface",
            "repository": self.repository,
            "revision": self.revision,
            "licence": self.licence,
            "gated": self.gated,
            "task": self.task,
            "library": self.library,
            "variants": self.variants(),
            "files": [
                {
                    "path": item.path,
                    "size_bytes": item.size_bytes,
                    "weight": item.is_weight,
                }
                for item in self.files
            ],
        }


def _weight_key(path: str) -> tuple[str, str]:
    """Folder and framework-neutral stem (``pytorch_model.bin`` ~ ``model``)."""
    folder, _, name = path.lower().rpartition("/")
    stem = name.rsplit(".", 1)[0]
    for prefix in ("pytorch_", "tf_", "flax_", "rust_"):
        if stem.startswith(prefix + "model"):
            stem = stem[len(prefix) :]
            break
    return folder, stem


def _preferred(variants: Sequence[str]) -> str:
    for marker in ("q4_k_m", "q4_k", "q5_k_m", "q4_0", "q8_0"):
        for name in variants:
            if marker in name.lower():
                return name
    return variants[len(variants) // 2]


def snapshot(
    repository: str,
    revision: str | None = None,
    *,
    source: str | None = None,
    policy: TransferPolicy | None = None,
    token: str | None = None,
) -> HubSnapshot:
    """Pin a public repository to one commit and list every file's identity."""
    repository_id(repository)
    if revision is not None and not _REVISION.fullmatch(revision):
        raise PreservationError("hub_revision_invalid")
    relative = "api/models/" + quote(repository, safe="/")
    if revision is not None:
        relative += "/revision/" + revision
    parsed = _json(
        _read(source, relative + "?blobs=true", MAX_INFO_BYTES, policy, token)
    )
    if not isinstance(parsed, dict):
        raise PreservationError("hub_metadata_invalid")
    info = cast(dict[str, object], parsed)
    pinned = info.get("sha")
    if not isinstance(pinned, str) or not _REVISION.fullmatch(pinned):
        raise PreservationError("hub_metadata_invalid")
    if revision is not None and pinned != revision:
        raise PreservationError("hub_revision_mismatch")
    if info.get("private") is True:
        raise PreservationError("hub_access_required")
    siblings = info.get("siblings")
    if not isinstance(siblings, list) or len(cast(list[object], siblings)) > MAX_FILES:
        raise PreservationError("hub_metadata_invalid")
    files: list[HubFile] = []
    seen: set[str] = set()
    for raw in cast(list[object], siblings):
        if not isinstance(raw, dict):
            raise PreservationError("hub_metadata_invalid")
        entry = cast(dict[str, object], raw)
        path = _canonical_path(entry.get("rfilename"))
        if path in seen:
            raise PreservationError("hub_metadata_invalid")
        seen.add(path)
        size = entry.get("size")
        lfs = entry.get("lfs")
        sha256 = None
        if isinstance(lfs, dict):
            lfs_entry = cast(dict[str, object], lfs)
            candidate = lfs_entry.get("sha256")
            lfs_size = lfs_entry.get("size")
            if not isinstance(candidate, str) or not _SHA256.fullmatch(candidate):
                raise PreservationError("hub_metadata_invalid")
            sha256 = candidate
            if type(lfs_size) is int:
                size = lfs_size
        blob = entry.get("blobId")
        blob_id = blob if isinstance(blob, str) and _BLOB.fullmatch(blob) else None
        if type(size) is not int or size < 0:
            raise PreservationError("hub_metadata_invalid")
        if sha256 is None and blob_id is None:
            raise PreservationError("hub_metadata_invalid")
        files.append(HubFile(path, size, sha256, blob_id))
    card = info.get("cardData")
    licence = None
    if isinstance(card, dict):
        card_data = cast(dict[str, object], card)
        value = card_data.get("license")
        if value == "other" and isinstance(card_data.get("license_name"), str):
            value = card_data.get("license_name")
        if isinstance(value, list) and value:
            value = cast(list[object], value)[0]
        if isinstance(value, str) and re.fullmatch(
            r"[a-z0-9][a-z0-9.+_ -]{0,119}", value.strip().lower()
        ):
            licence = value.strip().lower()
    task, library = info.get("pipeline_tag"), info.get("library_name")
    return HubSnapshot(
        repository=repository,
        revision=pinned,
        files=tuple(sorted(files, key=lambda item: item.path)),
        licence=licence,
        gated=info.get("gated") not in (None, False),
        task=task if isinstance(task, str) and len(task) <= 80 else None,
        library=library if isinstance(library, str) and len(library) <= 80 else None,
    )


def git_blob_id(data: bytes) -> str:
    """Git's object ID for a blob: SHA-1 over a typed, sized header."""
    return hashlib.sha1(  # noqa: S324 - git object identity, not a security hash
        b"blob " + str(len(data)).encode() + b"\0" + data
    ).hexdigest()


def exact_hashes(
    hub: HubSnapshot,
    paths: Iterable[str],
    control: OperationControl,
    *,
    source: str | None = None,
    policy: TransferPolicy | None = None,
    token: str | None = None,
) -> dict[str, tuple[int, str]]:
    """Give every selected file an exact size and SHA-256.

    Small git files are fetched from the pinned commit and must match their
    git blob ID, so the hash is the commit's content, not whatever a later
    request returns.
    """
    exact: dict[str, tuple[int, str]] = {}
    fetched = 0
    for path in sorted(set(paths)):
        control.checkpoint()
        item = hub.file(path)
        if item.sha256 is not None:
            exact[path] = (item.size_bytes, item.sha256)
            continue
        if (
            item.size_bytes > MAX_SMALL_FILE
            or fetched + item.size_bytes > MAX_SMALL_TOTAL
        ):
            raise PreservationError("hub_small_files_too_large")
        relative = (
            quote(hub.repository, safe="/")
            + "/raw/"
            + hub.revision
            + "/"
            + quote(path, safe="/")
        )
        data = _read(source, relative, item.size_bytes, policy, token)
        if len(data) != item.size_bytes or git_blob_id(data) != item.blob_id:
            raise PreservationError("hub_file_changed")
        fetched += len(data)
        exact[path] = (item.size_bytes, hashlib.sha256(data).hexdigest())
    return exact


# A Hugging Face access token ------------------------------------------------
#
# Optional. It raises Hugging Face's rate limits and opens gated models the
# person was granted. It is kept in the OS keychain, sent only to
# huggingface.co itself (never across redirects, never to mirrors), and never
# written to plans, logs or errors.

TOKEN_SERVICE = "sovereign-huggingface"  # noqa: S105 - a keychain service name
TOKEN_ACCOUNT = "token"  # noqa: S105 - a keychain account name, not a secret
_TOKEN = re.compile(r"hf_[A-Za-z0-9]{20,200}")


def token_valid(token: str) -> bool:
    """Whether a string has the shape of a Hugging Face user access token."""
    return bool(_TOKEN.fullmatch(token))


def whoami(
    token: str, *, source: str | None = None, policy: TransferPolicy | None = None
) -> str:
    """Return the account name a token belongs to; refused tokens raise."""
    if not token_valid(token):
        raise PreservationError("hub_token_invalid")
    try:
        parsed = _json(_read(source, "api/whoami-v2", 256 * 1024, policy, token))
    except PreservationError as error:
        if str(error) == "hub_access_required":
            raise PreservationError("hub_token_invalid") from None
        raise
    name = (
        cast(dict[str, object], parsed).get("name")
        if isinstance(parsed, dict)
        else None
    )
    if not isinstance(name, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", name
    ):
        raise PreservationError("hub_metadata_invalid")
    return name


def save_token(token: str) -> None:
    """Keep a token in the OS keychain and read it back."""
    if not token_valid(token):
        raise PreservationError("hub_token_invalid")
    save_secret(
        TOKEN_SERVICE,
        TOKEN_ACCOUNT,
        token,
        label="Sovereign Hugging Face token",
        error="hub_keychain_unavailable",
    )
    if load_token() != token:
        raise PreservationError("hub_keychain_unavailable")


def load_token() -> str:
    """Read the stored token from the OS keychain."""
    token = load_secret(TOKEN_SERVICE, TOKEN_ACCOUNT, error="hub_keychain_unavailable")
    if not token_valid(token):
        raise PreservationError("hub_keychain_unavailable")
    return token


def delete_token() -> None:
    """Remove the stored token; an absent token is already removed.

    A locked or unavailable keychain raises, so the person can retry rather
    than be told a token was removed that is still stored.
    """
    delete_secret(
        TOKEN_SERVICE, TOKEN_ACCOUNT, error="hub_keychain_unavailable", strict=True
    )
