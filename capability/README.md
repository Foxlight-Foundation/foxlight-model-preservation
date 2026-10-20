# Model Preservation Library capability

This separately packaged capability provides Guided first setup, Library for
everyday use, and Activity through Skulk Core's durable preservation host. It
uses the capability authoring SDK and local HTTP. It includes no Skulk imports,
private owner code, signing keys, verifier, artifact transfer or byte worker.

The current connected effects are **Use this device**, **Preserve**, **Check**,
**Check outcome** and **Stop job**. Preserve reviews the complete supported
installed companion selection. Core retains exact plans, request identities,
copy receipts and jobs across page/child restarts. Closing this capability stops
its UI listeners while Core keeps its jobs. Core restart interrupts uncertain
work for an explicit outcome check. The existing native recovery tools handle
fresh-home recovery; this capability does not yet import into a running store.

Public publication and opt-in BitTorrent sharing are implemented in the parent
standalone package. Hosting from Library, additional enrolled NAS/remote storage
adapters, followed catalogs, exportable verification kits and recovery controls remain
integration work. Full cluster-loss recovery through Library requires those
verification-kit and recovery steps; current private Preserve/Check uses the
trust material retained by Core.
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
currently needs the Core preservation-host component rather than an older
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
