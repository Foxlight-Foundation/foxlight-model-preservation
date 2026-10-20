# Changes

## Unreleased

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
