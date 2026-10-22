# Send and receive a public model invitation

An invitation is a small JSON file containing the original signed public
manifest, candidate public keys and up to 16 HTTP(S) mirror bases. It carries
the complete declared model and companion selection without weights, private
keys, a Foxlight account or a required online resolver. Reading it is offline.
Sending this file does not start a server or prove that any mirror is reachable.

## Publisher: prepare the handoff

First create and verify a separate [public publication](public-sharing.md), then
host its `manifests/` and `objects/sha256/` directories on an ordinary read-only
mirror. Export an invitation using that public copy's exact identity and keys:

```sh
model-preserve invite PUBLIC_MANIFEST_SHA256 --store /public/model \
  --trust publisher-trust.json --mirror https://mirror.example/models \
  --mirror https://another.example/models --output model.invitation.json
```

Export reads only bounded signed metadata. It does not rehash weights or check
hosting. The output must be a new file. Mirror bases cannot contain credentials,
query strings, fragments, filesystem paths or non-HTTP schemes. They are hints,
not authority; they can change without changing the model's signed identity.
Do not put private URLs or access tokens in an invitation.

Send the file and separately authenticate its manifest ID and publisher key
fingerprints through a channel the recipient trusts. An attacker replacing the
invitation and its keys can create a different self-signed model. A self-signature
alone does not establish the publisher's identity or registry provenance.

## Recipient: inspect before connecting

```sh
model-preserve inspect-invitation model.invitation.json
```

This checks candidate signatures, exact manifest identity, public audience and
structural bounds. It reports the name, total bytes, complete member list,
fingerprints, claimed threshold and mirrors. It makes no network requests and
does not verify stored weights, identify the publisher, enroll keys, restore a
model or authorize execution. The original model-card data remains opaque to
this container inspection, including unfamiliar future card profiles.

Use an independently retained trust file when available:

```sh
model-preserve fetch-invitation model.invitation.json \
  --trust publisher-trust.json --store ./received \
  --max-bytes 21474836480 --deadline-seconds 3600 --operation-id receive-model
```

For first contact, deliberately accept independently authenticated fingerprints
instead of supplying `--trust`:

```sh
model-preserve fetch-invitation model.invitation.json \
  --accept-key ed25519:REPLACE_WITH_AUTHENTICATED_FINGERPRINT \
  --store ./received --operation-id receive-model --save-trust accepted-trust.json
```

Repeat `--accept-key` for a threshold publication. Authenticate the intended
threshold too: it is candidate policy supplied by the sender. This creates a
choice for this exact copy, not global enrollment or a replacement for an existing
revocation policy. Keep the selected keys, manifest ID and invitation for later
verification. The default object allowance is 20 GiB; choose a sufficient finite
allowance for the model and possible retries. Reuse the same operation ID, store,
trust and transfer policy after interruption to retain the cumulative allowance.
Changing those inputs under the same ID refuses rather than resetting its budget.

`--save-trust` retains the selected public policy for later verification and
recovery-kit export. It writes no private key. An existing identical policy is
reused on retry; a different one refuses before networking. This is a local file
you explicitly retain, without enrolling its keys globally or as registry keys.
The selected policy must authenticate this invitation before it is saved. The
final file appears atomically after its complete contents are flushed; a crash
may leave a temporary file of public keys, without a partial final policy.

The downloader authenticates the included manifest before contacting a mirror,
then authenticates the fetched manifest before requesting weights. Every actual
object passes its signed length and SHA-256 check before completion. It tries
only the supplied mirrors. HTTP and non-global addresses require separate
`--allow-http` and `--allow-private` opt-ins; neither flag disables HTTPS
certificate checks. See [transfer limits](protocol.md#transfer-and-serving).

Downloading does not share, seed, install, execute or grant registry authority.
The network address used by the downloader and requested files are visible to
the contacted mirrors. This is direct receiving with integrity checks, without
an anonymity guarantee.

## Keep and recover independently

The downloaded store works with ordinary `verify`, `kit-export` and `restore`.
Those commands require the same independently selected trust profile and exact
manifest ID. See [trust and recovery](trust-and-recovery.md). A complete recovery
kit is also the handoff when recipients cannot reach a public mirror or when
metadata exceeds the invitation's 8 MiB bound. Kits and invitations are not
encrypted. Keep recovery material outside the original host's failure domain.

## Format and application API

The schema is
[`foxlight.model-preservation.invitation.v1`](schemas/Invitation.json). The
wrapper is not separately signed. Its embedded DSSE payload and original
signatures are preserved, so changing transport hints cannot alter the signed
bundle or grant mirror authority. Unknown wrapper fields/versions, duplicate
JSON keys, bad signatures/digests, private members and unsafe mirror syntax
refuse before receiving. JSON schema describes shape; cryptographic and custom
path/graph checks require the verifier.

`invitations.read_invitation(bytes)` is a bounded offline inspector.
`create_invitation(store, id, trust, mirrors)` reads only selected metadata.
`authenticate_invitation(invitation, trust)` uses caller-selected trust.
`accept_invitation_keys(invitation, key_ids)` makes an explicit first-contact
choice; a UI must make the trust implications clear.
`fetch_invitation(invitation, trust, destination, policy, operation_id=...)`
reuses the ordinary downloader and its cancellation/progress controls. Run it
in a bounded worker. No peer-supplied value authorizes reading a local host path.
