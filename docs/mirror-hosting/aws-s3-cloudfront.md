# Host a bucket mirror on AWS (S3 + CloudFront)

A private S3 bucket that only CloudFront can read, with Sovereign uploading
through the S3 API. It works well, but on pay-as-you-go pricing the download
bill grows quickly. Read [When AWS makes sense](#when-aws-makes-sense) first,
and do not skip the budget alarm in step 8.

Checked against AWS documentation and pricing on **2026-11-07**. Prices
change; check [CloudFront pricing](https://aws.amazon.com/cloudfront/pricing/)
and [S3 pricing](https://aws.amazon.com/s3/pricing/) before you budget.

## Before you start

- An AWS account you can sign in to as an administrator.
- The Sovereign desktop app with **Help keep models available** and **Cloud
  storage**: Library 0.24 or later (Mac build 10, Linux revision 5).
- At least one backed-up model whose licence allows sharing.
- For the final check: a second computer with the standalone tools.

The examples use the Region `eu-central-1` and the bucket
`sovereign-mirror-example`. Bucket names are global; pick your own, and avoid
dots.

## Steps

1. **Create the bucket, and keep it private.** In the S3 console, select your
   Region, then **General purpose buckets → Create bucket**:
   - **Bucket name:** `sovereign-mirror-example`.
   - **Object Ownership:** **ACLs disabled (Bucket owner enforced)**, the
     default. CloudFront's Origin Access Control requires it.
   - **Block Public Access:** leave **all four settings on**, the default.
     Nobody reads the bucket directly; CloudFront does.
   - **Versioning:** off. **Default encryption:** SSE-S3.

   Choose **Create bucket**.
2. **Clean up abandoned uploads (recommended).** In the bucket's
   **Management** tab, create a lifecycle rule for the whole bucket with
   **Delete expired object delete markers or incomplete multipart uploads →
   Delete incomplete multipart uploads** after 1 day. An interrupted large
   upload then stops costing storage.
3. **Create the CloudFront distribution.** In the CloudFront console, choose
   **Create distribution**:
   - Give it a name, choose **Single website or app**, then **Next**.
   - **Pricing plan:** choose **Pay as you go**, or a flat-rate plan (see
     [costs](#what-this-costs)). A flat-rate plan includes AWS WAF and
     requires it. On pay-as-you-go, AWS WAF is billed separately; you can
     choose not to enable security protections.
   - **Origin type:** **Amazon S3**, then **Browse S3** and pick
     `sovereign-mirror-example`.
   - Keep **Use recommended origin settings**. This creates an Origin Access
     Control (OAC), and CloudFront updates the bucket policy for you.
   - Keep the default cache settings (the managed **CachingOptimized**
     policy). See [Caching](#caching-and-invalidation) for why.

   Choose **Create distribution** and wait until **Last modified** shows a
   date instead of **Deploying**. Copy the **Distribution domain name**, such
   as `d1234abcd.cloudfront.net`.
4. **Check the bucket policy.** In S3, open the bucket's **Permissions →
   Bucket policy**. It should allow only CloudFront, only to read, only for
   your distribution:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Sid": "AllowCloudFrontServicePrincipalReadOnly",
         "Effect": "Allow",
         "Principal": {"Service": "cloudfront.amazonaws.com"},
         "Action": "s3:GetObject",
         "Resource": "arn:aws:s3:::sovereign-mirror-example/*",
         "Condition": {
           "StringEquals": {
             "AWS:SourceArn": "arn:aws:cloudfront::ACCOUNT_ID:distribution/DISTRIBUTION_ID"
           }
         }
       }
     ]
   }
   ```

   With this policy a missing file returns `403`, not `404`, to downloaders.
   That is fine: `fetch` treats any error status the same way and tries the
   next mirror.
5. **Create a least-privilege policy for the app.** In IAM, choose **Policies
   → Create policy → JSON**, paste this and name it
   `sovereign-mirror-example-upload`:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Sid": "ListThisBucketOnly",
         "Effect": "Allow",
         "Action": "s3:ListBucket",
         "Resource": "arn:aws:s3:::sovereign-mirror-example"
       },
       {
         "Sid": "ObjectsInThisBucketOnly",
         "Effect": "Allow",
         "Action": [
           "s3:GetObject",
           "s3:PutObject",
           "s3:DeleteObject",
           "s3:AbortMultipartUpload"
         ],
         "Resource": "arn:aws:s3:::sovereign-mirror-example/*"
       }
     ]
   }
   ```

   Why each permission: `PutObject` uploads (including every multipart step),
   `GetObject` and `ListBucket` let Sovereign's check of an object get `404`
   for a missing object instead of `403` (without `ListBucket`, uploads fail),
   `DeleteObject` withdraws a manifest and removes the setup check file, and
   `AbortMultipartUpload` cleans up a failed large upload.
6. **Create the uploader and its key.** In IAM, **Users → Create user**, name
   it `sovereign-mirror-uploader`, and do **not** give it console access.
   Attach the policy from step 5 (**Attach policies directly**). Then open the
   user's **Security credentials → Create access key**, choose the use case
   **Other**, and **Create access key**. Choose **Show**; do **not** download
   the `.csv` file. Leave the page open and go straight to the next step; do
   not save the keys anywhere else.

   AWS recommends temporary credentials over long-term keys. Sovereign needs a
   key pair it can keep in the keychain, so this key is limited to one bucket
   instead. Rotate it if you suspect it leaked.
7. **Connect it in Sovereign.** Open **Sharing → Help keep models available →
   Cloud storage → Set up cloud storage** and choose **Other S3-compatible
   storage**. The **Amazon S3** preset does not ask for a public address; it
   assumes a public bucket, which this guide avoids.

   | Field | Value |
   | --- | --- |
   | Endpoint | `https://s3.eu-central-1.amazonaws.com` (your Region) |
   | Region | `eu-central-1` |
   | Bucket name | `sovereign-mirror-example` |
   | Public address | `https://d1234abcd.cloudfront.net` |
   | Access key ID / Secret access key | paste from step 6 |

   Sovereign uploads through the Region's S3 endpoint (path-style requests,
   which S3 supports in all Regions) and tells recipients the CloudFront
   address. Choose **Check and connect**. The check uploads a test file
   through S3, then downloads it **through CloudFront**, so a **ready** result
   proves the OAC path works end to end. If it reports that nobody could
   download, wait until the distribution has finished deploying and check
   step 4.
8. **Set an AWS Budget alarm. Do not skip this.** CloudFront has no spending
   cap on pay-as-you-go. In **Billing and Cost Management → Budgets → Create
   budget**, choose **Use a template (simplified) → Monthly cost budget**.
   Enter a name, a monthly amount you would be uncomfortable exceeding (for
   example $10), and the email addresses to warn. Choose **Create budget**.
   The template emails you when spending exceeds, or is forecast to exceed,
   the amount.

   A budget **only warns; it does not stop spending**. Its data updates up to
   three times a day, so charges can run for hours before the email. If you
   get one, turn sharing off (see [How to stop](#how-to-stop)). AWS Budgets
   monitoring is free.
9. **Confirm downloads work from another computer.** In the S3 console, open
   the bucket's `manifests/` folder and copy one file name without `.json`. On
   the second computer:

   ```sh
   curl -fsS -o /dev/null -w '%{http_code}\n' \
     https://d1234abcd.cloudfront.net/manifests/MANIFEST_SHA256.json
   model-preserve fetch MANIFEST_SHA256 --trust publisher-trust.json \
     --mirror https://d1234abcd.cloudfront.net --store ./mirror-check
   ```

   `curl` should print `200`. `publisher-trust.json` is the trust snapshot for
   the key that signed the model, enrolled independently as for any fetch.
   Delete `./mirror-check` afterwards.

## Caching and invalidation

Sovereign sets its own `Cache-Control` headers, and the managed
**CachingOptimized** policy (minimum 1 second, default 1 day, maximum 1 year)
respects them:

- **Objects** (`objects/sha256/…`) are immutable and uploaded with
  `public, max-age=31536000, immutable`. CloudFront keeps them at the edge.
  Cached downloads cost no S3 request.
- **Manifests, `peers.json` and the setup check file** are uploaded with
  `no-cache`. Because the policy's minimum TTL is 1 second, CloudFront caches
  them for about a second. A withdrawn model (its manifest deleted) stops
  being served almost at once, and a repeated **Check and connect** sees its
  new test file.
- CloudFront caches error responses for 10 seconds by default. A `404` is
  cached; a `403` from S3 is not, unless S3 sends a `max-age`.

You do not need invalidations with these settings. If you choose a cache
policy with a higher minimum TTL, a withdrawn manifest stays downloadable
until it expires, and a second setup check can fail with a stale test file.
Then create an invalidation (**Invalidations → Create invalidation**, path
`/manifests/*`). The first 1,000 invalidation paths a month are free; a
wildcard counts as one path.

## What this costs

From [CloudFront pricing](https://aws.amazon.com/cloudfront/pricing/) and
[S3 pricing](https://aws.amazon.com/s3/pricing/) (2026-11-07, US and Europe):

| Item | Price |
| --- | --- |
| S3 Standard storage (us-east-1) | $0.023 per GB-month (first 50 TB) |
| S3 → CloudFront transfer | free |
| S3 PUT and LIST requests | $0.005 per 1,000 |
| S3 GET requests | $0.0004 per 1,000 |
| CloudFront always-free tier, monthly | 1 TB out, 10 million requests |
| CloudFront pay-as-you-go, next 9 TB | $0.085 per GB |
| HTTPS requests (US) | $0.0100 per 10,000 |

CloudFront also offers **flat-rate plans** with no overage charges:

| Plan | Per month | Data transfer allowance | Requests | S3 storage credit |
| --- | --- | --- | --- | --- |
| Free | $0 | 100 GB | 1 million | 5 GB |
| Pro | $15 | 50 TB | 10 million | 50 GB |

The allowances are not hard limits. AWS says that if you keep substantially
exceeding a plan's allowance without upgrading, "your traffic delivery might
be adjusted": slower or more distant edges. A plan covers one distribution
and includes AWS WAF. Plan availability can depend on your recent usage.

| Stored / downloaded per month | Pay-as-you-go | Pro plan |
| --- | --- | --- |
| 100 GB / 1 TB | about $2.30 | $15 + $1.15 storage |
| 500 GB / 10 TB | about $795 | $15 + $10.35 storage |
| 2 TB / 50 TB | over $3,000 | $15 + about $46 storage |

## When AWS makes sense

- You have AWS credits, or the account already exists and is well managed.
- Traffic stays inside the always-free 1 TB a month: then you pay only for
  storage, about 50% more per GB than R2.
- You want a predictable bill and choose the **Pro** flat-rate plan, and
  accept that delivery can be slowed if you outgrow it.

It does not make sense on **pay-as-you-go with several TB of downloads a
month**: each TB past the first costs about $87, where R2 charges nothing and
Hetzner about €1. It also has more parts to get wrong (IAM, OAC, cache
policies) than R2 or Hetzner.

## What this exposes

- The CloudFront address is **public**: anyone can download every object and
  see which models it holds. It is not anonymous.
- The bucket itself stays private, and its name is not part of the public
  address. Bucket listing is not possible through CloudFront.
- AWS knows the account holder and sees every request and the downloader's
  address.
- The mirror's signed card and discovery (DHT, Nostr) can publish the address.
- AWS can act on abuse reports. Serve only models whose licences allow
  redistribution.

## How to stop

1. In Sovereign, turn **Help keep models available** off and wait until the
   panel says the models are out of the bucket. Then choose **Disconnect**;
   Sovereign forgets the keys on this computer.
2. In IAM, open `sovereign-mirror-uploader` → **Security credentials**,
   deactivate then delete the access key, then delete the user.
3. In CloudFront, if the distribution is on a flat-rate plan, cancel the plan
   first. Then **Disable** the distribution, wait until it finishes deploying,
   and **Delete** it.
4. In S3, **Empty** the bucket, then **Delete** it. Storage billing stops once
   it is empty.
5. Keep the budget until the next bill confirms the charges have stopped.

## Sources (checked 2026-11-07)

- [Creating a general purpose bucket](https://docs.aws.amazon.com/AmazonS3/latest/userguide/create-bucket-overview.html)
- [Bucket naming rules](https://docs.aws.amazon.com/AmazonS3/latest/userguide/bucketnamingrules.html)
- [Virtual hosting and path-style requests](https://docs.aws.amazon.com/AmazonS3/latest/userguide/VirtualHosting.html)
- [Restricting access to an S3 origin (OAC)](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-s3.html)
- [Getting started with a CloudFront distribution](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/GettingStarted.SimpleDistribution.html)
- [Flat-rate pricing plans](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/flat-rate-pricing-plan.html)
- [Managed cache policies](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/using-managed-cache-policies.html)
- [Caching error responses](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/HTTPStatusCodes.html)
- [Invalidations](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/Invalidation_Requests.html) and [their pricing](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/PayingForInvalidation.html)
- [GetObject (403 versus 404)](https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObject.html)
- [Multipart upload permissions](https://docs.aws.amazon.com/AmazonS3/latest/userguide/mpuoverview.html)
- [Managing access keys](https://docs.aws.amazon.com/IAM/latest/UserGuide/access-keys-admin-managed.html)
- [Budget templates](https://docs.aws.amazon.com/cost-management/latest/userguide/budget-templates.html) and [AWS Budgets pricing](https://aws.amazon.com/aws-cost-management/aws-budgets/pricing/)
- [CloudFront pay-as-you-go pricing](https://aws.amazon.com/cloudfront/pricing/pay-as-you-go/)
- [Deleting a distribution](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/HowToDeleteDistribution.html)
