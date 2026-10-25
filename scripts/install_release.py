"""Install authenticated release bytes and hash-locked dependencies into a new env.

Verify the release's build attestations or independently authenticated hashes
BEFORE executing this helper. Same-source SHA256SUMS alone is not authenticity.
This helper never sets up Core, reads models, grants serving or starts services.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from email.parser import HeaderParser
from pathlib import Path

RECORD_FIELDS = {
    "schema_version",
    "project",
    "version",
    "source_revision",
    "python_min",
    "wheel",
    "wheel_sha256",
    "requirements",
}


def _hash(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("release assets must be regular files, not links")
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024**2), b""):
            result.update(chunk)
    return result.hexdigest()


def _wheel_identity(path: Path, project: str, version: str) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if len(names) != 1 or archive.getinfo(names[0]).file_size > 1024**2:
                raise ValueError("one bounded wheel identity required")
            metadata = HeaderParser().parsestr(archive.read(names[0]).decode())
    except (zipfile.BadZipFile, UnicodeError) as error:
        raise ValueError("invalid bundled wheel metadata") from error
    if metadata.get_all("Name") != [project] or metadata.get_all("Version") != [
        version
    ]:
        raise ValueError("bundled wheel identity does not match its record")


def validate_assets(root: Path, selected: str) -> dict[str, object]:
    """Verify a closed safe asset inventory before any installation mutation."""
    sums = {}
    for line in (root / "SHA256SUMS").read_text().splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if match is None or match[2] in sums:
            raise ValueError("unsafe/duplicate release inventory")
        sums[match[2]] = match[1]
    for name, wanted in sums.items():
        if _hash(root / name) != wanted:
            raise ValueError("release asset hash mismatch: " + name)
    record = json.loads((root / "INSTALL.json").read_text())
    if not isinstance(record, dict):
        raise ValueError("unsupported installation record")
    desktop = record.get("schema_version") == "foxlight.software-install.v2"
    if set(record) != RECORD_FIELDS | ({"components"} if desktop else set()):
        raise ValueError("unsupported installation record")
    if not desktop and record["schema_version"] != "foxlight.software-install.v1":
        raise ValueError("unsupported installation version")
    project, version = record["project"], record["version"]
    if project not in {"foxlight-model-preservation", "foxlight-preservation-library"}:
        raise ValueError("unexpected public project")
    if not isinstance(version, str) or not re.fullmatch(
        r"[0-9][0-9A-Za-z.+!_-]*", version
    ):
        raise ValueError("invalid project version")
    if not isinstance(record["source_revision"], str) or not re.fullmatch(
        r"[a-f0-9]{40}", record["source_revision"]
    ):
        raise ValueError("immutable source revision required")
    locks = (
        ["requirements.lock", "requirements-peer.lock"]
        if project == "foxlight-model-preservation"
        else ["requirements.lock"]
    )
    components = []
    if desktop:
        if project != "foxlight-preservation-library":
            raise ValueError("desktop profile requires the public Library")
        components = record["components"]
        if not isinstance(components, list) or len(components) != 1:
            raise ValueError("one exact desktop core component required")
        component = components[0]
        if (
            not isinstance(component, dict)
            or set(component)
            != {"project", "version", "source_revision", "wheel", "wheel_sha256"}
            or component["project"] != "foxlight-model-preservation"
            or component["source_revision"] != record["source_revision"]
            or not isinstance(component["version"], str)
            or not re.fullmatch(r"[0-9][0-9A-Za-z.+!_-]*", component["version"])
            or not isinstance(component["wheel"], str)
            or not component["wheel"].endswith(".whl")
            or component["wheel"] == record["wheel"]
            or component["wheel"] not in sums
            or sums[component["wheel"]] != component["wheel_sha256"]
        ):
            raise ValueError("invalid desktop core component binding")
        locks.append("requirements-desktop.lock")
    if project == "foxlight-model-preservation" and record["requirements"] == [
        *locks,
        "requirements-mirror.lock",
    ]:
        locks.append("requirements-mirror.lock")
    if record["requirements"] != locks or selected not in locks:
        raise ValueError("unsupported runtime dependency profile")
    wheel = record["wheel"]
    if not isinstance(wheel, str) or not wheel.endswith(".whl") or wheel not in sums:
        raise ValueError("missing release wheel")
    expected = {
        wheel,
        *(component["wheel"] for component in components),
        *locks,
        "INSTALL.json",
        "install.py",
    }
    sdists = [name for name in sums if name.endswith(".tar.gz")]
    bundle_name = project + "-" + version + "-install.zip"
    if bundle_name in sums:
        expected.add(bundle_name)
    if len(sdists) != 1 or set(sums) != expected | set(sdists):
        raise ValueError("unexpected or missing release assets")
    if sums[wheel] != record["wheel_sha256"]:
        raise ValueError("wheel hash does not match installation record")
    if desktop:
        _wheel_identity(root / wheel, project, version)
        for component in components:
            _wheel_identity(
                root / component["wheel"], component["project"], component["version"]
            )
    minimum = [3, 13] if project == "foxlight-preservation-library" else [3, 11]
    if record["python_min"] != minimum:
        raise ValueError("invalid Python contract")
    text = (root / selected).read_text().replace("\\\n", " ")
    own = f"{project}=={version}"
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    found = False
    found_core = False
    for line in lines:
        if not re.fullmatch(
            r"[A-Za-z0-9._-]+==[A-Za-z0-9.!+_-]+(?:\s*;\s*[^\n]+?)?(?:\s+--hash=sha256:[0-9a-f]{64})+",
            line,
        ):
            raise ValueError("exact hashed runtime requirements required")
        if any(v in line.lower() for v in ("git+", "://", " @ ", "skulk")):
            raise ValueError("private/source dependency refused")
        package = re.split(r"==", line, maxsplit=1)[0].lower().replace("_", "-")
        hashes = re.findall(r"--hash=sha256:([a-f0-9]{64})", line)
        if package == project:
            if (
                not line.startswith(own + " ")
                or hashes != [record["wheel_sha256"]]
                or found
            ):
                raise ValueError("runtime lock must pin this exact release wheel")
            found = True
        if desktop and package == "foxlight-model-preservation":
            component = components[0]
            if (
                selected != "requirements-desktop.lock"
                or not line.startswith(
                    component["project"] + "==" + component["version"] + " "
                )
                or hashes != [component["wheel_sha256"]]
                or found_core
            ):
                raise ValueError("desktop lock must pin its exact bundled core")
            found_core = True
    if not found:
        raise ValueError("runtime lock omits this exact release wheel")
    if selected == "requirements-desktop.lock" and not found_core:
        raise ValueError("desktop lock omits its exact bundled core")
    return record


def install(
    root: Path,
    destination: Path,
    *,
    selected: str = "requirements.lock",
    offline: bool = False,
    wheelhouse: Path | None = None,
) -> dict[str, object]:
    """Install reviewed wheels; offline mode forbids indexes/cache fallback."""
    record = validate_assets(root, selected)
    minimum = tuple(record["python_min"])
    if sys.version_info[:2] < minimum:
        raise ValueError("use Python " + ".".join(map(str, minimum)) + " or newer")
    if destination.exists() or destination.is_symlink():
        raise ValueError("installation directory must be new")
    if not destination.parent.is_dir():
        raise ValueError("installation parent must exist")
    if offline and (wheelhouse is None or not wheelhouse.is_dir()):
        raise ValueError(
            "offline installation requires a retained wheelhouse directory"
        )
    uv = shutil.which("uv")
    if uv is None:
        raise ValueError("install an authenticated uv tool before continuing")
    environment = dict(
        os.environ,
        UV_PYTHON_DOWNLOADS="never",
        UV_VENV_SEED="false",
        PYTHONNOUSERSITE="1",
    )
    environment.pop("UV_PROJECT_ENVIRONMENT", None)

    def run(args: list[str]) -> None:
        subprocess.run([uv, *args], cwd=root, env=environment, check=True)  # noqa: S603 - authenticated explicit uv executable, no shell

    destination.mkdir(mode=0o700)
    run(
        [
            "venv",
            "--python",
            sys.executable,
            "--no-cache",
            *(["--offline"] if offline else []),
            str(destination),
        ]
    )
    command = [
        "pip",
        "install",
        "--python",
        str(destination / "bin/python"),
        "--require-hashes",
        "--only-binary",
        ":all:",
        "--no-cache",
        "--find-links",
        str(root),
        "-r",
        str(root / selected),
    ]
    if offline:
        command += ["--offline", "--no-index", "--find-links", str(wheelhouse)]
    run(command)
    run(
        [
            "pip",
            "check",
            "--python",
            str(destination / "bin/python"),
            "--no-cache",
            *(["--offline"] if offline else []),
        ]
    )
    receipt = dict(
        record,
        requirements_profile=selected,
        requirements_sha256=_hash(root / selected),
        offline=offline,
        python=sys.version,
        starts_services=False,
        model_trust_granted=False,
    )
    components = record.get("components", [])
    receipt["components_installed"] = (
        components if selected == "requirements-desktop.lock" else []
    )
    evidence = destination / "sovereign-release"
    evidence.mkdir(mode=0o700)
    for name in (record["wheel"], "INSTALL.json", "SHA256SUMS", selected):
        shutil.copyfile(root / name, evidence / name)
    if selected == "requirements-desktop.lock":
        for component in components:
            name = component["wheel"]
            shutil.copyfile(root / name, evidence / name)
    (destination / "sovereign-install.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    )
    return receipt


def main() -> int:
    """Report failure, retaining partial installs for operator inspection."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--requirements", default="requirements.lock")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--wheelhouse", type=Path)
    args = parser.parse_args()
    try:
        result = install(
            Path(__file__).resolve().parent,
            args.directory.absolute(),
            selected=args.requirements,
            offline=args.offline,
            wheelhouse=args.wheelhouse.absolute() if args.wheelhouse else None,
        )
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(
            json.dumps({"error": str(error), "partial_install_retained": True}),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
