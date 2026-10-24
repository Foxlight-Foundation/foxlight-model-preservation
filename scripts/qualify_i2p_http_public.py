"""Explicit finite public I2P HTTP qualification with generated synthetic bytes.

Never runs in ordinary conformance/installation. Requires an explicit public
network grant, fresh evidence directory and selected locally built native tools.
Raw router logs/keys stay private; the report does not claim anonymity or custody.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from securesystemslib.signer import CryptoSigner
from tuf.api.metadata import Metadata, Root

from foxlight_model_preservation.discovery import (
    bootstrap_identity,
    fetch_selection,
    follow_source,
    refresh_source_i2p,
    search_sources,
    source_identity,
)
from foxlight_model_preservation.discovery_models import (
    DiscoveryCatalogTarget,
    DiscoveryDescription,
    SourceBootstrap,
)
from foxlight_model_preservation.discovery_publish import (
    DiscoverySigningKeys,
    publish_catalog,
)
from foxlight_model_preservation.i2p_http import I2PHTTPProxy, i2p_http_response
from foxlight_model_preservation.models import (
    Catalog,
    CatalogEntry,
    Recipe,
    TrustProfile,
)
from foxlight_model_preservation.operations import OperationControl
from foxlight_model_preservation.signing import (
    CATALOG_TYPE,
    digest,
    encode_record,
    public_record,
    sign_record,
)
from foxlight_model_preservation.storage import manifest_path, pack, restore
from foxlight_model_preservation.transfer import I2PTransferPolicy


def free_port(kind: int = socket.SOCK_STREAM) -> int:
    """Choose an unused local port without adopting any existing listener."""
    with socket.socket(type=kind) as reserve:
        reserve.bind(("127.0.0.1", 0))
        return reserve.getsockname()[1]


def fixture(root: Path, mib: int) -> tuple[str, TrustProfile, bytes]:
    """Pack only newly generated, non-executable synthetic model/companion bytes."""
    key = Ed25519PrivateKey.generate()
    trust = TrustProfile(
        schema_version="foxlight.model-preservation.trust.v1",
        keys=(public_record(key),),
        threshold=1,
        as_of=datetime.now(UTC).isoformat(),
    )
    sources, members = {}, []
    for name, role, dependencies in (
        ("weights", "primary", ["tokenizer"]),
        ("tokenizer", "tokenizer", []),
    ):
        path = root / name
        path.mkdir()
        (path / "fixture.bin").write_bytes(
            os.urandom(mib * 1024**2) if name == "weights" else b"synthetic companion"
        )
        (path / "LICENSE").write_text(
            "Newly generated test bytes dedicated to the public domain. "
            "CC0-1.0. No trained weights.\n"
        )
        sources[name] = path
        members.append(
            {
                "member_id": name,
                "role": role,
                "format": "synthetic",
                "dependencies": dependencies,
                "origin_repository": "synthetic/" + name,
                "origin_revision": "a" * 40,
                "artifact_identity": "fixture:" + name,
                "retained_card": {
                    "verification_class": "legacy",
                    "future": {"opaque": ["retained", 7]},
                },
                "intake": {
                    "audience": "public",
                    "license_id": "CC0-1.0",
                    "terms_reference": "LICENSE",
                    "decision": "generated public test data only",
                    "notice_paths": ["LICENSE"],
                },
                "files": [{"path": "fixture.bin"}, {"path": "LICENSE"}],
            }
        )
    recipe = Recipe.model_validate_json(
        json.dumps(
            {
                "schema_version": "foxlight.model-preservation.v1",
                "name": "Public I2P synthetic recovery",
                "root_member": "weights",
                "workloads": ["test-only"],
                "runtime_requirements": ["not an inference fixture"],
                "members": members,
            }
        )
    )
    identity = pack(recipe, sources, root / "model", (key,))
    envelope = (root / "model" / manifest_path(identity)).read_bytes()
    for path in sources.values():
        shutil.rmtree(path)
    return identity, trust, envelope


def publish(root: Path, base: str, identity: str) -> SourceBootstrap:
    """Publish synthetic TUF/catalog metadata under keys distinct from model trust."""
    tuf_key = Ed25519PrivateKey.generate()
    signer = CryptoSigner(tuf_key)
    md = Metadata(Root(expires=datetime.now(UTC) + timedelta(days=1)))
    for role in ("root", "targets", "snapshot", "timestamp"):
        md.signed.add_key(signer.public_key, role)
    md.sign(signer)
    catalog_key = Ed25519PrivateKey.generate()
    catalog = Catalog(
        schema_version="foxlight.model-preservation.catalog.v1",
        description="synthetic public I2P test",
        entries=(
            CatalogEntry(
                entry_id="fixture", manifest=identity, mirrors=(base + "/model",)
            ),
        ),
    )
    public = TrustProfile(
        schema_version="foxlight.model-preservation.trust.v1",
        keys=(public_record(catalog_key),),
        threshold=1,
        as_of=datetime.now(UTC).isoformat(),
    )
    profile = DiscoveryCatalogTarget(
        catalog_id=digest(encode_record(catalog)),
        catalog_trust=public,
        descriptions=(
            DiscoveryDescription(
                entry_id="fixture",
                manifest=identity,
                name="Synthetic public I2P qualification",
            ),
        ),
    )
    publish_catalog(
        root / "source",
        md.to_bytes(),
        encode_record(sign_record(catalog, (catalog_key,), CATALOG_TYPE)),
        profile,
        DiscoverySigningKeys(tuf_key, tuf_key, tuf_key),
        version=1,
        expires=datetime.now(UTC) + timedelta(days=1),
    )
    return SourceBootstrap(
        name="Synthetic public I2P qualification",
        root=base64.b64encode(md.to_bytes()).decode(),
        endpoints=(base + "/source",),
    )


def main() -> int:
    """Run a bounded opt-in experiment and retain precise success/failure evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--java-home", type=Path, required=True)
    parser.add_argument("--i2p-source", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=600)
    parser.add_argument("--kib", type=int, default=32)
    parser.add_argument("--payload-mib", type=int, default=1)
    parser.add_argument("--allow-public-i2p", action="store_true")
    args = parser.parse_args()
    if not args.allow_public_i2p or not (
        180 <= args.seconds <= 1800
        and 16 <= args.kib <= 128
        and 1 <= args.payload_mib <= 64
    ):
        parser.error(
            "explicit public I2P permission and bounded resources are required"
        )
    source, runtime = (
        args.i2p_source.resolve(strict=True),
        args.runtime.resolve(strict=True),
    )
    git = shutil.which("git")
    if git is None:
        parser.error("a trusted Git executable is required")
    revision = subprocess.check_output(  # noqa: S603 - selected local checkout, read-only
        [git, "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != "3c98503c5f14efe7bdd783528ba7f621f12d8081":
        parser.error("selected I2P source revision differs")
    jars = (
        runtime / "snark.jar",
        source / "build/router.jar",
        source / "build/i2ptunnel.jar",
    )
    native = Path(__file__).resolve().parent.parent / "tests/native"
    root = args.directory.absolute()
    root.mkdir(mode=0o700)
    classes = root / "classes"
    classes.mkdir()
    environment = dict(os.environ)
    for key in ("JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "CLASSPATH"):
        environment.pop(key, None)
    classpath = os.pathsep.join(map(str, jars))
    subprocess.run(  # noqa: S603 - selected compiler and owned source
        [
            str(args.java_home / "bin/javac"),
            "-cp",
            classpath,
            "-d",
            str(classes),
            str(native / "I2PPublicRouter.java"),
            str(native / "I2PHTTPTest.java"),
        ],
        env=environment,
        check=True,
    )  # noqa: S603 - selected compiler and owned source
    classpath = str(classes) + os.pathsep + classpath
    report = {
        "network": "public-I2P",
        "profile": "IPv4 SSU; configured three-hop HTTP; zero configured transit share",
        "seconds_limit": args.seconds,
        "router_rate_kib": args.kib,
        "hard_wire_quota": False,
        "payload_mib": args.payload_mib,
        "fixture_bytes": "os.urandom",
        "native_jars": {
            str(path.name): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in jars
        },
        "success": False,
        "anonymity_qualified": False,
        "independent_custody": False,
        "stage": "router-start",
    }
    processes, logs = [], []
    real_getaddrinfo = socket.getaddrinfo
    dns_attempts = []
    backend = None
    thread = None
    started = time.monotonic()
    deadline = started + args.seconds

    def launch(name, cls, arguments):
        directory = root / name
        directory.mkdir(exist_ok=True)
        log = (directory / "console.log").open("wb")
        logs.append(log)
        child = subprocess.Popen(  # noqa: S603 - selected runtime and owned helper
            [
                str(args.java_home / "bin/java"),
                "-Xmx256m",
                "-Djava.awt.headless=true",
                "-cp",
                classpath,
                cls,
                *arguments,
            ],
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=environment,
        )  # noqa: S603 - owned helper, selected runtime, no shell
        processes.append(child)
        return directory

    def wait(predicate):
        while time.monotonic() < deadline:
            if any(child.poll() is not None for child in processes):
                raise RuntimeError("owned native helper stopped")
            (root / "progress.json").write_text(
                json.dumps(
                    {
                        "stage": report["stage"],
                        "elapsed_seconds": time.monotonic() - started,
                    }
                )
                + "\n"
            )
            result = predicate()
            if result:
                return result
            time.sleep(1)
        raise TimeoutError("qualification window ended")

    try:
        report["stage"] = "offline-preflight"
        public_root = root / "http-root"
        public_root.mkdir()
        identity, trust, envelope = fixture(public_root, args.payload_mib)
        (root / "model-trust.json").write_bytes(encode_record(trust))
        report["model_trust_retained"] = True
        report["manifest_id"] = identity
        report["original_envelope_sha256"] = digest(envelope)
        preflight = root / "offline-preflight"
        preflight.mkdir()
        sample = base64.b32encode(b"i" * 32).decode().rstrip("=").lower()
        publish(preflight, "http://" + sample + ".b32.i2p", identity)
        report["stage"] = "router-start"
        ports = []
        for index in range(2):
            directory = root / ("router-" + str(index))
            directory.mkdir()
            shutil.copytree(
                source / "installer/resources/certificates", directory / "certificates"
            )
            port, udp = free_port(), free_port(socket.SOCK_DGRAM)
            ports.append(port)
            launch(
                directory.name,
                "I2PPublicRouter",
                [
                    str(directory),
                    str(port),
                    str(udp),
                    str(args.seconds),
                    str(args.kib),
                    "allow-public-i2p",
                ],
            )

        def routers_ready():
            try:
                observations = [
                    json.loads(
                        (root / ("router-" + str(i)) / "observation.json").read_text()
                    )
                    for i in range(2)
                ]
                return all(
                    value["active_peers"] >= 5 and value["uptime_ms"] >= 60000
                    for value in observations
                )
            except (FileNotFoundError, json.JSONDecodeError):
                return False

        wait(routers_ready)
        report["stage"] = "tunnel-start"
        backend = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            partial(SimpleHTTPRequestHandler, directory=str(public_root)),
        )
        thread = threading.Thread(target=backend.serve_forever, daemon=True)
        thread.start()
        proxy_port = free_port()
        server = launch(
            "server",
            "I2PHTTPTest",
            [
                "server",
                str(root / "server"),
                str(ports[0]),
                str(backend.server_port),
                str(args.seconds),
            ],
        )
        launch(
            "client",
            "I2PHTTPTest",
            [
                "client",
                str(root / "client"),
                str(ports[1]),
                str(proxy_port),
                str(args.seconds),
            ],
        )
        wait(
            lambda: (
                (server / "destination.txt").is_file()
                and (root / "client/ready.txt").is_file()
            )
        )
        base = "http://" + (server / "destination.txt").read_text()
        bootstrap = publish(public_root, base, identity)
        cache = root / "cache"
        follow_source(cache, encode_record(bootstrap), bootstrap_identity(bootstrap))
        proxy = I2PHTTPProxy(port=proxy_port)
        report["stage"] = "routed-readiness"

        def refuse_application_dns(*arguments, **keywords):
            dns_attempts.append(True)
            raise OSError("application DNS is forbidden in the protected phase")

        socket.getaddrinfo = refuse_application_dns

        def reachable():
            try:
                with i2p_http_response(
                    base, "source/metadata/1.root.json", proxy, 10
                ) as incoming:
                    return incoming.status == 200
            except OSError:
                return False

        wait(reachable)
        report["stage"] = "authenticated-update"
        refresh_source_i2p(
            cache, source_identity(bootstrap), proxy=proxy, allow_i2p=True
        )
        if search_sources(cache, "qualification")[0].manifest != identity:
            raise ValueError("authenticated search identity differs")
        report["stage"] = "complete-recovery"
        transfer_start = time.monotonic()
        received = root / "received"
        manifest = fetch_selection(
            cache,
            source_identity(bootstrap),
            "fixture",
            identity,
            trust,
            received,
            I2PTransferPolicy(
                proxy=proxy,
                deadline_seconds=max(1, min(3600, deadline - time.monotonic())),
            ),
            control=OperationControl(
                lambda progress: (root / "progress.json").write_text(
                    json.dumps(
                        {
                            "stage": "complete-recovery",
                            "elapsed_seconds": time.monotonic() - started,
                            "operation": progress.model_dump(mode="json"),
                        }
                    )
                    + "\n"
                ),
                interval_seconds=1,
            ),
        )
        report["transfer_seconds"] = time.monotonic() - transfer_start
        if (received / manifest_path(identity)).read_bytes() != envelope:
            raise ValueError("original signed envelope differs")
        restore(received, identity, trust, root / "restored")
        report.update(
            success=True, stage="complete", logical_bytes=manifest.total_bytes
        )
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        report["failure_type"] = type(error).__name__
    finally:
        socket.getaddrinfo = real_getaddrinfo
        report["protected_application_dns_attempts"] = len(dns_attempts)
        report["elapsed_seconds"] = time.monotonic() - started
        report["router_observations"] = []
        for index in range(2):
            try:
                report["router_observations"].append(
                    json.loads(
                        (
                            root / ("router-" + str(index)) / "observation.json"
                        ).read_text()
                    )
                )
            except (FileNotFoundError, json.JSONDecodeError):
                pass
        for child in processes:
            child.stdin.close()
        for child in processes:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
        report["owned_helpers_stopped"] = all(
            child.poll() is not None for child in processes
        )
        report["helper_exit_codes"] = [child.returncode for child in processes]
        for log in logs:
            log.close()
        if backend is not None:
            backend.shutdown()
            thread.join(5)
            backend.server_close()
        (root / "report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n"
        )
        print(json.dumps(report, sort_keys=True))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
