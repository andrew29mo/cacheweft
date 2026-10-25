# Architecture decisions (milestone 1)

Status: current implementation and qualification are linked from MILESTONE1_REVIEW.
The following design supersedes the initial chat proposal.

## Authority and trust

One process holds a stable writer-lock inode. A reentrant authority lock
serializes catalog changes, payload writes, sealing, publication and GC.
Network connect/get/read and consumer streaming run outside this lock. The two-worker pool uses generation
specific paths; retired work still counts against the outstanding-work limit.
No writable FD is passed to another process. Shutdown retains the writer lock
until the expiry sweep, workers, transport watchers and reader references have exited. It may wait for blocked
kernel I/O; no bounded cancellation time is promised.

The private root and its local filesystem are trusted. Root/same-UID attackers
and disk modification while the process is serving are outside scope. Payload
opens use O_NOFOLLOW. Unknown files are charged and reported by inspection,
never automatically claimed or deleted. The installed unprivileged daemon
exposes this same core through a bounded private Unix socket and CLI. systemd
installation/autostart remain future work. See SERVICE for framing and limits.

Manifest IDs permanently map to literal URL/size/SHA-256 tuples in SQLite.
Snapshots have monotonically advancing revisions, at most 64 snapshots and
128 lifetime IDs. Same revision with changed entries or an older revision is
rejected. A single
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
QUEUED/DOWNLOADING/VERIFYING -> PAUSED (no live bounded interest)
PAUSED -> QUEUED (new explicit resume request)
PAUSED -> DELETING -> absent
```

Retry waiting and sealing are synchronous phases, not separately persisted
states in this core. Every write completes and closes its FD under the lock
before verification. There is no writable background disk task. A digest plus
size identifies content; a random generation owns its staging path. Request
records persist parameters, generation and immutable terminal result.
Bounded requests additionally retain their original epoch, monotonic deadline
and revision. The daemon requires a finite duration; the backend enforces that
policy too when manifest management is active. It rejects an unversioned
exploration catalog with request history; preserve it and use a fresh service
root. It does not silently import old unbounded interest or rename old IDs.

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
Resume/cancel require current request tokens and epoch. After restart,
pre-publication bounded WAITING requests become INTERRUPTED/PAUSED without
rewriting deadlines. Committed intents recover normally. Explicit resume uses
a new request ID/deadline, exact artifact match and fresh tokens; it waits for
retired work to finish rather than accepting a race with its old completion.

## HTTP continuation

Only identity-encoded, exact Content-Length responses are supported. The client
uses one `bytes=N-` range and a strong ETag in If-Range. A resumed 206 must match
the exact expected interval, total and ETag. A 200 starts from zero. Weak or
missing validators force a restart. 416, redirects, wrong ranges, encodings,
lengths and digest mismatches cannot be published.

The attempt counter and accepted payload-byte counter persist across restart.
At most six attempts and four object lengths of charged body bytes are allowed;
this is not a total wire/network-quota guarantee. Received but uncheckpointed
network data and HTTP/TLS overhead are not included. Socket inactivity timeouts
are supplemented with a request's fixed total pre-publication deadline.
Nonblocking connect polls live interest; a transport watcher shuts down stalled
TLS/header/body I/O. Queueing, retries and verification do not reset duration.
Expired interest is checked before writes, after verification and on exception
cleanup (socket shutdown can surface as HTTP parser error). A later subscriber
cannot extend an old deadline. The expiry sweep is an aid, not the only guard.
See SERVICE for committed-publication, synchronous I/O and suspend boundaries.

Deadline-sensitive admission/intent transactions disable SQLite's internal busy
sleep and explicitly retry BEGIN/COMMIT with the original busy budget. Each
retry checks the fixed cutoff, including after validation. Publication expires
individual subscribers inside the transaction before every COMMIT attempt;
a live long subscriber cannot turn an expired short subscriber into success.
The prior busy_timeout is restored on success or rollback. Diagnostic busy
counters are process-local, saturated and neither authority nor durable audit.

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
PAUSED bytes stay charged while their unused reservation is released. Resume
must reserve the remaining bytes again. Socket workers, backlog, control frames
and stream deadlines are separately bounded; gc-plan returns at most 32 items.

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
