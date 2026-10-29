# Sovereign for Linux (APT)

The public Linux recipe builds `foxlight-sovereign` Debian packages for native
amd64 and arm64 runners. The package contains the same independent Library and
preservation core as the public Python installation, a pinned portable CPython
3.13 runtime, a GTK/WebKit window, desktop entry and the approved Sovereign icon.
It requires no Skulk Core, capability SDK, account or GPU.

The native recipe produces reproducible Debian packages. Public packages are
published by the reviewed `linux-release.yml` workflow at
`sovereign-linux-v0.1.0-3`, with exact source/input receipts, qualification evidence,
checksums and detached GitHub build provenance. The signed APT repository is a
separate publication step; CI artifacts alone are not that channel.

The current published package and signed apt channel are **0.1.0-3**, pairing
core 0.26 / Library 0.22 with first-launch choices, Getting started help,
working-space reviews, separate kit/public-mirror sharing and recovery instructions.
[Fresh public apt qualification](https://github.com/Foxlight-Foundation/foxlight-model-preservation/actions/runs/40722090639)
passed on native Ubuntu 24.04 amd64 and arm64, including offline GUI recovery and
restart. These installed-app checks are separate from package build provenance.

## Requirements and tested systems

Setup checks capabilities, not distribution names or release numbers. Compatible
APT-based desktops and derivatives can install without an OS-version allowlist.
Testing a release provides evidence; an untested release is not automatically
incompatible. Ubuntu 24.04 amd64/arm64 remains the native GUI test baseline.

The current app package requires amd64 or arm64, Python 3.11+, AppArmor 4.0+,
libc6 2.35+, GTK 3, WebKitGTK 4.1, Python GI, xdg-utils, zenity and the remaining
dependencies declared in the package. APT resolves those dependencies and refuses
an unsatisfiable installation before running package scripts. Setup does not
force installation, remove packages to satisfy dependencies, or disable the
WebKit sandbox or OS protections. The graphical setup also needs a desktop
polkit authentication agent. Its own package requires Python 3.11+, GTK 3,
Python GI, librsvg, pkexec and CA certificates.

The preliminary check detects Linux, package tools and processor architecture;
it is not a promise that all app dependencies are available. Read APT's error
if installation stops. Systems without APT need a different native package;
the [standalone Python tools and browser Library](getting-started.md) remain
independent of Skulk. This `.deb` installer does not provide RPM or AppImage
support.

## Graphical setup (recommended)

On a compatible **Linux desktop using APT**, download the small
[**Install Sovereign (.deb)**](https://github.com/Foxlight-Foundation/foxlight-model-preservation/releases/download/sovereign-graphical-setup-v0.1.3/foxlight-sovereign-setup_0.1.3-1_all.deb).
The same file supports 64-bit Intel/AMD and ARM computers. No terminal commands,
Skulk, account or GPU are required.

1. Open the downloaded `.deb` with your desktop’s package installer and choose
   **Install**. On Ubuntu, use **App Center**; another distribution may use a
   different application. If needed, right-click and choose **Open With**.
2. Open **Install Sovereign** from Applications. Read the review, then click
   **Install Sovereign**. Setup verifies the pinned app and software key first.
3. Enter the password for the **administrator account named in your system’s permission window**.
   Sovereign does not collect it. Wait for **Sovereign is ready**, then choose
   **Open Sovereign**. Later, open Sovereign directly from Applications.
4. Choose **Browse public models**, **Open recovery kit**, or **Open sharing file
   or source**. [Getting started](getting-started.md) explains those choices.

Setup uses the Sovereign Horizon starry-night backdrop, palette, approved
wordmark and a clear review-to-install-to-open flow. The bundled background
works offline without extra image libraries; a dark overlay and dark panels
keep text readable. The main action stays outside the scrolling
content, including when details are expanded or desktop text is enlarged.
Launching with a native high-contrast theme retains your desktop’s colors and a
readable icon/text identity instead of the dark wordmark. The theme is local;
no fonts or artwork are fetched during setup.

Setup detects your processor and checks the Linux/APT requirements. Its native window
stays unprivileged; only a fixed package-owned helper runs through the system’s
normal administrator authorization. Its named, helper-scoped OS action requires
administrator authentication each time, without a retained authorization grant. It reuses the guided installer's exact key,
package pins, verified root-owned staging and scoped APT signature/expiry checks.
It does not replace customized source/key files, grant model trust, or start
sharing, a service or a model download. Cancel before permission changes no system
settings; declining the system’s permission window also stops installation. Once
installation starts, let APT finish. **Installation details** explains failures,
and **Try again** repeats verification.

App updates arrive through the system’s normal signed software updates. The separate
setup utility may be removed through your package installer after installation; this retains
Sovereign, its update source, your Library, keys, models and recovery files.
Installing the setup package alone makes no source/key changes and runs no setup
or app automatically. The app remains the existing **0.1.0-3** package.

**Initial download trust:** a downloaded `.deb` can install software as administrator.
App Center's prompt does **not** independently authenticate Foxlight as publisher.
Use the official HTTPS release linked above. For independent verification before
opening it, download `SHA256SUMS` and `linux-graphical-setup.sigstore.json` alongside
the package and use GitHub CLI:

```sh
sha256sum --ignore-missing --check SHA256SUMS
gh attestation verify foxlight-sovereign-setup_0.1.3-1_all.deb \
  --repo Foxlight-Foundation/foxlight-model-preservation \
  --bundle linux-graphical-setup.sigstore.json \
  --signer-workflow Foxlight-Foundation/foxlight-model-preservation/.github/workflows/linux-graphical-setup-release.yml \
  --source-ref refs/tags/sovereign-graphical-setup-v0.1.3 --deny-self-hosted-runners
```

That optional advanced verification uses a terminal; the graphical installation
itself does not. Setup does not claim to authenticate its own initially downloaded
bytes. Software build provenance, repository signatures, and model provenance
remain distinct. The independently hash-pinned terminal route below is retained.

Native amd64/arm64 qualification exercises installed setup, real Ubuntu permission
cancellation/password authentication and the **Open Sovereign** handoff, plus
high-contrast and enlarged-text review/cancellation with a reachable main action. The same
setup package bytes reproduce on both architectures. This is not an observed
participant study or a complete App Center download-to-launch qualification.
Other distributions and desktops remain unqualified.

Ubuntu documents [third-party deb installation in App Center](https://ubuntu.com/desktop/docs/en/24.04/explanation/snap-and-deb-packages/).
The helper uses [polkit's normal administrator authorization](https://www.freedesktop.org/software/polkit/docs/master/pkexec.1.html)
without running the GUI as root or enabling a textual password fallback.

<a id="guided-setup-recommended"></a>

## Guided setup (terminal)

On a compatible **Linux APT desktop** with **Python 3.11+**, open **Terminal**,
paste this whole block and press Enter. It checks the installer before starting it;
you do not need to choose a processor type, install developer tools, sign in,
or edit software-source files.

```sh
python3 -I -c '
import hashlib, subprocess, sys, urllib.request
url = "https://github.com/Foxlight-Foundation/foxlight-model-preservation/releases/download/sovereign-setup-v0.1.1/Install-Sovereign.py"
expected = "f54b28621781e1ae04b632bcf37ec814d7deff89e56024b8918326596beb0eff"
with urllib.request.urlopen(url, timeout=60) as response:
    data = response.read(131073)
if len(data) > 131072 or hashlib.sha256(data).hexdigest() != expected:
    raise SystemExit("Installer verification failed. Nothing was run.")
raise SystemExit(subprocess.call([sys.executable, "-I", "-c", data.decode("utf-8")]))
'
```

1. Read the short review. Type **install** to continue, or anything else to cancel.
2. Setup verifies the software key and the exact app package before the system asks
   for administrator permission. Enter **your administrator password** at its password
   prompt; the characters may not appear while you type. No password is sent
   to Foxlight. Do not run the setup command with `sudo`.
3. Wait for **Sovereign is ready**, then open **Sovereign** from Applications.
   Choose **Browse public models**, **Open recovery kit**, or **Open sharing file
   or source**. [Getting started](getting-started.md) walks through the next steps.

Setup detects amd64/arm64 and checks the actual installer requirements. It adds only the scoped
Foxlight software source, retains apt signature and expiry checks, and installs
the pinned 0.1.0-3 package plus any required desktop libraries. Future
Sovereign releases arrive through normal software updates. It grants no model
trust and starts no service, sharing or model download. Cancellation happens
before downloads or system changes. Existing custom key/source files are
preserved; setup stops and directs those operators to the manual route below.

The [setup release](https://github.com/Foxlight-Foundation/foxlight-model-preservation/releases/tag/sovereign-setup-v0.1.1)
also contains the standalone installer, instructions, checksums and detached
GitHub provenance. The command above independently pins the installer bytes;
the installer in turn pins the reviewed software key and both app packages.
It never executes unchecked downloaded code. The package's original native build
attestation and the software repository's signatures remain separate.

If setup stops, read its last message. Restore the network connection or resolve
the APT package-manager error, then run the same command again. Do not turn
off verification to get past an error. Setup can reuse exact settings from a
previous successful or interrupted run. It does not replace customized files.
For a preliminary check only, the verified downloaded installer accepts
`python3 -I Install-Sovereign.py --check` without downloads or system changes.

This is guided **terminal setup**, with fresh native amd64/arm64 installation and
installed-window checks. It is not a graphical installer or an observed
nontechnical-user study. Other Linux distributions remain unqualified.

## Install through the signed apt channel (manual)

On a compatible **Linux APT desktop**, download the Foxlight software archive key and check its
pinned checksum before adding the Sovereign source. This source installs only
Sovereign; it does not install Skulk. Run these commands in a terminal:

```sh
(
  set -e
  sudo apt update
  sudo apt install -y ca-certificates curl
  curl --fail --silent --show-error \
    https://apt.foxlight.ai/sovereign-archive-keyring.gpg \
    -o sovereign-archive-keyring.gpg
  echo 'f9a76cf4131603e3b7d905868efcf53f7ebea98ccdbfaf0d47ae0ac825cfd338  sovereign-archive-keyring.gpg' | sha256sum --check
  sudo install -m 0644 sovereign-archive-keyring.gpg /usr/share/keyrings/sovereign-archive-keyring.gpg
  echo 'deb [signed-by=/usr/share/keyrings/sovereign-archive-keyring.gpg] https://apt.foxlight.ai sovereign main' | sudo tee /etc/apt/sources.list.d/sovereign.list
  sudo apt update
  sudo apt install foxlight-sovereign
)
```

Open **Sovereign** from the application menu, or run `sovereign`. Future versions
arrive through normal apt upgrades; each new version still requires its own
reviewed public release. Opening or updating the app starts no model download,
router, inference or sharing.

The binary keyring SHA-256 above binds the bootstrap to the reviewed Foxlight
Foundation APT Archive Signing key, fingerprint
`1C0564D3E46950C30CB1665E61975FEE192BAB20`. `Signed-By` scopes it to this repository.
This software key grants no trust to models or capture keys. The separate
`sovereign` suite contains authenticated amd64/arm64 indexes with by-hash fetches,
immutable package bytes and a signed 30-day `Valid-Until`. The release retains
public GitHub package attestations; repository signing is separate from model
provenance and from the native build provenance. Expired or altered metadata
must fail; never disable apt signature or freshness checks to install.

To stop receiving this channel, remove `/etc/apt/sources.list.d/sovereign.list`
and run `sudo apt update`. To remove the app, use `sudo apt remove
foxlight-sovereign`. Both actions preserve your Library, keys, models and saved
recovery files. Retain complete recovery kits on another drive to recover from
loss of the original machine.

## Verify and install the public package

On a compatible Linux APT desktop, choose the `.deb` matching `dpkg --print-architecture` (`amd64`
or `arm64`) from the [Linux release](https://github.com/Foxlight-Foundation/foxlight-model-preservation/releases/tag/sovereign-linux-v0.1.0-3).
Download `SHA256SUMS` and `linux-release.sigstore.json` alongside it. With GitHub CLI
installed, verify the downloaded package before installing:

```sh
(
  set -e
  architecture=$(dpkg --print-architecture)
  package="foxlight-sovereign_0.1.0-3_${architecture}.deb"
  sha256sum --ignore-missing --check SHA256SUMS
  gh attestation verify "$package" \
    --repo Foxlight-Foundation/foxlight-model-preservation \
    --bundle linux-release.sigstore.json \
    --signer-workflow Foxlight-Foundation/foxlight-model-preservation/.github/workflows/linux-release.yml \
    --source-ref refs/tags/sovereign-linux-v0.1.0-3 --deny-self-hosted-runners
  sudo apt install "./$package"
  sovereign
)
```

Open **Sovereign** from the application menu for later launches. Use **Find** to
browse model descriptions, or **Open recovery kit** to recover a complete local
capture without its original Core or registry. Independent publisher listings
can be followed from **Open sharing file or source**. Installation requires no
Skulk, capability SDK, model runtime, GPU or hosted account. Sharing is opt-in.

To remove the software, use `sudo apt remove foxlight-sovereign`. The Library,
models, provenance and saved recovery files remain under your control.

The current recipe is **0.1.0 revision 3**, paired with the authenticated public
core 0.26 / Library 0.22 cohort. It includes the Horizon interface, complete
companion recovery, verified backup destinations, explicit finite sharing,
recipient handoff and reviewed alternate routes for an exact model capture.
Input pins, native package provenance and signed APT metadata remain separate trust surfaces.

## Build without a network resolver

`desktop/linux/inputs.json` pins the public Library archive and separate native
Python archives for both architectures. The original notices and build inventory
come from each architecture's matching full upstream Python archive. A builder
checks every input digest before staging and records exact software and source
identities, including whether the recipe checkout was dirty.

Fetch these explicit archives and prepare a wheelhouse from the public archive's
`requirements-desktop.lock` using `pip download --require-hashes --only-binary=:all:`.
Run on the selected native architecture with `uv`, `dpkg-deb` and `zstd` installed:

```sh
python scripts/build_linux.py --architecture amd64 \
  --library-archive /path/to/library.archive \
  --python-archive /path/to/python.archive \
  --notices-archive /path/to/notices.archive \
  --wheelhouse /path/to/wheelhouse --directory /new/staging-directory
```

The builder itself downloads nothing, resolves no online dependencies and runs
no application. Offline hash-locked installation supplies the public modules.
It removes generated console stubs containing staging interpreter paths, adds
fixed isolated launchers, and uses the source commit time for reproducible Debian
assembly. The workflow builds twice and compares exact `.deb` bytes.

## Lifecycle, data and native dependencies

The distro supplies `python3-gi`, GTK 3 and WebKit2 4.1 and applies their security
updates normally. Only the narrow window shell uses the distro Python. The
independent model owner and `model-preserve` / `foxlight-sovereign` commands use
the bundled Python with isolated imports and no bytecode writes. `sovereign`
opens the native window. The Library and all model verification remain the same
public implementation.

The native window keeps an ephemeral browser session, accepts navigation and
downloads only within its owner's exact loopback ticket, and keeps WebKit's
sandbox and TLS verification enabled. Explicit HTTPS link clicks open in the
system browser. File inputs use GTK's native chooser; kit downloads require a
native Save choice. Permission requests are denied. The private session ticket
is not logged. No arbitrary remote URL or command can launch the model owner.

On Ubuntu 24.04, WebKit's sandbox needs a scoped user-namespace permission.
A small compiled `/usr/bin/sovereign` launcher gives the application a fixed
identity. Its AppArmor 4 profile grants `userns` to that identity and descendants;
it is not full AppArmor application confinement. Package scripts reload/unload
only this profile when AppArmor is already active. They do not relax the global
kernel restriction or grant namespaces to every Python, shell or bubblewrap
process. The profile is a conffile, so ordinary upgrades preserve operator edits.
AppArmor 4 is a declared package dependency; older distributions are unqualified.

Closing the window or sending SIGTERM asks only its owned child to stop and
waits for cooperative shutdown. A different running owner makes the new owner
refuse the same Library. An interrupted job remains recorded and is never
silently replayed on reopening. There is no background service or automatic
restart grant in this package. Installing, opening or updating the application
does not start sharing, a router, inference or model downloads.

The default Library remains in `$XDG_DATA_HOME/foxlight/sovereign` (normally
`~/.local/share/foxlight/sovereign`). `sovereign --data-directory /absolute/path`
selects a separate Library explicitly. Package removal and purge remove only
package-owned software and its policy: no Sovereign units, scheduled jobs or user
data are shipped. The only maintainer actions manage its named AppArmor profile.
Models, keys, journals, receipts, saved kits and restored folders
stay under the operator's control. Keep recovery kits outside the original host.

## Scoped qualification

`Independent Linux desktop` runs on native Ubuntu 24.04 amd64 and arm64 runners.
It validates the desktop entry, reproducible package bytes, bounded package
inventory and actual package installation. In an isolated D-Bus/Xvfb session it
launches the installed identity and checks its scoped AppArmor label, the actual
WebKit worker's distinct user mapping, seccomp and no-new-privileges state,
independent
first launch with no model jobs, native file-chooser cancellation and cooperative
Quit. Installed command checks restore the public two-member PMF vector including
opaque cards and the original signed envelope, refuse tampered bytes and ignore
ambient Python. A final package purge must retain exact user file hashes and
leave the system-wide user-namespace restriction unchanged.

The public `apt-install.yml` workflow additionally qualifies the full native GUI
recovery journey against the published `foxlight-sovereign=0.1.0-3` package on
both native architectures. `scripts/qualify_linux_recovery.py` uses AT-SPI and
GTK's actual Open/Save dialogs to import the public two-member PMF vector,
require explicit acceptance of independently pinned capture keys, restart after
removing the runner's input kit, restore from Library, save a complete kit and
restart again. It compares every restored file size/hash, opaque source card,
original signed envelope and exact exported kit bytes, and requires the completed
jobs/copy to survive both app restarts without replay or implicit sharing.

Recovery runs inside an ephemeral network namespace with only loopback; neither
the original registry nor a Core can be contacted. The installed isolated core
is used for fixture preparation and independent readback after native Quit;
positive recovery operations are performed through the native UI. No WebView
script injection, backend operation calls or sandbox exceptions substitute for
that journey. The exact installed package/source/component receipts and native
screenshots are retained as workflow artifacts. The
[first complete amd64/arm64 public apt run](https://github.com/Foxlight-Foundation/foxlight-model-preservation/actions/runs/40595374618)
used core 0.25.0 / Library 0.21.0; the current revision 3 run linked above uses
core 0.26.0 / Library 0.22.0. These are small synthetic conformance inputs;
real-model recovery must retain its own exact-input evidence.

The signed APT publication separately verifies the public package producer,
uses the Foxlight software archive key in a disposable signing environment, and
requires a real APT update plus rejection of unknown keys, modified signatures,
expired metadata and changed index bytes before publication. Public installation
verifies the downloaded package attestation and checks that purge retains exact
user data and the global sandbox policy. App restart is not an OS reboot or a
power-loss test. Interrupted installation/upgrade, other distributions/desktops,
model inference, participant usability and public-network privacy remain
separate qualification.

API references: [PyGObject](https://pygobject.gnome.org/getting_started.html),
[ephemeral WebKit contexts](https://webkitgtk.org/reference/webkit2gtk/stable/class.WebContext.html),
[navigation policy](https://webkitgtk.org/reference/webkit2gtk/stable/signal.WebView.decide-policy.html)
and [native file chooser](https://webkitgtk.org/reference/webkit2gtk/stable/signal.WebView.run-file-chooser.html).
Ubuntu's [user-namespace policy](https://documentation.ubuntu.com/release-notes/24.04/)
explains the application-scoped AppArmor grant.
