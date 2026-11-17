# Headless mirror deployment

Recipes for running a Sovereign mirror on a VPS, NAS or single-board computer.
See [persistent mirrors](../../docs/persistent-mirrors.md) for what a mirror
does, what it exposes and how consent works. Serving is public and not
anonymous.

| File | Purpose |
| --- | --- |
| `Dockerfile` | Multi-arch image (linux/amd64, linux/arm64) built from this checkout's wheel with hash-locked dependencies and the `bittorrent` extra. Runs as an unprivileged user. Published for each core release as `ghcr.io/foxlight-foundation/sovereign-mirror:VERSION`. |
| `compose.yaml` | Caddy (automatic TLS, no access log) in front of the mirror, which listens on 127.0.0.1 inside Caddy's network namespace. |
| `Caddyfile` | The reverse proxy for `SOVEREIGN_MIRROR_DOMAIN`. |
| `entrypoint.sh` | Runs `mirror quickstart`: plan-only without consent, otherwise set up, renew the grant and serve. |
| `mirror.env.example` | The settings to copy into `mirror.env`. |
| `sovereign-mirror@.service` | Optional hardened systemd system unit, run as the unprivileged `sovereign` account. |
| `install-system-service.sh` | Optional root setup script for the system unit: authenticate, install, quickstart, enable. |
| `model-preserve-mirror@.service` | systemd user unit for a non-container install from the release archive. |

## Choose an option

| Option | Suits |
| --- | --- |
| [Container](#container) | A VPS or any host with Docker. The simplest route. |
| [System service](#systemd-system-service-optional) (optional) | Always-on Linux servers and NAS boxes without Docker. |
| [User service](#systemd-user-unit) | A desktop or laptop you use yourself. |

The container keeps the mirror in its own namespaces with only its volume and
the read-only collection. The system service runs it as a dedicated account
that cannot see `/home` or `/root` and can write only its state directory. The
user service runs it as your login account, so a bug in this internet-facing
process could reach your home directory; on always-on machines the system
service or the container keeps it away from your own files. On Synology, QNAP
and TrueNAS the container route is usually the practical one. See
[what each option isolates](../../docs/persistent-mirrors.md#choose-how-to-run-it).

## Container

You need a domain whose DNS points at the host, ports 80 and 443 open, and a
reviewed collection file (`sovereign.mirror-collection.v1`).

Each core release tag publishes the image to the GitHub Container Registry with
a signed build provenance attestation (Sigstore, keyless). Verify it before the
first start; the attestation names the exact workflow and release tag that built
it:

```sh
docker pull ghcr.io/foxlight-foundation/sovereign-mirror:0.37.1
gh attestation verify oci://ghcr.io/foxlight-foundation/sovereign-mirror:0.37.1 \
  --repo Foxlight-Foundation/foxlight-model-preservation \
  --signer-workflow Foxlight-Foundation/foxlight-model-preservation/.github/workflows/mirror-image.yml \
  --source-ref refs/tags/v0.37.1 --deny-self-hosted-runners
```

To pin exactly what you verified, set
`SOVEREIGN_MIRROR_IMAGE=ghcr.io/foxlight-foundation/sovereign-mirror@sha256:DIGEST`
in `mirror.env` (the digest is printed by `docker pull`).

```sh
cp deploy/mirror/mirror.env.example deploy/mirror/mirror.env      # edit
mkdir deploy/mirror/collection
cp collection.json example.foxsource deploy/mirror/collection/
docker compose --env-file deploy/mirror/mirror.env \
  -f deploy/mirror/compose.yaml up -d
docker compose --env-file deploy/mirror/mirror.env -f deploy/mirror/compose.yaml logs mirror
```

The first start prints the review and serves nothing. When you agree with it,
set `SOVEREIGN_MIRROR_CONSENT=yes-i-consent-to-public-serving` in `mirror.env`
and run the same `up` command again. Each start re-applies the collection and
settings and renews the restart grant (`SOVEREIGN_MIRROR_GRANT`, default 180d).
Remove the consent value and restart to stop serving; retained models stay in
the `mirror-data` volume. A catalog that stops listing a model does not stop
this mirror serving it: that is an advisory in `mirror status`, and you decide.
To stop serving one model, for example after a legal notice, run
`docker compose exec mirror model-preserve mirror block --dir /data/mirror --manifest ID`
(see [withdrawal and takedown](../../docs/persistent-mirrors.md#catalog-withdrawal-is-advisory-takedown-is-yours)). If setup is refused (for example the mirror was
paused), the reason is logged and the container idles; run
`docker compose exec mirror model-preserve mirror resume --dir /data/mirror`
or fix the collection, then restart.

**Set an upload cap if your hosting bills for traffic.** There is none by
default: with `SOVEREIGN_MIRROR_EGRESS_MONTHLY` and
`SOVEREIGN_MIRROR_EGRESS_DAILY` empty, a new mirror uploads as much as people
ask for, over the web and BitTorrent, and that can cost a lot of money. They
are the first settings in `mirror.env.example`; set `1T` (or whatever you can
afford) before you consent. While there is no monthly cap, the review printed
at every start ends with a boxed warning, the mirror logs a
`mirror_upload_notice` each time its uploads pass another TiB in a month, and
`docker compose exec mirror model-preserve mirror status --dir /data/mirror`
shows this month's uploads. An existing mirror keeps the caps it already has;
set a value to `none` to remove one. The disk budget
(`SOVEREIGN_MIRROR_BUDGET`) and the daily download budget
(`SOVEREIGN_MIRROR_INGRESS_DAILY`) always stay bounded.

The mirror announces and shares by default; the review says so before you
consent, and each part has an off switch in `mirror.env`:

- **Nostr** (`SOVEREIGN_MIRROR_NOSTR=off` to stop): it posts its signed card to
  public Nostr relays so Sovereign apps can find it. Relays see the server's
  address, which the card publishes anyway. `SOVEREIGN_MIRROR_NOSTR_RELAYS`
  (comma-separated `wss://` URLs) replaces the default five relays.
- **BitTorrent** (`SOVEREIGN_MIRROR_BITTORRENT=off`): it shares its files in
  Sovereign's own per-file swarms, with its listing on the BitTorrent DHT under
  the mirror's key, so anyone can look up which models it shares.
- **Pirate Face** (`SOVEREIGN_MIRROR_PIRATE_FACE=off`): it looks each held
  model up on pirateface.co and shares a matching torrent from the stored
  files after checking every piece.

BitTorrent peers see the server's address. `compose.yaml` publishes the
sharing port (`SOVEREIGN_MIRROR_TORRENT_PORT`, 6881 TCP and UDP by default);
allow it in the host's firewall. Pirate Face torrents use ports the system
picks, so behind Docker they connect outward only.

The collection directory must hold `collection.json` and any source file it
names with `"file"`; alternatively embed the source with `"inline"`.

To build this checkout instead of pulling, add `--build` to the `up` command.
Set the build argument `EXTRAS=""` to omit libtorrent.

## systemd system service (optional)

For always-on Linux hosts without Docker. Nothing installs or enables it unless
you run the setup script (or the steps in the unit's header). As root, with an
authenticated release archive as in [installation](../../docs/installation.md):

```sh
sudo deploy/mirror/install-system-service.sh \
  --archive foxlight-model-preservation-0.37.1-install.zip \
  --bundle release.sigstore.json \
  --collection collection.json --instance example \
  -- --public-url https://mirror.example.org
```

It prints each step. Before changing anything it verifies the archive
(`gh attestation verify`, or `--sha256` with an independently obtained hash)
and refuses an unverified one, prints the `mirror quickstart` review and asks
you to type `serve`. Only then does it create the `sovereign` system account,
install the release into `/opt/sovereign/<version>`, set up the mirror as
`sovereign`, record the collection in `/etc/sovereign/mirrors/<instance>`,
point `/opt/sovereign/current` at the release, install the unit and enable
`sovereign-mirror@<instance>`. With `-- --plan-only` it stops after the review
and leaves the host, including a running service, untouched. State lives in
`/var/lib/sovereign/mirrors/<instance>` (mode 0700). Running it again is safe;
it is also how to upgrade or change settings. `--help` lists the options.

The mirror listens on `127.0.0.1:8080`. Install the distro's Caddy package and
use this directory's `Caddyfile` as `/etc/caddy/Caddyfile` (or merge its site
block), then set the domain for Caddy's service and restart it:

```sh
sudo install -m0644 deploy/mirror/Caddyfile /etc/caddy/Caddyfile
sudo systemctl edit caddy     # [Service] Environment=SOVEREIGN_MIRROR_DOMAIN=mirror.example.org
sudo systemctl restart caddy
```

## systemd user unit

For a desktop. It runs as your login account (see above).

Install the authenticated release as in [installation](../../docs/installation.md),
then link it and set up the mirror:

```sh
ln -sfn "$HOME/sovereign-0.37.1" "$HOME/sovereign"
mkdir -m 0700 "$HOME/sovereign-mirrors"
~/sovereign/bin/model-preserve mirror quickstart \
  --dir ~/sovereign-mirrors/example --collection collection.json \
  --public-url https://mirror.example.org
install -Dm0644 deploy/mirror/model-preserve-mirror@.service \
  ~/.config/systemd/user/model-preserve-mirror@.service
loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable --now model-preserve-mirror@example
```

The unit runs `mirror run --resume` only, so it refuses to serve without a
current grant. Put your own TLS reverse proxy in front of `127.0.0.1:8080`.
