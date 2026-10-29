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
its peers document, and keeps only cards that verify. The DHT carries addresses
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
together with its own record of which ones delivered correct bytes. The
directory is filled from any bootstrap, and none is required:

- mirrors named in a handoff, invitation or source the user already approved
  (Sovereign reads only those mirrors' peers documents when it downloads);
- peers documents reached outward from known mirrors;
- the BitTorrent DHT rendezvous and Nostr relays (above);
- optional seed lists, with Foxlight's as one among several (deferred).

To choose mirrors, a client takes those whose cards claim the model and samples
them at random within score bands. It confirms each one serves the
authenticated manifest before any weights move, and lets the ones that do share
the download. A mirror that fails is scored down and dropped after three
failures more than successes. A flood of fake cards can waste bounded attempts,
but it cannot corrupt anything.

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
  `record`, `spot_check` and `gossip`.
- `mirror_directory.fetch_discovered(manifest_id, trust, destination, directory,
  policy)` fetches an exact model from whichever discovered mirrors deliver it.
- `mirror_rendezvous.Rendezvous` provides `announce` and `lookup` on the
  BitTorrent DHT, and `nostr.publish_card` / `nostr.find_cards` do the same
  through relays.

## Limits

Decentralization removes the single point of failure. It does not hide anyone:
mirrors see the addresses of clients that connect to them, and a mirror's card
publishes its own address. Sovereign makes no anonymity or
censorship-resistance claim.
