# Independent Linux desktop candidates

The public Linux recipe builds `foxlight-sovereign` Debian packages for native
amd64 and arm64 runners. The package contains the same independent Library and
preservation core as the public Python installation, a pinned portable CPython
3.13 runtime, a GTK/WebKit window, desktop entry and the approved Sovereign icon.
It requires no Skulk Core, capability SDK, account or GPU.

These are **unsigned build candidates**. There is no Sovereign signed APT
repository or advertised supported Linux release yet. CI artifacts are useful
for developer/operator acceptance, not an authenticated public installation
channel. Ubuntu runner acceptance also does not establish Debian, every desktop,
Wayland, full real-model recovery, independent custody or network privacy.

The current recipe is **0.1.0 revision 2**, paired with the authenticated public
core 0.25 / Library 0.21 cohort. It includes the Horizon interface, complete
companion recovery, verified backup destinations, explicit finite sharing,
recipient handoff and reviewed alternate routes for an exact model capture.
Updating these input pins does not publish an installer or signed APT channel.

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

Full native kit import/restore/save/restart, interrupted installation/upgrade,
additional distributions/desktops and full real-model acceptance remain further
qualification. A signed APT channel additionally needs dedicated software key
custody, scoped `Signed-By`, authenticated/expiring metadata, immutable pool
publication, attested exact packages and negative update tests. An unsigned
candidate or successful local `.deb` install does not satisfy those gates.

API references: [PyGObject](https://pygobject.gnome.org/getting_started.html),
[ephemeral WebKit contexts](https://webkitgtk.org/reference/webkit2gtk/stable/class.WebContext.html),
[navigation policy](https://webkitgtk.org/reference/webkit2gtk/stable/signal.WebView.decide-policy.html)
and [native file chooser](https://webkitgtk.org/reference/webkit2gtk/stable/signal.WebView.run-file-chooser.html).
Ubuntu's [user-namespace policy](https://documentation.ubuntu.com/release-notes/24.04/)
explains the application-scoped AppArmor grant.
