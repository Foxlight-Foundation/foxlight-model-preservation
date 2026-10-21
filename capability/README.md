# Model Preservation Library capability

This separately packaged capability provides Guided first setup, Library for
everyday use, and Activity through Skulk Core's durable preservation host. It
uses local HTTP; its optional managed adapter uses the capability authoring SDK. It includes no Skulk imports,
private owner code, signing keys, verifier, artifact transfer or byte worker.

Version 0.4 connects **Use this device**, **Preserve**, **Check**, **Check outcome**,
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

Sharing now imports an existing signed **public** `.foxkit` into a separate
immutable copy, retaining unknown card metadata without model admission. Review
its exact manifest identity and candidate public fingerprints, then explicitly
accept the copy. Importing starts no listener or swarm. Private kits are refused;
creating a new public publication remains a separate review through the parent
standalone tools. It requires original terms review and a separate public key.

For a public copy, choose **Set up HTTP mirror** or **Set up BitTorrent**. Review
network access (default: only this device), port, duration up to 24 hours and upload
allowance. BitTorrent also has an upload-speed limit. Confirm the separate sharing
choice to start. Core verifies all bytes before serving. You can close Library;
Core retains the session. **Stop sharing** waits for the owned worker to stop and
keeps the verified public copy. Core restart ends sessions and never replays them.

HTTP counts full reserved response bytes, including interrupted responses, and
has no speed cap. BitTorrent reports observed payload; its stop threshold can
overshoot in flight. Network overhead is excluded. Status retains reviewed limits
and aggregate counters without peer addresses or request history. Download standard
`.torrent` metadata and the copy's public verification keys to share with recipients;
downloading these files does not itself join a swarm. Recipients must authenticate
fingerprints independently and select the exact manifest ID.

Network mode binds all IPv4 interfaces. Arrange firewall/routing, DNS and TLS
separately; a supplied mirror address is a hint, not verified reachability. Core
builds without the compatible native BitTorrent adapter retain HTTP. Additional
NAS/remote adapters, followed catalogs, one-step recipient invitations and
unattended continuous hosting remain separate enhancements. An unclean Core exit
can leave model-sized temporary torrent exports in Core-owned work storage. Clean
stop removes the temporary export, but this preview has no restart reclamation
control for abandoned sharing work. Preserve the verified public copy and use
operator storage maintenance rather than deleting unknown files while Core runs.
Tor/I2P remain under evaluation. Ordinary public mirrors and BitTorrent expose
network addresses to the serving operator/other peers; neither is anonymous.

## Install and open Library

Library has a public local launcher and an optional managed capability adapter.
The local launcher requires Python 3.13 or later and a compatible running Skulk
Core on the same model-store host. Its wheel includes the interface; Node.js,
Skulk's Python package and the managed SDK are not installation dependencies.

```sh
uv tool install https://github.com/Foxlight-Foundation/foxlight-model-preservation/releases/download/library-v0.4.0/foxlight_preservation_library-0.4.0-py3-none-any.whl
foxlight-preservation-library
```

The launcher checks the local Core and opens a private browser link. For another
local Core port use `--core-url http://127.0.0.1:PORT`; `--no-browser` prints the
link without opening it. It only listens on loopback and does not perform reverse DNS to start the local screen. Treat the ticket-scoped
link as private. Closing the launcher stops its screen, while accepted jobs stay
with Core. Reopening uses the same local workflow identity. It does not enable
an archive, start a restore, host a mirror or join a swarm on launch.

A Core build with the preservation/recovery APIs is required; an older stable
installation may need an update. This package does not update Core, configure a
Model Store or install an inference engine. The launcher and managed capability
use the same Library/Guided interface and canonical Core job journal.

Source installation is also available with `python -m pip install ./capability`
from this repository. The default wheel has only public `httpx` and `pydantic`
dependencies. The optional `managed` extra uses the separately supplied SDK; the
public launcher never loads it.

## Clear unused restore files

Activity offers **Clear unused restore files** for a stopped restore. It asks
Core to authenticate that job's known files and remove only unindexed temporary
work. Indexed models and saved kits are retained. Cleanup is explicit and safe
to repeat after an interruption. Then choose **Review again**, inspect the new
plan and confirm the current permissions; cleanup never starts recovery or
copies a prior consent. Missing/corrupt indexes and changed filesystem identities
refuse deletion rather than guessing. This control requires Core's reclaim API.

## Build and managed access

The interface build needs Node.js. The optional managed authoring SDK is pinned
to `b9dc2e9d0fb7a8466f5ffbb52efebfa6b2f8e259`, version 0.5.0. Its source remains
access-controlled. That limits managed builds and capability-store delivery;
it does not limit installing the public local launcher.

```sh
cd capability
npm ci --prefix web
npm run build --prefix web
uv sync --frozen --extra managed
uv run --extra managed ruff check
uv run --extra managed ruff format --check
uv run --extra managed basedpyright
uv run --extra managed pytest
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
The SDK owner launches the child from protected inherited startup data. The managed child requires that startup; the public local launcher has its own
loopback lifecycle and does not simulate an SDK owner.

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

## Earlier Library 0.3.0 delivery

- Public local launcher and default wheel installation no longer require the
  managed SDK or Skulk's Python environment. Core continues to own storage/jobs.
- Activity explicitly clears known unused stopped-restore files through Core's
  operator-gated reclaim API; fresh review and consent remain separate.
- Public-package CI installs the built wheel without SDK/Core packages and tests
  actual loopback HTTP controls. Managed adapter coverage remains separate.

## Sharing capability scope

The Library screen is the explicit human sharing surface. Public sharing jobs use
an owner identity separate from the existing governed `foxlight.preservation.copy`
operation, whose version 0.2 input/result contract is unchanged. SDK callers do
not acquire a new network-serving operation implicitly. A future agent-facing
sharing descriptor needs its own permission and immutable review contract.
Public-copy import uses the portable signed card as opaque data; actual loading
continues through Core's existing recovery and model-trust checks.
