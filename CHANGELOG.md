# Changes

## Unreleased

- Add bounded private per-attempt client/origin observations to the explicit
  synthetic public I2P drill. Controlled faults distinguish header/body timeouts,
  short bodies and invalid resume ranges without URL or exception-text logging.
  Ordinary transfers and immutable 0.15.1 artifacts remain unchanged.

## 0.15.1

- Bound mirror network writes separately from disk reads so continuously
  progressing slow HTTP receivers can complete and resume byte ranges without
  draining a 1 MiB write inside a single socket timeout. Request/concurrency
  limits and full-response upload reservations remain unchanged.
- Recheck persistent mirror serving authority during slow writes. Revocation
  interrupts transfer, a stalled receiver still times out, and interruptions
  never refund a conservatively reserved response.

## 0.15.0

- Add explicit receive-only I2P HTTP catalog refresh and trusted legacy archive
  downloads through a selected numeric loopback proxy. Hash-addressed I2P routes
  only; no DNS, redirects, address helpers, ambient proxies or direct fallback.
- Preserve enrolled TUF authority, root/version floors, last-good offline search,
  independent model trust, exact envelopes, companion completeness, byte budgets
  and range recovery. Ordinary transfer receipt identities remain unchanged.
- I2P downloads can resume progressing interruptions within an explicit bounded
  attempt count; two stalled attempts stop a supplier. Cancellation, original
  signatures, final-object hashes and cumulative byte/deadline bounds remain.
- Direct transports refuse I2P/onion names before DNS. This is a pilot boundary,
  not qualified public-network anonymity. The separately operated router can
  relay other traffic; see [I2P HTTP](docs/i2p-http.md).

## 0.14.0

- Add an optional standalone I2PSnark recovery-kit pilot: offline I2P-only offers,
  independently authenticated receiving and finite opt-in hosting, with original
  legacy/PMF evidence preserved. Receive requires separate payload-sharing
  consent. Native software is explicitly built/selected, controller loss fences
  the owned client and local inputs are retained. Public-network privacy and
  Library integration are unqualified; see [pilot limits](docs/i2p.md).

- Explicit PirateFace exact-repository lookup records bounded untrusted routing
  candidates without rescue requests, hooks, account enrollment or peer sessions.
- Offline original-repository torrent intake authenticates the original legacy
  or PMF capture under independent trust and verifies every file, companion and
  source document before publishing a new complete archive. Signatures and card
  data stay unchanged; nothing uploads, executes, deletes inputs or grants trust.
  See [PirateFace interoperability](docs/pirateface.md) for workflow and limits.

## 0.13.0

- Explicit persistent IPFS/BitTorrent mirrors use isolated per-model native
  custody, bounded rotating sessions and durable upload reservations. Public
  transport configuration invalidates restart consent; receive-only defaults,
  independently supplied model/terms policy and original signed evidence remain.
- Block/pause/expiry and abrupt controller loss stop native serving while keeping
  caches/pins. Journaled exact operator prune includes selected native custody.
  Offline exports provide standard torrents and original-evidence peer handoffs.
- A combined optional `requirements-mirror.lock` covers peer/BitTorrent Python
  dependencies without Skulk; native Kubo software remains separately selected.
  See [persistent peer mirrors](docs/persistent-peers.md) for bounds and exposure.

## 0.12.0

- Release installation archives include exact runtime versions/hashes, the wheel,
  source, source-revision record and an installer that starts no services. The
  workflow attests the archive and retained verification bundle; offline install
  uses a retained wheelhouse without indexes/cache fallback. Skulk remains optional.

- Persistent standalone HTTP mirrors follow authenticated sources under separate
  model keys and exact reviewed terms. Explicit manual serving and a distinct
  expiring restart grant preserve operator control; durable UTC budgets survive
  crashes and refusal/withdrawal/expiry never delete retained models or pins.
- Offline mirror plan/status, pause/block/pin/retry, completed-intent recovery and
  explicit stopped-controller prune preserve shared objects and interrupted
  deletion intent. No Skulk dependency, automatic eviction or native seeding.
  See [persistent mirrors](docs/persistent-mirrors.md) and the systemd recipe.

- Portable `.foxhandoff` wrappers retain original signed model/source references
  and bounded numeric peer contacts. `handoff-create`/`handoff-inspect` work
  offline; `handoff-fetch`/`handoff-follow`/`handoff-refresh` keep independent
  model/source trust and explicit network permissions. Skulk remains optional.
- `peer-share` verifies one complete public closure and publishes its handoff in
  a finite, separately opted-in native session. Stop, expiry and caller loss
  stop the daemon and retain the operator's archive; no automatic deletion,
  eviction, restart or persistent service is introduced.

- Releases are built, checked and attested by `.github/workflows/release.yml`
  from the pushed `v*` or `library-v*` tag. GitHub build-provenance attestations
  cover the wheel, sdist and `SHA256SUMS`; see [verifying a release](docs/releases.md).
  PyPI Trusted Publishing is prepared but not enabled.
- Peer receiving fails over to the next IPFS content root after a root serves
  bytes that fail verification; previously every later root failed at once.
  `OperationControl.fork()` gives each nested attempt its own control while
  sharing the parent's stop request and deadline.
- `fetch`, `fetch-catalog`, `plan-fetch` and `source-fetch` accept
  `--deadline-seconds` (default 3,600, at most 86,400). Rerunning resumes.
- Pack and fetch keep an existing envelope's signatures for the identical
  payload, so re-signing or a lower-threshold fetch cannot strip another signer.
- Newly initialized peer profiles run no circuit-relay or AutoNAT service and
  do not announce private/loopback addresses unless private peers are allowed.
  Existing profiles keep their recorded configuration.
- A provably stale Kubo API marker (dead loopback port, held writer lock) is
  cleared instead of blocking `peer-run` and `peer-bootstrap-set`.
- `kit-export`/`kit-import` stop cooperatively on Ctrl-C, and no command prints
  a traceback when interrupted.

## 0.10.0

- Add `peer-runtime-plan` and `peer-runtime-install`: pinned, verified Kubo
  0.43.1 acquisition for macOS/Linux ARM64/AMD64 into a private directory, with
  packaged archive/executable identities and no PATH or system changes.

## 0.9.1

- Add portable `.foxpeer-model` invitations, offline peer model review and
  durable, resumable peer receiving into the selected store volume.

## 0.9.0

- Add optional IPFS/IPNS peer discovery (`peer` extra, separately installed Kubo):
  `.foxpeer` source files, dedicated receive-only or mirror node profiles and
  the same TUF and model trust as direct sources. No default bootstrap list.

## 0.8.0

- Add followed-source discovery: stock-TUF-authenticated listings over the
  signed catalog, an owner-only offline cache, offline search, explicit refresh
  with rollback floors, and the `source-*` commands. Following grants listing
  authority only; model downloads still need independent bundle trust.

## 0.7.1

- Support PMF recovery kits (kit v2) and authenticated archive dispatch between
  legacy v1 and PMF manifests. Legacy captures still export byte-identical v1 kits.

## 0.7.0

- Add the draft Portable Model Format 0.1 schema, local `pmf-*` pack/verify/restore
  commands and a lossless round trip for existing preservation-v1/Skulk archives.

## 0.6.0

- Add portable public invitations with the original signed manifest, offline
  candidate-signature inspection and explicitly trusted bounded mirror receiving.
  Embedded keys never enroll themselves or grant registry authority. New CLI
  commands: `invite`, `inspect-invitation` and `fetch-invitation`.
- Add pure metadata-only publication review and optional bounded, explicitly
  supplied publisher notices. Original required notices remain intact; supplied
  text does not become original signed upstream evidence. Full publication still
  checks all source/output bytes and the exact reviewed identity.
- Preserve existing bundle identities, opaque future card metadata and
  receive-only defaults. No new account, hosted resolver or network transport.

## 0.5.3

- Bind HTTP mirrors without the standard server's incidental reverse-DNS lookup.
  Numeric loopback and public listeners no longer depend on reverse name service
  during startup. Selected signed files, budgets and protocol paths are unchanged.

## 0.5.2

- Expose read-only HTTP mirror response-budget reservations for host-owned
  progress and stop controls. Charges remain conservative after interrupted
  responses; this does not change wire formats, receiving or public consent.

## 0.5.1

- Retain additive model-card properties and new string vocabulary names without
  changing existing card identities. Keep known structural validation and closed,
  versioned artifact/manifest contracts; runtime admission remains separate.

## 0.4.0

- Add reviewed public-only publication under a separate signing identity, retaining
  required model data/original notices and excluding private installed evidence.
- Add optional libtorrent metainfo/web-seed export and explicit seed-only sessions,
  with native rate/concurrency limits, monitored lifetime and an observed payload
  stop threshold. Default receiving/verification still import no native binding.
- Add standalone plan/publish/export/seed commands and real local peer tests.
  No anonymity, durable daily upload quota or default torrent receiving is claimed.
- Mark the public Python API as typed (PEP 561) so strict runtime consumers can
  validate the shipped annotations without copied or incomplete type stubs.

## 0.3.0

- Publish the open model-card envelope/body profile and content-identity reader
  without a Skulk runtime dependency. Any publisher can create and retain cards.
- Add bounded offline TUF proofs for exact card publication, independent root
  enrollment, consecutive threshold-verified rotations and receiver rollback floors.
- Keep fresh and explicitly historical verification separate. Historical recovery
  does not discover later revocations or turn expired metadata into a fresh update.
- Add typed retained cards and per-member publication/file-evidence reports.
  Missing signed SHA-256 evidence stays visible; owner hashes never acquire
  registry authority. Contradictory evidence fails without silent downgrade.
- Add standalone card/proof commands and byte-verifying evidence inspection.
  Runtime installation, repository-code permission and qualification remain
  separate; this release does not yet install models into Skulk.

## 0.2.0

- Add read-only pack, fetch and restore plans with separate logical, unique,
  verified reuse and unverified partial-file accounting.
- Add bounded progress, cooperative cancellation and structured failure codes to
  worker entry points; expose planning/progress and Ctrl-C handling in the CLI.
- Add explicit operation IDs and durable object-byte reservations across process
  restarts, bound to exact request/trust/policy and pinned destination identity.
- Reject dot-only paths and reuse verified objects before copy-capacity checks.
- Preserve v1 bundle identities, independent owner trust, receive-only operation
  and publish-last verification. No registry proof or runtime integration added.

## 0.1.0

Initial standalone signed bundle, verification, receive/restore, catalog and
opt-in mirror core. Synthetic conformance and controlled model recovery;
independent custody, runtime integration and privacy qualification remain separate.
