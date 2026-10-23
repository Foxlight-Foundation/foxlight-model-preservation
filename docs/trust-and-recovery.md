# Trust and recovery

Obtain the verifier and its dependencies through an authenticated software channel.
Release files carry checksums and build-provenance attestations; [verifying a
release](releases.md) explains what they establish. Obtain the public trust profile and expected manifest/catalog IDs through a
separately authenticated reference: an already enrolled owner, a verified contact,
or recovery material retained before loss. Check the **entire** public-key
fingerprint. A peer supplying its own verifier, key and checksums cannot establish
its own authenticity. Merely copying `trust.json` beside a bundle does not enroll it.

`keygen` creates a new private owner Ed25519 key in a mode-0600 file. It requests
a passphrase by default; `--passphrase-file` supports unattended protected input.
Empty passphrases are rejected. `--unencrypted` is an explicit local/test option.
Never put private keys in a mirror, catalog, repository, ordinary model backup or
support report. Keep a separate encrypted recovery copy of the key, its passphrase
in separately recoverable custody, and independently authenticated public trust
material. Test decrypting the recovery copy on an offline machine before relying
on it. Losing the private key does not prevent verification of existing bundles.

Trust profiles contain public keys, a unique-key threshold, revoked key IDs and an
`as_of` label. A key's fingerprint must match its raw public key. To rotate keys,
enroll the successor through your existing authenticated channel, retain the old
public key only for deliberate historical use, and replace trust profiles for new
intake. After compromise, revoke/remove the affected key and distribute a newly
authenticated profile. Do not use a compromised key to authenticate its successor.
The library supports multiple signatures and a threshold; the simple owner CLI
signs with one key. No online root maintenance service is included.

Offline receipts report the supplied trust snapshot's `as_of`. The label is
operator supplied, not a trusted time attestation or a live revocation check.
Offline clients cannot learn about later compromise. Bundle trust profiles have
no automatic refresh, expiry override or global removal switch. The optional
[followed sources](discovery.md) use TUF to update listings, but they never
change or revoke your bundle trust. Independently retained, deliberately trusted
exact bundles remain usable without a live signer.

For a recovery kit, retain:

1. The exact signed bundle directories, notices and any signed catalog envelope.
2. The authentic public trust profiles and exact manifest/catalog IDs, with the
   independent fingerprint verification procedure.
3. This protocol, verifier source/wheel, and platform-compatible dependency wheels
   from an authenticated distribution. A wheel alone does not contain dependencies.
   Keep the release's `SHA256SUMS` and [attestation bundles](releases.md#keep-offline-evidence).
4. Required inference engines and their separate licenses/install instructions if
   actual offline inference is needed. Model preservation alone does not provision them.

Build software with `uv build`; retain `uv.lock`. Export locked production
requirements with `uv export --no-dev --format requirements-txt`, then obtain the
matching wheels on each intended Python/OS/architecture. Verify the retained kit
on a disconnected clean environment. No Hugging Face/Foxlight account is needed
for `verify`, filesystem `fetch`, or `restore`.

A consumer fetches directly from explicitly permitted endpoints, never advertises
inventory, and never starts a seed server. HTTPS endpoints still see IP addresses
and requested model identities; network observers may see destination/timing.
Neither encryption nor signed content provides anonymity or prevents domain/IP
blocking. The optional [peer discovery](peer-discovery.md) exposes your network
address to the peers you contact. There is no reviewed vulnerable-user privacy
profile. Do not describe it as censorship resistant or anonymous.

The supported threat boundary is untrusted transport, corrupted files, incomplete
sets, substituted keys and individual mirror failure with another permitted intact
copy reachable. A compromised user account/device can replace a verifier, alter
trust or change files after verification. All-copy loss and total network isolation
without retained media are outside that boundary. Local output directories and
the OS are trusted; this is not a sandbox against a hostile process with the same
user's privileges.
