# Model Preservation Library capability

This separately packaged capability provides Guided first setup, Library for
everyday use, and Activity through Skulk Core's durable preservation host. It
uses the capability authoring SDK and local HTTP. It includes no Skulk imports,
private owner code, signing keys, verifier, artifact transfer or byte worker.

Version 0.2 connects **Use this device**, **Preserve**, **Check**, **Check outcome**,
**Save recovery kit**, **Download recovery kit**, **Restore** and **Stop job**.
Preserve and restore review the complete supported companion selection. From a
preserved copy, create and download a `.foxkit` to storage separate from Core.
On a fresh Core, **Restore from recovery kit** works before archive setup. Choose
the file, review model, companions, capture fingerprints and provenance limits,
then confirm. Original Core storage, private signing keys and registry access are
not prerequisites for restoring an owner capture. Original registry evidence is
retained when available; a kit does not manufacture it.

Uploads use bounded 4 MiB chunks. Reselect the same file to resume a flushed
transfer after page, capability or Core restart. Completed jobs expose a native
download; the browser does not buffer whole kits in JavaScript. Core retains plans,
operation identities and jobs across page/child restarts. Closing this capability
stops its UI listeners while Core keeps submitted jobs. A Core restart interrupts
uncertain work for explicit review and retry; it never replays byte work.

Recovery requires separate capture acceptance and repository-code permission when
applicable. Deliberately removed models need a separate confirmation, bound to
the reviewed deletion records. Matching owned companions can join the new complete
generation after verification. A different existing primary capture is protected.
Cards and original signed evidence survive format evolution; an older runtime
may need an upgrade to interpret a newer card without changing the saved kit.

Public publication and opt-in BitTorrent sharing are implemented in the parent
standalone package. Hosting from Library, additional enrolled NAS/remote storage
adapters and followed catalogs remain integration work.
Library's Sharing page explains this boundary and starts no listener or swarm.
Tor/I2P remain under evaluation. Ordinary public mirrors and BitTorrent expose
network addresses to the serving operator/other peers; neither is anonymous.

## Build and access

Requires Python 3.13 and Node.js for the web build. The authoring SDK is pinned to
`b9dc2e9d0fb7a8466f5ffbb52efebfa6b2f8e259`, version 0.5.0. Its upstream source is
currently access-controlled; the public portable preservation core has no such
dependency. This capability's source and local qualification do not establish a
public self-service installation path or a capability-store release.

```sh
cd capability
npm ci --prefix web
npm run build --prefix web
uv sync --frozen
uv run ruff check
uv run ruff format --check
uv run basedpyright
uv run pytest
uv build
```

`capability.toml` describes the managed child, validator and exact web resources, including application and third-party notices.
A publisher uses the existing SDK `capability-build --spec capability.toml` path
with an independently enrolled publisher, reviewed permissions and a complete
protected wheelhouse containing the SDK's owner/management runtime launchers.
The archive carries this capability and its web assets only. The private SDK
owner and authoring SDK remain separately supplied dependency wheels.

The manifest requires Skulk 2.0.3 or later in the same major version; the preview
currently needs the Core recovery-kit/upload/live-store restoration APIs rather than an older
released installation. The first supported operator runs this on the configured model-store-owning
host, with Skulk's `/preservation` API available. The ordinary setting defaults
to `http://127.0.0.1:52415`; only explicit loopback Core authorities are allowed.
The SDK owner launches the child from protected inherited startup data. Direct
execution without that startup is not a supported installation shortcut.

## Control and privacy

Fabric callers use the SDK durable-operation contract for plan/start/status/list/
log/cancel. Core owns the sole canonical job journal; there is no second SDK byte
runner to conflict with it. Start requires an exact reviewed plan and stable
operation ID. Read/status/list do not start work. Plan normalization binds resolved
check targets; changed start inputs refuse. Logs contain safe status summaries.

The operator page follows SDK-authorized loopback/serve addresses and peer checks,
with a volatile ticket-scoped path, same-origin JSON controls, no-store responses,
no-referrer policy and a restrictive content policy. It loads no analytics,
external fonts or supplier resources. Opaque handles are not an OS sandbox:
same-user processes and the trusted fabric retain their existing authority.
