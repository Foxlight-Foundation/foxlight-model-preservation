"""Export the unchanged capability-owned manifest for the public publisher."""

import argparse
from pathlib import Path

from skulk_capability_sdk.contracts import canonical
from skulk_capability_sdk.storage import atomic_private, secure_directory

from foxlight_preservation_library.contracts import build_manifest


def main() -> None:
    """Write protected nonexecuting metadata; signing binds the actual bundle."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    secure_directory(arguments.output.parent)
    atomic_private(
        arguments.output,
        canonical(
            build_manifest(
                executable="/installed/preservation-library.pyz",
                executable_sha256="0" * 64,
            )
        ),
    )


if __name__ == "__main__":
    main()
