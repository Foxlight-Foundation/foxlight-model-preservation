# Headless mirror deployment

Recipes for running a Sovereign mirror on a VPS, NAS or single-board computer.
See [persistent mirrors](../../docs/persistent-mirrors.md) for what a mirror
does, what it exposes and how consent works. Serving is public and not
anonymous.

| File | Purpose |
| --- | --- |
| `Dockerfile` | Multi-arch image (linux/amd64, linux/arm64) built from this checkout's wheel with hash-locked dependencies and the `bittorrent` extra. Runs as an unprivileged user. |
| `compose.yaml` | Caddy (automatic TLS, no access log) in front of the mirror, which listens on 127.0.0.1 inside Caddy's network namespace. |
| `Caddyfile` | The reverse proxy for `SOVEREIGN_MIRROR_DOMAIN`. |
| `entrypoint.sh` | Runs `mirror quickstart`: plan-only without consent, otherwise set up, renew the grant and serve. |
| `mirror.env.example` | The settings to copy into `mirror.env`. |
| `model-preserve-mirror@.service` | systemd user unit for a non-container install from the release archive. |

## Container

You need a domain whose DNS points at the host, ports 80 and 443 open, and a
reviewed collection file (`sovereign.mirror-collection.v1`).

```sh
cp deploy/mirror/mirror.env.example deploy/mirror/mirror.env      # edit
mkdir deploy/mirror/collection
cp collection.json example.foxsource deploy/mirror/collection/
docker compose --env-file deploy/mirror/mirror.env \
  -f deploy/mirror/compose.yaml up -d --build
docker compose --env-file deploy/mirror/mirror.env -f deploy/mirror/compose.yaml logs mirror
```

The first start prints the review and serves nothing. When you agree with it,
set `SOVEREIGN_MIRROR_CONSENT=yes-i-consent-to-public-serving` in `mirror.env`
and run the same `up` command again. Each start re-applies the collection and
settings and renews the restart grant (`SOVEREIGN_MIRROR_GRANT`, default 90d).
Remove the consent value and restart to stop serving; retained models stay in
the `mirror-data` volume.

The collection directory must hold `collection.json` and any source file it
names with `"file"`; alternatively embed the source with `"inline"`.

Set the build argument `EXTRAS=""` to omit libtorrent. The image is not yet
published or signed; build it locally.

## systemd user unit

Install the authenticated release as in [installation](../../docs/installation.md),
then link it and set up the mirror:

```sh
ln -sfn "$HOME/sovereign-0.27.0" "$HOME/sovereign"
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
