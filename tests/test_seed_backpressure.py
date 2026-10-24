"""Real socket backpressure must not discard a progressing receiver."""

import hashlib
import http.client
import os
import socket
import threading
import time
from contextlib import contextmanager

import pytest

from foxlight_model_preservation.seed import SeedHandler, SeedServer
from foxlight_model_preservation.storage import object_path, pack, verify_store


@contextmanager
def slow_peer(capture, tmp_path, *, size=512 * 1024):
    payload = os.urandom(size)
    (capture.sources["weights"] / "model.bin").write_bytes(payload)
    store = tmp_path / "large-store"
    identity = pack(capture.recipe, capture.sources, store, (capture.key,))
    manifest = verify_store(store, identity, capture.trust)
    selected = next(
        file for file in manifest.members[0].files if file.path == "model.bin"
    )
    revoked = threading.Event()
    finished = threading.Event()

    class SmallBufferHandler(SeedHandler):
        def setup(self):
            super().setup()
            self.request.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8192)
            # Scale the real 30-second policy down for a deterministic fast test.
            self.request.settimeout(0.25)

    class ControlledSeed(SeedServer):
        def can_send(self, path):
            return not revoked.is_set()

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                finished.set()

    server = ControlledSeed(("127.0.0.1", 0), store, (identity,), capture.trust)
    server.RequestHandlerClass = SmallBufferHandler
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.connect()
        connection.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8192)
        yield server, connection, selected, payload, revoked, finished
    finally:
        connection.close()
        server.shutdown()
        thread.join(5)
        server.server_close()


@pytest.mark.parametrize("offset", [0, 777])
def test_continuously_slow_receiver_gets_full_verified_object(
    capture, tmp_path, offset
):
    with slow_peer(capture, tmp_path) as (
        server,
        client,
        selected,
        payload,
        _,
        finished,
    ):
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        client.request("GET", "/" + object_path(selected.sha256), headers=headers)
        incoming = client.getresponse()
        assert incoming.status == (206 if offset else 200)
        if offset:
            assert incoming.headers["Content-Range"] == (
                f"bytes {offset}-{len(payload) - 1}/{len(payload)}"
            )
        body = bytearray()
        while chunk := incoming.read(1024):
            body.extend(chunk)
            time.sleep(0.005)
        assert bytes(body) == payload[offset:]
        assert hashlib.sha256(payload[:offset] + body).hexdigest() == selected.sha256
        assert server.reserved_upload_bytes == len(payload) - offset
        assert finished.wait(2)


def test_stalled_receiver_releases_worker_and_keeps_full_reservation(capture, tmp_path):
    with slow_peer(capture, tmp_path, size=4 * 1024**2) as (
        server,
        client,
        selected,
        payload,
        _,
        finished,
    ):
        client.request("GET", "/" + object_path(selected.sha256))
        incoming = client.getresponse()
        assert incoming.status == 200
        # No body reads: a peer that stops making progress still times out.
        assert finished.wait(5)
        with pytest.raises(http.client.IncompleteRead) as stopped:
            incoming.read()
        assert len(stopped.value.partial) < len(payload)
        assert server.reserved_upload_bytes == len(payload)


def test_revocation_interrupts_slow_body_without_refunding_upload(capture, tmp_path):
    with slow_peer(capture, tmp_path, size=4 * 1024**2) as (
        server,
        client,
        selected,
        payload,
        revoked,
        finished,
    ):
        client.request("GET", "/" + object_path(selected.sha256))
        incoming = client.getresponse()
        assert incoming.status == 200
        body = bytearray(incoming.read(1024))
        revoked.set()
        started = time.monotonic()
        while chunk := incoming.read(1024):
            body.extend(chunk)
            time.sleep(0.005)
        assert len(body) < len(payload)
        assert finished.wait(2)
        assert time.monotonic() - started < 5
        assert server.reserved_upload_bytes == len(payload)
