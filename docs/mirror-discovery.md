# Mirror discovery: the network is the list

Mirrors find each other without a central list. Integrity never depends on
discovery: every byte a client receives is checked against model keys it chose
independently. So a list of mirrors only has to be *useful*, not *correct*. A
wrong or hostile entry costs one failed connection and can never substitute a
model. That is what lets discovery be decentralized here.

## Mirror cards

Each mirror has its own Ed25519 **mirror key**, separate from any capture or
publishing key. It signs a short-lived **mirror card**
(`application/vnd.sovereign.mirror-card.v1+json`) holding:

- one to eight plain HTTP(S) base URLs where it can be reached;
- a coverage filter: a Bloom filter of the manifest identities it holds,
  with about one percent false positives and no false negatives;
- optionally the catalogs it follows, a coarse self-declared region and an
  operator contact;
- an issue time and an expiry, at most 14 days later (7 by default).

A card is self-certifying: it is signed by the key it names, so nobody can forge
another mirror's card. It grants nothing else. A mirror that stops renewing its
card simply lapses.

## Gossip: `/.well-known/sovereign/peers.json`

A mirror serves a `sovereign.peers.v1` document at
`/.well-known/sovereign/peers.json`. It contains:

- `self`: the mirror's own card, when it has a reachable address;
- `peers`: up to 64 other mirrors' cards that **this mirror has checked
  itself**. For each, it fetched a manifest the card claims and confirmed that
  the signed payload hashes to that identity.

Knowing one mirror therefore reveals more. A client verifies every card it reads
and skips any that fail.

## A rendezvous nobody owns: the BitTorrent DHT

A client with no contacts at all still needs a first mirror. Mirrors announce
their HTTP port on the public BitTorrent mainline DHT under one well-known key,
the SHA-1 of `sovereign.mirror-rendezvous.v1`. That DHT is run by millions of
independent nodes. DHT nodes record the address they see, so a mirror never has
to know its own public address. A client looks the key up, asks each address for
its peers document, and keeps only cards that verify. A client does this only
when asked (`--look-further`, below). The DHT carries addresses
and nothing else: a squatter or non-mirror at an announced address costs one
failed request.

Announcing is part of the explicit mirror role. It needs the optional
`bittorrent` extra (libtorrent 2.0), and the app's mirror does it only when it
is reachable and `announce_dht` is on (the default). Joining the DHT shows this
computer's address to DHT nodes and relays a little DHT traffic for others.
Local service discovery, UPnP and NAT-PMP stay off. An IPFS DHT rendezvous can
follow the same pattern later.

## Nostr relays

Mirrors also publish their signed cards to several independent Nostr relays, as
replaceable events of kind 30471 tagged `t:sovereign-mirror-v1` and `d:<mirror
key ID>`. A client queries whichever relays it chooses. Relays are many
independently run servers, so no single relay operator controls discovery. A
relay can drop or delay cards, but it cannot forge or alter one: each event's
BIP340 signature and id are checked, then the card inside is verified with its
own mirror key, and the `d` tag must name that key.

The app's mirror publishes when it is reachable and `publish_nostr` is on (the
default), to its `nostr_relays` list, which defaults to five public relays and
accepts only `wss://` URLs. It also reads cards from those relays. Relays see
the network address of the publishing mirror and of each querying client. The
secp256k1 key used for these events is separate from every other key and signs
nothing but public announcements.

## A client's own directory

Each client keeps an owner-private directory of the mirrors it has seen,
together with its own record of which ones delivered correct bytes. The command
line keeps it at `~/.local/state/foxlight-preservation/mirror-directory` (or
`$SOVEREIGN_MIRROR_DIRECTORY`, or `--mirror-directory`); the app keeps its own
under its data folder. It is never shared as-is. It is filled from:

- the peers documents of mirrors a receive already uses: the explicit
  `--mirror` hints, a handoff's or invitation's signed hints, or a followed
  source's catalog hints. A handoff therefore seeds a new client's directory
  with every mirror its hints vouch for;
- not-yet-scored entries of a seed list (below), only when a receive's own
  sources have all failed;
- `model-preserve mirrors import`, from a peers document another device
  exported;
- `model-preserve mirrors crawl`, which follows peers documents outward from
  known mirrors within the crawl bounds (32 documents by default);
- only on explicit request, the DHT rendezvous and Nostr relays
  (`--look-further`, below).

### How a receive uses it

`fetch`, `fetch-invitation`, `source-fetch` and `handoff-fetch` use discovered
mirrors by default (`mirror_directory.fetch_with_mirrors`). A receive has up to
two stages.

**Primary stage.** Before the receive, the client reads the peers documents of
its remote hints, without following them further, for at most 20 seconds. Its
source list is:

1. the explicit or signed hints, exactly as given and always first;
2. up to eight more sources: mirrors already in the directory whose card's
   Bloom filter claims this manifest. Hints plus extras never exceed the
   receive's `max_sources` (16 by default).

No starter (seed) mirror is contacted in this stage.

**Fallback stage.** Only if every primary source failed for this manifest (none
supplied the signed manifest, or some object could not be had from any of
them), or there were none, does the client turn to the seed list. It picks at
most four seeds it has never scored, reads their peers documents the same
bounded way, and tries the primary sources again followed by mirrors that now
claim the manifest and seeds that no card speaks for yet. Discovered sources
across both stages stay within eight, all sources within `max_sources`, and one
deadline covers both stages. Cancellation, a deadline, a budget or a local
failure never falls back. With no seed list, or no room left, the stage
contacts nobody and the primary failure is reported.

Every extra must pass the receiver's own policy: HTTPS only unless
`--allow-http`, and no loopback, private, link-local or `.local` address unless
`--allow-private`. Many app mirrors advertise `http://` addresses, so they are
used only with `--allow-http`. Each card contributes at most two addresses.

Mirrors are sampled at random within score bands. A mirror's band is
`floor(log2((successes + 1) / (failures + 1)))`, so a new mirror sits in band 0,
one good delivery lifts it to band 1 and one failure lowers it to band -1.
Better bands come first; order inside a band is random each time, so neither
list order nor a flood of fake cards decides which mirrors a client tries.

Sources are tried in order for each object, so discovered mirrors are fallbacks
when the hints fail. After the receive, success or failure, each remote source
is scored: one failed object marks it failed for that receive, and only clean
delivery counts as a success. A discovered mirror or seed that was asked for the
signed manifest and could not supply an authentic one is also scored as failed;
sources never asked are not scored. A mirror is dropped once it has three failures and
more failures than successes. A scored seed is no longer offered as new; once a
seed publishes a card, the card speaks for it. With `--operation-id`, the
discovered sources chosen the first time are kept beside the operation's
transfer receipt in the destination store
(`.operations/<operation-id>.sources.json`) and reused on resume, so the
operation's transfer allowance still binds the same request for as long as the
receipt exists. The plan is created once (written, synced, then linked into
place so it is never partial or replaced) before the receipt. The receipt binds
the primary stage's sources. A fallback stage is fixed the same way when first
used (`.operations/<operation-id>.fallback.json`, including which starter
mirrors it asked) and extends that same request, so a resume after it goes
straight to the recorded fallback stage: it asks no further seed and does not
trip the changed-request check. Reusing the
operation ID with other hints or another manifest is refused before anything
is written, even if an earlier attempt stopped before its receipt was created.
If the private directory is damaged, the receive proceeds with the hints alone
and binds that plan.

`--no-discovered-mirrors` restores the previous behavior: only the given
mirrors are contacted, no peers document is read and nothing is recorded.

Discovery never touches trust. The manifest and every object are verified
against the trust the user chose independently, and a discovered mirror can
only waste a bounded number of attempts. Receiving never announces, listens,
seeds or publishes.

### Seed lists

The package ships an untrusted seed list,
`resources/seed-mirrors.json` (`sovereign.seed-mirrors.v1`, a list of HTTP(S)
base URLs). It is empty for now. A user can add their own file with
`--seed-mirrors` or `$SOVEREIGN_SEED_MIRRORS`; its entries are added after the
shipped ones. A seed grants nothing: it is only a place to ask when everything
else failed (the fallback stage above).

**Privacy.** Contacting a seed shows this computer's network address, and the
fact that it is looking for mirrors, to whoever runs that seed. That is why a
receive asks seeds only after the mirrors it already knows have failed, and
never when they work. Each time it does, the client notes it in its private
directory (`fallbacks.json`: when, which manifest, which seeds were asked; the
latest 64), shown as `starter_fallbacks` by `model-preserve mirrors list`.
`mirrors crawl` asks seeds directly, because that command is an explicit
request to explore.

### Looking further: an explicit privacy choice

By default a receive never contacts the BitTorrent DHT or a Nostr relay, because
those lookups show this computer's IP address to strangers and would let anyone
watching a public rendezvous map who is looking for mirrors. `--look-further`
(on the receive commands and on `mirrors crawl`) opts in for that one run. It
briefly joins the DHT (opening a UDP port and routing a little DHT traffic for
others, with the optional `bittorrent` extra), looks up the rendezvous key,
asks the default relays for cards, then closes the DHT session. Nothing is
announced or published. DHT addresses are plain `http://`, so they are used
only with `--allow-http`.

### The private directory commands

- `model-preserve mirrors list` shows each known mirror, its addresses, whether
  its card is live, and this client's own score. No network.
- `model-preserve mirrors import PEERS_JSON` adds the cards that verify.
- `model-preserve mirrors export --output FILE` writes live, undropped cards as a
  peers document, without any scores.
- `model-preserve mirrors crawl [--seed URL] [--no-expand] [--look-further]`
  follows peers documents from known mirrors and seeds.

### The app

The app's source and handoff receives use the same path, with the app's own
directory and the shipped seed list. They read only the approved mirrors' peers
documents, try discovered mirrors after the approved ones under the same
HTTP and private-address choices the user made for that receive, and record each
mirror's outcome. Seeds are contacted only as the fallback stage, after the
approved and known mirrors failed. The approved mirrors are still checked against the reviewed
plan; discovered mirrors are never part of it and never change the accepted
keys. An app receive never contacts the DHT or Nostr relays; only the
explicitly enabled community mirror does. Registry receive downloads from the
original upstream files and has no manifest to look up beforehand, so it does
not use mirrors.

## The app's community mirror

When **Help keep models available** is on, the app's mirror:

- publishes a card when it is reachable: either at an address the owner declares
  (`public_url`, for a port forward or a domain) or because the computer has a
  public address. A computer behind a router publishes no card, because nobody
  could reach it;
- serves its peers document, rewritten without a restart;
- every 15 minutes, announces on the DHT rendezvous, looks up other mirrors there,
  crawls known mirrors over public routes only (never private addresses),
  checks the ones holding models it holds, and re-announces its card.

Receiving or backing up never announces anything. Announcing is part of the
explicit mirror role.

## Python API

- `mirror_cards.issue_card`, `verify_card`, `build_peers` and `read_peers` cover
  the card and document formats.
- `mirror_directory.MirrorDirectory` provides `crawl`, `ingest`, `candidates`,
  `record`, `record_many`, `spot_check`, `gossip`, `listing` and
  `export_cards`.
- `mirror_directory.plan_sources` returns the primary stage (the hints plus
  bounded known mirrors) for one manifest, `plan_fallback` the starter-mirror
  stage, and `fetch_with_mirrors` receives through both stages and records
  outcomes. `MirrorDirectory.read_fallbacks` returns the private record of
  receives that fell back. `permitted` applies the receiver's policy to one address,
  `load_seeds` reads the seed lists, and `look_further` is the explicit DHT and
  relay lookup.
- `mirror_directory.fetch_discovered(manifest_id, trust, destination, directory,
  policy)` fetches only from discovered mirrors, probing each for the
  authenticated manifest before weights move.
- `mirror_rendezvous.Rendezvous` provides `announce` and `lookup` on the
  BitTorrent DHT, and `nostr.publish_card` / `nostr.find_cards` do the same
  through relays.

## Limits

Decentralization removes the single point of failure. It does not hide anyone:
mirrors see the addresses of clients that connect to them, and a mirror's card
publishes its own address. Keeping seeds as a fallback limits how many
receivers a seed operator sees; it does not hide a receiver that falls back. Sovereign makes no anonymity or
censorship-resistance claim.
