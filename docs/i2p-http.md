# Receive-only I2P HTTP pilot

Standalone 0.15 adds authenticated source updates and legacy model-archive
receiving through an explicitly selected local I2P HTTP proxy. It requires no
Skulk, SDK, account, Java, torrent bindings or running Foxlight service. It uses
an existing separately operated proxy/router; installing Sovereign starts none.

**This is a transport pilot, not qualified anonymity or censorship resistance.**
Public-network reachability, traffic/metadata exposure and large-model performance
remain unqualified. The existing I2PSnark path has a separate explicit upload
grant; these HTTP commands send GET requests and do not join a payload swarm.
Your router can still relay other encrypted I2P traffic. Receiving a model never
implicitly enables a Sovereign mirror or advertises your retained inventory.

## Prerequisites and strict routes

Independently select and operate a trusted I2P HTTP proxy bound to numeric
loopback (`127.0.0.1:4444` by default, or explicit `::1`). Merely selecting a port
does not authenticate the software listening there or prove it uses I2P.
Sovereign does not adopt, start, stop or reconfigure that router/proxy.

Only `http://HASH.b32.i2p` destinations with a canonical 52-character base32
hash are accepted, with an optional port and simple base path. Human address-book
names, extended/blinded names, address helpers, credentials, queries, fragments,
escaped/traversal paths, public Internet destinations and HTTPS/CONNECT are not
part of this initial profile. These limitations are explicit refusals.

All sockets connect directly to the selected numeric loopback proxy. The
application performs no DNS lookup, consults no ambient proxy configuration and
follows no redirects. An unavailable proxy, rejected destination or invalid
response fails; it never retries through direct HTTP, an exit proxy, IPFS or a
public torrent. Ordinary direct transports also refuse `.i2p`, `.i2p.alt` and
`.onion` names before DNS. A trusted local router remains responsible for its
own external routing, DNS, plugins, logging and configuration.

## Follow a source, refresh through I2P, search offline

Obtain the source document and bootstrap identity independently, as in the
[ordinary source workflow](discovery.md). Enrollment and local search make no
network requests. The original TUF root, signed catalog authority and separate
model authority remain unchanged:

```sh
model-preserve source-follow --cache /data/discovery --source-file source.json \
  --expected-bootstrap-id INDEPENDENT_BOOTSTRAP_SHA256
model-preserve i2p-http source-refresh --cache /data/discovery \
  --source ENROLLED_SOURCE_ID --allow-i2p
model-preserve source-search 'offline writing' --cache /data/discovery
```

The source's endpoint hints must all be I2P routes for that protected invocation.
Alternatively supply one or more explicit `--endpoint http://HASH.b32.i2p/source`
locators. This permits a previously enrolled public source to use an I2P mirror
without rewriting its original bootstrap, source identity, signed bytes or
trust/version floors. The command never consults other endpoint hints when
explicit protected locators are supplied. Mixed direct/I2P lists refuse before
connecting. These locator overrides and proxy selection are invocation-specific;
repeat the protected command/options for later updates. Ordinary explicit
refresh of a source with public hints retains its ordinary network behavior.

`--proxy-host ::1 --proxy-port PORT` chooses another numeric loopback proxy.
`--allow-i2p` is required on every network invocation; it grants neither model
trust nor payload seeding. Updates retain the existing per-role/catalog length,
32 MiB aggregate byte and 128-request limits, with a five-minute overall metadata
budget and bounded individual reads. TUF checks root rotation, signatures,
version rollback, expiration, target hashes and catalog signatures. Failure
retains the last-good searchable catalog and learned rollback floors. Removal
and expiry never delete model copies. Exported caches retain signed evidence,
not the receiver's local transport permission.

## Receive an exact model

For a selected entry whose signed catalog advertises I2P mirrors, use the source
selection directly; no manual mirror URL is needed:

```sh
model-preserve i2p-http source-fetch --cache /data/discovery \
  --source ENROLLED_SOURCE_ID --entry SELECTED_ENTRY \
  --expected-manifest-id MANIFEST_SHA256 --trust independently-enrolled-model.json \
  --store /data/received --allow-i2p
```

This retains the existing exact-selection and independent-model-trust checks.
Public/local mirror hints never turn into an alternate network route under this
I2P policy. If no supported I2P mirror supplies the selected model, receiving
fails and retains partial bytes.

Alternatively supply independent model trust and its exact manifest, plus only I2P mirrors:

```sh
model-preserve i2p-http fetch MANIFEST_SHA256 \
  --mirror http://HASH.b32.i2p/archive --trust independently-enrolled-model.json \
  --store /data/received --allow-i2p --max-bytes 21474836480 \
  --deadline-seconds 3600
```

This command uses the existing legacy archive verifier and complete-companion
fetcher. It authenticates the original envelope before object requests and
publishes only after every object hash passes. Original cards, unknown retained
properties, provenance bytes and envelope signatures are copied unchanged.
Candidate model keys from a catalog do not replace the supplied trust profile;
registry provenance retains its separate enrolled-root admission checks. No
model code executes.

HTTP Range requests and existing partial-file revalidation/resume remain in
place. Size, deadline, disk and byte-budget checks still apply. Stopping or
failure keeps partial/complete files for explicit inspection/resume. No local
model or router data is deleted. Direct PMF archive-object receiving is not yet
part of this command; existing signed PMF kits still use offline import or the
separately consented I2PSnark kit path. Public direct PMF transport remains open.

## Exposure and qualification

The local proxy sees destinations/paths. The remote mirror can observe requested
paths, timing and an I2P client destination; repeated requests may be linkable.
There is no application cookie, referrer, user-agent/version or installed-model
inventory header, but the selected proxy can transform headers and maintain its
own persistent identity. Signed metadata prevents undetected substitution; it
does not conceal requested model names or make a malicious local proxy safe.
Search remains local and sends no query text.

Conformance uses synthetic source/model data, adversarial local proxies and an
opt-in stock I2P HTTP proxy/server across private routers with three-hop tunnels.
This is distinct from a public-network, packet-capture, independent-review or
vulnerable-user qualification. Report actual observation coverage before making
privacy recommendations. Do not test political-risk claims on vulnerable users.

Primary references: [I2P naming and base32](https://www.i2p.net/en/docs/overview/naming/)
and the [selected stock HTTP proxy source](https://github.com/i2p/i2p.i2p/blob/3c98503c5f14efe7bdd783528ba7f621f12d8081/apps/i2ptunnel/java/src/net/i2p/i2ptunnel/I2PTunnelHTTPClient.java).

## Reproduce the public-network drill

This separate developer/operator harness is opt-in and never runs during
installation or normal CI. Use the reviewed source distribution/checkout, the
pinned Snark build and its I2P source from `scripts/build_i2p_runtime.py`, and
explicitly build `buildI2PTunnelJars` with the same selected Ant/Java toolchain.
Run from that reviewed source with its default Python dependencies:

```sh
python scripts/qualify_i2p_http_public.py --directory /new/private/evidence \
  --java-home /explicit/jdk --i2p-source /explicit/pinned-i2p-source \
  --runtime /explicit/snark-runtime --seconds 600 --kib 32 --payload-mib 1 \
  --allow-public-i2p
```

The directory must be new. The script validates synthetic model/source fixtures
offline first, then owns two fresh public IPv4 SSU routers and stock HTTP
client/server helpers. It uses generated model/companion bytes, separate metadata
and model keys, configured three-hop tunnels, zero configured transit share,
no router console/plugins/UPnP and no pre-existing router state. `--kib` bounds
the router's configured rate; it is not a hard wire quota, and bootstrap traffic
and protocol overhead need separate observation. Native EOF/lifetime fences and
parent cleanup stop only the owned helpers and retain their evidence directory.

The report records the exact native JAR hashes, configured profile, stage,
router allocation counters, application transfer time and recovery result.
Generated weight-like bytes use `os.urandom` to avoid a misleading compression
speedup. The protected application phase refuses OS DNS calls; that is an
instrumented application check, not packet-level proof about the router.
Public routers still use their own bootstrap/routing dependencies. The two
routers share one test host, so this is not separate-host/NAT or independent
custody qualification. A small or moderate synthetic transfer is not a complete
large-model performance claim. Raw router logs, addresses and generated keys
stay private; publish only reviewed, scoped observations.
