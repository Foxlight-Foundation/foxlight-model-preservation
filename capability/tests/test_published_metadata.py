"""Published wheel metadata must never name the private, unregistered SDK."""

import tomllib
from pathlib import Path


def test_private_sdk_is_a_dev_group_not_a_published_dependency() -> None:
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    published = list(project["project"].get("dependencies", []))
    for extra in project["project"].get("optional-dependencies", {}).values():
        published.extend(extra)
    assert not any("skulk-capability-sdk" in requirement for requirement in published)
    assert "skulk-capability-sdk==0.5.0" in project["dependency-groups"]["managed"]
