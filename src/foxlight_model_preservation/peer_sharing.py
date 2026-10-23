"""Public peer publication and handoff; no Skulk, runtime or signer enrollment."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .handoffs import Handoff, peer_contacts
from .models import PreservationError, TrustProfile
from .operations import OperationControl
from .peer import publish_peer_model
from .peer_invitations import peer_invitation_from_envelope
from .peer_runtime import PeerClient, initialize_peer, serve_peer
from .signing import encode_record
from .storage import directory, manifest_path, read_metadata, verify_store_contents


def prepare_peer_handoff(
    store: Path,
    manifest_id: str,
    trust: TrustProfile,
    client: PeerClient,
    *,
    public_model: bool,
    bootstrap: tuple[str, ...] = (),
) -> Handoff:
    """Verify/pin only one complete public closure and export metadata hints.

    Requires an already explicitly started mirror profile. The model's original
    envelope/card/provenance bytes remain unchanged; contacts and CID are not
    model authority or a reachability promise. No signing key is needed.
    """
    publication = publish_peer_model(
        store, manifest_id, trust, client, public_model=public_model
    )
    mirror = publication["mirror"]
    if not isinstance(mirror, str):
        raise PreservationError("peer publication returned an invalid hint")
    with directory(store) as root:
        envelope = read_metadata(root, manifest_path(manifest_id))
    reference = peer_invitation_from_envelope(envelope, manifest_id, trust, (mirror,))
    handoff = Handoff(reference=reference, contacts=peer_contacts(client, bootstrap))
    # Require a complete serializable reference before reporting readiness.
    _ = encode_record(handoff)
    return handoff


def serve_peer_model(
    store: Path,
    manifest_id: str,
    trust: TrustProfile,
    repo: Path,
    binary: str,
    *,
    contacts: tuple[str, ...],
    allow_private: bool,
    allow_network: bool,
    share_public: bool,
    public_model: bool,
    seconds: int,
    max_upload_bytes: int,
    control: OperationControl,
    prepared: Callable[[Handoff], None],
    observe_upload: Callable[[int], None],
) -> None:
    """Own a finite mirror in a host-supervised worker's process group.

    The host must fence EOF/cancellation and drain this worker's actual process
    group. Standalone callers use the CLI's EOF-fenced supervisor instead. The
    repo is fresh and host-selected; restarting never adopts or replays it.
    Stopping retains the source archive and any exported handoff.
    """
    if not allow_network or not share_public or not public_model:
        raise PreservationError("explicit public model and peer participation required")
    if not 1 <= seconds <= 86400 or not 1 <= max_upload_bytes <= 2**53 - 1:
        raise PreservationError("finite peer sharing bounds required")
    manifest = verify_store_contents(store, manifest_id, trust, control)
    if any(member.intake.audience != "public" for member in manifest.members):
        raise PreservationError("peer sharing requires a public complete closure")
    control.checkpoint()
    initialize_peer(
        repo,
        binary,
        mode="mirror",
        bootstrap=contacts,
        allow_private_peers=allow_private,
    )

    def ready(client: PeerClient) -> None:
        prepared(
            prepare_peer_handoff(
                store, manifest_id, trust, client, public_model=True, bootstrap=contacts
            )
        )

    _ = serve_peer(
        repo,
        binary,
        allow_network=True,
        share_public=True,
        seconds=seconds,
        max_upload_bytes=max_upload_bytes,
        control=control,
        on_ready=ready,
        observe_upload=observe_upload,
    )
