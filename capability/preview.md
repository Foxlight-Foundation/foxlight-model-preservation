# Install a compatible Sovereign preview

Use this path when you need a separate Core with the current preservation APIs.
Library itself installs from a public wheel, without Skulk's Python package,
Node.js or the optional managed SDK. Setting up Core is a separate explicit
installation: it downloads native prerequisites and builds the pinned public
Core source. This is a development preview, not a frozen or stable desktop
release.

## Install

On macOS or Linux, install `uv` and Git first. macOS also needs the Xcode Command
Line Tools (`xcode-select --install`). The canonical Core installer handles its
remaining prerequisites; initial native compilation can take several minutes
and substantial disk space. Leave room for build files, models and Core's
10 GiB operating-system reserve for canonical model downloads. Internet access
is needed to obtain software. Model
recovery kits do not contain the runtime and its dependencies; retain those
separately for offline recovery.

```sh
uv tool install --python 3.13 https://github.com/Foxlight-Foundation/foxlight-model-preservation/releases/download/library-v0.6.0/foxlight_preservation_library-0.6.0-py3-none-any.whl
foxlight-preservation-preview setup
```

Setup uses a new private `~/foxlight-sovereign-preview` directory. Choose another
existing parent with `--directory /local/disk/sovereign-preview`. It refuses an
occupied directory unless you explicitly `--resume` that same matching preview.
A resume can refresh its private Library tool installation; all Core and Library
launches for that profile must be stopped first.
It retains interrupted files for inspection and never adopts another existing
installation. The Core installer is downloaded from an immutable revision and
checked against the embedded SHA-256 before execution. The installed checkout's
revision is checked afterward. Setup and Core launch also validate the ignored
runtime configuration: the Model Store and staging must still point inside the
same profile, with its local host and port and no foreign per-node override.
Ordinary YAML saved by Core can retain other settings. A changed storage path is
refused before launch; restore its original profile paths or choose a new preview. Setup and preview launches share kernel-held locks:
resuming setup refuses while that preview Core or Library is running, and only
one preview Core can launch. Complete installer output stays in the private
`installation.log`; the final commands use the isolated profile.

Core source, state, Model Store, staging/search paths, worker cache, environment
and Library tools use separate paths beneath this preview. Its cluster namespace is separate from your
existing cluster. Setup does not start either runtime or migrate installed models
and pairing. Your existing compatible Core can use Library directly; upgrading
an existing operator installation remains its normal Core upgrade procedure.
The preview does not search the normal shared staging directory for existing models.
Do not point a second writer at a running Model Store.

The current Core revision is pinned in
[`preview.py`](src/foxlight_preservation_library/preview.py); a version number
alone is not proof that these APIs are available. The default setup is headless,
since Library already contains its own interface. Add `--with-dashboard` if you
also want to build Core's dashboard.

## Start and check

After installation, setup prints exact launch commands. With the default path,
run Core in one terminal:

```sh
~/foxlight-sovereign-preview/bin/foxlight-preservation-preview core
```

Then open Library in another terminal:

```sh
~/foxlight-sovereign-preview/bin/foxlight-preservation-preview library
```

Pass the same `--directory` to both commands if you chose a custom directory.
To recover without a registry connection after software is installed, start Core
with `SKULK_OFFLINE=true` before the same foreground Core command. Runtime
software and dependencies must already be present.

Core uses API port 53415 and Model Store port 13415; its libp2p port is assigned
by the OS. A conflicting service is not replaced. Stop each foreground command
with Ctrl+C. Closing Library does not stop jobs accepted by Core. Stopping Core
interrupts uncertain work for explicit review and never restarts sharing.

To inspect a running Core without opening Library or creating storage:

```sh
~/foxlight-sovereign-preview/bin/foxlight-preservation-library --core-url http://127.0.0.1:53415 --check
```

The check reads the actual Model Store owner and its bounded API description.
It reports Library, folder storage, recovery-kit and publishing/receiving API
availability. It does not qualify filesystem behavior, network reachability,
engine execution or hardware. Current packaged Skulk 2.0.2 does not provide all
these APIs. Existing compatible operators can install only Library and use its
normal `--core-url` option.

## Choose storage

In Guided setup, choose **Use this device** or **Use folder or NAS**. In
**Storage & keys**, choose **Add folder or NAS**, enter a path on the machine
running Core and a storage name, then choose **Review storage**. The review
shows the selected folder, observed free space and whether one private folder
will be created. Confirm **Use this storage** to enroll it for new Preserve
reviews. This copies no models and starts no sharing.

The browser cannot select a folder on a different machine using a file upload
picker. Mount a NAS or remote filesystem through your operating system first.
Choose an empty owner-only folder, or a new single folder directly beneath an
existing connected parent. Setup never creates missing mount paths, configures
network credentials or changes permissions on a shared NAS root. Core state,
Model Store and custom-card folders cannot become backup destinations. Direct
cloud-bucket APIs are separate from an already mounted filesystem.

Library retains the chosen default across restarts. **Use for new copies**
changes subsequent Preserve reviews; existing copies, plans and jobs keep their
original destination. A disconnected default blocks new copies without falling
back to the system disk. A copy on another connected destination remains usable.
Reconnect the exact enrolled root when it disappears or changes identity.

If setup was interrupted after enrollment but before its public key snapshot
was written, review the same enrolled folder and confirm again. Unregistered
populated folders are kept and refused. An empty crash-created leaf can be
explicitly reviewed again. Up to sixteen private destinations can be enrolled.
Core runs mounted-storage metadata in a bounded background I/O lane. A slow
availability check reports unavailable or still checking without queueing repeated
probes or blocking the node event loop. Setup cancellation and shutdown retain
ownership until actual filesystem calls and directory closes finish; a stalled
operating-system mount can delay shutdown. Specific NAS/provider and mount
behavior still need qualification with that operating system and configuration.

## Retain recovery evidence

Private means private audience, not encryption. Protect storage permissions,
credentials and encryption independently. Capture signing keys stay in protected
Core storage; an external archive retains only `archive-trust.json`, containing
public verification keys. Keep an independent record of its fingerprint and the
exact manifest identity. Replacing both a bundle and its bundled keys can replace
an unanchored identity.

Save a `.foxkit` through Library to storage independent of the original Core.
On a fresh compatible Core, **Restore from recovery kit** works before archive
setup. If only a raw archive remains, the standalone portable tools can verify
its manifest and export a kit using its public trust snapshot without the old
Core, private signing key or registry. Required companions and original
provenance are retained. Card extensions remain captured bytes; upgrading the
reader can add interpretation without rewriting the backup. Installation,
repository-code consent and registry assurance remain separate from integrity.
