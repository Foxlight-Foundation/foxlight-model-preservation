# Changes

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
