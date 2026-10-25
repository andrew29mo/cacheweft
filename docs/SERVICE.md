# Service operation contract

## Configuration and manifest history

Provide an owned mode-0600 regular JSON file. This example uses documentation
addresses and an intentionally fictitious digest; replace all source/size/hash
values with trusted inputs. It is not a runnable model download demonstration.

```json
{
  "revision": 1,
  "origin": "https://artifacts.example",
  "address": "192.0.2.10",
  "entries": [{
    "id": "model-v1",
    "url": "https://artifacts.example/model-v1.bin",
    "size": 1048576,
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  }]
}
```

`cafile` may specify a trusted CA bundle. TLS validates the URL hostname while
the transport connects directly to the configured numeric address. There is
no proxy-environment routing, DNS fallback, redirect following or decompression.
Every URL must belong to the configured origin. Origin TLS/address trust is
administrator configuration, not part of an artifact-content signature.

Manifest snapshots are persisted in the same SQLite catalog. A revision may
be reused only with the identical entry snapshot; a lower revision is rejected.
An artifact ID permanently binds literal URL, exact size and digest. Use a new
ID for changed content or URL. New revisions may add/omit IDs; omitted IDs
remain in the immutable ledger and cannot be repurposed. At most 64 revisions
and 128 lifetime IDs are retained. No history deletion or online reload exists.
Configuration changes require restart. A failed startup is not a usable service.
Use a fresh service root for the first managed manifest. An old unversioned
exploration catalog with request history is rejected, preserved and not
automatically migrated. The backend also rejects creating an unbounded request
under a managed manifest; this rule is not left solely to CLI validation.

## Operations

One JSON request per Unix socket connection, framed by a 4-byte big-endian
length (1..16384 bytes). The daemon accepts only known operations and fields.
Mutations carry the current instance epoch; cancel/resume carry the source
request generation/revision, pin and GC carry object generation/revision.
The CLI obtains fresh tokens. An explicitly saved GC plan remains subject to
revalidation; it is not a reserved deletion right.

| CLI operation | Behaviour |
|---|---|
| `info` | Live epoch, active manifest revision, degraded state, IDs and usage |
| `fetch ID ARTIFACT --seconds N` | Persist admission then return; same accepted ID/parameters returns original result, changed parameters conflict |
| `status ID` | Immutable terminal request result plus current content availability |
| `cancel ID` | Cancel that subscription; last cancellation stops write/publication authority and interrupts its transport |
| `resume NEW_ID ARTIFACT --source OLD_ID --seconds N` | New bounded request for INTERRUPTED/TIMED_OUT partial work; exact artifact match and current token required |
| `read DIGEST --output FILE` | Stream READY bytes; client atomically replaces its output only after exact stream length completes |
| `pin DIGEST --value true/false --request ID` | Persist pin with current object token |
| `gc-plan` | Up to 32 current unpinned/unreferenced candidates, including PAUSED leftovers |
| `gc-run --plan FILE` | Revalidate and remove eligible entries; repeat plan/run explicitly for more candidates |

Only an admitted transaction reserves a request ID. Pre-admission errors such
as invalid configuration, quota/history exhaustion or stale authority are
rejections, not successful work. Default request history is 256 and can be
configured up to 4096; it has no automatic reclamation. A historical success
can remain SUCCEEDED after content is evicted. Likewise a timed-out subscriber
can see `available=true` if another subscriber later completed the same content;
this never changes that timed-out request's result or deadline.
Fetch/resume require an artifact in the active manifest; `status ID` remains
the history lookup when an artifact is omitted from a later active snapshot.

## Deadline and restart semantics

Duration is finite 0.1..120 seconds (CLI default 30), canonicalized before
deduplication. Its monotonic deadline is fixed when the core submission begins,
before waiting for authority. Queue wait, connection, TLS, headers, body,
retries and verification consume that same budget. An authority wait that
exhausts the budget is rejected even if content has become a cache hit.

The transport uses nonblocking connect with interest/deadline polling and a
watcher that shuts down stalled TLS/header/body sockets. A 20 ms expiry sweep
updates request state. Before each write and before publication intent, the
controller rechecks interest. A new subscriber can keep a shared download
alive, but cannot extend an existing subscriber's stored deadline.
Admission checks again after SQLite BEGIN returns; publication checks live
interest inside its write transaction. Chunk writes recheck after precharging
their byte counter. Database waits do not authorize starting a later step
with an already exhausted budget.
The watcher owns an independent socket handle through response-body cleanup;
HTTP/1.0 or Connection: close detachment must not retire it after headers.

Before durable PUBLISHING intent, expiry makes the request TIMED_OUT and stops
its interest. If no subscribers remain, the object becomes PAUSED and transport
is interrupted. Staged bytes remain charged; unused reservation is released
because no further writes are authorized. Explicit GC may reclaim those bytes.
Cancellation before that boundary returns CANCELLED; after it, TOO_LATE.

Synchronous fsync/SQLite/kernel I/O and scheduling can delay actual response or
shutdown. The deadline is a cutoff for starting publication authority, not a
hard realtime promise to undo committed work or finish all local I/O by N.
If intent already committed, completion/recovery proceeds and a client timeout
means result unknown until status is queried. SIGKILL and normal reboot are
qualified separately; sudden power loss and suspend are not qualified.

On restart, bounded WAITING requests that had not committed publication become
INTERRUPTED, retaining their original deadline, epoch and parameters. No clock
translation or fresh automatic budget is applied. Partial objects become PAUSED.
An explicit resume needs a new request ID/deadline and a fresh epoch/token;
if a retired worker is still exiting, resume is rejected until it is gone.
Committed publish intents retain their old deadline field and recover to
SUCCEEDED rather than pretending cancellation was possible.

Request terminal states: SUCCEEDED, FAILED, CANCELLED, TIMED_OUT, INTERRUPTED.
Object states: QUEUED, DOWNLOADING, VERIFYING, PUBLISHING, READY, PAUSED,
FAILED, CANCELLED, CORRUPT, DELETING. The earlier library-only unbounded request
mode remains for the preserved exploration tests; the daemon never exposes it.

## Resource and process limits

Two download workers; at most 16 configured outstanding jobs, 8 readers,
16 active control connections plus a listen backlog of 16. Accepted control
frames have a total 2-second receive deadline. Excess accepted connections get
OVERLOADED; the OS can also refuse connections when its backlog is full.
Reads have a 10-second stream window and bounded chunks. Pin and request IDs
are bounded; GC plan/run is capped at 32 entries per frame. Config is at most
64 KiB, URLs 2048 characters and artifact IDs 64 characters.

The payload budget defaults to 16 MiB (configurable 64 KiB..1 GiB); metadata,
journals and block rounding are additional. SQLite pages are capped separately.
ENOSPC, read-only paths and SQLite FULL refuse mutation and require explicit
repair/restart. Never delete catalog or lock files to manufacture recovery.

The daemon refuses UID 0, has no network-admin operations and listens only on
an owned private directory/socket. The socket is mode 0600. Same UID/root are
trusted. SIGTERM closes admission, removes the readiness hint/socket, waits for
control readers and workers, then releases the stable writer lock. `ready.json`
is atomically replaced as a discovery hint; live `info` is the readiness check.
It is not a power-loss durable health record. systemd units and autostart are
deliberately outside this milestone.
