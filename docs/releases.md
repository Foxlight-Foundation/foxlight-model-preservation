# Verify a release

Each release tag has a GitHub pre-release holding a wheel, an sdist and
`SHA256SUMS`: `vX.Y.Z` for the core and `library-vX.Y.Z` for the Library.
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
for file in *.whl *.tar.gz SHA256SUMS; do
  gh attestation verify "$file" \
    --repo Foxlight-Foundation/foxlight-model-preservation \
    --signer-workflow Foxlight-Foundation/foxlight-model-preservation/.github/workflows/release.yml \
    --source-ref "refs/tags/$TAG"
done
```

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
are not covered. Nothing here authenticates a model, trust profile or manifest ID;
model trust remains independent of software distribution.

## Keep offline evidence

Save the attestation bundle and Sigstore's trusted roots with the verified files,
for example in a recovery kit:

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

## Install a verified wheel

Install the file you checked, not a fresh download. The core needs Python 3.11+;
the Library needs 3.13+:

```sh
uv tool install ./FILE.whl                  # Library: uv tool install --python 3.13 ./FILE.whl
python -m pip install ./FILE.whl            # alternative
```

Both resolve dependencies from your package index, outside these checks. To
install the locked, hash-checked dependency set that CI tests, export it from the
verified sdist:

```sh
tar -xzf NAME-VERSION.tar.gz
uv export --project NAME-VERSION --locked --no-dev --no-emit-project \
  --format requirements-txt -o requirements.txt
python -m pip install --require-hashes -r requirements.txt
python -m pip install --no-deps ./FILE.whl
```

Retain those dependency wheels as well for offline recovery; see
[trust and recovery](trust-and-recovery.md).
