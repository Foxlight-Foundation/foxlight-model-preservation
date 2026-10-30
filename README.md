# Foxlight model preservation

Keep an authentic model available from storage you control. Package complete model
files into a signed bundle, download from interchangeable mirrors, and restore
without a model hub, Foxlight account, GPU or Skulk cluster.

The Apache-2.0 reference core provides Ed25519-signed bundles, a standalone
verifier/downloader, signed portable catalogs, and opt-in read-only mirrors.
Model bytes are addressed by SHA-256. Locations can change without changing the
model's identity or granting a mirror the power to replace it.

A separately chosen [persistent mirror](docs/persistent-mirrors.md) can follow
authenticated sources or retain an exact public copy you already have, then serve
verified models under explicit model/terms policy. Choose HTTP independently of
[persistent IPFS/BitTorrent sessions](docs/persistent-peers.md). Restart consent
is separate, budgets are durable and deletion stays with the operator. Skulk is
optional.

## Use Sovereign

The Sovereign app helps you find a model, keep your own complete copy and recover
its files, companions and signed provenance if the original host disappears.
No Skulk, hosted account, GPU or manual Python setup is required by the native app.

New here? Follow [your first model with Sovereign](docs/getting-started.md):
install, find, preserve, send a kit and recover on another computer.

- **Mac (Apple silicon):** [download Sovereign 0.1.0 build 8](https://github.com/Foxlight-Foundation/foxlight-model-preservation/releases/tag/sovereign-macos-v0.1.0-build8),
  open the DMG and drag **Sovereign** to **Applications**.
  [Verification and installation](docs/macos-distribution.md).
- **Linux / APT (amd64 or arm64):** [graphical setup](docs/linux-distribution.md#graphical-setup-recommended) detects your processor and configures signed app updates. Guided terminal setup, manual apt and verified Debian packages are also available.
  Then open **Sovereign** from your applications menu.

Choose **Browse public models** to find something useful, or **Open recovery kit**
to recover a copy you already have. **Library** keeps your saved copies searchable.
**Backups** remembers storage folders; **Sharing** offers explicit, finite public
sharing and a one-file recipient handoff. Opening or installing the app starts no
model download, inference or sharing. A model's trust checks remain separate from
software signing, and an unfamiliar capture key still needs explicit review.

The [independent Library](docs/sovereign-desktop.md) also supports signed catalog
browsing, independent source following and offline search. The public Library
0.23 installation archive includes both independent public packages.
For the independent interface, authenticate and extract the
[Library 0.23 archive](https://github.com/Foxlight-Foundation/foxlight-model-preservation/releases/tag/library-v0.23.0),
then select its desktop profile:

```sh
python3 install.py --directory "$HOME/sovereign-desktop" \
  --requirements requirements-desktop.lock
"$HOME/sovereign-desktop/bin/foxlight-sovereign"
```

This Python installation path needs Python 3.13+ and uv. Follow the
[verification and offline installation guide](docs/installation.md) before
executing the helper. The 0.23 desktop profile offers first-launch choices for **Browse public models**,
**Choose a recovery kit** and **Choose a sharing file**, with reusable **Getting started** help.
**Share** separates a kit to send from public-mirror review; **Recovery instructions**
provides the saved copy’s public verification identifiers for separate comparison. No Skulk or managed SDK is required.

## Current public previews

- **Standalone 0.27.0** includes persistent HTTP/IPFS/BitTorrent mirrors,
  offline local-copy intake, portable handoffs, signed source following,
  retained-copy inspection/recovery-kit export, offline completion recovery,
  PirateFace lookup/intake and
  the separately opted-in I2P pilots.
- **Standalone 0.27.0 / Library 0.23.0** add the community mirror network:
  PMF archives travel natively, public copies share one identity per model and
  terms, the desktop app's **Help keep models available** mirror shares every
  eligible backup under one standing consent, and mirrors find each other
  through signed cards, gossip, the BitTorrent DHT and Nostr relays.
- **Library 0.22.0** added independent source review/update/search and separate
  capture-key acceptance to Find/Library/Activity through
  its `requirements-desktop.lock` installation profile. The default profile keeps
  the local interface through its separately pinned compatible Skulk Core. That
  Core-backed mode includes PMF preservation/recovery, one-file sharing,
  peer setup/receiving, persistent mirror controls and explicit I2P
  source/receiving controls. Installing the Library does not set up Skulk Core
  or start model transfers or sharing. Retained recovery-kit preparation, native download
  and explicit earlier-kit cleanup work from Library/Guided. Pending copies
  also offer a durable offline local-byte check before kit preparation, without
  receiving, sharing or new trust.
- Public releases are pre-releases. The v1 preservation format has its existing
  compatibility commitment; PMF remains a separately versioned draft. Persistent
  mirrors serve public v1 bundles and public PMF archives natively.

Ordinary IPFS/BitTorrent participation is not anonymous. The
[I2P HTTP](docs/i2p-http.md) pilot is receive-only at the application level and
uses a separately operated local proxy with no direct fallback. The distinct
[I2PSnark recovery-kit pilot](docs/i2p.md) can upload pieces while receiving and
requires explicit sharing consent. Neither pilot establishes anonymity or
censorship resistance for at-risk users.

## Install

Python 3.11+ on Linux or macOS, with an authenticated Python/uv toolchain. Use the
[0.27.0 installation archive](https://github.com/Foxlight-Foundation/foxlight-model-preservation/releases/tag/v0.27.0)
and its detached attestation bundle. [Verify it first](docs/installation.md),
extract it, then install into a new directory:

```sh
python3 install.py --directory "$HOME/sovereign-0.27.0"
"$HOME/sovereign-0.27.0/bin/model-preserve" --help
```

The archive contains the exact package wheel, source, hash-locked dependencies
and source revision. Installation starts no service and grants no model trust.
[Offline installation](docs/installation.md#retain-a-wheelhouse-and-install-offline)
uses retained dependency wheels with no index/cache fallback. No package has
been published to PyPI. Source development installation remains available:

```sh
git clone https://github.com/Foxlight-Foundation/foxlight-model-preservation.git
cd foxlight-model-preservation
uv sync --locked --no-dev
uv run model-preserve --help
```

## Verify a release

[Releases](https://github.com/Foxlight-Foundation/foxlight-model-preservation/releases)
after `v0.6.0` and `library-v0.6.0` are built from their tag and attested by the
[release workflow](.github/workflows/release.yml); earlier ones have checksums only.
With the files and `SHA256SUMS` in one directory:

```sh
sha256sum -c SHA256SUMS
gh attestation verify FILE --repo Foxlight-Foundation/foxlight-model-preservation \
  --signer-workflow Foxlight-Foundation/foxlight-model-preservation/.github/workflows/release.yml \
  --source-ref refs/tags/TAG
```

A pass binds the file to this repository's release workflow at that tag, not to a
maintainer's offline key. PyPI publishing is not enabled yet. [Verifying a release](docs/releases.md)
covers what each check proves, offline evidence and installing a verified wheel.

## Learn the system

| I want to… | Guide |
| --- | --- |
| Try a complete example without downloading a model | [Synthetic walkthrough](docs/quickstart.md) |
| Understand identity, signatures, files and mirrors | [How it works](docs/how-it-works.md) |
| Embed the current library in an application | [Python API and runtime boundary](docs/python-api.md) |
| Plan, observe, cancel or resume bounded worker operations | [Worker controls](docs/jobs.md) |
| Keep a store healthy and recover from failures | [Operations](docs/operations.md) |
| Host a copy for others | [Mirror guide](docs/mirrors.md) |
| Follow sources, search offline and fetch an exact model | [Standalone discovery](docs/discovery.md) |
| Find source updates and model providers without a central service | [Optional IPFS/IPNS peer discovery](docs/peer-discovery.md) |
| Look up a PirateFace candidate and verify an existing repository download | [Standalone PirateFace interoperability](docs/pirateface.md) |
| Pilot an explicitly opted-in I2P-only recovery-kit transfer | [Standalone I2P pilot and its limits](docs/i2p.md) |
| Set up optional peer support without a system installer | [Pinned native acquisition, including offline setup](docs/peer-discovery.md#install-and-start-a-receiver) |
| Refresh signed sources or receive over I2P HTTP | [I2P HTTP pilot](docs/i2p-http.md) |
| Send or receive a public model with an offline preview | [Public invitations](docs/invitations.md) |
| Share one exact public copy and receive using one file | [Portable handoffs and finite peer sharing](docs/handoffs.md) |
| Create a public-only publication or opt in to BitTorrent seeding | [Public sharing](docs/public-sharing.md) |
| Retain independent trust and offline recovery material | [Trust and recovery](docs/trust-and-recovery.md) |
| Check a downloaded release before installing it | [Verifying a release](docs/releases.md) |
| Publish an open model card or retain registry evidence | [Public cards and offline provenance](docs/cards-and-provenance.md) |
| Use draft PMF or round-trip an existing Skulk/preservation archive | [Portable Model Format](docs/portable-model-format.md) |

## Download, verify, restore

First obtain a trusted public-key profile and exact manifest ID through an
independently authenticated reference. A mirror supplying its own key and checksum
does not establish trust. Replace the example IDs, URLs and paths below:

```sh
model-preserve fetch MANIFEST_SHA256 --trust bundle-trust.json \
  --mirror https://mirror-a.example/models \
  --mirror https://mirror-b.example/models --store ./preserved

model-preserve verify MANIFEST_SHA256 --trust bundle-trust.json --store ./preserved

model-preserve restore MANIFEST_SHA256 --trust bundle-trust.json \
  --store ./preserved --output ./restored-model
```

Fetch checks signatures before requesting model bytes and checks every file
before publishing completion. It resumes interrupted transfers, tries permitted
alternate sources, and caps object transfers at 20 GiB by default (`--max-bytes`
changes the cap). Each invocation stops after one hour by default
(`--deadline-seconds`, up to 24 hours); running the same command again resumes.
Fetching never starts sharing or advertises possession.

A local folder or mounted NAS can replace either mirror URL. Restore writes a new
`members/<member-id>/` layout with the exact selected files; it refuses an existing
output directory. It does not install engines or execute model code.

A signed catalog can carry the exact model list and mirrors:

```sh
model-preserve fetch-catalog CATALOG_SHA256 --index catalog.json \
  --trust catalog-trust.json --bundle-trust bundle-trust.json \
  --entry my-model --store ./preserved
```

## Preserve and contribute a copy

```sh
model-preserve keygen --key owner.pem --trust-out owner-trust.json

model-preserve pack --recipe recipe.json --member weights=/path/to/model \
  --member notices=/path/to/notices --store /path/to/preserved --key owner.pem
```

The key is encrypted using the prompted passphrase and stored with private file
permissions. Keep it separate from mirrors and model backups. Pack prints the
exact manifest ID. A recipe explicitly selects every required file/member,
dependency, original revision and usage/redistribution declaration. It never
exports a repository glob or quietly upgrades provenance.

[Exact-revision seed recipes](examples/recipes) cover small GGUF, MLX, and a
vision model with its required projector. The [seed guide](docs/seed-collection.md)
explains their inputs and limits. Model weights and private keys are not shipped
in this repository. These recipes are examples of owner captures, not registry
publication or inference qualification.

Any ordinary static HTTPS server can host the `manifests/` and `objects/sha256/`
directories. No special store server or Foxlight signing key is needed. The
[mirror guide](docs/mirrors.md) also covers the bounded `seed` command and signed
catalogs. Only start public sharing after checking redistribution rights and
notices for every member.

## What this release establishes

The tests exercise signatures, complete dependency closure, unsafe paths,
corruption, mirror failover, HTTP ranges, TLS certificates, interruption/resume,
resource limits and fresh-directory restoration. The real-artifact recovery drill
covers GGUF, MLX and a required vision projector with central endpoints unavailable.
All drill endpoints are locally controlled; inference is a separate check.

This is an initial public reference implementation. External independent custody,
broader public-network peer qualification, stable/frozen Core distribution, a public hosted weight archive,
and censorship/privacy qualification are not included. Direct HTTPS mirrors see
the client's IP and requested files. This release makes no anonymity or
censorship-resistance claim.

Read the [protocol](docs/protocol.md), [trust and recovery guide](docs/trust-and-recovery.md),
[security reporting process](SECURITY.md), and [contribution guide](CONTRIBUTING.md).
The [public conformance vector](examples/conformance/v1.json) contains synthetic
files and public verification material; its private signing key was discarded.

## Skulk Library

The separate [Library application](capability/README.md) connects Guided setup,
Library and Activity to Skulk Core's durable preservation jobs. It preserves and
checks complete supported model closures, saves portable recovery kits, restores
them through the live store owner, and explicitly clears unused interrupted
restore files before fresh review. The public local launcher installs with public
dependencies and includes its interface. A compatible local Core is required.
The optional managed capability adapter retains its separately supplied SDK.
Library 0.12 includes reviewed local or mounted NAS/remote storage, durable
defaults and a [compatible isolated Core preview](capability/preview.md). Run the
installed launcher with `--check` to inspect the actual owner API before use.
Publishing and receiving retain separately reviewed, bounded HTTP/BitTorrent
sessions with explicit stop and public-key/torrent downloads. One-file sharing,
source following, offline search and native peer setup/receiving use Core-owned
jobs and explicit consent. See [peer setup and receiving](capability/README.md#receive-through-peers)
and [I2P source/receiving controls](capability/README.md#i2p-source-and-receiving-controls).
The I2P HTTP pilot needs a separately operated proxy/router; Library does not
install one or provide an application-wide anonymity mode.

The compatible Core preview builds pinned native source and remains separate
from stable/frozen distribution. Standalone persistent mirrors and peer tools
remain independently usable; they are not Library's unattended hosting mode.
Public reachability and custody remain operator-owned.

### Recover after losing the original host

Save a self-contained kit to storage separate from the original host:

```sh
model-preserve kit-export MANIFEST_SHA256 --store archive --trust owner-trust.json --output model.foxkit
model-preserve kit-inspect --kit model.foxkit
```

The kit includes the complete signed model/companion closure and public capture
keys. Keep its manifest ID and key fingerprints separately when possible. It
contains no private signing key and needs no registry connection to import:

```sh
model-preserve kit-import --kit model.foxkit --expected-manifest-id MANIFEST_SHA256 --trust owner-trust.json --output recovered-archive
model-preserve verify MANIFEST_SHA256 --store recovered-archive --trust recovered-archive/kit-trust.json
```

If the independently retained trust file is also lost, review `kit-inspect` and
use `--accept-kit-key` instead of `--trust` only for a kit you deliberately choose
to trust. Self-signature checks cannot identify its original owner. The imported
store works with the ordinary `restore`, evidence inspection and receiving tools.
Metadata inspection alone does not verify weights. See [the format and trust
contract](docs/protocol.md#self-contained-recovery-kits-v1).

Library's [PMF preservation and recovery](capability/README.md#pmf-source-preview-library-07)
uses PMF automatically for Preserve and Restore with a compatible pinned Core.
Its self-contained PMF kits also work with the independent tools; Skulk is not
required to recover the archived files/cards/provenance. Earlier kits retain
their own identities and verification. PMF preservation/recovery is included in
Library 0.12.0; generic PMF public transport adapters remain separate work.

## License, governance and names

Apache-2.0 ([LICENSE](LICENSE), [NOTICE](NOTICE)), published by FoxlightAI Inc.
Contributions are under Apache-2.0 ([CONTRIBUTING.md](CONTRIBUTING.md)).
How decisions and format changes are made is in [GOVERNANCE.md](GOVERNANCE.md);
the code is free to fork, and the Sovereign and Foxlight names are covered by
[TRADEMARKS.md](TRADEMARKS.md).
