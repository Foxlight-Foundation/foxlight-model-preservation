"""python-tuf's client, made safe to use on every platform Sovereign runs on."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from tuf.ngclient import Updater


class TufUpdater(Updater):
    """python-tuf's client, keeping ``root.json`` as a copy on Windows.

    The client points ``root.json`` at the current ``root_history`` file with
    a symbolic link, which Windows lets only privileged accounts (or
    Developer Mode) create. There it is written as an atomic copy instead.
    """

    if sys.platform == "win32":

        def _update_root_symlink(self) -> None:
            version = self._trusted_set.root.version  # pyright: ignore[reportPrivateUsage]
            data = Path(
                self._dir,  # pyright: ignore[reportPrivateUsage]
                "root_history",
                f"{version}.root.json",
            ).read_bytes()
            self._persist_file(  # pyright: ignore[reportPrivateUsage]
                os.path.join(self._dir, "root.json"),  # pyright: ignore[reportPrivateUsage]
                data,
            )
