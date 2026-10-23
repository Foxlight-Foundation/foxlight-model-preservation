# Portable Model Format — draft 0.1

PMF is an experimental, open model **meta-format**: a small common archive
contract plus optional ecosystem profiles and converters. It preserves selected
model files, required companions, source cards and signed evidence without
requiring Skulk, a model hub, a GPU, an OCI registry or a hosted account.

This implementation supplies the common schema, local pack/verify/restore tools,
and a lossless bridge for existing preservation-v1 archives, including Skulk
captures. It does not claim ModelPack, CycloneDX, SPDX or OpenSSF Model Signing
conformance. Tensor formats and execution semantics remain the responsibility of
their original formats and runtimes. PMF does not convert weights or execute code.

The draft is explicitly versioned as `pmf.manifest.v0.1`. Its unregistered draft
DSSE payload type is `application/vnd.pmf.manifest.v0.1+json`. Existing v1
preservation archives and recovery kits keep their own versions, media types,
identities and signatures. They remain usable without migration.

## Common schema

The [published JSON Schema](schemas/PMFManifestV0_1.json) is also packaged at
`foxlight_model_preservation/resources/pmf_manifest_v0_1.json`. The executable
models are in `pmf_models.py`; the verifier additionally checks graph, path,
reference, digest-size and supported-feature invariants that JSON Schema alone
cannot express.

| Field | Contract |
| --- | --- |
| `name`, `root_member`, `members` | One declared complete workload closure. Each member has a unique namespace, role, format, exact file table, required companion references and notice/intake declaration. |
| Member `files` | Canonical relative path, integer byte length, lowercase SHA-256. The actual stored bytes must match all three. |
| Member `origin` | Optional source statement. Repository and revision may be unknown. When a revision is given, its scheme is explicit, such as `git-sha1` or `release-label`. No synthetic Git commit is required. |
| Member `artifact_identity`, `loader_root` | Optional source identity and relative directory. They convey no execution or provenance authority. |
| `documents` | Bounded exact source bytes, stored as SHA-256 objects. Each record has an ID, optional member binding, role, media type, optional source schema/version, size and digest. |
| `profiles` | Versioned interpretation identified by `profile_id`, with explicit document and optional member bindings. Optional unknown profiles remain preservable without being interpreted. |
| `required_features`, profile `required` | Must-understand semantics. Draft 0.1 implements no additional required features/profiles, so any such requirement refuses pack, verify or restore. |
| `annotations` | Optional descriptive common metadata. It cannot amend file selection, grant provenance, or introduce required execution behavior. |
| `workloads`, `runtime_requirements` | Descriptive owner statements; not a promise of compatible execution. |
| `attestation` | `owner-capture`. A PMF signing key never implicitly becomes an upstream author or registry key. |

All structural fields are closed and strictly typed. New interpreted structural
semantics need an explicit supported version/feature. Additive source-card
properties belong in exact source documents; no runtime-specific field is
mandatory in the common schema. Unknown descriptive documents can survive even
when the source's card schema cannot yet be interpreted.

Each record's identity covers its exact bytes, including whitespace, numeric
representation and original signed payloads. PMF's own payload uses RFC 8785, so
common annotation numbers do not preserve JSON spelling such as `1.0` versus `1`.
Keep authoritative originals in source documents rather than normalizing them
into common annotations.

Limits reuse the existing core: signed envelope (including base64 overhead) at
most 8 MiB, at most 256 members and 100,000 member files, portable paths at most
64 components, byte counts within the RFC 8785 safe-integer domain. Each source
document is at most 8 MiB; at most 1,024 documents/profiles are permitted. Logical
file-plus-document totals are bounded, identical object hashes cannot declare
different lengths, and every required companion must be reachable and acyclic.
The common file-path and notice checks also apply to PMF.

## Storage, signatures and verification scope

```text
manifests/<pmf-payload-sha256>.json   # PMF-domain DSSE envelope
objects/sha256/<sha256>             # model files AND exact source documents
```

The manifest ID is SHA-256 of the exact canonical PMF payload. Signatures reuse
the existing DSSE PAE, Ed25519 key IDs, independent trust snapshots and distinct
non-revoked-key threshold verifier. The new payload type separates this domain
from legacy bundles and catalogs. An old bundle verifier refuses PMF rather than
reinterpreting it.

`pmf-verify` checks the independently selected identity/signature and rehashes
every selected file and source document. A source record labelled as a card,
registry proof or signature is **retained evidence**, not automatically verified
provenance. Its original verifier and independently enrolled authority are still
required. Optional profile labels cannot grant trust, runtime admission or
repository-code permission.

Pack and restore copy and hash regular files through descriptor-relative paths.
They publish a new complete directory only after all selected bytes pass, refuse
existing destination names/symlinks and support cooperative worker cancellation.
Restore writes `members/<id>/<path>`, `documents/<document-id>` and
`.pmf/envelope.json`. It never installs, registers or executes a model. A crash
before publication can leave hidden staging or an empty reservation; neither is
a valid archive. A directory flush failure after publication can leave complete
bytes with uncertain durability: verify the existing destination before retrying.
Linux/macOS filesystem, rename/fsync and mounted-storage limits still apply.

These `pmf-*` commands operate on local stores. Recovery kits (v2) and the
Library integration support PMF; see the sections below. PMF-aware network
fetching, invitations and public sharing are not added by this draft. To use
those transport and sharing paths, export the unchanged original v1 archive.
Existing v1 tools never silently gain PMF support by being renamed.

## Existing archive and Skulk profile

`pmf-import-preservation` authenticates the selected original owner capture and
copies every selected file. It retains the **exact original DSSE envelope** as
an `original-signed-archive` document under the optional
`foxlight.preservation.v1` profile. Every member's original `retained_card` is
also made independently accessible as a document. A typed
`foxlight.retained-card.v1` record gets the optional
`skulk.retained-card.v1` profile.

The converter does not deserialize the card through today's runtime classes.
That keeps unfamiliar future card fields/contracts recoverable. A Skulk profile
means these bytes use that retained-record convention; it is not a compatibility
or registry-verification badge.

| Preservation / Skulk source | PMF representation and export behavior |
| --- | --- |
| Member IDs, roles, formats, loader roots, dependencies, explicit files and intake | Native common fields; exact original v1 values must agree on reverse conversion. |
| Origin repository and 40-hex revision | Optional common origin with explicit `git-sha1` scheme. Other PMF producers can omit unknown origin or use a different honest scheme. |
| `card_…`, `bundle_…`, artifact selection | Original artifact identity plus complete retained card/envelope. Their original identity encoding is unchanged. |
| Every model-card property, including nested runtime/engine/capability data and future descriptive extensions | Exact original `card_base64` bytes inside the retained source record. No whitelist, defaults, projection or schema migration rewrites the original card. |
| Registry proof, signed TUF metadata, target bytes, root chain and original card evidence | Complete retained record plus original signed archive. Inner byte strings are unchanged; original proof verification remains independent. |
| Other retained-card profiles, private recovery metadata and selected companion/config/notice files | All source records and selected objects survive, including opaque future profiles. Nothing outside the selected closure is copied. |
| Receiver-local registry flags, installation grants and code permission | No new authority is created. Original recorded evidence stays evidence; the destination runtime makes fresh admission/authorization decisions. |

The reverse converter requires the exact original manifest ID and independent
original capture trust **as well as** PMF trust. It verifies every PMF object,
authenticates the original signature, checks that the entire common projection
and retained records match, and writes the original envelope and objects
unchanged. No original signing key is needed. A different PMF converter key
cannot replace the original capture key or the registry root.

This bridge only supports exact reverse conversion of its preservation-v1
profile. Extra common annotations, changed members or additional/unfamiliar
profiles may be perfectly valid PMF but cannot be exported losslessly to that
original v1 capture, so reverse conversion refuses. Generic PMF without that
source profile can still be verified/restored; it cannot be labelled a Skulk
archive by inventing an origin or new registry signature.

Conversion receipts identify the versioned converter (`pmf-preservation-v0.1`),
installed package version, adapter module's source SHA-256, source/output
schemas and payload digests, original envelope digest, and native,
retained and unrepresentable scopes. Complete retention for this source profile
does not mean all its semantics are native common PMF fields. Opaque retention
is not universal runtime interoperability.

## Commands

Convert an existing verified archive; keep original and converter keys separate
when desired. `--key` uses the existing encrypted-key/password behavior:

```sh
model-preserve pmf-import-preservation ORIGINAL_MANIFEST_SHA256 \
  --store original-archive --trust original-owner-trust.json \
  --output portable-pmf --key converter.pem

model-preserve pmf-verify PMF_MANIFEST_SHA256 \
  --store portable-pmf --trust converter-trust.json

model-preserve pmf-restore PMF_MANIFEST_SHA256 \
  --store portable-pmf --trust converter-trust.json --output portable-files

model-preserve pmf-export-preservation PMF_MANIFEST_SHA256 \
  --store portable-pmf --trust converter-trust.json \
  --source-trust original-owner-trust.json \
  --expected-source-manifest-id ORIGINAL_MANIFEST_SHA256 --output recovered-archive
```

The result of the last command works with existing `verify`, `restore`,
`inspect-evidence`, `kit-export` and Skulk's ordinary archive admission/recovery
path. PMF does not bypass provenance warnings or code consent in Skulk.

A non-Skulk producer supplies a common descriptor with exact precomputed file
identities and, optionally, its own source documents:

```sh
model-preserve pmf-pack --descriptor pmf.json \
  --member model=/path/to/model --member tokenizer=/path/to/tokenizer \
  --document original-card=/path/to/original-card.json \
  --store portable-pmf --key owner.pem
```

Member/document mappings must select exactly the descriptor's IDs. Documents,
profiles and origin can all be absent; omit `--document` when no document is
selected. Pack reads only explicit files and checks actual sizes/hashes. None of
these commands starts sharing, advertises possession or makes network requests.

Python entry points are `pmf_models.PMFManifest`, `pmf.load_pmf`, `pmf.pack_pmf`,
`pmf.verify_pmf`, `pmf.restore_pmf`, and the separate
`pmf_preservation.import_preservation` / `export_preservation` adapter. All byte
work is synchronous and belongs in a bounded background worker. OperationControl
uses the existing `pack`, `verify` and `restore` progress kinds; each invocation
needs its own control. This draft does not add automatic job resumption.

## Reproducible evidence

The [public PMF vector](../examples/conformance/pmf-v0.1.json) has synthetic model
and companion files, an unfamiliar optional card profile, an original JSON
document with `1.0` and an int64, a PMF signature and public key. Its private key
was discarded. Negative cases exercise missing companions, unknown versions and
unsupported required features.

`uv run pytest tests/test_pmf.py` additionally checks full Skulk-card extensions,
exact noncanonical card/envelope bytes, independent registry-proof verification,
distinct capture/converter keys, corruption, strict reverse fidelity,
cancellation and recovery after source deletion. A round-tripped original
`.foxkit` must be byte-for-byte identical. Existing v1 tests remain unchanged.
The common-reader test blocks Skulk, Hugging Face, tensor libraries and all
ecosystem/card adapters from importing. Installed-wheel CI also exercises the
standalone vector outside this checkout. This is format/library evidence;
automatic PMF handling in a released Skulk UI remains separate work.

### Self-contained kits and application adapters

The existing `kit-export`, `kit-inspect` and `kit-import` commands detect PMF
automatically. PMF uses kit v2; legacy archives retain kit v1 without changed
identities or bytes. A PMF kit carries all opaque documents as well as every
model/companion object and the public candidate trust snapshot. No Skulk runtime
is required. Use `pmf-verify` and `pmf-restore` on the imported portable store.
Kits are unencrypted, never include a private key, and do not start sharing.

The optional preservation adapter exposes metadata-only
`build_preservation_pmf` and `inspect_preservation_pmf` helpers. The latter
requires an already authenticated PMF and independently chosen original capture
trust. It binds the complete common projection and every retained record to the
exact original signed envelope. Runtime admission, registry verification and
code permission remain responsibilities of the receiving application.

### Library and Guided

The [Library source interface](../capability/README.md#pmf-source-preview-library-07)
and its [compatible Core preview](../capability/preview.md#pmf-capable-source-cohort)
use PMF automatically for new Preserve jobs. Check, recovery-kit export and
Restore recognize both PMF and legacy captures. No format selection or manual
converter step is needed for the supported preservation/Skulk profile.

Core verifies the original capture signatures with enrolled owner trust, then
independently checks registry evidence, required companions, runtime support and
repository-code consent. PMF conversion signatures grant none of those rights.
A separately signed converter requires independently chosen original trust via
the standalone reverse converter before legacy Core admission. Generic PMF and
unknown optional records remain portable without Skulk, even when this Core
cannot interpret them for execution.

Public publication remains a separate reviewed/sanitized v1 output for existing
mirrors, invitations and opt-in torrents. Direct public PMF transport requires
an explicit adapter. PMF is still an experimental draft; no stable software
release or external ecosystem conformance is implied by Library adoption.
