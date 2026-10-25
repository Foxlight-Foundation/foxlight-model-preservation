# Independent Sovereign Library (source preview)

Sovereign can browse the public model catalog, privately preserve a supported
exact model selection, save a portable recovery kit and restore its files without
Skulk Core, a managed capability SDK, a GPU or a hosted account. The native macOS
shell uses the same Library interface as the public local launcher. This source
preview is not a published notarized Mac application or an APT release.

## Everyday use

Launch `foxlight-sovereign` with the public core and Library packages installed,
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

## Reproducible local Mac preview

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
