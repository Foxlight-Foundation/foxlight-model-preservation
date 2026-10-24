# Standalone I2P recovery pilot

This optional pilot exports an original signed recovery kit as one I2P-only
BitTorrent archive, hosts it for an explicit finite session, and receives it into
a newly verified store. It accepts legacy preservation and PMF archives. Neither
Skulk, a registry, an account nor an online Foxlight service is required. Retain
independently authenticated model signer keys and the exact manifest ID.

**This is an engineering pilot, not a qualified anonymity or censorship-resistance
mode.** Public-network reachability, leak assessment, operational privacy and
large-model performance have not been qualified. Library/Guided integration is
not present. Existing direct downloads and public mirrors retain their existing
network behavior; this command never falls back to them.

## Software and consent

Use an independently installed and operated I2P router with I2CP listening on
numeric loopback (normally `127.0.0.1:7654`). This pilot does not install, start,
stop, reconfigure or adopt the router. Router participation, bandwidth and
retention are separate operator responsibilities. Restrict local access to I2CP
and the profile; this first pilot does not configure I2CP authentication.

The client uses stock I2PSnark 2.14.0 and its existing I2P DHT, with a small owned
launcher and the separately maintained Snark RPC plugin. It does not implement a
new anonymity network. The selected RPC plugin cannot enforce receive-only
payload settings: **receiving can upload pieces**. Both `--allow-i2p` and
`--allow-sharing` are required on every receive/share invocation. Inspection,
export, profile initialization and handoff creation start no network session.
Public-intake permission for every model member is required for this swarm flow.

Optional libtorrent bindings parse/hash torrent metadata offline; they never
start a libtorrent session in this flow. Install the `bittorrent` Python extra:

```sh
uv sync --locked --extra bittorrent
```

Build the native runtime from exact upstream commits, using independently
selected Java 17+ and Ant. Paths below are examples; use absolute paths. The
script downloads source only when explicitly requested and writes a new tree:

```sh
python scripts/build_i2p_runtime.py --directory /data/sovereign-snark-build \
  --java-home /path/to/jdk --ant /path/to/ant \
  --allow-source-download
```

The script prints a SHA-256 runtime ID. Keep it with the build. `RUNTIME.json`
records source revisions, the two narrowly scoped RPC compatibility adaptations,
launcher source identity and all three JAR hashes. The small launcher source is
MIT-licensed; the build retains full upstream license texts separately. One adaptation wraps a newly
declared metainfo exception; the other maps the RPC download directory to the
current Snark base-file API. The ID identifies those local bytes; it is not an
upstream signature or a promise of byte-identical builds across toolchains.
No prebuilt native-runtime download or system-service installation is provided.
Never accept software/runtime IDs selected by an untrusted model offer.

Initialize separate new private profiles for host and receiver, each with its
own selected runtime, Java executable, router and application bandwidth limits:

```sh
model-preserve i2p init --dir /data/sovereign-i2p-host \
  --runtime /data/sovereign-snark-build/runtime --expected-runtime-id RUNTIME_SHA256 \
  --java /path/to/jdk/bin/java --upload-kib 32 --download-kib 256
```

Initialization is offline. Runtime files and the Java launcher executable are
rehashed before starting a session. Keep the entire JDK/runtime under operator
control: hashing the launcher alone does not attest every JDK library.

## Host and send one file

Export a complete public archive under independently enrolled trust. Export
copies and verifies a recovery kit and creates `offer.json` plus `model.torrent`:

```sh
model-preserve i2p export MANIFEST_SHA256 --store /data/preserved \
  --trust model-trust.json --output /data/i2p-offer

model-preserve i2p share --dir /data/sovereign-i2p-host \
  --offer /data/i2p-offer/offer.json --kit /data/i2p-offer/MANIFEST_SHA256.foxkit \
  --trust model-trust.json --expected-manifest-id MANIFEST_SHA256 \
  --allow-i2p --allow-sharing --seconds 3600 --max-bytes 21474836480
```

While that opted-in session is connected, use another terminal to make a one-file
recipient handoff containing its observed I2P DHT contact:

```sh
model-preserve i2p handoff --dir /data/sovereign-i2p-host \
  --offer /data/i2p-offer/offer.json --trust model-trust.json \
  --expected-manifest-id MANIFEST_SHA256 --output /data/model.i2p-offer
```

Send `model.i2p-offer` through your chosen channel. Convey the independently
verified manifest ID and trusted signer profile separately. A handoff cannot
enroll its own keys. It contains metadata and routing hints, not model weights.
The contact is an I2P destination, not a public IP address. It is an observation;
the exporter cannot guarantee the host is still available. Keep the hosting
session running during receipt. A new session may use a different destination;
export an updated handoff after restarting. `i2p status` and `i2p contact` read
local observations and do not silently refresh them through the network.

## Receive, verify and restore

Initialize an independent receiving profile as above, then preview and receive:

```sh
model-preserve i2p inspect --offer model.i2p-offer

model-preserve i2p receive --dir /data/sovereign-i2p-receiver \
  --offer model.i2p-offer --trust model-trust.json \
  --expected-manifest-id MANIFEST_SHA256 --store /data/recovered \
  --allow-i2p --allow-sharing --seconds 3600 --max-bytes 21474836480

model-preserve verify MANIFEST_SHA256 --store /data/recovered --trust model-trust.json
```

Restore PMF with `pmf-restore`, or legacy archives with `restore`, into a new
output directory. Restoring files does not execute models. Original card data,
required companions, PMF documents and signed provenance survive. Existing
registry-proof verification and runtime trust/admission policy remain separate;
a swarm, a capture signer or a successful transfer grants no registry authority.

The offer is bounded unsigned routing metadata. Before starting native software,
the receiver authenticates the exact original DSSE envelope under independent
model trust and checks the selected manifest identity. Only a canonical v1,
single-file torrent naming exactly that recovery kit is accepted. Public
trackers, web seeds, IP peer hints, alternative file paths, hybrid/v2 inventories
and credentials are refused. Accepted tracker URLs are restricted to I2P b32
HTTP endpoints. The generated metainfo lists no trackers. Stock Snark 2.14 can still announce
to its built-in **I2P backup trackers** when its DHT is small, even with configured
open trackers disabled. This is I2P traffic, not public BitTorrent or direct HTTP
fallback. The profile is not a closed group or an allowlist of contacted peers;
sharing can disclose the selected kit infohash to those I2P services/peers. It is
not a tracker-free privacy mode. The final kit
hash/length, original envelope bytes and complete signed archive closure all
verify before a new complete store is published.

## Stop, resume and retain

Each profile owns one immutable model/kit/swarm selection. Repeating the same
receive command can reuse retained partial files; changed selections refuse.
Routing contacts can change. A profile can host only its selected archive.
Use a new profile for another model. Existing archive destinations refuse.

Ctrl-C, SIGTERM, deadline, detected router disconnection, errors and controller EOF stop only the owned Snark
JVM. An independent native timer also bounds its lifetime. The separately operated
router stays under its operator's control. The launcher exposes authenticated
RPC on numeric loopback only; commands do not use global start/stop, torrent
removal or deletion RPC. Cancellation does not remove cached kits, partials,
DHT state, source kits or recovered archives. This pilot has no automatic eviction
or remote delete. Delete retained local files only through your own deliberate
filesystem operations after stopping the profile.

`--max-bytes` bounds the selected kit's size, not cumulative wire traffic. Native
application upload/download rates and a maximum 24-hour client lifetime apply.
I2P protocol overhead, retransmissions and the router's own participation are not
covered by a hard per-model upload budget. Do not describe these as wire quotas.
The profile can contain sensitive possession/routing metadata; protect the local
filesystem. Sharing the handoff and I2P participation have their own metadata
exposure. This pilot supplies no anonymity guarantee.

## Conformance

`tests/test_i2p.py` exercises offline trust, public-intake, path/routing refusal,
original envelope pinning, grants and CLI behavior. `tests/test_i2p_native.py`
requires explicitly selected native software and starts eight separate routers
in an isolated synthetic network (ID 99), with public reseeding disabled. It
waits for native startup readiness and configures known loopback SSU transport
reachability; production client tunnel lengths are unchanged. Where available,
`lsof` samples owned router/seeder sockets. These are snapshots, not a packet
capture or proof of all traffic. It exercises native DHT and
Snark rather than a simulated Python transport. Such a local test cannot
establish public-network anonymity, discovery coverage or hostile-network safety.

Upstream references: [I2P BitTorrent guidance](https://www.i2p.net/en/docs/applications/bittorrent/),
[standalone Snark](https://github.com/i2p/i2p.i2p/blob/i2p-2.14.0/apps/i2psnark/readme-standalone.txt),
[the selected RPC plugin](https://github.com/i2p/i2p.plugins.i2psnark-rpc/tree/9fa6d9bc601f9ea6cfd18ce1e365bee989067665).
