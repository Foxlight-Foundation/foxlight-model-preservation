# Reproducible public installation

The standalone core needs no Skulk, SDK, GPU or hosted account. Library is an
optional separate application that connects to a compatible Skulk Core. Installing
software does not follow sources, trust model keys, set up Core, download models,
install native peer software or grant public serving.

The `v0.16.0` core and `library-v0.12.0` Library pre-releases provide one installation
archive each, plus `release.sigstore.json`, a detached build-attestation bundle.
The archive contains a wheel, source distribution, `INSTALL.json`, hash-locked
runtime requirements, `install.py` and inner checksums. An optional standalone
peer profile has its own lock; default installation needs no native peer runtime.
The separately pinned compatible Core remains development source, not a stable
or fully frozen Core release. See [Core setup](../capability/preview.md).

## Download and authenticate

Choose the standalone core, or the optional Library with Python 3.13+:

| Role | Tag | Archive |
| --- | --- | --- |
| Standalone | `v0.16.0` | `foxlight-model-preservation-0.16.0-install.zip` |
| Optional Library | `library-v0.12.0` | `foxlight-preservation-library-0.12.0-install.zip` |

Download the archive and `release.sigstore.json` from the same chosen release.
Public asset downloads need no account. For the standalone core:

```sh
base=https://github.com/Foxlight-Foundation/foxlight-model-preservation/releases/download/v0.16.0
curl --fail --location --proto '=https' -O "$base/foxlight-model-preservation-0.16.0-install.zip"
curl --fail --location --proto '=https' -O "$base/release.sigstore.json"
```

Authenticate the archive **before extracting/executing its helper**, using a
trusted verifier and the expected repository, workflow and tag. An independently
authenticated archive hash is another bootstrap path; same-source checksums
alone do not identify a publisher.

```sh
gh attestation verify foxlight-model-preservation-0.16.0-install.zip \
  --bundle release.sigstore.json \
  --repo Foxlight-Foundation/foxlight-model-preservation \
  --signer-workflow Foxlight-Foundation/foxlight-model-preservation/.github/workflows/release.yml \
  --source-ref refs/tags/v0.16.0 --deny-self-hosted-runners
```

Use the corresponding archive/tag for Library. Pin `--source-digest COMMIT` as
well when you obtained that commit independently. Verification binds artifact
bytes to the GitHub-hosted release workflow, not a maintainer's offline signing
key or a guarantee about code correctness. Retain the attestation, trusted roots
and verifier software as described in [release verification](releases.md).
Model/source/registry authority is independent of software provenance.

## Install into a new environment

Use Python 3.11+ for standalone or Python 3.13+ for Library and an authenticated
`uv` installation. The release workflow uses uv 0.12.23. Keep compatible Python/uv
binaries separately for offline use; this helper never acquires Python for you.

```sh
unzip foxlight-model-preservation-0.16.0-install.zip -d sovereign-install
cd sovereign-install
python3 install.py --directory "$HOME/sovereign-0.16.0"
"$HOME/sovereign-0.16.0/bin/model-preserve" --help
```

The parent must exist and the target must be new. The helper verifies every
listed asset and closed installation record before creating the environment.
Dependency versions/hashes come from the committed project lock and include the
exact release wheel. Binary-only/hash-required installation refuses unlisted
bytes and builds no dependency source. The release wheel itself comes from the
verified local files. Ordinary unpinned package-index installation remains
possible, but does not carry this reproducibility claim.

The result retains `sovereign-install.json` and `sovereign-release/` with its
original wheel, source record, selected requirements and checksums. Receipts
name exact source/software bytes; they do not enroll model signers or grant
serving. A failure leaves the partial directory for inspection rather than
adopting or deleting existing software. Use a separate new directory for a new
installation. No service is started, enabled or resumed by the installer.

For the standalone peer tools, choose `--requirements requirements-peer.lock`.
This locks the Python peer adapter; native Kubo installation and network use
still require their existing separate explicit grants. Persistent HTTP mirrors
need only the default profile. See [mirror operation](persistent-mirrors.md).

PirateFace lookup uses the default core in 0.15.0 and later. Offline repository
torrent import also needs libtorrent: choose `--requirements
requirements-mirror.lock` from that core's archive for its hash-locked optional
dependencies. Installing this profile starts no peer runtime or sharing session.
See [PirateFace interoperability](pirateface.md) for the separate offline workflow.

For Library, use the corresponding archive and target directory, then run
`bin/foxlight-preservation-library` against your compatible local Core. Existing
Core API/trust checks remain in place. A separate new Core can be prepared with
`bin/foxlight-preservation-preview setup`; this explicitly downloads/builds the
pinned Core and its prerequisites. Retained Library wheel evidence is checked
against actual installed files before choosing its immutable public source.
A copied version label alone does not qualify a local package for this setup.

## Retain a wheelhouse and install offline

While the dependency sources are available, retain the platform-compatible
wheels with a trusted Python/pip tool. From the extracted authenticated archive:

```sh
python3 -m pip download --only-binary=:all: --require-hashes \
  --find-links . --dest wheelhouse -r requirements.lock
```

Choose `requirements-peer.lock` instead when retaining that standalone profile.
A wheelhouse is specific to Python/ABI/platform; test it on the intended recovery
host. Save the installation archive, wheelhouse, Python/uv, verifier, detached
attestation and trusted-root snapshot on storage independent of Foxlight/GitHub.
Signatures do not encrypt them. A model recovery kit does not contain the runtime.

Install with all indexes and cache fallback disabled:

```sh
python3 install.py --directory "$HOME/sovereign-offline" \
  --offline --wheelhouse ./wheelhouse
```

Use the same selected requirements profile that you archived. Missing or changed
wheels cause failure rather than a network fallback. The helper checks installed
dependency compatibility and retains the exact receipt. To verify the archive
itself without online attestation/root retrieval, supply both `--bundle` and your
saved `--custom-trusted-root` to the verifier.

This reproduces selected versions and hashes, not bit-identical builds across
machines. Dependencies and platform runtimes have their own supply-chain trust.
It does not qualify all filesystems, public routing/NAT, independent custody,
anonymity or censorship resistance. Keep those deployment/failure gates separate.
