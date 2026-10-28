# Reproducible public installation

The standalone core needs no Skulk, SDK, GPU or hosted account. Library is an
optional application with independent Sovereign and compatible Skulk Core modes.
Older releases provide only the Core-backed client. Installing
software does not follow sources, trust model keys, set up Core, download models,
install native peer software or grant public serving.

The `v0.26.0` core and `library-v0.22.0` Library pre-releases provide one installation
archive each, plus `release.sigstore.json`, a detached build-attestation bundle.
Each archive contains its primary wheel, source distribution, `INSTALL.json`, hash-locked
runtime requirements, `install.py` and inner checksums. Library additionally
contains its matching core wheel and independent desktop lock. An optional standalone
peer profile has its own lock; default installation needs no native peer runtime.
The separately pinned compatible Core remains development source, not a stable
or fully frozen Core release. See [Core setup](../capability/preview.md).

For the Sovereign interface without Skulk, use the
[independent desktop profile](#select-the-independent-desktop-profile) below.
The default Library profile is the separate client for an existing Skulk Core.

## Download and authenticate

Choose the standalone core, or the optional Library with Python 3.13+:

| Role | Tag | Archive |
| --- | --- | --- |
| Standalone | `v0.26.0` | `foxlight-model-preservation-0.26.0-install.zip` |
| Optional Library | `library-v0.22.0` | `foxlight-preservation-library-0.22.0-install.zip` |

Download the archive and `release.sigstore.json` from the same chosen release.
Public asset downloads need no account. For the standalone core:

```sh
base=https://github.com/Foxlight-Foundation/foxlight-model-preservation/releases/download/v0.26.0
curl --fail --location --proto '=https' -O "$base/foxlight-model-preservation-0.26.0-install.zip"
curl --fail --location --proto '=https' -O "$base/release.sigstore.json"
```

Authenticate the archive **before extracting/executing its helper**, using a
trusted verifier and the expected repository, workflow and tag. An independently
authenticated archive hash is another bootstrap path; same-source checksums
alone do not identify a publisher.

```sh
gh attestation verify foxlight-model-preservation-0.26.0-install.zip \
  --bundle release.sigstore.json \
  --repo Foxlight-Foundation/foxlight-model-preservation \
  --signer-workflow Foxlight-Foundation/foxlight-model-preservation/.github/workflows/release.yml \
  --source-ref refs/tags/v0.26.0 --deny-self-hosted-runners
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
unzip foxlight-model-preservation-0.26.0-install.zip -d sovereign-install
cd sovereign-install
python3 install.py --directory "$HOME/sovereign-0.26.0"
"$HOME/sovereign-0.26.0/bin/model-preserve" --help
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

PirateFace lookup uses the default core in 0.16.0 and later. Offline repository
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

## Select the independent desktop profile

Library installation archives with `foxlight.software-install.v2` include
`requirements-desktop.lock` and the exactly pinned public preservation-core
wheel. Both wheels are built from the same release revision, included in the
same authenticated archive, and individually covered by the release workflow's
attestations. Authenticate that archive before using its helper. Earlier v1
Library archives do not offer this profile; they refuse it rather than silently
installing a Core-dependent client as an independent application.

With Python 3.13+ and uv, download and authenticate the matching Library archive,
then install its desktop profile into a new environment:

```sh
base=https://github.com/Foxlight-Foundation/foxlight-model-preservation/releases/download/library-v0.22.0
curl --fail --location --proto '=https' -O "$base/foxlight-preservation-library-0.22.0-install.zip"
curl --fail --location --proto '=https' -O "$base/release.sigstore.json"
gh attestation verify foxlight-preservation-library-0.22.0-install.zip \
  --bundle release.sigstore.json \
  --repo Foxlight-Foundation/foxlight-model-preservation \
  --signer-workflow Foxlight-Foundation/foxlight-model-preservation/.github/workflows/release.yml \
  --source-ref refs/tags/library-v0.22.0 --deny-self-hosted-runners
unzip foxlight-preservation-library-0.22.0-install.zip -d sovereign-desktop-install
cd sovereign-desktop-install
python3 install.py --directory "$HOME/sovereign-desktop" \
  --requirements requirements-desktop.lock
"$HOME/sovereign-desktop/bin/foxlight-sovereign"
```

This opens the independent Library without installing Skulk or the managed SDK.
First launch offers **Browse public models**, **Choose a recovery kit** and
**Choose a sharing file**, with reusable **Getting started** help. Each
model transfer or capture-key acceptance still requires its own review. See
[everyday use and privacy](sovereign-desktop.md). Closing the browser leaves the
application working; quitting the launcher stops its private owner safely.

The helper validates both component identities, their common source revision,
exact wheel hashes and the selected lock before creating an environment. It
retains both wheels as software evidence for this profile. Omitting the profile
keeps the original Core-backed Library installation and does not install the
core wheel. The standalone preservation release remains independently usable.

This installation path requires an authenticated Python/uv toolchain. It is
separate from a bundled Mac application, notarization or APT distribution; those
are not established by a Python installation archive.

## Upgrade and downgrade boundaries

Install a new software cohort into a new environment; keep the previous one and
verified recovery kits until you accept the update. Stop the existing owner
before opening its private home with another version. Do not start two writers
against one home or copy active state as a substitute for a recovery kit.

Core 0.22 / Library 0.17 adds source Activity job kinds. An earlier independent
owner can refuse that newer journal rather than ignoring or rewriting records.
An app downgrade is therefore not a supported history reader. Keep the updated
owner available; never delete or reset its journal to make old software open it.
Portable model archives and kits have separate format/version contracts and
remain recoverable with compatible standalone tools. The optional public source
key profile is preservable by earlier generic PMF readers; an older reverse
converter can need an update to interpret it. Embedded keys remain candidates,
not inherited model or registry authority.

## Retain a wheelhouse and install offline

While the dependency sources are available, retain the platform-compatible
wheels with a trusted Python/pip tool. From the extracted authenticated archive:

```sh
python3 -m pip download --only-binary=:all: --require-hashes \
  --find-links . --dest wheelhouse -r requirements.lock
```

Choose `requirements-peer.lock` for the standalone peer profile, or
`requirements-desktop.lock` for the independent Library. Use that same profile
with the offline installation command.
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
