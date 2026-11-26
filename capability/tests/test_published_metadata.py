"""Standalone wheel metadata stays independent of optional public SDK tools."""

import tomllib
from pathlib import Path


def test_public_sdk_is_an_optional_group_not_a_standalone_dependency() -> None:
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    published = list(project["project"].get("dependencies", []))
    for extra in project["project"].get("optional-dependencies", {}).values():
        published.extend(extra)
    assert not any("skulk-capability-sdk" in requirement for requirement in published)
    assert "skulk-capability-sdk==1.0.1" in project["dependency-groups"]["managed"]


def test_managed_sdk_uses_the_exact_public_wheel() -> None:
    """Require anonymous immutable SDK acquisition, without private git access."""
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert project["tool"]["uv"]["sources"]["skulk-capability-sdk"] == {
        "url": "https://github.com/Foxlight-Foundation/skulk-capability-sdk/releases/download/v1.0.1/skulk_capability_sdk-1.0.1-py3-none-any.whl"
    }
