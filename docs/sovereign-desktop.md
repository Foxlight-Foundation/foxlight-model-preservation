# Independent Sovereign Library

Sovereign can browse the public model catalog, privately preserve a supported
exact model selection, save a portable recovery kit and restore its files without
Skulk Core, a managed capability SDK, a GPU or a hosted account. The native macOS
shell uses the same Library interface as the public local launcher. The public Python installation archive provides this interface; signed native
Mac and APT releases have separate publication and qualification gates.

The [Linux desktop recipe](linux-distribution.md) builds offline Debian candidates
with a native GTK/WebKit window and bundled public runtime. Signed APT publication
and its broader installed qualification remain separate from those candidates.

## Everyday use

Select `requirements-desktop.lock` from a verified v2 Library installation
archive ([instructions](installation.md#select-the-independent-desktop-profile)),
then launch `foxlight-sovereign` with both public packages installed,
or open the self-contained Mac app built below. First launch opens **Find models**. Once you have saved copies, reopening starts
in **Library**.
Choose **Browse public models** to request metadata from the optional built-in
Foxlight registry preset. No bootstrap file or account is needed. Search uses the
accepted signed catalog on this device and continues to work offline. Updating
listings is an explicit action, separate from model downloads.

Choose **Review download & preserve** for a supported selection. The review
includes the exact card, full selected companion repositories, total object
bytes, publication freshness and connection disclosure. Confirming starts a
finite, private background job. Each file must match its original signed size
and SHA-256. The original model card, all properties and signed publication proof
are retained in the PMF capture. No model code or runtime is executed.

**Library** contains complete saved PMF copies. **Save recovery kit** prepares a
kit and opens a download/save dialog. **Restore files** verifies the copy and
writes a fresh private folder, including all captured members and source
documents. **Activity** retains outcomes and provides the completed kit download
or **Open restored folder** action after the original view closes.

**Open recovery kit** works without the original Core or registry. Its review
shows the exact archive and capture-key fingerprints. A kit's self-signature
does not establish authorship: independently check those fingerprints before
accepting them. Acceptance permits verification of this exact capture; it grants
neither registry authority nor permission to run code. Legacy preservation kits
are losslessly converted to PMF; existing PMF kits keep their original identity.
Unknown card data and source documents remain opaque and are preserved.

## Trust, privacy and limits

### Follow independent sources

Independent source following requires matching core **0.22.0** and Library
**0.17.0** packages. Earlier published 0.21/0.16 installations retain their
registry catalog and recovery features; they do not include these new controls.
A Python package release does not publish a new signed native application.

In **Find models → Independent sources**, choose **Add source file**. The desktop
accepts a direct HTTP(S) source bootstrap or exported offline source cache up to
32 MiB. Opening the file inspects it locally. Check its bootstrap fingerprint,
root keys and threshold with its publisher before choosing **Follow source**.
Source trust authorizes listings only; it does not accept model keys or confer
registry authority. Following a bootstrap offers an update review without
starting a connection. An imported cache is searchable as historical evidence.

**Sources & updates → Review update** starts a finite authenticated TUF metadata
job only after confirmation. Failed updates preserve the last accepted listings
and rollback floors. Search stays on the device, including after restart and
when source hosts are unavailable. Results retain their individual source
attribution and exact manifest IDs. **Stop following** hides that source's
listings and prevents updates; it keeps rollback protection, already inspected
model references, saved copies and recovery kits. No automatic eviction or
deletion is added.

**Review signed details** contacts the selected mirrors for the original signed
manifest, using source-supplied candidate keys only to inspect its signatures.
It fetches no weights and enrolls no model signer. Open the successful metadata
job in **Activity → Review signed model**, independently check the capture-key
fingerprints, and select which keys to accept for that exact copy. The original
threshold and revocations still apply. Review the complete selected byte count,
companions, mirrors and received-byte allowance before **Download & preserve**.
The original signed envelope is pinned byte for byte across this review and
download. Every file verifies before a PMF copy appears in Library; the complete
original archive and signed evidence remain inside it. Existing kit export and
offline restore then work without the source, registry or original host.
The original public capture-key candidates, threshold and revocations travel in
an optional PMF document too. They can be checked after recovery, but remain
candidates requiring independent fingerprint authentication, not inherited trust.

HTTP and private-address permissions are separate for source updates, model
details and weight downloads. Changing metadata connection settings requires a
new review before starting. Direct mirrors see the recipient's address and
requested paths. None of these operations starts serving, a swarm, a router,
runtime execution or global model-key enrollment. The desktop source adapter
currently accepts direct HTTP(S) sources and v1 signed preservation manifests.
Peer/I2P source following and direct PMF network transport remain separate
command/Core adapters; unsupported routes are never silently enabled. Skulk's
existing source interface and admission checks remain separate.

### Built-in registry source

The bundled [registry root](../src/foxlight_model_preservation/resources/foxlight_registry_root.json)
is a preset supplied by the application, not a key obtained from the endpoint.
Stock TUF verifies current updates and rollback protection. Original accepted
snapshots retain their exact catalog and proof bytes. A historical snapshot can
be browsed, but an expired publication or one older than already observed TUF
metadata requires explicit historical acceptance
before downloading. A failed update does not erase the accepted catalog.

The first standalone registry adapter requires complete SHA-256 file maps. It
supports primary selections and explicitly mapped image companions. It refuses
unsupported companion declarations or missing hashes before fetching objects;
it never silently drops companions. Unsupported cards stay browsable. Existing
portable kits and standalone command-line adapters remain usable independently
of this registry adapter. Neither listing nor successful capture establishes
runtime compatibility or redistribution rights.

Updating contacts `registry.foxlight.ai`. Search terms are not sent. Direct
model downloads contact the pinned Hugging Face repository/revision and its
HTTPS CDNs. Each redirect is independently checked for credential-free HTTPS
and public destination addresses. Suppliers can see the client's address and
requested files. There is no automatic seeding, public listener, peer joining,
router installation or telemetry. The local view uses a private loopback ticket,
Host/Origin checks and bounded same-origin controls.

A private process lock fences the owner. Jobs and immutable review bindings are
journaled before work. Closing the view leaves the application working; quitting
requests cooperative cancellation. Reopening records unfinished jobs as
interrupted and never replays network work automatically. Complete copies are
indexed only after verification. Partial work stays private and is not a model
copy. Existing generations, kits and interrupted work are retained; this preview
has no automatic retention policy. Older completed job receipts move to private
history, remain browsable in Activity, and retain their request identity so a
repeated submission cannot restart them. Receipt history does not consume active
queue slots. **Remove transfer** in Activity explicitly removes only this app's
input copy of a recovery kit; it keeps the original file, saved model copies and
completed kits, and refuses removal while an import needs it. Eight input
transfers, four active jobs and 256 saved copies are preview limits. Reaching the
saved-copy limit does not block exporting or restoring existing copies. It writes only to its dedicated
private home and fresh recovery destinations.

Default data locations are `~/Library/Application Support/Foxlight/Sovereign` on
macOS and `$XDG_DATA_HOME/foxlight/sovereign` (normally
`~/.local/share/foxlight/sovereign`) on Linux. `--home` selects a dedicated private
folder. Concurrent owners or changed storage identities are refused. Original
runtime-owned stores are not modified. Capturing can require several times the
selected bytes for private working copies; insufficient local space refuses the
download before object requests. Storage needs normal POSIX rename/fsync/flock
semantics. No Windows support is claimed.

See [upgrade and downgrade boundaries](installation.md#upgrade-and-downgrade-boundaries)
before changing the software that owns an existing private home. Archive recovery
compatibility does not make an older application a reader for new Activity kinds.

## Reproducible local Mac preview

For a release-mode candidate with hash-pinned public inputs and the separate
Developer ID/notarization procedure, see [macOS distribution](macos-distribution.md).
The following flexible builder remains a local preview tool.

Build on macOS with Swift, uv and a portable CPython 3.13 runtime. End users of
the assembled application need neither Python nor a developer checkout. Build
the frontend, public core wheel and Library wheel from the same revision, then
export and install the hash-locked desktop dependencies into a clean directory:

```sh
npm ci --prefix capability/web
npm run build --prefix capability/web
uv build --wheel --out-dir dist-core
uv build --project capability --wheel --out-dir dist-library
uv export --project capability --extra desktop --no-dev --no-emit-project \
  --no-emit-package foxlight-model-preservation --format requirements-txt \
  --output-file desktop-requirements.txt
uv pip install --python /absolute/portable-python/bin/python3 \
  --target /absolute/clean-site --require-hashes -r desktop-requirements.txt
```

Run `python scripts/package_desktop.py --help` for the explicit runtime, clean
site, two wheel, source-revision, build-directory and new output `.app` arguments.
The app bundles the runtime, both public packages and their local assets. The isolated runtime disables bytecode writes inside the sealed application. Its
build receipt records input wheel and runtime-file identities. No private SDK,
Core checkout or model data belongs in those inputs. Packaging applies an ad hoc
local signature; it does not claim Developer ID signing, notarization,
Gatekeeper acceptance or clean-machine qualification. `--data-directory` on the
native executable selects isolated storage for acceptance testing.
