# Verify a release

The following build-provenance commands apply to **Python package releases**.
Native Mac releases have a separate [verification workflow and predicate](macos-distribution.md#public-download-verification)
for existing Developer ID signed, notarized containers. That attestation does
not claim the native app was built by the public publication workflow.

Each release tag has a GitHub pre-release: `vX.Y.Z` for standalone preservation
and `library-vX.Y.Z` for the optional Library. Starting at `v0.12.0` and
`library-v0.11.0`, it contains the wheel, sdist, hash-locked requirements,
`INSTALL.json`, `install.py`, a single installation archive, outer `SHA256SUMS`
and a detached `release.sigstore.json` verification bundle. Library v2
installation archives additionally include the exactly pinned public core wheel
and a separate desktop dependency lock. Both components come from the same
release revision; their hashes are bound by the archive and software record. See the
[installation walkthrough](installation.md).
Releases after `v0.6.0` and `library-v0.6.0` are built by the
[release workflow](../.github/workflows/release.yml) on GitHub-hosted runners from
the tagged commit. The workflow attests each file, `SHA256SUMS` included, with
GitHub build provenance: an SLSA provenance v1 statement signed through Sigstore
and recorded in its public transparency log. Releases up to `v0.6.0` and
`library-v0.6.0` were built by hand and have `SHA256SUMS` only.

**PyPI publishing is not enabled yet.** Until this page says otherwise, a PyPI
package named like these projects is not from this repository.

## Check the checksums

In a directory holding the downloaded files and `SHA256SUMS`:

```sh
sha256sum -c SHA256SUMS        # macOS: shasum -a 256 -c SHA256SUMS
```

Every file must report `OK`. This catches corrupt or mismatched downloads. The
list comes from the same release page as the files, so on its own it does not
detect a substituted release.

## Check the build provenance

With the [GitHub CLI](https://cli.github.com/), and `TAG` set to the release tag:

```sh
for file in *.whl *.tar.gz *.lock INSTALL.json install.py *-install.zip SHA256SUMS; do
  gh attestation verify "$file" \
    --repo Foxlight-Foundation/foxlight-model-preservation \
    --signer-workflow Foxlight-Foundation/foxlight-model-preservation/.github/workflows/release.yml \
    --source-ref "refs/tags/$TAG"
done
```

These commands apply to current flat release assets downloaded individually;
older releases lack the new installation files. For the installation archive,
verify the ZIP before extracting it. Its inner checksums are authenticated by
the ZIP, and differ from the separately attested outer `SHA256SUMS`.

For each file, `gh` computes the SHA-256, fetches this repository's attestations
for that digest and checks that:

- a Sigstore certificate issued to a GitHub Actions run of `release.yml` in this
  repository signed the attestation, with a transparency-log timestamp;
- that run was for `refs/tags/$TAG`. Rehearsal runs from branches also attest
  their files; `--source-ref` rejects those;
- the statement is SLSA provenance, which records the commit, workflow and run.
  `--format json` shows them. `--source-digest COMMIT` also pins the commit when
  you have it from another channel.

The repository's attestations are also listed at
<https://github.com/Foxlight-Foundation/foxlight-model-preservation/attestations>.

### What a pass establishes

A pass binds the file's exact bytes to this repository's release workflow running
for that tag. It is not a signature by a maintainer's offline key. Trust rests on
GitHub (repository access, Actions and its OIDC issuer) and on Sigstore's public
certificate authority and log. Anyone who can push a tag or change the workflow in
this repository, including through a compromised maintainer account, can produce
a release that passes. The [trust guide](trust-and-recovery.md) asks for the
verifier from an authenticated channel; this check authenticates the channel
GitHub provides. For more, compare the digest with one obtained independently,
such as from a contact who verified it or from a kit you retained earlier.

A pass does not mean the code was reviewed, is correct or is safe, and it does not
make the build reproducible. The build fetches uv, hatchling and, for the
Library, npm packages from public registries. Dependencies you install afterwards
are not authenticated by an unconstrained installation. New installation locks
name exact accepted dependency hashes; they do not attest upstream dependency
authorship or review. Nothing here authenticates a model, trust profile or manifest ID;
model trust remains independent of software distribution.

## Keep offline evidence

New releases include `release.sigstore.json` beside the installation archive.
It is detached verification evidence for the attested subjects, not a subject
that signs itself. Save it and Sigstore's trusted roots with the verified files.
The archive, evidence and dependency wheels can be copied to independent storage;
there is no mandatory Foxlight account. You can also obtain per-digest bundles:

```sh
gh attestation download FILE --repo Foxlight-Foundation/foxlight-model-preservation
gh attestation trusted-root > trusted_root.jsonl
```

`download` writes `sha256:DIGEST.jsonl` (`sha256-DIGEST.jsonl` on Windows). Verify
later without fetching attestations or trust roots:

```sh
gh attestation verify FILE --repo Foxlight-Foundation/foxlight-model-preservation \
  --signer-workflow Foxlight-Foundation/foxlight-model-preservation/.github/workflows/release.yml \
  --source-ref "refs/tags/$TAG" \
  --bundle sha256:DIGEST.jsonl --custom-trusted-root trusted_root.jsonl
```

## PyPI attestations (once enabled)

When enabled, the same workflow run uploads the same wheel and sdist to PyPI
through Trusted Publishing, without a stored token, and with PEP 740 attestations
signed for the publishing job. PyPI checks them on upload and shows them on each
file's page. `SHA256SUMS` from the GitHub release also matches the PyPI files.
To check a PyPI file yourself:

```sh
uvx pypi-attestations verify pypi \
  --repository https://github.com/Foxlight-Foundation/foxlight-model-preservation pypi:FILE
```

This binds the upload to this repository's workflow, with the same limits as above.

## Install verified software

Use the [installation archive walkthrough](installation.md) for a new environment
with exact dependency versions/hashes and optional offline wheelhouse. Installation
starts no Core, service, native runtime or model transfer. The standalone package
requires Python 3.11+; optional Library requires 3.13+. Core setup is a separate
explicit development-preview operation.

A simple wheel installation remains available:

```sh
uv tool install ./FILE.whl                  # Library: add --python 3.13
```

That resolves dependencies from your index. It does not reproduce the release's
hash-locked runtime unless you deliberately apply its lock. Never select the
managed SDK extra as a substitute for the separately authorized SDK distribution.
Retain platform-compatible dependency wheels, Python/uv and verification evidence
for offline recovery. See [trust and recovery](trust-and-recovery.md).
