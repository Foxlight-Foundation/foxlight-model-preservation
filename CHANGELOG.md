# Changes

## Unreleased

- Pin Sovereign Mac build 14 and Linux revision 9 to the authenticated
  Library 0.28 installation archive (sha256 8ee926d2…, source d8055f4f; its
  desktop lock carries core 0.32.0 and libtorrent 2.0.14), which adds Hugging
  Face search in Find, savable listings without their own file list and an
  optional Hugging Face token. Native signing, notarization, apt publication
  and qualification remain separate gates.

## 0.32.0

- Optional Hugging Face token. Under Source and privacy, a person can add a
  Hugging Face access token for Hugging Face's higher rate limits and for gated
  models they have been granted. Sovereign checks it with Hugging Face
  (`whoami`) before keeping it, stores it in the OS keychain (macOS Keychain or
  Secret Service), and shows only the account name afterwards; it can be
  removed in one step. It is sent only to huggingface.co itself: Hub API
  requests, and the first hop of a download, never a CDN redirect target, a
  mirror, plain HTTP or I2P. It is never written to plans, logs or errors; an
  unavailable keychain means downloads continue without it. New
  `keychain` module (shared with bucket credentials); `transfer.response`
  takes an `authorization` sent only to its exact HTTPS source; owner controls
  `hub-token-set` and `hub-token-remove`; `read()` reports `hub_token` (the
  account name).

- Find searches Hugging Face, not only saved listings. Searching Hugging Face
  is a one-time choice ("Also search Hugging Face?"), because the search words
  go to huggingface.co; it can be turned off under Source and privacy, and it
  works before any registry catalog is loaded. A chosen repository is pinned to
  its current commit. Large files carry their SHA-256 in Hub metadata; the few
  small git files are fetched from that commit, checked against their git blob
  ID and hashed, so every selected file has an exact hash before any download.
  The save then uses the same verification, resume and community-mirror
  fallback as registry saves, and records the model's licence. GGUF
  repositories default to one quant (Q4_K_M when present), its split parts and
  one projector; other repositories skip duplicate encodings of the same
  weights. Every default can be changed in the file review. Gated models are
  shown with a note that Hugging Face asks for access. New core module `hub`
  (`search`, `snapshot`, `exact_hashes`) and `selection.hub_recipe`; owner
  controls `hub-consent`, `hub-search`, `hub-review` and a `receive` plan with
  `origin: hub`.

- Registry listings without their own file list (about half of the current
  catalog, mostly mlx-community) can be saved: Sovereign builds the file list
  for the exact revision the listing pins from Hugging Face's metadata for
  that commit. The registry and Skulk are unchanged. Search rows report
  `file_list` (`registry` or `huggingface`).

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

## 0.31.0

- Bucket uploads survive slow or flaky connections. The bucket client's
  timeout is 300 s (was 60 s), and idempotent requests (HEAD, PUT, DELETE and
  each multipart part) are repeated with exponential backoff, up to 6 attempts,
  after a timeout, a dropped connection or a provider "try again" status (408,
  429, 5xx). Security and validation failures, and multipart create/complete,
  are never repeated. This replaces the local retry wrapper used for the
  starter mirror.

- Hugging Face downloads resume. A save writes each file to `<file>.partial`
  and, when a transfer drops or the origin errors, retries once with an HTTP
  range from the verified prefix (re-hashed, so the finished file is still
  checked in full). A refusal (4xx, including 429) goes straight to the mirror
  fallback. Each finished save records which sources supplied its files
  (beside the journal, so older versions still read it), reported as
  `sources` on the job.

- Mirrors publish signed **object coverage** (`/.well-known/sovereign/objects.json`,
  `sovereign.mirror-objects.v1`): a Bloom filter of the file hashes they serve,
  signed by the mirror key and short-lived like a card, kept separate so strict
  card readers are unaffected. Community, persistent and bucket mirrors (and the
  starter publish script) write it. Crawls keep coverage only when the card's key
  signed it, and a catalog save that falls back asks mirrors claiming the file
  first, then seeds, then other known mirrors. HTTP mirrors answer `HEAD` for
  served files without charging the upload allowance.

- Saving from Find falls back to mirrors. Hugging Face is still tried first; if it
  can't provide a file, Sovereign fetches the same file by its signed SHA-256
  from the starter mirror and mirrors it already knows (HTTPS, public addresses
  only), verified before it is kept. Activity shows "Getting a file from
  community mirrors…", and a save no source can supply fails plainly with
  `origin_unavailable`. New `transfer.fetch_object` fetches one object by hash
  from a list of sources.

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

## 0.30.0

- Catalog updates also fetch the registry's signed card-licence file
  (`v1/card-licences.json`), checked offline against the retained signed
  targets. Listings and the save review show that licence when the card names
  none, and saving writes it into the saved copy's own signed record, so
  "Help keep models available" can share it without the registry. The
  registry publishes the file separately from catalog metadata, so it needs
  no Skulk release. A companion from another repository never inherits it, a
  licence the card names itself still wins, and a missing or unusable file
  never blocks a catalog update.

- Catalog browsing without a search or chosen use lists saveable chat models
  first (`result_order`), then the rest smallest first.

- A card that names no licence takes the licence its signed catalog metadata
  records (the registry's `license`), both in the save review and when Help
  keep models available decides what may be shared. The evidence travels in
  the backup's retained registry proof, so sharing decisions stay offline.
  A genuinely unknown licence now reads "Licence not named".

- The shipped starter (seed) mirror list now names `https://starter.foxlight.ai`,
  Foxlight's R2 bucket mirror of the starter collection. It is contacted only
  after a receive's own and discovered mirrors fail.

- The starter collection: a signed, one-time import list of the open models
  the community runs most, chosen by sustained interest (Hugging Face base and
  conversion downloads, Ollama and LM Studio). There are three tiers, each
  including the smaller ones: small (default, about 10.6 GB), standard (about
  78 GB) and large (about 310 GB), all builds that run on any platform. The
  Apple-only builds (MLX 4-bit Qwen3.8 27B and Gemma 4 E4B, and the MLX
  Parakeet checkpoint) are an opt-in Apple add-on: entries
  carry a strict `format` (`gguf` or `mlx`), and `--formats gguf,mlx`
  (`--starter-formats`, `SOVEREIGN_MIRROR_STARTER_FORMATS`) adds them for the
  selected tiers; the review shows the add-on's size. Its 38 recipes in `examples/recipes/starter/` pin exact revisions
  and every file's SHA-256. They carry the base model's LICENSE, NOTICE and
  usage policy, added from pinned sources where a conversion ships none,
  including the Gemma Terms of Use with its NOTICE line and Prohibited Use
  Policy, the Llama licences with "Built with Llama", and CC BY attribution.
  Licences are resolved from the base model, and a conversion whose tag
  disagrees is refused. Llama 3.1/3.2, Gemma 3, SDXL and XTTS v2 sit behind the
  acceptance gate. `mirror import-starter` verifies the list
  (`sovereign.starter-list.v1`, DSSE-signed, pinned by `--expected-list-id`),
  shows one review of models, licences, gates, signing keys and total size
  against the disk budget, needs consent, then retains each bundle as an
  ordinary local selection. Gated licences need exactly the reviewed
  acceptance (`--accept-terms`, `--accept-starter-terms`,
  `SOVEREIGN_MIRROR_STARTER_ACCEPT_TERMS`), recorded first. Re-running is
  idempotent, removed models are never re-imported, and nothing published
  later can stop or change an imported copy. `mirror quickstart --starter-list`
  (where `--collection` is now optional) and the container's
  `SOVEREIGN_MIRROR_STARTER_LIST`, `_LIST_ID` and `_TIER` opt in.
  `scripts/build_starter_collection.py` checks every pin against the Hub
  without downloading weights, then packs, signs and publishes the collection
  to a bucket mirror. `scripts/derive_starter_signals.py` recomputes the
  interest signals each quarter and proposes a diff, with promotion after
  three months on two signals and demotion after two weak quarters; operators
  choose what to import. Not legal advice; operators are responsible for
  their jurisdiction.

- Starter (seed) mirrors are now only a fallback. A receive first contacts
  just its approved or signed hints and the mirrors its private directory
  already knows; it reads seeds' peers documents, or tries seeds as sources,
  only after all of those failed for that manifest (or there were none). Before,
  unscored seeds were offered and asked up front, showing every receiver's IP
  address to seed operators even when the named mirrors worked. The CLI
  receives and the app's Library receive follow the same rule. Bounds are
  unchanged (`max_sources`, the HTTPS policy, crawl caps and one deadline for
  the whole receive). With `--operation-id`, the fallback stage is fixed once
  beside the receipt (`.operations/<id>.fallback.json`) and extends the same
  bound request, so a resume reuses it and never trips the changed-request
  check. Each fallback is noted in the private directory's `fallbacks.json`
  and listed by `mirrors list` as `starter_fallbacks`. `--look-further` is
  unchanged. New API: `mirror_directory.plan_fallback`,
  `MirrorDirectory.read_fallbacks`/`note_fallback`,
  `transfer.SourcesExhausted`, `fetch(..., request_sources=...)` and
  provisional child controls (`OperationControl.fork(provisional=True)`,
  `resume_after`, `settle`, `conclude`). Progress observers, including the
  Library and `--progress`, never see the primary stage's failure as terminal;
  only the receive's final outcome is. The caller's control always ends with that real
  outcome (stage, bytes, commitment, error code), including when the primary
  stage was final or planning failed. Without `--operation-id` both stages
  share one in-memory `max_bytes` allowance. A failure of the receiver's own
  storage (full disk, failed write or sync) now raises
  `operations.StorageFailed` (`storage_error`) at once instead of counting
  against the source being read, so it never triggers the fallback.
  The fallback stage gets only what is left of the receive's deadline: its
  seed crawl and fetch cut every request timeout to that remainder, and with
  none left no seed is contacted. Nested crawls, spot checks, discovered
  fetches and manifest reads likewise never run past a deadline their
  control already carries (`OperationControl.deadline_after`).
  A named receive's fallback stage keeps the primary stage's unused durable
  reservation in the same process (`ledger.CreditCarry`), only while the
  receipt on disk is unchanged; reservations are still never refunded.
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

## 0.29.0

- Catalog withdrawal is now advisory: no catalog publisher, Foxlight
  included, can take down a mirror's copies. Catalogs are discovery, not
  authority. When a followed catalog stops listing a retained copy, the mirror
  keeps serving it. It records which source withdrew it, the exact catalog ID
  and when, and `mirror status` lists it under `withdrawal_advisories` with the
  `mirror block` command for that copy. Takedown stays with each operator:
  `mirror block` is immediate and persistent. `mirror config
  --follow-withdrawals on` (also on `mirror quickstart` and
  `SOVEREIGN_MIRROR_FOLLOW_WITHDRAWALS` in the container) is an opt-in, off by default, that restores the
  previous behaviour with its safeguards. It is reviewed policy and clears the
  restart grant. The new `mirror acknowledge` records that you reviewed an
  advisory, and that copy then keeps serving even with following on.
  `mirror unwithdraw` is a deprecated alias. Discovery is unchanged: a
  withdrawn entry is not newly admitted or fetched. **Upgrade:** a ledger's old
  `withdrawn: true` receipts are read as advisories, so those copies serve
  again unless you block them or follow withdrawals. The first write after
  upgrade stores the new form and logs a warning listing them. Earlier releases
  refuse a ledger with an advisory or the setting.

- Licences come in one canonical, versioned family definition
  (`foxlight_model_preservation.licences`, `licence_families_v1.json`). The
  defaults follow Hugging Face: Permissive, Attribution, Copyleft, Responsible
  AI, a new Non-commercial family (switch it off for a commercial mirror) and
  makers' own terms that allow redistribution (Llama 2 to 4, Gemma 1 to 3,
  Qwen, Tongyi Qianwen) are on. Grok 2, DeepFloyd IF and Apple's research
  licence stay off because we couldn't confirm they allow redistribution. Llama
  3.2 and Llama 4 have their own switches and an EU notice. Unknown, "other"
  and blank licences still never match. Policies saved earlier keep their
  exact licence lists. This is not legal advice; operators are responsible
  for their jurisdiction.

- Keeping or downloading a model whose licence has use restrictions or
  conditions to pass on (or can't be identified) now asks, as Hugging Face's
  gated models do, for an explicit "I accept these terms" after showing each
  licence's name, a plain summary and a link to its full text. Sovereign
  refuses a start without exactly that acceptance (`terms_acceptance_required`)
  and records it in `terms-acceptance/<job>.json` before any download. Receive
  plans carry a `terms` review. Plain permissive licences need no tick.

- Upload caps are opt-in and off by default for the desktop community mirror,
  the CLI persistent mirror, quickstart and the container. "No cap" is `None`
  in the community policy (`sovereign.community-mirror.v2`) and the mirror
  ledger (`foxlight.mirror-state.v6`); uploads are still counted, but never
  refused or answered with 503 for volume. Pass `--egress-daily none` (or the
  `none` environment value) to remove a cap. Stored policies and ledgers keep
  the caps they have; older releases refuse the new formats rather than
  misread them. The disk budget and the daily download budget stay bounded.

- The persistent mirror can keep a standing intake policy (`mirror config
  --share all|chosen --licence-families ... --licenses ...`, `mirror
  withhold`/`release`). The restart grant covers the policy as a whole, so
  retaining a newly matching public copy keeps the grant; changing the policy,
  or adopting one on an existing mirror, clears it and needs fresh consent.
  Withholding a copy only narrows and never clears the grant.

- `mirror quickstart` is now all or nothing on an existing mirror. It checks
  the settings, the source's bootstrap ID, disk admission under the new budget
  and the grant length before writing anything, and restores the previous
  ledger if a later step still refuses. A refused reconfiguration (for example
  a new source with a disk budget below what is already retained) previously
  cleared the restart grant, so the existing service could no longer resume.

- Add an optional hardened systemd system service for the headless mirror,
  `deploy/mirror/sovereign-mirror@.service`, for always-on Linux servers and
  NAS boxes without Docker. It runs as the unprivileged `sovereign` system
  account from `/opt/sovereign/current`, keeps state in
  `/var/lib/sovereign/mirrors/<instance>` and cannot see home directories or
  write outside its state. `deploy/mirror/install-system-service.sh` sets it
  up as root, refusing an unauthenticated release archive. The container and
  the per-user unit are unchanged; the docs now compare what each of the three
  options isolates.

- The Amazon S3 bucket preset takes an optional public address, such as a
  CloudFront distribution in front of a private bucket (Origin Access
  Control). Uploads still go to S3; the setup check downloads through that
  address, so **ready** proves the whole CDN path. It must be a plain
  `https://` address (`bucket_public_url_invalid` otherwise). Left empty, the
  public bucket's own address is used as before. Presets now say which
  answers are optional.
- The Cloudflare R2 preset takes an optional jurisdiction (`eu`, `fedramp` or
  `us`) and uses that jurisdiction's S3 endpoint,
  `https://ACCOUNT_ID.JURISDICTION.r2.cloudflarestorage.com`. Nothing new is
  stored: the jurisdiction is part of the saved endpoint.
- The Cloud storage panel lists the models the bucket serves (name, manifest
  ID, size, upload time) from the app's own records, and **Export
  verification info** saves the ordinary one-file handoff for each one: the
  signed manifest, its signer's public trust profile and the bucket's public
  address, with no keys or local paths.
- The bucket state records upload times as `sovereign.community-bucket.v2`.
  A state without them is still written as version 1, unchanged; older
  versions refuse a version 2 file rather than misread it.

- Add a Hetzner Object Storage bucket preset (`hetzner`): the location code
  (`fsn1`, `nbg1` or `hel1`) gives the endpoint, signing region and public
  address. A bucket target saved with it is not readable by older versions.

## 0.28.1

- The mirror's default standing restart grant (quickstart, the container's
  `SOVEREIGN_MIRROR_GRANT` and `--resume-for`) is now 180 days, up from 90.
  The 366-day cap is unchanged.

- Publish the headless mirror image to
  `ghcr.io/foxlight-foundation/sovereign-mirror:VERSION` for each core release
  tag, with a signed build provenance attestation bound to that tag (the first
  is 0.28.1). `compose.yaml` now pulls that image by default and
  `SOVEREIGN_MIRROR_IMAGE` pins a verified digest; `up --build` still builds the
  checkout.

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

## 0.28.0

- Threads of one app now take turns on a mirror's ledger instead of refusing
  each other. Bucket uploads charging the upload limits could otherwise make
  the mirror record a spurious failure and briefly stop serving a healthy
  model, which also withdrew it from a connected bucket. Separate processes
  are still fenced by the file lock.

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
- Help keep models available can keep a copy in the owner's own cloud bucket.
  Connecting checks upload and anonymous public download before anything is
  saved; keys go only to the OS keychain. Shared models upload as soon as they
  join, withheld ones are withdrawn, and turning sharing off withdraws them all.
  The mirror's signed card then points at the bucket, so a computer behind a
  router is still discoverable. Disconnecting forgets the keys and leaves the
  bucket's contents alone.
- Receives now use discovered mirrors. `fetch`, `fetch-invitation`,
  `source-fetch` and `handoff-fetch` read the peers documents of the mirrors they
  already use, then add up to eight mirrors whose cards claim the model (sampled
  at random within score bands) and not-yet-scored seeds, after the explicit or
  signed hints. Discovered sources pass the receiver's policy (HTTPS only unless
  `--allow-http`, no private routes unless `--allow-private`, bounded
  `max_sources`), never affect trust, and are scored by what they delivered.
  `--no-discovered-mirrors` opts out. A resumed `--operation-id` keeps its
  sources. Receiving still never contacts the DHT or Nostr relays unless
  `--look-further` is given, which discloses the computer's IP address to
  strangers. Add `model-preserve mirrors list|import|export|crawl` for the
  private directory and an untrusted, currently empty seed list
  (`resources/seed-mirrors.json`, extendable with `--seed-mirrors`). The app's
  source and handoff receives use the same path and score what they used.
- Prepare Mac build 9 from the authenticated public core 0.27 / Library 0.23
  installation archive, which adds Help keep models available. Native signing,
  notarization and publication remain separate gates. The publication record
  pins the Apple-accepted, stapled DMG and ZIP for the native release lane.
- Publish Linux revision 4 from the same core 0.27 / Library 0.23 archive and
  pin guided setup 0.1.2 and graphical setup 0.1.4 to its attested packages.

- Add bucket mirrors (`bucket`). A public-read bucket at Cloudflare R2, Backblaze
  B2, Amazon S3, Wasabi or any S3-compatible service becomes a complete mirror
  with no server or open port. Provider presets need only a few answers; keys
  live in the OS keychain and never in files or logs. A setup check proves
  upload and anonymous public download before anything is shared. Uploads
  verify every object first, skip what the bucket already holds, use multipart
  for large files and publish the signed manifest last. Private archives are
  refused. Requests use AWS Signature Version 4 from the standard library.

- Make a headless public mirror a one-command job. `mirror quickstart` reads one
  strict, versioned collection file (`sovereign.mirror-collection.v1`: source,
  expected bootstrap ID, model trust and terms), prints a single review and,
  after typed or `--yes-i-consent-to-public-serving` consent, runs init, follow
  and grant-resume. `--plan-only` changes nothing.
- Add `deploy/mirror/`: a multi-arch (amd64/arm64) image with hash-locked
  dependencies and the `bittorrent` extra, and a compose file with Caddy
  automatic TLS (access log off) in front of a loopback mirror. Without the
  consent variable the container is plan-only and serves nothing. A CI workflow
  builds the image without pushing; publishing and signing are not set up yet.
  The systemd unit is now a user unit whose paths match the installer.
- Mirror sizes accept binary units (`500G`, `2T`); `--name` defaults to the host
  name; `grant-resume --for 365d` keeps the 366-day cap; `--bind` also accepts
  `::` and `::1`. The per-model limit now defaults to the disk budget less
  headroom instead of 20 GiB.
- Add `mirror config` for name, budgets, bind, port, serving window, refresh and
  announcement. Any effective change clears the restart grant.
- Ingress and disk budget refusals are now deferred and retried automatically
  in a later cycle, without new contact until the budget could admit them and
  without refunding or double-charging reservations. Other failures still need
  `mirror retry`, which can now `--adopt-source-grant` after a re-follow.
- A selection withdrawn from every current followed catalog stops being served
  automatically (bytes are retained); this is the takedown path. Withdrawal
  persists until a current catalog relists the model or `mirror unwithdraw`,
  even if the withdrawing catalog later expires. An expired or unreachable
  catalog is availability, not withdrawal.
- `MirrorDirectory.spot_check(max_checks=...)` and `nostr.find_cards` /
  `publish_card(max_bytes=...)` add optional bounds (defaults unchanged), so a
  gossip round's reads stay within its ingress reservation.
- With `--public-url`, a CLI mirror signs a mirror card with its own key, serves
  `/.well-known/sovereign/peers.json` and gossips like the desktop community
  mirror. Nostr publishing and DHT announcing are separate opt-ins that require
  acknowledging that they publish the address. Serving is not anonymous.

## 0.27.0

- Add Nostr relays as a discovery channel (`nostr`). Mirrors publish their
  signed cards as replaceable kind-30471 events to several independent relays,
  and clients query any relays they choose. Events are checked with BIP340
  (validated against the official test vectors) and the card inside is still
  verified with its own mirror key, so relays can drop cards but not forge them.
  The app's mirror publishes and reads when reachable and `publish_nostr` is on,
  to `wss://` relays it can change. No new dependency is required.
- Add the BitTorrent DHT rendezvous (`mirror_rendezvous`, optional `bittorrent`
  extra). Mirrors announce their HTTP port under one well-known key on the public
  mainline DHT, so a client with no contacts can find a first mirror and then
  follow signed peers documents. The app's mirror announces only when reachable
  and `announce_dht` is on, re-announces every 15 minutes, and uses lookups to
  find other mirrors. UPnP, NAT-PMP and local service discovery stay off.
- Add decentralized mirror discovery. Each mirror signs short-lived,
  self-certifying mirror cards (addresses plus a Bloom filter of what it holds)
  with its own mirror key, and serves `/.well-known/sovereign/peers.json` with
  its card and the peers it has itself spot-checked. Clients keep their own
  scored directory, crawl outward from any mirror they know, sample untrusted
  candidates at random, and fetch from whichever deliver verified bytes
  (`mirror_directory.fetch_discovered`). The app's community mirror announces
  itself when reachable (a declared `public_url` or a public address), gossips
  every 15 minutes over public routes only, and learns from the mirrors a user
  already approved when downloading. No mirror list is required.
- Add **Help keep models available** to the desktop owner: one long-lived
  mirror of every backup whose licence is in one permissive, widenable policy
  (Apache-2.0, MIT, BSD and OpenRAIL-M by default; licences come from the backup
  or its original model card). New backups join on their own, there is no hour
  limit, and serving resumes after a restart under one standing, revocable
  consent. Upload caps still apply. A backup with no identifiable licence stays
  private. Withholding or turning it off blocks serving and deletes nothing.
- Link immutable store objects instead of copying them when publishing and
  mirroring on the same filesystem, so sharing a backup needs almost no extra
  space. Ordinary verified copies remain the fallback.
- Make public copies network PMF archives (`foxlight.public-preservation.v1`)
  whose identity depends only on the public selection, never on its signer.
  People who share the same model under the same terms converge on one identity,
  so their mirrors serve each other's recipients. The desktop app receives public
  PMF archives directly, and a received public copy can be shared again under
  its exact identity with no new publication.
- Carry draft PMF archives natively over the network. Fetch, invitations and
  handoffs, persistent mirrors (retain, follow, serve, prune, recovery and kit
  export), HTTP seeding, IPFS sessions and BitTorrent export dispatch on the
  signed payload type, with no conversion into another identity. PMF source
  documents travel and verify as objects. Public intake on every member is still
  required, so private backups are never served unchanged.
- Prepare Mac build 8 / Linux revision 3 from the authenticated public core 0.26
  / Library 0.22 installation archive. Native signing/publication and signed apt
  indexes remain distinct gates. Require the new first-launch controls during
  native Linux qualification and use their recovery-kit entry for GUI recovery.

## 0.26.0

- Expose saved-copy public verification instructions without granting trust or
  returning private keys. Keep original card and provenance bytes unchanged.
- Include the existing conservative working-space reserve and observed free
  space in independent download reviews; enforce the same reserve again in the
  worker. No lower-space or integrity bypass is added.

- Align the Mac build 7 and Linux revision 2 recipes with authenticated public
  core 0.25.0 / Library 0.21.0 bytes and their exact source revision. Native
  signing, publication and installed-app qualification remain separate.
- Handle automatically removed AppleDouble metadata while replacing generated
  Mac console stubs. Keep unexpected-directory refusal.

## 0.25.0

- Start independent Find with complete signed selections ready for review, while
  keeping every supported signed listing inspectable and showing honest counts.
- Offer explicitly reviewed alternate HTTP routes from followed sources for the
  same exact manifest. Keep supplier statements and the selected model-key policy
  separate; recheck statements before networking. Cached hints imply no health or
  independent custody. Protected/peer routes never become implicit gateways.
- Show metadata connection choices before contacting HTTP mirrors; retain separate
  local-network, capture-key, byte-limit and private-receiving decisions.
- Keep original already-public payload identities when defaults were omitted,
  preserve selected Linux folder whitespace, and return to actual visited Sharing
  and source-result page offsets.

- Add independent Horizon backup folders, complete recovery-kit readback and
  standalone `kit-verify`; pin the original destination and refuse missing mounts,
  changed markers, replacement folders and overwrite attempts. Forgetting a
  destination keeps its files; no automatic retention or deletion is added.
- Open HTTP sharing files offline in the independent app, retaining separate
  exact-copy key acceptance, connection permissions and verified PMF custody.
- Prepare separate public copies through the existing public-only projection,
  preserving original card extensions and signed evidence. Require separate
  consent for finite HTTP mirror sessions, retain durable upload accounting and
  expose a recipient handoff only after verified serving selection. Stop keeps
  copies; restart opens no listener. Native peer sharing remains separate.
- Decode original cards for public-content review without changing signed bytes;
  keep large proofs outside compact controls and reject changed preview metadata.
  Fence abandoned backup reviews, report selected-drive space, keep Linux pickers
  alive for bounded interaction and terminate them on cancellation/quit.
- Refuse native peer policies in HTTP-only app sessions, permit a separately
  reviewed IPv4 route for isolated LANs, and withdraw handoffs on deselection.
- Drain accepted backup review/setup calls before releasing owner exclusivity;
  page long Unicode destinations, refuse broader mirror selections, and expose
  explicit interrupted-staging cleanup while retaining all saved copies.
- Advance only the independent owner's private journal to v2, retaining validated
  prior metadata. Signed card/archive/kit formats and trust roles are unchanged.
- Add a narrowly scoped native Mac backup-folder picker and Linux picker
  dependency; preserve correct `.foxhandoff` filenames in native save dialogs.


- Preserve complete signed selections with vision/audio/video metadata, named
  projectors, video companions and same-repository draft/active MTP files.
  Refuse missing or size-conflicting companions before downloading; keep whole
  external repositories without complete understood maps unsupported.
- Share one exact source/revision/path file map between review and download,
  keeping original cards, extensions, registry proofs and recovery identities.
- Show legacy reference-audio inputs and video soundtracks without inventing
  transcription, audio understanding or independent music-generation features.
- Target native Mac build 6 at the attested core 0.24 / Library 0.20 cohort,
  bringing the Sovereign Horizon interface to the native app. Native signing,
  installation and publication remain separate from this recipe update.
- Target native Mac build 5 at the attested core 0.24 / Library 0.19 cohort,
  including multimodal descriptions, declared input/output direction and
  selected-file caveats. Native signing, installation and publication remain
  separate from this recipe update.

## 0.24.0

- Describe model uses and input/output direction from complete captured tasks,
  structured capability claims and modalities, including image-text-to-text,
  reasoning and video understanding. Do not infer capabilities from model names
  or turn text compatibility flags on embedding models into chat support.
- Include the exact selected card’s signed-catalog sidecar in local Find/review
  and saved-copy search. Retain claim source and selected-file limitations; a
  repository projector inventory is not proof it is in a selected download.
- Read original retained-card documents up to the existing 8 MiB metadata limit
  for optional descriptions, accommodating normal publication proofs above the
  former 512 KiB preview limit. Keep 1,536-byte display summaries, complete
  original objects, archive identities, trust checks and owner journals unchanged.

- Target native Mac build 4 at the attested core 0.23 / Library 0.18 cohort,
  including the human-centered browsing, metadata and recovery interface. Native
  signing, notarization, installation and public final-container publication
  remain separate from this recipe update.

## 0.23.0

- Expose the bounded pure model metadata projection for hosts with installed cards
  that lack portable envelopes or pinned revisions. No card identity or trust is
  manufactured, and original metadata remains unchanged.

- Add bounded descriptive views of preserved PMF metadata and original retained
  cards for the independent Library. Keep descriptions, tasks, origin, license
  attribution, text context and runtime notes separate from verification and
  execution authority. Unknown profiles stay opaque; original objects and the
  versioned owner journal are unchanged.
- Support local everyday-purpose search and filters in the saved registry
  catalog. Bound descriptive responses inside the existing control-plane limit
  with exact forward pagination. Display reads fetch no weights or metadata.
- Expose the actual active operation stage for plain-language progress; retain
  explicit starts, per-copy key acceptance and complete export/restore checks.

## 0.22.0

- Follow independently reviewed HTTP(S) source bootstraps and offline caches from
  the independent local owner. Keep explicit authenticated updates, local search,
  model-metadata inspection and copy-scoped key acceptance separate.
- Preserve complete source-fed models and companions into PMF, pinning the
  original signed wrapper and reviewed mirror routes before any object fetch.
  Keep durable cancellation/history and no automatic network replay or sharing.
- Retain public original-capture key candidates, thresholds and revocations in
  the optional `foxlight.preservation-source-keys.v1` PMF profile. Default
  conversion identities remain unchanged; old generic PMF readers preserve the
  optional record. Reverse conversion still requires independent original trust.
- Add `pmf-import-preservation --retain-source-keys` and preserve the candidate
  record in a reverse-conversion sidecar. Never turn embedded keys into authority.
- Add relocatable bundled Mac commands and a separate final-container verification
  and attestation publication lane. Python releases and recipes do not themselves
  publish a signed native app. Existing archive/kit identities remain unchanged.
- New source job kinds require the updated independent owner for Activity history.
  Older owners refuse unknown journal records; this does not invalidate portable
  archives or authorize resetting local state during a downgrade.

## 0.21.0

- Add the independent Sovereign local owner: authenticated preset discovery,
  offline search, exact complete model/companion downloading and private PMF
  custody without Skulk, a managed SDK, GPU or account. Retain all original card
  data and signed registry evidence, including unknown properties.
- Journal finite work before starting; never replay downloads after restart.
  Keep completed receipts in paginated private history with durable request
  bindings. Explicit input-transfer cleanup keeps originals, saved copies and
  kits, and refuses transfers needed by active imports.
- Preserve exact reviewed kit signature wrappers and capture thresholds during
  fresh-owner import/restore. Check publication freshness against observed TUF
  rollback floors. No model execution or sharing starts automatically.
- Add the two-wheel public desktop installation profile. The Library archive
  includes its exactly pinned core from the same release source, separate hashed
  dependency locks, normal/offline installation and SDK-free local-host checks.
  Existing v1 core installation records and model/archive wire formats remain.

## 0.20.0

- Save source-fed mirror intake's original authenticated signed wrapper privately
  before receiving objects. Pin its exact bytes, source/catalog/entry attribution,
  independently selected trust/terms and original transfer allowance. Retry uses
  that wrapper rather than replacing provenance from an available supplier.
- Extend offline inspection/recovery and explicit operator prune to these
  unfinished source copies. Complete bytes can recover after source/catalog loss;
  partial, corrupt, changed-authority and removed selections refuse. Shared objects
  remain protected by other unfinished selections; no deletion or sharing is
  automatic. Existing recovery kits and signed archive identities stay unchanged.
- Require owner-state v5 for source metadata custody so older mirror owners refuse
  instead of ignoring partial source references during deletion. Existing v1-v4
  wire/policy identities stay unchanged; local/native/HTTP configuration cannot
  downgrade v5. Legacy unattended restart requires a renewed explicit grant;
  ordinary manual startup can upgrade before any listener/worker starts.
- Add `transfer.inspect_envelope` and optional exact original-wrapper fetch.
  Supplied metadata is reauthenticated before any objects; a different valid
  destination wrapper refuses rather than stripping its existing signatures.

## 0.19.0

- Add explicit offline `mirror recover` and its cancellable embedding API for
  complete pending copies after an interrupted intake/receipt. Authenticate
  original model keys, exact identity and all public terms; verify every actual
  object before promoting. A retained local intent can restore the unchanged
  original signed wrapper after publisher/input loss.
- Keep missing/corrupt/removed copies unpromoted, preserve block/pin/policy and
  accounting, and clear restart permission after pending recovery. This starts
  no networking or sharing, enrolls no keys, deletes nothing and does not bypass
  separate recipient, registry or runtime trust. Library pending-copy adoption
  remains separate from its released retained recovery-kit controls.

## 0.18.0

- Inspect exact retained mirror state independently of catalog/application
  receipts or pagination with `mirror selection`. Corrupt/unavailable state
  refuses instead of appearing empty. `mirror inspect` authenticates original
  signed metadata and complete card/companion terms without scanning weights;
  a pending local intent is metadata, not proof of complete payload bytes.
- Export a complete retained copy with `mirror export-kit` and the reusable
  standalone recovery API. Original wrapper bytes, every opaque card/companion
  and candidate public trust survive; every object is verified while copying
  into an atomic, non-overwriting kit. Existing serving need not stop. No new
  signer, network, runtime or recipient trust authority is granted.
- Let enrolled hosts optionally pin the source store's device/inode during kit
  export, refusing a replaced directory before reading its metadata or objects.
  This changes no signed archive/kit format or existing identities.

## 0.17.1

- Allow a trusted embedding process owner to select the direct native peer
  session adapter without a nested Python module launcher. Qualified native
  command helpers remain in the caller's owned process group; standalone callers
  keep their default EOF-fenced supervisor. No model, network or restart
  authority is added by this programmatic adapter.
- Isolate a direct runner's data deadline from its controller while sharing
  cancellation, so normal expiry and slow daemon teardown allow the next
  finite native session without marking the retained model failed.

## 0.17.0

- Choose HTTP serving independently of persistent IPFS/BitTorrent sessions with
  `mirror http --enable` or `--disable`. Disabled HTTP constructs no listener;
  native-only sessions keep their existing trust, budgets, Stop and retention.
- Bind the choice into explicit owner-state v4 and clear restart permission when
  it changes. Older v1/v2/v3 state and restart policy identities remain unchanged;
  local admission and native configuration preserve v4 without downgrading it.
- Native-only startup reports no HTTP port. It does not grant an unconfigured
  native transport, change outbound source permissions or start a router. A run
  with no selected serving transport refuses before worker/network effects.
- Read startup policy under the owner lock. Add optional typed live activity
  observations and `mirror run --observe`, with bounded events and a final
  drained status. Worker/listener activity is not public reachability evidence.

## 0.16.0

- Admit an independently trusted exact public copy into a persistent mirror
  offline, without creating or following a catalog. `mirror retain` copies and
  verifies complete companions and original card/provenance bytes, pins the exact
  signed wrapper, and preserves operator pin/block/deletion policy.
- New local selections invalidate standing restart consent and require explicit
  serving grants. Partial failures retain bytes but acquire no network repair
  authority. The owner-state v3 profile preserves legacy v1/v2 policy identities;
  unchanged HTTP/IPFS/BitTorrent sessions can serve the retained public v1 copy.
  PMF network transport remains separate and fails closed before local admission.
- Retain signed intake metadata privately before copying, permitting exact
  operator prune of interrupted local copies after input loss while protecting
  shared objects. Explicit retry can restore a damaged wrapper only from the
  independently authenticated, originally pinned bytes.
- Keep removed local selections as tombstones. Re-admission requires separate
  `--confirm-reintake`, keeps their block/accounting and clears restart consent.
  Persist signed metadata before ledger selection so interrupted admission or
  removed-copy retry cannot wedge deletion of another retained model.

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
