# Host a server mirror on Hetzner

For heavy traffic: a server that runs the Sovereign mirror container behind
Caddy, at a flat monthly price. Unlike a bucket, a server mirror follows a
signed source and keeps itself current without the desktop app. It follows
[persistent mirrors](../persistent-mirrors.md) and the
[container recipe](../../deploy/mirror/README.md); this page adds the Hetzner
parts.

Checked against Hetzner's and Docker's documentation on **2026-11-07**.
Prices change; check Hetzner's [cloud](https://www.hetzner.com/cloud/) and
[dedicated](https://www.hetzner.com/dedicated-rootserver/) pages before you
budget.

## Before you start

- A Hetzner account and an SSH key on your computer.
- A domain whose DNS you control, for example `mirror.example.org`.
- A reviewed collection file (`sovereign.mirror-collection.v1`) and the source
  file it names, from a source publisher you trust
  ([quickstart](../persistent-mirrors.md#quickstart-one-reviewed-collection-file)).
- The GitHub CLI (`gh`) on your own computer, to verify the image.

## Steps

1. **Pick a server.**
   - **Small (up to about 20 TB a month):** a cloud server in an EU location
     (`FSN1`, `NBG1` or `HEL1`). EU cloud plans include 20 TB of traffic a
     month; the same plans in the US and Singapore include far less. A
     Cost-Optimized **CX23** (2 vCPU, 4 GB, €5.99) or Arm **CAX11** (€6.49) is
     enough; the image runs on amd64 and arm64. If those show as unavailable,
     a Regular Performance **CPX22** (€19.99) works too. Disks are 40–160 GB;
     add a Volume (step 3) if your collection is larger.
   - **Large (tens of TB a month):** a dedicated server ordered in Robot, such
     as an **AX42** (from €79, €49 setup) or a storage-heavy **SX65** (from €84,
     €39 setup). Hetzner says root servers have a 1 Gbit uplink "and with it
     unlimited traffic". It is delivered in the rescue system; install
     Ubuntu 24.04 with
     [installimage](https://docs.hetzner.com/robot/dedicated-server/operating-systems/installimage/),
     then continue at step 4.
2. **Create a firewall (cloud).** In the Console, open **Firewalls → Create
   Firewall** and add these inbound rules:

   | Protocol | Port | Source |
   | --- | --- | --- |
   | TCP | 22 | your own IP address, if it is fixed; otherwise any |
   | TCP | 80 | any IPv4 and IPv6 |
   | TCP | 443 | any IPv4 and IPv6 |
   | UDP | 443 | any IPv4 and IPv6 (HTTP/3) |

   Inbound traffic without a rule is dropped. Leave outbound empty (all
   allowed). Port 80 is needed for the TLS certificate and redirects.

   On a **dedicated** server use the Robot firewall (**Server → Firewall**)
   instead. It is stateless: also allow incoming TCP 32768–65535 with the
   `ack` flag, or replies to the server's own connections are dropped.

   Use the Hetzner firewall rather than `ufw` on the server: Docker's
   published ports bypass `ufw` and `firewalld` rules.
3. **Create the cloud server.** In the Console, **Servers → Add Server**:
   location as in step 1, image **Ubuntu 24.04**, the plan, public IPv4 and
   IPv6, your **SSH key**, and the firewall from step 2. If you need more
   disk, add a **Volume** with automatic mounting; note its mount path,
   `/mnt/HC_Volume_NUMBER`. Choose **Create & Buy now** and copy the server's
   IPv4 and IPv6 addresses.
4. **Point DNS at it.** At your DNS provider, add an `A` record for
   `mirror.example.org` with the IPv4 address and an `AAAA` record with the
   IPv6 address. Check from your computer:

   ```sh
   dig +short A mirror.example.org
   dig +short AAAA mirror.example.org
   ```

   Do not put a CDN proxy in front of the mirror. Caddy needs the name to
   reach this server directly to get its certificate.
5. **Install Docker.** Sign in with `ssh root@mirror.example.org` (or your
   own user). If you added a Volume, keep Docker's data on it first:

   ```sh
   sudo mkdir -p /etc/docker
   echo '{"data-root": "/mnt/HC_Volume_NUMBER/docker"}' | sudo tee /etc/docker/daemon.json
   ```

   Then install Docker Engine and the Compose plugin from Docker's own apt
   repository, exactly as in
   [Docker's Ubuntu guide](https://docs.docker.com/engine/install/ubuntu/):

   ```sh
   sudo apt update
   sudo apt install ca-certificates curl git
   sudo install -m 0755 -d /etc/apt/keyrings
   sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
   sudo chmod a+r /etc/apt/keyrings/docker.asc
   sudo tee /etc/apt/sources.list.d/docker.sources <<EOF
   Types: deb
   URIs: https://download.docker.com/linux/ubuntu
   Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
   Components: stable
   Architectures: $(dpkg --print-architecture)
   Signed-By: /etc/apt/keyrings/docker.asc
   EOF
   sudo apt update
   sudo apt install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
   sudo docker run hello-world
   ```
6. **Verify the image on your own computer.** The image's signed build
   provenance ties it to the public release workflow:

   ```sh
   gh attestation verify oci://ghcr.io/foxlight-foundation/sovereign-mirror:0.28.1 \
     --repo Foxlight-Foundation/foxlight-model-preservation \
     --signer-workflow Foxlight-Foundation/foxlight-model-preservation/.github/workflows/mirror-image.yml \
     --source-ref refs/tags/v0.28.1 --deny-self-hosted-runners
   ```

   The 0.28.1 image index digest is
   `sha256:49c30848a5c9459e28b6b47942a6d5339bed7318b8ecd5fd9868826e4b48b84b`.
   The server will run exactly that digest (step 7), so it does not matter
   that `gh` runs on your computer and not on the server.
7. **Get the recipe and settings on the server.**

   ```sh
   git clone --branch v0.28.1 --depth 1 \
     https://github.com/Foxlight-Foundation/foxlight-model-preservation.git
   cd foxlight-model-preservation
   cp deploy/mirror/mirror.env.example deploy/mirror/mirror.env
   mkdir deploy/mirror/collection
   ```

   Copy your reviewed `collection.json` and its source file into
   `deploy/mirror/collection/` (for example with `scp` from your computer).
   Then edit `deploy/mirror/mirror.env`:

   ```sh
   SOVEREIGN_MIRROR_DOMAIN=mirror.example.org
   SOVEREIGN_MIRROR_CONTACT=mirror-abuse@example.org
   SOVEREIGN_MIRROR_BUDGET=100G
   SOVEREIGN_MIRROR_IMAGE=ghcr.io/foxlight-foundation/sovereign-mirror@sha256:49c30848a5c9459e28b6b47942a6d5339bed7318b8ecd5fd9868826e4b48b84b
   ```

   Set the disk budget below the free space. The default upload limits are
   50G a day and 1T a month. On a cloud server, keep
   `SOVEREIGN_MIRROR_EGRESS_MONTHLY` under the included 20 TB (for example
   `18T`, with `SOVEREIGN_MIRROR_EGRESS_DAILY=600G`) unless you accept
   €1 per extra TB. On a dedicated server, raise them to what you want to
   serve.
8. **Start it and read the review.** The first start serves nothing; it
   prints what the mirror would follow and serve.

   ```sh
   sudo docker compose --env-file deploy/mirror/mirror.env \
     -f deploy/mirror/compose.yaml up -d
   sudo docker compose --env-file deploy/mirror/mirror.env \
     -f deploy/mirror/compose.yaml logs mirror
   ```
9. **Consent.** If you agree with the review, set
   `SOVEREIGN_MIRROR_CONSENT=yes-i-consent-to-public-serving` in
   `deploy/mirror/mirror.env` and run the same `up -d` command again. Each
   start renews the standing restart grant (180 days by default).
10. **Check that it works.** On the server:

    ```sh
    sudo docker compose --env-file deploy/mirror/mirror.env \
      -f deploy/mirror/compose.yaml exec mirror \
      model-preserve mirror status --dir /data/mirror
    ```

    From another computer, once the status shows a retained model:

    ```sh
    curl -fsS -o /dev/null -w '%{http_code}\n' https://mirror.example.org/.well-known/sovereign/peers.json
    curl -fsS -o /dev/null -w '%{http_code}\n' https://mirror.example.org/manifests/MANIFEST_SHA256.json
    model-preserve fetch MANIFEST_SHA256 --trust publisher-trust.json \
      --mirror https://mirror.example.org --store ./mirror-check
    ```

    Both `curl` lines should print `200`. Use a manifest ID from the status
    output and the trust snapshot from your collection's reviewed `trust`.

The container restarts with the server. Without containers, the standalone
tools can also run the mirror as a system service behind your own TLS proxy;
see [persistent mirrors](../persistent-mirrors.md#run-as-a-service).

## What this costs

Hetzner, 2026-11-07, excluding VAT:

| Option | Monthly | Setup | Traffic |
| --- | --- | --- | --- |
| Cloud CX23 (EU) | €5.99 | none | 20 TB included, then €1 per TB |
| Cloud CAX11 (EU, Arm) | €6.49 | none | 20 TB included, then €1 per TB |
| Cloud CPX22 (EU) | €19.99 | none | 20 TB included, then €1 per TB |
| Dedicated AX42 | from €79 | €49 | unlimited on the 1 Gbit uplink |
| Dedicated SX65 | from €84 | €39 | unlimited on the 1 Gbit uplink |

- Cloud servers bill hourly up to the monthly price, and keep billing while
  powered off. Volumes and an unattached primary IP bill separately.
- Traffic overage is billed in 100 MB steps. Hetzner's pricing pages render
  the overage unit oddly ("per hour"); it is per TB. Confirm in the Console.
- 1 Gbit/s is about 320 TB a month at full speed. 50 TB a month averages
  about 155 Mbit/s.
- Cloud plans can show as "currently unavailable" in some locations. Dedicated
  prices vary with the configuration.

## What this exposes

- The server's **IP address and domain are public**. Anyone can download what
  it serves and see which models it holds. It is not anonymous.
- The mirror's signed card names the domain and your optional contact, and
  discovery (DHT, Nostr) can publish them further. The sources and peers it
  contacts see its address.
- Caddy writes no access log by default; keep it that way. Hetzner still sees
  traffic volumes and knows the account holder.
- Hetzner acts quickly on abuse reports. Serve only models whose licences
  allow redistribution, and publish a contact.

## How to stop

1. **Stop serving:** remove the `SOVEREIGN_MIRROR_CONSENT` value from
   `mirror.env` and run the `up -d` command again. The mirror starts
   plan-only and serves nothing; retained models stay in the `mirror-data`
   volume.
2. **Stop the containers:** `sudo docker compose --env-file
   deploy/mirror/mirror.env -f deploy/mirror/compose.yaml down`. Add `-v` only
   if you also want to delete the retained models.
3. **Remove the DNS records** for `mirror.example.org`.
4. **Stop paying:** delete a cloud server in the Console (**Server → Delete**),
   and delete any Volume and unattached primary IP with it. Cancel a dedicated
   server in Robot (**Server → Cancellation**); its data is lost.

## Sources (checked 2026-11-07)

- [Hetzner cloud plans](https://www.hetzner.com/cloud/): [Cost-Optimized](https://www.hetzner.com/cloud/cost-optimized/) and [Regular Performance](https://www.hetzner.com/cloud/regular-performance/)
- [Dedicated AX](https://www.hetzner.com/dedicated-rootserver/matrix-ax/) and [SX](https://www.hetzner.com/dedicated-rootserver/matrix-sx/) servers
- [Root server traffic](https://docs.hetzner.com/robot/general/traffic/)
- [Cloud billing FAQ](https://docs.hetzner.com/cloud/billing/faq/)
- [Creating a Cloud Firewall](https://docs.hetzner.com/cloud/firewalls/getting-started/creating-a-firewall/) and [Firewall FAQ](https://docs.hetzner.com/cloud/firewalls/faq/)
- [Robot firewall](https://docs.hetzner.com/robot/dedicated-server/firewall/)
- [Creating a cloud server](https://docs.hetzner.com/cloud/servers/getting-started/creating-a-server/)
- [Primary IPs](https://docs.hetzner.com/cloud/servers/primary-ips/overview/)
- [Cancellations in Robot](https://docs.hetzner.com/general/billing-and-account-management/cancellation/cancellations-robot/)
- [Install Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
