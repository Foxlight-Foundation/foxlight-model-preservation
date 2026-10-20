# Foxlight model preservation

Keep an authentic model available from storage you control. Package complete model
files into a signed bundle, download from interchangeable mirrors, and restore
without a model hub, Foxlight account, GPU or Skulk cluster.

The Apache-2.0 reference core provides Ed25519-signed bundles, a standalone
verifier/downloader, signed portable catalogs, and opt-in read-only mirrors.
Model bytes are addressed by SHA-256. Locations can change without changing the
model's identity or granting a mirror the power to replace it.

## Install

Python 3.11+ on Linux or macOS, with [uv](https://docs.astral.sh/uv/):

```sh
git clone https://github.com/Foxlight-Foundation/foxlight-model-preservation.git
cd foxlight-model-preservation
uv tool install .
model-preserve --help
```

`python -m pip install .` is also supported. No package has been published to PyPI.
For offline recovery, retain the verifier and its platform-compatible dependency
wheels through an authenticated software channel.

## Learn the system

| I want to… | Guide |
| --- | --- |
| Try a complete example without downloading a model | [Synthetic walkthrough](docs/quickstart.md) |
| Understand identity, signatures, files and mirrors | [How it works](docs/how-it-works.md) |
| Embed the current library in an application | [Python API and runtime boundary](docs/python-api.md) |
| Plan, observe, cancel or resume bounded worker operations | [Worker controls](docs/jobs.md) |
| Keep a store healthy and recover from failures | [Operations](docs/operations.md) |
| Host a copy for others | [Mirror guide](docs/mirrors.md) |
| Create a public-only publication or opt in to BitTorrent seeding | [Public sharing](docs/public-sharing.md) |
| Retain independent trust and offline recovery material | [Trust and recovery](docs/trust-and-recovery.md) |
| Publish an open model card or retain registry evidence | [Public cards and offline provenance](docs/cards-and-provenance.md) |

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
changes the cap). Fetching never starts sharing or advertises possession.

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
P2P discovery, Skulk installation/UI integration, a public hosted weight archive,
and censorship/privacy qualification are not included. Direct HTTPS mirrors see
the client's IP and requested files. This release makes no anonymity or
censorship-resistance claim.

Read the [protocol](docs/protocol.md), [trust and recovery guide](docs/trust-and-recovery.md),
[security reporting process](SECURITY.md), and [contribution guide](CONTRIBUTING.md).
The [public conformance vector](examples/conformance/v1.json) contains synthetic
files and public verification material; its private signing key was discarded.

## Skulk Library capability

The separate [Library capability source](capability/README.md) connects Guided
setup, Library and Activity to Skulk Core's durable preservation jobs. Its first
host adapter supports private Preserve and Check on the model-store-owning host.
It remains a capability preview with SDK distribution prerequisites; public
hosting controls and live-store recovery are still being connected. The portable
core and its public mirror tools remain independent of this capability.

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
