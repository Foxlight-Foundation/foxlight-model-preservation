# Follow sources, search locally, recover independently

The standalone `source-*` commands follow authenticated model catalogs, refresh
through interchangeable endpoints, search locally and retain an offline cache.
They need no Skulk, Core process, private SDK, model hub, hosted account or GPU.
The commands operate on metadata until an explicit exact-model fetch. They do
not start a listener, advertise possession, seed a torrent or send search terms
and installed-model inventories to suppliers. The separate optional
[IPFS/IPNS peer adapter](peer-discovery.md) adds peer routing with explicit
network participation, keeping this offline search and TUF authority unchanged.
The [I2P HTTP pilot](i2p-http.md) can refresh the same enrolled source through
explicit protected locators without joining a payload swarm. Ordinary refresh
never resolves I2P names directly; use the explicit transport command.

## Follow and search

Obtain a source file and its **bootstrap ID through an independently trusted
channel**. `source-inspect` computes candidate IDs offline; values supplied only
by the same untrusted download do not authenticate its publisher. Replace the
uppercase placeholders with real independently authenticated values:

```sh
model-preserve source-inspect --source-file community-source.json
model-preserve source-follow --source-file community-source.json \
  --expected-bootstrap-id BOOTSTRAP_SHA256
model-preserve source-refresh
model-preserve source-search 'offline writing'
model-preserve source-list
```

First-contact enrollment does not connect. `source-refresh` and explicit
transport refresh commands are the metadata network actions. Refresh all enabled sources, or use `--source SOURCE_ID` for one.
A failed source does not prevent healthy sources from updating or remaining
searchable. A refresh returns a nonzero exit status if any selected source fails;
its JSON result includes per-source failures. Search and listing make no network
requests, including when metadata has expired or all endpoints are unavailable.

Choose a result's exact manifest, source and entry, then independently select the
model capture's signing keys. Source trust grants discovery scope only. Candidate
model keys in descriptions are hints, never automatically enrolled model trust:

```sh
model-preserve source-fetch --source SOURCE_ID --entry ENTRY_ID \
  --expected-manifest-id MANIFEST_SHA256 --bundle-trust model-trust.json \
  --store ./received
model-preserve verify MANIFEST_SHA256 --trust model-trust.json --store ./received
model-preserve restore MANIFEST_SHA256 --trust model-trust.json \
  --store ./received --output ./restored
```

Changing the entry's manifest between search and selection requires a new review;
it cannot silently substitute a new model. Fetch retains ordinary SHA-256 file,
complete companion, transfer-budget and signature checks. It does not execute
code or grant registry provenance. Current network receiving uses preservation
v1 public bundles; PMF file/kit recovery remains separately available. Discovery
does not add a direct PMF network adapter.

## Trust, updates and cached results

The stock [python-tuf Updater](https://theupdateframework.readthedocs.io/en/stable/api/tuf.ngclient.html)
verifies root rotation, thresholds, timestamp/snapshot/targets signatures,
versions and expiry. The client uses its bounded pinned-DNS transport, not a
second update protocol. No redirects, ambient proxies or endpoint credentials
are accepted. Metadata endpoints do not become bundle signers or registry roots.
The updater's verified role state survives later catalog rejection, failover and
restart. The last accepted catalog is committed atomically with its proof.

Search groups **exact manifests**, including alternate suppliers whose aliases
do not match the query. Conflicting names and publisher statements stay attributed
to their source rather than being merged into a synthetic authoritative card.
Descriptions use PMF-style name/workload concepts; missing descriptions remain
absent. Descriptions and optional candidate keys neither replace archived cards
and extensions nor imply runtime compatibility or qualification.

Each source reports acceptance time, signed expiry, last attempt, error and state:

| State | Meaning |
| --- | --- |
| `empty` | Followed, but no catalog accepted yet. |
| `fresh` | A successfully refreshed signed snapshot remains unexpired; this is not a reachability claim. |
| `retained` | A subsequent update failed; the last accepted snapshot remains available. |
| `stale` | The accepted snapshot has expired; local browsing does not bypass fresh-update checks. |
| `imported` | A signed historical snapshot was imported offline; no successful online refresh is claimed. |
| `superseded-root` | New root metadata is known, but the saved catalog belongs to an older root. |
| `invalid` | Local source evidence failed verification; other sources remain usable. |

Root revocations are limited to the authenticated chain actually observed. No
client can infer a withheld, unknown revocation. Historical listing evidence is
not a global exemption for expired new updates. Registry proof, model file
integrity, capture authority and runtime code consent remain separate decisions.

## Offline interchange and removal

```sh
model-preserve source-export --source SOURCE_ID --output offline-source.json
model-preserve source-follow --source-file offline-source.json \
  --expected-bootstrap-id BOOTSTRAP_SHA256
model-preserve source-search 'writing'
model-preserve source-remove --source SOURCE_ID
```

Export contains the original bootstrap, authenticated root chain and last accepted
catalog/proof. Receivers verify the complete signed evidence offline. It excludes
receiver grants, errors, search history and installed-model inventory. The source
file includes its original endpoint hints: review those before sharing, especially
if an operator deliberately configured local filesystem paths or private hosts.
An exported snapshot is historical bootstrap material, **not a backup of every
receiver update floor observed during failed refreshes**. Retain the protected
cache directory itself for a full receiver-state backup. Importing into an
existing cache cannot lower its known versions or replace conflicting metadata
at an already accepted version.

Removal disables following and search results but retains update floors, so
re-following cannot reset rollback protection. Re-following without a receiver
policy flag keeps that source's existing grant; `--no-allow-http`,
`--no-allow-private` and `--no-allow-local` revoke one explicitly.

Refresh refuses when the system clock is behind the source's last recorded
attempt. If a refresh once ran under a clock set in the future, correct the clock
and run `source-refresh --source SOURCE_ID --accept-clock-rollback`. This clears
only the recorded attempt time; trusted roots, role versions and the accepted
catalog are retained, and TUF still checks expiry against the corrected clock. It never removes installed models,
preserved copies, downloaded files or recovery kits. Search continues to work
with no original publisher or Foxlight service. Runtime software/dependency
wheels must be retained separately for completely offline software installation.

## Storage, receive policy and limits

Use `--cache /private/path` on source commands to select a dedicated owner-only
cache instead of `~/.local/state/foxlight-preservation/discovery`. Group/world
access and symlink state files refuse. Nonblocking writer locks fence concurrent
mutations. Atomic state publication keeps accepted catalog/proof and verifier
state together; interruptions cannot publish a partial catalog.

Receiver policy is explicit. `source-follow --allow-http` permits plaintext
metadata, `--allow-private` permits non-global network destinations, and
`--allow-local` permits absolute local/mounted metadata paths. None is enabled by
an imported source file. `source-fetch` has its **own** separate flags for these
policies; metadata enrollment does not grant model-file access. Unapproved local
model hints are skipped, and a selection with no permitted hints refuses.
No query, download or import implicitly starts serving.

| Bound | Implemented limit |
| --- | --- |
| Source/cache reservations | 16 identities, including disabled sources; at most 32 MiB per source record. |
| Root metadata | 64 KiB per role; 128 retained successor roots, at most 32 rotations per refresh. |
| Other top-level TUF roles | 1 MiB each; only `catalog.json` from top-level targets is interpreted. |
| Signed catalog target | 2 MiB, at most 1,000 v1 entries; each retains existing mirror bounds. |
| Refresh across endpoint failover | 32 MiB of metadata bodies, 128 requests, cooperative 120-second deadline; socket timeout at most 10 seconds. |
| Search | 500-character query; up to 1,000 exact-identity results, default 100. |
| JSON structure | 64 levels, 200,000 nodes; duplicate keys refuse. |

Socket operations, reads and verification checkpoints enforce bounds; OS DNS,
filesystem stalls and a single cryptographic/library call are not interruptible
hard wall-clock deadlines. HTTP/TLS overhead is outside body-byte accounting.
Root-history exhaustion refuses updates rather than silently discarding trust
history. There is no recursive source enrollment, DHT, automatic eviction, paid
storage, anonymity or proof of independent custody/reachability in this profile.
Local fault drills do not establish global network-censorship resistance.

## Publish a source on ordinary storage

Source operators need a static HTTPS tree, not Skulk or a new sovereign server.
Keep all private keys/passphrases outside the public tree. Use existing TUF
operator tooling for production custody, threshold signing and root rotation.
The sample below uses one offline root key and one online metadata key; distinct
online role keys can be substituted. The existing model/capture signer remains
independent. No command below starts hosting or grants redistribution rights.

Create encrypted keys using the ordinary `keygen` command. Supply a protected
nonempty passphrase file; never put its literal contents on the command line:

```sh
model-preserve keygen --key private/root.pem --trust-out private/root-trust.json \
  --passphrase-file private/passphrase
model-preserve keygen --key private/online.pem --trust-out private/online-trust.json \
  --passphrase-file private/passphrase
uv run python examples/create_discovery_root.py --root-key private/root.pem \
  --passphrase-file private/passphrase --targets-trust private/online-trust.json \
  --snapshot-trust private/online-trust.json --timestamp-trust private/online-trust.json \
  --output initial-root.json
```

Sign an explicit existing catalog with `catalog-sign` and independently review
its entries/mirror hints. The following requires that the selected online key is
authorized by the supplied public root for all three online roles:

```sh
model-preserve source-publish --root initial-root.json --index signed-catalog.json \
  --catalog-id CATALOG_PAYLOAD_SHA256 --catalog-trust catalog-trust.json \
  --targets-key private/online.pem --snapshot-key private/online.pem \
  --timestamp-key private/online.pem --passphrase-file private/passphrase \
  --version 1 --output public-source
model-preserve source-bootstrap --root initial-root.json --name 'Community models' \
  --endpoint https://YOUR-ACTUAL-SOURCE-HOST/models --output community-source.json
```

`YOUR-ACTUAL-SOURCE-HOST` is a placeholder, not an available recommended source.
The bootstrap command prints the stable source ID and exact bootstrap-file ID.
Distribute the source file and authenticate its ID separately. Ordinary updates
reuse the source identity and increment `--version`; timestamp publishes last.
Existing immutable versions cannot be replaced. Retain required historical roots
when mirroring the metadata tree. Rotate roots with standard sequential old/new
threshold endorsements; ordinary publishing never needs the root private key.
Serve the same metadata tree from multiple endpoints to remove one hostname or
provider dependency. Endpoint changes do not change the initial-root identity;
reviewing a replacement bootstrap for that same identity retains local floors.

Optional `--descriptions descriptions.json` adds an array of exact-entry metadata:

```json
[
  {
    "entry_id": "small_text",
    "manifest": "REPLACE_WITH_EXACT_MANIFEST_SHA256",
    "name": "Small text model",
    "summary": "Publisher description for local search",
    "workloads": ["text.generate"],
    "formats": ["gguf"],
    "licenses": ["LICENSE-IDENTIFIER"],
    "annotations": {"example": "optional source statements remain attributed"}
  }
]
```

Placeholders are not valid manifest identities. An optional
`candidate_bundle_trust` can supply correctly fingerprinted public capture keys
for explicit receiver review; it is never a grant. The entire target custom
profile, descriptions and catalog signer profile are authenticated by TUF targets.
All describe public candidates, not mutable replacements for original cards.

## Format and static paths

Schemas: [source bootstrap](schemas/SourceBootstrap.json),
[catalog target custom profile](schemas/DiscoveryCatalogTarget.json),
[offline cache bundle](schemas/DiscoveryCacheBundle.json).

```text
metadata/1.root.json                 # initial authenticated root
metadata/2.root.json                 # optional sequential signed rotation
metadata/timestamp.json             # current signed timestamp, published last
metadata/N.snapshot.json             # consistent-snapshot metadata
metadata/N.targets.json
metadata/snapshot.json               # also supplied for non-consistent repositories
metadata/targets.json
targets/CATALOG_FILE_SHA256.catalog.json
targets/catalog.json                 # also supplied if consistent_snapshot is false
```

Source bootstrap version is `foxlight.discovery-source.v1`. The signed TUF
target named `catalog.json` carries custom profile
`foxlight.discovery-catalog.v1`, including the unchanged catalog v1 payload ID,
catalog signer profile and optional exact-entry descriptions. Offline interchange
is `foxlight.discovery-cache.v1`. These explicit surrounding profiles do not
reinterpret or change historical catalog v1 bytes, model cards, PMF manifests,
bundle identities or recovery kits. Unsupported interpreted versions fail closed.

Offline import into an existing followed source requires the catalog's accepted
root authority to be at least the newest root in both receiver and incoming
chains. Higher role versions signed by retired keys cannot restore revoked
authority, even after an unsuccessful target update. Historical snapshots with
older accepted roots remain importable into a new cache as explicitly
superseded-root evidence; use the bootstrap file to refollow an existing source
without replacing its retained catalog.

The static publisher can initialize a non-consistent TUF tree, but refuses its
in-place updates before changing any committed file. Updates require consistent
snapshots in both the prior and proposed roots; rotating a non-consistent root
to a consistent one also refuses in place. Operators of other TUF layouts must use an atomic whole-generation
deployment through their own repository tooling, or publish a separately
reviewed source. The default root example enables consistent snapshots.

For in-place consistent-snapshot root rotations, both old and new roots must
authenticate the committed and proposed online-role signatures. Stage overlapping
online keys, publish using the existing keys, publish a generation using the new
keys, and only then revoke the old keys in the next root. The helper refuses
an incompatible transition before writing any generation. Root files still need
both old-root endorsement and new-root self-signatures. Other rotation schemes
require the operator's atomic whole-generation TUF deployment tooling.
