# Persistent IPFS and BitTorrent mirrors

The standalone [mirror controller](persistent-mirrors.md) can explicitly serve
selected public models through Kubo/IPFS and libtorrent/BitTorrent as well as
HTTP. Skulk, its SDK, GPUs and a Foxlight service/account are not prerequisites.
Ordinary model receiving still starts neither hosting nor a peer network.

## Select software and public transport policy offline

Use the optional `peer` Python extra for IPFS, `bittorrent` for BitTorrent, or
both. The combined public installation profile is `requirements-mirror.lock`;
the default installation remains free of native dependencies. Authenticate a
compatible Kubo 0.43.1 executable separately using the existing
[native runtime acquisition](peer-discovery.md) instructions.
Supplying a path/version is not software-publisher authentication.

After init and independent source/model/terms enrollment, or offline
[retention of an exact public copy](persistent-mirrors.md#retain-a-public-copy-you-already-have),
stop the controller and explicitly choose the public transports:

```sh
model-preserve mirror transports --dir /srv/sovereign/example \
  --ipfs /absolute/path/to/ipfs --ipfs-sha256 AUTHENTICATED_SHA256 \
  --bootstrap /ip4/REACHABLE_ADDRESS/tcp/PORT/p2p/PEER_ID \
  --torrent --max-active 2 --session-seconds 3600 \
  --session-bytes 67108864 --upload-rate 1048576 \
  --confirm-public-transports
model-preserve mirror plan --dir /srv/sovereign/example
```

Omit either transport to select only the other. Omit both to disable native
sharing while retaining its caches. Reconfiguration is offline, requires the
controller to be stopped and invalidates any standing restart grant. Native
policy upgrades local mirror state to v2, retaining v3 when direct local selections
are present; older tools refuse unsupported profiles. HTTP-only
v1 state and its existing restart identity remain readable without migration.
This changes neither model manifests nor original signed envelopes/cards.

The executable path/hash is checked again before native startup. The IPFS repo
is newly created for this mirror, never adopted from a user's existing Kubo repo.
Contacts are numeric explicit multiaddresses, at most eight. Empty contacts mean
no initial peers, not a compulsory public/Foxlight bootstrap. Retain replaceable
reachable contacts outside Foxlight. Private/LAN contacts require the separate
`--allow-private-peers` choice and matching receiver consent. Changing that
address scope for an existing native repo refuses rather than silently expanding
its authority. Reachability, firewall/NAT and provider policy remain operator work.

## Run and restart under the existing owner

```sh
model-preserve mirror run --dir /srv/sovereign/example --public --seconds 3600
```

Manual serving has a fresh finite grant. Unattended `--resume` instead requires
a new separate expiring `grant-resume` after native configuration. The existing
systemd recipe uses `--resume` only. No service is automatically installed or
enabled, and import/export never starts network participation.

Each retained model gets isolated native custody beneath
`scratch/native/<manifest>-<generation>/`. BitTorrent exports only its exact verified public
closure. IPFS uses a dedicated per-model repo, pins the same verified closure
and lets maintained Kubo renew its provider advertisements. The controller does
not use an unpin, garbage collection or cache deletion to stop sharing.
An interrupted offline Kubo initialization or torrent export leaves its private
generation retained. If no completion receipt was committed, explicit retry
creates a fresh named generation rather than adopting or deleting that tree.
Partial and unreceipted complete generations count toward disk usage and remain
until the operator explicitly prunes that model's native tree.
Initial per-model custody journals its generated name before directory creation
and commits the inode before writing contents. Retry retains interrupted empty
intents and creates a fresh directory. Explicit prune may remove those empty
intents; unexpected contents refuse deletion and leave the retained model intact.
Kubo's provider selection and Bitswap serving are distinct
[configuration controls](https://github.com/ipfs/kubo/blob/v0.43.1/docs/config.md).
Isolation lets a blocked model's daemon stop while other models remain served.
It costs more disk/process resources than sharing one unrestricted block cache.

At most four native transport sessions run concurrently (default two); each
IPFS or torrent session uses a slot. Waiting models remain retained and available
over their independently allowed HTTP routes. Expiring sessions rotate toward
models that have waited longest; this is bounded availability, not simultaneous
peer hosting of every archived model. Each session lasts at most 24 hours and
spends its full selected upload allowance durably before startup. Crash, retry,
stop or prune never refunds that allowance. Publication capacity is acquired
before starting the finite Kubo daemon, so waiting for another publisher cannot
consume that daemon's serving lifetime. A later session needs new credit
from the same UTC daily/monthly ledger. DHT overhead, IPFS wire observation and
torrent payload counters differ: native thresholds can overshoot and are not
hard total-wire quotas. The torrent upload-rate cap does not impose an IPFS rate.

Disk admission conservatively includes three model copies plus 256 MiB per new
native selection, in addition to existing usage/reservations. Existing adapters
copy export files and Kubo stores its own blocks. All actual private cache,
staging and partial allocations count in observed usage. This deliberately can
refuse early and is not an OS filesystem quota. Disk exhaustion stops native
work on the next inventory observation (at most every five seconds on a responsive
filesystem); native allocation can grow between observations. It never triggers
automatic eviction. Native errors remain local and
require explicit `mirror retry --manifest ...` after fixing the cause.

## Export one recipient file

After successful native publication:

```sh
model-preserve mirror export --dir /srv/sovereign/example \
  --manifest MANIFEST_SHA256 --kind peer --output model.foxhandoff
model-preserve mirror export --dir /srv/sovereign/example \
  --manifest MANIFEST_SHA256 --kind torrent --output model.torrent
```

Exports are offline and atomically refuse an existing output. They remain
available after Stop. The peer handoff retains the original signed envelope,
candidate keys and last observed numeric contacts. Recipients still independently
authenticate model keys/identity and explicitly choose peer/address permissions.
Saved contacts can become stale; they are routing suggestions, never trust or a
current availability guarantee. A standard torrent client can download the
archive; verify its signed manifest and every object with standalone tools before
using it. The [BitTorrent DHT](https://www.bittorrent.org/beps/bep_0005.html)
locates peers for an exact infohash; it does not authenticate model provenance.

## Stop, block and delete separately

Pause, leave, resume-grant revocation, serving windows, policy changes and block
request native shutdown. The owner cancels actual sessions and joins them;
IPFS runs in the existing EOF-fenced child supervisor, so abrupt controller loss
also stops its Kubo child. Wait for the controller to drain before treating
shutdown as complete. Status records are historical receipts; a saved port,
publication count or CID is not proof of a currently listening process.

Catalog withdrawal, expiry or disappearance has no deletion power. Pins protect
the original model and its isolated native custody. Exact stopped-controller
`mirror prune` requires the same explicit blocked/unpinned identity confirmation;
its write-ahead journal includes native custody. It removes only that selected
model's native tree and unshared archive objects. A replaced root refuses and an
interrupted deletion stays fenced until the operator repeats the same command.
No remote withdrawal can erase an independently retained recipient's copy.

Public peers and mirrors reveal network addresses and offered content. Encryption,
CIDs, infohashes and signatures are not anonymity. Controlled local conformance
does not establish general NAT reachability, independent community custody,
adversarial-network resilience or censorship-resistance qualification.
