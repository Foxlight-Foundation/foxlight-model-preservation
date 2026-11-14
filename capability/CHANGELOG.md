# Library changes

Skulk Library is versioned separately from the portable core. Releases are
tagged `library-v*`; versions without a tag are source previews installed from
`main` (see [README](README.md)).

## Unreleased

- Library → **Where model copies are kept**: choose any connected drive, NAS or
  folder (native folder picker on Mac and Linux), see free space there, move
  existing copies with read-back verification, and get a clear message when the
  drive is unplugged.

- Guided Linux setup 0.1.8 and graphical setup 0.1.10 install revision 10
  (core 0.33 / Library 0.29), now served by the signed apt channel; package pins
  amd64 15e7ccf3… and arm64 949da3ea…, documented bootstrap hash fa79ef17….

- Pin Sovereign Mac build 15 and Linux revision 10 to the authenticated
  Library 0.29 installation archive (sha256 ae9467bf…, source c300dd6e; its
  desktop lock carries core 0.33.0 and libtorrent 2.0.14), which adds mirror
  holdings in Find. Native signing, notarization, apt publication and
  qualification remain separate gates.

## 0.29.0

- Find shows models kept by community mirrors, found from their signed lists
  and searched on this device, with "Kept by N mirrors" (also on matching
  Hugging Face results). Keeping one checks its origin against Hugging Face
  and says what was confirmed. Discovery starts only after Browse public
  models or Hugging Face search was chosen. Requires core 0.33.0.

- Install docs point at the public 0.32 cohort: core 0.32.0, Library 0.28.0,
  Mac build 14, Linux revision 9 (guided setup 0.1.7, graphical setup 0.1.9)
  and `sovereign-mirror:0.32.0`.

- Guided Linux setup 0.1.7 and graphical setup 0.1.9 install revision 9
  (core 0.32 / Library 0.28), now served by the signed apt channel; package pins
  amd64 662d5494… and arm64 ce7ad19d…, documented bootstrap hash 74c6c907….

- Pin Sovereign Mac build 14 and Linux revision 9 to the authenticated
  Library 0.28 installation archive (sha256 8ee926d2…, source d8055f4f; its
  desktop lock carries core 0.32.0 and libtorrent 2.0.14), which adds Hugging
  Face search in Find, savable listings without their own file list and an
  optional Hugging Face token. Native signing, notarization, apt publication
  and qualification remain separate gates.

## 0.28.0

- Add or remove a Hugging Face token under Source and privacy, for faster
  downloads and gated models you've been given access to. Requires core 0.32.

- Find searches Hugging Face as well as saved listings (one-time choice), with
  a file review that pins the exact version, offers GGUF quant choices and
  hands the plan to the usual "Keep a copy" review. Listings without their own
  file list are now savable. Requires core 0.32.

- Install docs point at the public 0.31 cohort: core 0.31.0, Library 0.27.0,
  Mac build 13, Linux revision 8 (guided setup 0.1.6, graphical setup 0.1.8)
  and `sovereign-mirror:0.31.0`.

- Guided Linux setup 0.1.6 and graphical setup 0.1.8 install revision 8
  (core 0.31 / Library 0.27), now served by the signed apt channel; package pins
  amd64 00ea5294… and arm64 1cc98f06…, documented bootstrap hash 949d5c30….

- Pin Sovereign Mac build 13 and Linux revision 8 to the authenticated
  Library 0.27 installation archive (sha256 40ddc68b…, source ec4f166a; its
  desktop lock carries core 0.31.0 and libtorrent 2.0.14), which adds catalog
  saves that fall back to community mirrors, signed mirror object coverage,
  resumable Hugging Face downloads with per-save sources and bucket upload
  retries. Native signing, notarization, apt publication and qualification
  remain separate gates.

## 0.27.0

- Activity says where a finished save's files came from: "From Hugging Face",
  or "From Hugging Face and community mirrors (starter.foxlight.ai): 3 of 10
  files".

- The save review says Sovereign asks community mirrors for the same verified
  file when Hugging Face can't provide it; Activity shows that step, and a save
  no source could supply explains itself plainly.

- Install docs point at the public 0.30 cohort: core 0.30.0, Library 0.26.0,
  Mac build 12, Linux revision 7 (guided setup 0.1.5, graphical setup 0.1.7)
  and `sovereign-mirror:0.30.0`, and name the 0.30 buttons.

- Guided Linux setup 0.1.5 and graphical setup 0.1.7 install revision 7
  (core 0.30 / Library 0.26), now served by the signed apt channel; package pins
  amd64 8bb8da99… and arm64 8045ccf9…, documented bootstrap hash 05979b41….
  The setup's closing message uses the 0.30 button names.

- Pin Sovereign Mac build 12 and Linux revision 7 to the authenticated
  Library 0.26 installation archive (sha256 20f5b71a…, source e09221be; its
  desktop lock carries core 0.30.0 and libtorrent 2.0.14), which adds the
  starter mirror fallback and starter collection, plainer first-time wording
  and registry card licences. Native signing, notarization, apt publication and
  qualification remain separate gates.

## 0.26.0

- Plainer first-time wording. The save review is titled "Keep a copy",
  explains checking and privacy without jargon, and says why saving briefly
  needs extra free space. Choosing Keep a copy is the confirmation: the
  separate "Proceed with this exact private selection" box is gone (kept only
  for an older historical selection). Each file version says which apps can
  open it ("For Macs with Apple silicon…", "For apps such as LM Studio, Ollama
  and Jan…"). Getting started is rewritten in plain words. Smaller fixes: the
  search placeholders fit, file-opening buttons share one name ("Open a
  sharing file", "Open a recovery kit"), the header button stays on one line,
  Activity no longer repeats a job's name, the Library match count is
  grammatical and only shown while searching, and Sharing explains the
  licence choice without "your list" or protocol names.

- Find is clearer for newcomers. Getting listings shows a loader instead of a
  silent wait, and the leftover "Listing update started" notice is gone.
  Browsing without a search lists saveable chat models first. The
  "Preservation support" dropdown and its count line are replaced by one
  plain option: "Also show listings Sovereign can't save yet".

- The save review no longer asks people to "accept the No identified licence".
  A listing without a licence shows "Licence not named", and its checkbox
  reads "I'll read and follow the model's own terms before using it"; the
  internal marker is no longer shown as if it were a licence.

- Publish Sovereign Mac build 11 (core 0.29 / Library 0.25): Developer ID signed
  and Apple notarized (app 7504d452…, DMG 5cfd0510…), DMG f97edfe6… and ZIP
  2dbbc527…. Install docs, download pages and the mirror deploy files now point
  at core 0.29.0, Library 0.25.0, Mac build 11 and the 0.29.0 mirror image.

- Guided setup 0.1.4 and graphical setup 0.1.6 install Linux revision 6
  (core 0.29 / Library 0.25), now served by the signed apt channel; package pins
  amd64 61d9198e… and arm64 31fe8dde…, documented bootstrap hash 5f3c50b5….

- Prepare Mac build 11 and Linux revision 6 from the authenticated core 0.29 /
  Library 0.25 installation archive (sha256 8c7b1030…, source d66ab33f), which
  adds one-switch sharing for Skulk, licence families and opt-in upload caps,
  advisory catalog withdrawal, the Hetzner preset and bucket fixes. Native
  signing, notarization, apt publication and qualification remain separate gates.

## 0.25.0

- Both sharing sheets (Sovereign and Library mode) show licence families with
  a switch each, a per-licence checklist (with summaries, notes and the EU
  notice for Llama 3.2 and Llama 4), a custom licence id field and how many of
  your copies match. Defaults follow Hugging Face: every family is on,
  including Non-commercial and makers' terms that allow redistribution. The
  Library ships the core's licence definition byte for byte instead of keeping
  its own list.

- Download reviews in Sovereign (registry and independent sources) and in
  Library mode show each licence's name, a plain summary and a link to its
  full text, and ask for "I accept these terms" before downloading a model
  under use-restricted terms. In Library mode this needs a Core that reports
  member licences on receive plans (`member_terms`); the acceptance is sent as
  `accepted_terms`. Older Cores are unchanged.

- Upload limits are opt-in: **Limit uploads** is off for a new setup and
  reveals daily and monthly limits when turned on. Existing limits stay. A
  Skulk host whose Core can't store "no limit" keeps the switch on and says
  why (`Uncapped uploads` feature check).

- With a Core that keeps a standing intake policy (`Standing intake` feature
  check), Turn on saves the share mode and licence list on Core as an `intake`
  decision covered by the year's grant, and the card and sheet show the saved
  choice after a reload. Older Cores get no intake decision.

- Cloud storage: Amazon S3 takes an optional public address (for CloudFront),
  Cloudflare R2 an optional jurisdiction, and optional answers are labelled.
  A connected bucket lists the models it serves, with copyable manifest IDs,
  and **Export verification info** saves a sharing file per model and shows
  the signing key to confirm with recipients.

- Sharing in Library mode now opens on one "Help keep models available"
  switch, like Sovereign: choose what to share and a licence list, read what
  the HTTP and BitTorrent exposure reveals, then give one consent that lasts a
  year and survives restarts. It drives the host's existing persistent mirror
  (a 365-day restart grant). Existing mirror selections, settings and grants
  are kept as they are. The older mirror controls move under a collapsed
  "Advanced mirror controls".

- On phones, a light system theme now draws the light tab bar and backdrop.
  Before, only the in-app Noon choice did, so the dark starry backdrop and
  tab bar showed behind the light pages.

- Publish Sovereign Mac build 10 (core 0.28 / Library 0.24): Developer ID
  signed and Apple notarized (app 088a3489…, DMG 995d3735…), DMG d02c319a… and
  ZIP c116c0f5…. Download pages now point at build 10.

- Guided setup 0.1.3 and graphical setup 0.1.5 install Linux revision 5
  (core 0.28 / Library 0.24), now served by the signed apt channel; their
  package pins are amd64 7af93ed5… and arm64 16738ce7…, and the documented
  bootstrap hash is 29a601e8….

- Prepare Mac build 10 and Linux revision 5 from the authenticated core 0.28 /
  Library 0.24 installation archive (sha256 49c28e28…, source b3e5511a), which
  adds cloud bucket mirrors, a public address and DHT/Nostr choices in setup,
  downloads that use discovered mirrors and the bundled BitTorrent DHT binding.
  Native signing, notarization, apt publication and qualification remain
  separate gates.

## 0.24.0

- Help keep models available says how the mirror is found. Setup adds an
  optional public address (HTTPS preferred, plain HTTP warned) and toggles for
  announcing on the BitTorrent DHT and publishing to Nostr relays. Both stay on
  by default, but each now says before consent that it publishes this
  computer's address publicly as a Sovereign mirror and is not anonymous, and
  the consent line names the announcements that are on. The status reports
  `dht_available`, `dht_announcing`, `nostr_publishing` and the reasons when
  either is not announcing. **Check address** fetches the public address's
  peers document from this computer only, labelled as a best-effort check. The
  DHT now announces only while something is shared. A consent given before
  this disclosure keeps sharing but announces nothing on the DHT or Nostr
  until the owner reviews **Choose how people find this computer**; the
  review is recorded beside the policy, which keeps its format.
  A public address with an unusable port is refused; one stored earlier is
  set aside and reported rather than breaking the status, and the address
  check stops at its deadline however slowly the endpoint answers.
- Bundle the BitTorrent DHT binding in the desktop profile. The Library's
  `desktop` extra is now `foxlight-model-preservation[bittorrent]==<core>`, so
  `requirements-desktop.lock` and the Mac and Linux apps built from it include
  libtorrent 2.0.14 and the community mirror's DHT rendezvous can run. Turning
  on Help keep models available then also announces a reachable mirror on the
  public BitTorrent DHT, which reveals this computer's public IP address and
  mirror port as a Sovereign mirror to anyone who looks; it is not anonymous.
  The Mac wheelhouse now selects libtorrent's macOS 14.0 build and the Mac
  builder refuses bundled native code that needs a newer macOS; the DHT binding
  is not supported on macOS 13. Linux needs no new apt dependency. Both
  builds ship libtorrent's upstream BSD-3-Clause notice (v2.0.14 `LICENSE`).
- Help keep models available gains Cloud storage: pick a provider (Cloudflare
  R2 is preselected), answer only what it needs, paste a key and choose Check
  and connect. A failed check explains whether the upload or the public
  download failed. Once connected the panel shows where people download from
  and how many models the bucket holds.
- Downloading a model from a followed source or a handoff now falls back to
  other mirrors that the approved mirrors vouch for, under the same HTTP and
  private-address choices, and remembers which mirrors delivered. Accepted keys
  are unchanged. No DHT or relay is asked.

- Prepare Mac build 9 from the authenticated public core 0.27 / Library 0.23
  installation archive, which adds Help keep models available. Native signing,
  notarization and publication remain separate gates. The publication record
  pins the Apple-accepted, stapled DMG and ZIP for the native release lane.
- Publish Linux revision 4 from the same core 0.27 / Library 0.23 archive and
  pin guided setup 0.1.2 and graphical setup 0.1.4 to its attested packages.

## 0.23.0

- Add **Help keep models available** to Sovereign's Sharing screen: one switch
  and one licence policy share every eligible backup from a long-lived mirror,
  with no per-model step. Setup asks once for standing consent, says plainly
  what others can see, and lets the licence list be widened as chips. The panel
  states whether this computer is reachable or behind a router, lists every
  backup's standing (sharing, kept back, licence not on the list, no licence),
  and offers Keep this one back, Change what's shared and Turn off. It hides on
  an older core that lacks the feature.
- Prepare Mac build 8 / Linux revision 3 from the authenticated public core 0.26
  / Library 0.22 installation archive. Native signing/publication and signed apt
  indexes remain distinct gates. Require the new first-launch controls during
  native Linux qualification and use their recovery-kit entry for GUI recovery.

## 0.22.0

- Add guided first-launch choices and reusable install/find/preserve/share/recover
  help, retaining Library for returning users and the Horizon interface.
- Offer sending a complete recovery kit separately from public-mirror preparation.
  Explain private metadata, encryption limits and sender reachability.
- Provide copyable recipient instructions with exact saved capture identifiers;
  opening or copying them enrolls no keys and starts no job.
- Explain download working space before consent and keep low-space starts disabled.
- Refresh saved discovery results after an explicit listing update even when
  Activity has no new visible job. Pair with independent core 0.26.0.

- Advance both independent native recipes to the published Library 0.21.0 and
  core 0.25.0 cohort. Keep runtime pins and model/source trust unchanged.

## 0.21.0

- Add Horizon backup destinations, verified complete recovery kits, separate
  public-copy review and finite HTTP sharing, and offline recipient handoff.
- Start Find with complete preservation selections and offer explicitly reviewed
  alternate routes from followed sources for the exact signed capture. Keep
  model keys, connection permissions and private receiving separate.
- Preserve original public identities with omitted defaults, Unicode backup
  paths and correct previous-page navigation. Prevent repeated More clicks
  while the requested page is loading.
- Pair independent desktop installation with public core 0.25.0. Native package
  input pins and signing/publication remain separate from this Python release.
- Advance the optional Core-backed preview to the verified companion-recovery
  and model-description host revision. The immutable installer checksum stays
  unchanged; existing preview data and separate runtime/code permissions remain.


- Explain required repository-code permission accurately for Skulk recovery and
  point to standalone files-only recovery when that permission is not desired.
  Keep the separate code/provenance gates. Unselected backup storage no longer
  implies that a model was successfully recovered.

- Explain a missing or size-conflicting signed companion while keeping its
  listing browsable and its download disabled. Preserve the Horizon interface,
  independent trust decisions and receive-only defaults.
## 0.20.0

- Apply the Sovereign Horizon design across Find, Library, Activity, reviews and
  every Core-backed area: starry backdrop and glass panel, pill navigation,
  Sovereign logo, amber primary actions and explanatory popovers. The companion
  warning keeps its full meaning in the "Companion file" popover. API calls,
  confirmations, consent and review steps are unchanged.

## 0.19.0

- Show readable uses, declared inputs and outputs in Find, Library and shared
  Core-host model information. Image understanding appears alongside text use;
  video understanding, video generation, speech directions and document-search
  models remain distinct. Older hosts keep task-based descriptions.
- Explain unlisted image companions and incomplete file-version claims without
  treating descriptions, automated interpretations or repository inventories as
  runtime qualification or verified download completeness.
- Keep additional automated interpretations in attributed, unverified notes;
  main capability labels use declared cards and structured source evidence.
- Preserve the source of capability information and unknown metadata. Pair the
  independent desktop profile with public core 0.24.0; native packaging and Core
  host adoption remain separate from this Python/UI release.

## 0.18.0

- Render optional Core-host card descriptions and saved-copy metadata in Library;
  show each source listing’s declared purposes in Find without merging its claims
  with signed cards. Older hosts keep working with honest missing-data states.
- Recognize code generation, visual question answering and reranking in source
  listings. Explain that source descriptions do not establish registry trust.

- Put model purpose and preserved descriptions before technical identity in the
  independent Find and Library screens. Add local saved-model search, everyday
  purpose filters, source/license/context details and honest missing-data states.
- Group matching descriptions on each catalog page while keeping file variants,
  exact card reviews and consent separate. Count model versions explicitly.
- Explain digital fingerprints and verification keys beside their exact values.
  Add contextual help usable by hover, keyboard and touch, and explain recovery
  kits, restored folders, storage sizes and advanced download allowances.
- Translate active stages, terminal states and unknown errors into readable
  guidance; remove authoring notes from accessible logo descriptions. Shared
  identity/help components also apply to Skulk Library. Core-host metadata
  adoption and native/public package publication remain separate deliveries.

## 0.17.0

- Add independent source/cache review, authenticated updates, offline attributed
  search and signed model-detail inspection in Sovereign's Find models area.
- Require a separate original capture-key decision before Download & preserve.
  Keep source-update, metadata and payload connection permissions separate; no
  new serving, router, runtime execution or global key enrollment starts.
- Preserve complete companion closure, original envelopes/cards and public
  source-key candidates through private PMF custody, recovery kits and restore.
- Fence delayed source responses after membership changes and dismissal. Keep
  newer navigation and trust decisions authoritative while durable jobs persist.
- Apply the supplied Sovereign branding to the independent app and Skulk Library.
  Include native icon assets in the Mac recipe; native public delivery is separate.
- Pair the independent desktop profile with core 0.22.0. Keep the existing
  Core-backed and managed interfaces with their separate compatible Core pin.

## 0.16.0

- Add independent `foxlight-sovereign` with Find models, Library and Activity.
  Start with authenticated public catalog browsing; keep search local/offline.
  Review complete mapped selections before private download/preservation.
- Open complete recovery kits, show exact capture keys/thresholds and restore
  files without Core or a registry. Save a kit with one action and native download
  dialog; reopen directly into Library once copies exist. Preserve the existing
  Core-backed launcher and managed adapter.
- Add a self-contained Mac source-preview shell, native file dialogs and safe
  owner shutdown. Disable runtime bytecode writes inside the sealed bundle.
  This does not by itself publish a notarized Mac app or an APT package.
- Include the public core wheel and hash-locked `requirements-desktop.lock` in
  v2 installation archives. Default installation remains the Core client; the
  explicitly selected desktop profile installs both verified public wheels with
  no private SDK, Python source build, runtime service, model trust or sharing.

## 0.15.0

- Check a pending mirror copy's local bytes from Library, Guided first launch
  and Sharing, using its original accepted model authority and current policy.
- Observe durable progress and Stop after client loss. Retry an ambiguous
  submission as the same check; another check names an exact terminal receipt.
- Keep pin/block and deletion choices, clear sharing restart permission, and
  receive no replacement bytes or new trust. Incomplete copies stay pending.
- Offer the existing recovery-kit journey only after retained custody and check
  completion. Preserve original envelopes, card extensions, companions and notices.
- Negotiate pending recovery separately from retained-kit support, so older hosts
  keep their existing controls. Include mirror cases in SDK-free package CI.
- Pin compatible Core `446079c2417f1b8ef5bef0934e605b73654e4e47`. Source-fed
  intake without a retained original wrapper remains a separate durability gap.
  This GUI requires compatible Core; standalone preservation remains Skulk-free.

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
- Keep earlier preparations accessible for their own download and cleanup after
  another preparation, including failed replacements, pagination and loss of
  the original import or retained-model metadata.
- Require the existing blocked, unpinned and stopped model state before offering
  mirror-copy removal, and explain the separate blocking step before deletion.
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
