# Install a compatible Sovereign preview

Use this path when you need a separate Core with the current preservation APIs.
Library itself installs from a public wheel, without Skulk's Python package,
Node.js or the optional managed SDK. Setting up Core is a separate explicit
installation: it downloads native prerequisites and builds the pinned public
Core source. This is a development preview, not a frozen or stable desktop
release.

## Current development cohort

For independent source following and private recovery without Skulk, use the
[desktop installation profile](../docs/installation.md#select-the-independent-desktop-profile)
and `foxlight-sovereign`. The commands below select the separate Core-backed
mode; installing the public Library does not configure Core automatically.

The independent Library 0.22 / core 0.26 profile includes Horizon backups,
public-copy review, explicit finite HTTP sharing, recipient files and reviewed
exact-copy alternatives from followed sources. It retains all original model
cards, companions, signed evidence and separate trust decisions.

The optional Core-backed preview pins verified Core revision
`2cb8c3c29ac936cc2749302a4e3a4d73aa25de86`, including complete companion
selection and attributed model descriptions. The installer retains its existing
checksum. Preview setup keeps its existing refusal and ownership checks for
older or unrecognized installations; it does not delete their saved data.

Portable handoffs, finite peer sharing, PMF, peer receiving and I2P source/receiving controls are included in
Library 0.22.0 and its immutable compatible Core pin, including persistent mirror,
retained-copy recovery and offline pending-copy checks. Install the verified
[hash-locked release archive](../docs/installation.md), then explicitly run
`foxlight-preservation-preview setup` from that environment. The installer retains
the source record and original wheel; preview setup checks those against installed
package bytes. It does not rewrite local download history into a public URL.
For a public Git source install instead:

```sh
uv tool install --python 3.13 'git+https://github.com/Foxlight-Foundation/foxlight-model-preservation.git@main#subdirectory=capability'
foxlight-preservation-preview setup
```

Git installation records its resolved immutable Library commit; setup reuses
that exact commit and the pinned compatible Core. This is a development preview.
For peer operations, use **Set up peer support** in Library to review and
explicitly install the pinned native build, then review starting contacts. No
administrator or PATH changes are needed on supported managed platforms. No native peer networking starts on launch or
compatibility checks. Source/receiving trust and restore/code permission remain
separate. See [Library peer use](README.md#receive-through-peers).

For I2P HTTP operations, configure an independently trusted local I2P proxy on
the Core host, then select **I2P only** and confirm the operation in Library.
The selected Core pin includes exact-policy source jobs and retained Resume
policy display. Setup does not install or start an I2P router. This functional
pilot does not establish anonymity or change other Core traffic settings.

## Released cohort installation

On macOS or Linux, install `uv` and Git first. macOS also needs the Xcode Command
Line Tools (`xcode-select --install`). The canonical Core installer handles its
remaining prerequisites; initial native compilation can take several minutes
and substantial disk space. Leave room for build files, models and Core's
10 GiB operating-system reserve for canonical model downloads. Internet access
is needed to obtain software. Model
recovery kits do not contain the runtime and its dependencies; retain those
separately for offline recovery.

```sh
uv tool install --python 3.13 https://github.com/Foxlight-Foundation/foxlight-model-preservation/releases/download/library-v0.22.0/foxlight_preservation_library-0.22.0-py3-none-any.whl
foxlight-preservation-preview setup
```

Setup uses a new private `~/foxlight-sovereign-preview` directory. Choose another
existing parent with `--directory /local/disk/sovereign-preview`. It refuses an
occupied directory unless you explicitly `--resume` that same matching preview.
Initial setup creates the private Model Store and records its filesystem identity.
A missing or replaced store is refused on resume and launch; it is not recreated.
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
interrupts uncertain work for explicit review. Finite sessions do not restart.
Persistent mirroring resumes only with its exact unexpired permission and prior
running intent; Stop clears that intent.

To inspect a running Core without opening Library or creating storage:

```sh
~/foxlight-sovereign-preview/bin/foxlight-preservation-library --core-url http://127.0.0.1:53415 --check
```

The check reads the actual Model Store owner and its bounded API description.
It reports Library, peer discovery, peer software setup, source discovery,
folder storage, recovery-kit, publishing/receiving, persistent-mirror and complete
public-copy review API availability, and
exits with status 2 when any group is missing. It does not qualify filesystem behavior, network reachability,
engine execution or hardware. Current packaged Skulk 2.0.2 does not provide all
these APIs. Existing compatible operators can install only Library and use its
normal `--core-url` option.

## Choose storage

In Guided setup, choose **Use this device** or **Use a folder or NAS**. In
**Storage**, choose **Add folder or NAS**, enter a path on the machine
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
On a fresh compatible Core, the header's **Restore** works before archive
setup. If only a raw archive remains, the standalone portable tools can verify
its manifest and export a kit using its public trust snapshot without the old
Core, private signing key or registry. Required companions and original
provenance are retained. Card extensions remain captured bytes; upgrading the
reader can add interpretation without rewriting the backup. Installation,
repository-code consent and registry assurance remain separate from integrity.

## PMF-capable source cohort

The released 0.6 wheel above remains available. For automatic PMF preservation
and recovery with the current source cohort, install Library from the public
repository instead:

```sh
uv tool install --force --python 3.13 'git+https://github.com/Foxlight-Foundation/foxlight-model-preservation.git@main#subdirectory=capability'
```

Then use the same `setup`, `core` and `library` commands above with a **new**
preview directory. The installer pins a reviewed PMF-capable Core revision and
its exact installer checksum. Source setup reads the installed Library's resolved
public Git commit and installs that same cohort in the preview; a later change
to `main` cannot silently replace it during setup. Installations obtained from an official release URL reuse that exact URL.
Local directory/wheel or index-only installs can use an existing Core, but
preview setup refuses them before creating a new profile. Use the public Git
command above for a reproducible source cohort. Unsupported identities never
become arbitrary installer input.

Preserve, Check, Save recovery kit and Restore use their normal reviewed jobs.
PMF kits use explicit v2 framing; old v1 kits still work. Every captured model,
companion and source record is hash-checked. Runtime profile support, registry
proof and repository-code permission remain separate from capture-key trust.
Neither installing nor launching joins a torrent swarm or starts a mirror.
Library 0.8 source/installed-component acceptance is distinct from a published
0.8 release, stable/frozen distribution or inference qualification.


The source cohort also includes **Find** and Guided source following.
No source is enrolled by setup. Get a source bootstrap file from a trusted
operator, inspect it offline, then explicitly follow and refresh. Search is local;
model details, download and Restore retain separate trust and network reviews.
See [the Library discovery flow](README.md#follow-sources-and-find-preserved-models-library-08-source-preview).
