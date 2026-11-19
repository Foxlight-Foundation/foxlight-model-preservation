"""Secrets in Windows Credential Manager, through the shared keychain calls."""

from __future__ import annotations

import ctypes
import sys
import traceback
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from foxlight_model_preservation import keychain
from foxlight_model_preservation.models import PreservationError

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows Credential Manager"
)

SECRET = "hf_" + "s3cr3t" * 6
ERROR = "probe_keychain_unavailable"
# A logon with no credential set (some service and SSH logons) has no store.
_NO_SUCH_LOGON_SESSION = 1312


@pytest.fixture
def account() -> Iterator[str]:
    """A test-only entry, always removed afterwards."""
    from foxlight_model_preservation import _wincred

    name = f"test-{uuid.uuid4().hex}"
    try:
        _wincred.write(f"foxlight-test:{name}", name, "probe", "probe")
    except OSError as error:
        if error.winerror == _NO_SUCH_LOGON_SESSION:
            pytest.skip("this logon session has no Credential Manager store")
        raise
    _wincred.delete(f"foxlight-test:{name}")
    yield name
    _wincred.delete(f"foxlight-test:{name}")


def test_a_secret_round_trips_and_is_replaced(account: str) -> None:
    keychain.save_secret("foxlight-test", account, SECRET, label="t", error=ERROR)
    assert keychain.load_secret("foxlight-test", account, error=ERROR) == SECRET
    keychain.save_secret("foxlight-test", account, "second", label="t", error=ERROR)
    assert keychain.load_secret("foxlight-test", account, error=ERROR) == "second"


def test_removal_is_idempotent_and_a_missing_entry_raises(account: str) -> None:
    keychain.save_secret("foxlight-test", account, SECRET, label="t", error=ERROR)
    keychain.delete_secret("foxlight-test", account, error=ERROR, strict=True)
    keychain.delete_secret("foxlight-test", account, error=ERROR, strict=True)
    with pytest.raises(PreservationError, match=ERROR):
        keychain.load_secret("foxlight-test", account, error=ERROR)


def test_an_empty_secret_is_kept_as_empty(account: str) -> None:
    keychain.save_secret("foxlight-test", account, "", label="t", error=ERROR)
    assert keychain.load_secret("foxlight-test", account, error=ERROR) == ""


def test_a_macos_keychain_file_and_an_oversized_secret_are_refused(
    account: str,
) -> None:
    with pytest.raises(PreservationError, match=ERROR):
        keychain.save_secret(
            "foxlight-test",
            account,
            SECRET,
            label="t",
            error=ERROR,
            keychain=Path("login.keychain-db"),
        )
    with pytest.raises(PreservationError, match=ERROR):
        keychain.save_secret(
            "foxlight-test", account, "x" * 2561, label="t", error=ERROR
        )
    with pytest.raises(PreservationError, match=ERROR):
        keychain.load_secret("foxlight-test", account, error=ERROR)


def test_a_locked_store_fails_without_the_secret(account: str) -> None:
    from foxlight_model_preservation import _wincred

    def denied(*_: object) -> int:
        ctypes.set_last_error(5)  # ERROR_ACCESS_DENIED
        return 0

    # Undone before the fixture removes its entry.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(_wincred, "_CredDeleteW", denied)
        with pytest.raises(PreservationError):
            keychain.delete_secret("foxlight-test", account, error=ERROR, strict=True)
        keychain.delete_secret("foxlight-test", account, error=ERROR)  # not strict
        patch.setattr(_wincred, "_CredWriteW", denied)
        with pytest.raises(PreservationError) as raised:
            keychain.save_secret(
                "foxlight-test", account, SECRET, label="t", error=ERROR
            )
    rendered = "".join(traceback.format_exception(raised.value))
    assert SECRET not in rendered and SECRET.encode().hex() not in rendered
    assert raised.value.__cause__ is None and raised.value.__suppress_context__
