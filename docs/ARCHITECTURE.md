# Architecture decisions (exploration core)

Status: implementation and qualification evidence are linked from EXPLORATIONS.
The following design supersedes the initial chat proposal.

## Authority and trust

One process holds a stable writer-lock inode. A reentrant authority lock
serializes catalog changes, payload writes, sealing, publication and GC. Only
network reads occur outside this lock. The two-worker pool uses generation
specific paths; retired work still counts against the outstanding-work limit.
No writable FD is passed to another process. Shutdown retains the writer lock
until all workers and reader references have exited. It may wait for blocked
kernel I/O; no bounded cancellation time is promised.

The private root and its local filesystem are trusted. Root/same-UID attackers
and disk modification while the process is serving are outside scope. Payload
opens use O_NOFOLLOW. Unknown files are charged and reported by inspection,
never automatically claimed or deleted. The core is a Python library; daemon,
Unix socket framing/authorization and packaging of a service are future work.

Manifest names map to fixed URL/size/SHA-256 tuples for an instance. A single
origin with an explicit numeric destination is configured; TLS still verifies
the URL hostname. No DNS fallback, redirects, proxy environment routing,
automatic decompression, arbitrary URL input or cross-origin credentials.

## Transfer states and identity

Object states:

```
QUEUED -> DOWNLOADING -> VERIFYING -> PUBLISHING -> READY
             |                |
             +-> FAILED       +-> FAILED
QUEUED/DOWNLOADING -> CANCELLED
READY/FAILED/CANCELLED/CORRUPT -> DELETING -> absent
```

Retry waiting and sealing are synchronous phases, not separately persisted
states in this core. Every write completes and closes its FD under the lock
before verification. There is no writable background disk task. A digest plus
size identifies content; a random generation owns its staging path. Request
records persist parameters, generation and immutable terminal result.

Repeated request IDs return the original result plus current availability; an
evicted prior success does not trigger a new download. Same ID with different
parameters is rejected. A cancelled subscriber cannot affect other subscribers.
After the final cancellation is committed, late network data cannot write.
The persisted publish intent is the point after which cancellation is TOO_LATE.
Disk failure after cancellation commit is reported as an error, not clean
cancellation completion; any surviving file remains charged.

Pin/unpin and GC plans carry instance epoch, object generation and revision.
Pin changes increment revision. GC rechecks the token, state, pin and readers
at execution; stale plans cannot delete a newer generation or bypass changed
pins. Old cancellation refers only to its immutable request generation.

## HTTP continuation

Only identity-encoded, exact Content-Length responses are supported. The client
uses one `bytes=N-` range and a strong ETag in If-Range. A resumed 206 must match
the exact expected interval, total and ETag. A 200 starts from zero. Weak or
missing validators force a restart. 416, redirects, wrong ranges, encodings,
lengths and digest mismatches cannot be published.

The attempt counter and accepted payload-byte counter persist across restart.
At most six attempts and four object lengths of charged body bytes are allowed;
this is not a total wire/network-quota guarantee. Received but uncheckpointed
network data and HTTP/TLS overhead are not included. Socket operations use a
two-second inactivity timeout. An overall wall-clock deadline against a
slow-trickle origin remains an MVP gap; do not describe the current timeout as
a hard total download deadline.

## Checkpoint and publication

Checkpoint ordering is payload write/flush/fsync, then SQLite offset/ETag
commit. At recovery a tail beyond the checkpoint is truncated; a shorter file
forces offset zero. The complete content is hashed before publication.

A complete checkpoint (offset equals trusted size) is verified locally before
any attempt/transfer budget check. It never sends `Range: bytes=size-` and does
not need an ETag or working origin to finish publication. A compliant origin
would reject that range with 416. The TLS fixture now does so as well.

Publication: fsynced file -> durable PUBLISHING intent -> atomic hard link to
digest destination (must not overwrite) -> target directory fsync -> unlink
stage -> staging directory fsync -> READY and subscriber success transaction.
Both directories are on one local filesystem. Ordinary rename/replace is not
used. A destination already existing in the live publish must be the same
inode; unknown destinations are never overwritten.

On recovery a persistent intent permits adoption only after size/hash
verification. The target is rechecked even when the staging name was already
removed. No-intent orphan files are not promoted. READY objects are fully
revalidated before startup completes. Same-size corruption becomes CORRUPT.

SQLite uses DELETE/FULL. A synchronous storage/SQLite error inside the controller retires mutation
authority as DEGRADED. No async DB task or timeout-based lock takeover exists.
The authority context classifies errors from controlled file/catalog operations;
it does not infer their origin from a short errno allowlist. Network get/read
is outside that context and can retry. EDQUOT/EACCES regression checks inject
errors at a formal write boundary; they are not real quota-device trials.
Consumer reads through the public file object occur outside authority; their
read errors propagate to the consumer and do not promise global retirement.
Repair then restart the same catalog/root/lock. Never erase them to manufacture
recovery. Long-running synchronous fsync/commit blocks authority operations;
bounded responsiveness under that fault is not qualified.

## Capacity and eviction

Admission uses unique managed/unknown payload inode sizes plus the unfilled
portion of active reservations. A two-link publish counts its inode once.
Cancelled/failed bytes remain charged until actually removed. Catalog history
is finite (256 requests by default); there is no automatic ID reuse.

Two workers, sixteen outstanding jobs and eight readers are default limits.
An old cancelled but unfinished worker counts toward the work cap. READY
objects and requests are additionally bounded by finite request history.

GC plan/run is explicit. Fetch never silently evicts other content. Reads acquire
references and FDs under authority, and close the FD before dropping the
reference. DELETING is persisted before unlink; crash recovery finishes owned
deletions. Concurrent read/pin cannot slip between the candidate recheck and
state transition. A GC plan does not reserve a future deletion right.

The payload budget is logical admission control. SQLite journals, metadata,
block rounding and other filesystem users are not covered by that number;
allocated bytes, total cache-file allocation and filesystem free bytes are
reported separately. Dedicated filesystem sizing is needed for an external
physical limit. Actual ENOSPC can still happen and must fail safely.
