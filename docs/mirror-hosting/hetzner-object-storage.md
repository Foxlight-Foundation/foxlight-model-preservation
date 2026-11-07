# Host a bucket mirror on Hetzner Object Storage

The recommended second mirror: a different company (Hetzner, Germany) in EU
data centers, so one provider's decision does not take every copy offline. It
is still a bucket, so there is no server to run.

Checked against Hetzner's documentation and pricing on **2026-11-07**. Prices
change; check the [Object Storage page](https://www.hetzner.com/storage/object-storage/)
before you budget.

## Before you start

- A Hetzner account. New accounts can be asked for identity verification.
- The Sovereign desktop app with **Help keep models available** and **Cloud
  storage**: Library 0.24 or later (Mac build 10, Linux revision 5).
- At least one backed-up model whose licence allows sharing.
- For the final check: a second computer with the standalone tools.

The examples use the location `fsn1` (Falkenstein) and the bucket
`sovereign-mirror`. Bucket names are lowercase letters, numbers and hyphens;
avoid dots, because the public address puts the name in a TLS host name.

## Steps

1. **Make a project just for the mirror.** In
   [console.hetzner.com](https://console.hetzner.com/), create a project such as
   `sovereign-mirror`. Hetzner S3 credentials are valid for **every bucket in
   their project**, so a project of its own keeps the key from reaching other
   buckets.
2. **Create the bucket.** In that project, open **Object Storage → Create
   Bucket**:
   - **Location:** `FSN1`, `NBG1` or `HEL1`.
   - **Name:** `sovereign-mirror`.
   - **Visibility:** **Public**. Anyone can then read an object by its URL, but
     listing the bucket stays denied. Writing and deleting still need keys.

   Choose **Create & Buy now**. Billing starts now (see costs below).
3. **Generate S3 credentials.** In the same project, open **Security → S3
   Credentials → Generate credentials** and give them a description such as
   `sovereign-mirror app`. Hetzner shows the **Access key** and the **Secret
   key** once. Leave the window open and go straight to the next step; do not
   save the keys in a file, a note or a message. If you lose the secret, delete
   the credentials and generate new ones.
4. **Connect it in Sovereign.** Open **Sharing → Help keep models available →
   Cloud storage → Set up cloud storage**.

   If your app lists **Hetzner Object Storage**, choose it and enter:

   | Field | Value |
   | --- | --- |
   | Region | `fsn1` (your location, in lowercase) |
   | Bucket name | `sovereign-mirror` |
   | Access key ID / Secret access key | paste from step 3 |

   Sovereign fills in the endpoint and public address itself.

   Otherwise choose **Other S3-compatible storage** and enter these exact
   values (replace `fsn1` with `nbg1` or `hel1` if you chose that location):

   | Field | Value |
   | --- | --- |
   | Endpoint | `https://fsn1.your-objectstorage.com` |
   | Region | `fsn1` |
   | Bucket name | `sovereign-mirror` |
   | Public address | `https://sovereign-mirror.fsn1.your-objectstorage.com` |
   | Access key ID / Secret access key | paste from step 3 |

   The keys go only into these two fields. Sovereign stores them in the
   computer's keychain and sends them only to Hetzner.
5. **Choose Check and connect.** Sovereign uploads a small test file, downloads
   it without credentials from the public address and deletes it.
   - **Ready:** the panel shows the bucket and **People download from** its
     address. Close the credentials window.
   - **The upload didn't work:** check the region, the bucket name and the
     keys, and that the keys belong to the bucket's project.
   - **Nobody could download from the public address:** the bucket's
     Visibility is not **Public**, or the public address has a typo.
6. **Let it upload.** With sharing on, Sovereign uploads each shared model,
   objects first and the signed manifest last. Uploads count toward the same
   daily and monthly upload limits as sharing from this computer.
7. **Confirm downloads work from another computer.** Take a manifest ID from
   a mirror that already holds the same model (for example the `manifests/`
   folder of your R2 bucket), or from **Sharing → a public copy →
   Verification details**. On the second computer:

   ```sh
   curl -fsS -o /dev/null -w '%{http_code}\n' \
     https://sovereign-mirror.fsn1.your-objectstorage.com/manifests/MANIFEST_SHA256.json
   model-preserve fetch MANIFEST_SHA256 --trust publisher-trust.json \
     --mirror https://sovereign-mirror.fsn1.your-objectstorage.com \
     --store ./mirror-check
   ```

   `curl` should print `200`. `publisher-trust.json` is the trust snapshot for
   the key that signed the model, enrolled independently as for any fetch.
   Delete `./mirror-check` afterwards.

## No custom domain, and why that's fine

Hetzner Object Storage does not support custom domains: its FAQ says the
feature "is currently not available". Recipients download from
`https://sovereign-mirror.fsn1.your-objectstorage.com`.

That is fine for Sovereign. Trust never comes from the host name: `fetch`
checks every byte against signatures from keys the recipient chose, so a
provider's address is as good as your own domain. The mirror's signed card
carries the address, so recipients do not need to type it. The cost is
portability: if you move the mirror, the address changes, and recipients find
the new one through the updated card. Putting your own domain in front would
need a reverse proxy, which is a server again; see
[Hetzner server](hetzner-server.md) instead.

## What this costs

Hetzner Object Storage, from the [product page](https://www.hetzner.com/storage/object-storage/)
and [overview](https://docs.hetzner.com/storage/object-storage/overview)
(2026-11-07, excluding VAT):

| Item | Price |
| --- | --- |
| Base fee, per account | €6.49 per month (billed hourly, capped monthly) |
| Included | 1 TB storage and 1 TB egress per month |
| Extra storage | €0.0087 per TB-hour (about €6.47 per TB-month) |
| Extra egress | €1.00 per TB (the page's unit wording is garbled; confirm it) |
| Ingress and API requests | free |
| Objects under 64 kB | billed as 64 kB |

| Stored / downloaded per month | Approximate monthly bill |
| --- | --- |
| 100 GB / 1 TB | €6.49 |
| 500 GB / 10 TB | €15.49 |
| 2 TB / 50 TB | €62 |

The base fee runs for every hour at least one bucket exists in the account,
even an empty one. Unlike R2, egress is billed: a scraper pulling 100 TB would
add about €100. Watch usage in the Console.

## What this exposes

- The bucket is **public**: anyone can download every object by its URL and
  see which models it holds. It is not anonymous. Listing is denied, but a
  manifest's name is public once it is in the mirror's card or `peers.json`.
- Hetzner knows the account holder and sees every request and the
  downloader's address. The address names Hetzner and your bucket.
- The mirror's signed card and discovery (DHT, Nostr) can publish the address.
- Hetzner acts quickly on abuse reports. Serve only models whose licences
  allow redistribution.

## How to stop

1. In Sovereign, turn **Help keep models available** off and wait until the
   panel says the models are out of the bucket (their manifests are deleted).
2. Choose **Disconnect**. Sovereign forgets the keys on this computer.
3. In the Console, delete the S3 credentials (**Security → S3 Credentials**).
4. Empty the bucket, turn off any protection, then delete it. A bucket must
   hold no objects before it can be deleted.
5. The base fee stops only when **no** bucket remains in the account.

## Sources (checked 2026-11-07)

- [Object Storage product and pricing](https://www.hetzner.com/storage/object-storage/)
- [Overview: endpoints, pricing details and limits](https://docs.hetzner.com/storage/object-storage/overview)
- [Creating a bucket](https://docs.hetzner.com/storage/object-storage/getting-started/creating-a-bucket)
- [Generating S3 keys](https://docs.hetzner.com/storage/object-storage/getting-started/generating-s3-keys)
- [S3 credentials FAQ (project-wide keys)](https://docs.hetzner.com/storage/object-storage/faq/s3-credentials)
- [Buckets and objects FAQ (public buckets, custom domains, deletion)](https://docs.hetzner.com/storage/object-storage/faq/buckets-objects/)
- [Using S3 API tools (region is the location code)](https://docs.hetzner.com/storage/object-storage/getting-started/using-s3-api-tools/)
