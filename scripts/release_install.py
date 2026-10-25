"""Build public hash-locked installation assets from the committed project lock."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tomllib
import zipfile
from email.parser import HeaderParser
from pathlib import Path


def check_requirements(text: str) -> None:
    """Refuse unlocked, local/private or un-hashed package specifications."""
    joined = text.replace("\\\n", " ")
    lines = [
        line.strip()
        for line in joined.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        raise ValueError("empty runtime dependency lock")
    for line in lines:
        if not re.fullmatch(
            r"[A-Za-z0-9._-]+==[A-Za-z0-9.!+_-]+(?:\s*;\s*[^\n]+?)?"
            r"(?:\s+--hash=sha256:[0-9a-f]{64})+",
            line,
        ):
            raise ValueError(
                "runtime lock requires exact package versions and SHA-256 hashes"
            )
        if any(value in line.lower() for value in ("skulk", "git+", "://", " @ ")):
            raise ValueError(
                "public runtime lock cannot enroll private SDK/source dependencies"
            )


def check_wheel(path: Path, name: str, version: str) -> None:
    """Require one regular wheel with the exact declared public identity."""
    if (
        path.is_symlink()
        or not path.is_file()
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.whl", path.name)
    ):
        raise ValueError("regular public wheel required")
    with zipfile.ZipFile(path) as archive:
        members = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        if len(members) != 1:
            raise ValueError("one wheel metadata record required")
        built = HeaderParser().parsestr(archive.read(members[0]).decode())
        if built["Name"] != name or built["Version"] != version:
            raise ValueError("wheel does not match the declared project/version")


def generate(
    project: Path,
    destination: Path,
    revision: str,
    *,
    desktop_core_wheel: Path | None = None,
) -> None:
    """Bind wheel identity and public dependency closure to an immutable source."""
    if re.fullmatch(r"[a-f0-9]{40}", revision) is None:
        raise ValueError("complete immutable source revision required")
    metadata = tomllib.loads((project / "pyproject.toml").read_text())["project"]
    name, version = metadata["name"], metadata["version"]
    if name not in {"foxlight-model-preservation", "foxlight-preservation-library"}:
        raise ValueError("unexpected public project")
    wheels = list(destination.glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("one built wheel required")
    wheel = wheels[0]
    check_wheel(wheel, name, version)
    sdists = list(destination.glob("*.tar.gz"))
    if len(sdists) != 1:
        raise ValueError("one built source archive required")
    wheel_hash = hashlib.sha256(wheel.read_bytes()).hexdigest()
    profiles = {"requirements.lock": []}
    components: list[dict[str, str]] = []
    desktop = metadata.get("optional-dependencies", {}).get("desktop")
    if name == "foxlight-preservation-library" and desktop:
        if len(desktop) != 1 or not re.fullmatch(
            r"foxlight-model-preservation==[0-9][0-9A-Za-z.+!_-]*", desktop[0]
        ):
            raise ValueError("desktop must pin one exact public core")
        if desktop_core_wheel is None:
            raise ValueError("matching desktop core wheel required")
        core_version = desktop[0].split("==")[1]
        check_wheel(desktop_core_wheel, "foxlight-model-preservation", core_version)
        if desktop_core_wheel.name == wheel.name:
            raise ValueError("distinct desktop core wheel required")
        components.append(
            {
                "project": "foxlight-model-preservation",
                "version": core_version,
                "source_revision": revision,
                "wheel": desktop_core_wheel.name,
                "wheel_sha256": hashlib.sha256(
                    desktop_core_wheel.read_bytes()
                ).hexdigest(),
            }
        )
        shutil.copyfile(desktop_core_wheel, destination / desktop_core_wheel.name)
        profiles["requirements-desktop.lock"] = [
            "--extra",
            "desktop",
            "--no-emit-package",
            "foxlight-model-preservation",
        ]
    elif desktop_core_wheel is not None:
        raise ValueError("desktop core wheel requires a Library desktop profile")
    if name == "foxlight-model-preservation":
        profiles["requirements-peer.lock"] = ["--extra", "peer"]
        profiles["requirements-mirror.lock"] = [
            "--extra",
            "peer",
            "--extra",
            "bittorrent",
        ]
    for filename, extra in profiles.items():
        command = [
            os.environ.get("UV_BIN", "uv"),
            "export",
            "--project",
            str(project),
            "--locked",
            "--no-dev",
            "--no-editable",
            "--no-emit-project",
            "--no-header",
            "--format",
            "requirements-txt",
            *extra,
        ]
        exported = subprocess.run(  # noqa: S603 - reviewed uv executable, no shell
            command, check=True, capture_output=True, text=True
        ).stdout
        check_requirements(exported)
        content = f"{name}=={version} \\\n    --hash=sha256:{wheel_hash}\n"
        if filename == "requirements-desktop.lock":
            component = components[0]
            content += (
                component["project"]
                + "=="
                + component["version"]
                + " \\\n    --hash=sha256:"
                + component["wheel_sha256"]
                + "\n"
            )
        content += exported
        (destination / filename).write_text(content)
    shutil.copyfile(
        Path(__file__).with_name("install_release.py"), destination / "install.py"
    )
    record = {
        "schema_version": "foxlight.software-install.v2"
        if components
        else "foxlight.software-install.v1",
        "project": name,
        "version": version,
        "source_revision": revision,
        "python_min": [3, 13] if name.endswith("-library") else [3, 11],
        "wheel": wheel.name,
        "wheel_sha256": wheel_hash,
        "requirements": list(profiles),
    }
    if components:
        record["components"] = components
    (destination / "INSTALL.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n"
    )
    assets = [
        wheel.name,
        sdists[0].name,
        *(component["wheel"] for component in components),
        *profiles,
        "INSTALL.json",
        "install.py",
    ]
    inner = "".join(
        hashlib.sha256((destination / name).read_bytes()).hexdigest()
        + "  "
        + name
        + "\n"
        for name in sorted(assets)
    )
    bundle = destination / (name + "-" + version + "-install.zip")
    # Fixed archive metadata; installation reproducibility is tied to exact
    # retained bytes, not a claim of bit-identical builds across builders.
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename in [*sorted(assets), "SHA256SUMS"]:
            data = (
                inner.encode()
                if filename == "SHA256SUMS"
                else (destination / filename).read_bytes()
            )
            info = zipfile.ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100600 << 16
            archive.writestr(info, data)
    assets.append(bundle.name)
    outer = "".join(
        hashlib.sha256((destination / filename).read_bytes()).hexdigest()
        + "  "
        + filename
        + "\n"
        for filename in sorted(assets)
    )
    (destination / "SHA256SUMS").write_text(outer)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--desktop-core-wheel", type=Path)
    args = parser.parse_args()
    generate(
        args.project,
        args.dist,
        args.revision,
        desktop_core_wheel=args.desktop_core_wheel,
    )
