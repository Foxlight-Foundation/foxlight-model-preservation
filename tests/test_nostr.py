"""Nostr relays as a discovery channel: BIP340, events and a local relay."""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import socket
import socketserver
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from foxlight_model_preservation.mirror_cards import issue_card
from foxlight_model_preservation.mirror_directory import MirrorDirectory
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.nostr import (
    EVENT_KIND,
    card_event,
    card_from_event,
    find_cards,
    public_key,
    publish_card,
    schnorr_sign,
    schnorr_verify,
)
from foxlight_model_preservation.transfer import TransferPolicy

LOCAL = TransferPolicy(allow_http=True, allow_private=True, timeout=3)
VECTORS = Path(__file__).parent / "fixtures/bip340/test-vectors.csv"


def test_bip340_matches_every_official_test_vector() -> None:
    rows = list(csv.DictReader(VECTORS.open()))
    assert len(rows) >= 15
    for row in rows:
        message = bytes.fromhex(row["message"])
        key = bytes.fromhex(row["public key"])
        signature = bytes.fromhex(row["signature"])
        if row["secret key"]:
            secret = bytes.fromhex(row["secret key"])
            assert public_key(secret) == key, row["index"]
            assert schnorr_sign(message, secret, bytes.fromhex(row["aux_rand"])) == (
                signature
            ), row["index"]
        expected = row["verification result"] == "TRUE"
        assert schnorr_verify(message, key, signature) is expected, row["comment"]


def test_events_carry_only_cards_signed_by_their_own_mirror_key() -> None:
    secret = bytes(range(1, 33))
    card = issue_card(Ed25519PrivateKey.generate(), ("https://m.example",), set())
    event = card_event(card, secret)
    assert event["kind"] == EVENT_KIND
    assert card_from_event(event) == card
    other = issue_card(Ed25519PrivateKey.generate(), ("https://x.example",), set())
    for field, value in (
        ("content", other.decode()),
        ("sig", "00" * 64),
        ("id", "11" * 32),
        ("tags", [["d", "ed25519:" + "0" * 64], ["t", "sovereign-mirror-v1"]]),
    ):
        assert card_from_event({**event, field: value}) is None, field
    assert card_from_event("not an event") is None


class _Relay(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _RelayHandler)
        self.events: dict[str, dict[str, object]] = {}


class _RelayHandler(socketserver.BaseRequestHandler):
    """A tiny relay: stores events, answers kind/#t filters, then EOSE."""

    def handle(self) -> None:
        sock: socket.socket = self.request
        header = b""
        while b"\r\n\r\n" not in header:
            header += sock.recv(1)
        key = next(
            line.split(":", 1)[1].strip()
            for line in header.decode().split("\r\n")
            if line.lower().startswith("sec-websocket-key:")
        )
        accept = base64.b64encode(
            hashlib.sha1(  # noqa: S324 - websocket protocol constant
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
            ).digest()
        ).decode()
        sock.sendall(
            (
                "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n"
            ).encode()
        )
        relay: _Relay = self.server  # type: ignore[assignment]
        while True:
            message = self._read(sock)
            if message is None:
                return
            if message[0] == "EVENT":
                event = message[1]
                relay.events[event["tags"][0][1]] = event
                self._write(sock, ["OK", event["id"], True, ""])
            elif message[0] == "REQ":
                for event in relay.events.values():
                    self._write(sock, ["EVENT", message[1], event])
                self._write(sock, ["EOSE", message[1]])

    @staticmethod
    def _read(sock: socket.socket) -> list | None:
        def exact(count: int) -> bytes:
            data = b""
            while len(data) < count:
                chunk = sock.recv(count - len(data))
                if not chunk:
                    raise ConnectionError
                data += chunk
            return data

        try:
            first, second = exact(2)
            length = second & 0x7F
            if length == 126:
                length = int.from_bytes(exact(2), "big")
            elif length == 127:
                length = int.from_bytes(exact(8), "big")
            mask = exact(4)
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(exact(length)))
        except (ConnectionError, OSError):
            return None
        if first & 0x0F == 0x8:
            return None
        return json.loads(payload)

    @staticmethod
    def _write(sock: socket.socket, message: object) -> None:
        payload = json.dumps(message).encode()
        if len(payload) < 126:
            head = bytes((0x81, len(payload)))
        else:
            head = bytes((0x81, 126)) + len(payload).to_bytes(2, "big")
        sock.sendall(head + payload)


@contextmanager
def relay() -> Generator[tuple[str, _Relay]]:
    server = _Relay()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"ws://127.0.0.1:{server.server_address[1]}", server
    finally:
        server.shutdown()
        server.server_close()


def test_mirror_cards_travel_through_independent_relays(tmp_path: Path) -> None:
    secret = bytes(range(1, 33))
    card = issue_card(
        Ed25519PrivateKey.generate(), ("https://mirror.example",), {"a" * 64}
    )
    with relay() as (first, _), relay() as (second, store):
        assert publish_card(card, secret, (first, second), LOCAL) == 2
        # A relay that is down only costs that relay.
        assert publish_card(card, secret, ("ws://127.0.0.1:9",), LOCAL) == 0
        # A relay that serves an altered event cannot change the card.
        forged = dict(next(iter(store.events.values())))
        forged["content"] = issue_card(
            Ed25519PrivateKey.generate(), ("https://evil.example",), {"a" * 64}
        ).decode()
        store.events["forged"] = forged
        found = find_cards((second,), LOCAL)
        assert found == [card]
        directory = MirrorDirectory(tmp_path / "client")
        assert directory.ingest(found) == 1
        assert directory.candidates("a" * 64) == ("https://mirror.example",)


def test_relays_must_be_plain_websocket_urls() -> None:
    from foxlight_model_preservation.nostr import Relay

    for url in (
        "https://relay.example",
        "wss://u:p@relay.example",
        "ws://relay.example",
    ):
        with pytest.raises((PreservationError, OSError)):
            Relay(url, TransferPolicy())


class _HostileHandler(_RelayHandler):
    """Upgrades, then misbehaves: endless pings or deeply nested JSON."""

    mode = "pings"

    def handle(self) -> None:
        sock: socket.socket = self.request
        header = b""
        while b"\r\n\r\n" not in header:
            header += sock.recv(1)
        key = next(
            line.split(":", 1)[1].strip()
            for line in header.decode().split("\r\n")
            if line.lower().startswith("sec-websocket-key:")
        )
        accept = base64.b64encode(
            hashlib.sha1(  # noqa: S324 - websocket protocol constant
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
            ).digest()
        ).decode()
        sock.sendall(
            (
                "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n"
            ).encode()
        )
        self._read(sock)
        try:
            if self.mode == "nested":
                payload = b"[" * 100_000 + b"]" * 100_000
                sock.sendall(bytes((0x81, 127)) + len(payload).to_bytes(8, "big"))
                sock.sendall(payload)
                time.sleep(2)
                return
            for _ in range(400):
                sock.sendall(bytes((0x89, 0)))  # A ping, then another...
                time.sleep(0.05)
        except OSError:
            return


@contextmanager
def hostile(mode: str) -> Generator[str]:
    handler = type("Handler", (_HostileHandler,), {"mode": mode})
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"ws://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("mode", ["pings", "nested"])
def test_a_misbehaving_relay_costs_only_itself(mode: str) -> None:
    secret = bytes(range(1, 33))
    card = issue_card(Ed25519PrivateKey.generate(), ("https://m.example",), set())
    policy = TransferPolicy(allow_http=True, allow_private=True, timeout=2)
    with hostile(mode) as bad, relay() as (good, _):
        assert publish_card(card, secret, (good,), LOCAL) == 1
        started = time.monotonic()
        assert find_cards((bad, good), policy) == [card]
        assert time.monotonic() - started < 8


def test_a_relay_trickling_its_upgrade_headers_cannot_stall_discovery() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    stop = threading.Event()

    def trickle() -> None:
        try:
            connection, _ = listener.accept()
        except OSError:
            return
        try:
            connection.recv(4096)
            while not stop.is_set():
                connection.sendall(b"X")  # Headers that never finish.
                time.sleep(0.3)
        except OSError:
            pass
        finally:
            connection.close()

    threading.Thread(target=trickle, daemon=True).start()
    secret = bytes(range(1, 33))
    card = issue_card(Ed25519PrivateKey.generate(), ("https://m.example",), set())
    policy = TransferPolicy(allow_http=True, allow_private=True, timeout=2)
    try:
        with relay() as (good, _):
            assert publish_card(card, secret, (good,), LOCAL) == 1
            started = time.monotonic()
            assert find_cards((f"ws://127.0.0.1:{port}", good), policy) == [card]
            assert time.monotonic() - started < 6
    finally:
        stop.set()
        listener.close()
