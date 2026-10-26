# Public cards and offline provenance

Anyone can create a model card and preserve or mirror its files. Creating a
`card_…` identity does not require Foxlight, registry publication or a hosted
account. A card describes the model; the preservation manifest selects a complete
workload and binds exact file bytes; independently enrolled signatures identify
who made which statement. These are separate checks.

The standalone core does not install or execute models. An inference application
must explain provenance to its user, validate its own runtime semantics, obtain
any required repository-code permission, and install through its normal store.
Accepting a provenance warning must not silently authorize repository code.

[Portable Model Format draft 0.1](portable-model-format.md) adds a standalone
common archive schema and optional ecosystem profiles. Its preservation bridge
retains the full original Skulk card and signed evidence, and can recover the
unchanged v1 archive. Common PMF verification requires no Skulk/card interpreter;
the original provenance still needs its independent verifier and trust root.

## The open card contract

[`ModelCardEnvelope.json`](schemas/ModelCardEnvelope.json) describes the existing
v1/v2 envelope: alias, immutable `card_id`, pinned origin repository/revision,
selected artifact and body. Version 2 selects an exact artifact bundle. A bundle
records paths, sizes, optional upstream object IDs and an optional loader root;
an alternate location changes neither origin authority nor the file selection.

[`SkulkModelCardBodyV1.json`](schemas/SkulkModelCardBodyV1.json) is the frozen
structural body profile derived from public Skulk commit
`0b6f71da14777ddfbcbf47a278edb3cda7366ac9`. It covers the published model,
runtime and capability fields. Local projections (`is_custom`,
`qualification_only`, `registry_*`) are excluded. It is packaged with the
verifier, so schema validation requires no network lookup or Skulk import.
The preservation reader uses the additive
[`PreservedModelCardBodyV1.json`](schemas/PreservedModelCardBodyV1.json) profile,
derived from that frozen writer schema. New descriptive properties at any depth
and new string vocabulary names are retained without interpretation. Known field
types and constraints still validate. Receiver-only `registry_*`, `is_custom` and
`qualification_only` properties remain prohibited. Nothing is silently discarded
or rewritten, and all retained values participate in the existing card identity.

An actual runtime must still validate cross-field semantics, companion revisions
and supported vocabulary. Retention is not runtime admission. Selected artifact
bundles/files, the card envelope and schema versions remain closed; incompatible
changes to their interpreted semantics require an explicit versioned change.
Existing cards and captures keep their identities. Library `0.5.1` introduces this
additive reader contract; consumers using `0.5.0` or earlier need one upgrade to
retain future descriptive metadata. Previously released readers are unchanged.

Card and selected-bundle IDs retain the existing registry encoding: SHA-256 of
sorted, compact UTF-8 JSON (`ensure_ascii=False`, `allow_nan=False`), encoded as
lowercase unpadded base32 with `card_` or `bundle_`. Card seeds contain every
envelope field except `card_id`, including the alias. Bundle seeds are sorted
rows of `relative_path`, `size_bytes`, `object_id`; remove the declared root
prefix from paths. Optional artifact fields are expanded to their defaults.
Historic v1 IDs also permit the precise pre-v2 seed that omitted only
`artifact.bundle=null`. New cards use the current expanded form. Integers and
floats retain their JSON representation in these identities. This domain differs
from RFC 8785 preservation manifests. Never normalize one as the other.

```sh
model-preserve card-create --body body.json --output card.json
model-preserve card-verify --card card.json
model-preserve card-retain --card card.json --output retained-card.json
```

`body.json` uses snake_case field names. An optional `--alias organization/name`
selects a different artifact alias. `card-verify` validates content identity and
structure, and reports `publication: not-established`. It does not imply a
registry signature. Outputs are created exclusively, without overwriting files.

## Retaining actual registry publication

[`RegistryProof.json`](schemas/RegistryProof.json) packages exact signed TUF
timestamp, snapshot and top-level targets bytes, the exact target payload, a
root fingerprint and up to 32 consecutive root rotations. Roots use Ed25519 in
this profile. The archive does **not** enroll a trust root: receivers supply one
through an independently authenticated channel. Re-serializing metadata can
break its parent length/hash bindings, so retain the original bytes.

The profile supports `v1/cards/<exact-card-id>.json` and `v1/catalog.json`.
Both prove card publication; only the catalog carries the separately signed
`foxlight`, `agent` or `community` provenance classification. An individual card
proof reports that classification as `null`, rather than inventing one.
Delegated target discovery is outside this bounded v1 profile.

With already retained, verified metadata and a receiver-enrolled root:

```sh
model-preserve proof-create --root enrolled-root.json --card-id CARD_ID \
  --timestamp timestamp.json --snapshot snapshot.json --targets targets.json \
  --target-path v1/catalog.json --target catalog.json --output proof.json

model-preserve proof-verify --proof proof.json --root enrolled-root.json \
  --card-id CARD_ID

model-preserve card-retain --card card.json --proof proof.json \
  --root enrolled-root.json --output retained-card.json
```

Replace `CARD_ID` with the complete immutable ID. For rotations, repeat
`--root-chain 2.root.json --root-chain 3.root.json` in consecutive version order
starting immediately after the enrolled root. Each successor must satisfy both
the previous root threshold and its own root threshold; the final root verifies
the timestamp/snapshot/targets roles. SHA-256, exact lengths and matching
versions bind the complete chain to the selected card. An old proof cannot be
verified from an unrelated newer root: retain the authentic historical anchor
and the required rotation chain.

The commands and library never fetch missing metadata or trust material. A proof
is bounded to 8 MiB including base64 overhead, with JSON structure, root key and
signature limits. Unsupported profiles, invalid signatures, a different card,
contradictory metadata or a wrong root fail. There is no automatic downgrade
after a failed registry proof.

## Fresh statements and historical recovery

Fresh mode is the default. Final root, timestamp, snapshot and targets must be
unexpired at receiver time. Receivers can pass `--minimum-versions floors.json`,
containing the positive integer fields `root`, `timestamp`, `snapshot`, `targets`,
to enforce independently retained rollback floors. Save and manage floors in
the receiving application; this library does not mutate update-client state.
Fresh mode establishes expiry and supplied floors, not a network search for the
latest publication.

For deliberate offline recovery after metadata expires:

```sh
model-preserve proof-verify --proof proof.json --root enrolled-root.json \
  --card-id CARD_ID --historical --minimum-versions floors.json
```

Historical mode still verifies every signature, rotation, version/hash/length
binding, exact card and supplied rollback floor. It explicitly permits expired
metadata and reports the mode, expired roles, checked time, versions and anchor.
It proves publication under the retained trust chain, not current registry
approval. It cannot discover a later compromise, revocation or advisory after
the last supplied root. `revocation_scope: supplied-root-chain-only` makes that
limit explicit. This narrow opt-in does not disable expiry in an ordinary TUF
client or change its cache. Historical mode is a receiver decision; the archive
cannot select it. Pass `--historical` explicitly to `card-retain` when retaining
such a proof.

## File evidence stays separate

Put the object from `retained-card.json` in the recipe member's `retained_card`.
The owner signature then retains the exact public card and any registry proof
as part of the capture. `card_base64` retains the card's exact identity encoding:
embedding a plain card object would let RFC 8785 change whole-number floats
such as `1.0` to `1`, breaking a registry-domain card identity. This is an optional typed profile
([`RetainedCard.json`](schemas/RetainedCard.json)); older captures with arbitrary
retained metadata still verify and restore under the unchanged bundle v1 format.
They cannot use typed evidence inspection until an appropriate new capture is
created. Preserve original evidence rather than rewriting an existing identity.

```sh
model-preserve inspect-evidence MANIFEST_SHA256 --store ./preserved \
  --trust owner-trust.json --root enrolled-root.json
```

This rehashes every required stored object before reporting each member:

| Field | Meaning |
| --- | --- |
| `publication: owner-only` | An open card retained by the capture owner; no registry proof. |
| `publication: registry-fresh` | The exact card passed fresh proof verification. |
| `publication: registry-historical` | The exact card passed explicitly selected historical verification. |
| `file_coverage: complete-sha256` | Every captured path/length/SHA-256 agrees with that card's exact selected bundle. |
| `file_coverage: partial-or-missing` | Some paths lack card-bound SHA-256 evidence; `unbound_paths` lists them. |
| `qualification: not-assessed` | These checks make no claim of successful inference or runtime safety. |

A registry card with no exact bundle, optional missing object IDs or Git SHA-1
object IDs does not gain full SHA-256 coverage from newly owner-computed hashes.
Extra captured files also remain unbound. Missing selected files, changed sizes,
contradictory SHA-256 or origin/revision/loader/identity mismatches fail. The
capture's `artifact_identity` must equal the retained card or its selected bundle
ID. Format and layout must match. Run these checks for **every** member; a proof
for the primary model says nothing about an unproved companion. Completeness is
relative to the manifest selection, not an automatic discovery of loader inputs.

An owner-only card can have complete file hashes, and a registry-published card
can have incomplete file evidence. Applications must preserve both distinctions.
No output is an execution grant. A user can deliberately choose a community
model while a runtime keeps registry assurance visibly distinct.

## Library boundary

`cards.create_card` / `load_card` construct and validate public envelopes.
`registry_proof.create_registry_proof` packages exact retained bytes;
`verify_registry_proof` returns a `VerifiedCard` and `ProofReport` after checking
them against the caller's independent root, exact ID, mode and floors.
`assurance.retain_card` creates the encoding-preserving profile;
`assurance.inspect_member_evidence` binds a manifest member to its typed retained
card/proof and reports file coverage. That helper checks metadata; unlike the
CLI's `inspect-evidence`, it does not read stored object bytes. Call
`storage.verify_store` first. These values are reports, not transferable runtime
authorization tokens. The receiver must derive decisions from verified input,
not trust a serialized report supplied by a peer.

The [Library's model descriptions](sovereign-desktop.md#model-information) are
optional display projections of those original documents. They distinguish
declared model uses and input/output types from selected-file coverage and
runtime qualification. Captured automated interpretations stay attributed and
do not supply primary capability labels. Reading a description grants no trust
and never rewrites a card, proof or archive identity.
