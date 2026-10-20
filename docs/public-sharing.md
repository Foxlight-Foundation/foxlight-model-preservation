# Publish a model and optionally seed it

Public mirrors and BitTorrent carry the same signed bundle. Hosting does not
grant a mirror authority to change its identity or registry provenance. Ordinary
`fetch`, verification and recovery remain receive-only and do not use BitTorrent.
This software does not provide anonymity.

## Create a separate public publication

A private recovery archive can contain installed observations and prior local
history. Never serve that archive folder or mark its existing manifest public.
Create a separate publication and public publishing key instead:

```sh
model-preserve keygen --key publisher.pem --trust-out publisher-trust.json
```

Keep that key outside every archive/mirror root. A `PublicationRequest` selects
the public name, workloads, runtime requirements and terms for **every retained
member**, including original notice members. For example:

```json
{
  "name": "My model publication",
  "workloads": ["text"],
  "runtime_requirements": [],
  "members": [{
    "member_id": "primary",
    "license_id": "THE_ACTUAL_MODEL_LICENSE",
    "terms_reference": "LICENSE",
    "decision": "I reviewed the applicable terms for public redistribution.",
    "notice_paths": ["LICENSE"]
  }]
}
```

Use the source manifest's actual member IDs and original notices. There is no
automatic license decision. The publisher's declaration does not grant rights.
An unresolved private-capture notice cannot substitute for original notices.

```sh
model-preserve plan-publication PRIVATE_MANIFEST --store /private/archive \
  --trust capture-trust.json --request publication.json

model-preserve publish PRIVATE_MANIFEST --store /private/archive \
  --trust capture-trust.json --request publication.json --key publisher.pem \
  --expected-manifest-id REVIEWED_PUBLIC_MANIFEST --output /public/model
```

Review the complete proposed public manifest before using its exact ID. Publication
verifies source bytes again, requires a distinct signing identity and publishes
only the selected public objects and new signed envelope. The recognized
`source-evidence` notice member and generated private-capture notice are omitted;
other original notice members remain required. Known operational filenames are
refused, and retained metadata must use the explicit public card/proof profile.
Private installed records, earlier local code grants and private capture identity
are not added to the public manifest. Original model/card identity remains visible.

This is not a general secret scanner: deliberately selected model files and
retained public cards can themselves contain sensitive data. Inspect the proposed
fields and content before publishing. It starts no server or peer participation.

## Host an ordinary mirror

Point an ordinary read-only static HTTPS server at `/public/model`, or use the
bounded reference `seed` described in [the mirror guide](mirrors.md). Share its
base URL, exact public manifest ID and independently authenticated public trust
material. The hosting operator accepts public exposure; the mirror can observe
downloader addresses and requests. Do not expose the Skulk API or storage/key
management listener as a mirror.

## Optional BitTorrent export

Install the optional maintained binding, separately from default verification:

```sh
uv tool install '.[bittorrent]'
# Or: python -m pip install '.[bittorrent]'
```

The adapter pins libtorrent 2.0.14; installation needs a compatible native wheel.
It is tested on the CI's Linux/macOS and Python 3.11/3.13 combinations, not every
platform. No packages are downloaded or installed automatically at runtime.

```sh
model-preserve torrent-export PUBLIC_MANIFEST --store /public/model \
  --trust publisher-trust.json --output /public/export \
  --web-seed https://mirror.example/models/
```

The fresh export has this portable layout:

```text
PUBLIC_MANIFEST/
  manifests/PUBLIC_MANIFEST.json
  objects/sha256/...
PUBLIC_MANIFEST.torrent
torrent.json
```

Serve `/public/export` with the static server. An ordinary receiver uses mirror
base `https://mirror.example/models/PUBLIC_MANIFEST`. A torrent web seed uses
its parent `https://mirror.example/models/`; the standard client appends the
torrent's manifest-ID folder. HTTP web seeds require `--allow-local-http` explicitly
for trusted tests; default web-seed metadata permits only credential-free HTTPS.

Export authenticates and copies selected public bytes and creates standard v1
torrent metainfo. It opens no listener or discovery session. BitTorrent piece
hashes aid transfer; the signed manifest's SHA-256 verification remains the
authority. Creating metainfo does not create an independently retained replica.

## Explicitly start and stop seeding

```sh
model-preserve torrent-seed PUBLIC_MANIFEST --store /public/export \
  --trust publisher-trust.json --public --bind 0.0.0.0 --port 8126 \
  --duration 3600 --upload-rate 1048576 --connections 8 \
  --stop-after-payload-bytes 1073741824
```

`--public` explicitly authorizes public DHT and peer exchange. Without it the
session is loopback-only with public discovery disabled. LAN discovery, UPnP and
automatic port mapping are always disabled. The worker strips metainfo supplier/
tracker hints and uses seed-only mode, never downloading missing model data.
Peers may observe network addresses and the offered torrent; this is public
participation, not anonymous sharing. Do not assume routing an ordinary torrent
client through Tor makes it private.

The worker independently verifies the signed public selection and metainfo/file
mapping before opening its session. A monitor enforces lifetime and observed
payload thresholds even when nobody polls status. Ctrl-C stops this owned session
and retains the export. It closes its listeners without signalling other clients
or deleting data. Confirm the terminal result before reporting sharing stopped.
`--progress` reports one `seed` operation through checking, sharing and its final
stopped outcome; successful verification alone is not job completion. Library
callers emitting generic progress own a `control.operation("seed")` scope around
the complete session lifetime and close the seeder in `finally` before it exits.

Upload rate and connection count are native limits. **The payload byte threshold
is not a hard quota**: in-flight/native buffered traffic can overshoot before
observation and shutdown. Reported bytes exclude protocol overhead and are not
network billing. This first standalone session does not provide durable daily
accounting or restart itself. Restart is a new explicit session/allowance.

A standard external torrent client can receive the export. Before loading it,
verify using independently enrolled public trust and the expected manifest ID:

```sh
model-preserve verify PUBLIC_MANIFEST --store /downloads/PUBLIC_MANIFEST \
  --trust publisher-trust.json
```

The supplied torrent or mirror cannot enroll its own signing key. This adapter
does not add a torrent-receiving path to ordinary `fetch` or automatically seed
a received model. Retained registry proof, repository-code permission, runtime
admission and actual inference remain separate checks.
