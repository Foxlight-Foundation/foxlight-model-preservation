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
| **Cloudflare R2** (recommended) | account ID, bucket, public address | No download fees. In the bucket's **Settings**, turn on **Public access** (the `r2.dev` subdomain or your own domain) and copy that address. Create an R2 API token with Object Read & Write on this bucket. |
| Backblaze B2 | region, bucket | Make the bucket public. Create an application key limited to it. The region is in the bucket's S3 endpoint, for example `us-west-004`. |
| Amazon S3 | region, bucket | Turn off Block Public Access for the bucket and add a read-only bucket policy. Amazon bills downloads. |
| Wasabi | region, bucket | Create a bucket with public read access. |
| Other S3-compatible | endpoint, region, bucket, public address | For MinIO and similar services. |

Sovereign works out the endpoint, signing region, addressing style and (where
it can) the public address from these answers (`bucket.target_for`).

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

## Limits

A bucket cannot follow sources or renew its own mirror card. The desktop app or
a server mirror keeps it current while running. Requests use AWS Signature
Version 4 with the standard library only (checked against AWS's published
example), with no new dependency.
