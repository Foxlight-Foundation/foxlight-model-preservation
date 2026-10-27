"""Linux publication refuses incomplete, changed or unqualified package pairs."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/release_linux.py"
spec = importlib.util.spec_from_file_location("release_linux", SCRIPT)
assert spec and spec.loader
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def candidates(root, monkeypatch):
    lock = json.loads((release.PROJECT / "desktop/linux/inputs.json").read_bytes())
    version = lock["version"] + "-" + lock["revision"]
    source = "a" * 40
    for arch in ("amd64", "arm64"):
        folder = root / arch
        folder.mkdir(parents=True)
        package = folder / f"foxlight-sovereign_{version}_{arch}.deb"
        package.write_bytes(b"exact package " + arch.encode())
        (folder / "SHA256SUMS").write_text(
            f"{release.digest(package)}  {package.name}\n"
        )
        (folder / "build-receipt.json").write_text(
            json.dumps(
                {
                    "source_revision": source,
                    "source_dirty": False,
                    "architecture": arch,
                    "inputs": lock,
                }
            )
        )
        (folder / "qualification.json").write_text(
            json.dumps(
                {key: True for key in release.REQUIRED_CHECKS}
                | {"skulk_or_sdk_required": False}
            )
        )

    def metadata(arguments, **_):
        arch = Path(arguments[2]).stem.rsplit("_", 1)[1]
        return (
            f"Package: foxlight-sovereign\nVersion: {version}\nArchitecture: {arch}\n"
        )

    monkeypatch.setattr(release.subprocess, "check_output", metadata)
    return source


def test_complete_pair_promotes_exact_bytes_and_evidence(tmp_path, monkeypatch):
    incoming = tmp_path / "incoming"
    revision = candidates(incoming, monkeypatch)
    output = tmp_path / "release"
    release.assemble(incoming, output, revision)
    record = json.loads((output / "linux-release.json").read_bytes())
    assert record["source_revision"] == revision
    assert record["architectures"] == ["amd64", "arm64"]
    for name, expected in record["artifacts"].items():
        assert release.digest(output / name) == expected
    assert len((output / "SHA256SUMS").read_text().splitlines()) == 3
    with pytest.raises(ValueError, match="new output"):
        release.assemble(incoming, output, revision)


@pytest.mark.parametrize("problem", ["missing", "tampered", "dirty", "skulk", "failed"])
def test_bad_pair_never_creates_release(tmp_path, monkeypatch, problem):
    incoming = tmp_path / "incoming"
    revision = candidates(incoming, monkeypatch)
    folder = incoming / "arm64"
    package = next(folder.glob("*.deb"))
    if problem == "missing":
        package.unlink()
    elif problem == "tampered":
        package.write_bytes(b"changed after qualification")
    elif problem == "dirty":
        path = folder / "build-receipt.json"
        data = json.loads(path.read_bytes())
        data["source_dirty"] = True
        path.write_text(json.dumps(data))
    else:
        path = folder / "qualification.json"
        data = json.loads(path.read_bytes())
        if problem == "skulk":
            data["skulk_or_sdk_required"] = True
        else:
            data["tampered_copy_refused"] = False
        path.write_text(json.dumps(data))
    output = tmp_path / "release"
    with pytest.raises(ValueError):
        release.assemble(incoming, output, revision)
    assert not output.exists()
