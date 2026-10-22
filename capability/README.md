# Model Preservation Library capability

This separately packaged capability provides Guided first setup, Library for
everyday use, and Activity through Skulk Core's durable preservation host. It
uses local HTTP; its optional managed adapter uses the capability authoring SDK. It includes no Skulk imports,
private owner code, signing keys, verifier, artifact transfer or byte worker.

Version 0.6 adds reviewed local/mounted-folder setup, a durable destination for
new copies, independent availability for each saved copy, a pinned separate Core
preview installer and `--check` API compatibility. See [compatible preview setup
and storage](preview.md). Version 0.5 added reviewed **Publish**, **Share with someone**, **Receive shared model**
and **Review download resume** to the existing controls. Version 0.4 connected **Use this device**, **Preserve**, **Check**, **Check outcome**,
**Save recovery kit**, **Download recovery kit**, **Restore** and **Stop job**.
Preserve and restore review the complete supported companion selection. From a
preserved copy, create and download a `.foxkit` to storage separate from Core.
On a fresh Core, **Restore from recovery kit** works before archive setup. Choose
the file, review model, companions, capture fingerprints and provenance limits,
then confirm. Original Core storage, private signing keys and registry access are
not prerequisites for restoring an owner capture. Original registry evidence is
retained when available; a kit does not manufacture it.

Library navigation closes import and cleanup reviews. Late previews and errors
from a view you left are ignored. Flushed transfers remain available for explicit
resume; accepted Core jobs keep running.

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
create a separate public publication with **Publish** beside any saved private
copy in **Your copies**, even after its original installation is gone. Review
every member’s license, terms reference, redistribution decision and required
notices. Missing notice text can be supplied explicitly as publisher-supplied
text; it never replaces an original notice or grants rights. **Set up publishing**
creates a separate protected publishing identity. Inspect the exact proposed
public manifest and confirm before Core checks bytes and signs the public copy.
This does not start hosting or grant registry authority.

For a public copy, choose **Set up HTTP mirror** or **Set up BitTorrent**. Review
network access (default: only this device), port, duration up to 24 hours and upload
allowance. The suggested finite allowance scales with the selected copy
(three copies plus metadata/retry room; at least 16 MiB), with a 24-hour session
suggestion. It resets when choosing a different copy. BitTorrent also has an upload-speed limit. Confirm the separate sharing
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
Direct object-store adapters, followed catalogs and unattended continuous hosting
remain separate enhancements. An unclean Core exit
can leave model-sized temporary torrent exports in Core-owned work storage.
Activity lists known stopped jobs with **Review temporary files**. Inspect the
logical regular-file size (a lower bound when inspection reaches its limit),
then confirm **Clear temporary files**. This removes only that job's owned work;
verified public copies, private archives, keys, kits and retained torrent metadata
stay intact. Core authenticates custody before deletion and quarantines the work
so interrupted cleanup can be explicitly retried after restart. Older work needs
its matching host-authored request. Missing custody evidence, changed directory
identity, unsafe links and active jobs refuse deletion. Cleanup never restarts
sharing or imports models. This control requires Core's temporary-files APIs.
Tor/I2P remain under evaluation. Ordinary public mirrors and BitTorrent expose
network addresses to the serving operator/other peers; neither is anonymous.

## Send and receive a model

For a kept public copy, **Share with someone** offers two paths:

- **Prepare recovery kit for recipient**: save the complete `.foxkit`, then send
  it or put it on storage your recipient can access. No live mirror is needed.
- **Download invitation file**: supply one or more reachable HTTP(S) mirror base
  addresses hosting this exact complete bundle. Download the small original-signed
  metadata file, attach it to a message and use the supplied recipient text.
  The message contains the exact bundle identity and candidate public fingerprints,
  never the private Library control link. Addresses are unverified hints; exporting
  an invitation does not open a listener, arrange TLS or establish reachability.

The recipient opens **Sharing → Receive shared model** and chooses the invitation.
Core inspects the signed identity, all members, declared size, candidate keys,
threshold and mirror hints offline before any DNS or download. Authenticate the
identity, fingerprints and threshold independently; a file and its bundled keys
can be replaced together. Explicitly confirm the independently authenticated bundle identity and signature
threshold, and select only fingerprints you independently checked. Fingerprint
confirmation alone does not enable download review. Selecting a different
invitation resets these confirmations.
Review finite object-byte allowance and per-attempt deadline; plaintext HTTP and
local/private network destinations each need a separate opt-in. Confirm the exact
copy and policy to download. Core retains model files, companions, provenance and
public verification keys. It does not install, execute repository code or seed.
Choose **Save recovery kit** or **Restore** afterward; loading trust and code
permissions remain separate. Unknown future cards remain preserved even when
this runtime cannot interpret them.

Closing Library leaves Core’s accepted download running. A Core restart stops it
and never automatically replays it. For a stopped receive with retained working
files, **Activity → Review download resume** uses the original invitation even
if its upload was removed. A fresh reviewed action resumes the same job, partial
files and cumulative object-byte allowance. Earlier charges stay deducted;
resume never quietly refills the budget or changes sources/trust/policy. If work
was explicitly cleared, review a new download instead. A complete local stage
can finish after all mirrors disappear by checking every retained object offline.

Public copies can export kits and restore before private archive setup. A kit is
not encrypted. Public signatures protect captured integrity and do not establish
registry authority, grant redistribution rights or provide anonymity. Direct
mirrors see Core’s network address; public BitTorrent exposes peer participation.

These controls require Core’s publishing, invitation and receive-resume APIs
(delivered in Skulk PR #1280); version numbers alone do not establish their
availability. Older Core builds keep the earlier controls and show an upgrade
message. Core PR #1281 replaces the final-directory reservation with an atomic exclusive
move on supported Linux/macOS filesystems. A process exit retains the verified
stage or complete destination without creating an empty placeholder. Unsupported
filesystems refuse safely; old unauthenticated remnants still need inspection.
Folder enrollment/default selection requires the additional setup APIs in the
compatible preview. Specific NAS/provider behavior remains separately qualified.

## Install and open Library

Library has a public local launcher and an optional managed capability adapter.
The local launcher requires Python 3.13 or later and a compatible running Skulk
Core on the same model-store host. Its wheel includes the interface; Node.js,
Skulk's Python package and the managed SDK are not installation dependencies.

```sh
uv tool install https://github.com/Foxlight-Foundation/foxlight-model-preservation/releases/download/library-v0.6.0/foxlight_preservation_library-0.6.0-py3-none-any.whl
foxlight-preservation-library
```

The launcher checks the local Core and opens a private browser link. Use `--check`
for a read-only report of the actual owner and required API surfaces. If Core is
missing or older, [preview setup](preview.md) installs pinned compatible components
in a separate private directory. For another
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

## PMF source preview (Library 0.7)

New compatible Core plans preserve in Portable Model Format automatically. Check,
Save recovery kit and Restore detect PMF or existing v1 copies without a format
selector. The review names the format and retains the same exact-copy trust,
registry evidence, companion and separate repository-code decisions. A PMF kit
can also be imported/verified/restored with the standalone tools without Skulk.
A generic PMF profile does not imply this Core can run it; its files remain
recoverable with a compatible standalone reader.

The PMF-capable Library source can be installed with public dependencies:

```sh
uv tool install --force --python 3.13 'git+https://github.com/Foxlight-Foundation/foxlight-model-preservation.git@main#subdirectory=capability'
foxlight-preservation-library
```

A compatible Core is required on the model-store host. For a separate unconfigured
installation use [the source preview setup](preview.md). Source setup reuses the
exact resolved Library commit from the installed package; it does not replace
this version with an older released wheel. The 0.7 software release has not been
cut; the older 0.6 release link above retains its previous behavior.
