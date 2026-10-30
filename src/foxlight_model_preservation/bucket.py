"""Bucket mirrors: a public-read bucket is a complete mirror.

A Sovereign store is only ``manifests/`` and ``objects/sha256/``, which any
static HTTPS host can serve. A public-read bucket at Cloudflare R2, Backblaze
B2, Amazon S3, Wasabi or any S3-compatible service is therefore a mirror that
needs no server, opens no port and exposes no home IP address.

This module uploads with the standard library only (AWS Signature Version 4).
Every object is verified locally before upload and the signed manifest is
uploaded last, so a recipient never sees a manifest whose bytes are missing.
Nothing is ever deleted except a manifest the owner explicitly removes.
Credentials live only in memory and in the operating system's keychain; they
never appear in configuration files, logs, exceptions or representations.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import os
import re
import subprocess
import time
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree

from pydantic import Field, field_validator, model_validator

from .archives import archive_objects, verify_archive_contents
from .mirror_cards import PEERS_PATH, base_url
from .models import PreservationError, Record, TrustProfile
from .operations import OperationControl
from .storage import CHUNK_SIZE, directory, manifest_path, object_path, open_file
from .transfer import PinnedConnection, TransferPolicy

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
# Recorded on each object only after its full SHA-256 was verified on upload.
SHA256_METADATA = "x-amz-meta-sovereign-sha256"
SINGLE_PUT_LIMIT = 64 * 1024**2
PART_SIZE = 64 * 1024**2
CHECK_PATH = ".well-known/sovereign/bucket-check.json"
KEYCHAIN_SERVICE = "sovereign-bucket"

Provider = Literal["r2", "b2", "s3", "wasabi", "other"]


@dataclass(frozen=True)
class ProviderPreset:
    """What a provider needs from the owner, and how to address it."""

    name: str
    asks: tuple[str, ...]
    path_style: bool
    note: str


PRESETS: dict[str, ProviderPreset] = {
    "r2": ProviderPreset(
        "Cloudflare R2",
        ("account_id", "bucket", "public_url"),
        True,
        "Recommended: R2 charges nothing for downloads. In the bucket's Settings, "
        "turn on Public access (the r2.dev subdomain or your own domain) and paste "
        "that public address here.",
    ),
    "b2": ProviderPreset(
        "Backblaze B2",
        ("region", "bucket"),
        False,
        "Create a public bucket, then an application key limited to it. The region "
        "is in the bucket's S3 endpoint, for example us-west-004.",
    ),
    "s3": ProviderPreset(
        "Amazon S3",
        ("region", "bucket"),
        False,
        "Allow public reads on the bucket (turn off Block Public Access and add a "
        "read-only bucket policy). Downloads are billed by Amazon.",
    ),
    "wasabi": ProviderPreset(
        "Wasabi",
        ("region", "bucket"),
        False,
        "Create a bucket with public read access in your chosen region.",
    ),
    "other": ProviderPreset(
        "Other S3-compatible storage",
        ("endpoint", "region", "bucket", "public_url"),
        True,
        "For MinIO or any S3-compatible service: give its endpoint and the public "
        "address recipients will download from.",
    ),
}


class BucketTarget(Record):
    """Where a bucket mirror lives. It holds no secret, only a keychain name."""

    schema_version: Literal["sovereign.bucket-target.v1"] = "sovereign.bucket-target.v1"
    provider: Provider
    endpoint: str = Field(min_length=8, max_length=2048)
    region: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    bucket: str = Field(
        min_length=3, max_length=63, pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$"
    )
    path_style: bool
    public_url: str = Field(min_length=8, max_length=2048)
    key_name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")

    @field_validator("endpoint", "public_url")
    @classmethod
    def plain(cls, value: str) -> str:
        """Endpoints and public addresses are credential-free HTTP(S) base URLs."""
        return base_url(value)

    @model_validator(mode="after")
    def tls(self) -> BucketTarget:
        """Uploads carry credentials, so endpoints must use HTTPS.

        The one exception is a loopback endpoint (a local MinIO or test server),
        whose traffic never leaves this computer.
        """
        parsed = urlsplit(self.endpoint)
        loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not loopback:
            raise PreservationError("bucket endpoint must use HTTPS")
        return self


def target_for(
    provider: Provider,
    *,
    bucket: str,
    key_name: str,
    account_id: str | None = None,
    region: str | None = None,
    endpoint: str | None = None,
    public_url: str | None = None,
) -> BucketTarget:
    """Build a target from the few answers a provider preset asks for."""
    preset = PRESETS[provider]
    if provider == "r2":
        if not account_id or not re.fullmatch(r"[0-9a-f]{32}", account_id):
            raise PreservationError("bucket_account_id_invalid")
        endpoint, region = f"https://{account_id}.r2.cloudflarestorage.com", "auto"
    elif provider == "b2":
        endpoint = f"https://s3.{region}.backblazeb2.com"
        public_url = public_url or f"https://{bucket}.s3.{region}.backblazeb2.com"
    elif provider == "s3":
        endpoint = f"https://s3.{region}.amazonaws.com"
        public_url = public_url or f"https://{bucket}.s3.{region}.amazonaws.com"
    elif provider == "wasabi":
        endpoint = f"https://s3.{region}.wasabisys.com"
        public_url = public_url or f"https://s3.{region}.wasabisys.com/{bucket}"
    if not endpoint or not region or not public_url:
        raise PreservationError("bucket_details_incomplete")
    return BucketTarget(
        provider=provider,
        endpoint=endpoint,
        region=region,
        bucket=bucket,
        path_style=preset.path_style,
        public_url=public_url,
        key_name=key_name,
    )


@dataclass(frozen=True)
class BucketCredentials:
    """An access key pair kept only in memory; it never prints its secret."""

    access_key: str
    secret_key: str = field(repr=False)

    def __post_init__(self) -> None:
        if not (
            1 <= len(self.access_key) <= 256
            and 1 <= len(self.secret_key) <= 512
            and self.access_key.isprintable()
            and self.secret_key.isprintable()
            and not any(char.isspace() for char in self.access_key + self.secret_key)
        ):
            raise PreservationError("bucket_credentials_invalid")

    def __repr__(self) -> str:
        return f"BucketCredentials(access_key={self.access_key[:4]}…)"


# Keychain ------------------------------------------------------------------


def save_credentials(
    key_name: str, credentials: BucketCredentials, *, keychain: Path | None = None
) -> None:
    """Store a key pair in the OS keychain (macOS Keychain or Secret Service).

    ``keychain`` selects a specific macOS keychain file instead of the default.
    """
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", key_name):
        raise PreservationError("bucket_key_name_invalid")
    secret = credentials.access_key + " " + credentials.secret_key
    if os.uname().sysname == "Darwin":
        # Interactive mode reads the command from stdin, so the secret never
        # appears in any process's argument list; -X takes it hex-encoded.
        _run(
            ["/usr/bin/security", "-i"],
            f"add-generic-password -U -s {KEYCHAIN_SERVICE} -a {key_name} "
            f"-X {secret.encode().hex()}"
            + (f" {_keychain_arg(keychain)}" if keychain else "")
            + "\n",
        )
    else:
        _run(
            [
                "secret-tool",
                "store",
                "--label",
                f"Sovereign bucket {key_name}",
                "service",
                KEYCHAIN_SERVICE,
                "account",
                key_name,
            ],
            secret,
        )
    # Some keychain front ends report success on failure; read the item back.
    if load_credentials(key_name, keychain=keychain) != credentials:
        raise PreservationError("bucket_keychain_unavailable")


def load_credentials(
    key_name: str, *, keychain: Path | None = None
) -> BucketCredentials:
    """Read a key pair back from the OS keychain."""
    if os.uname().sysname == "Darwin":
        raw = _run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                key_name,
                "-w",
                *([str(keychain)] if keychain else []),
            ],
            None,
        )
    else:
        raw = _run(
            ["secret-tool", "lookup", "service", KEYCHAIN_SERVICE, "account", key_name],
            None,
        )
    access, _, secret = raw.rstrip("\n").partition(" ")
    return BucketCredentials(access, secret)


def _keychain_arg(path: Path) -> str:
    text = str(path)
    if not re.fullmatch(r"[A-Za-z0-9._/~-]+", text):
        raise PreservationError("bucket_keychain_path_invalid")
    return text


def _run(command: list[str], stdin: str | None) -> str:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed system keychain tools
            command,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        # The original exception can carry the command and its input; it is
        # deliberately not chained.
        raise PreservationError("bucket_keychain_unavailable") from None
    if completed.returncode != 0:
        # Keychain tools may echo arguments; never surface their output.
        raise PreservationError("bucket_keychain_unavailable")
    return completed.stdout


# Signature Version 4 --------------------------------------------------------


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


def sign_request(
    method: str,
    host: str,
    path: str,
    query: dict[str, str],
    headers: dict[str, str],
    payload_hash: str,
    credentials: BucketCredentials,
    region: str,
    amz_date: str,
) -> dict[str, str]:
    """Return the headers for an AWS Signature Version 4 request (service s3)."""
    signed = {
        **{name.lower(): " ".join(value.split()) for name, value in headers.items()},
        "host": host,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
    }
    names = sorted(signed)
    canonical = "\n".join(
        (
            method,
            path,
            "&".join(
                f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}"
                for k, v in sorted(query.items())
            ),
            "".join(f"{name}:{signed[name]}\n" for name in names),
            ";".join(names),
            payload_hash,
        )
    )
    date = amz_date[:8]
    scope = f"{date}/{region}/s3/aws4_request"
    to_sign = "\n".join(
        (
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical.encode()).hexdigest(),
        )
    )
    key = _hmac(
        _hmac(
            _hmac(_hmac(("AWS4" + credentials.secret_key).encode(), date), region), "s3"
        ),
        "aws4_request",
    )
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        **{name: value for name, value in headers.items()},
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={credentials.access_key}/{scope}, "
            f"SignedHeaders={';'.join(names)}, Signature={signature}"
        ),
    }


# Client -----------------------------------------------------------------------


class BucketClient:
    """A minimal S3 client for one bucket: PUT, HEAD, GET, DELETE and multipart."""

    def __init__(
        self,
        target: BucketTarget,
        credentials: BucketCredentials,
        policy: TransferPolicy | None = None,
    ) -> None:
        self.target = target
        self._credentials = credentials
        self._policy = policy or TransferPolicy(timeout=60)
        parsed = urlsplit(target.endpoint)
        assert parsed.hostname is not None  # noqa: S101 - validated by BucketTarget
        self._tls = parsed.scheme == "https"
        self._port = parsed.port or (443 if self._tls else 80)
        if target.path_style:
            self._host = parsed.hostname
            self._prefix = f"/{target.bucket}"
        else:
            self._host = f"{target.bucket}.{parsed.hostname}"
            self._prefix = ""

    def _authority(self) -> str:
        default = 443 if self._tls else 80
        return self._host if self._port == default else f"{self._host}:{self._port}"

    @contextmanager
    def _request(
        self,
        method: str,
        key: str,
        *,
        query: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        payload_hash: str = EMPTY_SHA256,
        body: Callable[[http.client.HTTPConnection], None] | bytes | None = None,
        length: int = 0,
    ) -> Generator[http.client.HTTPResponse]:
        path = self._prefix + "/" + quote(key, safe="/-_.~")
        amz_date = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        signed = sign_request(
            method,
            self._authority(),
            path,
            query or {},
            headers or {},
            payload_hash,
            self._credentials,
            self.target.region,
            amz_date,
        )
        connection = PinnedConnection(
            self._host, self._port, self._policy, tls=self._tls
        )
        target_path = path + (
            "?"
            + "&".join(
                f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}"
                for k, v in sorted((query or {}).items())
            )
            if query
            else ""
        )
        try:
            connection.putrequest(
                method, target_path, skip_host=True, skip_accept_encoding=True
            )
            connection.putheader("Host", self._authority())
            for name, value in signed.items():
                connection.putheader(name, value)
            if method in {"PUT", "POST"}:
                connection.putheader("Content-Length", str(length))
            connection.endheaders()
            if isinstance(body, bytes):
                connection.send(body)
            elif body is not None:
                body(connection)
            yield connection.getresponse()
        except http.client.HTTPException as error:
            raise PreservationError("bucket_request_failed") from error
        finally:
            connection.close()

    def _check(self, response: http.client.HTTPResponse, *ok: int) -> bytes:
        data = response.read(1024 * 1024)
        if response.status not in ok:
            code = re.search(rb"<Code>([A-Za-z]+)</Code>", data)
            raise PreservationError(
                "bucket_refused:" + (code[1].decode() if code else str(response.status))
            )
        return data

    def head(self, key: str) -> tuple[int, str] | None:
        """Return an object's size and recorded SHA-256, or None if absent.

        The SHA-256 is the value Sovereign recorded when it uploaded and
        verified the object; an object without it is not trusted as complete.
        """
        with self._request("HEAD", key) as response:
            response.read()
            if response.status == 404:
                return None
            if response.status != 200:
                raise PreservationError(f"bucket_refused:{response.status}")
            length = response.getheader("Content-Length")
            return (
                int(length) if length and length.isdecimal() else -1,
                response.getheader(SHA256_METADATA, "") or "",
            )

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        """Upload a small object whose bytes are already in memory."""
        with self._request(
            "PUT",
            key,
            headers={"Content-Type": content_type, "Cache-Control": "no-cache"},
            payload_hash=hashlib.sha256(data).hexdigest(),
            body=data,
            length=len(data),
        ) as response:
            self._check(response, 200)

    def put_file(
        self,
        key: str,
        root: int,
        path: str,
        size: int,
        sha256: str,
        control: OperationControl,
    ) -> None:
        """Upload one immutable object, in parts when it is large."""
        if size <= SINGLE_PUT_LIMIT:

            def send(connection: http.client.HTTPConnection) -> None:
                with open_file(root, path) as stream:
                    remaining = size
                    while remaining:
                        control.checkpoint()
                        chunk = stream.read(min(CHUNK_SIZE, remaining))
                        if not chunk:
                            raise PreservationError("object changed during upload")
                        connection.send(chunk)
                        control.advance(copied=len(chunk))
                        remaining -= len(chunk)

            with self._request(
                "PUT",
                key,
                headers={
                    "Content-Type": "application/octet-stream",
                    "Cache-Control": "public, max-age=31536000, immutable",
                    # The provider rejects a body whose SHA-256 differs from
                    # the signed payload hash, so this record is truthful.
                    SHA256_METADATA: sha256,
                },
                payload_hash=sha256,
                body=send,
                length=size,
            ) as response:
                self._check(response, 200)
            return
        self._multipart(key, root, path, size, sha256, control)

    def _multipart(
        self,
        key: str,
        root: int,
        path: str,
        size: int,
        sha256: str,
        control: OperationControl,
    ) -> None:
        with self._request(
            "POST",
            key,
            query={"uploads": ""},
            headers={
                "Content-Type": "application/octet-stream",
                "Cache-Control": "public, max-age=31536000, immutable",
                SHA256_METADATA: sha256,
            },
        ) as response:
            created = self._check(response, 200)
        upload = _xml_text(created, "UploadId")
        parts: list[tuple[int, str]] = []
        whole = hashlib.sha256()
        try:
            with open_file(root, path) as stream:
                number, offset = 1, 0
                while offset < size:
                    control.checkpoint()
                    count = min(PART_SIZE, size - offset)
                    data = stream.read(count)
                    if len(data) != count:
                        raise PreservationError("object changed during upload")
                    whole.update(data)
                    with self._request(
                        "PUT",
                        key,
                        query={"partNumber": str(number), "uploadId": upload},
                        payload_hash=hashlib.sha256(data).hexdigest(),
                        body=data,
                        length=count,
                    ) as response:
                        self._check(response, 200)
                        etag = response.getheader("ETag") or ""
                    parts.append((number, etag))
                    control.advance(copied=count)
                    number, offset = number + 1, offset + count
            # The parts must add up to the exact object, or the upload is
            # aborted before it can ever become visible.
            if whole.hexdigest() != sha256:
                raise PreservationError("object changed during upload")
            document = (
                "<CompleteMultipartUpload>"
                + "".join(
                    f"<Part><PartNumber>{n}</PartNumber><ETag>{e}</ETag></Part>"
                    for n, e in parts
                )
                + "</CompleteMultipartUpload>"
            ).encode()
            with self._request(
                "POST",
                key,
                query={"uploadId": upload},
                headers={"Content-Type": "application/xml"},
                payload_hash=hashlib.sha256(document).hexdigest(),
                body=document,
                length=len(document),
            ) as response:
                completed = self._check(response, 200)
            if b"<Error>" in completed:
                raise PreservationError("bucket_refused:multipart")
        except BaseException:
            try:
                with self._request(
                    "DELETE", key, query={"uploadId": upload}
                ) as response:
                    response.read()
            except (OSError, PreservationError):
                pass  # The provider expires abandoned uploads on its own.
            raise

    def delete(self, key: str) -> None:
        """Delete one object (used only for a manifest the owner removes)."""
        with self._request("DELETE", key) as response:
            self._check(response, 200, 204)


def _xml_text(data: bytes, tag: str) -> str:
    try:
        root = ElementTree.fromstring(data)  # noqa: S314 - bounded provider reply
    except ElementTree.ParseError as error:
        raise PreservationError("bucket_reply_invalid") from error
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == tag and element.text:
            return element.text
    raise PreservationError("bucket_reply_invalid")


# Operations -------------------------------------------------------------------


def publish_to_bucket(
    store: Path,
    manifest_id: str,
    trust: TrustProfile,
    client: BucketClient,
    *,
    control: OperationControl | None = None,
) -> int:
    """Upload one complete public archive; return the number of objects sent.

    Every object is verified locally first. Objects the bucket already holds at
    the right size are skipped. The signed manifest goes last, so recipients
    never see a manifest whose bytes are missing. Nothing is deleted.
    """
    active = control or OperationControl()
    with active.operation("bucket_publish"):
        manifest = verify_archive_contents(store, manifest_id, trust, active)
        if any(member.intake.audience != "public" for member in manifest.members):
            raise PreservationError(
                "bucket mirrors require public intake for every member"
            )
        sent = 0
        with directory(store) as root:
            for file in archive_objects(manifest):
                active.checkpoint()
                key = object_path(file.sha256)
                if client.head(key) == (file.size, file.sha256):
                    continue  # Uploaded and verified earlier by Sovereign.
                client.put_file(key, root, key, file.size, file.sha256, active)
                if client.head(key) != (file.size, file.sha256):
                    raise PreservationError("bucket_object_incomplete")
                sent += 1
            with open_file(root, manifest_path(manifest_id)) as stream:
                envelope = stream.read()
        active.phase("publishing")
        client.put_bytes(manifest_path(manifest_id), envelope, "application/json")
        active.mark_committed()
        return sent


def remove_from_bucket(client: BucketClient, manifest_id: str) -> None:
    """Withdraw one model by deleting only its manifest; shared objects remain."""
    if not re.fullmatch(r"[0-9a-f]{64}", manifest_id):
        raise PreservationError("invalid manifest identity")
    client.delete(manifest_path(manifest_id))


def publish_peers(client: BucketClient, document: bytes) -> None:
    """Publish this bucket mirror's signed peers document for gossip."""
    client.put_bytes(PEERS_PATH, document, "application/json")


def check_bucket(
    client: BucketClient, policy: TransferPolicy | None = None
) -> dict[str, str]:
    """Prove the setup works end to end: upload, public download, clean up.

    Returns a status the app can explain plainly: ``ready``, or which step
    failed (``upload`` for credentials or bucket name, ``public`` when the
    bucket is not publicly readable at its public address).
    """
    from .transfer import response as public_response

    token = uuid.uuid4().hex
    body = f'{{"sovereign":"bucket-check","token":"{token}"}}'.encode()
    try:
        client.put_bytes(CHECK_PATH, body, "application/json")
    except (OSError, PreservationError) as error:
        return {"status": "upload", "detail": str(error).split(":", 1)[-1][:64]}
    try:
        with public_response(
            client.target.public_url, CHECK_PATH, policy or TransferPolicy(timeout=20)
        ) as received:
            served = received.read(4096)
        if served != body:
            return {"status": "public", "detail": "different_content"}
    except (OSError, PreservationError, http.client.HTTPException) as error:
        return {"status": "public", "detail": type(error).__name__}
    finally:
        try:
            client.delete(CHECK_PATH)
        except (OSError, PreservationError):
            pass
    return {"status": "ready", "detail": ""}
