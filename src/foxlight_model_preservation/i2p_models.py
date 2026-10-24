"""Unsigned, bounded I2P routing records; model authority stays in original DSSE."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from .models import MAX_METADATA_BYTES, Digest, FileIdentity, PreservationError, Record


class I2PContact(Record):
    """An I2P destination and DHT port, never an IP, URL, signer or trust grant."""

    destination: str = Field(
        min_length=500, max_length=2048, pattern=r"^[A-Za-z0-9~=-]+$"
    )
    port: int = Field(ge=1, le=65534)


class I2POffer(Record):
    """Offline handoff for one original signed recovery kit in an I2P-only swarm."""

    schema_version: Literal["foxlight.i2p-kit-offer.v1"] = "foxlight.i2p-kit-offer.v1"
    manifest_id: Digest
    envelope_base64: str = Field(max_length=MAX_METADATA_BYTES * 2)
    kit: FileIdentity
    metainfo_base64: str = Field(max_length=MAX_METADATA_BYTES * 2)
    infohash: str = Field(pattern=r"^[0-9a-f]{40}$")
    contacts: tuple[I2PContact, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def exact_kit_name(self) -> Self:
        """Use one non-extracting canonical file; arbitrary torrent paths refuse."""
        if self.kit.path != self.manifest_id + ".foxkit" or not self.kit.size:
            raise PreservationError("I2P offer must name its exact recovery kit")
        return self


class I2PProfile(Record):
    """Explicit local software/router choices; initializes no network or serving."""

    schema_version: Literal["foxlight.i2p-profile.v1"] = "foxlight.i2p-profile.v1"
    runtime: str
    runtime_id: Digest
    java: str
    java_sha256: Digest
    router_port: int = Field(ge=1, le=65535)
    upload_kib: int = Field(default=32, ge=5, le=1024)
    download_kib: int = Field(default=256, ge=5, le=10240)


class I2PRuntime(Record):
    """Explicitly pinned local build inventory, separate from model provenance."""

    schema_version: Literal["foxlight.i2p-runtime.v1"]
    i2p_version: Literal["2.14.0"]
    i2p_revision: Literal["3c98503c5f14efe7bdd783528ba7f621f12d8081"]
    rpc_revision: Literal["9fa6d9bc601f9ea6cfd18ce1e365bee989067665"]
    launcher_sha256: Digest
    rpc_patched_sha256: Digest
    files: dict[str, Digest]


class I2PStatus(Record):
    """Private native status; exporting an I2P contact is an explicit choice."""

    rpc_port: int = Field(ge=1, le=65535)
    pid: int = Field(ge=1)
    connected: bool
    dht_nodes: int = Field(ge=0)
    destination: str | None
    dht_port: int = Field(ge=0, le=65534)
