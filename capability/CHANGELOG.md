# Library changes

Skulk Library is versioned separately from the portable core. Releases are
tagged `library-v*`; versions without a tag are source previews installed from
`main` (see [README](README.md)).

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
