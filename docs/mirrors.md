# Operate a mirror

A mirror needs only the standard `manifests/` and `objects/sha256/` directories.
It needs no Foxlight account, signing key, Skulk cluster, GPU or dedicated store
server. Copy those directories to storage you control and expose them read-only
through an ordinary HTTPS static server. Preserve required notices within the
objects. Disable listing, uploads, management, and forwarding. Use your own TLS
certificate and ordinary hosting controls. Keep the writer's private key and any
other files outside the served root. Verify the copied store before advertising it.

One filesystem copy is also a usable offline or mounted-NAS source. Consumers can
mix local folders and HTTPS mirrors in one bounded fetch. Stop your first server
and repeat the download from an empty consumer store to test the second copy.
Record what was tested, including the exact manifest IDs and last full verification.
Several hosts/accounts controlled by one operator remain one administrative domain.

For a small opt-in reference seed:

```sh
model-preserve seed --store /srv/model-store --trust bundle-trust.json \
  --manifest MANIFEST_SHA256 --bind 0.0.0.0 --public \
  --cert /secure/server.crt --tls-key /secure/server.key \
  --max-upload-bytes 1073741824
```

Every selected member must have a public intake declaration. That record does not
replace your responsibility to check the exact redistribution terms, attribution,
notices and audience for all required members. Private/restricted captures are
refused by public seed. A static server does not enforce this check for you.

The reference seed defaults to loopback, four concurrent workers, 30-second socket
timeouts and a 1 GiB reserved upload budget. It serves only the selected verified
identities. Stop it to stop sharing. It does not announce itself, join a swarm,
repair replicas, automatically restart or remove your copies. For trusted local
exercises only, explicitly use `--allow-http`; the client also needs
`--allow-http --allow-private` for loopback/LAN endpoints. Standard static HTTPS
hosting avoids a required long-lived Python process.

Create an independent signed list by writing a catalog with a description and
entries shaped as `{"entry_id":"my-model","manifest":"64-hex-id",
"mirrors":["https://my.example/models"]}` and
`"schema_version":"foxlight.model-preservation.catalog.v1"`. Sign it:

```sh
model-preserve catalog-sign --index catalog-input.json --output catalog.json \
  --key catalog-owner.pem
```

Distribute the printed exact catalog ID, authentic catalog trust profile, bundle
trust profile, software and catalog envelope through independently authenticated
channels. Catalog keys may differ from bundle keys. Publishing a mirror URL does
not grant its operator permission to change a bundle or sign a new model capture.
A consumer can also use an exact manifest and explicit mirrors without a catalog.

No externally administered mirror deployment is included in this repository.
The tests run isolated local servers and exercise supplier failure, corruption,
range recovery, endpoint restrictions and bounded upload/download. They establish
interoperable mirroring behavior, not independent custody, global availability,
censorship resistance or anonymity.
