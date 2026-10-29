# Find sources and model providers through independent peers

The optional standalone adapter uses stock **Kubo 0.43.1**, IPFS/libp2p's DHT,
IPNS and content-addressed UnixFS directories. It requires no Skulk, Foxlight
account, registry, model hub, HTTP gateway or delegated routing service.
It introduces no peer wire protocol, custom DHT or custom transport signatures.
The existing opt-in BitTorrent transport remains available separately.

This is source following and provider discovery. Keyword search runs over
catalogs you have explicitly followed and authenticated; there is no global
keyword index or automatic enrollment of unknown publishers.

## Retain three independent references

1. An independently authenticated peer source file and its SHA-256 identity.
   It binds an IPNS name, the original TUF source root and optional immutable
   snapshot CIDs. The file's own download cannot authenticate its own digest.
2. One or more reachable numeric libp2p peer contacts. These are routing hints,
   not trusted model signers. They can be replaced without rotating identity.
3. The exact model manifest and independently trusted model signing keys.
   A source, peer or mirror cannot enroll these keys for you.

Retain these references, recovery kits and platform-compatible verifier wheels
outside the original Core/registry. No discovery system can recover absent
bytes: someone must retain and serve the model and source snapshots. A fresh
node must have a reachable contact. Default service shutdowns do not remove a
protocol dependency, but losing every retained contact still prevents discovery.

## Install and start a receiver

Install from this repository; there is no PyPI release:

```sh
python -m pip install '.[peer]'
```

Review and install the pinned native support in private per-user storage:

```sh
model-preserve peer-runtime-plan
model-preserve peer-runtime-install --allow-runtime-download
```

This supports macOS/Linux ARM64 and AMD64, needs no administrator access and
changes no PATH or system packages. The offline plan reports the exact version,
download size, SHA-512 archive identity, SHA-256 executable identity and fixed
official download origins. Install verifies those packaged identities rather
than trusting a checksum fetched during installation. If the first official
origin is unavailable, the second can supply the same pinned archive. HTTPS
redirects are restricted to approved official distribution hosts. No installers,
hooks or extracted binaries run during acquisition. Publication is atomic;
interrupted work is not usable. Normal failures remove staging; a process crash
can leave private `.native-pending-*` scratch directories, which can be removed
when no installation is running. Never remove the lock file during a running
installation. The next explicit invocation starts again; partial downloads are
not resumed. Verified existing generations are reused without another download.

For an offline computer, bring the exact official archive reported by the plan:

```sh
model-preserve peer-runtime-install --archive ./kubo_v0.43.1_linux-amd64.tar.gz
```

The default native directory is `~/.local/state/foxlight-preservation/runtime`;
`--runtime-directory` chooses another private local directory. The CLI detects
the default managed executable automatically and rehashes it before selection.
With a custom directory, pass its returned executable using `--ipfs`.
Explicit `--ipfs` and an existing PATH installation remain supported and are
checked for the exact qualified version. A damaged managed installation refuses;
inspect/remove that generation offline before reinstalling. Acquiring software
does not initialize a peer repository, connect to peers, grant model trust or
start sharing. It does not adopt an existing IPFS repository.

Acquisition has a five-minute cooperative deadline, bounded archive/executable
sizes, and a free-space check for staging plus a reserve. OS DNS/filesystem
stalls may outlast the deadline. This software transfer is separate from model
object allowances; interrupted attempts may download the archive again. Native
repository caches and model transfers retain their separate documented limits.
See the [official Kubo release](https://github.com/ipfs/kubo/releases/tag/v0.43.1)
for upstream distributions and notices. Broader native platform and public-network
qualification are distinct from verifying distribution identities.

Replace `PEER_MULTIADDRESS` with a reachable standard contact such as
`/ip4/PUBLIC_IP/tcp/PORT/p2p/PEER_ID`. Numeric IPv4/IPv6 TCP addresses are accepted;
DNS, URL, gateway and delegated routing contacts are not interpreted. Keep the
native repository in a dedicated owner-only directory on local POSIX storage.
Its parent must already exist.

```sh
model-preserve peer-init --repo ./receiver-peer --mode receive \
  --bootstrap-address PEER_MULTIADDRESS
model-preserve peer-run --repo ./receiver-peer --allow-peer-network --seconds 3600
```

Run subsequent commands in a second terminal while that bounded session runs.
Use `--ipfs /absolute/path/to/ipfs` on native commands if Kubo is not on PATH.
Initialization is offline. Starting the node is separate explicit network
participation. No default Foxlight/IPFS bootstrap list, mDNS, HTTP retrieval,
public gateway, delegated router or delegated IPNS publisher is enabled. Owned
nodes in either mode run no circuit-relay or AutoNAT service for other peers.
Private/LAN contacts need `peer-init --allow-private-peers`, which also permits
connections to private peer addresses discovered by the routing layer. Without
it, the node neither dials nor announces private/loopback addresses. Profiles
created by earlier releases keep their recorded configuration; create a new
profile to adopt these settings.

A receive profile disables **both Bitswap block serving and provider
advertisement**. It can retain blocks in its private native cache without
sharing them. It still exchanges routing/control traffic and exposes its
network address to peers. A download command neither starts a node nor changes
its lifetime; stop its separate `peer-run` session when finished.

## Follow, update, search and receive

```sh
model-preserve peer-inspect --source-file community.foxpeer
model-preserve peer-follow --source-file community.foxpeer \
  --expected-peer-id INDEPENDENTLY_AUTHENTICATED_PEER_FILE_SHA256
model-preserve peer-refresh --source SOURCE_ID --repo ./receiver-peer \
  --allow-peer-network
model-preserve source-search 'offline writing'
model-preserve source-list
model-preserve peer-fetch --source SOURCE_ID --entry ENTRY_ID \
  --expected-manifest-id MANIFEST_SHA256 --bundle-trust model-trust.json \
  --store ./received --repo ./receiver-peer --allow-peer-network \
  --max-bytes 21474836480
model-preserve verify MANIFEST_SHA256 --trust model-trust.json --store ./received
model-preserve restore MANIFEST_SHA256 --trust model-trust.json \
  --store ./received --output ./restored
```

All source commands accept `--cache /private/cache`. Following and inspection
are offline. Refresh asks Kubo to resolve the exact pinned IPNS public-key name,
then obtains `source.json` from the returned immutable root or trusted snapshot
fallbacks. IPNS authenticates its pointer; **stock TUF Updater** independently
checks the original root, thresholds, root rotation, expiry and retained
rollback/equivocation floors before accepting a fresh catalog. A compromised
IPNS publisher cannot replace the TUF authority. Retained HTTP source hints
are never consulted by peer refresh or peer fetch.

Search/list use the same offline cache as ordinary source following. Selecting
an exact result remains separate from trusting its model signer. Peer fetch
checks the exact signed manifest before requesting model objects, then verifies
all declared files and companions before publishing a complete archive.
Original envelopes, cards and registry evidence remain byte-for-byte intact;
transport does not confer registry trust, upstream authorship or code consent.
Changing an entry's manifest requires a new explicit selection.

This receiver transfers **public preservation-v1 and PMF archives**, as ordinary
source fetch does; the signed payload type selects verification. Skulk can restore the
resulting archives/kits through its existing workflow. The separate [Library 0.9
source preview](../capability/README.md#receive-through-peers) adds peer file
import, offline setup, explicit updates and metadata review, bounded receiving,
Activity and resume through a compatible Core. Kubo 0.43.1 is a separately
installed prerequisite on that Core host. Core owns finite native sessions and
drains them before terminal receipts. The standalone adapter requires no Skulk
or managed SDK.

## Historical recovery after publisher loss

IPNS records expire and the original publisher may no longer renew them. Source
operators should publish a source file with reviewed immutable snapshot CIDs,
and several peers should explicitly retain the same snapshots. The IPNS private
key is not needed to keep serving those immutable bytes.

```sh
model-preserve peer-refresh --source SOURCE_ID --repo ./receiver-peer \
  --allow-peer-network --historical
model-preserve source-search 'offline writing'
```

`--historical` tries only snapshot CIDs pinned in the enrolled source file. It
never imports an arbitrary expired snapshot chosen by a mutable name. Signed
historical evidence remains `imported` (or `stale` if expired), not fresh; it
cannot lower previously observed TUF floors or bypass known root revocations.
It permits explicit recovery of an independently trusted exact model, not a
claim that a dead publisher's catalog is current. Retain the protected cache
itself to preserve every receiver floor, including failed-update progress.
Unknown withheld revocations cannot be inferred.

## Publish models and a source

Use a **different** dedicated mirror profile. Publication and ongoing serving
both require explicit consent; a recipient file cannot grant either.

```sh
model-preserve peer-init --repo ./public-peer --mode mirror \
  --bootstrap-address PEER_MULTIADDRESS
model-preserve peer-run --repo ./public-peer --allow-peer-network \
  --share-public --seconds 3600 --max-upload-bytes 1073741824
```

In another terminal, publish a fully verified selected archive. Every member
must already have public intake approval. Only that archive's signed envelope
and exact objects enter the native pin set; unrelated store files do not.

```sh
model-preserve peer-model-publish --repo ./public-peer --allow-peer-network \
  --public-model --store ./preserved --manifest MANIFEST_SHA256 \
  --trust model-trust.json
model-preserve peer-status --repo ./public-peer --allow-peer-network
```

The model result includes a canonical `ipfs://CID` mirror hint. Place it in the
ordinary v1 catalog entry's `mirrors`, then sign/publish the source using the
[existing source operator tooling](discovery.md#publish-a-source-on-ordinary-storage).
No new catalog version is needed: old HTTP-only clients refuse this unsupported
transport instead of treating its hint as authority. A catalog can retain HTTPS
and IPFS hints together.

`peer-status` supplies this repository's public IPNS name. Wrap the original
trusted `source.json` bootstrap with that name, and follow it in the publisher
cache before publication:

```sh
model-preserve peer-source-file --source-file original-source.json \
  --ipns-name IPNS_PUBLIC_KEY_NAME --output community-initial.foxpeer
model-preserve peer-follow --source-file community-initial.foxpeer \
  --expected-peer-id REVIEWED_PEER_FILE_SHA256
model-preserve peer-source-publish --source SOURCE_ID --repo ./public-peer \
  --allow-peer-network --public-metadata --metadata-directory ./public-source
```

`--metadata-directory` checks a locally produced static TUF tree with the same
Updater before publishing; omit it when an already verified source cache is
available. No HTTP hosting is required. Publication pins a deterministic
`source.json` export and updates the dedicated IPNS name with a 48-hour record.
Retain its returned snapshot CID in the distributable source file:

```sh
model-preserve peer-source-file --source-file original-source.json \
  --ipns-name IPNS_PUBLIC_KEY_NAME --snapshot-cid SNAPSHOT_CID \
  --output community.foxpeer
```

Authenticate the **final** file identity to recipients through a trusted channel.
Adding fallback CIDs changes that identity. IPNS pointing to a newer snapshot
does not silently enroll new historical fallback pins. No keys, local grants,
errors, inventory or search history are exported. Original source endpoint hints
and catalog descriptions **are** exported: review for private names, local paths
or sensitive metadata before consenting. Publication cannot retract copies.
A failed/interrupted public command may have already pinned blocks or published
an IPNS update; inspect the resulting name/pins before repeating it.

## Retain an independent copy

Another operator uses their own mirror repo and keys, imports the source through
normal authenticated following, then uses the same model publication command
on a fully verified local copy. To pin source metadata without possessing or
impersonating the original IPNS private key:

```sh
model-preserve peer-source-mirror --source SOURCE_ID --repo ./another-peer \
  --allow-peer-network --public-metadata
```

This republishes the exact verified snapshot, returning the same CID. It does
not renew or sign the original publisher's IPNS name. Several retained copies
can supply the same content without the catalog naming each operator. DHT
provider records are leases: those peers must run and reprovide to remain
findable. Retaining bytes alone does not provide perpetual discovery.

Stop the owned node before changing routing contacts:

```sh
model-preserve peer-bootstrap-set --repo ./public-peer \
  --bootstrap-address REPLACEMENT_MULTIADDRESS
```

This atomically changes only bounded contacts, preserves the native identity,
pins and source trust, and refuses an active repository. An empty list is
permitted for an isolated first routing node; it cannot find a network alone.
Critical serving/privacy/identity config changes refuse. A live API marker
refuses startup and contact changes. Every owned node holds the repository's
writer lock while it runs, so under that lock a loopback marker whose port
refuses connections (left by a killed node or an OS crash) is cleared
automatically. Any other marker still refuses; investigate before removing it.
Never point the adapter at a user's existing IPFS repo.

## Privacy, resources and demonstrated limits

This is **public peer networking, not anonymity**. Peers can observe network
addresses, routing requests and requested content identifiers. Search terms stay
local, but fetching a catalog/model reveals interest in those identifiers.
Encrypted transport does not conceal this metadata. Do not assume Tor/I2P,
private model sharing, censorship resistance or protection against a powerful
network observer. The adapter does not run a public gateway or open its RPC
API beyond loopback. Mirrors enable native DHT service and pinned block serving;
receive nodes do neither block serving nor provider advertising.

| Boundary | Implemented bound |
| --- | --- |
| Source file | 256 KiB; one canonical base36 IPNS key, at most eight canonical SHA-256 UnixFS snapshot CIDs. |
| Contacts | At most eight unique numeric IP/TCP/libp2p addresses; private connectivity requires separate local permission. |
| Metadata receive | 32 MiB aggregate exported snapshot bytes across candidate failover, plus at most one oversize probe byte per attempt; existing TUF role/catalog limits also apply. |
| Model receive | Explicit aggregate manifest/object export allowance across retries; exact declared file lengths, complete closure and original signature checks before store publication. Oversized output is stopped after one probe byte. |
| Native commands | Bounded stdout and explicit cooperative deadline, at most 24 hours; requesting CLI children are stopped on cancellation. |
| Node lifetime | Explicit 1–86,400 seconds; owned native daemon stops on caller exit/loss. A separate running node outlives an individual fetch request. |
| Mirror egress | Observed native upload threshold; checked periodically, with in-flight/interval overshoot. This is not a hard wire quota. |

Export allowances do **not** bound all native wire traffic, routing/DAG overhead
or block-cache storage. Kubo can cache traversed blocks during failed requests.
Model receive stages objects in the destination archive's own resumable partial
files, after a free-space check, and model publication stages its export copy in
a `.peer-export` area beside the archive on the same volume. Neither uses system
temporary storage for weights. Allow space for the native block store, the export
copy and the final archive. Use an
operator disk/filesystem quota and firewall/network quota where hard limits are
required. There is no automatic eviction, unpin, storage purchase, repair or
persistent service registration. Software/dependency distribution and native
version upgrades remain separate operator responsibilities.

Native conformance uses stock Kubo peers with only local numeric bootstrap
contacts: two peers pin matching model/source CIDs, then the original publisher,
its IPNS key, HTTP metadata tree and model inputs are deleted. A cold receiver
resolves the name through a routing-only peer, authenticates TUF, discovers a
provider, receives the complete model/companion and restores identical signed
bytes. Further tests demonstrate no block serving by receive nodes and daemon
shutdown after abrupt caller loss. These controlled local tests do not qualify
Internet-wide DHT behavior, independent operator custody, NAT reachability,
long-duration availability or resistance to censorship/eclipsing attacks.

Protocol references: [IPNS](https://docs.ipfs.tech/concepts/ipns/),
[IPFS DHT](https://docs.ipfs.tech/concepts/dht/),
[privacy](https://docs.ipfs.tech/concepts/privacy-and-encryption/),
[Kubo configuration at the qualified revision](https://github.com/ipfs/kubo/blob/v0.43.1/docs/config.md).

## Review a peer model before receiving its weights

For one file carrying starting contacts with finite supervised receiving/sharing,
see [portable handoffs](handoffs.md). The lower-level references and separately
supervised nodes below remain supported.

The separately versioned `foxlight.model-preservation.peer-invitation.v1`
[reference schema](../src/foxlight_model_preservation/resources/peer_invitation_v1.json)
retains the exact signed envelope, candidate public keys and canonical IPFS
root hints. It grants no trust or network access. Released HTTP invitations
remain unchanged and do not accept this version. A metadata-only handoff lets
an application show all members and provenance before asking to receive bytes:

```sh
model-preserve peer-model-metadata --source SOURCE_ID --entry ENTRY_ID \
  --expected-manifest-id MANIFEST_SHA256 --bundle-trust model-trust.json \
  --repo ./receiver-peer --allow-peer-network --output model.foxpeer-model
model-preserve peer-invitation-inspect --invitation model.foxpeer-model
model-preserve peer-invitation-fetch --invitation model.foxpeer-model \
  --bundle-trust independently-selected-trust.json --repo ./receiver-peer \
  --allow-peer-network --store ./received --max-bytes 21474836480 \
  --operation-id receive
```

Inspection is entirely offline. Fetch authenticates the exact envelope with
independently selected trust before requesting objects. `--operation-id` binds
the protected destination inode, complete reference, trust and allowance to a
fsynced transfer receipt. Retries with that ID cannot increase the allowance or
change the selection. Reservations precede each object request; uncertain
reservations survive a crash without refund. Verified complete objects are
reused, while partial object exports restart; Kubo's separate native cache may
retain their blocks. A completed archive is published only after the full
closure verifies. Object staging uses descriptor-relative files on the selected
archive volume and atomic rename after verification, avoiding a second full
object allocation. Local write/fsync/rename errors stop the attempt instead of
triggering supplier failover. Unverified known staging slots left by a crash are
removed before the next space check/request. Native cache space remains separate.
Accounting remains private under `.operations/`; public
publish/export still selects only signed manifest and exact objects.

Python consumers use `peer_invitations.inspect_peer_model`,
`read_peer_invitation`, `authenticate_peer_invitation`,
`accept_peer_invitation_keys` and `fetch_peer_invitation`. The existing
copy-scoped threshold/revocation policy is shared with HTTP handoffs. Embedded
keys remain candidates; a source's metadata signature cannot silently grant
model, registry or runtime authority. `peer_runtime.serve_peer` is the owned
synchronous primitive for known/frozen worker integrations; its caller must
supply a lifetime/cancellation fence and drain the actual native process.
Standalone callers should use `run_peer`, which already owns that supervision.
