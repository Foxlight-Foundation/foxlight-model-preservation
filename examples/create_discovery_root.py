"""Create an initial public TUF root with a separately held encrypted root key.

This small stock-python-tuf example is not a hosted service. Ordinary updates
need only the online role keys. Keep private keys outside every served directory.
"""

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from securesystemslib.signer import CryptoSigner, SSlibKey
from tuf.api.metadata import Metadata, Root

from foxlight_model_preservation.signing import decode_base64, load_key, load_trust


def main() -> int:
    """Write only self-signed public root metadata, refusing existing output."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-key", type=Path, required=True)
    parser.add_argument("--passphrase-file", type=Path, required=True)
    for role in ("targets", "snapshot", "timestamp"):
        parser.add_argument("--" + role + "-trust", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    with arguments.passphrase_file.open("rb") as stream:
        password = stream.read(8193).rstrip(b"\r\n")
    if not password or len(password) > 8192:
        parser.error("use a bounded, nonempty protected passphrase file")
    root_signer = CryptoSigner(load_key(arguments.root_key, password))
    metadata = Metadata(
        Root(version=1, expires=datetime.now(UTC) + timedelta(days=365))
    )
    metadata.signed.add_key(root_signer.public_key, "root")
    for role, path in (
        ("targets", arguments.targets_trust),
        ("snapshot", arguments.snapshot_trust),
        ("timestamp", arguments.timestamp_trust),
    ):
        with path.open("rb") as stream:
            trust = load_trust(stream.read(8 * 1024 * 1024 + 1))
        for key in trust.keys:
            if key.key_id not in trust.revoked_keys:
                public = Ed25519PublicKey.from_public_bytes(
                    decode_base64(key.public_key)
                )
                metadata.signed.add_key(SSlibKey.from_crypto(public), role)
        metadata.signed.roles[role].threshold = trust.threshold
    metadata.sign(root_signer)
    metadata.signed.verify_delegate("root", metadata.signed_bytes, metadata.signatures)
    with arguments.output.open("xb") as stream:
        stream.write(metadata.to_bytes())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
