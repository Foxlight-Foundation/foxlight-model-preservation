"""The public source archive must start without checkout-only resources."""

import io
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


def test_packaged_child_reads_its_licence_definition(tmp_path: Path) -> None:
    """Import the actual public-tools archive in an isolated child interpreter."""
    pytest.importorskip("skulk_capability_tools", reason="optional public author tools")
    from skulk_capability_tools.packaging import archive, load_spec

    root = Path(__file__).parents[1]
    spec, source = load_spec(root / "capability.toml")
    content = archive(spec, source)
    with zipfile.ZipFile(io.BytesIO(content)) as packed:
        assert (
            packed.read("foxlight_preservation_library/licence_families_v1.json")
            == (
                root / "src/foxlight_preservation_library/licence_families_v1.json"
            ).read_bytes()
        )
    path = tmp_path / "library.pyz"
    path.write_bytes(content)
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys; sys.path.insert(0,sys.argv[1]); "
            "import foxlight_preservation_library.child as child; "
            "assert child.__file__.startswith(sys.argv[1]); "
            "from foxlight_preservation_library.licences import definition_json; "
            "assert definition_json()['version'] >= 1",
            str(path),
        ],
        cwd=tmp_path,
        check=True,
        timeout=20,
        capture_output=True,
    )
