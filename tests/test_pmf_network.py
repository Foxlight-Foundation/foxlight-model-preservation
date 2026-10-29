"""Native PMF network transport: mirror, serve, fetch and recover byte for byte."""

from __future__ import annotations

import http.client
import importlib.util
import queue
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import Capture
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_peer_native import native_binary  # noqa: F401 - shared fixture

from foxlight_model_preservation.archives import verify_archive
from foxlight_model_preservation.mirror import model_action, run_mirror
from foxlight_model_preservation.mirror_intake import retain_local
from foxlight_model_preservation.mirror_models import SourceGrant, Terms
from foxlight_model_preservation.mirror_prune import prune
from foxlight_model_preservation.mirror_state import MirrorLedger, encode_state
from foxlight_model_preservation.models import PreservationError, TrustProfile
from foxlight_model_preservation.pmf import pack_pmf, restore_pmf, verify_pmf
from foxlight_model_preservation.pmf_models import PMF_VERSION, PMFManifest, PMFMember
from foxlight_model_preservation.pmf_preservation import (
    export_preservation,
    import_preservation,
)
from foxlight_model_preservation.seed import SeedServer
from foxlight_model_preservation.signing import public_record
from foxlight_model_preservation.storage import (
    manifest_path,
    object_path,
    verify_store,
)
from foxlight_model_preservation.transfer import TransferPolicy, fetch, plan_fetch

LOCAL = TransferPolicy(allow_http=True, allow_private=True)


@dataclass
class PublicPMF:
    """A public PMF archive projected losslessly from a v1 capture."""

    store: Path
    identity: str
    trust: TrustProfile
    manifest: PMFManifest


def pmf_trust(key: Ed25519PrivateKey) -> TrustProfile:
    return TrustProfile(
        schema_version="foxlight.model-preservation.trust.v1",
        keys=(public_record(key),),
        threshold=1,
        as_of="2026-10-29T00:00:00Z",
    )


@pytest.fixture
def public_pmf(capture: Capture, tmp_path: Path) -> PublicPMF:
    key = Ed25519PrivateKey.generate()
    store = tmp_path / "pmf"
    conversion = import_preservation(
        capture.store, capture.identity, capture.trust, store, (key,)
    )
    trust = pmf_trust(key)
    manifest = verify_pmf(store, conversion.output_identity, trust)
    # A real multi-member backup: weights and tokenizer, each with a retained
    # card document, plus the original signed provenance envelope.
    assert len(manifest.members) == 2 and len(manifest.documents) == 3
    return PublicPMF(store, conversion.output_identity, trust, manifest)


def mirror_ledger(tmp_path: Path) -> MirrorLedger:
    return MirrorLedger.initialize(
        tmp_path / "mirror",
        name="PMF mirror",
        disk_bytes=2 * 1024**3,
        ingress_daily=1024**3,
        egress_daily=1024**3,
        egress_monthly=1024**3,
        port=0,
    )


def grant(trust: TrustProfile) -> SourceGrant:
    return SourceGrant(
        trust=trust,
        terms=(Terms(license_id="test-only", terms_reference="LICENSE"),),
    )


def stored_files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_pmf_mirrored_served_fetched_and_recovered_byte_for_byte(
    public_pmf: PublicPMF, capture: Capture, tmp_path: Path
) -> None:
    ledger = mirror_ledger(tmp_path)
    retained = retain_local(
        ledger, public_pmf.store, public_pmf.identity, grant(public_pmf.trust)
    )
    assert retained == public_pmf.manifest
    assert ledger.read().models[public_pmf.identity].state == "retained"
    original = (public_pmf.store / manifest_path(public_pmf.identity)).read_bytes()
    # The mirror holds its own copy: the owner's archive can disappear.
    shutil.rmtree(public_pmf.store)

    ready: queue.Queue[int] = queue.Queue()
    failures: list[BaseException] = []

    def run() -> None:
        try:
            run_mirror(ledger, public=True, seconds=30, ready=ready.put)
        except BaseException as error:  # noqa: BLE001 - surfaced by the test
            failures.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        port = ready.get(timeout=10)
        end = time.monotonic() + 10
        while True:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            connection.request("GET", "/" + manifest_path(public_pmf.identity))
            response = connection.getresponse()
            body = response.read()
            connection.close()
            if response.status == 200:
                break
            assert time.monotonic() < end
            time.sleep(0.05)
        assert body == original
        mirror = (f"http://127.0.0.1:{port}",)
        received = tmp_path / "recipient"
        plan = plan_fetch(
            public_pmf.identity, public_pmf.trust, mirror, received, LOCAL
        )
        assert plan.logical_bytes == public_pmf.manifest.total_bytes
        fetched = fetch(public_pmf.identity, public_pmf.trust, mirror, received, LOCAL)
        assert fetched == public_pmf.manifest
    finally:
        ledger.update(lambda state: state.model_copy(update={"paused": True}))
        thread.join(15)
    assert not thread.is_alive() and not failures

    assert (received / manifest_path(public_pmf.identity)).read_bytes() == original
    restored = tmp_path / "restored"
    restore_pmf(received, public_pmf.identity, public_pmf.trust, restored)
    for member in ("weights", "tokenizer"):
        assert (restored / "members" / member / "model.bin").read_bytes() == (
            (member.encode() + b"\x00") * 1000
        )
        assert (restored / "members" / member / "LICENSE").is_file()
    documents = {item.document_id: item for item in public_pmf.manifest.documents}
    for name, item in documents.items():
        data = (restored / "documents" / name).read_bytes()
        assert len(data) == item.size
    # Signed provenance survives the network: the original v1 capture is
    # recovered exactly, under the original owner's independent trust.
    exported = tmp_path / "original"
    export_preservation(
        received,
        public_pmf.identity,
        public_pmf.trust,
        exported,
        source_trust=capture.trust,
        expected_source_manifest_id=capture.identity,
    )
    assert verify_store(exported, capture.identity, capture.trust)
    assert (exported / manifest_path(capture.identity)).read_bytes() == (
        capture.store / manifest_path(capture.identity)
    ).read_bytes()


def test_pmf_tampered_object_and_substituted_manifest_are_refused(
    public_pmf: PublicPMF, capture: Capture, tmp_path: Path
) -> None:
    document = public_pmf.manifest.documents[0]
    tampered = tmp_path / "tampered"
    shutil.copytree(public_pmf.store, tampered)
    path = tampered / object_path(document.sha256)
    path.chmod(0o600)
    data = bytearray(path.read_bytes())
    data[0] ^= 1
    path.write_bytes(bytes(data))
    received = tmp_path / "from-tampered"
    with pytest.raises(PreservationError):
        fetch(
            public_pmf.identity,
            public_pmf.trust,
            (str(tampered),),
            received,
            LOCAL,
        )
    assert not (received / manifest_path(public_pmf.identity)).exists()

    # Another validly signed PMF with different content (a substitution or a
    # rollback to an older backup) is never accepted under the requested ID,
    # even when its own signer is trusted.
    other_store, other_identity, other_trust = private_pmf(capture, tmp_path)
    assert other_identity != public_pmf.identity
    substituted = tmp_path / "substituted"
    shutil.copytree(public_pmf.store, substituted)
    target = substituted / manifest_path(public_pmf.identity)
    target.chmod(0o600)
    target.write_bytes((other_store / manifest_path(other_identity)).read_bytes())
    for trust in (public_pmf.trust, other_trust):
        with pytest.raises(PreservationError):
            fetch(
                public_pmf.identity,
                trust,
                (str(substituted),),
                tmp_path / "from-substituted",
                LOCAL,
            )
    assert not (tmp_path / "from-substituted" / "manifests").exists()

    # An untrusted signer is refused before any object is requested.
    with pytest.raises(PreservationError):
        fetch(
            public_pmf.identity,
            pmf_trust(Ed25519PrivateKey.generate()),
            (str(public_pmf.store),),
            tmp_path / "untrusted",
            LOCAL,
        )


def private_pmf(capture: Capture, tmp_path: Path) -> tuple[Path, str, TrustProfile]:
    original = verify_store(capture.store, capture.identity, capture.trust)
    manifest = PMFManifest(
        schema_version=PMF_VERSION,
        name="Private backup",
        root_member=original.root_member,
        members=tuple(
            PMFMember(
                member_id=item.member_id,
                role=item.role,
                format="synthetic",
                dependencies=item.dependencies,
                files=item.files,
                intake=item.intake.model_copy(update={"audience": "private"}),
            )
            for item in original.members
        ),
    )
    key = Ed25519PrivateKey.generate()
    store = tmp_path / "private-pmf"
    identity = pack_pmf(manifest, capture.sources, {}, store, (key,))
    return store, identity, pmf_trust(key)


def test_private_pmf_backup_is_never_retained_or_served(
    capture: Capture, tmp_path: Path
) -> None:
    store, identity, trust = private_pmf(capture, tmp_path)
    assert verify_archive(store, identity, trust)
    ledger = mirror_ledger(tmp_path)
    before = encode_state(ledger.read())
    with pytest.raises(PreservationError, match="public intake"):
        retain_local(ledger, store, identity, grant(trust))
    assert encode_state(ledger.read()) == before
    with pytest.raises(PreservationError, match="public intake"):
        SeedServer(("127.0.0.1", 0), store, (identity,), trust, public=True)


def test_pmf_prune_keeps_objects_shared_with_another_format(
    public_pmf: PublicPMF, capture: Capture, tmp_path: Path
) -> None:
    ledger = mirror_ledger(tmp_path)
    retain_local(ledger, public_pmf.store, public_pmf.identity, grant(public_pmf.trust))
    retain_local(ledger, capture.store, capture.identity, grant(capture.trust))
    model_action(ledger, public_pmf.identity, "block")
    prune(ledger, public_pmf.identity, public_pmf.identity)
    store = ledger.path / "store"
    assert not (store / manifest_path(public_pmf.identity)).exists()
    for item in public_pmf.manifest.documents:
        assert not (store / object_path(item.sha256)).exists()
    # Member objects are shared with the v1 capture and stay intact.
    assert verify_store(store, capture.identity, capture.trust)


@pytest.mark.skipif(
    importlib.util.find_spec("libtorrent") is None,
    reason="optional native BitTorrent extra",
)
def test_pmf_exports_as_a_standard_torrent_of_its_exact_objects(
    public_pmf: PublicPMF, tmp_path: Path
) -> None:
    from foxlight_model_preservation.torrent import export_torrent

    exported = tmp_path / "export"
    result = export_torrent(
        public_pmf.store, public_pmf.identity, public_pmf.trust, exported
    )
    assert result.manifest_id == public_pmf.identity
    wire = exported / public_pmf.identity
    assert verify_archive(wire, public_pmf.identity, public_pmf.trust)
    assert set(stored_files(wire)) >= {
        object_path(item.sha256) for item in public_pmf.manifest.documents
    }


def test_pmf_shared_over_ipfs_by_one_file_handoff(
    public_pmf: PublicPMF,
    tmp_path: Path,
    native_binary: str,  # noqa: F811 - the shared fixture
) -> None:
    import json
    import subprocess
    import sys

    from test_peer_native import model_sharer, node

    from foxlight_model_preservation.handoffs import read_handoff

    with node(tmp_path, "pmf-router", native_binary, mode="mirror") as (
        _,
        _,
        contact,
    ):
        with model_sharer(tmp_path, public_pmf, native_binary, (contact,)) as (
            _process,
            handoff,
            _repo,
        ):
            record = read_handoff(handoff.read_bytes())
            assert record.reference.manifest_id == public_pmf.identity
            assert record.reference.manifest == public_pmf.manifest
            receiver = tmp_path / "pmf-receiver"
            result = subprocess.run(  # noqa: S603 - own module, fixed arguments
                [
                    sys.executable,
                    "-m",
                    "foxlight_model_preservation.cli",
                    "handoff-fetch",
                    "--handoff",
                    str(handoff),
                    "--store",
                    str(receiver),
                    "--repo",
                    str(tmp_path / "pmf-receiver-peer"),
                    "--ipfs",
                    native_binary,
                    "--allow-peer-network",
                    "--allow-private-peers",
                    "--seconds",
                    "60",
                    "--accept-key",
                    public_pmf.trust.keys[0].key_id,
                ],
                capture_output=True,
                timeout=85,
                check=False,
            )
            assert result.returncode == 0, result.stderr.decode()
            assert json.loads(result.stdout)["serving"] is False
    assert verify_pmf(receiver, public_pmf.identity, public_pmf.trust) == (
        public_pmf.manifest
    )
    restored = tmp_path / "pmf-restored"
    restore_pmf(receiver, public_pmf.identity, public_pmf.trust, restored)
    assert (restored / "members/weights/model.bin").read_bytes() == (
        b"weights\x00" * 1000
    )


def test_pmf_mirror_selection_recovers_and_exports_a_pmf_kit(
    public_pmf: PublicPMF, tmp_path: Path
) -> None:
    from foxlight_model_preservation.kits import import_kit, inspect_kit
    from foxlight_model_preservation.mirror_recovery import (
        export_selection_kit,
        recover_selection,
    )

    ledger = mirror_ledger(tmp_path)
    retain_local(ledger, public_pmf.store, public_pmf.identity, grant(public_pmf.trust))
    assert recover_selection(ledger, public_pmf.identity) == public_pmf.manifest
    kit = tmp_path / "selection.foxkit"
    export_selection_kit(ledger, public_pmf.identity, kit)
    assert inspect_kit(kit).manifest == public_pmf.manifest
    recovered = tmp_path / "from-kit"
    import_kit(
        kit,
        recovered,
        trust=public_pmf.trust,
        expected_manifest_id=public_pmf.identity,
    )
    assert verify_pmf(recovered, public_pmf.identity, public_pmf.trust) == (
        public_pmf.manifest
    )


def test_network_pmf_identity_is_signer_independent_and_bound_to_its_payload(
    capture: Capture, tmp_path: Path
) -> None:
    from foxlight_model_preservation.pmf_preservation import (
        PUBLIC_DOCUMENT,
        inspect_network_pmf,
        is_network_pmf,
        public_network_pmf,
        publish_network_pmf,
    )

    original = verify_store(capture.store, capture.identity, capture.trust)
    identities = set()
    for name in ("one", "two"):
        key = Ed25519PrivateKey.generate()
        store = tmp_path / name
        identity = publish_network_pmf(
            capture.store, capture.identity, capture.trust, store, (key,)
        )
        manifest = verify_pmf(store, identity, pmf_trust(key))
        assert is_network_pmf(manifest)
        document = next(
            item for item in manifest.documents if item.document_id == PUBLIC_DOCUMENT
        )
        payload = (store / object_path(document.sha256)).read_bytes()
        assert inspect_network_pmf(manifest, payload) == (capture.identity, original)
        with pytest.raises(PreservationError):
            inspect_network_pmf(manifest, payload.replace(b"fixture", b"fixturf"))
        identities.add(identity)
    assert identities == {public_network_pmf(original, None)[0]}

    private = original.model_copy(
        update={
            "members": tuple(
                member.model_copy(
                    update={
                        "intake": member.intake.model_copy(
                            update={"audience": "private"}
                        )
                    }
                )
                for member in original.members
            )
        }
    )
    with pytest.raises(PreservationError, match="public intake"):
        public_network_pmf(private)


def test_mirror_intake_can_share_immutable_objects_instead_of_copying(
    public_pmf: PublicPMF, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    member = public_pmf.manifest.members[0].files[0]
    source = public_pmf.store / object_path(member.sha256)
    (tmp_path / "linked").mkdir()
    (tmp_path / "copied").mkdir()
    linked = mirror_ledger(tmp_path / "linked")
    retain_local(
        linked,
        public_pmf.store,
        public_pmf.identity,
        grant(public_pmf.trust),
        link_objects=True,
    )
    retained = linked.path / "store" / object_path(member.sha256)
    assert os.stat(retained).st_ino == os.stat(source).st_ino
    assert verify_archive(linked.path / "store", public_pmf.identity, public_pmf.trust)

    # A filesystem that cannot link falls back to an ordinary verified copy.
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError("cross-device link")

    monkeypatch.setattr(os, "link", refuse)
    copied = mirror_ledger(tmp_path / "copied")
    retain_local(
        copied,
        public_pmf.store,
        public_pmf.identity,
        grant(public_pmf.trust),
        link_objects=True,
    )
    duplicate = copied.path / "store" / object_path(member.sha256)
    assert os.stat(duplicate).st_ino != os.stat(source).st_ino
    assert duplicate.read_bytes() == source.read_bytes()
