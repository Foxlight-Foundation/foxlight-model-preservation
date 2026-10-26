# Self-contained macOS candidate

The public Mac recipe builds Sovereign for Apple silicon, macOS 13 or newer.
The app bundles Python and the independent Library, including populated model
discovery, private preservation and recovery. Recipients do not install Python,
uv, Skulk, a managed SDK or a source checkout. It starts no sharing, router or
automatic model download. Linux/APT distribution is a separate delivery.

The build recipe and its CI produce an **ad hoc candidate**. The separate
[native release workflow](../.github/workflows/macos-release.yml) can verify and
publish reviewed Developer ID signed, notarized final containers without
rebuilding or signing them. Developer ID signing, Apple notarization and an
installed-app journey remain separate checks. A locally passing Gatekeeper
check does not establish clean-host acceptance, model compatibility or anonymity.

## Public download verification

Native releases use a separate `sovereign-macos-vVERSION-buildN` pre-release.
The workflow is explicit: a reviewed recipe alone does not mean its release has
been published. Download the DMG or ZIP and the neighboring `SHA256SUMS`,
`native-release.json`, `verification.json` and
`native-verification.sigstore.json` from the public GitHub release. Check the
download against its checksum, then authenticate it with GitHub CLI:

```sh
TAG=sovereign-macos-v0.1.0-build2
FILE=Sovereign-0.1.0-macos-arm64.dmg
shasum -a 256 "$FILE"  # compare the corresponding line in SHA256SUMS
gh attestation verify "$FILE" \
  --repo Foxlight-Foundation/foxlight-model-preservation \
  --signer-workflow Foxlight-Foundation/foxlight-model-preservation/.github/workflows/macos-release.yml \
  --source-ref "refs/tags/$TAG" \
  --predicate-type https://foxlight.ai/attestations/macos-verification/v1 \
  --bundle native-verification.sigstore.json --deny-self-hosted-runners
```

Repeat for the ZIP if you choose it, and for the manifest, predicate and checksum
file. Use `--source-digest VERIFIED_PUBLICATION_COMMIT` to pin the verifier
revision independently. This differs from `native-release.json`'s older
`source_revision`, which identifies the already-built app. This custom predicate
asserts **verification of existing files**, not native build provenance. It
records the exact host, performed checks and qualification limits; inspect those
claims along with the [software trust limits](releases.md#what-a-pass-establishes).
Retain the bundle and trusted roots for [offline verification](releases.md#keep-offline-evidence).

For Apple silicon, open the verified DMG and drag **Sovereign** to
**Applications**, then open it normally. The ZIP contains the same signed app.
Apple checks its Developer ID and notarization. First launch opens the independent
Library with public model listings. Search, review a model's complete storage
requirements, and choose preservation; no model downloads or sharing begin merely
by opening the app. A retained recovery kit can be opened and restored without
Skulk or the original registry. A new capture key still requires explicit trust
acceptance; software signing never grants that model authority.

The build 2 manifest pins the final DMG/ZIP, Developer ID team `ZP9J3449GF` and
certificate fingerprint `A148825F0E5B1F0F29C7A314129AB6D090EF9FF5`.
Its declared minimum is macOS 13; actual GUI evidence is macOS 26.6.2, with
synthetic offline CLI coverage on macOS 15 and 26.6.2. Full OS-range, clean-host,
real-model and public-network privacy qualification are separate open gates.

## Build without a signing key

Use a native Apple silicon Mac with Swift, Python 3.13 and uv. Read
[`desktop/macos/inputs.json`](../desktop/macos/inputs.json). It pins the
published Library archive, its exact component source, and the Astral portable
CPython archive with SHA-256 digests. The published Library archive has its own
[GitHub provenance verification](releases.md); verify that before using its
installation helper. Fetch both pinned URLs and compare their digests before
extracting. Keep those bytes and the public dependency wheelhouse offline.

After verifying and extracting the Library archive into `software`, prepare the
wheelhouse on the target architecture:

```sh
python -m pip download --only-binary=:all: --require-hashes \
  --find-links software --dest wheelhouse \
  -r software/requirements-desktop.lock
python scripts/build_macos.py --library-archive /absolute/library.zip \
  --python-archive /absolute/python.tar.gz --wheelhouse /absolute/wheelhouse \
  --directory /absolute/new-candidate
```

The build verifies the archive pins before creating its output. It extracts
confined runtime files, installs the hash-locked public dependencies without an
index or cache, compiles a release Swift shell and bundles both public wheels.
It neither downloads a Python runtime nor executes models. The embedded
`Contents/Resources/build-receipt.json` records the **actual shell/build source**
separately from the published core/Library source, their input wheel hashes,
portable-runtime file hashes and the complete input lock. Dirty local builds
are labeled and cannot enter the signing step. Rebuilding the same recipe
reproduces the inputs and behavior; compiler output, timestamped signatures and
notarization are not claimed to be byte-identical.

The application also retains Python's own license, Python-wheel license
directories and `Resources/runtime-notices/`. The latter contains upstream
dependency notices omitted from the small runtime archive, copied from the
matching hash-verified full Python build. `SOURCES.json` records that archive and
every notice digest; the builder checks and retains this inventory. Some full
build notices cover components not used by this application.

## Bundled commands

The app includes two commands backed by its own Python and public packages:

```sh
"/Applications/Sovereign.app/Contents/Resources/bin/model-preserve" --help
"/Applications/Sovereign.app/Contents/Resources/bin/foxlight-sovereign" --help
```

`model-preserve` provides the standalone preservation CLI. `foxlight-sovereign`
opens the independent local Library host, with `--home` and `--no-browser`
available for operators. Neither needs system Python or Skulk. Their launchers
follow relative/absolute command symlinks and remain valid when the app moves or
is renamed, including paths with spaces or Unicode. Installer-generated console
paths inside `Resources/site-packages/bin` point to the same supported commands;
unrelated optional/developer command stubs are omitted from this native bundle.
The original Python distributions keep their normal entry points.

Scripts live under Resources so the app's resource seal covers them, following
[Apple's bundle signing guidance](https://developer.apple.com/library/archive/technotes/tn2206/).
The launchers use isolated Python, ignore incoming Python paths/venv launchers
and disable bytecode writes into the signed app. They do not install a global
command, start sharing or change model/source trust.

For a built candidate, run this installed-command check before signing:

```sh
python scripts/qualify_macos_cli.py --app /absolute/new-candidate/Sovereign.app \
  --directory /absolute/new-cli-qualification
```

It copies/relocates the app and runs its real commands with network access denied
by a child-process sandbox. A public synthetic PMF vector restores its model,
required companion, notices, opaque card and original signed envelope; corrupt
bytes refuse a new restoration. Incoming Python configuration and command
symlinks are exercised, and the resource seal must still validate afterward.
This checks the bundled CLI mechanics, not clean-host, full real-model or public
network qualification.

## Sign a reviewed candidate

Merge/review the source and qualify an app built from that clean exact revision.
Keep private keys in the existing Keychain. Only an explicitly authorized release
operator runs the signing step:

```sh
python scripts/release_macos.py --app /absolute/new-candidate/Sovereign.app \
  --directory /absolute/new-release --expected-source FULL_SOURCE_COMMIT \
  --identity DEVELOPER_ID_IDENTITY --team APPLE_TEAM_ID \
  --notary-profile EXISTING_KEYCHAIN_PROFILE
```

It refuses a dirty/mismatched checkout, a changed recipe or an app with external
symlinks. It copies the candidate, signs nested Mach-O code explicitly using
hardened runtime and no permissive entitlements, verifies the expected Developer
ID team, and signs the app. It requires Apple to accept the submission, staples
the app and checks Gatekeeper. It then creates a ZIP and a drag-to-Applications
DMG, signs/notarizes/staples the DMG and checks its Gatekeeper policy. Hashes are
written **after** final stapling. Submission results and `release.json` retain the
exact source, inputs, artifacts and Apple submission identities.

The script never publishes. Failed staging is retained for diagnosis; rerun into
a new directory. It never modifies user model storage or the unsigned candidate.
Signing applies only to software; it grants no model, registry or source trust.

## Verify and qualify the final containers

Check the downloaded DMG/ZIP against the published final `SHA256SUMS`, mount the
DMG, drag the app into a fresh writable directory, and check:

```sh
codesign --verify --deep --strict /absolute/Sovereign.app
codesign --display --verbose=4 /absolute/Sovereign.app
xcrun stapler validate /absolute/Sovereign.app
spctl --assess --type execute --verbose=4 /absolute/Sovereign.app
```

Check the Developer ID team independently. The Apple signature covers the
embedded build receipt and app resources. Apple notarization is a malware/trust
distribution check; it does not prove a reproducible build. It is distinct from
the GitHub attestations on the pinned public Python inputs. Do not describe a
locally signed app as a GitHub-built artifact.

Run the actual installed application with no checkout/system Python/Skulk:
first launch and public discovery, search and a supported preservation review;
save/open a complete recovery kit through native dialogs; restore while the
registry/original source is unavailable; quit/restart and confirm Library and
Activity persist. Compare original card extensions, companions, notices and
signed evidence. Verify resource signatures again after those actions, including
relocation. A synthetic kit validates recovery mechanics only. Qualify real-model
transfers and clean-host/OS coverage separately and publish the precise scope.
