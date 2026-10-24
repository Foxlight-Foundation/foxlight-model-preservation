"""Stock I2P HTTP proxy/server and three-hop authenticated recovery on private I2P."""

import os
import re
import socket
import subprocess
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from test_discovery import Publisher
from test_i2p_native import native_network as native_network
from test_i2p_native import socket_snapshot, wait_until

from foxlight_model_preservation.discovery import (
    bootstrap_identity,
    fetch_selection,
    follow_source,
    refresh_source_i2p,
    search_sources,
    source_identity,
)
from foxlight_model_preservation.i2p_http import I2PHTTPProxy, i2p_http_response
from foxlight_model_preservation.signing import encode_record
from foxlight_model_preservation.storage import (
    manifest_path,
    object_path,
    restore,
    verify_store,
)
from foxlight_model_preservation.transfer import I2PTransferPolicy

TUNNEL_JAR = os.environ.get("SOVEREIGN_I2P_TUNNEL_JAR")
pytestmark = pytest.mark.skipif(
    not (
        TUNNEL_JAR
        and os.environ.get("SOVEREIGN_I2P_RUNTIME")
        and os.environ.get("SOVEREIGN_I2P_ROUTER_JAR")
        and os.environ.get("SOVEREIGN_I2P_JAVA_HOME")
    ),
    reason="explicit optional stock I2P HTTP test runtime",
)


def test_stock_i2p_http_updates_and_full_recovery(capture, native_network, tmp_path):
    runtime, java, ports, _routers = native_network
    classes = tmp_path / "classes"
    classes.mkdir()
    classpath = os.pathsep.join((str(runtime / "snark.jar"), str(TUNNEL_JAR)))
    subprocess.run(  # noqa: S603 - explicit native test runtime and owned source
        [
            str(java.with_name("javac")),
            "-cp",
            classpath,
            "-d",
            str(classes),
            str(Path(__file__).parent / "native/I2PHTTPTest.java"),
        ],
        check=True,
    )
    original_envelope = (capture.store / manifest_path(capture.identity)).read_bytes()
    tokenizer = (capture.sources["tokenizer"] / "model.bin").read_bytes()
    file = max(
        verify_store(capture.store, capture.identity, capture.trust).members[0].files,
        key=lambda item: item.size,
    )
    partial_directory = tmp_path / "received/.partial"
    partial_directory.mkdir(parents=True)
    (partial_directory / file.sha256).write_bytes(
        (capture.store / object_path(file.sha256)).read_bytes()[:500]
    )
    range_requests = []
    publisher = Publisher(tmp_path / "publisher", capture)
    # One read-only backend exposes metadata and the synthetic archive objects.
    root = tmp_path / "http-root"
    root.mkdir()
    import shutil

    shutil.copytree(publisher.path, root / "source")
    shutil.copytree(capture.store, root / "model")

    class RangeHandler(SimpleHTTPRequestHandler):
        def do_GET(self):
            requested = self.headers.get("Range")
            if requested is None:
                return super().do_GET()
            if not re.fullmatch(
                r"/model/objects/sha256/[a-f0-9]{64}", self.path
            ) or not re.fullmatch(r"bytes=[0-9]{1,16}-", requested):
                self.send_error(416)
                return
            data = (root / self.path.lstrip("/")).read_bytes()
            start = int(requested[6:-1])
            if not 0 <= start < len(data):
                self.send_error(416)
                return
            range_requests.append(requested)
            self.send_response(206)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data) - start))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header(
                "Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}"
            )
            self.end_headers()
            self.wfile.write(data[start:])

    handler = partial(RangeHandler, directory=str(root))
    backend = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        proxy_port = reserve.getsockname()[1]
    processes, logs = [], []
    try:
        for mode, i2cp, port in (
            ("server", ports[0], backend.server_port),
            ("client", ports[2], proxy_port),
        ):
            directory = tmp_path / mode
            directory.mkdir()
            log = (directory / "console.log").open("wb")
            logs.append(log)
            processes.append(
                subprocess.Popen(  # noqa: S603 - owned private native helper
                    [
                        str(java),
                        "-Xmx128m",
                        "-Djava.awt.headless=true",
                        "-cp",
                        str(classes) + os.pathsep + classpath,
                        "I2PHTTPTest",
                        mode,
                        str(directory),
                        str(i2cp),
                        str(port),
                    ],
                    stdin=subprocess.PIPE,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            )
        wait_until(
            lambda: all(
                (tmp_path / mode / "ready.txt").exists()
                for mode in ("server", "client")
            ),
            seconds=90,
        )
        base = "http://" + (tmp_path / "server/destination.txt").read_text()
        publisher.mirrors = (base + "/model",)
        publisher.publish()
        shutil.copytree(publisher.path, root / "source", dirs_exist_ok=True)
        bootstrap = publisher.bootstrap(base + "/source")
        # The retained HTTP provider outlives all original publication/input trees.
        shutil.rmtree(publisher.path)
        shutil.rmtree(capture.store)
        for source in capture.sources.values():
            shutil.rmtree(source)
        cache = tmp_path / "cache"
        follow_source(cache, encode_record(bootstrap), bootstrap_identity(bootstrap))
        proxy = I2PHTTPProxy(port=proxy_port)

        def reachable():
            try:
                with i2p_http_response(
                    base, "source/metadata/1.root.json", proxy, 10
                ) as incoming:
                    return incoming.status == 200
            except OSError:
                return False

        # A local listener/session is not proof that the remote lease is routable.
        wait_until(reachable, seconds=90)
        status = refresh_source_i2p(
            cache, source_identity(bootstrap), proxy=proxy, allow_i2p=True
        )
        assert status.freshness == "fresh"
        assert search_sources(cache, "offline")[0].manifest == capture.identity
        fetch_selection(
            cache,
            source_identity(bootstrap),
            "small_text",
            capture.identity,
            capture.trust,
            tmp_path / "received",
            I2PTransferPolicy(proxy=proxy, timeout=60),
        )
        assert range_requests == ["bytes=500-"]
        assert (
            tmp_path / "received" / manifest_path(capture.identity)
        ).read_bytes() == original_envelope
        restore(
            tmp_path / "received",
            capture.identity,
            capture.trust,
            tmp_path / "restored",
        )
        assert (
            tmp_path / "restored/members/tokenizer/model.bin"
        ).read_bytes() == tokenizer
        for mode, process in zip(("server", "client"), processes, strict=True):
            socket_snapshot(process.pid, tmp_path / (mode + "-sockets.txt"))
    finally:
        for process in processes:
            process.stdin.close()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        for log in logs:
            log.close()
        backend.shutdown()
        thread.join(5)
        backend.server_close()
