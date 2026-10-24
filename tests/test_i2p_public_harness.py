"""Public qualification refuses absent grants and validates fixtures offline first."""

import base64
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from foxlight_model_preservation.discovery import (
    bootstrap_identity,
    follow_source,
    refresh_source_from_directory,
    search_sources,
    source_identity,
)
from foxlight_model_preservation.signing import encode_record
from foxlight_model_preservation.storage import manifest_path, restore

SCRIPT = Path(__file__).parents[1] / "scripts/qualify_i2p_http_public.py"


@pytest.mark.parametrize(
    "extra",
    [
        [],
        ["--allow-public-i2p", "--seconds", "1801"],
        ["--allow-public-i2p", "--kib", "129"],
    ],
)
def test_public_harness_refuses_before_files_or_tools(tmp_path, extra):
    target = tmp_path / "untouched"
    result = subprocess.run(  # noqa: S603 - fixed interpreter and synthetic paths
        [
            sys.executable,
            str(SCRIPT),
            "--directory",
            str(target),
            "--java-home",
            str(tmp_path / "absent-java"),
            "--i2p-source",
            str(tmp_path / "absent-source"),
            "--runtime",
            str(tmp_path / "absent-runtime"),
            *extra,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "bounded resources" in result.stderr
    assert not target.exists()


def test_generated_fixture_source_and_recovery_pass_without_network(tmp_path):
    spec = importlib.util.spec_from_file_location("public_qualification", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    identity, trust, original = module.fixture(tmp_path, 1)
    host = base64.b32encode(b"i" * 32).decode().rstrip("=").lower()
    bootstrap = module.publish(tmp_path, "http://" + host + ".b32.i2p", identity)
    cache = tmp_path / "cache"
    follow_source(cache, encode_record(bootstrap), bootstrap_identity(bootstrap))
    refresh_source_from_directory(
        cache, source_identity(bootstrap), tmp_path / "source"
    )
    assert search_sources(cache, "qualification")[0].manifest == identity
    restore(tmp_path / "model", identity, trust, tmp_path / "restored")
    assert (tmp_path / "model" / manifest_path(identity)).read_bytes() == original
    assert not (tmp_path / "weights").exists()
    assert (
        tmp_path / "restored/members/tokenizer/fixture.bin"
    ).read_bytes() == b"synthetic companion"
