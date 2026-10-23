"""Finite standalone sessions using the existing EOF-fenced native supervisor."""

from __future__ import annotations

import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from .models import PreservationError
from .operations import OperationCancelled, OperationControl, OperationError
from .peer_runtime import PeerClient, initialize_peer, run_peer


@contextmanager
def owned_peer(
    repo: Path,
    binary: str,
    *,
    mode: Literal["receive", "mirror"],
    contacts: tuple[str, ...],
    allow_network: bool,
    allow_private: bool,
    share_public: bool = False,
    seconds: int = 3600,
    max_upload_bytes: int = 1024**3,
    control: OperationControl,
) -> Generator[PeerClient]:
    """Create one fresh profile; drain its actual supervised node on every exit.

    Permission and bounds precede even offline profile creation. The native
    supervisor runs in a separate EOF-fenced process group, so abrupt loss of
    this caller cannot turn into an independently running public server.
    Retained archives are never removed. Existing repos are never adopted.
    """
    if not allow_network or (mode == "mirror") != share_public:
        raise PreservationError("separate peer network/public participation required")
    if not 1 <= seconds <= 86400 or not 1 <= max_upload_bytes <= 2**53 - 1:
        raise PreservationError("finite peer lifetime and allowance required")
    control.checkpoint()
    initialize_peer(
        repo, binary, mode=mode, bootstrap=contacts, allow_private_peers=allow_private
    )
    control.checkpoint()
    lifetime = OperationControl()
    failures: list[BaseException] = []

    def run() -> None:
        try:
            _ = run_peer(
                repo,
                binary,
                allow_network=True,
                share_public=share_public,
                seconds=seconds,
                max_upload_bytes=max_upload_bytes,
                control=lifetime,
            )
        except OperationCancelled:
            pass
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=run, name="preservation-peer-owner", daemon=True)
    thread.start()
    try:
        end = time.monotonic() + 25
        while time.monotonic() < end:
            control.checkpoint()
            if not thread.is_alive():
                raise PreservationError("owned peer stopped before ready")
            if (repo / "api").exists():
                try:
                    client = PeerClient(
                        repo,
                        binary,
                        allow_network=True,
                        # A node's wire deadline must not expire its caller
                        # while normal owned-process teardown is still draining.
                        # Cancellation remains shared in both directions.
                        control=control.fork(),
                        seconds=seconds,
                    )
                    _ = client.wire_usage()
                    break
                except OperationError:
                    raise
                except (OSError, ValueError):
                    pass
            time.sleep(0.05)
        else:
            raise PreservationError("owned peer startup deadline exceeded")
        yield client
    finally:
        lifetime.cancel()
        thread.join(timeout=25)
        if thread.is_alive():
            raise PreservationError("owned peer stop could not be confirmed")
    if failures:
        raise PreservationError("owned peer session failed") from failures[0]
