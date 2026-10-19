# Model preservation core

This standalone public core implements portable signed model preservation.
Keep runtime dependencies independent of Skulk, Hugging Face, a GPU, and hosted
accounts. Internal strategy, plans and operations belong in private foxlight-docs.

- Read docs/protocol.md before changing the format or trust semantics.
- Never commit private keys, credentials, real operator inventories or weights.
- Signing a capture does not establish upstream authorship or qualification.
- Require independent trust and exact manifest identity before fetching weights.
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
