# Run a persistent HTTP mirror

A mirror is a separately chosen public role. It follows operator-selected signed
sources, admits complete public models under independently supplied model keys
and reviewed terms, retains their original signed data, and serves verified
copies. It needs Python 3.11+ on Linux/macOS, ordinary POSIX filesystem semantics
and the standalone package. Skulk, its SDK, GPUs and hosted accounts are optional.

This implementation supplies the HTTP controller and a Linux service recipe.
Persistent torrent/IPFS adapters, generic PMF network transport and delegated
mirror discovery remain separate work. Existing finite peer/BitTorrent tools
remain usable. It makes no anonymity or censorship-resistance qualification.

## Prepare offline

Install the reviewed package as described in [release verification](releases.md).
Use a disk whose rename, fsync and flock behavior is reliable. A missing/replaced
enrolled directory is refused. Keep the owner-only state on durable storage;
retaining only weights loses source floors, policy and accounting.

```sh
model-preserve mirror init --dir /srv/sovereign/example --name 'My mirror' \
  --disk-bytes 107374182400 --ingress-daily 53687091200 \
  --egress-daily 53687091200 --egress-monthly 1099511627776
```

The parent must exist and belong to you; the selected directory must be new.
Init creates private `mirror.json`, `discovery/`, `store/`, `public/`, `scratch/`
and enrolls their device/inode identities. It opens no listener and obtains no
software or models. The default HTTP bind is `127.0.0.1:8080`. Choose
`--bind 0.0.0.0` explicitly at init for other hosts. Arrange routing/firewall/TLS
separately; a bind does not establish public reachability. HTTP clients need
explicit plaintext/private-address grants where applicable.

Obtain a source/cache file and authenticate its whole bootstrap identity
independently. Separately obtain the model-signing trust file. A source's bundled
candidate model keys are never silently accepted. Create `terms.json` with exact
license/reference pairs whose redistribution terms you have reviewed:

```json
[{"license_id":"YOUR_REVIEWED_LICENSE","terms_reference":"EXACT_SIGNED_REFERENCE"}]
```

```sh
model-preserve mirror follow --dir /srv/sovereign/example \
  --source-file chosen.foxsource --expected-bootstrap-id BOOTSTRAP_SHA256 \
  --bundle-trust model-trust.json --terms-file terms.json --confirm-redistribution
model-preserve mirror plan --dir /srv/sovereign/example
```

Follow and plan stay offline. The model-key and terms policy authorizes intake
for this mirror only; it grants no restore, registry or runtime authority. A
public label/SPDX name is not a grant of rights. Every member must have public
intake and match an explicitly reviewed license/reference pair before model
bytes are requested. Required notices and companions remain in the signed
closure. Unfamiliar cards/properties/provenance are retained unchanged.

Use `--allow-http`, `--allow-private`, and `--allow-local` on **follow** only when
those source/model connections are intended. `--max-bytes` sets the fixed
per-model object transfer allowance (default 20 GiB), including retries. The
stored grant includes all these choices. Re-following changes policy, preserves
source floors and invalidates any standing restart grant. Stop the controller
before changing sources; pause/block/pin are separate live local controls.

## Run manually, then choose restart permission separately

```sh
model-preserve mirror run --dir /srv/sovereign/example --public --seconds 3600
```

This is a fresh finite manual public grant, at most 24 hours. It refreshes source
metadata, selects at most 1,000 exact cached candidates in deterministic
mirror-specific order and admits only fresh followed-source selections. Failed
admissions retain a reason and require explicit `retry`. A full 1,000-selection
ledger refuses new intake while continuing existing retries and verification;
removed tombstones also occupy ledger slots. Status reports capacity. Retain
that ledger and use a separately configured state directory for additional
intake rather than erasing deletion/accounting history. There is no default
catalog or compulsory Foxlight key/service. Existing verified retained models
remain available when catalogs expire, withdraw entries or disappear. A completed
local pending capture can be recovered after a crash without the publisher.

The `ready` receipt identifies a local listening port; it does not mean models
have finished downloading or that the port is publicly reachable. Use status.
Every cycle re-verifies retained closures, publishes objects before envelopes,
and serves only exact manifest/object paths. There is no upload, directory
listing, admin HTTP endpoint, request-history log or implicit proxy. Public
hardlinks avoid duplicate model allocation; they are not an integrity boundary.
Clients independently verify exact identities, signatures and file hashes.

Stop with Ctrl-C, SIGTERM, or your service manager. The process retains ownership
until byte work and actual HTTP handlers drain. Stop actively shuts down accepted
sockets, including incomplete request headers, before joining handlers. An OS filesystem stall may delay
that drain; cancellation does not interrupt the filesystem. A forced process kill
releases the kernel lock and stops its in-process HTTP threads, but does not
reset reservations or authorize a restart. No separate native daemon is started.

Unattended serving requires a **separate** expiring restart grant, valid for at
most 366 days. Supply an absolute UTC Unix expiry you chose:

```sh
model-preserve mirror grant-resume --dir /srv/sovereign/example \
  --expires UTC_UNIX_EXPIRY --confirm-resume
model-preserve mirror run --dir /srv/sovereign/example --resume
```

This grant binds directory identities, sources/model keys/terms, transports,
limits and serving policy. Changes invalidate it. `--resume` refuses without it,
after expiry, clock rollback or durable pause/leave. **Do not configure a service
manager to replay `--public`**: that is a new manual grant on each invocation.
Use only `--resume` with the separate standing grant. The
[systemd template](../deploy/mirror/model-preserve-mirror@.service) does this.
Install/enable it only after reviewing paths, user, configuration and public
permission. The recipe does not create users, mount disks or grant consent.

## Observe, stop serving, retain

```sh
model-preserve mirror status --dir /srv/sovereign/example
model-preserve mirror pause --dir /srv/sovereign/example
model-preserve mirror resume --dir /srv/sovereign/example
model-preserve mirror revoke-resume --dir /srv/sovereign/example
model-preserve mirror leave --dir /srv/sovereign/example
model-preserve mirror pin --dir /srv/sovereign/example --manifest MANIFEST_SHA256
model-preserve mirror block --dir /srv/sovereign/example --manifest MANIFEST_SHA256
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
with different keys does not silently rewrite retained model authority. Block a
retained copy explicitly if you no longer wish to serve it. A catalog withdrawal,
expiry, budget shortage or missing source has no deletion power.

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
on retry, withdrawal or prune. Selected HTTP response bodies are fully reserved
and fsynced before sending; interrupted responses keep the entire reservation.
Headers, TLS/TCP overhead and HTTP error responses are outside that body budget.
Daily/monthly windows use UTC; forward rollover resets only its own window.
Clock rollback refuses new credit rather than reopening an earlier window.

An optional UTC serving window is selected at init with `--serve-start HOUR`
and `--serve-end HOUR`; equal values mean all day and overnight ranges work.
Budget/window/pause/grant refusal returns 503 with `Retry-After` and an empty
body. The server checks local policy between chunks. Metadata acquisition and
model object budgets remain distinct; no hard total wire quota is claimed.

## Delete only when you choose

Stop the controller and explicitly unblock its pin protection before physical
removal; keeping it blocked prevents serving and automatic re-admission:

```sh
model-preserve mirror block --dir /srv/sovereign/example --manifest MANIFEST_SHA256
model-preserve mirror unpin --dir /srv/sovereign/example --manifest MANIFEST_SHA256
model-preserve mirror prune --dir /srv/sovereign/example \
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
your actual filesystem. Ordinary HTTP hosting is visible and non-anonymous.
