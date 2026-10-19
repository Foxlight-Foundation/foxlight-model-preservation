"""Small standalone owner, consumer and explicit mirror commands."""

from __future__ import annotations

import argparse
import getpass
import json
import ssl
import sys
from pathlib import Path

from pydantic import JsonValue

from .models import MAX_METADATA_BYTES, Catalog, PreservationError, Recipe
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
from .storage import pack, restore, verify_store
from .transfer import TransferPolicy, fetch


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
    return parser


def run(arguments: Arguments) -> dict[str, JsonValue]:
    """Execute exactly the selected operation and return a bounded safe receipt."""
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
    if arguments.command == "pack":
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
        identity = pack(
            recipe,
            sources,
            arguments.store,
            (load_key(arguments.key, _password(arguments)),),
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
    if arguments.command == "verify":
        if not isinstance(arguments.manifest, str):
            raise PreservationError("one exact manifest identity required")
        manifest = verify_store(arguments.store, arguments.manifest, trust)
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
        )
    elif arguments.command == "restore":
        if not isinstance(arguments.manifest, str):
            raise PreservationError("one exact manifest identity required")
        manifest = restore(arguments.store, arguments.manifest, trust, arguments.output)
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


def main() -> int:
    """Return actionable failure without a traceback or leaking key material."""
    try:
        receipt = run(argument_parser().parse_args(namespace=Arguments()))
    except (OSError, ValueError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
