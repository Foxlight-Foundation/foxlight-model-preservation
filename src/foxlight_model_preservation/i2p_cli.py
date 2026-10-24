"""Explicit standalone I2P profile, kit export, receive and sharing commands."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from pydantic import JsonValue

from .i2p import authenticate_offer, export_i2p, read_offer, transfer_i2p
from .i2p_models import I2PContact
from .i2p_runtime import initialize_i2p, native_status, read_profile
from .mirror_native import write_reference
from .models import MAX_METADATA_BYTES, PreservationError
from .operations import OperationControl
from .signing import canonical_bytes, encode_record, load_trust, strict_json


class I2PArguments(argparse.Namespace):
    """Only named local/native effects; no implicit sharing or router installation."""

    i2p_action: str
    i2p_dir: Path
    runtime: Path
    expected_runtime_id: str
    java: Path
    router_port: int
    upload_kib: int
    download_kib: int
    offer: Path
    contact_file: Path | None
    manifest: str | list[str]
    trust: Path | None = None
    store: Path
    output: Path
    expected_manifest_id: str
    allow_i2p: bool
    allow_sharing: bool
    seconds: int
    max_bytes: int
    kit: Path


def register_i2p_commands(add_parser: Callable[..., argparse.ArgumentParser]) -> None:
    """Register independently granted network, payload-sharing and offline actions."""
    parser = add_parser(
        "i2p", help="standalone I2P recovery pilot; privacy not qualified"
    )
    commands = parser.add_subparsers(dest="i2p_action", required=True)
    for name in (
        "init",
        "export",
        "inspect",
        "receive",
        "share",
        "status",
        "contact",
        "handoff",
    ):
        command = commands.add_parser(name)
        if name in {"init", "receive", "share", "status", "contact", "handoff"}:
            command.add_argument("--dir", dest="i2p_dir", type=Path, required=True)
        if name == "init":
            command.add_argument("--runtime", type=Path, required=True)
            command.add_argument("--expected-runtime-id", required=True)
            command.add_argument("--java", type=Path, required=True)
            command.add_argument("--router-port", type=int, default=7654)
            command.add_argument("--upload-kib", type=int, default=32)
            command.add_argument("--download-kib", type=int, default=256)
        if name == "export":
            command.add_argument("manifest")
            command.add_argument("--store", type=Path, required=True)
            command.add_argument("--output", type=Path, required=True)
        if name in {"export", "receive", "share", "handoff"}:
            command.add_argument("--trust", type=Path, required=True)
            command.add_argument("--contact-file", type=Path)
            command.add_argument("--progress", action="store_true")
        if name in {"inspect", "receive", "share", "handoff"}:
            command.add_argument("--offer", type=Path, required=True)
        if name == "handoff":
            command.add_argument("--expected-manifest-id", required=True)
            command.add_argument("--output", type=Path, required=True)
        if name in {"receive", "share"}:
            command.add_argument("--expected-manifest-id", required=True)
            command.add_argument(
                "--allow-i2p",
                action="store_true",
                help="allow the selected I2P client to join I2P",
            )
            command.add_argument(
                "--allow-sharing",
                action="store_true",
                help="allow payload uploads while receiving/hosting",
            )
            command.add_argument("--seconds", type=int, default=3600)
            command.add_argument("--max-bytes", type=int, default=20 * 1024**3)
        if name == "receive":
            command.add_argument("--store", type=Path, required=True)
        if name == "share":
            command.add_argument("--kit", type=Path, required=True)


def _read(path: Path) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(MAX_METADATA_BYTES + 1)
    if len(data) > MAX_METADATA_BYTES:
        raise PreservationError("I2P input exceeds its metadata bound")
    return data


def run_i2p_command(
    arguments: I2PArguments, control: OperationControl
) -> dict[str, JsonValue]:
    """Execute only the requested effect; status/contact never refresh a network."""
    action = arguments.i2p_action
    if action == "init":
        _ = initialize_i2p(
            arguments.i2p_dir,
            arguments.runtime,
            arguments.expected_runtime_id,
            arguments.java,
            router_port=arguments.router_port,
            upload_kib=arguments.upload_kib,
            download_kib=arguments.download_kib,
        )
        return {"initialized": True, "network_started": False, "sharing_started": False}
    if action in {"status", "contact"}:
        _ = read_profile(arguments.i2p_dir)
        try:
            status = native_status(arguments.i2p_dir)
        except FileNotFoundError:
            return {"native_status": "unavailable", "network_started": False}
        if action == "contact":
            if not status.connected or status.destination is None:
                raise PreservationError(
                    "I2P contact unavailable until an opted-in session is connected"
                )
            contact = I2PContact(destination=status.destination, port=status.dht_port)
            return {
                "contact": strict_json(encode_record(contact)),
                "live_endpoint_not_guaranteed": True,
                "network_started": False,
            }
        return {
            "observed_connected": status.connected,
            "observed_dht_nodes": status.dht_nodes,
            "observation_may_be_stale": True,
            "network_started": False,
            "privacy_qualified": False,
        }
    contacts: tuple[I2PContact, ...] = ()
    if action != "inspect" and arguments.contact_file is not None:
        value = strict_json(_read(arguments.contact_file))
        if not isinstance(value, dict) or not isinstance(value.get("contact"), dict):
            raise PreservationError("require an explicitly exported I2P contact record")
        contacts = (I2PContact.model_validate_json(canonical_bytes(value["contact"])),)
    if action == "inspect":
        offer = read_offer(_read(arguments.offer))
        return {
            "manifest_id": offer.manifest_id,
            "authority": "untrusted-routing",
            "kit_bytes": offer.kit.size,
            "network_started": False,
            "sharing_started": False,
        }
    if arguments.trust is None:
        raise PreservationError("independently enrolled model trust is required")
    trust = load_trust(_read(arguments.trust))
    if action == "export":
        if not isinstance(arguments.manifest, str):
            raise PreservationError("one exact manifest required")
        offer = export_i2p(
            arguments.store,
            arguments.manifest,
            trust,
            arguments.output,
            contacts=contacts,
            control=control,
        )
        return {
            "manifest_id": offer.manifest_id,
            "network_started": False,
            "sharing_started": False,
        }
    offer = read_offer(_read(arguments.offer))
    if action == "handoff":
        _ = authenticate_offer(offer, trust, arguments.expected_manifest_id)
        status = native_status(arguments.i2p_dir)
        if not status.connected or status.destination is None:
            raise PreservationError(
                "start an explicitly opted-in sharing session first"
            )
        routed = offer.model_copy(
            update={
                "contacts": (
                    I2PContact(destination=status.destination, port=status.dht_port),
                )
            }
        )
        write_reference(arguments.output, encode_record(routed))
        return {
            "manifest_id": offer.manifest_id,
            "network_started": False,
            "sharing_started": False,
            "live_endpoint_not_guaranteed": True,
        }
    if contacts:
        offer = offer.model_copy(update={"contacts": contacts})
    return transfer_i2p(
        offer,
        arguments.i2p_dir,
        trust,
        arguments.expected_manifest_id,
        allow_i2p=arguments.allow_i2p,
        allow_sharing=arguments.allow_sharing,
        seconds=arguments.seconds,
        max_bytes=arguments.max_bytes,
        destination=arguments.store if action == "receive" else None,
        kit_source=arguments.kit if action == "share" else None,
        control=control,
    )
