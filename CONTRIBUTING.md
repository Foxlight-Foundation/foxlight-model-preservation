# Contributing

Contributions are made under the [Apache License 2.0](LICENSE) and the
[Developer Certificate of Origin 1.1](https://developercertificate.org/).
Sign off every commit (`git commit -s`), adding a line such as:

```text
Signed-off-by: Your Name <you@example.com>
```

The sign-off certifies that you wrote the change or otherwise have the right to
submit it under the project's license. There is no contributor license
agreement: you keep your copyright. Project governance and trademark use are
described in [GOVERNANCE.md](GOVERNANCE.md) and [TRADEMARKS.md](TRADEMARKS.md).

## Development

Use Python 3.11+ on Linux or macOS. Install with `uv sync --dev`, then run:

```sh
uv run ruff check
uv run ruff format --check
uv run basedpyright
uv run pytest
uv build
```

Transport adapters must preserve exact signed identity, independent trust,
receive-only defaults, endpoint policy and cumulative resource bounds. Keep
locations outside manifests. Add adversarial and interruption tests for behavioral
changes. Keep the verifier independent of inference runtimes and hosted accounts.

Third-party implementations can consume the protocol, JSON schemas and public
conformance vector. Run `tests/test_conformance.py` against the retained vector;
its signing key is intentionally not retained. Synthetic negative fixtures are
created by tests. New schemas/domains need an explicit compatibility decision.

Do not commit weights, credentials, private signing keys, operator inventories or
private planning. Use exact-revision recipes for redistributable sample models;
retain all required notices and distinguish an owner capture from upstream or
registry verification. Review is through ready pull requests. Security concerns
follow [SECURITY.md](SECURITY.md).
