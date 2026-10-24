"""Build an optional isolated Snark pilot runtime from immutable upstream sources.

Requires explicitly selected Java 17+ and Ant; does not start a router/client,
install system services, modify an existing checkout or publish binaries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

I2P_REVISION = "3c98503c5f14efe7bdd783528ba7f621f12d8081"
RPC_REVISION = "9fa6d9bc601f9ea6cfd18ce1e365bee989067665"


def build(destination: Path, java_home: Path, ant: Path) -> str:
    """Build into a fresh directory and return its untrusted runtime-record digest."""
    destination.mkdir(mode=0o700)
    environment = dict(os.environ, JAVA_HOME=str(java_home))
    for key in ("JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "CLASSPATH"):
        environment.pop(key, None)

    def run(arguments: list[str], cwd: Path) -> None:
        subprocess.run(arguments, cwd=cwd, env=environment, check=True)  # noqa: S603 - explicit executable, no shell

    for name, url, revision in (
        ("i2p", "https://github.com/i2p/i2p.i2p.git", I2P_REVISION),
        ("rpc", "https://github.com/i2p/i2p.plugins.i2psnark-rpc.git", RPC_REVISION),
    ):
        source = destination / name
        source.mkdir()
        run(["git", "init", "--quiet"], source)
        run(["git", "fetch", "--depth=1", url, revision], source)
        run(["git", "checkout", "--detach", "FETCH_HEAD"], source)
    i2p, rpc = destination / "i2p", destination / "rpc"
    run([str(ant), "-q", "-Drequire.gettext=false", "buildRouter", "i2psnark"], i2p)
    shutil.copyfile(
        i2p / "apps/i2psnark/java/build/i2psnark.jar", i2p / "build/i2psnark.jar"
    )
    patched = rpc / "src/java/org/klomp/snark/rpc/XMWebUIPlugin.java"
    original = (
        "            torrent = new MetaInfo(new ByteArrayInputStream(metainfoBytes));"
    )
    replacement = """            try {
                torrent = new MetaInfo(new ByteArrayInputStream(metainfoBytes));
            } catch (IOException e) {
                throw e;
            } catch (Throwable e) {
                throw new IOException("Invalid torrent metainfo", e);
            }"""
    text = patched.read_text()
    if text.count(original) != 1:
        raise ValueError("reviewed RPC compatibility patch no longer applies")
    text = text.replace(original, replacement)
    original_base = (
        "_manager.addTorrent( torrent, null, null, file_Download_dir, add_stopped )"
    )
    replacement_base = (
        "_manager.addTorrent( torrent, null, null, "
        "file_Download_dir == null ? null : new File(file_Download_dir, "
        "torrent.getName()), add_stopped )"
    )
    if text.count(original_base) != 1:
        raise ValueError("reviewed RPC base-file compatibility patch no longer applies")
    patched.write_text(text.replace(original_base, replacement_base))
    run([str(ant), "-q", "-f", "src/build.xml", "-Di2pbase=" + str(i2p), "war"], rpc)
    runtime = destination / "runtime"
    runtime.mkdir()
    shutil.copyfile(
        i2p / "apps/i2psnark/java/build/i2psnark-standalone.jar", runtime / "snark.jar"
    )
    run(
        [
            str(java_home / "bin/jar"),
            "cf",
            str(runtime / "rpc.jar"),
            "-C",
            str(rpc / "src/build/obj"),
            ".",
        ],
        destination,
    )
    launcher = (
        Path(__file__).resolve().parent.parent
        / "src/foxlight_model_preservation/resources/SnarkPilot.java"
    )
    classes = destination / "launcher-classes"
    run(
        [
            str(java_home / "bin/javac"),
            "-cp",
            os.pathsep.join(str(runtime / p) for p in ("snark.jar", "rpc.jar")),
            "-d",
            str(classes),
            str(launcher),
        ],
        destination,
    )
    run(
        [
            str(java_home / "bin/jar"),
            "cf",
            str(runtime / "launcher.jar"),
            "-C",
            str(classes),
            ".",
        ],
        destination,
    )
    record = {
        "schema_version": "foxlight.i2p-runtime.v1",
        "i2p_version": "2.14.0",
        "i2p_revision": I2P_REVISION,
        "rpc_revision": RPC_REVISION,
        "launcher_sha256": hashlib.sha256(launcher.read_bytes()).hexdigest(),
        "rpc_patched_sha256": hashlib.sha256(patched.read_bytes()).hexdigest(),
        "files": {
            name: hashlib.sha256((runtime / name).read_bytes()).hexdigest()
            for name in ("snark.jar", "rpc.jar", "launcher.jar")
        },
    }
    for source, name in ((i2p / "licenses", "i2p"), (rpc / "plugin/licenses", "rpc")):
        shutil.copytree(source, runtime / "licenses" / name)
    shutil.copyfile(
        launcher.with_name("SnarkPilot-LICENSE.txt"), runtime / "SnarkPilot-LICENSE.txt"
    )
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    (runtime / "RUNTIME.json").write_bytes(encoded)
    for source, name in (
        (i2p / "LICENSE.txt", "I2P-LICENSE.txt"),
        (rpc / "LICENSE.txt", "RPC-LICENSE.txt"),
    ):
        shutil.copyfile(source, runtime / name)
    print("Runtime ID is a local build identity, not an upstream attestation.")
    return hashlib.sha256(encoded).hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--java-home", type=Path, required=True)
    parser.add_argument("--ant", type=Path, required=True)
    parser.add_argument("--allow-source-download", action="store_true", required=True)
    args = parser.parse_args()
    print(
        build(args.directory.absolute(), args.java_home.absolute(), args.ant.absolute())
    )
