# Operate and recover a preservation store

A preservation store is durable data you choose to retain. Keep it outside an
inference runtime's staging/cache eviction directories and outside a capability's
replaceable installation directory. The CLI does not automatically evict models,
prune old generations, buy storage, run scheduled jobs or delete originals.

## What to retain

Keep `manifests/` and `objects/sha256/` together, including every object required
by the retained manifests. Keep public trust profiles, exact IDs and authentic
verifier/recovery material separately recoverable. Keep private signing keys and
passphrases in protected custody outside served model directories.

The `.partial/` directory contains incomplete downloads; `.pending/` can contain
unfinished captures. Neither is a verified generation. `.write.lock` is a file
used for a kernel-held writer lock, not a persistent ownership record. Deleting
it while a process is active does not safely release the lock. After a crashed
process the kernel releases its lock; retry the intended operation.

Copy the two transport directories to a new mirror, then run `verify` against the
independently enrolled trust and exact ID there. A copied directory listing or
successful upload is insufficient evidence of a recoverable copy. Avoid changing
the store while testing restoration. Test in a new output directory and record
which workload, engine and environment actually ran, if inference was exercised.

## Failure and recovery

| Observation | Action |
| --- | --- |
| No authenticated manifest | Check the exact ID and independently enrolled signer. Try another permitted source; do not enroll the failing source's key automatically. |
| Hash or length mismatch | Keep other verified stores intact. Check the affected store/source. Fetch into a dedicated destination from a permitted intact supplier and verify again. |
| Interrupted fetch | Run the same exact request again. Valid objects are reused, partial objects can resume, and corrupt prefixes are discarded. |
| Insufficient space | Add space or choose another destination. No automatic deletion or paid expansion occurs. Current source reuses verified objects before checking capacity for new copies; the v0.1.0 release edge is noted below. |
| Another writer owns the destination | Let the active writer finish or stop that operation through its owner. Do not delete locks or kill unrelated processes. |
| Restore output exists | Choose a new output directory. Restore never merges into or overwrites an existing model tree. |
| NAS path unavailable | Verify the actual mounted volume before operating. The core does not enroll volume sentinels or detect every unmounted-share alias. |
| Seed stops serving after its limit | Its upload reservation budget is exhausted. Review policy before deliberately starting another serving session. |
| Trust/key lost | Use independently retained public trust to verify existing captures. Losing a private signing key prevents new signatures; it need not prevent old verification. See the recovery guide. |

The reference seed's allowance is per process and reserves selected manifest/object
response bytes before sending. Restarting it resets that allowance; it is not a
persistent monthly egress quota. Network protocol/error overhead is not a hard
wire-byte cap. Filesystem copies and HTTP(S) transport require the documented
local/network policy. Use a production static HTTPS service's own operational
controls when durable hosting needs persistent quotas or service supervision.

## Retention, privacy and sharing

Only the explicit `seed` command or your separately configured server shares
content. Fetch/verify/restore do not publish inventory or start a server. The
reference seed does not retain request-history logs; a reverse proxy, static
server or provider may have its own logs. Inspect those separately. Direct HTTPS
mirrors still see client IPs and requested paths.

A public audience declaration is a prerequisite for the reference public seed,
not a license grant or automatic publishing decision. Check all selected members'
actual terms and carry required notices. A private capture should not be copied
into a public static server's root. Signed bundles are not encrypted backups;
a person who obtains their objects and manifest can read the contents.

Stopping sharing cannot recall copies already delivered. Independent operators
can preserve the public format without Foxlight enrollment. Source availability,
verified bytes, independent custody and successful inference are separate facts.

## v0.1.0 release edges and current-source fixes

[Issue #2](https://github.com/Foxlight-Foundation/foxlight-model-preservation/issues/2)
records two cases in the v0.1.0 release: another trusted signer can produce a dot-only file path that
passes hash-addressed receiving but fails restoration; current pack cannot emit
it. Pack can also refuse a nearly full destination before reusing an already
verified duplicate object. Neither accepts changed content or overwrites an
existing capture.

Current source rejects dot-only paths during recipe/manifest validation, including
correctly signed manifests from another implementation. Pack rehashes and reuses
valid existing objects before checking capacity for a new copy. Missing/corrupt
objects still require space for the full temporary replacement plus the 16 MiB
reserve. Publication still requires metadata space and can fail without it; no
disk capacity is promised by deduplication alone. These fixes are not a replacement
v0.1.0 release artifact; use a reviewed source revision until a later release.

See the [protocol](protocol.md) for the full limits and
[trust and recovery](trust-and-recovery.md) for offline material and key handling.
