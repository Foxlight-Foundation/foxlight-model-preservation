# Sovereign managed capability: SDK1 candidate

Sovereign's optional managed Library controls Skulk Core's preservation service.
The capability owns its interface and translates reviewed requests; Core retains
storage, keys, durable jobs and byte workers. Closing or restarting Library does
not cancel a Core job. Preserving never starts model inference or sharing.

This SDK1 consumer candidate uses the public SDK **0.5.16rc1**, public author
tools **0.2.0rc6**, owner **0.18.0rc8**, and native kernel **0.7.0rc7**. These are
immutable candidate inputs, not the final stable SDK1 release. The managed
runtime targets CPython3.13 on macOS ARM64 and glibc Linux x86_64/ARM64
(glibc2.34 or newer), with Skulk `>=2.0.3,<3`. Windows standalone desktop support
does not imply Windows managed capability hosting.

The standalone core remains0.38.1 and Library remains0.34.1. Its base and desktop
wheel dependencies do not include the SDK, owner, kernel or publisher. SDK and
publisher acquisition are opt-in groups using exact public asset URLs and
hash-locked dependencies. The separate `managed/uv.lock` is only the managed
runtime recipe. No private repository credential is required.

## Contributor checks

From `capability/`, run:

```sh
uv sync --frozen --all-extras --group managed --group author
uv run --no-sync ruff check
uv run --no-sync ruff format --check
uv run --no-sync basedpyright
uv run --no-sync pytest
```

The `desktop` extra allows checking the standalone adapter as well. To work only
on the optional managed adapter, omit `--all-extras`; desktop-only tests then
report their explicit skips.

The original SDK0.5.0/Pydantic2.14.1 manifest and settings are retained in
`tests/fixtures/sdk050-managed-contracts.json`. Both descriptors, permissions,
Steward declarations, bounds and configuration remain unchanged. The new
`runtime_dependencies` field explicitly declares HTTPX, Pydantic and PyYAML;
these are packaging roots, not new capabilities or permissions. The public
archive test imports the actual packed child in an isolated interpreter and
checks the bundled licence definition. The managed spec lists all required
non-code resources, including that definition and the unchanged interface.

## Candidate packaging and installed qualification

The **Sovereign managed SDK1 candidate** workflow:

1. Runs native managed and standalone-adapter source checks on all three
   supported managed platforms.
2. Installs a separate hash-locked public author environment without the
   application, Core, owner or kernel. Builds each complete platform runtime
   and an independent macOS reference. The build job imports only the selected
   application source; a separate frozen Core verifies the result.
3. Verifies the complete frozen dependency closure against the independent
   reference and signs a candidate feed using a disposable qualification key.
   Its output is explicitly **unsigned for production distribution**. It has
   no production signing secret or Store-upload step.
4. Installs the actual signed candidate through the pinned Core's normal
   installer, selection and supervisor, with real compiled Core bindings.
   The installed child plans and submits a real preservation job over the
   normal protected owner channel and loopback operator API. Tiny synthetic
   local model and companion fixtures exercise the actual Core byte worker;
   no model, mirror or account is downloaded or connected.
5. Reopens the same completed job after owner restart and Core journal reopen,
   verifying its identity, result, configuration and host identity. The
   selector is disabled and all owned services close afterward. Only a bounded
   receipt is uploaded, without service roots, keys or bearer URLs.

The Core source is pinned in `skulk-baseline.json` to the accepted coordinated
consumer baseline. Final Windows/engine reconciliation, stable SDK1 versions,
matched host packages and release qualification remain separate gates.

This is the first managed runtime qualification for this wrapper. It does not
claim an upgrade from an issued managed Store package or replace an independent
Sovereign desktop release. Production signing, Store publication and the final
coordinated release remain ahead.
