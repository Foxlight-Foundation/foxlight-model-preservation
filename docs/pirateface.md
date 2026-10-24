# PirateFace interoperability

Sovereign can explicitly look up one exact repository in PirateFace and verify
externally downloaded repository files into an unchanged signed preservation
archive. The Python API and CLI require no Skulk, Hugging Face library, hosted
account, PirateFace Python package or startup hook.

PirateFace is an additional discovery source. A listing, infohash, mutable
revision such as `main`, or response hash **does not authenticate a model**.
Import requires the original signed legacy or PMF envelope, its independently
obtained manifest ID and independently enrolled signer trust. This first adapter
does not discover missing signed envelopes or keys from PirateFace.

## Explicit lookup

```sh
model-preserve pirateface lookup OWNER/MODEL --output candidate.json
```

Lookup makes one direct metadata GET to `https://pirateface.co/api/torrents`
for that repository. The service can observe the repository query and network
address. It does not search globally, refresh followed sources, submit a rescue
request, fetch weights, contact trackers, start a torrent client or share files.
There is no anonymity claim.

The strict `foxlight.pirateface-candidate.v1` receipt records repository,
revision hint, v1 infohash, observation time, source base and response SHA-256.
The magnet contains only that infohash: remote tracker/web-seed instructions are
discarded. Candidate metadata is limited to 256 KiB; duplicate JSON properties,
repository substitutions and conflicting magnet identities refuse. The output
is published without replacing an existing file. Keep candidates local if your
interests are sensitive.
Its [JSON schema](schemas/PirateFaceCandidate.json) describes this untrusted
receipt separately from the unchanged signed model formats.

`--source` selects another compatible API explicitly. HTTPS, pinned DNS, no
redirects and no ambient proxies are the defaults. Plain HTTP and non-global
addresses need separate `--allow-http` and `--allow-private` grants, for example
in a local test. `--deadline-seconds` defaults to 60; socket waits are bounded,
but OS DNS and filesystem stalls are not interruptible.

## Verify an existing repository download

Install the optional `bittorrent` extra through a reviewed installation method
from the [installation guide](installation.md). Only metainfo parsing needs it;
lookup uses the default core. The adapter loads the maintained libtorrent 2.0
parser and never creates a session. It does not install a torrent application.

Obtain the matching `.torrent` metainfo and repository files through an external
client of your choice. **Ordinary BitTorrent clients can upload while downloading**;
using one is a separate public sharing decision. This import command is offline
and starts no peer participation, upload, tracker announcement or background job.

```sh
model-preserve pirateface import MANIFEST_SHA256 \
  --candidate candidate.json --metainfo repository.torrent \
  --candidate-member weights --envelope original-envelope.json \
  --trust independently-enrolled-trust.json \
  --member weights=/download/model-repository \
  --member tokenizer=/local/required-tokenizer \
  --store ./new-preserved-store
```

Use the actual signed member IDs, and supply **all** members, even when a required
companion came from another source. Each directory is that member's repository
root, with its signed relative paths. For a single-file torrent the containing
directory is the root. For PMF, also supply every opaque source document with
`--document DOCUMENT_ID=/path/to/exact-record`. Unknown optional profiles remain
opaque; unsupported required semantics refuse as usual. PMF does not otherwise
require a Git origin; this particular repository mapping requires the selected
member's signed repository and immutable `git-sha1` revision.

Before opening model files, import authenticates the expected original envelope
and checks the candidate's exact repository binding. A pinned 40-hex candidate
revision must agree with the signed origin. A mutable listing hint cannot change
that origin; the complete signed sizes and SHA-256 hashes authenticate the bytes.
Metainfo must have one canonical v1 encoding with safe, unambiguous regular-file
paths. V2/hybrid torrents, symlinks, alternate UTF-8 path aliases, duplicate/case
aliases and file/directory overlap refuse. Unselected torrent files are not copied.

Every selected local file is hashed, including duplicated inputs that reuse one
stored object. All companion files and PMF source documents must pass before
publication. `--max-bytes` bounds the unique signed object total (default 20 GiB),
with disk-space preflight and a reserve. Cancellation/failure before publication
cleans private staging. A new destination is required; existing files/stores are
never overwritten. Downloaded inputs remain under the operator's control and are
never modified or deleted. No automatic retention, eviction or repair is added.

The output retains the **exact original signed envelope**, card data and source
records. It creates no new signatures, registry authority, code consent or runtime
registration. Existing [provenance verification](cards-and-provenance.md),
[PMF verification/restoration](portable-model-format.md), and recovery-kit tools
continue to apply. Independently verify registry evidence when claiming registry
provenance; an owner-capture signature cannot confer it.

## API and qualification

`pirateface.lookup` returns a `PirateFaceCandidate`; `read_candidate` parses a
local receipt. `pirateface_import.import_candidate` receives explicit trust,
manifest identity, candidate member, local sources and optional PMF documents.
Neither API requires Skulk or starts sharing. Import returns the authenticated
archive manifest after complete publication.

The conformance suite covers exact HTTP lookup, refusal paths, legacy/PMF intake,
unchanged signatures, deduplicated-source verification and complete recovery.
The native interoperability test transfers synthetic original-repository files
between stock libtorrent clients over isolated loopback, then independently
verifies the signed archive. DHT, LAN discovery, public trackers and public peers
are disabled in that test. It establishes standard v1 repository-torrent
compatibility, not public reachability, PirateFace operator independence,
cross-seeding eligibility, custody durability or anonymity.

Sovereign's exported archive torrents use content-addressed archive paths;
PirateFace repository torrents use original repository paths. They cannot be
assumed interchangeable or submitted as the same swarm. This adapter does not
submit listings or public weights, automatically receive a swarm, translate
layouts for cross-seeding, or add a Library/Guided screen. All existing sharing
controls remain explicitly opt-in. I2P/I2PSnark is a separate evaluation; this
adapter provides no protected transport or clearnet fallback policy.
