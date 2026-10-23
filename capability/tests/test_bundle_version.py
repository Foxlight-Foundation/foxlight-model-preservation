"""The managed bundle version must track the released package version."""

import tomllib
from pathlib import Path

from foxlight_preservation_library.contracts import BUNDLE_VERSION


def test_managed_bundle_version_matches_package_version() -> None:
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())[
        "project"
    ]
    assert BUNDLE_VERSION == project["version"]
