# Releasing

Release by pushing a tag. [release.yml](.github/workflows/release.yml) builds and
checks the files, attests them, creates the GitHub pre-release and, once enabled,
publishes to PyPI. Do not upload release files by hand: hand-built files carry no
provenance. Users verify releases as described in [docs/releases.md](docs/releases.md).

| Tag | Project | Version | Changelog |
| --- | --- | --- | --- |
| `vX.Y.Z` | Core | `pyproject.toml` | `CHANGELOG.md` |
| `library-vX.Y.Z` | Library | `capability/pyproject.toml` | `capability/CHANGELOG.md` |

The tag must be the prefix plus the project's `version`, exactly (`v0.11.0` for
`0.11.0`); otherwise the run fails before building. A tag push runs the workflow
file at the tagged commit, so only commits that contain `release.yml` release this
way.

The workflow runs four jobs. **Build** checks the tag against the version,
rebuilds the Library interface and requires it to match the committed `static/`
(Library only), runs `uv build`, checks the built name and version and writes
`SHA256SUMS`. **Provenance** attests the wheel, sdist and `SHA256SUMS`; it runs
no build code and, with **PyPI**, is the only job that can request an OIDC token.
**GitHub release**
creates the pre-release with generated notes and a verification block. **PyPI**
uploads the wheel and sdist when enabled. Each later job re-checks the files
against `SHA256SUMS` and the digest the build job recorded.

## Cut a core release

1. On a branch, set `version` in `pyproject.toml` and run `uv lock`. The lock
   records the project version; `uv sync --locked` fails until it is updated.
2. In `CHANGELOG.md`, move the `Unreleased` entries under `## X.Y.Z`.
3. Open a ready pull request and merge it once CI passes.
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
