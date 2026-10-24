"""Actual I2P/Snark private-network transfer and owned-process lifetime evidence."""

import http.client
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from foxlight_model_preservation.archives import verify_archive
from foxlight_model_preservation.i2p import export_i2p, transfer_i2p
from foxlight_model_preservation.i2p_models import I2PContact
from foxlight_model_preservation.i2p_runtime import initialize_i2p, native_status
from foxlight_model_preservation.operations import OperationCancelled, OperationControl
from foxlight_model_preservation.pmf import restore_pmf
from foxlight_model_preservation.pmf_preservation import (
    export_preservation,
    import_preservation,
)
from foxlight_model_preservation.signing import digest, encode_record
from foxlight_model_preservation.storage import manifest_path

RUNTIME = os.environ.get("SOVEREIGN_I2P_RUNTIME")
JAVA_HOME = os.environ.get("SOVEREIGN_I2P_JAVA_HOME")
ROUTER = os.environ.get("SOVEREIGN_I2P_ROUTER_JAR")
pytestmark = pytest.mark.skipif(
    not (RUNTIME and JAVA_HOME and ROUTER),
    reason="explicit optional native I2P pilot runtime",
)


def wait_until(predicate, seconds=30):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            result = predicate()
            if result:
                return result
        except (FileNotFoundError, ConnectionError):
            pass
        time.sleep(0.1)
    raise AssertionError("native I2P pilot did not reach its bounded checkpoint")


def socket_snapshot(pid, destination):
    tool = shutil.which("lsof")
    if tool is None:
        return
    result = subprocess.run(  # noqa: S603 - selected diagnostic, exact owned PID
        [tool, "-nP", "-a", "-p", str(pid), "-i"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    destination.write_text(result.stdout)
    rows = result.stdout.splitlines()[1:]
    assert rows
    for row in rows:
        protocol = " TCP " if " TCP " in row else " UDP "
        endpoints = row.split(protocol)[1].split()[0].split("->")
        assert all(endpoint.startswith("127.0.0.1:") for endpoint in endpoints), row


def listening(port):
    with socket.socket() as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture(scope="module")
def native_network(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("private-i2p-network")
    monkeypatch = pytest.MonkeyPatch()
    real_popen = subprocess.Popen
    native_logs = []

    def diagnostic_popen(arguments, **kwargs):
        if "ai.foxlight.sovereign.i2p.SnarkPilot" in arguments:
            cwd = Path(kwargs["cwd"])
            (cwd / "logger.config").write_text(
                "logger.defaultLevel=INFO\nlogger.fileSize=2000000\nlogger.numFiles=2\n"
            )
            arguments = [
                arguments[0],
                "-DloggerFilenameOverride=" + str(cwd / "native.log"),
                *arguments[1:],
            ]
            log = (cwd / "console.log").open("wb")
            native_logs.append(log)
            kwargs.update(stdout=log, stderr=subprocess.STDOUT)
        return real_popen(arguments, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", diagnostic_popen)
    runtime, java_home = Path(RUNTIME), Path(JAVA_HOME)
    classes = tmp_path / "testnet-classes"
    classes.mkdir()
    classpath = os.pathsep.join((str(runtime / "snark.jar"), str(ROUTER)))
    subprocess.run(  # noqa: S603 - explicit runtime and fixed synthetic source
        [
            str(java_home / "bin/javac"),
            "-cp",
            classpath,
            "-d",
            str(classes),
            str(Path(__file__).parent / "native/I2PTestNet.java"),
        ],
        check=True,
    )  # noqa: S603 - fixed source, explicit native test runtime
    sockets = [
        socket.socket(type=socket.SOCK_STREAM if i % 2 == 0 else socket.SOCK_DGRAM)
        for i in range(16)
    ]
    try:
        for handle in sockets:
            handle.bind(("127.0.0.1", 0))
        ports = [handle.getsockname()[1] for handle in sockets]
    finally:
        for handle in sockets:
            handle.close()
    processes = []
    logs = []
    try:
        for index in range(8):
            log = (tmp_path / f"private-router-{index}.log").open("wb")
            logs.append(log)
            process = subprocess.Popen(  # noqa: S603 - isolated synthetic test network
                [
                    str(java_home / "bin/java"),
                    "-Xmx256m",
                    "-Djava.awt.headless=true",
                    "-cp",
                    str(classes) + os.pathsep + classpath,
                    "I2PTestNet",
                    str(tmp_path / "routers"),
                    str(index),
                    str(ports[index * 2]),
                    str(ports[index * 2 + 1]),
                ],
                stdin=subprocess.PIPE,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            processes.append(process)
        wait_until(lambda: all(listening(port) for port in ports[::2]), seconds=45)
        wait_until(
            lambda: all(
                (tmp_path / "routers" / f"state-{i}.txt").is_file()
                and "ready=true"
                in (tmp_path / "routers" / f"state-{i}.txt").read_text()
                for i in range(8)
            ),
            seconds=125,
        )
        for i, process in enumerate(processes):
            socket_snapshot(process.pid, tmp_path / f"router-{i}-sockets.txt")
        yield runtime, java_home / "bin/java", ports, processes
    finally:
        for process in processes:
            process.stdin.close()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        for log in (*logs, *native_logs):
            log.close()
        monkeypatch.undo()


def new_profile(root, native_network, port_index=0):
    runtime, java, ports, _process = native_network
    identity = digest((runtime / "RUNTIME.json").read_bytes())
    initialize_i2p(
        root,
        runtime,
        identity,
        java,
        router_port=ports[port_index],
        upload_kib=128,
        download_kib=256,
    )


def test_actual_i2p_dht_and_snark_transfer_recovers_pmf_without_source_or_registry(
    capture, native_network, tmp_path
):
    original = (capture.store / manifest_path(capture.identity)).read_bytes()
    conversion = import_preservation(
        capture.store, capture.identity, capture.trust, tmp_path / "pmf", (capture.key,)
    )
    manifest_id = conversion.output_identity
    offer = export_i2p(
        tmp_path / "pmf", manifest_id, capture.trust, tmp_path / "offered"
    )
    source = tmp_path / "offered" / offer.kit.path
    shutil.rmtree(capture.store)
    shutil.rmtree(tmp_path / "pmf")
    for root in capture.sources.values():
        shutil.rmtree(root)
    seed, receive = tmp_path / "seed", tmp_path / "receive"
    new_profile(seed, native_network)
    new_profile(receive, native_network, 2)
    control = OperationControl()
    failures = []

    def serve():
        try:
            transfer_i2p(
                offer,
                seed,
                capture.trust,
                manifest_id,
                kit_source=source,
                allow_i2p=True,
                allow_sharing=True,
                seconds=360,
                control=control,
            )
        except OperationCancelled:
            pass
        except BaseException as error:
            failures.append(error)

    worker = threading.Thread(target=serve)
    worker.start()
    try:

        def connected():
            assert not failures, failures
            status = native_status(seed)
            return status if status.connected else None

        status = wait_until(connected, seconds=150)
        socket_snapshot(status.pid, tmp_path / "seed-sockets.txt")
        contact = I2PContact(destination=status.destination, port=status.dht_port)
        routed = offer.model_copy(update={"contacts": (contact,)})
        target = tmp_path / "independently-recovered"
        result = transfer_i2p(
            routed,
            receive,
            capture.trust,
            manifest_id,
            destination=target,
            allow_i2p=True,
            allow_sharing=True,
            seconds=150,
        )
        assert result["stopped"] and not result["privacy_qualified"]
        assert not result["retained_inputs_deleted"]
        verified = verify_archive(target, manifest_id, capture.trust)
        assert len(verified.members) == 2
        assert verified.documents
        restore_pmf(target, manifest_id, capture.trust, tmp_path / "restored")
        assert (
            tmp_path / "restored/members/tokenizer/model.bin"
        ).read_bytes() == b"tokenizer\0" * 1000
        recovered = tmp_path / "original-recovered"
        export_preservation(
            target,
            manifest_id,
            capture.trust,
            recovered,
            source_trust=capture.trust,
            expected_source_manifest_id=capture.identity,
        )
        assert (recovered / manifest_path(capture.identity)).read_bytes() == original
        assert (receive / "downloads" / offer.kit.path).is_file()
        assert (seed / "downloads" / offer.kit.path).is_file()
        stopped = native_status(receive)
        assert not listening(stopped.rpc_port)
        assert native_status(seed).dht_nodes > 0
        repeated = tmp_path / "recovered-again"
        resumed = transfer_i2p(
            routed,
            receive,
            capture.trust,
            manifest_id,
            destination=repeated,
            allow_i2p=True,
            allow_sharing=True,
            seconds=20,
        )
        assert resumed["stopped"]
        assert (repeated / manifest_path(manifest_id)).read_bytes() == (
            target / manifest_path(manifest_id)
        ).read_bytes()

    finally:
        control.cancel()
        worker.join(timeout=12)
        assert not worker.is_alive(), "owned native session did not drain"
    assert not failures, failures


def test_killed_cli_closes_native_eof_fence_and_retains_kit(
    capture, native_network, tmp_path
):
    offer = export_i2p(
        capture.store, capture.identity, capture.trust, tmp_path / "offered"
    )
    profile = tmp_path / "owned-profile"
    new_profile(profile, native_network)
    trust = tmp_path / "trust.json"
    trust.write_bytes(encode_record(capture.trust))
    process = subprocess.Popen(  # noqa: S603 - fixed interpreter and test arguments
        [
            sys.executable,
            "-m",
            "foxlight_model_preservation.cli",
            "i2p",
            "share",
            "--dir",
            str(profile),
            "--offer",
            str(tmp_path / "offered/offer.json"),
            "--trust",
            str(trust),
            "--expected-manifest-id",
            capture.identity,
            "--kit",
            str(tmp_path / "offered" / offer.kit.path),
            "--allow-i2p",
            "--allow-sharing",
            "--seconds",
            "60",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )  # noqa: S603 - fixed interpreter and synthetic test selection
    try:
        status = wait_until(lambda: native_status(profile))
        assert listening(status.rpc_port)
        connection = http.client.HTTPConnection("127.0.0.1", status.rpc_port, timeout=2)
        try:
            connection.request("POST", "/transmission/rpc", b"{}")
            response = connection.getresponse()
            assert response.status == 401
            response.read()
        finally:
            connection.close()
        process.kill()
        process.wait(timeout=3)
        wait_until(lambda: not listening(status.rpc_port), seconds=10)
        assert (profile / "downloads" / offer.kit.path).is_file()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        process.stdout.close()
        process.stderr.close()


def test_finite_native_window_stops_and_retains_selected_kit(
    capture, native_network, tmp_path
):
    offer = export_i2p(
        capture.store, capture.identity, capture.trust, tmp_path / "offer"
    )
    profile = tmp_path / "finite"
    new_profile(profile, native_network)
    start = time.monotonic()
    result = transfer_i2p(
        offer,
        profile,
        capture.trust,
        capture.identity,
        kit_source=tmp_path / "offer" / offer.kit.path,
        allow_i2p=True,
        allow_sharing=True,
        seconds=3,
    )
    assert time.monotonic() - start < 12
    assert result["stopped"]
    assert not listening(native_status(profile).rpc_port)
    assert (profile / "downloads" / offer.kit.path).is_file()


def test_router_loss_stops_owned_session_and_keeps_inputs(
    capture, native_network, tmp_path
):
    from foxlight_model_preservation.models import PreservationError

    offer = export_i2p(
        capture.store, capture.identity, capture.trust, tmp_path / "offer"
    )
    profile = tmp_path / "router-loss"
    new_profile(profile, native_network, 14)
    failures = []
    control = OperationControl()

    def host():
        try:
            transfer_i2p(
                offer,
                profile,
                capture.trust,
                capture.identity,
                kit_source=tmp_path / "offer" / offer.kit.path,
                allow_i2p=True,
                allow_sharing=True,
                seconds=90,
                control=control,
            )
        except BaseException as error:
            failures.append(error)

    worker = threading.Thread(target=host)
    worker.start()
    try:
        status = wait_until(
            lambda: s if (s := native_status(profile)).connected else None, seconds=60
        )
        # Allow the Python observer to see the connected state before router loss.
        time.sleep(0.5)
        native_network[3][7].kill()
        native_network[3][7].wait(timeout=3)
        worker.join(timeout=12)
        assert not worker.is_alive()
        assert len(failures) == 1 and isinstance(failures[0], PreservationError)
        assert not listening(status.rpc_port)
        assert (profile / "downloads" / offer.kit.path).is_file()
        assert (tmp_path / "offer" / offer.kit.path).is_file()
    finally:
        control.cancel()
        worker.join(timeout=12)
