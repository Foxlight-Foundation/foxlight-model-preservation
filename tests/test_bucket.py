"""Bucket mirrors: SigV4, provider presets, publish-last uploads and the check."""

from __future__ import annotations

import hashlib
import http.server
import re
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

import pytest
from conftest import Capture

from foxlight_model_preservation import bucket as module
from foxlight_model_preservation.bucket import (
    EMPTY_SHA256,
    BucketClient,
    BucketCredentials,
    BucketTarget,
    check_bucket,
    publish_to_bucket,
    remove_from_bucket,
    sign_request,
    target_for,
)
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.storage import manifest_path, object_path, verify_store
from foxlight_model_preservation.transfer import TransferPolicy, fetch

LOCAL = TransferPolicy(allow_http=True, allow_private=True, timeout=5)
KEYS = BucketCredentials("AKIATESTKEY", "secret/TEST+key")


def test_signature_matches_the_aws_reference_example() -> None:
    """AWS's published Signature Version 4 example for GET /test.txt."""
    headers = sign_request(
        "GET",
        "examplebucket.s3.amazonaws.com",
        "/test.txt",
        {},
        {"Range": "bytes=0-9"},
        EMPTY_SHA256,
        BucketCredentials(
            "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        ),
        "us-east-1",
        "20130524T000000Z",
    )
    assert headers["Authorization"].endswith(
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )


def test_presets_need_only_a_few_answers() -> None:
    r2 = target_for(
        "r2",
        bucket="models",
        key_name="r2-models",
        account_id="0" * 32,
        public_url="https://pub-abc.r2.dev",
    )
    assert r2.endpoint == "https://" + "0" * 32 + ".r2.cloudflarestorage.com"
    assert r2.region == "auto" and r2.path_style
    b2 = target_for("b2", bucket="models", key_name="b2", region="us-west-004")
    assert b2.endpoint == "https://s3.us-west-004.backblazeb2.com"
    assert b2.public_url == "https://models.s3.us-west-004.backblazeb2.com"
    s3 = target_for("s3", bucket="models", key_name="s3", region="eu-west-1")
    assert not s3.path_style and s3.public_url.endswith(".amazonaws.com")
    for bad in (
        lambda: target_for("r2", bucket="models", key_name="k", account_id="nope"),
        lambda: target_for("other", bucket="models", key_name="k", region="x"),
        lambda: BucketTarget(
            provider="other",
            endpoint="http://storage.example",
            region="x",
            bucket="models",
            path_style=True,
            public_url="https://pub.example",
            key_name="k",
        ),
    ):
        with pytest.raises((PreservationError, ValueError)):
            bad()


def test_credentials_never_print_their_secret() -> None:
    assert "secret" not in repr(KEYS) and "TEST+key" not in repr(KEYS)
    assert "TEST+key" not in str(KEYS)


class _Bucket(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, dict[str, str]] = {}
        self.uploads: dict[str, dict[int, bytes]] = {}
        self.order: list[str] = []
        self.public = True
        self.fail_puts = 0
        self.prefix = ""


class _Handler(http.server.BaseHTTPRequestHandler):
    """A tiny S3: checks every Signature V4, stores objects, serves public reads."""

    def log_message(self, format: str, *args: object) -> None:
        """Keep the test output quiet."""

    @property
    def store(self) -> _Bucket:
        return self.server  # type: ignore[return-value]

    def _parts(self) -> tuple[str, dict[str, str]]:
        parsed = urlsplit(self.path)
        if self.store.prefix and parsed.path.startswith(self.store.prefix + "/"):
            parsed = parsed._replace(path=parsed.path[len(self.store.prefix) :])
        return unquote(parsed.path), dict(
            parse_qsl(parsed.query, keep_blank_values=True)
        )

    def _authorized(self, body: bytes) -> bool:
        auth = self.headers.get("Authorization", "")
        matched = re.fullmatch(
            r"AWS4-HMAC-SHA256 Credential=([^/]+)/(\d{8})/([^/]+)/s3/aws4_request, "
            r"SignedHeaders=([^,]+), Signature=([0-9a-f]{64})",
            auth,
        )
        if not matched or matched[1] != KEYS.access_key:
            return False
        claimed = self.headers["x-amz-content-sha256"]
        if claimed != hashlib.sha256(body).hexdigest():
            return False
        names = matched[4].split(";")
        extra = {
            name: self.headers[name]
            for name in names
            if name not in {"host", "x-amz-date", "x-amz-content-sha256"}
        }
        path, query = urlsplit(self.path).path, self._parts()[1]
        expected = sign_request(
            self.command,
            self.headers["Host"],
            path,
            query,
            extra,
            claimed,
            KEYS,
            matched[3],
            self.headers["x-amz-date"],
        )
        return expected["Authorization"] == auth

    def _reply(self, status: int, body: bytes = b"") -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length", "0")))

    def do_PUT(self) -> None:
        body = self._body()
        if not self._authorized(body):
            return self._reply(
                403, b"<Error><Code>SignatureDoesNotMatch</Code></Error>"
            )
        path, query = self._parts()
        key = path.split("/", 2)[2]
        if "uploadId" in query:
            self.store.uploads[query["uploadId"]][int(query["partNumber"])] = body
            self.send_response(200)
            self.send_header("ETag", '"' + hashlib.md5(body).hexdigest() + '"')  # noqa: S324
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        if self.store.fail_puts and key.startswith("objects/"):
            self.store.fail_puts -= 1
            return self._reply(500, b"<Error><Code>InternalError</Code></Error>")
        self.store.objects[key] = body
        self.store.metadata[key] = self._meta()
        self.store.order.append(key)
        return self._reply(200)

    def _meta(self) -> dict[str, str]:
        return {
            name.lower(): value
            for name, value in self.headers.items()
            if name.lower().startswith("x-amz-meta-")
        }

    def do_POST(self) -> None:
        body = self._body()
        if not self._authorized(body):
            return self._reply(403)
        path, query = self._parts()
        key = path.split("/", 2)[2]
        if "uploads" in query:
            upload = str(len(self.store.uploads) + 1)
            self.store.uploads[upload] = {}
            self.store.metadata["upload:" + upload] = self._meta()
            return self._reply(
                200,
                f"<InitiateMultipartUploadResult><UploadId>{upload}</UploadId>"
                "</InitiateMultipartUploadResult>".encode(),
            )
        parts = self.store.uploads.pop(query["uploadId"])
        self.store.objects[key] = b"".join(parts[n] for n in sorted(parts))
        self.store.metadata[key] = self.store.metadata.pop(
            "upload:" + query["uploadId"]
        )
        self.store.order.append(key)
        return self._reply(200, b"<CompleteMultipartUploadResult/>")

    def do_HEAD(self) -> None:
        if not self._authorized(b""):
            return self._reply(403)
        key = self._parts()[0].split("/", 2)[2]
        data = self.store.objects.get(key)
        if data is None:
            return self._reply(404)
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        for name, value in self.store.metadata.get(key, {}).items():
            self.send_header(name, value)
        self.end_headers()
        return None

    def do_DELETE(self) -> None:
        if not self._authorized(b""):
            return self._reply(403)
        path, query = self._parts()
        if "uploadId" in query:
            self.store.uploads.pop(query["uploadId"], None)
        else:
            self.store.objects.pop(path.split("/", 2)[2], None)
        return self._reply(204)

    def do_GET(self) -> None:
        # Public reads at /public/<key>, like an r2.dev address, with ranges.
        path = self._parts()[0]
        if not path.startswith("/public/") or not self.store.public:
            return self._reply(403)
        data = self.store.objects.get(path.removeprefix("/public/"))
        if data is None:
            return self._reply(404)
        requested = self.headers.get("Range")
        if requested:
            start = int(requested.removeprefix("bytes=").split("-")[0])
            chunk = data[start:]
            self.send_response(206)
            self.send_header(
                "Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}"
            )
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)
            return None
        return self._reply(200, data)


@contextmanager
def bucket(prefix: str = "") -> Generator[tuple[_Bucket, BucketClient]]:
    server = _Bucket()
    server.prefix = prefix
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    target = target_for(
        "other",
        bucket="models",
        key_name="local",
        region="local-1",
        endpoint=f"http://127.0.0.1:{port}{prefix}",
        public_url=f"http://127.0.0.1:{port}/public",
    )
    try:
        yield server, BucketClient(target, KEYS, LOCAL)
    finally:
        server.shutdown()
        server.server_close()


def test_a_bucket_serves_a_fresh_client_with_no_compute_attached(
    tmp_path: Path, capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Small limits exercise multipart uploads with the synthetic model files.
    monkeypatch.setattr(module, "SINGLE_PUT_LIMIT", 4096)
    monkeypatch.setattr(module, "PART_SIZE", 3000)
    with bucket() as (server, client):
        assert publish_to_bucket(capture.store, capture.identity, capture.trust, client)
        # The signed manifest is uploaded last, after every object.
        assert server.order[-1] == manifest_path(capture.identity)
        assert all(key.startswith("objects/") for key in server.order[:-1])
        # Uploading again sends nothing: the bucket already holds every object.
        before = list(server.order)
        assert (
            publish_to_bucket(capture.store, capture.identity, capture.trust, client)
            == 0
        )
        assert server.order[len(before) :] == [manifest_path(capture.identity)]
        received = tmp_path / "recipient"
        manifest = fetch(
            capture.identity,
            capture.trust,
            (client.target.public_url,),
            received,
            LOCAL,
        )
        assert manifest == verify_store(received, capture.identity, capture.trust)


def test_an_interrupted_upload_never_publishes_its_manifest(
    capture: Capture,
) -> None:
    with bucket() as (server, client):
        server.fail_puts = 1
        with pytest.raises(PreservationError):
            publish_to_bucket(capture.store, capture.identity, capture.trust, client)
        assert manifest_path(capture.identity) not in server.objects
        # A retry completes and only then publishes the manifest.
        publish_to_bucket(capture.store, capture.identity, capture.trust, client)
        assert server.order[-1] == manifest_path(capture.identity)


def test_private_archives_never_reach_a_bucket(
    tmp_path: Path, capture: Capture
) -> None:
    from foxlight_model_preservation.storage import pack

    private = capture.recipe.model_copy(
        update={
            "members": tuple(
                member.model_copy(
                    update={
                        "intake": member.intake.model_copy(
                            update={"audience": "private"}
                        )
                    }
                )
                for member in capture.recipe.members
            )
        }
    )
    store = tmp_path / "private"
    identity = pack(private, capture.sources, store, (capture.key,))
    with bucket() as (server, client):
        with pytest.raises(PreservationError, match="public intake"):
            publish_to_bucket(store, identity, capture.trust, client)
        assert server.objects == {}


def test_removing_a_model_deletes_only_its_manifest(capture: Capture) -> None:
    with bucket() as (server, client):
        publish_to_bucket(capture.store, capture.identity, capture.trust, client)
        remove_from_bucket(client, capture.identity)
        assert manifest_path(capture.identity) not in server.objects
        manifest = verify_store(capture.store, capture.identity, capture.trust)
        assert object_path(manifest.members[0].files[0].sha256) in server.objects


def test_the_setup_check_explains_what_is_wrong(capture: Capture) -> None:
    with bucket() as (server, client):
        assert check_bucket(client, LOCAL)["status"] == "ready"
        assert module.CHECK_PATH not in server.objects  # The check cleans up.
        server.public = False
        assert check_bucket(client, LOCAL)["status"] == "public"
        wrong = BucketClient(
            client.target, BucketCredentials("AKIATESTKEY", "wrong"), LOCAL
        )
        result = check_bucket(wrong, LOCAL)
        assert result["status"] == "upload"
        assert "wrong" not in str(result)


def test_an_unverified_object_of_the_right_size_is_replaced(capture: Capture) -> None:
    manifest = verify_store(capture.store, capture.identity, capture.trust)
    file = manifest.members[0].files[0]
    with bucket() as (server, client):
        # Something else left wrong bytes of exactly the right length.
        server.objects[object_path(file.sha256)] = b"x" * file.size
        publish_to_bucket(capture.store, capture.identity, capture.trust, client)
        stored = server.objects[object_path(file.sha256)]
        assert hashlib.sha256(stored).hexdigest() == file.sha256


def test_a_multipart_upload_that_does_not_hash_to_its_identity_is_aborted(
    capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from foxlight_model_preservation.operations import OperationControl

    monkeypatch.setattr(module, "PART_SIZE", 3000)
    file = verify_store(capture.store, capture.identity, capture.trust).members[0]
    item = file.files[0]
    with bucket() as (server, client):
        root = os.open(capture.store, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with pytest.raises(PreservationError, match="changed"):
                client._multipart(  # noqa: SLF001 - the changed-bytes path
                    "objects/test",
                    root,
                    object_path(item.sha256),
                    item.size,
                    "0" * 64,
                    OperationControl(),
                )
        finally:
            os.close(root)
        assert "objects/test" not in server.objects and server.uploads == {}


def test_keychain_failures_never_carry_the_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess
    import traceback

    def slow(command: list[str], **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(command, 30, output=str(kwargs.get("input")))

    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(PreservationError) as raised:
        module.save_credentials("probe", KEYS)
    rendered = "".join(traceback.format_exception(raised.value))
    assert KEYS.secret_key not in rendered
    assert KEYS.secret_key.encode().hex() not in rendered
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


def test_the_published_manifest_is_the_one_that_was_verified(
    capture: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = capture.store / manifest_path(capture.identity)
    original = path.read_bytes()
    real = BucketClient.put_file

    def swap_then_upload(self: BucketClient, *args: object, **kwargs: object) -> None:
        # The local manifest is replaced while objects are uploading.
        path.chmod(0o600)
        path.write_bytes(b'{"tampered": true}')
        real(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(BucketClient, "put_file", swap_then_upload)
    with bucket() as (server, client):
        publish_to_bucket(capture.store, capture.identity, capture.trust, client)
        assert server.objects[manifest_path(capture.identity)] == original


def test_an_endpoint_behind_a_path_prefix_works(capture: Capture) -> None:
    with bucket("/s3") as (server, client):
        assert check_bucket(client, LOCAL)["status"] == "ready"
        publish_to_bucket(capture.store, capture.identity, capture.trust, client)
        assert manifest_path(capture.identity) in server.objects
