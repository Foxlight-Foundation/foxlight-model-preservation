"""Portable signed bootstrap lists and the installed command surface."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import Capture

from foxlight_model_preservation.models import Catalog, CatalogEntry, PreservationError
from foxlight_model_preservation.signing import (
    CATALOG_TYPE,
    digest,
    encode_record,
    sign_record,
    verify_catalog,
    verify_manifest,
)
from foxlight_model_preservation.storage import verify_store


def command(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter; no shell or user input
        [sys.executable, "-m", "foxlight_model_preservation.cli", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_signed_catalog_and_domain_separation(capture: Capture, tmp_path: Path) -> None:
    catalog = Catalog(
        schema_version="foxlight.model-preservation.catalog.v1",
        description="Test bootstrap list",
        entries=(
            CatalogEntry(
                entry_id="test",
                manifest=capture.identity,
                mirrors=(str(capture.store),),
            ),
        ),
    )
    identity = digest(encode_record(catalog))
    envelope = encode_record(sign_record(catalog, (capture.key,), CATALOG_TYPE))
    assert verify_catalog(envelope, capture.trust, identity) == catalog
    with pytest.raises(PreservationError, match="payload type"):
        verify_manifest(envelope, capture.trust, identity)
    trust_file = tmp_path / "trust.json"
    trust_file.write_bytes(encode_record(capture.trust))
    index = tmp_path / "catalog.json"
    index.write_bytes(envelope)
    target = tmp_path / "from-catalog"
    result = command(
        "fetch-catalog",
        identity,
        "--index",
        str(index),
        "--trust",
        str(trust_file),
        "--entry",
        "test",
        "--store",
        str(target),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["manifest"] == capture.identity
    verify_store(target, capture.identity, capture.trust)


def test_cli_keygen_protection_and_no_overwrite(tmp_path: Path) -> None:
    key, trust, password = [
        tmp_path / name for name in ("key.pem", "trust.json", "pass")
    ]
    password.write_text("test-passphrase\n")
    args = (
        "keygen",
        "--key",
        str(key),
        "--trust-out",
        str(trust),
        "--passphrase-file",
        str(password),
    )
    first = command(*args)
    assert first.returncode == 0, first.stderr
    assert b"ENCRYPTED PRIVATE KEY" in key.read_bytes()
    assert key.stat().st_mode & 0o077 == 0
    before = key.read_bytes()
    assert command(*args).returncode == 1
    assert key.read_bytes() == before
    os.chmod(key, 0o644)
    from foxlight_model_preservation.signing import load_key

    with pytest.raises(PreservationError, match="group/world"):
        load_key(key, b"test-passphrase")


def test_empty_passphrase_never_creates_plain_key(tmp_path: Path) -> None:
    password = tmp_path / "pass"
    password.write_bytes(b"\n")
    key = tmp_path / "key.pem"
    result = command(
        "keygen",
        "--key",
        str(key),
        "--trust-out",
        str(tmp_path / "trust"),
        "--passphrase-file",
        str(password),
    )
    assert result.returncode == 1
    assert not key.exists()
    assert "Traceback" not in result.stderr


def test_malformed_cli_input_has_actionable_failure(tmp_path: Path) -> None:
    trust = tmp_path / "trust.json"
    trust.write_text('{"duplicate":1,"duplicate":2}')
    result = command(
        "verify", "0" * 64, "--store", str(tmp_path), "--trust", str(trust)
    )
    assert result.returncode == 1
    assert "JSON metadata" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("name", ["fetch", "plan-fetch"])
def test_fetch_deadline_is_an_explicit_bounded_option(
    capture: Capture, tmp_path: Path, name: str
) -> None:
    trust_file = tmp_path / "trust.json"
    trust_file.write_bytes(encode_record(capture.trust))
    arguments = (
        name,
        capture.identity,
        "--trust",
        str(trust_file),
        "--mirror",
        str(capture.store),
        "--store",
        str(tmp_path / name),
    )
    refused = command(*arguments, "--deadline-seconds", "86401")
    assert refused.returncode == 1
    assert "timeouts must be positive and bounded" in refused.stderr
    extended = command(*arguments, "--deadline-seconds", "86400")
    assert extended.returncode == 0, extended.stderr
