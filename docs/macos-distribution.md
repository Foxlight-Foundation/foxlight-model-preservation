# Self-contained macOS candidate

The public Mac recipe builds Sovereign for Apple silicon, macOS 13 or newer.
The app bundles Python and the independent Library, including populated model
discovery, private preservation and recovery. Recipients do not install Python,
uv, Skulk, a managed SDK or a source checkout. It starts no sharing, router or
automatic model download. Linux/APT distribution is a separate delivery.

This recipe and its CI produce an **ad hoc candidate**, not an automatically
published notarized download. Developer ID signing, Apple notarization and an
installed-app journey are separate checks. A locally passing Gatekeeper check
does not establish clean-host acceptance, model compatibility or anonymity.

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
