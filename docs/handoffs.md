# Share one model with one file

A `.foxhandoff` is metadata, not weights. It carries an unchanged signed model
invitation or followed-source reference and, for peers, up to eight numeric
starting contacts. Review and receive without Skulk, an account or a Foxlight
service. These commands are available from the current source checkout; consult
the [release notes](../CHANGELOG.md) for your installed version.

The current independent app also provides offline HTTP handoff review and
explicit one-hour HTTP sharing. See [the app workflow and privacy limits](sovereign-desktop.md#backup-folders-and-public-sharing-in-current-source).
Peer/source handoffs retain the standalone mechanisms below.

## Publisher

For an existing public HTTPS mirror, wrap an ordinary invitation offline:

```sh
model-preserve invite MANIFEST_SHA256 --store ./public-copy \
  --trust independently-selected-trust.json \
  --mirror https://mirror.example/models --output model.invitation.json
model-preserve handoff-create --reference model.invitation.json \
  --output model.foxhandoff
```

For optional peer sharing, install the `peer` extra and the
[qualified native runtime](peer-discovery.md#install-and-start-a-receiver):

```sh
model-preserve peer-share --store ./public-copy --manifest MANIFEST_SHA256 \
  --trust independently-selected-trust.json --repo ./new-sharing-peer \
  --ipfs /absolute/path/to/verified/ipfs --output model.foxhandoff \
  --allow-peer-network --share-public --public-model \
  --bootstrap-address /ip4/PUBLIC_IP/tcp/PORT/p2p/PEER_ID \
  --seconds 3600 --max-upload-bytes 1073741824
```

The new repo's parent must exist. Existing repos are refused, preventing silent
rehosting of old pins. No routing service is selected by default. The tool
verifies the complete public model/companion closure, pins it, then atomically
publishes the file. No signing key is needed. Existing output files and symlinks
are refused. Send the file through a channel you choose; the tool does not send it.

Keep the command running while sharing. Ctrl-C stops the actual supervised
native daemon; abrupt process loss closes its lifetime fence. Expiry and the
observed upload threshold also stop sharing. **Stopping retains the archive and
handoff.** Restart needs a new explicit session and fresh repo. No service is
installed or replayed. The operator controls deletion, including old peer repos.

Contacts are routing suggestions, not proof of reachability. Fallback contacts
can help find another provider retaining the identical CID. The tool does not
arrange NAT traversal, relay service or another operator's retention. LAN peers
require both sides to select `--allow-private-peers` independently; a received
file cannot grant it.

## Recipient

```sh
model-preserve handoff-inspect --handoff model.foxhandoff
model-preserve handoff-fetch --handoff model.foxhandoff --store ./received \
  --bundle-trust independently-selected-trust.json --repo ./new-receiving-peer \
  --ipfs /absolute/path/to/verified/ipfs --allow-peer-network \
  --seconds 3600 --max-bytes 21474836480 --operation-id received-copy
```

Inspection is entirely offline. Authenticate the candidate fingerprint through
an independent channel or use an existing trust file. An explicitly reviewed
`--accept-key ed25519:FINGERPRINT` may replace `--bundle-trust`; it authorizes
only this copy and does not enroll registry, catalog or code signers. The file
alone cannot prove who the publisher is. Authentication precedes native startup
or network requests. Receiving is receive-only and drains its daemon before
returning; it never begins serving the model.

For HTTPS, omit peer options and provide only the handoff, store and trust.
Unencrypted/private HTTP still needs its separate `--allow-http` and
`--allow-private` grants. HTTP handoffs cannot carry peer contacts.

Only complete verified generations become available. Retries keep the same
durable operation ID and allowance, reuse verified archive objects and start a
fresh peer repo. Restore normally with `model-preserve restore MANIFEST_SHA256
--trust independently-selected-trust.json --store ./received --output ./restored`.
All companions, cards and signed provenance remain unchanged. Registry evidence
still requires its independently enrolled root; owner captures and routing hints
never become registry trust. Code execution permission remains separate.

## Sources and embedding

Wrap an existing `.foxpeer` using `handoff-create --contact MULTIADDRESS`.
`handoff-inspect` reports the unchanged **inner peer/source IDs** separately from
the wrapper hash. Follow that independently selected inner peer ID offline:

```sh
model-preserve handoff-follow --handoff source.foxhandoff --cache ./discovery \
  --expected-peer-id PEER_SOURCE_SHA256
model-preserve handoff-refresh --handoff source.foxhandoff --cache ./discovery \
  --repo ./new-discovery-peer --ipfs /absolute/path/to/verified/ipfs \
  --allow-peer-network
```

Refresh uses the already followed source, original TUF root, version floors and
expiry checks. Historical snapshots remain explicitly historical. Source
authority cannot enroll model signers. Cache export remains its existing format.

The closed [handoff-v1 schema](../src/foxlight_model_preservation/resources/handoff_v1.json)
permits at most 8 MiB for model wrappers and 256 KiB for source wrappers.
Unknown versions/fields, duplicate keys and unsafe references fail closed.
The wrapper hash covers unsigned routing metadata; it is not an attestation.
Changing contacts leaves the signed model identity unchanged. Numeric IP/TCP
contacts exclude DNS, gateways, credentials, executable URLs and local paths.
Old readers continue to refuse this new version; legacy references stay valid.

Python applications use `handoffs.read_reference`, `reference_contacts`,
`read_handoff`, `write_handoff` and `handoff_identity`. `peer_session.owned_peer`
provides finite standalone supervision. `peer_sharing.serve_peer_model` is a
synchronous primitive for a host-owned worker with an EOF/cancellation fence and
actual process-group drain. Its independent wire guard remains active during
slow preparation; exhausting the allowance before readiness fails preparation
and stops the native daemon. No module imports Skulk or its SDK.

Peer sharing is public, not anonymous: peers may observe addresses, content IDs
and timing. Native upload accounting is an observed threshold with in-flight
overshoot, not a hard wire quota. Native cache, export staging and archives all
consume disk; use operator quotas for hard limits. There is no automatic
eviction/deletion. Network publication currently supports the public
preservation-v1 bridge. Generic PMF-only documents/profiles need a compatible
adapter and are never silently discarded; see [PMF](portable-model-format.md).

Controlled local conformance covers identical-file recovery from a retained
provider after the original publisher stops, full companion restore and native
shutdown after Ctrl-C or abrupt caller loss. It does not qualify Internet-wide
availability, independent operator custody, anonymity or censorship resistance.
