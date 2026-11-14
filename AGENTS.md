# Model preservation core

This standalone public core implements portable signed model preservation.
Keep runtime dependencies independent of Skulk, Hugging Face, a GPU, and hosted
accounts. Internal strategy, plans and operations belong in private foxlight-docs.

- Read docs/protocol.md before changing the format or trust semantics.
- Never commit private keys, credentials, real operator inventories or weights.
- Signing a capture does not establish upstream authorship or qualification.
- Require independent trust and exact manifest identity before fetching weights.
  A copy found in community mirror holdings is identified by its exact
  manifest ID; it may be saved when its origin is confirmed against Hugging
  Face, or after the owner explicitly acknowledges that it could not be. The
  acknowledgement is required by Core and recorded in the copy (Tom, 2026-11-13).
  The same applies to a model Hugging Face no longer has, saved from the
  Pirate Face swarm against the hashes Pirate Face recorded (foxlight-docs#714).
- Pirate Face search, swarm downloads and rescue requests are separate explicit
  choices; a swarm receive never uploads and nothing seeds after the save.
- Keep consumers receive-only. Serving and public intake are explicit choices.
- Publish complete generations only after all required bytes pass verification.
- Public functions/classes require useful docstrings and precise types.
- Validate with `uv run ruff check`, `uv run ruff format --check`,
  `uv run basedpyright`, and `uv run pytest` before committing.
- Use branches and ready PRs. Apply the Foxlight severity rubric to findings;
  fix severity 4–5, explain and resolve lower-severity deferrals.
- Merge only when authorized by the user or an applicable standing policy.
- A task that uses a linked worktree owns removal after merge and verification;
  never force-remove dirty, locked or active work.

Cross-project strategy and the initiative live in the private
Foxlight-Foundation/foxlight-docs repository. Public docs describe this core's
implemented format, behavior, limits and reproducible verification.
