# Portable model preservation v1

This is the implemented v1 contract. The Python models in
`src/foxlight_model_preservation/models.py` and the published JSON schemas define
accepted fields. Unknown fields and versions fail closed. A future incompatible
format requires a new schema version and signing domain.

A preservation set describes **one declared workload closure**. Every member has
an exact origin repository and 40-hex revision, an artifact identity, a role,
format, optional loader root, retained original card, intake declaration, and
explicit files. Dependencies reference other member IDs. The graph must be
acyclic and every member reachable from the root. Completeness is relative to
this selection; pack cannot infer an omitted projector, tokenizer or engine.

A file identity is its canonical relative path, integer byte length and SHA-256.
Member directories prevent collisions between repositories. Paths reject
traversal, absolute paths, symlinks, non-portable aliases, case collisions and
file/directory overlap. Files are regular files only. Metadata is bounded to
8 MiB, members to 256, total files to 100,000, paths to 64 components, and byte
counts to the RFC 8785 safe integer range. An envelope's base64 overhead counts
toward the metadata limit.

## Identity and signatures

Manifest payloads use [RFC 8785 canonical JSON](https://www.rfc-editor.org/rfc/rfc8785).
The manifest ID is the lowercase SHA-256 of those exact canonical payload bytes.
A [DSSE v1 envelope](https://github.com/secure-systems-lab/dsse/blob/master/protocol.md)
contains the base64 payload, `payloadType`, and Ed25519 signatures. Signing uses
the standard DSSE pre-authentication encoding:

```
DSSEv1 <UTF-8 type byte length> <type> <payload byte length> <payload>
```

Bundle type: `application/vnd.foxlight.model-preservation.v1+json`.
Catalog type: `application/vnd.foxlight.model-preservation.catalog.v1+json`.
Each signature's `keyid` is `ed25519:` plus SHA-256 of the raw 32-byte public key.
The key ID is a lookup hint, never a trust grant. A verifier checks the fingerprint
against independently enrolled public keys and counts distinct valid non-revoked
keys toward the explicit threshold. It checks the expected payload digest,
signing domain, canonical encoding and strict semantics. Duplicate JSON keys,
unknown versions and changed files fail verification.

The owner signature authenticates an **owner capture**. Retaining a registry card
or writing an origin revision does not prove upstream authorship, turn legacy
bytes into a registry-verified artifact, or qualify inference. A newly computed
hash is an exact local identity, not new upstream evidence. Intake is an
operator's recorded audience/terms decision, not a license grant.

## Storage and publication

```
manifests/<manifest-sha256>.json    # DSSE envelope
objects/sha256/<file-sha256>        # exact file bytes
```

The manifest identity excludes transport URLs. The same bytes can be hosted by
any ordinary static HTTPS server or filesystem. A signed catalog has unique
entry IDs, exact manifest IDs and replaceable mirror hints; it has a separate
signing domain. The reference client pins the catalog digest explicitly. It does
not treat a pinned historical catalog as fresh update metadata or perform an
implicit latest-version lookup. Use separate owner/catalog trust files for
separate roles. `fetch-catalog` accepts a distinct `--bundle-trust` for model
signers; absent that option, the explicitly supplied trust profile authorizes
both domains. There is no compulsory Foxlight key, online catalog or account.

Pack and fetch write content to private temporary files, check the actual
length/hash, fsync, and replace within the selected store. A nonblocking kernel
writer lock fences concurrent writers. The envelope is published last, after
all required objects pass read verification. Partial objects alone are not a
complete generation. Verify always rehashes actual stored bytes. A previously
verified store can later be corrupted and must be checked again.

Restore verifies the store, copies and verifies each declared file into a fresh
private tree, then publishes that tree into a newly reserved empty destination.
It refuses existing destinations and never adds files to an existing installation.
It emits `members/<id>/<path>` plus `.preservation/envelope.json`. Runtime
requirements are recorded separately; restore does not install engines, import
code, execute models, or register cards with an inference runtime.

POSIX descriptor-relative operations and writer locks currently require Linux
or macOS and a filesystem implementing ordinary rename/fsync/flock semantics.
Mounted NAS support depends on those semantics; there is no automatic mount or
volume-sentinel management. Use an explicit verified mount. Restore uses copies,
not links back into a mutable source.

## Transfer and serving

Fetch authenticates the exact manifest before requesting model bytes. It tries
only supplied sources, resumes hash-named partial objects, validates exact HTTP
ranges/lengths, and verifies complete hashes before publication. Bad prefixes
are discarded and a clean retry is permitted. Both declared bundle size and
actual object bytes across retries have a cap (default 20 GiB). Metadata has its
own per-source 8 MiB cap; source count is bounded. Disk space is checked with a
16 MiB reserve. There is no automatic storage purchase, eviction or repair job.

HTTPS uses the system certificate store and hostname verification. The actual
connection is pinned to a validated DNS result. Non-global addresses and plain
HTTP require separate explicit flags. No redirects, credentials, proxy environment
variables, encoded responses or upstream file-list lookups are used. The client
makes direct connections: it is **not a proxy or anonymity client**. Socket
operations have timeouts and a deadline is checked between reads/attempts. OS DNS
resolution and local filesystem stalls are not interruptible by this deadline.
Budgets count application object bytes, excluding HTTP/TLS overhead and metadata.

`seed` is an explicitly started, bounded reference HTTP(S) server. It verifies
selected bundles before listening, serves only exact selected manifest/object
paths, supports one byte range, limits worker count and reserves an upload budget
before each response. Interrupted responses consume their reserved budget. It has
no upload, discovery, directory-listing, administration or proxy endpoints. It
keeps no request-history log. It does not reload new bundles automatically.
Public binding additionally requires every member's audience to be `public`.
An ordinary read-only static HTTPS server is the preferred durable hosting path.

## Optional local worker controls (0.2.0)

Read-only plans, bounded progress/cancellation and per-operation transfer receipts
are library/client controls; they do not change v1 signed bundle payloads, IDs or
public transport paths. `.operations/` is trusted local accounting and is never
served by the reference seed. A named transfer reserves object-byte credit before
reading and retains conservative charges across restarts. Without an operation
ID, the existing per-invocation actual-byte policy applies. All required file
hashes still verify before final publication. See [worker controls](jobs.md) for
accounting exclusions, cancellation/commit semantics and host responsibilities.

## Optional retained public cards (0.3.0)

The existing `retained_card` object can carry the typed
`foxlight.retained-card.v1` profile: an existing public model-card envelope and
an optional `foxlight.registry-proof.v1` archive. This does not change the bundle
v1 signing domain, identity, paths or owner-capture attestation. The receiver
must independently verify registry evidence; preserving arbitrary metadata or
serializing a verification label never grants registry authority. See
[cards and provenance](cards-and-provenance.md) for exact encoding, trust and
historical-recovery semantics.
