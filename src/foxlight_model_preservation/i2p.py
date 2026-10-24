"""Standalone I2P-only recovery-kit offers, verified receiving and opt-in hosting."""

from __future__ import annotations

import base64
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from pydantic import JsonValue

from .archives import ArchiveManifest, verify_archive_manifest
from .i2p_models import I2PContact, I2POffer
from .i2p_runtime import SnarkRPC, native_status, owned_snark
from .kits import export_kit, import_kit
from .models import MAX_METADATA_BYTES, PreservationError, TrustProfile
from .operations import OperationControl
from .pmf import new_pmf_tree
from .signing import canonical_bytes, decode_base64, encode_record, strict_json
from .storage import (
    atomic_bytes,
    copy_exact,
    directory,
    file_identity,
    manifest_path,
    open_file,
    parent_directory,
    read_metadata,
    writer_lock,
)
from .torrent_binding import torrent_engine


def read_offer(data: bytes) -> I2POffer:
    """Parse bounded local routing metadata without keys, network or execution."""
    return I2POffer.model_validate_json(canonical_bytes(strict_json(data)))


def check_metainfo(offer: I2POffer) -> bytes:
    """Check a single v1 kit torrent offline; refuse every non-I2P network hint."""
    data = decode_base64(offer.metainfo_base64)
    if len(data) > MAX_METADATA_BYTES:
        raise PreservationError("I2P metainfo exceeds its bound")
    engine = torrent_engine()
    try:
        decoded_value = cast(object, engine.bdecode(data))
        if not isinstance(decoded_value, dict):
            raise PreservationError("I2P metainfo must be a dictionary")
        decoded = cast(dict[bytes, object], decoded_value)
        if engine.bencode(decoded) != data:
            raise PreservationError("I2P metainfo must have one canonical encoding")
        if any(key in decoded for key in (b"url-list", b"httpseeds", b"nodes")):
            raise PreservationError(
                "I2P offers refuse web seeds and embedded peer addresses"
            )
        trackers: list[object] = []
        if b"announce" in decoded:
            trackers.append(decoded[b"announce"])
        tiers = decoded.get(b"announce-list", [])
        if not isinstance(tiers, list) or len(cast(list[object], tiers)) > 16:
            raise PreservationError("I2P tracker list exceeds its bound")
        for tier in cast(list[object], tiers):
            if not isinstance(tier, list):
                raise PreservationError("invalid I2P tracker tier")
            trackers.extend(cast(list[object], tier))
        if len(trackers) > 32:
            raise PreservationError("I2P tracker list exceeds its bound")
        for tracker in trackers:
            if not isinstance(tracker, bytes):
                raise PreservationError("invalid I2P tracker URL")
            value = tracker.decode("ascii")
            parsed = urlsplit(value)
            host = parsed.hostname or ""
            label = host.removesuffix(".b32.i2p")
            if (
                parsed.scheme != "http"
                or len(label) != 52
                or not host.endswith(".b32.i2p")
                or any(c not in "abcdefghijklmnopqrstuvwxyz234567" for c in label)
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port is not None
                or parsed.query
                or parsed.fragment
                or any(ord(c) < 33 or c == "\\" for c in value)
            ):
                raise PreservationError(
                    "only credential-free I2P b32 tracker URLs are accepted"
                )
        info_value = decoded.get(b"info")
        if not isinstance(info_value, dict):
            raise PreservationError("I2P torrent has no info dictionary")
        info = cast(dict[bytes, object], info_value)
        if (
            set(info) - {b"name", b"length", b"piece length", b"pieces", b"private"}
            or info.get(b"name") != offer.kit.path.encode()
            or type(info.get(b"length")) is not int
            or info[b"length"] != offer.kit.size
            or info.get(b"private", 0) != 0
        ):
            raise PreservationError(
                "I2P torrent differs from its single exact recovery kit"
            )
        native = engine.torrent_info(decoded)
        hashes, inventory = native.info_hashes(), native.files()
        if (
            not hashes.has_v1()
            or str(hashes.v1) != offer.infohash
            or inventory.num_files() != 1
            or inventory.file_path(0) != offer.kit.path
            or inventory.file_size(0) != offer.kit.size
        ):
            raise PreservationError("I2P torrent routing identity differs")
    except (RuntimeError, TypeError, UnicodeError, OverflowError) as error:
        raise PreservationError("invalid I2P torrent metainfo") from error
    return data


def authenticate_offer(
    offer: I2POffer, trust: TrustProfile, expected_manifest_id: str
) -> ArchiveManifest:
    """Authenticate original model authority before any native network effects."""
    offer = read_offer(encode_record(offer))
    if offer.manifest_id != expected_manifest_id:
        raise PreservationError(
            "I2P offer manifest differs from the independent selection"
        )
    manifest = verify_archive_manifest(
        decode_base64(offer.envelope_base64), trust, expected_manifest_id
    )
    if any(m.intake.audience != "public" for m in manifest.members):
        raise PreservationError(
            "I2P swarm sharing requires public intake for every member"
        )
    check_metainfo(offer)
    return manifest


def export_i2p(
    store: Path,
    manifest_id: str,
    trust: TrustProfile,
    destination: Path,
    *,
    contacts: tuple[I2PContact, ...] = (),
    control: OperationControl | None = None,
) -> I2POffer:
    """Export one unchanged public legacy/PMF kit and an unsigned I2P-only offer.

    This is offline. It does not install native software, operate a router, enroll
    keys or start sharing. The original kit/envelope and provenance stay intact.
    """
    with (control or OperationControl()).operation("i2p_export") as active:
        with directory(store) as source:
            envelope = read_metadata(source, manifest_path(manifest_id))
        manifest = verify_archive_manifest(envelope, trust, manifest_id)
        if any(m.intake.audience != "public" for m in manifest.members):
            raise PreservationError(
                "I2P swarm export requires public intake for every member"
            )
        with new_pmf_tree(destination, active) as target:
            # An independent staged file is needed by the maintained piece hasher.
            with tempfile.TemporaryDirectory(
                prefix=".i2p-kit-", dir=destination.parent
            ) as temporary:
                kit = Path(temporary) / (manifest_id + ".foxkit")
                export_kit(
                    store,
                    manifest_id,
                    trust,
                    kit,
                    expected_envelope=envelope,
                    control=active.fork(),
                )
                engine = torrent_engine()
                inventory = engine.file_storage()
                inventory.add_file(kit.name, kit.stat().st_size)
                creator = engine.create_torrent(
                    inventory, 1024**2, engine.create_torrent.v1_only
                )
                engine.set_piece_hashes(
                    creator, str(kit.parent), lambda _index: active.checkpoint()
                )
                metainfo = engine.bencode(creator.generate())
                with directory(kit.parent) as parent:
                    identity = file_identity(parent, kit.name, control=active)
                offer = I2POffer(
                    manifest_id=manifest_id,
                    envelope_base64=base64.b64encode(envelope).decode(),
                    kit=identity,
                    metainfo_base64=base64.b64encode(metainfo).decode(),
                    infohash=str(
                        engine.torrent_info(engine.bdecode(metainfo)).info_hashes().v1
                    ),
                    contacts=contacts,
                )
                check_metainfo(offer)
                encoded = encode_record(offer)
                if len(encoded) > MAX_METADATA_BYTES:
                    raise PreservationError("I2P offer exceeds its metadata bound")
                # Copy the staged kit using the existing exact-byte descriptor boundary.
                with (
                    directory(kit.parent) as parent,
                    open_file(parent, kit.name) as stream,
                ):
                    with parent_directory(target, kit.name, create=True) as (
                        parent_fd,
                        name,
                    ):
                        descriptor = os.open(
                            name,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600,
                            dir_fd=parent_fd,
                        )
                        with os.fdopen(descriptor, "wb") as output:
                            copy_exact(stream, output, identity.size, control=active)
                            output.flush()
                            os.fsync(output.fileno())
                    if (
                        file_identity(target, kit.name, identity.size, control=active)
                        != identity
                    ):
                        raise PreservationError(
                            "staged I2P kit changed during publication"
                        )
                atomic_bytes(target, "offer.json", encoded)
                atomic_bytes(target, "model.torrent", metainfo)
        return offer


def _torrent(client: SnarkRPC, infohash: str) -> dict[str, JsonValue] | None:
    result = client.call(
        "torrent-get",
        {
            "fields": [
                "hashString",
                "downloadDir",
                "files",
                "status",
                "percentDone",
                "error",
                "downloadedEver",
                "uploadedEver",
            ],
        },
    )
    torrents = result.get("torrents")
    if not isinstance(torrents, list) or len(torrents) > 1:
        raise PreservationError("I2P torrent selection is ambiguous")
    if not torrents:
        return None
    value = torrents[0]
    if not isinstance(value, dict) or value.get("hashString") != infohash:
        raise PreservationError("I2P torrent identity differs")
    return value


def _cache_hosting_kit(
    source: Path, data: Path, offer: I2POffer, active: OperationControl
) -> None:
    with (
        directory(source.parent) as origin,
        open_file(origin, source.name) as stream,
        directory(data) as target,
    ):
        pending = ".hosting-" + uuid.uuid4().hex
        descriptor = os.open(
            pending,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=target,
        )
        try:
            with os.fdopen(descriptor, "wb") as output:
                copy_exact(stream, output, offer.kit.size, control=active)
                output.flush()
                os.fsync(output.fileno())
            if (
                file_identity(target, pending, offer.kit.size, control=active).sha256
                != offer.kit.sha256
            ):
                raise PreservationError("hosting kit changed during copy")
            os.link(
                pending,
                offer.kit.path,
                src_dir_fd=target,
                dst_dir_fd=target,
                follow_symlinks=False,
            )
            os.fsync(target)
        finally:
            os.unlink(pending, dir_fd=target)


def transfer_i2p(
    offer: I2POffer,
    profile: Path,
    trust: TrustProfile,
    expected_manifest_id: str,
    *,
    allow_i2p: bool = False,
    allow_sharing: bool = False,
    seconds: int = 3600,
    max_bytes: int = 20 * 1024**3,
    destination: Path | None = None,
    kit_source: Path | None = None,
    control: OperationControl | None = None,
) -> dict[str, JsonValue]:
    """Explicitly receive or host one verified kit through an owned I2P session.

    Receiving may upload and requires separate sharing consent. Host only when
    kit_source is explicitly selected; destination is required for receiving.
    Stop/loss leaves local files intact. Every receive reauthenticates the offer
    and completes independent kit verification before publishing the archive.
    """
    with (control or OperationControl()).operation(
        "i2p_receive" if kit_source is None else "i2p_share"
    ) as active:
        _ = authenticate_offer(offer, trust, expected_manifest_id)
        if not allow_i2p or not allow_sharing:
            raise PreservationError(
                "I2P participation and payload sharing need separate explicit grants"
            )
        if type(seconds) is not int or not 1 <= seconds <= 86400:
            raise PreservationError("I2P sharing requires a finite lifetime")
        if (
            type(max_bytes) is not int
            or not 0 < max_bytes <= 2**53 - 1
            or offer.kit.size > max_bytes
        ):
            raise PreservationError("I2P kit exceeds its explicit byte allowance")
        if kit_source is None and (
            destination is None or destination.exists() or destination.is_symlink()
        ):
            raise PreservationError("I2P receiving requires a new archive destination")
        with directory(profile) as profile_fd, writer_lock(profile_fd):
            return _transfer_locked(
                offer,
                profile,
                trust,
                expected_manifest_id,
                allow_i2p=allow_i2p,
                allow_sharing=allow_sharing,
                seconds=seconds,
                destination=destination,
                kit_source=kit_source,
                active=active,
            )


def _transfer_locked(
    offer: I2POffer,
    profile: Path,
    trust: TrustProfile,
    expected_manifest_id: str,
    *,
    allow_i2p: bool,
    allow_sharing: bool,
    seconds: int,
    destination: Path | None,
    kit_source: Path | None,
    active: OperationControl,
) -> dict[str, JsonValue]:
    data = profile.absolute() / "downloads"
    data.mkdir(mode=0o700, exist_ok=True)
    with directory(data):
        pass
    if shutil.disk_usage(data).free < 2 * offer.kit.size + 16 * 1024**2:
        raise PreservationError(
            "insufficient disk space for I2P kit and verified archive"
        )
    with directory(profile) as root:
        encoded = canonical_bytes(
            cast(
                JsonValue,
                {
                    "manifest_id": offer.manifest_id,
                    "envelope_base64": offer.envelope_base64,
                    "kit": offer.kit.model_dump(mode="json"),
                    "infohash": offer.infohash,
                },
            )
        )
        try:
            previous = read_metadata(root, "selection.json")
        except FileNotFoundError:
            atomic_bytes(root, "selection.json", encoded)
        else:
            if previous != encoded:
                raise PreservationError(
                    "I2P profile belongs to another immutable selection"
                )
    if kit_source is not None:
        with directory(kit_source.parent) as source:
            if (
                file_identity(
                    source, kit_source.name, offer.kit.size, control=active
                ).sha256
                != offer.kit.sha256
            ):
                raise PreservationError("selected hosting kit differs")
        # Verify its original signed closure before admitting public hosting.
        with tempfile.TemporaryDirectory(
            prefix=".i2p-host-verify-", dir=profile
        ) as temporary:
            import_kit(
                kit_source,
                Path(temporary) / "archive",
                trust=trust,
                expected_manifest_id=expected_manifest_id,
                expected_envelope=decode_base64(offer.envelope_base64),
                control=active.fork(),
            )
        target = data / offer.kit.path
        if target.exists():
            with directory(data) as root:
                if file_identity(root, offer.kit.path, control=active) != offer.kit:
                    raise PreservationError("cached hosting kit differs")
        else:
            _cache_hosting_kit(kit_source, data, offer, active)
    end = time.monotonic() + seconds
    active.phase("sharing" if kit_source else "downloading")
    with owned_snark(
        profile,
        allow_i2p=allow_i2p,
        allow_sharing=allow_sharing,
        seconds=seconds,
        contacts=offer.contacts,
        control=active,
    ) as client:
        existing = _torrent(client, offer.infohash)
        if existing is None and (data / (offer.kit.path + ".torrent")).exists():
            # The native directory monitor loads retained torrents asynchronously.
            # Never overwrite/remove that operator-owned resume metadata.
            ready = min(end, time.monotonic() + 10)
            while existing is None and time.monotonic() < ready:
                active.checkpoint()
                time.sleep(0.1)
                existing = _torrent(client, offer.infohash)
            if existing is None:
                raise PreservationError("retained I2P torrent did not become available")
        if existing is None:
            added = client.call(
                "torrent-add",
                {
                    "metainfo": offer.metainfo_base64,
                    "download-dir": str(data),
                    "paused": True,
                },
            )
            result = added.get("torrent-added")
            if (
                not isinstance(result, dict)
                or result.get("hashString") != offer.infohash
            ):
                raise PreservationError(
                    "owned I2P client did not add the exact torrent"
                )
        client.call("torrent-start", {"ids": [offer.infohash]})
        was_connected = False
        while time.monotonic() < end:
            active.checkpoint()
            connected = native_status(profile).connected
            if was_connected and not connected:
                raise PreservationError(
                    "I2P router connection lost; retained files can resume"
                )
            was_connected = was_connected or connected
            observed = _torrent(client, offer.infohash)
            if (
                observed is None
                or observed.get("downloadDir") != str(data / offer.kit.path)
                or observed.get("error") not in (None, 0)
            ):
                raise PreservationError(
                    "owned I2P transfer failed or changed selection"
                )
            if kit_source is None and observed.get("percentDone") == 1:
                with directory(data) as root:
                    if file_identity(root, offer.kit.path, control=active) != offer.kit:
                        raise PreservationError(
                            "received I2P kit hash or length differs"
                        )
                break
            time.sleep(0.2)
        else:
            if kit_source is None:
                raise PreservationError(
                    "I2P transfer deadline exceeded; retained partials can resume"
                )
    if kit_source is None:
        if destination is None:
            raise PreservationError("missing I2P receive destination")
        import_kit(
            data / offer.kit.path,
            destination,
            trust=trust,
            expected_manifest_id=expected_manifest_id,
            expected_envelope=decode_base64(offer.envelope_base64),
            control=active.fork(),
        )
        active.mark_committed()
    return {
        "manifest_id": expected_manifest_id,
        "verification": "complete",
        "network": "i2p-only",
        "payload_sharing_consented": True,
        "stopped": True,
        "retained_inputs_deleted": False,
        "registry_authority_granted": False,
        "privacy_qualified": False,
    }
