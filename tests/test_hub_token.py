"""A Hugging Face token reaches huggingface.co only, and is never shown again."""

from __future__ import annotations

import io
import json
import time
from pathlib import Path

import pytest

from foxlight_model_preservation import hub
from foxlight_model_preservation.cards import ArtifactFile
from foxlight_model_preservation.local_host.owner import LocalOwner
from foxlight_model_preservation.local_host.selection import download_file
from foxlight_model_preservation.models import PreservationError
from foxlight_model_preservation.operations import OperationControl
from foxlight_model_preservation.signing import digest
from foxlight_model_preservation.transfer import (
    I2PTransferPolicy,
    TransferPolicy,
    response,
)

TOKEN = "hf_" + "A1b2C3d4" * 4
DATA = b"weights" * 512


def test_credentials_never_go_over_plain_http_or_i2p() -> None:
    with pytest.raises(PreservationError, match="credentials require HTTPS"):
        with response(
            "http://127.0.0.1:1",
            "api/whoami-v2",
            TransferPolicy(allow_http=True, allow_private=True),
            authorization="Bearer " + TOKEN,
        ):
            pass
    with pytest.raises(PreservationError, match="credentials require HTTPS"):
        with response(
            "http://example.i2p",
            "x",
            I2PTransferPolicy(),
            authorization="Bearer " + TOKEN,
        ):
            pass


class Redirecting:
    """huggingface.co answers with a redirect to a CDN, which serves the bytes."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, dict[str, str]]] = []

    def connection(self, host: str, *args: object, **kwargs: object):
        origin = self

        class Reply(io.BytesIO):
            def __init__(self, status: int, body: bytes, headers: dict[str, str]):
                super().__init__(body)
                self.status, self.headers = status, headers

            def getheader(self, name: str, default: str | None = None) -> str | None:
                return self.headers.get(name, default)

        class Connection:
            def request(self, method: str, path: str, headers: dict[str, str]) -> None:
                origin.seen.append((host, dict(headers)))

            def getresponse(self) -> Reply:
                if host == "huggingface.co":
                    return Reply(
                        302,
                        b"",
                        {"Location": "https://cdn-lfs.hf.co/signed?x=1"},
                    )
                return Reply(200, DATA, {"Content-Length": str(len(DATA))})

            def close(self) -> None:
                pass

        return Connection()


def test_a_download_sends_the_token_to_huggingface_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin = Redirecting()
    monkeypatch.setattr(
        "foxlight_model_preservation.local_host.selection.PinnedConnection",
        origin.connection,
    )
    destination = tmp_path / "out" / "model.bin"
    download_file(
        "example/model",
        "a" * 40,
        ArtifactFile(
            path="model.bin", size_bytes=len(DATA), object_id="sha256:" + digest(DATA)
        ),
        destination,
        OperationControl(),
        deadline=time.monotonic() + 30,
        partial=tmp_path / "staging" / "x.partial",
        token=TOKEN,
    )
    assert destination.read_bytes() == DATA
    assert [host for host, _ in origin.seen] == ["huggingface.co", "cdn-lfs.hf.co"]
    assert origin.seen[0][1]["Authorization"] == "Bearer " + TOKEN
    assert "Authorization" not in origin.seen[1][1]


def test_only_token_shaped_strings_are_accepted() -> None:
    assert hub.token_valid(TOKEN)
    for value in ("", "hf_short", "token " + TOKEN, TOKEN + "\n", "sk_" + "a" * 30):
        assert not hub.token_valid(value)
    with pytest.raises(PreservationError, match="hub_token_invalid"):
        hub.whoami("not-a-token")


def test_the_owner_keeps_a_checked_token_and_never_shows_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored: dict[str, str] = {}
    monkeypatch.setattr(hub, "whoami", lambda token, **_: "someone")
    monkeypatch.setattr(hub, "save_token", lambda token: stored.update(token=token))
    monkeypatch.setattr(hub, "load_token", lambda: stored["token"])
    monkeypatch.setattr(hub, "delete_token", lambda: stored.clear())
    owner = LocalOwner(tmp_path / "owner")
    try:
        assert owner.read()["hub_token"] is None
        assert owner.control("hub-token-set", {"token": TOKEN}) == {
            "hub_token": "someone"
        }
        page = owner.read()
        assert page["hub_token"] == "someone"
        assert TOKEN not in json.dumps(page)
        assert stored == {"token": TOKEN}
        assert owner._hub_token() == TOKEN  # noqa: SLF001
        # Nothing on disk holds the token itself.
        for path in (tmp_path / "owner").rglob("*"):
            if path.is_file():
                assert TOKEN.encode() not in path.read_bytes(), path
        assert owner.control("hub-token-remove", {}) == {"hub_token": None}
        assert owner.read()["hub_token"] is None and stored == {}
        assert owner._hub_token() is None  # noqa: SLF001
    finally:
        owner.close()


def test_a_refused_token_is_not_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(token: str, **_: object) -> str:
        raise PreservationError("hub_token_invalid")

    saved: list[str] = []
    monkeypatch.setattr(hub, "whoami", refuse)
    monkeypatch.setattr(hub, "save_token", saved.append)
    owner = LocalOwner(tmp_path / "owner")
    try:
        with pytest.raises(PreservationError, match="hub_token_invalid"):
            owner.control("hub-token-set", {"token": TOKEN})
        assert saved == [] and owner.read()["hub_token"] is None
    finally:
        owner.close()
