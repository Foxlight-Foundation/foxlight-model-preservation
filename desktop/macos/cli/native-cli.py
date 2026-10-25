"""Run only the bundled public commands with their isolated installed packages."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int | None:
    """Select a fixed installed entry point without model or source trust changes."""
    if len(sys.argv) < 2:
        raise SystemExit("A bundled command is required.")
    command = sys.argv.pop(1)
    resources = Path(__file__).resolve().parent
    sys.path.insert(0, str(resources / "site-packages"))
    sys.argv[0] = command
    if command == "model-preserve":
        from foxlight_model_preservation.cli import main as entry
    elif command == "foxlight-sovereign":
        from foxlight_preservation_library.desktop import main as entry
    else:
        raise SystemExit("Unknown bundled Sovereign command.")
    return entry()


if __name__ == "__main__":
    raise SystemExit(main())
