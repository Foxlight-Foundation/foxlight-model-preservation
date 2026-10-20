"""Small standalone owner, consumer and explicit mirror commands."""

from __future__ import annotations

import argparse
import getpass
import json
import signal
import ssl
import sys
import time
from pathlib import Path

from pydantic import JsonValue

from .assurance import inspect_member_evidence, retain_card
from .cards import create_card, identity_json, load_card
from .models import MAX_METADATA_BYTES, Catalog, PreservationError, Recipe, TrustProfile
from .operations import OperationCancelled, OperationControl, Progress, failure_code
from .publication import PublicationRequest, plan_publication, publish_public
from .registry_proof import VersionFloor, create_registry_proof, verify_registry_proof
from .seed import SeedServer
from .signing import (
    CATALOG_TYPE,
    canonical_bytes,
    create_key,
    digest,
    encode_record,
    load_key,
    load_trust,
    sign_record,
    strict_json,
    verify_catalog,
)
from .storage import pack, plan_pack, plan_restore, restore, verify_store
from .torrent import TorrentExport, TorrentSeeder, TorrentSeedPolicy, export_torrent
from .transfer import TransferPolicy, fetch, plan_fetch


class Arguments(argparse.Namespace):
    """Typed parser output; each command uses only its declared arguments."""

    bundle_trust: Path | None = None
    index: Path
    catalog: str
    entry: str
    command: str
    key: Path
    trust_out: Path
    trust: Path
    passphrase_file: Path | None = None
    unencrypted: bool = False
    recipe: Path
    member: list[str]
    store: Path
    manifest: str | list[str]
    mirror: list[str]
    max_bytes: int
    allow_private: bool
    allow_http: bool
    output: Path
    cert: Path | None
    tls_key: Path | None
    bind: str
    port: int
    public: bool
    max_upload_bytes: int
    operation_id: str | None = None
    progress: bool = False
    body: Path
    card: Path
    alias: str | None = None
    card_id: str
    proof: Path | None = None
    root: Path | None = None
    root_chain: list[Path]
    timestamp: Path
    snapshot: Path
    targets: Path
    target: Path
    target_path: str
    historical: bool = False
    minimum_versions: Path | None = None
    request: Path
    expected_manifest_id: str
    web_seed: list[str]
    allow_local_http: bool
    duration: int
    upload_rate: int
    connections: int
    stop_after_payload_bytes: int


def read_input(path: Path) -> bytes:
    """Read bounded explicit local metadata, including recipes and trust."""
    with path.open("rb") as stream:
        data = stream.read(MAX_METADATA_BYTES + 1)
    if len(data) > MAX_METADATA_BYTES:
        raise PreservationError("input exceeds metadata size limit")
    return data


def _password(arguments: Arguments, *, create: bool = False) -> bytes | None:
    if arguments.unencrypted:
        return None
    if arguments.passphrase_file is not None:
        supplied = read_input(arguments.passphrase_file).rstrip(b"\r\n")
        if not supplied:
            raise PreservationError("passphrase file must not be empty")
        return supplied
    password = getpass.getpass("Key passphrase: ")
    if not password:
        raise PreservationError("an encrypted key needs a nonempty passphrase")
    if create and getpass.getpass("Confirm passphrase: ") != password:
        raise PreservationError("passphrases did not match")
    return password.encode("utf-8")


def _key_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--key", type=Path, required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--passphrase-file", type=Path)
    group.add_argument(
        "--unencrypted",
        action="store_true",
        help="explicit local/test key without passphrase protection",
    )


def _trust_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("manifest", help="exact SHA-256 manifest identity")
    parser.add_argument(
        "--trust",
        type=Path,
        required=True,
        help="independently enrolled trust snapshot",
    )


def argument_parser() -> argparse.ArgumentParser:
    """Expose effect-specific commands with no implicit public serving behavior."""
    parser = argparse.ArgumentParser(prog="model-preserve")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("card-create", "card-verify", "card-retain"):
        card = commands.add_parser(name, help="use the open public model-card format")
        if name == "card-create":
            card.add_argument("--body", type=Path, required=True)
            card.add_argument("--alias")
        else:
            card.add_argument("--card", type=Path, required=True)
        if name != "card-verify":
            card.add_argument("--output", type=Path, required=True)
        if name == "card-retain":
            card.add_argument("--proof", type=Path)
            card.add_argument("--root", type=Path)
            card.add_argument("--historical", action="store_true")
    for name in ("proof-create", "proof-verify"):
        proof = commands.add_parser(name, help="verify exact offline registry evidence")
        proof.add_argument("--root", type=Path, required=True)
        proof.add_argument("--card-id", required=True)
        proof.add_argument("--historical", action="store_true")
        proof.add_argument("--minimum-versions", type=Path)
        if name == "proof-verify":
            proof.add_argument("--proof", type=Path, required=True)
        else:
            for option in ("timestamp", "snapshot", "targets", "target", "output"):
                proof.add_argument("--" + option, type=Path, required=True)
            proof.add_argument("--target-path", required=True)
            proof.add_argument("--root-chain", type=Path, action="append", default=[])
    evidence = commands.add_parser(
        "inspect-evidence", help="verify bundle bytes and report retained card evidence"
    )
    _trust_options(evidence)
    evidence.add_argument("--store", type=Path, required=True)
    evidence.add_argument("--root", type=Path)
    evidence.add_argument("--historical", action="store_true")
    evidence.add_argument("--minimum-versions", type=Path)
    keys = commands.add_parser(
        "keygen", help="create a private signing key and public trust snapshot"
    )
    _key_options(keys)
    keys.add_argument("--trust-out", type=Path, required=True)
    create = commands.add_parser(
        "pack", help="copy explicit selected members and sign a complete bundle"
    )
    _key_options(create)
    create.add_argument("--recipe", type=Path, required=True)
    create.add_argument(
        "--member", action="append", required=True, metavar="NAME=DIRECTORY"
    )
    create.add_argument("--store", type=Path, required=True)
    verify = commands.add_parser(
        "verify", help="verify signatures and every required stored file"
    )
    _trust_options(verify)
    verify.add_argument("--store", type=Path, required=True)
    download = commands.add_parser(
        "fetch", help="receive an exact bundle from allowed mirrors; never seed"
    )
    _trust_options(download)
    download.add_argument("--mirror", action="append", required=True)
    download.add_argument("--store", type=Path, required=True)
    download.add_argument("--max-bytes", type=int, default=20 * 1024**3)
    download.add_argument("--allow-private", action="store_true")
    download.add_argument("--allow-http", action="store_true")
    materialize = commands.add_parser(
        "restore", help="restore member layouts into a new directory"
    )
    _trust_options(materialize)
    materialize.add_argument("--store", type=Path, required=True)
    materialize.add_argument("--output", type=Path, required=True)
    for name in ("plan-publication", "publish", "torrent-export", "torrent-seed"):
        sharing = commands.add_parser(
            name, help="explicit public publication or sharing"
        )
        _trust_options(sharing)
        sharing.add_argument("--store", type=Path, required=True)
        sharing.add_argument("--progress", action="store_true")
        if name in {"plan-publication", "publish"}:
            sharing.add_argument("--request", type=Path, required=True)
        if name in {"publish", "torrent-export"}:
            sharing.add_argument("--output", type=Path, required=True)
        if name == "publish":
            _key_options(sharing)
            sharing.add_argument("--expected-manifest-id", required=True)
        if name == "torrent-export":
            sharing.add_argument("--web-seed", action="append", default=[])
            sharing.add_argument("--allow-local-http", action="store_true")
        if name == "torrent-seed":
            sharing.add_argument(
                "--public",
                action="store_true",
                help="explicitly join public DHT/peer discovery",
            )
            sharing.add_argument("--bind", default="127.0.0.1")
            sharing.add_argument("--port", type=int, default=0)
            sharing.add_argument("--duration", type=int, default=3600)
            sharing.add_argument("--upload-rate", type=int, default=1024 * 1024)
            sharing.add_argument("--connections", type=int, default=8)
            sharing.add_argument(
                "--stop-after-payload-bytes", type=int, default=1024**3
            )
    seed = commands.add_parser(
        "seed", help="explicit bounded read-only serving of selected verified bundles"
    )
    seed.add_argument("--store", type=Path, required=True)
    seed.add_argument("--trust", type=Path, required=True)
    seed.add_argument("--manifest", action="append", required=True)
    seed.add_argument("--bind", default="127.0.0.1")
    seed.add_argument("--port", type=int, default=8125)
    seed.add_argument(
        "--public",
        action="store_true",
        help="require public intake for every selected member",
    )
    seed.add_argument("--max-upload-bytes", type=int, default=1024**3)
    seed.add_argument("--cert", type=Path)
    seed.add_argument("--tls-key", type=Path)
    seed.add_argument("--allow-http", action="store_true")
    publish = commands.add_parser(
        "catalog-sign", help="sign a portable exact model list"
    )
    _key_options(publish)
    publish.add_argument("--index", type=Path, required=True)
    publish.add_argument("--output", type=Path, required=True)
    for name in ("catalog-verify", "fetch-catalog"):
        catalog = commands.add_parser(name, help="use an independently pinned catalog")
        catalog.add_argument("catalog", help="exact catalog payload SHA-256")
        catalog.add_argument("--index", type=Path, required=True)
        catalog.add_argument("--trust", type=Path, required=True)
        if name == "fetch-catalog":
            catalog.add_argument("--bundle-trust", type=Path)
            catalog.add_argument("--entry", required=True)
            catalog.add_argument("--store", type=Path, required=True)
            catalog.add_argument("--max-bytes", type=int, default=20 * 1024**3)
            catalog.add_argument("--allow-private", action="store_true")
            catalog.add_argument("--allow-http", action="store_true")
    for name in (
        "pack",
        "verify",
        "fetch",
        "restore",
        "fetch-catalog",
        "inspect-evidence",
    ):
        commands.choices[name].add_argument(
            "--progress", action="store_true", help="JSON progress on stderr"
        )
    for name in ("fetch", "fetch-catalog"):
        commands.choices[name].add_argument(
            "--operation-id", help="retain this request's transfer allowance"
        )
    for name in ("plan-pack", "plan-fetch", "plan-restore"):
        planning = commands.add_parser(name, help="read-only content and space plan")
        planning.add_argument("--progress", action="store_true")
        planning.add_argument("--store", type=Path, required=True)
        if name == "plan-pack":
            planning.add_argument("--recipe", type=Path, required=True)
            planning.add_argument("--member", action="append", required=True)
        else:
            _trust_options(planning)
            if name == "plan-restore":
                planning.add_argument("--output", type=Path, required=True)
            else:
                planning.add_argument("--mirror", action="append", required=True)
                planning.add_argument("--max-bytes", type=int, default=20 * 1024**3)
                planning.add_argument("--allow-private", action="store_true")
                planning.add_argument("--allow-http", action="store_true")
    return parser


def _card_commands(arguments: Arguments) -> dict[str, JsonValue]:
    if arguments.command == "card-create":
        body = strict_json(read_input(arguments.body))
        if not isinstance(body, dict):
            raise PreservationError("card body must be an object")
        card = create_card(body, alias=arguments.alias)
        with arguments.output.open("xb") as stream:
            stream.write(identity_json(card.model_dump(mode="json")))
        return {"card_id": card.card_id, "publication": "not-established"}
    card = load_card(read_input(arguments.card))
    if arguments.command == "card-verify":
        return {"card_id": card.card_id, "publication": "not-established"}
    proof = None
    if arguments.proof is not None:
        if arguments.root is None:
            raise PreservationError("retaining a registry proof needs an enrolled root")
        data = read_input(arguments.proof)
        verify_registry_proof(
            data,
            read_input(arguments.root),
            card.card_id,
            mode="historical" if arguments.historical else "fresh",
        )
        from .registry_proof import RegistryProof

        proof = RegistryProof.model_validate_json(identity_json(strict_json(data)))
    retained = retain_card(card, registry_proof=proof)
    encoded = encode_record(retained)
    if len(encoded) > MAX_METADATA_BYTES:
        raise PreservationError("retained card exceeds metadata limit")
    with arguments.output.open("xb") as stream:
        stream.write(encoded)
    return {"card_id": card.card_id, "retained_proof": proof is not None}


def _proof_commands(arguments: Arguments) -> dict[str, JsonValue]:
    if arguments.root is None:
        raise PreservationError("registry proof verification needs an enrolled root")
    root = read_input(arguments.root)
    if arguments.command == "proof-create":
        proof = create_registry_proof(
            trusted_root=root,
            timestamp=read_input(arguments.timestamp),
            snapshot=read_input(arguments.snapshot),
            targets=read_input(arguments.targets),
            target_path=arguments.target_path,
            target=read_input(arguments.target),
            root_chain=tuple(read_input(path) for path in arguments.root_chain),
        )
        data = encode_record(proof)
    else:
        if arguments.proof is None:
            raise PreservationError("registry proof input required")
        data = read_input(arguments.proof)
    floors = (
        VersionFloor.model_validate_json(
            identity_json(strict_json(read_input(arguments.minimum_versions)))
        )
        if arguments.minimum_versions is not None
        else None
    )
    verified = verify_registry_proof(
        data,
        root,
        arguments.card_id,
        mode="historical" if arguments.historical else "fresh",
        minimum_versions=floors,
    )
    if arguments.command == "proof-create":
        with arguments.output.open("xb") as stream:
            stream.write(data)
    return verified.report.model_dump(mode="json")


def run(
    arguments: Arguments, *, control: OperationControl | None = None
) -> dict[str, JsonValue]:
    """Execute exactly the selected operation and return a bounded safe receipt."""
    if arguments.command.startswith("card-"):
        return _card_commands(arguments)
    if arguments.command.startswith("proof-"):
        return _proof_commands(arguments)
    if arguments.command == "keygen":
        if arguments.trust_out.exists():
            raise PreservationError("trust output already exists")
        trust = create_key(arguments.key, _password(arguments, create=True))
        with arguments.trust_out.open("xb") as stream:
            stream.write(encode_record(trust) + b"\n")
        return {
            "key_ids": [key.key_id for key in trust.keys],
            "trust_as_of": trust.as_of,
        }
    if arguments.command in {"pack", "plan-pack"}:
        recipe = Recipe.model_validate_json(
            canonical_bytes(strict_json(read_input(arguments.recipe)))
        )
        sources: dict[str, Path] = {}
        for value in arguments.member:
            name, separator, path = value.partition("=")
            if not separator or name in sources:
                raise PreservationError(
                    "member inputs must be unique NAME=DIRECTORY pairs"
                )
            sources[name] = Path(path)
        if arguments.command == "plan-pack":
            plan = plan_pack(recipe, sources, arguments.store, control=control)
            return {"plan_id": plan.plan_id, **plan.model_dump(mode="json")}
        identity = pack(
            recipe,
            sources,
            arguments.store,
            (load_key(arguments.key, _password(arguments)),),
            control=control,
        )
        return {"manifest": identity, "attestation": "owner-capture"}
    if arguments.command == "catalog-sign":
        catalog = Catalog.model_validate_json(
            canonical_bytes(strict_json(read_input(arguments.index)))
        )
        envelope = sign_record(
            catalog, (load_key(arguments.key, _password(arguments)),), CATALOG_TYPE
        )
        encoded = encode_record(envelope)
        if len(encoded) > MAX_METADATA_BYTES:
            raise PreservationError("catalog envelope exceeds metadata limit")
        with arguments.output.open("xb") as output:
            output.write(encoded)
        return {"catalog": digest(encode_record(catalog))}
    trust = load_trust(read_input(arguments.trust))
    if arguments.command in {
        "plan-publication",
        "publish",
        "torrent-export",
        "torrent-seed",
    }:
        return _sharing_commands(arguments, trust, control or OperationControl())
    if arguments.command == "inspect-evidence":
        if not isinstance(arguments.manifest, str):
            raise PreservationError("one exact manifest identity required")
        manifest = verify_store(
            arguments.store, arguments.manifest, trust, control=control
        )
        root = read_input(arguments.root) if arguments.root is not None else None
        floors = (
            VersionFloor.model_validate_json(
                identity_json(strict_json(read_input(arguments.minimum_versions)))
            )
            if arguments.minimum_versions is not None
            else None
        )
        return {
            "manifest": arguments.manifest,
            "verified_bytes": manifest.total_bytes,
            "attestation": manifest.attestation,
            "members": [
                inspect_member_evidence(
                    member,
                    trusted_root=root,
                    mode="historical" if arguments.historical else "fresh",
                    minimum_versions=floors,
                ).model_dump(mode="json")
                for member in manifest.members
            ],
        }
    if arguments.command in {"catalog-verify", "fetch-catalog"}:
        catalog = verify_catalog(read_input(arguments.index), trust, arguments.catalog)
        if arguments.command == "catalog-verify":
            return {
                "catalog": arguments.catalog,
                "entries": [entry.entry_id for entry in catalog.entries],
                "trust_as_of": trust.as_of,
            }
        selected = next(
            (entry for entry in catalog.entries if entry.entry_id == arguments.entry),
            None,
        )
        if selected is None:
            raise PreservationError("catalog entry does not exist")
        bundle_trust = (
            load_trust(read_input(arguments.bundle_trust))
            if arguments.bundle_trust is not None
            else trust
        )
        fetched = fetch(
            selected.manifest,
            bundle_trust,
            selected.mirrors,
            arguments.store,
            TransferPolicy(
                max_bytes=arguments.max_bytes,
                allow_private=arguments.allow_private,
                allow_http=arguments.allow_http,
            ),
            control=control,
            operation_id=arguments.operation_id,
        )
        return {
            "manifest": selected.manifest,
            "verified_bytes": fetched.total_bytes,
            "catalog_trust_as_of": trust.as_of,
            "trust_as_of": bundle_trust.as_of,
            "attestation": fetched.attestation,
        }

    if arguments.command != "seed" and not isinstance(arguments.manifest, str):
        raise PreservationError("one exact manifest identity required")
    if arguments.command == "plan-fetch":
        if not isinstance(arguments.manifest, str):
            raise PreservationError("one exact manifest identity required")
        plan = plan_fetch(
            arguments.manifest,
            trust,
            tuple(arguments.mirror),
            arguments.store,
            TransferPolicy(
                max_bytes=arguments.max_bytes,
                allow_private=arguments.allow_private,
                allow_http=arguments.allow_http,
            ),
            control=control,
        )
        return {"plan_id": plan.plan_id, **plan.model_dump(mode="json")}
    if arguments.command == "plan-restore":
        if not isinstance(arguments.manifest, str):
            raise PreservationError("one exact manifest identity required")
        plan = plan_restore(
            arguments.store,
            arguments.manifest,
            trust,
            arguments.output,
            control=control,
        )
        return {"plan_id": plan.plan_id, **plan.model_dump(mode="json")}
    if arguments.command == "verify":
        if not isinstance(arguments.manifest, str):
            raise PreservationError("one exact manifest identity required")
        manifest = verify_store(
            arguments.store, arguments.manifest, trust, control=control
        )
    elif arguments.command == "fetch":
        if not isinstance(arguments.manifest, str):
            raise PreservationError("one exact manifest identity required")
        manifest = fetch(
            arguments.manifest,
            trust,
            tuple(arguments.mirror),
            arguments.store,
            TransferPolicy(
                max_bytes=arguments.max_bytes,
                allow_private=arguments.allow_private,
                allow_http=arguments.allow_http,
            ),
            control=control,
            operation_id=arguments.operation_id,
        )
    elif arguments.command == "restore":
        if not isinstance(arguments.manifest, str):
            raise PreservationError("one exact manifest identity required")
        manifest = restore(
            arguments.store,
            arguments.manifest,
            trust,
            arguments.output,
            control=control,
        )
    elif arguments.command == "seed":
        if not isinstance(arguments.manifest, list):
            raise PreservationError("explicit bundle list required")
        if bool(arguments.cert) != bool(arguments.tls_key):
            raise PreservationError("supply both certificate and TLS key")
        if not arguments.cert and not arguments.allow_http:
            raise PreservationError(
                "seed needs TLS or explicit --allow-http for trusted tests"
            )
        if (
            arguments.bind not in {"127.0.0.1", "::1", "localhost"}
            and not arguments.public
        ):
            raise PreservationError("non-loopback seeding requires explicit --public")
        context: ssl.SSLContext | None = None
        if arguments.cert:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(arguments.cert, arguments.tls_key)
        with SeedServer(
            (arguments.bind, arguments.port),
            arguments.store,
            tuple(arguments.manifest),
            trust,
            public=arguments.public,
            max_upload_bytes=arguments.max_upload_bytes,
            tls_context=context,
        ) as server:
            print(
                json.dumps({"listening": server.server_address, "receive_only": False}),
                flush=True,
            )
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
        return {"stopped": True}
    else:
        raise PreservationError("unsupported operation")
    return {
        "manifest": arguments.manifest,
        "verified_bytes": manifest.total_bytes,
        "trust_as_of": trust.as_of,
        "attestation": manifest.attestation,
    }


def _sharing_commands(
    arguments: Arguments, trust: TrustProfile, control: OperationControl
) -> dict[str, JsonValue]:
    if not isinstance(arguments.manifest, str):
        raise PreservationError("one exact manifest identity required")
    if arguments.command in {"plan-publication", "publish"}:
        request = PublicationRequest.model_validate_json(
            canonical_bytes(strict_json(read_input(arguments.request)))
        )
        if arguments.command == "plan-publication":
            manifest = plan_publication(
                arguments.store, arguments.manifest, trust, request, control=control
            )
            return {
                "manifest_id": digest(encode_record(manifest)),
                "public_manifest": manifest.model_dump(mode="json"),
            }
        identity = publish_public(
            arguments.store,
            arguments.manifest,
            trust,
            request,
            arguments.output,
            (load_key(arguments.key, _password(arguments)),),
            expected_manifest_id=arguments.expected_manifest_id,
            control=control,
        )
        return {"manifest": identity, "audience": "public", "serving": False}
    if arguments.command == "torrent-export":
        result = export_torrent(
            arguments.store,
            arguments.manifest,
            trust,
            arguments.output,
            web_seeds=tuple(arguments.web_seed),
            allow_local_http=arguments.allow_local_http,
            control=control,
        )
        return result.model_dump(mode="json")
    expected = TorrentExport.model_validate_json(
        read_input(arguments.store / "torrent.json")
    )
    if expected.manifest_id != arguments.manifest:
        raise PreservationError("export receipt differs from the selected manifest")
    policy = TorrentSeedPolicy(
        public_swarm=arguments.public,
        listen_host=arguments.bind,
        listen_port=arguments.port,
        max_seconds=arguments.duration,
        upload_rate_bytes=arguments.upload_rate,
        max_connections=arguments.connections,
        stop_after_payload_bytes=arguments.stop_after_payload_bytes,
    )
    control.checkpoint()
    seeder = TorrentSeeder(arguments.store, expected, trust, policy, control=control)
    try:
        print(
            json.dumps(
                {
                    "listening_port": seeder.port,
                    "public_peer_participation": policy.public_swarm,
                    "payload_threshold_is_hard_cap": False,
                }
            ),
            flush=True,
        )
        while True:
            control.checkpoint()
            observation = seeder.observe()
            if observation.stopped:
                return observation.model_dump(mode="json")
            time.sleep(0.1)
    finally:
        seeder.close()


def main() -> int:
    """Return actionable failure without a traceback or leaking key material."""
    arguments = argument_parser().parse_args(namespace=Arguments())

    def report(progress: Progress) -> None:
        print(progress.model_dump_json(), file=sys.stderr, flush=True)

    control = OperationControl(report if arguments.progress else None)
    controlled = arguments.command in {
        "pack",
        "verify",
        "fetch",
        "restore",
        "fetch-catalog",
        "plan-pack",
        "plan-fetch",
        "plan-restore",
        "inspect-evidence",
        "plan-publication",
        "publish",
        "torrent-export",
        "torrent-seed",
    }
    previous = signal.getsignal(signal.SIGINT)
    if controlled:
        signal.signal(signal.SIGINT, lambda _number, _frame: control.cancel())
    try:
        receipt = run(arguments, control=control)
    except (OSError, ValueError) as error:
        print(
            json.dumps({"error": str(error), "code": failure_code(error)}),
            file=sys.stderr,
        )
        return 130 if isinstance(error, OperationCancelled) else 1
    finally:
        if controlled:
            signal.signal(signal.SIGINT, previous)
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
