# Model Preservation Library capability

This separately packaged capability provides Guided first setup, Library for
everyday use, and Activity through Skulk Core's durable preservation host. It
uses local HTTP; its optional managed adapter uses the capability authoring SDK. It includes no Skulk imports,
private owner code, signing keys, verifier, artifact transfer or byte worker.

Library 0.14.0 adds [retained-copy recovery](#recover-a-retained-mirror-copy), keeps [persistent mirror controls](#keep-models-available), and retains explicit [I2P HTTP source and receiving controls](#i2p-source-and-receiving-controls)
with its pinned compatible Core, alongside the peer setup, PMF and one-file
sharing workflows below. These are public previews with separate networking and
trust decisions, not a qualified anonymity mode.

Library 0.11 introduced **Share with peers** and portable `.foxhandoff` files.
From **Sharing**, choose **Share with someone** beside a verified public copy,
then **Share with peers**. Review local contacts and network access, duration and
the observed native upload threshold, then explicitly start the public session.
Download the sharing file when preparation completes. It remains downloadable
after reopening Library or stopping the session, using Core's retained job plan.

Recipients choose **Receive shared model** and open that file. Core inspects the
original signed reference offline. Contacts are unsaved suggestions; importing
does not accept model keys, allow private addresses or authorize a connection.
Independently authenticate the exact bundle, signature threshold and fingerprints,
review and save any contact changes, then separately permit the download.
Receiving keeps the complete copy; ordinary **Restore** recovers its companions,
cards and signed provenance with the existing owner/code permissions.

Closing Library leaves the reviewed Core job running. Stop waits for native
serving to drain and retains the archive, file and native cache. Cache cleanup is
an explicit stopped-job action; Core restart never resumes those finite peer sessions. Native
wire thresholds include routing overhead and can overshoot. These controls do
not impose hard filesystem/network quotas, arrange reachability or provide
anonymity. Persistent mirror controls have separate settings and restart permission below.

The same file works with the [standalone tools](../docs/handoffs.md), without
Skulk or this capability. Standalone tools also wrap source references; **Add a
source** accepts those files without changing their inner source/TUF identity.
Older Core keeps the existing HTTP invitation workflow and does not offer the
new peer-sharing action. Use the [current compatible preview](preview.md).

Version 0.6 adds reviewed local/mounted-folder setup, a durable destination for
new copies, independent availability for each saved copy, a pinned separate Core
preview installer and `--check` API compatibility. See [compatible preview setup
and storage](preview.md). Version 0.5 added reviewed **Publish**, **Share with someone**, **Receive shared model**
and **Review download resume** to the existing controls. Version 0.4 connected **Use this device**, **Preserve**, **Check**, **Check outcome**,
**Save recovery kit**, **Download recovery kit**, **Restore** and **Stop job**.
Preserve and restore review the complete supported companion selection. From a
preserved copy, create and download a `.foxkit` to storage separate from Core.
On a fresh Core, the header's **Restore** works before archive setup. Choose
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
copy in **Library** (each copy's **⋯** menu holds **Publish** and
**Save recovery kit**), even after its original installation is gone. Review
every member’s license, terms reference, redistribution decision and required
notices. Missing notice text can be supplied explicitly as publisher-supplied
text; it never replaces an original notice or grants rights. **Set up publishing**
creates a separate protected publishing identity. Inspect the exact proposed
public manifest and confirm before Core checks bytes and signs the public copy.
This does not start hosting or grant registry authority.

For a public copy, choose **Set up HTTP mirror** or **Set up BitTorrent**. Review
who can connect (**Only this device** by default, or **Other devices**), port, duration up to 24 hours and upload
allowance. The suggested finite allowance scales with the selected copy
(three copies plus metadata/retry room; at least 16 MiB), with a 24-hour session
suggestion. It resets when choosing a different copy. BitTorrent also has an upload-speed limit. Confirm the separate sharing
choice to start. Core verifies all bytes before serving. You can close Library;
Core retains the session. **Stop sharing** waits for the owned worker to stop and
keeps the verified public copy. Core restart ends these finite sessions and never replays them. Persistent mirroring below has its own separately confirmed restart permission.

HTTP counts full reserved response bytes, including interrupted responses, and
has no speed cap. BitTorrent reports observed payload; its stop threshold can
overshoot in flight. Network overhead is excluded. Status retains reviewed limits
and aggregate counters without peer addresses or request history. Download standard
`.torrent` metadata and the copy's public verification keys to share with recipients;
downloading these files does not itself join a swarm. Recipients must authenticate
fingerprints independently and select the exact manifest ID.

Network mode binds all IPv4 interfaces. Arrange firewall/routing, DNS and TLS
separately; a supplied mirror address is a hint, not verified reachability. Core
builds without the compatible native BitTorrent adapter retain HTTP. Direct object-store adapters remain separate enhancements. Persistent hosting has its own explicit controls below. An unclean Core exit
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
Ordinary public mirrors and BitTorrent expose network addresses to the serving
operator/other peers; neither is anonymous. Library 0.12.0 separately supports
[I2P HTTP source updates and receiving](#i2p-source-and-receiving-controls)
through an explicitly chosen local proxy. It is a functional pilot without
qualified anonymity, not a routing mode for other Library/Core traffic. The
standalone I2PSnark recovery-kit pilot remains separate from Library.

## Keep models available

In **Sharing → Keep models available**, choose **Set up mirror**. Name the
mirror and set its storage, daily download, daily upload and monthly upload
allowances. The allowance includes space needed for safe preparation, not just
model files. Reaching an allowance stops new work; it never authorizes deletion.
All transports start off. Choose HTTP, public IPFS and public BitTorrent
separately, then **Save settings**. Saving starts no listener or peer session.
IPFS uses the existing reviewed peer-software and starting-contact setup.

**HTTP mirror** defaults to this device only. To accept connections from other
devices, separately select that exposure and arrange firewall/routing, hostname
and TLS as needed. Local ports and native-worker counts describe this host's
activity; they do not establish reachable or independently operated replicas.
Public IPFS/BitTorrent and direct HTTP can expose addresses and offered content.
These controls do not provide anonymity.

Open **Keep another public copy**, then **Keep available** beside an accepted
public Library copy. Library first reads that exact retained identity, regardless
of the displayed page. A previously removed selection requires a separate,
fresh reintake confirmation. An unavailable observation never means absence.
Review the exact signed identity and every member's original
license declaration, terms reference and required notice paths, including
companions and notice-only members. Confirm that you reviewed those terms and
may redistribute the complete selection. These references are not a new license
grant. Core checks and retains the original envelope, cards and every required
file in separate managed storage. Retaining starts no sharing or model loading.
Direct public PMF mirror transport is not available in this adapter yet: its
metadata can be reviewed, but its original PMF archive is kept and intake refuses.

Choose **Start sharing**, review the exact settings and finite duration, then
confirm serving. Jobs belong to Core and continue when Library closes. Reopen
Library to inspect the same owner and choose **Stop sharing**. Stop waits for the
actual listener and owned workers to drain, clears running intent and retains
models. A delayed or failed historical receipt does not hide Stop for a current
controller. Cancelling another intake operation does not disable that controller's
Stop.

**Restart permission** is a separate, expiring decision for the exact reviewed
policy. Permission alone starts no sharing. To use it, separately Start and select
the saved permission instead of a finite run. Core resumes only with the exact
unexpired grant and prior running intent. New intake or changed settings require
fresh permission; Stop clears intent. Closing Library grants nothing.

Each retained copy's **⋯** menu offers **Pin mirror copy**, **Block future
serving**, retry for pending intake and **Remove mirror copy**. Pin protects
against removal; block keeps bytes while withholding future serving. Stop active
sharing before changes. Removal requires blocking future serving, unpinning and
fresh confirmation for the
exact selected identity, and deletes only that mirror's selected copy. Recipient
copies remain theirs. Removed identities stay recorded; returning one needs a
separate deliberate reintake. Storage pressure and source withdrawal never grant
automatic deletion. **Mirror activity and decisions** retains the host's jobs.

These controls need Core's persistent-mirror API. Retaining a new copy also needs
complete public-copy review and exact retained-selection lookup; an older host
missing those contracts can still expose
its existing Stop controls. Unavailable storage is reported explicitly and is
not shown as an empty mirror. The [compatible preview](preview.md) pins these
APIs. Library itself still needs a compatible running Core, while the
[standalone mirror tools](../docs/persistent-mirrors.md) work without Skulk.

## Recover a retained mirror copy

In **Library → Retained mirror copies**, choose **Save recovery kit**. These
copies are also shown during first launch, even without private archive storage
or an installed model. The same action appears beside retained selections in
**Sharing → Keep models available**. Names and sizes in a list are hints; opening
the selection authenticates its complete signed metadata locally. This read
does not scan every model file or grant recipient trust.

Review the exact manifest, all members and original terms. Expand **Capture
signatures and trust** for the capture fingerprints and threshold. Choose
**Prepare recovery kit** to start one durable host job. Core checks every
required file and creates a separate complete kit with the original envelope,
cards, unknown extensions, companions and provenance. This works after the
original public import or Library copy receipt is gone. Preparation starts no
listener, swarm, model installation or execution, and grants no restart rights.

Close Library whenever needed. Return to the same retained copy to find its
preparation, including a receipt older than the current activity page. An
ambiguous submission can be retried as the same immutable decision; reopening
only reads its outcome. **Prepare another kit** is a separate explicit choice.
Use **Earlier recovery kits** in the recovery sheet to browse this model's
permanent preparations. Choose **Recovery kit actions** on a receipt to download
or explicitly remove only its kit, even when a newer preparation fails. Empty
filtered pages offer **Older decisions**; they do not mean all earlier kits are
gone. The same controls are in **Sharing → Mirror activity and decisions** and
remain usable after the model or original import metadata is removed. Preparing
another kit never silently removes the earlier files.

Active work exposes **Stop this preparation**. Failed or interrupted work is
not silently restarted. No timer removes kit files.

When preparation succeeds, choose **Download recovery kit**. The browser saves
the stream directly; Library never constructs a whole-model JavaScript Blob or
puts weights in control-plane JSON. GET/HEAD and a single HTTP byte range use
the prepared file's strong SHA-256 ETag; `If-Range` protects resumed downloads.
Core refuses a changed or unsealed file. A fingerprint check protects the local
prepared artifact; recipients still verify the signed contents independently.

Keep the `.foxkit` file on storage you control. It is not encrypted. Use
**Restore** in Library or the [standalone recovery tools](../docs/trust-and-recovery.md)
with independently authenticated identity, keys and threshold. Included keys
alone do not identify an author or grant registry trust. Existing registry,
owner-capture and repository-code permissions stay separate. Restoring a kit
does not require the original Core or registry; executing a model still requires
a compatible runtime and any separately retained runtime software.

To reclaim the host's prepared file, open **Remove this prepared kit…** and
confirm **Remove only this kit**. This deletes only that stopped preparation's
kit files. The retained mirror selection and files saved by recipients remain.
It is separate from **Remove mirror copy**, which has its own pin and exact
identity controls. Unavailable metadata or storage is a refusal, never an empty
result or permission to delete.

The new **Retained recovery** feature check requires the exact selection, review,
preparation, seal, download and removal API contracts. Older compatible hosts
keep their existing sharing and Stop controls and request an upgrade for these
new actions. This GUI remains a Core client; it is not yet an independent local
GUI host, frozen Mac app or APT package. Standalone core tools remain Skulk-free.

## Send and receive a model

For a kept public copy, **Share with someone** offers three paths on current Core:

- **Share with peers**: review starting contacts and a finite public session, then
  send the `.foxhandoff` file. The recipient reviews its signed identity and
  contact suggestions separately before connecting. A reachable retained provider
  is needed; Skulk is optional for recipients.
- **Prepare recovery kit for recipient**: save the complete `.foxkit`, then send
  it or put it on storage your recipient can access. No live mirror is needed.
- **Download sharing file** (or **Download invitation file** on older Core):
  supply one or more reachable HTTP(S) mirror base
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
uv tool install https://github.com/Foxlight-Foundation/foxlight-model-preservation/releases/download/library-v0.14.0/foxlight_preservation_library-0.14.0-py3-none-any.whl
foxlight-preservation-library
```

The 0.14.0 pre-release includes persistent mirror controls, PMF, followed sources, peer receiving/native setup
and one-file sharing, plus explicit I2P source/receiving controls. For exact dependency versions and offline retention, use
the [hash-locked installation archive](../docs/installation.md) instead of an
unconstrained tool install. [Verify the release](../docs/releases.md) before
executing its installer. These are public software artifacts for a development
cohort; the compatible Core remains a separately installed pinned source preview.

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
use the same Library/Guided interface and canonical Core job journal. Each mode
uses its own owner identity, so Activity in one mode does not list or control
jobs started from the other.

Source installation is also available with `python -m pip install ./capability`
from this repository. The default wheel has only public `httpx`, `pydantic` and
`PyYAML` dependencies. The optional `managed` extra uses the separately supplied SDK; the
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

Library's own screen listens only on loopback, but Core's `/preservation` API
uses Skulk's standard operator boundary: loopback, the authenticated operator
gateway, or a direct private-LAN/CGNAT peer (for example over Tailscale) that
sends no forwarding headers. Any machine your cluster already trusts that way can
call these controls directly, including folder review and starting public
sharing. Run the model-store host only on networks whose peers you trust.

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
this version with an older released wheel. Local-source/local-wheel installs
remain usable with an existing Core; preview setup requires the public Git
installation command, an official release URL, or the release helper's retained
wheel/installation evidence. The 0.12.0 release includes the PMF behavior
introduced in the earlier 0.7 source preview.


## Follow sources and find preserved models (Library 0.8 source preview)

Choose **Find**, or **Find a shared model** during Guided setup. Choose a
small `.foxsource`/JSON bootstrap file from a supplier you trust. Core inspects
it offline and shows the source identity and metadata endpoints. Confirm listing
trust, then choose **Trust source and get listings**. No central source is
preselected. HTTP and local/private metadata endpoints need separate opt-ins.
The source operator can create this file with the public standalone
[`source` commands](../docs/discovery.md); running Library or Skulk is unnecessary
for publishing, following, caching, searching or fetching with those tools.

**Search saved listings** queries only Core's protected cache. Source metadata
uses TUF update verification plus the original signed catalog. **Sources & updates**
shows freshness, accepted history and finite update jobs. Refresh is explicit;
opening Library or searching never sends your query or installed inventory to a
source. Failed updates retain verified listings. Unfollow stops using a source
and retains its rollback evidence. Stale or retained listings are labeled;
changed root authority cannot use the automatic model handoff.

Choose **Review model** beside an attributed supplier to retrieve only signed
model metadata. All companions, the exact bundle, signature threshold and keys
are shown before downloading. A listing's keys are candidate references: they
are never enrolled as registry authorities. Explicitly accept this source's
saved reference and keys for this copy, review the finite download policy, then
confirm the normal **Download this signed copy** review. Model metadata and
object-download HTTP/private-network permissions are separate from source-update
permissions. Downloads start no hosting, swarm, installation or code execution.
**Restore** subsequently checks the retained cards, signed provenance and
repository-code consent through its existing flow.

Source updates and model-detail jobs belong to Core, survive closing this page,
and are interrupted without automatic replay after Core restart. Reopen completed
model details under **Source activity**. Local search remains available with all
sources offline. The UI bounds result pages and display text; full signed
metadata remains in Core's cache. Offline cache export/import and local-file
sources are available through standalone tools; Library accepts bootstrap files
up to 256 KiB, not full cache interchange.

These source-following controls are included in Library 0.12.0 with its compatible
Core preview. Software delivery does not establish independent community custody,
anonymous retrieval or censorship-resistance qualification. Direct source and mirror requests expose
Core's network address and requested metadata/artifact to their operators.

## Receive through peers (Library 0.9–0.10 source preview)

Library 0.12.0 includes peer discovery and receiving in Library and Guided. Library 0.10 adds **Set up peer support** on a compatible Core. Review
the official source and download size, explicitly allow installation, and start.
Core installs its pinned Kubo 0.43.1 build privately, with no administrator or
PATH changes. Supported managed targets are macOS/Linux ARM64 and AMD64; existing
compatible installations remain supported. The ordinary Library launcher still
needs no managed SDK. Use `foxlight-preservation-library --check` to inspect actual
Core API support; this does not qualify the native runtime or start networking.

Setup continues when Library closes. Reopening shows the same Core-owned receipt
and a **Stop setup** action while active. Core restart marks interruption without
another download. A new attempt needs a deliberate fresh authorization; an
uncertain response reuses the original operation identity. Archive progress is
conservative and can include abandoned attempts. Software setup does not enroll
sources, configure peer contacts, accept model keys, enable code or start sharing.
Finished installation leaves peer consent unchecked and retains unsaved contact
edits. A local recheck handles a temporarily busy runtime observation. Managed
setup retains at most 64 replay receipts and refuses further jobs at capacity.
If the managed installation is later removed, the old successful receipt does
not prevent a fresh, separately authorized setup attempt. Altered or incomplete
managed directories still refuse and need operator inspection before another
installation; setup does not silently overwrite suspect local software.
The standalone [offline archive installation](../docs/peer-discovery.md#install-and-start-a-receiver)
remains available independently of Skulk. Existing PATH installation also works.

In **Find**, choose **Add a source** and import a trusted `.foxpeer` file.
Review its source identity, enter up to eight numeric starting contacts supplied
by your peer operator, and save the connection settings. Saving and browsing
stay offline. Allow peer connections separately when getting listings or
reviewing a model. Source trust authorizes listings only. Download review asks
for fresh exact-copy key acceptance, network consent and a cumulative object-byte
allowance. Routing traffic and the native block cache are outside that allowance.
Suppliers can observe Core's address and requested content; this is not an
anonymous mode.

A `.foxpeer-model` invitation also works from **Sharing → Receive** with an
independently authenticated model identity, threshold and fingerprints. Resume
retains the original trust, bytes and remaining allowance. You may review new
starting contacts within the same private-address scope; changing that scope
requires a new receive. Public and private peer scopes use separate caches.
Receiving advertises no blocks and starts no sharing. Original cards, signed
provenance, companions and the existing separate code-permission check survive
Restore.

**Sources & updates** offers **Recover pinned listings** only when the imported
source includes exact snapshots. This explicit historical path does not relax
signature checks or rollback floors. Search uses the authenticated local cache.
Activity and downloads belong to Core and survive closing Library. Stop drains
the actual native session; Core restart interrupts work without replaying it.
Peer publishing/sharing controls and direct PMF peer transport are separate
features. Standalone operator tools remain available without Skulk.


## I2P source and receiving controls

Library 0.12.0 provides these controls with its compatible Core source pin,
including I2P job contracts and the retained receiving-policy preview. Library checks actual OpenAPI fields rather
than inferring support from a version or an existing route. An older Core keeps
its direct/peer workflows; protected sources and I2P-only references cannot
silently become direct requests.

In **Find → Add a source**, choose a trusted source file, then **I2P only** under
Source connection. Review canonical `http://…b32.i2p` metadata endpoints and an
already configured numeric loopback HTTP proxy on the **Core host**, which may
be a different device from the browser. Trusting the source for listings and
allowing its I2P connections are separate choices. Confirming starts one durable
metadata update; opening Find or searching saved listings contacts no supplier.
The source retains its local I2P-only policy across page/Core restarts. Refresh
and model-detail actions bind that exact policy and refuse drift before effects.

Reviewing model details does not download weights. The model download has its
own proxy/connection consent, finite allowance and exact-copy review. Direct
HTTP(S) references can select I2P only when they contain supported I2P model
mirrors. Unsupported hints are excluded, with no direct/outproxy fallback.
Independent model-key/identity acceptance, registry provenance and repository-code
permission retain their existing separate boundaries. Downloading starts no
sharing, installation or model execution.

Activity owns progress and Stop. Resume displays the saved original local proxy,
requires fresh I2P confirmation and retains the same job, partials, trust and
remaining cumulative allowance. It cannot edit the proxy or refill the budget.
A Core restart never automatically replays the operation. Stop drains the owned
request worker; an independently operated router keeps its own lifecycle.
Retained copies and original evidence remain recoverable with the standalone
preservation tools after the original Core/source/proxy are gone.

This is a functional pilot, not an anonymity guarantee or a global Core privacy
mode. Mirrors see requested content and timing and may recognize the I2P client
destination; the selected proxy/router is a separately trusted dependency. Other
Core communications keep their existing settings. Library neither installs nor
authenticates a router, and broader public privacy/performance qualification
remains separate. The public Library launcher remains SDK-optional and uses Core
for its durable jobs; the standalone command-line preservation core needs neither
Skulk nor the SDK.
