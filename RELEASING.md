# Releasing

Release Python packages by pushing a tag. [release.yml](.github/workflows/release.yml) builds and
checks the files, attests them, creates the GitHub pre-release and, once enabled,
publishes to PyPI. Do not upload Python release files by hand: hand-built files
carry no build provenance. Users verify releases as described in [docs/releases.md](docs/releases.md).
The separate [Mac candidate recipe](docs/macos-distribution.md) pins these
published inputs and records a distinct native-shell revision. Its signing and
notarization step creates candidates only. The separate reviewed
[native publication lane](#publish-an-existing-signed-mac-candidate) verifies
existing final bytes before public release. Its custom attestation must not be
called native build provenance or a GitHub-built Python release.

## Publish an existing signed Mac candidate

[`macos-release.yml`](.github/workflows/macos-release.yml) accepts only the final
DMG/ZIP named and SHA-256 pinned in
[`0.1.0-build7.json`](desktop/macos/releases/0.1.0-build7.json). That reviewed
record separates binary source, published public Python inputs, Developer ID
certificate, complete app CodeDirectory and Apple submission identities. No
signing key, Keychain prompt or Apple credential enters this workflow. Changing
the files requires a new reviewed manifest and distinct release identity.

After this lane is reviewed and merged:

1. Create the record's tag at the reviewed publication commit on `main`; never
   replace a published tag. The binary source remains the manifest's older exact
   source; the tag names the verification workflow and recipe, not a rebuild.
2. Prepare release notes stating the exact supported architecture, verification
   scope/limits, installation steps and verification commands from
   [Mac distribution](docs/macos-distribution.md#public-download-verification).
   Create a **draft** pre-release at that existing tag. Upload **only** the two
   final container files whose hashes match the reviewed manifest. This is
   untrusted staging for verification, never a manual public release. Do not
   upload local raw logs, private producer details, credentials or inventories.
3. Dispatch `macos-release.yml` **at the tag**, `mode=rehearse`, confirmation
   `verify Sovereign 0.1.0 build 7`. Intake checks a closed two-file inventory and
   reviewed hashes. A fresh Apple silicon runner validates both containers,
   Apple Developer ID certificate, complete app CodeDirectory, stapled tickets,
   Gatekeeper and signed build receipts before running actual offline CLI PMF
   recovery/tamper checks. It mounts the DMG read-only and always detaches it.
4. A separate job with OIDC attests the final files, public manifest, verification
   predicate and checksums using the custom Mac verification predicate. It runs
   no downloaded app or build code. Rehearsal creates public verification
   attestations but leaves the draft private. Inspect that exact run's result.
5. Dispatch the same tag with `mode=publish` and the same confirmation. It repeats
   verification, checks every attested subject against the exact workflow, tag
   and verifier commit, re-downloads the draft to confirm unchanged bytes, adds
   public evidence without replacing assets, then publishes the pre-release.
   Only this final job publishes. If it fails after uploading evidence, resume
   that **failed publish job** with the same run's artifacts; a complete new run
   intentionally refuses a draft that already has extra files. Never clobber
   assets or move a tag to get a failed check to pass.
6. Download anonymously and verify the public files and retained attestation
   bundle again. Check actual installation separately from workflow acceptance.

This lane asserts verification of previously signed bytes, **not a native build
in the public workflow**. Apple checks and synthetic recovery do not establish
clean-host acceptance, full real-model preservation, inference qualification,
all supported OS versions or public-network privacy. Model/source/registry trust
is independent and unchanged. Draft download needs GitHub contents write
permission; it is confined to intake and publication jobs, neither of which
executes the incoming app. The Mac verification job has contents read only.

| Tag | Project | Version | Changelog |
| --- | --- | --- | --- |
| `vX.Y.Z` | Standalone preservation | `pyproject.toml` | `CHANGELOG.md` |
| `library-vX.Y.Z` | Library | `capability/pyproject.toml` | `capability/CHANGELOG.md` |

The tag must be the prefix plus the project's `version`, exactly (`v0.11.0` for
`0.11.0`); otherwise the run fails before building. A tag push runs the workflow
file at the tagged commit, so only commits that contain `release.yml` release this
way.

The workflow runs four jobs. **Build** checks the tag against the version,
rebuilds the Library interface and requires it to match the committed `static/`
(Library only), runs `uv build`, checks the built name/version, exports public runtime hash locks from committed
project locks, and builds installation records/helpers and one ZIP. Library releases also build the exactly pinned core wheel from the same
checkout and include it, a separate desktop lock and v2 component bindings in the
Library archive. Default Library installs keep the original Core client; the
desktop profile installs both public wheels. No private SDK/source requirement
is allowed in either public lock. It writes outer
`SHA256SUMS`; the archive retains its own inner inventory. **Provenance** attests the distributions, locks, installer, record, archive and
outer `SHA256SUMS`, and retains the detached verification bundle; it runs
no build code and, with **PyPI**, is the only job that can request an OIDC token.
**GitHub release**
verifies the retained bundle against the exact tag/source/workflow before it
creates the pre-release with all installation assets and that bundle. **PyPI**
uploads the wheel and sdist when enabled. Each later job re-checks the files
against `SHA256SUMS` and the digest the build job recorded.

## Cut a core release

1. On a branch, set `version` in `pyproject.toml` and run `uv lock`. The lock
   records the project version; `uv sync --locked` fails until it is updated.
2. In `CHANGELOG.md`, move the `Unreleased` entries under `## X.Y.Z`.
3. Build/generate and qualify both the normal and offline hash-locked install
   outside the checkout. Preserve the default SDK/Skulk-free gate, installer
   refusal cases and exact archive/source evidence. Open a ready pull request
   and merge it once review threads and CI pass.
4. Tag the merged commit on `main` and push only that tag:

   ```sh
   git switch main && git pull --ff-only
   git tag -a vX.Y.Z -m "Core X.Y.Z"
   git push origin vX.Y.Z
   ```

5. Watch the **Release** run under Actions. You can edit the release title and
   prose afterwards; do not replace its files.

## Cut a Library release

1. On a branch, set the same version in `capability/pyproject.toml` and
   `BUNDLE_VERSION` in `capability/src/foxlight_preservation_library/contracts.py`
   (a test keeps them equal). Then run `uv lock --project capability`, and
   `npm version X.Y.Z --no-git-tag-version` in `capability/web`.
2. If anything under `capability/web` changed, rebuild and commit the interface:
   `npm ci && npm run build` in `capability/web` (Node 24, as CI uses). The
   release fails if the committed bundle differs from a clean build.
3. In `capability/CHANGELOG.md`, drop "(source preview)" from the released
   heading. Point the install commands and "latest tagged release" text in
   `capability/README.md` and `capability/preview.md` at the new wheel.
4. Merge, then tag and push as for the core, with `library-vX.Y.Z`.

## Rehearse

Once `release.yml` is on `main`, rehearse any branch before tagging:

```sh
gh workflow run release.yml --ref BRANCH -f tag=vX.Y.Z -f dry_run=true
gh run watch
```

The **Run workflow** button under Actions → Release does the same. A rehearsal
runs **Build** and **Provenance** on the selected commit: the tag/version match,
the Library bundle rebuild, `uv build`, the metadata check, `SHA256SUMS` and a
real attestation. Download the `distributions` artifact from the run to inspect
the files. It never creates a release or uploads to PyPI, and `dry_run: false` is
refused. Its attestations are public and name the branch, not a tag, so the
documented `--source-ref refs/tags/...` check does not accept them.

## When a run fails

Re-run failed jobs from the run page; each job re-checks the files first. If the
run failed before anything was published, you may delete the tag
(`git push origin :refs/tags/vX.Y.Z`), fix `main` and tag again. Once a GitHub
release or PyPI upload exists, never move its tag: release a new version. PyPI
never accepts a replacement for an uploaded file name.

## One-time PyPI setup

PyPI publishing stays off until the repository variable `PUBLISH_PYPI` is `true`.

1. On pypi.org, under Your account → Publishing, add a pending GitHub publisher
   for each project. Use each `[project] name` as it stands when you enable
   publishing (core: `pyproject.toml`; Library: `capability/pyproject.toml`):
   - PyPI project name: that name
   - Owner: `Foxlight-Foundation`
   - Repository name: `foxlight-model-preservation`
   - Workflow name: `release.yml`
   - Environment name: `pypi`

   A pending publisher does not reserve the name; the first upload creates the
   project.
2. In the repository's Settings → Environments, create `pypi`. Add required
   reviewers so each upload waits for approval (leave "Prevent self-review" off
   if you are the only reviewer), and limit deployment branches and tags to the
   tag patterns `v*` and `library-v*`.
3. In Settings → Secrets and variables → Actions → Variables, add the repository
   variable `PUBLISH_PYPI` with the value `true`.
4. The next tag push uploads after approval. Earlier releases stay GitHub-only.
   Update the PyPI statements in `README.md` and `docs/releases.md`.

Before the Library's first upload: its `managed` extra requires
`skulk-capability-sdk==0.5.0`, which is not on PyPI. Anyone could register that
name, and installing the Library with `[managed]` from PyPI would then install
their package. Hold the name on PyPI or remove the extra first. Installing the
GitHub release wheel with `[managed]` has the same exposure today.

## Optional repository hardening

- A tag ruleset for `v*` and `library-v*` that limits creation to maintainers and
  blocks updates and deletion. Deleting a failed tag then needs a bypass.
- Release immutability, which locks published assets and tags and adds GitHub's
  own release attestation (`gh release verify-asset`). The workflow publishes
  through `gh release create`, which uploads to a draft before publishing.

## Qualify the independent Library installation

Build both projects from the same reviewed revision, then give the Library
asset builder its matching core wheel:

```sh
uv build --no-sources --wheel --out-dir dist-core
uv build --no-sources --project capability --out-dir dist-library
python scripts/release_install.py --project capability --dist dist-library \
  --revision FULL_REVIEWED_COMMIT --desktop-core-wheel dist-core/CORE.whl
python dist-library/install.py --directory /absolute/new-sovereign \
  --requirements requirements-desktop.lock
/absolute/new-sovereign/bin/python -I scripts/qualify_installed_desktop.py
```

Repeat using a retained wheelhouse and `--offline`; the qualifier permits only
its owned loopback surface and refuses external connections/lookups. It checks
the installed source/component receipt, versions, absence of Skulk/SDK, real
local screen/search and foreign-Origin refusal. This is installation/local-host
evidence, not full native UI, model recovery or public-network qualification.
Keep those separately scoped acceptances. The workflow includes and attests the
component wheel; PyPI selection uploads only the project's primary wheel/sdist,
never the other project's component. Public PyPI remains separately disabled.
