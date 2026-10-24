"""Receive-only HTTP through an explicitly selected local I2P proxy.

Only canonical hash-addressed I2P destinations are accepted. This module never
resolves DNS, follows redirects, starts a router, or selects an Internet outproxy.
The operator's proxy/router remains a separately trusted transport dependency.
"""

from __future__ import annotations

import base64
import http.client
import re
import socket
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import urlsplit

from .models import PreservationError


@dataclass(frozen=True)
class I2PHTTPProxy:
    """One explicit numeric loopback proxy; no ambient proxy or router adoption."""

    host: str = "127.0.0.1"
    port: int = 4444

    def validate(self) -> None:
        """Refuse nonlocal, named, malformed or unbounded control destinations."""
        if self.host not in {"127.0.0.1", "::1"} or (
            type(self.port) is not int or not 1 <= self.port <= 65535
        ):
            raise PreservationError("I2P HTTP proxy must be numeric loopback")


def i2p_http_endpoint(source: str) -> str:
    """Validate a canonical I2P-only HTTP base without resolving its name.

    The initial profile supports ordinary 52-character destination hashes, not
    human address-book aliases, address helpers, blinded names or exit proxies.
    """
    if not re.fullmatch(
        r"http://[a-z2-7]{52}\.b32\.i2p(?::[1-9][0-9]{0,4})?(?:/[A-Za-z0-9._~/-]*)?",
        source,
        flags=re.ASCII,
    ):
        raise PreservationError("I2P HTTP requires a canonical b32.i2p destination")
    parsed = urlsplit(source)
    encoded = (parsed.hostname or "").split(".")[0]
    raw = base64.b32decode(encoded.upper() + "====")
    if base64.b32encode(raw).decode().rstrip("=").lower() != encoded:
        raise PreservationError("I2P destination hash is not canonical")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise PreservationError("I2P HTTP destination port is invalid")
    if any(part in {".", ".."} for part in parsed.path.split("/")) or (
        "//" in parsed.path
    ):
        raise PreservationError("I2P HTTP path is not canonical")
    return source


class _ProxyConnection(http.client.HTTPConnection):
    def __init__(self, proxy: I2PHTTPProxy, timeout: float):
        super().__init__(proxy.host, proxy.port, timeout=timeout)
        self.proxy = proxy

    def connect(self) -> None:
        # Do not call create_connection/getaddrinfo, even for numeric loopback.
        family = socket.AF_INET6 if self.proxy.host == "::1" else socket.AF_INET
        connection = socket.socket(family, socket.SOCK_STREAM)
        connection.settimeout(self.timeout)
        try:
            connection.connect((self.proxy.host, self.proxy.port))
            self.sock = connection
        except BaseException:
            connection.close()
            raise


@contextmanager
def i2p_http_response(
    source: str,
    relative: str,
    proxy: I2PHTTPProxy,
    timeout: float,
    *,
    offset: int = 0,
) -> Generator[http.client.HTTPResponse]:
    """Issue one bounded-time GET through loopback; never retry another route.

    Byte limits, hashes, authority and overall deadlines belong to the caller's
    existing transfer/TUF verifier. No cookies, referrers, telemetry or payload
    uploads are sent. The local router can still relay other encrypted traffic.
    """
    proxy.validate()
    i2p_http_endpoint(source)
    if (
        not 0 < timeout <= 300
        or type(offset) is not int
        or not 0 <= offset <= 2**53 - 1
        or not re.fullmatch(r"[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*", relative)
        or any(part in {".", ".."} for part in relative.split("/"))
    ):
        raise PreservationError("invalid bounded I2P HTTP request")
    parsed = urlsplit(source)
    target = source.rstrip("/") + "/" + relative
    connection = _ProxyConnection(proxy, timeout)
    headers = {
        "Host": parsed.netloc,
        "Accept-Encoding": "identity",
        "Connection": "close",
    }
    if offset:
        headers["Range"] = f"bytes={offset}-"
    try:
        connection.request("GET", target, headers=headers)
        incoming = connection.getresponse()
        entries = incoming.getheaders()
        if len(entries) > 64 or sum(len(k) + len(v) for k, v in entries) > 16384:
            raise PreservationError("I2P HTTP response headers exceed bounds")
        lengths = [v for k, v in entries if k.lower() == "content-length"]
        encodings = [v for k, v in entries if k.lower() == "transfer-encoding"]
        if (
            len(lengths) > 1
            or len(encodings) > 1
            or (lengths and encodings)
            or (lengths and not re.fullmatch(r"[0-9]{1,16}", lengths[0]))
            or (encodings and encodings != ["chunked"])
            or incoming.getheader("Content-Encoding", "identity") != "identity"
        ):
            raise PreservationError("I2P HTTP response framing is unsupported")
        yield incoming
    finally:
        connection.close()
