# Where to host a public mirror

Step-by-step setup guides for specific providers. Each lists what you need,
exact values, what it costs, what it exposes and how to stop.

| Guide | Shape | Best for |
| --- | --- | --- |
| [Cloudflare R2](r2.md) | [bucket mirror](../bucket-mirrors.md) | The main mirror: downloads are free. |
| [Hetzner Object Storage](hetzner-object-storage.md) | bucket mirror | A second mirror at a different company, in the EU. |
| [AWS S3 + CloudFront](aws-s3-cloudfront.md) | bucket mirror | AWS credits, small traffic, or a CloudFront flat-rate plan. |
| [Hetzner server](hetzner-server.md) | [server mirror](../persistent-mirrors.md) | Heavy traffic at a flat price, with no desktop app running. |

A bucket mirror is kept current by the Sovereign desktop app (**Help keep
models available → Cloud storage**) while it runs. A server mirror follows a
signed source on its own.

## Recommended setup

Run **two mirrors at two different companies**, so one provider's decision
cannot take every copy offline: R2 as the main mirror, plus Hetzner Object
Storage (or a Hetzner server at high traffic) as the second. Keep a private
copy of what you share elsewhere, such as a backup drive, so either mirror can
be refilled.

## Monthly cost at three sizes

Prices checked **2026-11-07**, excluding VAT and sales tax. Prices change;
check the provider before you budget.

| Provider | 100 GB stored, 1 TB downloaded | 500 GB, 10 TB | 2 TB, 50 TB |
| --- | --- | --- | --- |
| Cloudflare R2 | $1.35 | $7.35 | $30 |
| Hetzner Object Storage | €6.49 | €15.49 | €62 |
| AWS S3 + CloudFront, pay-as-you-go | $2.30 | about $795 | over $3,000 |
| AWS S3 + CloudFront, Pro flat-rate plan | $16 | $25 | $61 |
| Hetzner cloud server (EU, 20 TB included) | €5.99–19.99 | €5.99–19.99 | about €36–50 (30 TB over) |
| Hetzner dedicated server | from €79 | from €79 | from €79–84, plus setup |

Each figure assumes that one mirror serves all the downloads. With two mirrors
the load splits, which only lowers the per-TB bills. The AWS Pro plan has no
overage charges, but AWS can slow delivery if you keep exceeding its 50 TB
allowance. A cloud server's disk also limits what it can store.

## What every mirror has in common

- **Public, not anonymous.** Anyone can download what a mirror holds and see
  which models those are. The provider knows the account holder and sees
  every request. The mirror's address is published in its signed card and can
  be announced on the DHT and Nostr.
- **No trust in the host.** Recipients verify every byte against keys they
  chose, so the host name (yours or the provider's) never makes a model
  trusted.
- **Keys stay in the keychain.** Paste access keys only into the app's
  **Cloud storage** fields. Never put them on a command line, in a file or in
  a message.
- **Licences.** Serve only models whose licences allow redistribution.
  Providers can act on abuse reports and suspend accounts.
