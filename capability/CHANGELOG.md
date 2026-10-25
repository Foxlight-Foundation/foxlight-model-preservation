# Library changes

Skulk Library is versioned separately from the portable core. Releases are
tagged `library-v*`; versions without a tag are source previews installed from
`main` (see [README](README.md)).

## 0.14.0

- Recover retained mirror copies from Library, including first launch and loss of
  the original import or copy receipt. Review complete signed member metadata.
- Prepare a separate recovery kit as a durable host job, reopen its outcome,
  and stream the exact sealed file with HEAD/range/If-Range support.
- Keep preparation separate from sharing. Retry the same explicit preparation
  after a lost response without creating another kit; preparing another kit
  requires another operator decision.
- Confirm removal of one preparation's kit files separately from retained-model
  removal. No automatic cleanup, eviction or new trust grant is introduced.
- Look up the exact retained identity before reintake, including removed records
  beyond the displayed page. Negotiate the new recovery contract separately;
  older hosts retain existing sharing and Stop controls.
- Pin integrated Core `246fe15cc8702ba7037bb967db6cf80675bc1ea5`. The GUI still
  needs compatible Core; standalone preservation does not require Skulk.

## 0.13.0

- Manage separate retained public copies from Sharing: explicit resource settings,
  HTTP/IPFS/BitTorrent choices, complete member-term review and durable jobs.
- Keep Start, Stop and expiring restart permission separate. Observe the actual
  controller after reopening Library; Stop remains available through stale or
  failed receipts and while another intake cancellation is pending.
- Add pin, block, retry and exact confirmed removal controls. Keep unavailable
  storage distinct from an empty mirror; no automatic eviction or trust grants.
- Check versioned Core authority/lifecycle schemas and complete review separately,
  preserving existing Stop when a host lacks the new intake review endpoint.
- Pin the accepted Core source for public-copy review and persistent ownership.
  Preserve original cards, companions and signed provenance; direct public PMF
  mirror transport and retained-copy recipient export remain separate work.

## 0.12.0

- Follow authenticated sources through an explicitly selected I2P HTTP proxy;
  saved source refreshes and model details bind the exact retained local policy.
- Receive through I2P with separate model-key/exact-copy and network consent.
  Resume shows the original proxy and keeps the same job, partials and allowance.
- Check actual Core schemas before offering I2P controls; refuse unsupported
  I2P-only references and mixed grants without direct fallback or payload sharing.
- Pin the compatible Core with retained receiving-policy previews. Keep the
  public Library launcher SDK-optional and standalone preservation Skulk-free.
- Document the functional pilot and its unqualified public privacy/performance
  boundaries. No router setup or application-wide anonymity mode is implied.

## 0.11.0

- Include a hash-locked public installation archive and exact source evidence.
  Local release installs can reuse the authenticated cohort for separate Core
  preview setup after checking retained wheel and actual installed bytes.

- Share an exact public copy through a finite Core-owned peer session and send
  one `.foxhandoff` file. Download remains available after reload or Stop.
- Import model/source files with reviewable contact suggestions, preserving
  original signatures and separate model, source, code and network decisions.
- Keep explicit cache cleanup, retained archives and no restart replay; preserve
  HTTP invitation compatibility with older Core. Standalone tools need no Skulk.

## 0.10.0 (source preview)

- Guide verified peer software setup: Library can install the pinned Kubo runtime
  through Core, and offers explicit repair after the runtime is lost.

## 0.9.0 (source preview)

- Connect Library and Guided to reviewed peer discovery and receiving: `.foxpeer`
  sources, starting contacts, `.foxpeer-model` invitations and resumable receives.

## 0.8.0 (source preview)

- Add source following, Sources & updates, offline search and reviewed model
  downloads from followed sources.

## 0.7.0 (source preview)

- Preserve in Portable Model Format automatically; Check, recovery kits and
  Restore detect PMF and legacy copies without a format selector.

## 0.6.0

- Add reviewed local/mounted-folder backup storage, a durable default destination,
  per-copy availability, the pinned isolated Core preview installer and `--check`.

## 0.5.0

- Add Publish, Share with someone, Receive shared model and download-resume review.

## 0.4.1

- Fix navigation out of public import; add Activity review and explicit cleanup
  of stopped sharing/import work. Saved copies, keys, kits and metainfo remain.

## 0.4.0

- Add public-only signed bundle import, bounded HTTP mirrors and opt-in BitTorrent
  sharing through Core-owned jobs, with metainfo and public-key downloads.

## 0.3.0

- Make the local launcher publicly installable without the managed SDK, and add
  explicit cleanup of unused stopped-restore files.

## 0.2.0

- Add recovery kit export/download and complete restore, including on a fresh Core.

## 0.1.0

- Initial Library capability preview: Guided setup, Library and Activity over
  Core's durable preservation jobs.
