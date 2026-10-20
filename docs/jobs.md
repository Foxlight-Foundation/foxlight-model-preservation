# Plans, progress, cancellation and retained transfer allowances

These library and CLI controls are available in 0.2.0. Existing bundle signing
and storage format remain v1; v0.1.0 bundles still verify. They are worker
primitives, not a background service, scheduler or inference-runtime installer.

## Plan before copying

```sh
model-preserve plan-fetch MANIFEST_SHA256 --trust bundle-trust.json \
  --mirror https://mirror.example/models --store /path/to/preserved

model-preserve plan-pack --recipe recipe.json --member weights=/path/to/model \
  --store /path/to/preserved

model-preserve plan-restore MANIFEST_SHA256 --trust bundle-trust.json \
  --store /path/to/preserved --output /path/to/new-restored-layout
```

Plans hash selected sources or existing objects, authenticate supplied manifests
and return a small `OperationPlan`. They create no directories, objects, locks,
receipts or signing keys. A remote fetch plan makes bounded metadata requests;
it does not request model objects. Planning can take time and supports the same
progress/cancellation controls as execution.

| Field | Meaning |
| --- | --- |
| `manifest_id` | Exact selected or observed complete manifest payload identity |
| `request_digest` | Hash binding the supplied content/trust/source/path/policy inputs appropriate to this operation; no raw paths or credentials |
| `logical_bytes` | All declared files, counting duplicate content at each restored path |
| `unique_bytes` | Distinct content object sizes |
| `reused_bytes` | Actual existing complete objects rehashed successfully |
| `partial_bytes` | Unverified partial file lengths, capped to each expected object size; potential disk reuse only |
| `additional_bytes` | New object space after observed reuse/partials, or full logical copy space for restore |
| `metadata_bytes` | Known envelope length for fetch/restore; unsigned canonical manifest length before signing for pack |
| `reserve_bytes` | The 16 MiB reserve used for object-copy preflight; this is not a reservation of real free space |

`plan_id` identifies the exact observation. Plans do not grant authority, lock
sources, pin a NAS volume or reserve space. Observations can change immediately.
A host must bind its own authorized job to the approved request and revalidate
roots, capacity and generations before effects. Pack accepts
`expected_manifest_id=plan.manifest_id` and refuses changed source content before
creating the destination. A matching digest does not prove upstream authorship.

An unverified partial can be corrupt or ignored by a server. Its space can be
reused, but do not subtract it from a guaranteed network-transfer allowance.
Fetch still checks available additional space plus the reserve for missing
objects, verifies all complete hashes, and bounds retries. Sparse files and
filesystem allocation/compression can make byte-length estimates optimistic;
an actual write may fail. Previous complete generations remain separate.

## Observe and cancel a worker

```python
from pathlib import Path
from foxlight_model_preservation.operations import OperationControl, Progress
from foxlight_model_preservation.transfer import fetch


# expected_id, trust and sources were independently selected/authenticated.
def observe(progress: Progress) -> None:
    print(progress.model_dump_json())


control = OperationControl(observe)
# Run this in a bounded worker; a separate owner thread may call control.cancel().
manifest = fetch(
    expected_id,
    trust,
    sources,
    Path("preserved"),
    control=control,
    operation_id="recovery_job_1",
)
```

Use a fresh control per invocation. `cancel()` is thread-safe; other control
methods belong to the single worker. `pack`, `verify_store`, `restore`, `fetch`
and the three planning functions accept keyword `control=`. Callbacks receive
immutable snapshots with an operation, stage, cumulative work counters, commit
acknowledgement and optional stable error code. Counters distinguish hashing,
copying, received bytes and verified object reuse. Hashing can read the same bytes
several times; these counters are not a percentage or unique verified coverage.
Pack's reuse counter includes each logical file skipped; fetch counts objects.
No event contains paths, URLs, full cards, keys or file lists. The library keeps
only the latest snapshot. Callers choose bounded retention and privacy policy.

Callbacks run synchronously and must be short. Reports throttle to 0.1 seconds
by default; phase/final events always report. Cancellation is checked at every
bounded chunk even when reporting is throttled. An observer exception disables
that callback and sets `observer_failed`; it cannot turn a completed publication
into a failed copy. Use `cancel()` to stop work rather than raising in a callback.

A cancelled operation raises `OperationCancelled`; the last snapshot says
`cancelled`. Source hashing/copying checks at most 1 MiB per chunk, transfers at
most 64 KiB. Cancellation cannot interrupt a blocked OS filesystem/DNS call or
an in-flight socket read; socket timeouts still apply. A host may need an
isolated worker for hard deadlines. Transfer deadlines also apply at hashing
checkpoints and never extend within an invocation.

Cancellation is checked immediately before final publication. Once publication
and its directory fsync succeed, a late cancellation leaves the outcome complete.
A cancellation request alone is not proof work stopped. I/O failure during commit
can be ambiguous: reconcile actual contents with `verify_store`. Never infer
completion from a started process, partial file or accounting receipt.

The CLI offers `--progress` on pack/verify/fetch/restore/fetch-catalog and plans;
JSON snapshots go to stderr, the result to stdout. Ctrl-C requests cooperative
stop, returning 130 when cancellation wins. `failure_code(error)` gives stable
codes without exception text: `cancelled`, `budget_exceeded`, `deadline_exceeded`,
`request_changed`, `invalid_or_unsafe`, `storage_error`, `operation_failed`.

## Retain an allowance across restarts

```sh
model-preserve fetch MANIFEST_SHA256 --trust bundle-trust.json \
  --mirror https://mirror.example/models --store /path/to/preserved \
  --operation-id recovery_job_1 --max-bytes 21474836480 --progress
```

Explicitly repeat the same request and ID to resume. The destination writer lock
fences concurrent writers. A local `.operations/<id>.json` receipt binds the exact
manifest, trust snapshot, ordered source list, destination path and pinned root
identity, and complete transfer policy. Changed inputs under the same ID fail
with `request_changed`, even if files are already cached. Malformed/symlinked
accounting fails rather than being replaced with a fresh allowance.

Before an object read, the worker durably reserves up to 1 MiB of remaining
allowance, capped by the limit. Within the invocation, unused credit can cover
later reads. A crash/restart forfeits unused credit: `charged_bytes` is a
conservative upper bound, not measured network billing or bytes successfully
stored. Budget headroom may be needed to finish after interruptions. An ambiguous
read error also forfeits its uncertain credit before supplier fallback. No
automatic refund, cap increase, new ID or eviction occurs. A deliberately new ID
is a new authorized allowance, a decision for the operation owner.

`ledger.read_transfer_receipt(store, id)` reads this bounded local record without
writing. Retain receipts with the owning job; deleting them loses cumulative
accounting. They are local trusted state, not part of the public mirror layout,
not a signature and not proof of completion. A machine owner can alter their own
state; this is not an enforcement sandbox against that owner.

Without `operation_id`, fetch retains its original per-invocation actual-byte
limit. Both modes require declared unique content to fit `max_bytes`. Metadata,
HTTP/TLS overhead and OS buffering are outside object-byte accounting. Attempt
count and the deadline apply per invocation. A durable host job must separately
retain retry limits, its overall lifetime, cancellation intent, authority,
idempotency and reconciliation; this library does not automatically restart jobs.

The tests include forced process exit mid-transfer, fresh-process CLI resume,
non-refilling allowances, changed-input refusal, partial-space accounting,
cancellation during verification/copy/publication and observer failure. These
are synthetic transport/storage tests, not Skulk integration, ordinary inference,
independent custody or anonymity qualification.
