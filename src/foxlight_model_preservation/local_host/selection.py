"""Conservative, exact registry selections for standalone private preservation."""

from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path
from typing import cast
from urllib.parse import quote, urljoin, urlsplit

from pydantic import JsonValue

from ..assurance import retain_card
from ..cards import ArtifactFile, ModelCardEnvelope, identity_json
from ..models import Intake, PreservationError, Recipe, RecipeMember, SourceFile
from ..operations import OperationControl
from ..registry_proof import RegistryProof
from ..transfer import PinnedConnection, TransferPolicy

NOTICE = (
    b"Private owner capture. Registry publication and signed file hashes "
    b"do not establish runtime qualification or redistribution rights. "
    b"Original cards and registry proof are retained.\n"
)
NOTICE_PATH = ".sovereign-capture-notice.txt"


def registry_recipe(card: ModelCardEnvelope, proof: RegistryProof) -> Recipe:
    """Keep the whole original card and all understood, hash-bound companions.

    A missing file map or unsupported external companion refuses the entire
    selection. Nothing silently reduces a multimodal model to its base weights.
    """
    bundle = card.artifact.bundle
    if bundle is None:
        raise PreservationError("complete_signed_file_map_required")
    for section in ("vision", "video", "audio"):
        if card.card.get(section):
            raise PreservationError("companion_adapter_required")
    runtime = card.card.get("runtime")
    if isinstance(runtime, dict) and any(
        key.endswith(("_repo", "_repository")) and value is not None
        for key, value in runtime.items()
    ):
        raise PreservationError("companion_adapter_required")
    groups: dict[tuple[str, str], tuple[str, dict[str, ArtifactFile]]] = {
        (card.artifact.repository, card.artifact.revision): (
            "base",
            {file.path: file for file in bundle.files},
        )
    }
    image = card.card.get("image")
    if image is not None:
        if not isinstance(image, dict) or not isinstance(image.get("components"), list):
            raise PreservationError("companion_adapter_required")
        for component in cast(list[JsonValue], image["components"]):
            if not isinstance(component, dict):
                raise PreservationError("companion_adapter_required")
            repo, revision, selected = (
                component.get("repository"),
                component.get("revision"),
                component.get("files"),
            )
            if (
                not isinstance(repo, str)
                or not isinstance(revision, str)
                or not isinstance(selected, list)
            ):
                raise PreservationError("companion_adapter_required")
            role = str(component.get("role", "companion"))
            _, mapped = groups.setdefault((repo, revision), (role, {}))
            for item in selected:
                file = ArtifactFile.model_validate_json(identity_json(item))
                previous = mapped.setdefault(file.path, file)
                if previous != file:
                    raise PreservationError("conflicting companion file identities")
    members: list[RecipeMember] = []
    names = ["base", *(f"companion-{index}" for index in range(1, len(groups)))]
    for index, ((repository, revision), (role, mapped)) in enumerate(groups.items()):
        if not mapped or any(
            not file.object_id or not file.object_id.startswith("sha256:")
            for file in mapped.values()
        ):
            raise PreservationError("complete_signed_file_map_required")
        # Validate every repository before URL construction.
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise PreservationError("unsupported artifact origin")
        if NOTICE_PATH in mapped:
            raise PreservationError("capture notice collides with selected files")
        members.append(
            RecipeMember(
                member_id=names[index],
                role=role,
                format=card.artifact.format if index == 0 else "companion",
                origin_repository=repository,
                origin_revision=revision,
                artifact_identity=bundle.bundle_id
                if index == 0
                else card.card_id + ":" + names[index],
                loader_root=bundle.root if index == 0 else None,
                dependencies=tuple(names[1:]) if index == 0 else (),
                retained_card=retain_card(card, registry_proof=proof).model_dump(
                    mode="json"
                )
                if index == 0
                else None,
                intake=Intake(
                    audience="private",
                    license_id="see-original-card",
                    terms_reference=f"https://huggingface.co/{repository}/tree/{revision}",
                    decision="Explicit private preservation; no redistribution grant.",
                    notice_paths=(NOTICE_PATH,),
                ),
                files=(
                    *tuple(
                        SourceFile(
                            path=file.path,
                            expected_sha256=file.object_id.removeprefix("sha256:"),
                        )
                        for file in mapped.values()
                        if file.object_id
                    ),
                    SourceFile(
                        path=NOTICE_PATH,
                        expected_sha256=hashlib.sha256(NOTICE).hexdigest(),
                    ),
                ),
            )
        )
    tasks = card.card.get("tasks")
    return Recipe(
        schema_version="foxlight.model-preservation.v1",
        name=card.alias[:200],
        root_member="base",
        members=tuple(members),
        workloads=tuple(str(value) for value in tasks)
        if isinstance(tasks, list) and tasks
        else ("model",),
        runtime_requirements=(
            "Runtime compatibility and code authorization are not assessed.",
        ),
    )


def download_file(
    repository: str,
    revision: str,
    file: ArtifactFile,
    destination: Path,
    control: OperationControl,
    *,
    deadline: float,
) -> None:
    """Stream one pinned, hash-bound Hub file over public HTTPS, then fsync it.

    Hub CDNs use redirects. Every hop is separately DNS-pinned, public-address
    only, HTTPS and credential-free; headers and ambient proxies are not forwarded.
    The original signed length/hash remain authoritative across redirects.
    """
    if not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository
    ) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise PreservationError("an immutable repository/revision is required")
    url = f"https://huggingface.co/{repository}/resolve/{revision}/" + quote(
        file.path, safe="/"
    )
    for _hop in range(6):
        control.checkpoint()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PreservationError("download deadline exceeded")
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.fragment
        ):
            raise PreservationError("unsafe model download redirect")
        connection = PinnedConnection(
            parts.hostname,
            parts.port or 443,
            TransferPolicy(timeout=min(15, remaining)),
            tls=True,
        )
        try:
            connection.request(
                "GET",
                parts.path + ("?" + parts.query if parts.query else ""),
                headers={
                    "User-Agent": "Foxlight-Sovereign/0.21",
                    "Accept-Encoding": "identity",
                },
            )
            stream = connection.getresponse()
            if stream.status in {301, 302, 303, 307, 308}:
                location = stream.getheader("Location")
                if not location or len(location) > 8192:
                    raise PreservationError("invalid model download redirect")
                url = urljoin(url, location)
                continue
            if (
                stream.status != 200
                or stream.getheader("Content-Encoding", "identity") != "identity"
            ):
                raise PreservationError("model source unavailable")
            if stream.getheader("Content-Length") not in {None, str(file.size_bytes)}:
                raise PreservationError("model source length changed")
            sha, count = hashlib.sha256(), 0
            from ..storage import directory

            with directory(destination.parent, create=True) as parent:
                descriptor = os.open(
                    destination.name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent,
                )
            with os.fdopen(descriptor, "wb") as output:
                while chunk := stream.read(
                    min(256 * 1024, file.size_bytes - count + 1)
                ):
                    control.checkpoint()
                    if time.monotonic() >= deadline:
                        raise PreservationError("download deadline exceeded")
                    count += len(chunk)
                    if count > file.size_bytes:
                        raise PreservationError("model source exceeds selected length")
                    control.advance(received=len(chunk))
                    sha.update(chunk)
                    output.write(chunk)
                if (
                    count != file.size_bytes
                    or file.object_id != "sha256:" + sha.hexdigest()
                ):
                    raise PreservationError(
                        "model bytes differ from signed file identity"
                    )
                output.flush()
                os.fsync(output.fileno())
            return
        finally:
            connection.close()
    raise PreservationError("model download redirect limit exceeded")
