"""Publish and find mirror cards on Nostr relays.

Nostr relays are many independently run servers. A mirror publishes its signed
card as a replaceable event to several relays, and a client queries any relays
it chooses. No single relay operator controls discovery. The card inside each
event is still verified with the mirror's own Ed25519 key, so a relay can drop
or delay cards but never forge or alter one.

Relays see the network addresses of the publishing mirror and of each querying
client; the app's disclosure says so. The secp256k1 key used for Nostr events
is separate from every model, publishing and mirror key and signs only these
public announcements.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import time
from collections.abc import Iterable
from typing import cast
from urllib.parse import urlsplit

from .mirror_cards import verify_card
from .models import PreservationError
from .operations import OperationControl
from .transfer import PinnedConnection, TransferPolicy

EVENT_KIND = 30471  # Parameterized replaceable: one current card per mirror key.
TOPIC = "sovereign-mirror-v1"
MAX_MESSAGE_BYTES = 1024 * 1024
MAX_EVENTS = 500
DEFAULT_RELAYS = (
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.primal.net",
    "wss://relay.nostr.band",
    "wss://nostr.mom",
)

# BIP340 Schnorr signatures over secp256k1, following the specification's
# reference algorithm. Signing is not constant-time; this key signs only public
# mirror announcements, never anything that grants trust.
_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_G = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)
_Point = tuple[int, int] | None


def _tagged(tag: str, message: bytes) -> bytes:
    prefix = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(prefix + prefix + message).digest()


def _add(left: _Point, right: _Point) -> _Point:
    if left is None:
        return right
    if right is None:
        return left
    if left[0] == right[0] and left[1] != right[1]:
        return None
    if left == right:
        slope = 3 * left[0] * left[0] * pow(2 * left[1], _P - 2, _P) % _P
    else:
        slope = (right[1] - left[1]) * pow(right[0] - left[0], _P - 2, _P) % _P
    x = (slope * slope - left[0] - right[0]) % _P
    return x, (slope * (left[0] - x) - left[1]) % _P


def _mul(point: _Point, scalar: int) -> _Point:
    result: _Point = None
    for bit in range(256):
        if (scalar >> bit) & 1:
            result = _add(result, point)
        point = _add(point, point)
    return result


def _lift_x(x: int) -> _Point:
    if x >= _P:
        return None
    square = (pow(x, 3, _P) + 7) % _P
    y = pow(square, (_P + 1) // 4, _P)
    if pow(y, 2, _P) != square:
        return None
    return x, y if y % 2 == 0 else _P - y


def _bytes(value: int) -> bytes:
    return value.to_bytes(32, "big")


def public_key(secret: bytes) -> bytes:
    """Return the 32-byte x-only public key for a 32-byte secret key."""
    scalar = int.from_bytes(secret, "big")
    if not 1 <= scalar < _N:
        raise PreservationError("invalid secp256k1 secret key")
    point = _mul(_G, scalar)
    assert point is not None  # noqa: S101 - scalar is in range, never infinity
    return _bytes(point[0])


def schnorr_sign(message: bytes, secret: bytes, aux: bytes | None = None) -> bytes:
    """Sign a message with BIP340 Schnorr."""
    scalar = int.from_bytes(secret, "big")
    if not 1 <= scalar < _N:
        raise PreservationError("invalid secp256k1 secret key")
    point = _mul(_G, scalar)
    assert point is not None  # noqa: S101
    scalar = scalar if point[1] % 2 == 0 else _N - scalar
    randomness = secrets.token_bytes(32) if aux is None else aux
    masked = bytes(
        a ^ b
        for a, b in zip(_bytes(scalar), _tagged("BIP0340/aux", randomness), strict=True)
    )
    nonce = (
        int.from_bytes(
            _tagged("BIP0340/nonce", masked + _bytes(point[0]) + message), "big"
        )
        % _N
    )
    if nonce == 0:
        raise PreservationError("BIP340 nonce generation failed")
    commitment = _mul(_G, nonce)
    assert commitment is not None  # noqa: S101
    nonce = nonce if commitment[1] % 2 == 0 else _N - nonce
    challenge = (
        int.from_bytes(
            _tagged(
                "BIP0340/challenge",
                _bytes(commitment[0]) + _bytes(point[0]) + message,
            ),
            "big",
        )
        % _N
    )
    return _bytes(commitment[0]) + _bytes((nonce + challenge * scalar) % _N)


def schnorr_verify(message: bytes, key: bytes, signature: bytes) -> bool:
    """Verify a BIP340 Schnorr signature with an x-only public key."""
    if len(key) != 32 or len(signature) != 64:
        return False
    point = _lift_x(int.from_bytes(key, "big"))
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    if point is None or r >= _P or s >= _N:
        return False
    challenge = (
        int.from_bytes(
            _tagged("BIP0340/challenge", signature[:32] + key + message), "big"
        )
        % _N
    )
    commitment = _add(_mul(_G, s), _mul(point, _N - challenge))
    return commitment is not None and commitment[1] % 2 == 0 and commitment[0] == r


# Events -------------------------------------------------------------------


def _event_id(event: dict[str, object]) -> str:
    body = json.dumps(
        [
            0,
            event["pubkey"],
            event["created_at"],
            event["kind"],
            event["tags"],
            event["content"],
        ],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def card_event(
    card: bytes, secret: bytes, *, now: int | None = None
) -> dict[str, object]:
    """Wrap a signed mirror card in a replaceable Nostr event for its mirror key."""
    parsed = verify_card(card, now=now)
    event: dict[str, object] = {
        "pubkey": public_key(secret).hex(),
        "created_at": int(time.time()) if now is None else now,
        "kind": EVENT_KIND,
        "tags": [["d", parsed.mirror_key.key_id], ["t", TOPIC]],
        "content": card.decode("utf-8"),
    }
    event["id"] = _event_id(event)
    event["sig"] = schnorr_sign(bytes.fromhex(str(event["id"])), secret).hex()
    return event


def card_from_event(event: object, *, now: int | None = None) -> bytes | None:
    """Return the verified mirror card an event carries, or None."""
    if not isinstance(event, dict):
        return None
    source = cast(dict[str, object], event)
    try:
        fields: dict[str, object] = {
            key: source[key]
            for key in ("id", "pubkey", "created_at", "kind", "tags", "content", "sig")
        }
        if (
            fields["kind"] != EVENT_KIND
            or not isinstance(fields["content"], str)
            or not isinstance(fields["tags"], list)
            or _event_id(fields) != fields["id"]
            or not schnorr_verify(
                bytes.fromhex(str(fields["id"])),
                bytes.fromhex(str(fields["pubkey"])),
                bytes.fromhex(str(fields["sig"])),
            )
        ):
            return None
        card = str(fields["content"]).encode("utf-8")
        parsed = verify_card(card, now=now)
        # The replaceable slot must name the card's own mirror key.
        if ["d", parsed.mirror_key.key_id] not in cast(list[object], fields["tags"]):
            return None
        return card
    except (KeyError, ValueError, TypeError, PreservationError):
        return None


# A minimal, bounded websocket client (RFC 6455) -----------------------------


class Relay:
    """One bounded websocket connection to a Nostr relay, with pinned DNS."""

    def __init__(self, url: str, policy: TransferPolicy) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"wss", "ws"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or (parsed.scheme == "ws" and not policy.allow_http)
        ):
            raise PreservationError("relay must be a credential-free wss:// URL")
        connection = PinnedConnection(
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "wss" else 80),
            policy,
            tls=parsed.scheme == "wss",
        )
        connection.connect()
        assert connection.sock is not None  # noqa: S101
        self._socket: socket.socket = connection.sock
        self._socket.settimeout(policy.timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        host = parsed.hostname + (f":{parsed.port}" if parsed.port else "")
        path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
        self._socket.sendall(
            (
                f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = self._socket.recv(1)
            if not chunk or len(header) > 16384:
                raise PreservationError("relay handshake failed")
            header += chunk
        expected = base64.b64encode(
            hashlib.sha1(  # noqa: S324 - fixed by the websocket protocol
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
            ).digest()
        ).decode()
        lines = header.decode("latin-1").split("\r\n")
        if not lines[0].startswith("HTTP/1.1 101") or not any(
            line.lower().startswith("sec-websocket-accept:")
            and line.split(":", 1)[1].strip() == expected
            for line in lines
        ):
            raise PreservationError("relay refused the websocket upgrade")

    def send(self, message: object) -> None:
        """Send one masked text frame holding a JSON message."""
        payload = json.dumps(message, separators=(",", ":")).encode()
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            head = bytes((0x81, 0x80 | length))
        elif length < 65536:
            head = bytes((0x81, 0x80 | 126)) + length.to_bytes(2, "big")
        else:
            head = bytes((0x81, 0x80 | 127)) + length.to_bytes(8, "big")
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self._socket.sendall(head + mask + masked)

    def _exact(self, count: int) -> bytes:
        data = b""
        while len(data) < count:
            chunk = self._socket.recv(count - len(data))
            if not chunk:
                raise PreservationError("relay closed the connection")
            data += chunk
        return data

    def receive(self) -> list[object]:
        """Receive one JSON message, answering pings and joining fragments."""
        message = b""
        while True:
            first, second = self._exact(2)
            length = second & 0x7F
            if length == 126:
                length = int.from_bytes(self._exact(2), "big")
            elif length == 127:
                length = int.from_bytes(self._exact(8), "big")
            if length + len(message) > MAX_MESSAGE_BYTES:
                raise PreservationError("relay message exceeds size limit")
            mask = self._exact(4) if second & 0x80 else b""
            payload = self._exact(length)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            opcode = first & 0x0F
            if opcode == 0x8:
                raise PreservationError("relay closed the connection")
            if opcode == 0x9:
                self._control(0xA, payload)
                continue
            if opcode in {0x1, 0x0}:
                message += payload
                if first & 0x80:
                    value: object = json.loads(message.decode("utf-8"))
                    if not isinstance(value, list):
                        return []
                    return cast(list[object], value)
            # Binary and pong frames are ignored.

    def _control(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload[:125]))
        self._socket.sendall(bytes((0x80 | opcode, 0x80 | len(masked))) + mask + masked)

    def close(self) -> None:
        """Close the websocket and its socket."""
        try:
            self._control(0x8, b"")
        except OSError:
            pass
        self._socket.close()


def publish_card(
    card: bytes,
    secret: bytes,
    relays: Iterable[str],
    policy: TransferPolicy,
    *,
    control: OperationControl | None = None,
) -> int:
    """Publish a mirror card to each relay; return how many accepted it."""
    active = control or OperationControl()
    event = card_event(card, secret)
    accepted = 0
    for url in relays:
        active.checkpoint()
        try:
            relay = Relay(url, policy)
        except (OSError, ValueError):
            continue
        try:
            relay.send(["EVENT", event])
            deadline = time.monotonic() + policy.timeout
            while time.monotonic() < deadline:
                reply = relay.receive()
                if len(reply) >= 3 and reply[0] == "OK" and reply[1] == event["id"]:
                    accepted += reply[2] is True
                    break
        except (OSError, ValueError):
            pass
        finally:
            relay.close()
    return accepted


def find_cards(
    relays: Iterable[str],
    policy: TransferPolicy,
    *,
    control: OperationControl | None = None,
) -> list[bytes]:
    """Ask relays for current mirror cards; return every one that verifies."""
    active = control or OperationControl()
    cards: dict[bytes, None] = {}
    for url in relays:
        active.checkpoint()
        try:
            relay = Relay(url, policy)
        except (OSError, ValueError):
            continue
        subscription = secrets.token_hex(8)
        try:
            relay.send(
                [
                    "REQ",
                    subscription,
                    {"kinds": [EVENT_KIND], "#t": [TOPIC], "limit": MAX_EVENTS},
                ]
            )
            received = 0
            deadline = time.monotonic() + policy.timeout
            while time.monotonic() < deadline and received < MAX_EVENTS:
                message = relay.receive()
                if not message:
                    continue
                if message[0] == "EOSE":
                    break
                if message[0] == "EVENT" and len(message) >= 3:
                    received += 1
                    card = card_from_event(message[2])
                    if card is not None:
                        cards[card] = None
            relay.send(["CLOSE", subscription])
        except (OSError, ValueError):
            pass
        finally:
            relay.close()
    return list(cards)
