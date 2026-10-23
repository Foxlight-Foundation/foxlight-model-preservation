"""One-file connection hints around unchanged, independently trusted references."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .invitations import Invitation, read_invitation
from .models import MAX_METADATA_BYTES, PreservationError, Record
from .peer import PeerSource, inspect_peer_source
from .peer_invitations import PeerInvitation, read_peer_invitation
from .peer_runtime import PeerClient, bootstrap_address
from .signing import canonical_bytes, digest, encode_record, strict_json
from .storage import directory

Reference = Annotated[
    Invitation | PeerInvitation | PeerSource, Field(discriminator="schema_version")
]


class Handoff(Record):
    """Portable routing suggestions; no trust, network, serving or code grant.

    Inner model/source identities and original signed payloads are unchanged.
    The wrapper identity includes unsigned contacts, not a publisher attestation.
    Private contacts still require the recipient's separate local permission.
    """

    schema_version: Literal["foxlight.model-preservation.handoff.v1"] = (
        "foxlight.model-preservation.handoff.v1"
    )
    reference: Reference
    contacts: tuple[str, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def validate_hints(self) -> Handoff:
        """Check canonical bounded hints offline, without importing permissions."""
        if len(set(self.contacts)) != len(self.contacts):
            raise PreservationError("duplicate handoff contact")
        for contact in self.contacts:
            bootstrap_address(contact, allow_private=True)
        if isinstance(self.reference, Invitation):
            if self.contacts:
                raise PreservationError("HTTP handoff cannot grant peer contacts")
        elif not self.contacts:
            raise PreservationError("peer handoff needs a starting contact")
        bound = MAX_METADATA_BYTES
        if isinstance(self.reference, PeerSource):
            inspect_peer_source(encode_record(self.reference))
            bound = 256 * 1024
        if len(encode_record(self)) > bound:
            raise PreservationError("handoff exceeds its metadata bound")
        return self


def read_handoff(data: bytes) -> Handoff:
    """Inspect canonical typed metadata offline; candidate keys stay candidates."""
    try:
        return Handoff.model_validate_json(canonical_bytes(strict_json(data)))
    except ValueError as error:
        raise PreservationError("invalid or unsafe model/source handoff") from error


def read_reference(data: bytes) -> Invitation | PeerInvitation | PeerSource:
    """Read a new wrapper or a legacy reference without changing its identity."""
    value = strict_json(data)
    if not isinstance(value, dict):
        raise PreservationError("typed model/source reference required")
    version = value.get("schema_version")
    if version == "foxlight.model-preservation.handoff.v1":
        return read_handoff(data).reference
    if version == "foxlight.model-preservation.peer-invitation.v1":
        return read_peer_invitation(data)
    if version == "foxlight.peer-source.v1":
        return inspect_peer_source(data)
    return read_invitation(data)


def reference_contacts(data: bytes) -> tuple[str, ...]:
    """Read optional routing suggestions without saving settings or connecting."""
    value = strict_json(data)
    if isinstance(value, dict) and value.get("schema_version") == (
        "foxlight.model-preservation.handoff.v1"
    ):
        return read_handoff(data).contacts
    read_reference(data)
    return ()


def handoff_identity(handoff: Handoff) -> str:
    """Identify the whole routing wrapper separately from signed content."""
    return digest(encode_record(read_handoff(encode_record(handoff))))


def write_handoff(destination: Path, handoff: Handoff) -> None:
    """Publish one complete metadata file atomically, without replacing a file.

    No signing key or weights are copied. A flush failure after publication can
    leave a complete file; inspect it before choosing another destination.
    """
    data = encode_record(read_handoff(encode_record(handoff)))
    parent = destination.absolute().parent
    with directory(parent) as root:
        with tempfile.TemporaryDirectory(prefix=".handoff-", dir=parent) as staging:
            path = Path(staging) / "reference"
            with path.open("xb") as output:
                os.chmod(path, 0o600)
                _ = output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.link(path, destination.name, dst_dir_fd=root, follow_symlinks=False)
            os.fsync(root)


def peer_contacts(
    client: PeerClient, bootstrap: tuple[str, ...] = ()
) -> tuple[str, ...]:
    """Read own numeric addresses and explicit fallback contacts, not an inventory.

    No reachability is asserted. Public-only profiles never export private
    addresses; no DNS, relay, gateway or delegated routing hint is accepted.
    """
    record = strict_json(client.command("id", maximum=65536))
    addresses = record.get("Addresses") if isinstance(record, dict) else None
    if not isinstance(addresses, list):
        raise PreservationError("owned peer addresses unavailable")
    own: list[str] = []
    for address in addresses:
        if not isinstance(address, str):
            continue
        try:
            valid = bootstrap_address(address, client.profile.allow_private_peers)
        except ValueError:
            continue
        if valid not in own:
            own.append(valid)
    fallbacks = tuple(
        bootstrap_address(item, client.profile.allow_private_peers)
        for item in bootstrap
    )
    # Retain fallback routing even on machines with many interface addresses.
    contacts = tuple(dict.fromkeys((*own[:4], *fallbacks, *own[4:])))[:8]
    if not contacts:
        raise PreservationError(
            "no usable peer contact; configure a reachable route "
            "or explicit private mode"
        )
    return contacts
