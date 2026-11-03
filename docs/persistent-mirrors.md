# Run a persistent mirror

A mirror is a separately chosen public role. It follows operator-selected signed
sources, admits complete public models under independently supplied model keys
and reviewed terms, retains their original signed data, and serves verified
copies. It needs Python 3.11+ on Linux/macOS, ordinary POSIX filesystem semantics
and the standalone package. Skulk, its SDK, GPUs and hosted accounts are optional.

Installing the software is receive-only. Nothing is followed, served or
announced until you review one policy and consent to public serving. Public
serving is not anonymous: anyone can see and contact a public mirror, and the
sources and peers it contacts see its address. It makes no censorship-resistance
qualification.

Mirrors carry both public preservation-v1 bundles and draft
[PMF](portable-model-format.md) archives natively (see
[PMF over the network](#pmf-over-the-network)). Standalone 0.13 and later include
separately opted-in [persistent IPFS/BitTorrent sessions](persistent-peers.md).

## Quickstart: one reviewed collection file

A **collection file** bundles everything an operator must independently review
before following a source. Obtain it, and the source document it names, from
someone you trust, and check them yourself:

```json
{
  "schema_version": "sovereign.mirror-collection.v1",
  "title": "Example public models",
  "source": {"file": "example.foxsource"},
  "expected_bootstrap_id": "BOOTSTRAP_SHA256",
  "trust": {
    "schema_version": "foxlight.model-preservation.trust.v1",
    "keys": [{"key_id": "ed25519:KEY_SHA256", "public_key": "BASE64_KEY"}],
    "threshold": 1,
    "as_of": "2026-11-01"
  },
  "terms": [
    {"license_id": "Apache-2.0", "terms_reference": "EXACT_SIGNED_REFERENCE"}
  ]
}
```

| Field | Meaning |
| --- | --- |
| `schema_version` | Exactly `sovereign.mirror-collection.v1`. |
| `title` | A label shown in the review (1-200 characters). |
| `source` | Exactly one of `file` (a path beside the collection file; absolute paths and `..` are refused) or `inline` (the source document's JSON object). |
| `expected_bootstrap_id` | The whole source document's independently authenticated bootstrap ID. |
| `trust` | The model-signing trust profile. Key IDs must match their keys. |
| `terms` | 1-100 exact license/reference pairs you reviewed for redistribution. |
| `max_bytes` | Optional per-model byte limit (default: derived from the disk budget). |
| `allow_http` | Optional; `true` permits plaintext source/model connections. |

The parser is strict: duplicate keys, unknown fields and other versions refuse.
A collection grants nothing by itself. A source's own candidate keys are never
accepted; only the `trust` you reviewed is.

```sh
mkdir -m 0700 ~/sovereign-mirrors
model-preserve mirror quickstart --dir ~/sovereign-mirrors/example \
  --collection collection.json --disk-bytes 500G \
  --public-url https://mirror.example.org
```

Quickstart prints one review: source name and IDs, model key fingerprints and
threshold, accepted terms, per-model limit, every budget, listener, public URL,
announcement choices, how long the restart grant lasts, and what is exposed. It
then asks you to type `serve`. For non-interactive use, read the review with
`--plan-only` (which changes nothing) and consent with
`--yes-i-consent-to-public-serving`. Without consent nothing is written.

After consent it runs `init` (or `config` on an existing mirror), `follow` and
`grant-resume` in one step, still offline. Add `--run` to start serving
immediately under the new grant, or start the [service](#run-as-a-service).
Re-running quickstart applies a changed policy, which clears the old grant, and
records a fresh one. The default grant lasts 90 days (`--resume-for`, at most
366 days).

Sizes everywhere accept bytes or binary units: `K`, `M`, `G`, `T` and `P` are
powers of 1024 (`100G` is 100 × 1024³ bytes; `GiB`/`GB` spellings are the same).
`--name` defaults to the host name.

## Run in a container

[`deploy/mirror/`](../deploy/mirror/README.md) packages a multi-arch image
(amd64 and arm64) and a compose file with Caddy. Caddy obtains a certificate for
your domain and reverse-proxies to the mirror, which listens on loopback inside
the same network namespace. Caddy's access log stays off.

```sh
cp deploy/mirror/mirror.env.example deploy/mirror/mirror.env   # edit it
mkdir deploy/mirror/collection
cp collection.json example.foxsource deploy/mirror/collection/
docker compose --env-file deploy/mirror/mirror.env \
  -f deploy/mirror/compose.yaml up -d --build
docker compose --env-file deploy/mirror/mirror.env \
  -f deploy/mirror/compose.yaml logs mirror   # read the review
```

`SOVEREIGN_MIRROR_DOMAIN`, `SOVEREIGN_MIRROR_CONTACT`, `SOVEREIGN_MIRROR_BUDGET`,
the collection directory and `SOVEREIGN_MIRROR_CONSENT` configure it. Until
`SOVEREIGN_MIRROR_CONSENT=yes-i-consent-to-public-serving` is set, the container
runs plan-only: it prints the review and serves nothing. With consent, every
start re-applies the collection and policy, renews the grant and serves.

## Run as a service

The [systemd user unit](../deploy/mirror/model-preserve-mirror@.service) matches
the [installer](installation.md): software in `~/sovereign` (a link to the
installed release) and mirrors in `~/sovereign-mirrors/<instance>`. It only runs
`mirror run --resume`, which refuses without a current restart grant.

```sh
install -Dm0644 deploy/mirror/model-preserve-mirror@.service \
  ~/.config/systemd/user/model-preserve-mirror@.service
loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable --now model-preserve-mirror@example
```

The intended layout keeps the mirror on loopback behind a TLS reverse proxy
(such as Caddy) that you run separately. The Python server does not terminate
TLS itself.

## Change configuration later

```sh
model-preserve mirror config --dir ~/sovereign-mirrors/example \
  --disk-bytes 2T --egress-monthly 4T --port 8081
```

`config` changes the name, budgets, bind (`127.0.0.1`, `::1`, `0.0.0.0` or `::`),
port, UTC serving window, refresh interval, public URL, contact and announcement
choices. Stop the controller first. Any effective change alters the policy
identity and clears the standing restart grant; grant it again after review.
Retained bytes are never touched. Per-model limits that were derived from the
old disk budget follow the new one; limits set explicitly stay.

An announcement policy and the deferred/withdrawn receipts below are written to
the owner state only when used, so an unchanged mirror keeps its exact encoding
and restart grant across this upgrade. A release that predates them refuses such
a ledger rather than silently ignoring it.

## Announce the mirror

With a public URL, a running mirror signs a short-lived (7-day) mirror card with
its own mirror key (`mirror-key.pem` in the mirror directory, never a model or
source key). The card names the URL, an optional contact, the followed catalog
IDs and a filter of the verified selections it serves. It is published, with
peers this mirror has spot-checked, at `/.well-known/sovereign/peers.json`. Each
cycle the mirror learns peers from followed catalogs' mirror hints, checks that
they serve bytes they claim, and republishes. Gossip is charged 64 MiB of ingress
per round and skipped when the daily budget cannot cover it. See
[mirror discovery](mirror-discovery.md) for how clients use cards.

HTTPS is preferred; an `http://` URL is accepted with a warning. Two further
choices are separate opt-ins, each needing `--confirm-public-announcement`
because they publish the address beyond this mirror:

- `--announce-nostr` publishes the card to public Nostr relays.
- `--announce-dht` announces the address on the public BitTorrent DHT (needs the
  `bittorrent` extra). The DHT can only express a plain-HTTP root URL, so an
  HTTPS URL behind a proxy is not announced there.

`mirror config --clear-public-url` stops announcing and removes the peers
document; an already published card lapses when it expires. The desktop
community mirror announces the same way. Bucket mirrors are static: they publish
no card of their own and do not gossip.

## Takedown: withdrawal versus expiry

A source withdraws a model by publishing a new signed catalog without it. When
the selection's own source has a current (authenticated, unexpired) catalog that
no longer lists it, and no other followed source's current catalog lists it,
the mirror records the selection as `withdrawn` and stops serving it on the next
cycle: its public links are removed and requests are refused. Retained bytes
stay under the ordinary retention rules (pin, block and explicit prune). If a
current catalog lists it again, it is served again.

Expiry is different. A catalog that merely expires, a source that cannot be
reached, or a refresh that fails says nothing about withdrawal: that is an
availability problem, and retained verified copies keep serving. To stop serving
one model yourself, use `mirror block`.

## Budgets that defer, failures that wait

When the daily ingress budget or the disk budget cannot admit a selection now,
it is recorded as **deferred**, not failed. A later cycle retries it
automatically once an offline estimate says the budget could admit it; until
then no supplier is contacted. Reservations already made stay charged and are
never refunded or charged twice. Raising a budget with `mirror config` lets the
next cycle continue. Every other failure (signatures, terms, corrupt bytes, the
per-model limit) stays a recorded error until an explicit `mirror retry`.

`mirror retry --adopt-source-grant` (with the controller stopped) replaces a
selection's original keys, terms and limit with its source's current grant,
after you re-followed that source with a changed policy. A pinned original
wrapper must still authenticate under the adopted keys and terms. A changed limit
starts a new, separately charged ingress operation.

## Set up manually

Quickstart wraps these steps; they remain available individually. Use a disk
whose rename, fsync and flock behavior is reliable. A missing/replaced enrolled
directory is refused. Keep the owner-only state on durable storage; retaining
only weights loses source floors, policy and accounting.

```sh
model-preserve mirror init --dir ~/sovereign-mirrors/example \
  --disk-bytes 100G --ingress-daily 50G --egress-daily 50G --egress-monthly 1T
```

The parent must exist and belong to you; the selected directory must be new.
Init creates private `mirror.json`, `discovery/`, `store/`, `public/`, `scratch/`
and enrolls their device/inode identities. It opens no listener and obtains no
software or models. The default HTTP bind is `127.0.0.1:8080`. Choose
`--bind 0.0.0.0` or `--bind ::` explicitly for other hosts. Arrange
routing/firewall/TLS separately; a bind does not establish public reachability.
HTTP clients need explicit plaintext/private-address grants where applicable.

Obtain a source/cache file and authenticate its whole bootstrap identity
independently. Separately obtain the model-signing trust file. A source's bundled
candidate model keys are never silently accepted. Create `terms.json` with exact
license/reference pairs whose redistribution terms you have reviewed:

```json
[{"license_id":"YOUR_REVIEWED_LICENSE","terms_reference":"EXACT_SIGNED_REFERENCE"}]
```

```sh
model-preserve mirror follow --dir ~/sovereign-mirrors/example \
  --source-file chosen.foxsource --expected-bootstrap-id BOOTSTRAP_SHA256 \
  --bundle-trust model-trust.json --terms-file terms.json --confirm-redistribution
model-preserve mirror plan --dir ~/sovereign-mirrors/example
```

Follow and plan stay offline. The model-key and terms policy authorizes intake
for this mirror only; it grants no restore, registry or runtime authority. A
public label/SPDX name is not a grant of rights. Every member must have public
intake and match an explicitly reviewed license/reference pair before model
bytes are requested. Required notices and companions remain in the signed
closure. Unfamiliar cards/properties/provenance are retained unchanged.

Use `--allow-http`, `--allow-private`, and `--allow-local` on **follow** only when
those source/model connections are intended. `--max-bytes` sets the fixed
per-model object transfer allowance, including retries; by default it is the
disk budget less 256 MiB of headroom, so any model the disk could hold is
admitted. The stored grant includes all these choices. Re-following changes
policy, preserves source floors and invalidates any standing restart grant. Stop
the controller before changing sources; pause/block/pin are separate live local
controls.

## Retain a public copy you already have

`mirror retain` copies one exact public preservation-v1 or PMF archive into this mirror
without a catalog, registry, source publisher or network connection. Supply the
exact manifest, independently accepted model keys, and terms you reviewed for
every member:

```sh
model-preserve mirror retain --dir ~/sovereign-mirrors/example \
  --store /path/to/verified-public-archive --manifest MANIFEST_SHA256 \
  --bundle-trust model-trust.json --terms-file terms.json \
  --confirm-redistribution
model-preserve mirror status --dir ~/sovereign-mirrors/example
```

A public kit can first be imported using the ordinary independently trusted
`kit-import` workflow. The input must already declare every member public; this
command does not turn a private capture into a public publication or establish
redistribution rights. It authenticates the selected manifest and terms, copies
and verifies all required objects, and keeps the exact original signed wrapper,
cards and companions. It neither starts sharing nor imports a source's keys or
network permissions. Later HTTP/IPFS/BitTorrent sessions use the same retained
selection and require their normal explicit serving policy.

Local admission upgrades owner state to `foxlight.mirror-state.v3`; older tools
refuse that profile. Existing v1/v2 state remains readable without migration.
The selection records local custody rather than inventing a source identity. Its
original wrapper hash and independently accepted keys/terms are immutable. A new
selection clears standing restart permission; grant it again deliberately after
review. Repeating the same completed selection preserves its pin, block and
existing policy. Changed keys, terms or wrapper bytes cannot replace its accepted
authority silently.

Stop the controller before admission. Disk limits include retained and partial
copies; local copying spends no network ingress allowance. A failed copy stays
unserved and needs `mirror retry --manifest ...` followed by the same `mirror
retain` command. The original remaining limits and deletion tombstones stay in
place. The same explicit retry can repair a damaged destination wrapper only
from bytes matching the originally pinned wrapper hash and accepted authority.
A complete selected closure interrupted immediately before its receipt
can recover offline; incomplete local custody never acquires a catalog/network
repair route. Source loss never deletes this copy. Ordinary block/pin/pause and
explicit stopped-owner prune still apply.

Before copying, the owner privately retains the authenticated wrapper under
`scratch/local-intake/`. This is selection metadata, not a published complete
archive or an HTTP route. It permits explicit prune of partial local custody
after input loss. Prune rechecks its exact hash, signatures and terms and protects
objects shared by other complete or partial local selections. Its write-ahead
deletion journal remains resumable if removal is interrupted.
The signed metadata is durable before the selection enters the ledger, so a
crash can leave unselected private metadata but no admitted partial selection
without its authenticated file table. Unselected metadata owns no model objects,
cannot be served, and counts toward disk usage.

A removed local selection stays a tombstone: ordinary `mirror retry` refuses
instead of turning it into a partial copy without metadata. To deliberately
copy it again, repeat its original `mirror retain` command with the separate
`--confirm-reintake` flag. The original keys, terms, wrapper, block and accounting
remain; restart consent is cleared. Unblock and grant serving separately if
desired. Retrying one removed selection cannot prevent pruning a different model.

### PMF over the network

A PMF archive travels under its own identity, never converted into another
one. It uses the same `manifests/<id>.json` and `objects/sha256/<hash>` layout as
a preservation-v1 bundle. Fetch, `mirror retain`, source following, serving,
prune and recovery all read the signed payload type and verify that format; a
failed PMF verification is never retried as another format. A PMF archive's
objects are its member files plus every source document (cards, the original
signed preservation envelope and any optional profile records), so each is
fetched, hashed and served like a model file. Prune protects objects shared
between formats.

The public intake rule is unchanged: every PMF member must declare public
intake under terms you reviewed. A private owner backup is refused before any
mirror change, so a private copy is never served unchanged; publish a public
copy first. PMF remains a draft: an incompatible change needs a new payload type.

## Original source metadata before downloading

From standalone 0.20, source-fed admission authenticates the original wrapper and
every member's public terms before object work. It durably writes that exact
wrapper under private `scratch/source-intake/`, then pins its digest and a small
authority record to the original selection. The source ID, catalog ID and entry,
mirror hints, independently accepted model trust/terms, operation and original
allowance remain bound. Historical source selections have no invented catalog ID.
Retry reuses this original wrapper; it cannot silently substitute another valid
supplier's signatures or normalize away original metadata.

Private metadata and partial objects count toward disk usage. Metadata publication
or owner-state failure prevents object receiving. A crash can leave an unpinned
private record; it grants no recovery, object ownership or serving authority.
Only a verified complete closure publishes a store manifest. Inspection/recovery
still needs the accepted owner ledger, and partial bytes cannot become a kit.

Owner-state **v5** makes this custody explicit. Older owners refuse it rather than
ignoring unfinished source references while pruning another model. Existing v1-v4
wire encodings and restart identities remain unchanged until explicit source
configuration, manual startup or a renewed restart grant upgrades the owner.
Legacy unattended resume refuses before listeners/network workers; grant restart
again with the current tool or start manually. Native/local/HTTP settings preserve
v5, including disabled HTTP. Normal source intake within an already approved v5
policy retains that grant; source following still grants no new model trust.
There is no downgrade of an upgraded owner directory. Unchanged recovery kits and
portable complete stores continue to work with older compatible archive readers.

## Inspect and recover retained copies

A retained model does not depend on its original catalog, publisher or Library
import record. Inspect an exact identity directly, regardless of list pages:

```sh
model-preserve mirror selection --dir ~/sovereign-mirrors/example \
  --manifest MANIFEST_SHA256
model-preserve mirror inspect --dir ~/sovereign-mirrors/example \
  --manifest MANIFEST_SHA256
model-preserve mirror export-kit --dir ~/sovereign-mirrors/example \
  --manifest MANIFEST_SHA256 --output /separate-disk/model.foxkit
```

`selection` reads bounded local state and distinguishes an unknown selection
from pending, retained and deliberately removed selections. An unavailable or
invalid owner ledger refuses; it is not reported as an empty mirror. This read
does not rely on source freshness, an application receipt or pagination.

`inspect` authenticates the bounded original signed manifest against this
selection's accepted keys, exact identity and every member's reviewed public
terms. It returns all original card data, opaque extensions, companions and
notice metadata without opening or hashing weight objects. A pending local or
v5 source intake can supply its authenticated private intent before publication;
inspection does not claim its payload is complete or uncorrupted. Removed
selections cannot supply inspection metadata after deliberate deletion.

If interruption left a selection **pending** although its complete bytes are
already on disk, stop the mirror controller, then recover that exact selection
without starting a service or contacting a source:

```sh
model-preserve mirror recover --dir ~/sovereign-mirrors/example \
  --manifest MANIFEST_SHA256 --progress
```

`recover` authenticates the original model identity, accepted keys and every
member's reviewed public terms, then hashes every actual object. A local or v5
source write-ahead record can restore the exact original wrapper if the crash
preceded its publication. This works after the original input, publisher,
registry or catalog disappears. Missing or damaged bytes refuse recovery and
remain on disk; this command does not fetch, retry a transfer, sign, delete,
unblock or re-admit a removed selection. Complete pending copies become retained
and can use `export-kit` below. Their pin/block settings, accepted policy and
resource accounting stay unchanged; the old failure and restart grant are
cleared. Sharing requires a subsequent explicit Start or new restart grant.
An already retained copy is reverified without rewriting its receipt. Progress
is bounded metadata on stderr; Ctrl-C/termination cooperatively stops hashing.
Cancellation before the completion receipt keeps the selection pending. A crash
between wrapper publication and receipt commit can leave a complete pending
store; rerun this same offline command rather than deleting it. A historical
source intake that lost its wrapper before this feature cannot reconstruct
provenance from bare weights; recovery correctly refuses that missing evidence.

Application hosts can run `mirror_recovery.recover_selection` in a durable
cancellable worker. Inspection remains metadata-only and does not perform this
promotion implicitly. Library 0.15 provides the pending-copy action on compatible
Core hosts. The new source write-ahead path requires a host using standalone 0.20;
installing this CLI does not upgrade an existing Core host or its pinned library.

`export-kit` requires a retained selection and creates a new self-contained
[recovery kit](protocol.md#self-contained-recovery-kits-v1). It preserves the
exact original signed wrapper and streams every selected object through
length/hash checks. It refuses a
replaced enrolled store, changed wrapper, unsatisfied signer/terms policy,
damaged bytes or an existing output file. Cancellation or a failed copy leaves
no published partial kit. It does not need to stop HTTP/native sharing, alter
budgets or restart permission, sign new provenance, delete retained copies or
start network participation. Concurrent removal can make preparation refuse;
an already exported separate kit remains under its own operator's control.

The kit's public keys are candidates. A recipient can inspect its fingerprints,
then import with independently selected keys and the exact identity into a new
destination:

```sh
model-preserve kit-inspect --kit model.foxkit
model-preserve kit-import --kit model.foxkit \
  --expected-manifest-id MANIFEST_SHA256 --trust independently-accepted-keys.json \
  --output /path/to/new-recovered-archive
```

The kit profile also supports a deliberate first-contact key decision, which
does not establish publisher identity. Model, source, registry and runtime
authority remain separate. No private signing key or mirror policy is exported.
These tools recover both public preservation-v1 and PMF selections; a PMF
selection exports as a PMF (v2) recovery kit.


## Run manually, then choose restart permission separately

HTTP serving is a separate choice from native peer transports. It remains enabled
for existing mirrors. To run IPFS/BitTorrent without an HTTP listener, stop the
controller and choose:

```sh
model-preserve mirror http --dir ~/sovereign-mirrors/example --disable
```

Then explicitly configure at least one native transport as described in
[persistent peers](persistent-peers.md). HTTP can be restored with `--enable`.
Either change is offline, retains copies/caches/accounting and clears standing
restart permission. It upgrades owner state to v4; older tools refuse rather
than ignoring a disabled transport. Existing v1/v2/v3 state and its policy
identities remain unchanged until this explicit configuration is selected.
Disabling HTTP serving changes no source-update/download permissions, starts no
router, and is not a privacy mode. It cannot stop an independently operated
static server or recall copies another operator retained.

```sh
model-preserve mirror run --dir ~/sovereign-mirrors/example --public --seconds 3600
```

This is a fresh finite manual public grant, at most 24 hours. It refreshes source
metadata, selects at most 1,000 exact cached candidates in deterministic
mirror-specific order and admits only fresh followed-source selections. Failed
admissions retain a reason and require explicit `retry`; budget refusals are
[deferred](#budgets-that-defer-failures-that-wait) instead. A full
1,000-selection ledger refuses new intake while continuing existing retries and
verification; removed tombstones also occupy ledger slots. Status reports
capacity. Retain that ledger and use a separately configured state directory for
additional intake rather than erasing deletion/accounting history. There is no
default catalog or compulsory Foxlight key/service. Existing verified retained
models remain available when catalogs expire or disappear; entries
[withdrawn](#takedown-withdrawal-versus-expiry) from every current followed
catalog stop being served. A completed pending local or v5 source capture can be
recovered without its publisher once all original objects are present.
Receiving remaining objects still requires the original source freshness, route
permissions and bounded operation policy.

The `ready` receipt identifies a local listening port; it does not mean models
have finished downloading or that the port is publicly reachable. Use status.
With HTTP disabled, startup reports `port: null` and `http_enabled: false` for
the owned controller, without constructing an HTTP listener. Native publication
readiness remains a separate observation. Running with neither HTTP nor a
selected native transport refuses before starting byte workers or networking.
Startup reads and validates the selected policy under the owner lock, so a
completed configuration change cannot leave an old transport choice in effect.
Every cycle re-verifies retained closures, publishes objects before envelopes,
and serves only exact manifest/object paths and the peers document. There is no
upload, directory listing, admin HTTP endpoint, request-history log or implicit
proxy. Public hardlinks avoid duplicate model allocation; they are not an
integrity boundary. Clients independently verify exact identities, signatures
and file hashes.

Stop with Ctrl-C, SIGTERM, or your service manager. The process retains ownership
until byte work and actual HTTP handlers drain. Stop actively shuts down accepted
sockets, including incomplete request headers, before joining handlers. An OS
filesystem stall may delay that drain; cancellation does not interrupt the
filesystem. A forced process kill releases the kernel lock and stops its
in-process HTTP threads, but does not reset reservations or authorize a restart.
No separate native daemon is started.

Unattended serving requires a **separate** expiring restart grant, valid for at
most 366 days:

```sh
model-preserve mirror grant-resume --dir ~/sovereign-mirrors/example \
  --for 365d --confirm-resume
model-preserve mirror run --dir ~/sovereign-mirrors/example --resume
```

`--expires UTC_UNIX_EXPIRY` remains available instead of `--for`. This grant
binds directory identities, sources/model keys/terms, transports, limits,
listener, announcement and serving policy. Changes invalidate it. `--resume`
refuses without it, after expiry, clock rollback or durable pause/leave. **Do not
configure a service manager to replay `--public`**: that is a new manual grant on
each invocation. Use only `--resume` with the separate standing grant, as the
[service](#run-as-a-service) and the container do. The recipes do not create
users, mount disks or grant consent.

## Observe, stop serving, retain

Use `mirror run --observe` with the ordinary explicit `--public` or `--resume`
choice for a live JSON-lines activity feed. The initial startup receipt remains
first; additional `mirror_status` events contain `running`, `http_port` and
`native_workers`. Events are emitted at most once per second, plus a final
`running: false`/zero-worker event after owned work and listeners drain. The
final command receipt follows it. No peer/request history is collected.

An HTTP port describes the owned local listener. Native workers include
preparation, serving and draining; their count is not a reachable replica count.
Saved publication IDs/ports in offline status are historical data, not live
reachability evidence. The Python `run_mirror` API exposes the same typed feed
through its optional `observe` callback. Callbacks run synchronously, must be
short, and confer no permission; a callback failure stops and drains the role.

A trusted Python embedding host can supply `native_peer_runner=serve_peer` from
`foxlight_model_preservation.peer_runtime` to `run_mirror`. That host must own a
dedicated process group, provide its own parent-EOF lifetime fence, and confirm
the whole group has drained before reporting Stop. The adapter runs the qualified
native executable directly, so a frozen application need not emulate Python's
`-m` module launch. Native command helpers stay in their caller's group. This
programmatic choice is never read from cards, archives or source listings and
does not change explicit participation, profile checks, finite limits, original
evidence or operator retention/deletion. Ordinary callers retain the default
standalone subprocess supervisor; no Skulk dependency is introduced.

```sh
model-preserve mirror status --dir ~/sovereign-mirrors/example
model-preserve mirror pause --dir ~/sovereign-mirrors/example
model-preserve mirror resume --dir ~/sovereign-mirrors/example
model-preserve mirror revoke-resume --dir ~/sovereign-mirrors/example
model-preserve mirror leave --dir ~/sovereign-mirrors/example
model-preserve mirror pin --dir ~/sovereign-mirrors/example --manifest MANIFEST_SHA256
model-preserve mirror block --dir ~/sovereign-mirrors/example --manifest MANIFEST_SHA256
```

Status/plan read local state and authenticated cached listings. They contact no
suppliers. `controller_active` observes the kernel lock; it is not a public
reachability or independent-custody claim. Status includes original admission
policy, reasons, last verification, last cycle, UTC window labels, conservative
reservations and logical disk usage.

Pause stops public work; resume clears the pause but does not itself start a
listener or supply a missing restart grant. Leave also revokes restart consent.
Block stops new responses for that model, with in-flight chunk checks; shared
objects may remain served for another unblocked model. Every already reserved
response stays charged. Pin protects the retained copy from explicit prune.
None of these operations deletes models, public verification data or partial
work. `unblock`, `unpin` and `retry` are explicit matching controls. Retry keeps
the original trust, sources, policy, operation and remaining allowance; it never
refills the operation's spent credit.

A saved copy's terms and trust remain its original accepted policy. Re-following
with different keys does not silently rewrite retained model authority; adopt
the new grant explicitly with `mirror retry --adopt-source-grant`. Block a
retained copy explicitly if you no longer wish to serve it. A catalog withdrawal
stops serving but, like expiry, a budget shortage or a missing source, has no
deletion power.

## Budgets and windows

Disk admission counts regular logical bytes once per inode, including state,
source cache, retained objects, partial work and staging. Shared objects and
public hardlinks are not charged twice. New model admission checks missing
bytes plus 256 MiB headroom, both against the configured budget and observed free
space. It refuses new work at capacity; it performs no automatic eviction or
storage purchase. Admission cannot reserve space against unrelated writers or
supply a hard filesystem quota. Enforce hard limits with operator OS/provider
controls. The controller has no native torrent/IPFS blockstore in this release.

Ingress reserves bounded metadata headroom before first contact, then the
per-model object retry allowance before fetching objects, plus 32 MiB before each
source refresh. Object allowance scales to three complete copies plus 16 MiB,
capped by the reviewed maximum. The exact allowance is retained across retries.
These are intentionally conservative reservations, not received-byte counters.
They include unused and uncertain credit, survive crashes and are not refunded
on retry, deferral, withdrawal or prune. Selected HTTP response bodies are fully reserved
and fsynced before sending; interrupted responses keep the entire reservation.
Headers, TLS/TCP overhead and HTTP error responses are outside that body budget.
Daily/monthly windows use UTC; forward rollover resets only its own window.
Clock rollback refuses new credit rather than reopening an earlier window.

An optional UTC serving window is selected at init or with `mirror config`
using `--serve-start HOUR` and `--serve-end HOUR`; equal values mean all day and overnight ranges work.
Budget/window/pause/grant refusal returns 503 with `Retry-After` and an empty
body. The server checks local policy between chunks. Metadata acquisition and
model object budgets remain distinct; no hard total wire quota is claimed.

## Delete only when you choose

Stop the controller and explicitly unblock its pin protection before physical
removal; keeping it blocked prevents serving and automatic re-admission:

```sh
model-preserve mirror block --dir ~/sovereign-mirrors/example --manifest MANIFEST_SHA256
model-preserve mirror unpin --dir ~/sovereign-mirrors/example --manifest MANIFEST_SHA256
model-preserve mirror prune --dir ~/sovereign-mirrors/example \
  --manifest MANIFEST_SHA256 --confirm-manifest MANIFEST_SHA256
```

Prune requires an exact confirmation, a blocked unpinned model and an idle service
lock. It verifies other retained manifests before deleting unshared objects;
shared bytes remain. Unknown store manifests refuse deletion. It writes and
fsyncs `prune.json` before effects. An interrupted prune blocks serving and
requires rerunning the same confirmed command; restart never completes deletion
implicitly. Do not delete the journal to bypass that fence. The completed
operator removal leaves a tombstone, preserving accounting/audit receipts and
preventing catalog updates from quietly re-downloading the removed model.

Do not attach an unmanaged static server directly to `public/` if you expect the
controller's live pause/grant/budget enforcement. A static host supplies its own
policy and quotas. Retain the complete state/software/trust and test recovery on
your actual filesystem. Ordinary HTTP hosting is visible and not anonymous.

## Slow recipients and upload reservations

The HTTP mirror reads storage in bounded chunks and writes the network in
16 KiB segments. A slow recipient can continue making progress without having
to drain a 1 MiB socket write within the 30-second socket timeout. A stalled
write still times out; worker concurrency stays bounded. During a slow transfer,
the handler rechecks serving authority between writes at least every 0.1 seconds
of active processing and at each disk-chunk boundary. A blocked write can delay
that check until it finishes or times out; controller Stop also interrupts owned
connections. There is no guarantee that bytes already buffered in the transport
can be recalled.

Each response reserves its full body allowance before any body is sent. A
disconnected, paused or revoked response retains that reservation; range retries
need allowance for their own remaining response. The counter is conservative
budget usage, not actual peer receipt or wire traffic. None of these events
deletes retained models.
