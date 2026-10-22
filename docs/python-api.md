# Use the standalone Python library

The CLI and library use the same implementation. No Skulk import, GPU library,
hub account or service is required. This reference describes the 0.6.0 entry
points; pin the package revision/version in an integration and test its actual
contracts. It is not a promise that every helper is a stable extension interface.

## Main entry points

| Module / function | Inputs | Result and effects |
| --- | --- | --- |
| `signing.load_trust` | Bounded public trust JSON bytes | Strict trust profile, including public-key fingerprint checks. Does not enroll it for the caller. |
| `signing.load_key` | Owner PEM path, passphrase bytes or explicit `None` | Ed25519 private key. Rejects group/world-accessible key files. |
| `storage.pack` | `Recipe`, member-ID-to-folder mapping, destination, tuple of owner keys | Manifest ID. Copies selected content, checks it, signs and publishes the envelope last. |
| `storage.verify_store` | Store path, expected manifest ID, independently trusted profile | Validated `Manifest`, after checking every required actual object. |
| `transfer.fetch` | Expected manifest ID, trust, source tuple, destination, `TransferPolicy` | Validated `Manifest`. Resumes bounded transfers and publishes completion after verification. |
| `publication.review_publication` | Already authenticated source `Manifest`, `PublicationRequest` | Exact proposed public metadata, without IO or weight hashing. Optional supplied notices are distinct owner evidence. Actual publication still verifies all bytes. |
| `invitations.create_invitation` | Public store, exact ID, trust, HTTP(S) mirror tuple | Metadata-only `Invitation`; starts no network and asserts no current availability. |
| `invitations.read_invitation` | Bounded invitation bytes | Offline candidate-signature inspection, without adopting keys or parsing retained card bodies. |
| `invitations.authenticate_invitation` | Invitation, independently selected trust | Authenticated public `Manifest`; no IO or model admission. |
| `invitations.accept_invitation_keys` | Invitation, explicitly authenticated fingerprint tuple | Exact-copy first-contact trust; never global enrollment or replacement for an existing revocation policy. |
| `invitations.fetch_invitation` | Invitation, selected trust, caller-owned destination, transfer policy/controls | Authenticates before networking, then uses the ordinary fetcher. Never seeds, restores or runs code. |
| `storage.restore` | Store, expected ID, trust, new output path | Validated `Manifest` and a fresh member layout. Existing outputs are refused. |
| `signing.verify_catalog` | Envelope bytes, catalog trust, expected catalog ID | Validated catalog. No network request or trust enrollment. |
| `seed.SeedServer` | Bind address, store, exact selected bundle IDs, trust and serving limits | Explicit read-only server. Construction verifies all selected objects before listening. |
| `seed.SeedServer.reserved_upload_bytes` | No arguments | Thread-safe read of the session's reserved response-body allowance. Interrupted responses retain their full charge; this is not measured peer delivery and excludes HTTP/TLS overhead. Reading starts no server or transfer. |

Models are strict Pydantic records. For direct model validation, first read input
with a size bound, then apply `signing.strict_json` and `signing.canonical_bytes`
before `model_validate_json`. Direct Pydantic model parsing alone does not apply
the byte limit or duplicate-key guard. Tuples are required where the in-memory
API declares tuples. Use the published
[schemas](schemas) and executable conformance tests together: custom graph/path
invariants are not fully expressible in a JSON schema alone.

## Receiving from already enrolled references

```python
from pathlib import Path

from foxlight_model_preservation.models import MAX_METADATA_BYTES
from foxlight_model_preservation.signing import load_trust
from foxlight_model_preservation.storage import restore, verify_store
from foxlight_model_preservation.transfer import TransferPolicy, fetch

# This file and ID must have been independently authenticated by the caller.
with Path("bundle-trust.json").open("rb") as source:
    trust_bytes = source.read(MAX_METADATA_BYTES + 1)
trust = load_trust(trust_bytes)  # Rejects an oversized input before parsing.
expected_id = "replace-with-the-exact-64-character-manifest-id"
store = Path("preserved")

manifest = fetch(
    expected_id,
    trust,
    ("https://mirror-a.example/models", "https://mirror-b.example/models"),
    store,
    TransferPolicy(max_bytes=20 * 1024**3),
)
verify_store(store, expected_id, trust)
restore(store, expected_id, trust, Path("new-restored-layout"))
print(manifest.name, manifest.total_bytes)
```

Validate/bound configuration files before reading them in an application; the
CLI's `read_input` caps input files at 8 MiB. `strict_json` and signing entry
points that call it (such as `load_trust`) also reject bytes beyond that limit.
Direct `Recipe.model_validate_json` / `Catalog.model_validate_json` calls do not
enforce it or reject duplicate object keys. No parser can undo an oversized
allocation the caller already made before passing bytes in. Never accept a remote request's arbitrary local
path as authority to read a host file.

`TransferPolicy` defaults: 20 GiB object cap, 30-second socket timeout,
3,600-second overall fetch deadline checked between reads/attempts, at most 16
sources, HTTPS only and non-global addresses blocked. Progress does not reset
that deadline. A filesystem source is an explicit selected folder. Deliberate
LAN/HTTP policies are separate flags; `allow_private` does not disable TLS checks.
DNS resolution and local filesystem stalls are not interruptible by the deadline.
The declared unique-object total must fit the cap even if some objects are cached;
actual object bytes across attempts are also bounded. Metadata and protocol
overhead have separate limits or are excluded as described in the protocol.

## Calling from an application

[Public invitations](invitations.md) describe the offline handoff and trust
choices. `review_publication`, invitation inspection and authentication are
metadata operations; `plan_publication` still reads and hashes the full source.
Never run the latter in a short control-plane request or treat the former as
proof of current disk bytes or redistribution rights.

These entry points are synchronous and perform potentially long disk/network
work. Run them in a bounded worker or isolated process, not in an event loop,
HTTP handler's execution path or short RPC invocation. The host owns its
background service and automatic resumption. Version 0.2.0 adds
read-only planning, structured progress, cooperative cancellation and optional
persistent transfer allowances; see [worker controls](jobs.md). A successful return is the
completion receipt; a caller cannot infer successful completion from a process
starting or from a partial object appearing.

Failures can be `PreservationError` / `ValueError` (including Pydantic validation)
or operating-system errors. The CLI turns supported failures into a JSON error
and nonzero exit. Keep full sensitive paths out of remotely visible logs. If a
worker is terminated, published generations remain separately checkable; fetch
can reuse valid objects and retained partials when explicitly called again.
This is not automatic job resumption.

## Integrating an inference runtime

Version 0.3.0 adds public cards, bounded offline registry proofs and separate
per-member publication/file-evidence reports. See [cards and provenance](cards-and-provenance.md)
for the typed retained profile, receiver trust, historical verification and
the distinction between metadata checks and actual byte verification.

A verified preservation manifest establishes an owner capture and exact bytes.
An application must still validate its original model-card/provenance evidence,
match role and loader mappings, preserve remote-code approval, and install through
its normal staged generation/store contract. Never manufacture registry trust
from an owner signature or a boolean supplied by another process.

Retain the original evidence as signed capture content where applicable. Keep
runtime installation, execution and engine provisioning separate from portable
restoration. Restoring into a new folder does not register that folder with a
runtime. The separate [Skulk Library](../capability/README.md) uses Core's ordinary
admission and durable jobs. The standalone [PMF draft and archive bridge](portable-model-format.md)
can preserve and round-trip its public cards and archives without importing
Skulk. This draft does not add automatic PMF admission to Core or Library.

For protocol-compatible third-party mirrors, use the documented immutable paths.
There is no registry of required approved mirror hosts: an explicitly permitted
supplier must return the same independently trusted identity and bytes. Transport
and discovery policy belongs to the caller; this core never silently switches
from a private transport to public peering.

HTTP mirror construction binds numeric addresses without a reverse-DNS lookup.
`server_name` records the numeric bound address; it is not an advertised public
name or a reachability claim. An explicitly configured hostname may still require
resolution to bind. This does not provide a protected transport or anonymity.
## Standalone source discovery API

`foxlight_model_preservation.discovery` supplies `inspect_source`, `follow_source`,
`list_sources`, `refresh_source`, `search_sources`, `select_entry`,
`fetch_selection`, `export_source` and `remove_source`. These import no Skulk,
Core, capability SDK or GPU runtime. Only `refresh_source` reads metadata
endpoints; `fetch_selection` is the separately authorized model download.

```python
from pathlib import Path
from foxlight_model_preservation.discovery import (
    follow_source,
    refresh_source,
    search_sources,
    fetch_selection,
)
from foxlight_model_preservation.signing import load_trust

cache = Path("private-discovery-cache")
source = follow_source(
    cache,
    Path("community-source.json").read_bytes(),
    independently_authenticated_bootstrap_id,
)
refresh_source(cache, source.source_id)
results = search_sources(cache, "offline writing")
# Explicitly review a result, then provide independently selected model keys.
fetch_selection(
    cache,
    source.source_id,
    selected_entry_id,
    selected_exact_manifest,
    load_trust(Path("model-trust.json").read_bytes()),
    Path("received"),
)
```

Placeholders in this snippet are application inputs, not inferred trust.
Following grants metadata authority only. Optional source-supplied model keys
remain candidates; `fetch_selection` always needs its explicit `bundle_trust`.
Private network, plaintext HTTP and local files need independent receive policy.
Metadata source grants do not carry over to model downloads. The existing
model verifier/restore and original card/provenance contracts remain authoritative.

`refresh_source` accepts a caller-owned cancellation/progress fence. A stock
`OperationControl` is suitable for one source; a batch must not let one source's
commit consume the remaining batch's cancellation fence. Updates retain stock
TUF verification state and the last accepted catalog transactionally. Offline
export/import checks historical signed evidence and never labels an import as a
successful fresh update. See [discovery](discovery.md) for limits and state meanings.

`foxlight_model_preservation.discovery_publish.publish_catalog` takes public root
metadata, a verified signed catalog, its `DiscoveryCatalogTarget` profile and
explicit `DiscoverySigningKeys`. It writes a static TUF tree with timestamp last;
it starts no listener and does not need the root private key for updates.
