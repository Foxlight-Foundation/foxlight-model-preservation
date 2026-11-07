# Bucket mirrors

A Sovereign store is only `manifests/` and `objects/sha256/`, which any static
HTTPS host can serve. A public-read bucket is therefore a complete mirror: no
server to run, no port to open, and no home IP address exposed. It is the
easiest way for someone behind a home router to help keep models available.

Recipients use it like any mirror: `fetch` downloads from the bucket's public
address and verifies every byte against model keys they chose. The bucket never
holds a signing key and grants no trust.

## Choose a provider

You need a bucket, a key that can write to it, and public read access.

| Provider | You provide | Notes |
| --- | --- | --- |
| **Cloudflare R2** (recommended) | account ID, bucket, public address; optionally a jurisdiction | No download fees. In the bucket's **Settings**, connect your own domain (or the `r2.dev` address) and copy that address. Create an R2 API token with Object Read & Write on this bucket. Choose the jurisdiction (EU, FedRAMP or US) only for a bucket created in one. |
| Backblaze B2 | region, bucket | Make the bucket public. Create an application key limited to it. The region is in the bucket's S3 endpoint, for example `us-west-004`. |
| Amazon S3 | region, bucket; optionally a public address | With CloudFront, keep the bucket private behind Origin Access Control and give the distribution's `https://` address (or your domain) as the public address. Without one, the bucket's own address is used, so turn off Block Public Access and add a read-only bucket policy. Amazon bills downloads. |
| Wasabi | region, bucket | Create a bucket with public read access. |
| Hetzner Object Storage | region (location), bucket | Create the bucket with Visibility **Public**. The region is the location code: `fsn1`, `nbg1` or `hel1`. S3 credentials cover every bucket in the project. |
| Other S3-compatible | endpoint, region, bucket, public address | For MinIO and similar services. |

Sovereign works out the endpoint, signing region, addressing style and (where
it can) the public address from these answers (`bucket.target_for`). Each
preset lists the answers it asks for and which of them are optional
(`ProviderPreset.optional`); the app requires every other one.

- **R2 jurisdictions.** A bucket created in a jurisdiction is reachable only
  at that jurisdiction's S3 endpoint,
  `https://ACCOUNT_ID.JURISDICTION.r2.cloudflarestorage.com`, where the
  jurisdiction is `eu`, `fedramp` or `us`
  ([R2 data location](https://developers.cloudflare.com/r2/reference/data-location/),
  checked 2026-11-07). Choosing one switches the endpoint; the default keeps
  `https://ACCOUNT_ID.r2.cloudflarestorage.com`. The signing region stays
  `auto`, and the public address is the one you connected to the bucket.
- **A public address in front of S3.** When the Amazon S3 preset is given a
  public address, Sovereign still uploads to S3
  (`https://BUCKET.s3.REGION.amazonaws.com`), and recipients download only
  from the address you gave. It must be a plain `https://` address with no
  user name, password, query or fragment. Left empty, the public address is
  the bucket's own `https://BUCKET.s3.REGION.amazonaws.com`, which needs a
  public-read bucket.

Before app versions with these options, both setups were connected with
**Other S3-compatible storage** (see the [AWS](mirror-hosting/aws-s3-cloudfront.md)
and [R2](mirror-hosting/r2.md) guides). Buckets connected that way keep
working; there is nothing to change.

Step-by-step guides with exact values, costs and how to stop:
[Cloudflare R2](mirror-hosting/r2.md),
[Hetzner Object Storage](mirror-hosting/hetzner-object-storage.md) and
[AWS S3 behind CloudFront](mirror-hosting/aws-s3-cloudfront.md). See
[where to host a mirror](mirror-hosting/README.md) for a cost comparison.

## Keys stay in the keychain

The access key and secret are stored in the operating system's keychain (the
macOS Keychain, or the Secret Service on Linux through `secret-tool`) under the
service name `sovereign-bucket`. A bucket target records only the keychain
entry's name. Keys never appear in configuration files, logs, exceptions or
printed values. Uploads require HTTPS, except to a loopback endpoint such as a
local MinIO.

## Check, then publish

`check_bucket` proves the whole path works before anything is shared. It uploads
a small check file, downloads it anonymously from the public address, then
deletes it. It reports `ready`, or the step that failed:

- `upload` means the keys or the bucket name are wrong;
- `public` means the bucket is not publicly readable at that address.

`publish_to_bucket` verifies the archive locally and refuses any member without
public intake, so private backups never reach a bucket. It then uploads each
object the bucket does not already hold, using multipart uploads for large
weight files. The signed manifest is uploaded **last**, so a recipient never sees
a manifest whose bytes are missing. Running it again uploads only what is
missing. `remove_from_bucket` withdraws a model by deleting only its manifest;
objects that other models may share are left in place. Nothing else is ever
deleted.

`publish_peers` writes the bucket's `/.well-known/sovereign/peers.json`, so a
bucket mirror takes part in [mirror discovery](mirror-discovery.md) like any
other.

## What the bucket serves, and what recipients need

The **Cloud storage** panel lists the models the bucket currently serves:
name, manifest ID, size and when each finished uploading. The list comes from
the app's own records of confirmed uploads (`bucket.json` and the community
mirror's retained copies); Sovereign never lists the bucket remotely. A model
whose upload is not yet confirmed is not listed, although Sovereign still
withdraws it if you stop sharing.

**Export verification info** gives a recipient what they need to download
from the bucket and verify every byte, without trusting the bucket:

- one ordinary [`.foxhandoff`](handoffs.md) per model, the same one-file
  handoff the app already opens. It wraps an unchanged signed invitation: the
  signed manifest, its exact manifest ID, the public trust profile of the key
  that signed it, and the bucket's public address as the only mirror;
- the signing key's fingerprint, to give recipients through a channel they
  already trust. A file alone cannot prove who sent it.

Bucket copies are signed by the key of the copy the community mirror serves:
the app's own publisher key (`sharing/publisher-trust.json`) for a backup you
made public, or the original signer for a public copy you received and
re-share. Each handoff carries that model's own trust profile. Handoffs
contain no bucket keys, keychain names or local paths, and are built offline.

Recipients open the file in Sovereign (**Find → Independent sources → Open
sharing file**) and compare the key before downloading, or use the
standalone tools:

```sh
model-preserve handoff-inspect --handoff MODEL.foxhandoff
model-preserve handoff-fetch --handoff MODEL.foxhandoff --store ./received \
  --accept-key ed25519:FINGERPRINT
```

## Stored state

`sharing/community/bucket.json` records the bucket target (never its keys)
and which models it serves. Version 2 (`sovereign.community-bucket.v2`) adds
when each model finished uploading. A state without upload times is still
written as version 1, unchanged, so a bucket that has not uploaded since an
upgrade still reads in older apps. Once an upload time is recorded, older apps
refuse the version 2 file instead of misreading it. R2 jurisdictions and S3
public addresses need no new stored field: they are in the target's endpoint
and public address, which every version reads.

## Limits

A bucket cannot follow sources or renew its own mirror card. The desktop app or
a server mirror keeps it current while running. Requests use AWS Signature
Version 4 with the standard library only (checked against AWS's published
example), with no new dependency.
