# Milestone 1: managed manifest, bounded requests and daemon

Historical initial implementation at 705c510. It did not cover COMMIT lock
waits and mixed-subscriber expiry during transaction waiting. Those defects
and the current qualification are in [MILESTONE1_REVIEW](MILESTONE1_REVIEW.md).
The numbers below refer to the archived initial cohort, not current results.

This milestone implements the local daemon/CLI over the formal core, persisted
manifest revisions, immutable request operations, fixed per-request download
deadlines and conservative explicit continuation after restart. Current
configuration, commands and exact semantics are in [SERVICE](SERVICE.md).

## Formal implementation and evidence

The wheel contains the runtime and console entrypoint. The isolated Linux VM
installs that wheel, moves runtime source outside import reach and compares its
module hashes against native runtime/frozen inputs. Both environments run the
same identified tests, including actual daemon and CLI subprocesses. The Linux
observer uses setpriv to execute the product as UID/GID 65534, CapEff 0 and
NoNewPrivs 1; root is used only by the observer for fixtures and reboot.

Archived initial [JSON](../evidence/milestone1-initial.json) and [HTML](../evidence/milestone1-initial.html) bind
the test cohort, wheel digest, all 18 source inputs, actual stage exit/timeout
results, controlled cases and cleanup. Deliberately corrupted report copies
are rejection controls, not additional implementation tests. The original
reviewed exploration cohort is preserved [here](../evidence/explorations-reviewed.json).

## Recorded final qualification

Native macOS and isolated Linux each passed **63 implementation tests**, with zero failures/errors/skips. **36 modified evidence copies** were rejected. These are observer controls, not extra implementation tests.

The following single observations use a configured 0.3-second download duration for slow header/body cases. Cancellation is a separate explicit operation. Stop is measured after terminal state, not from original admission.

| Environment | Slow headers terminal | 1-byte slow body terminal | Cancel terminal | Normal stop observations | Daemon current RSS samples |
|---|---:|---:|---:|---:|---:|
| Native macOS | 312.74 ms | 320.40 ms | 8.67 ms | 227.96–235.34 ms | 27.61–27.69 MiB |
| Isolated Linux VM | 308.12 ms | 312.80 ms | 8.21 ms | 217.66–229.49 ms | 23.19–23.20 MiB |

The Linux unresolved-neighbor connect plus daemon stop took **550.74 ms** in this observation. Its request timed out without an origin GET; the isolated network fixture was removed.

Each memory sample belongs to a separate actual daemon PID. A range across those samples is not a growth trace or peak. No platform speed ranking or production recovery upper bound is inferred. Both normal VM reboot witnesses and all VM/disk/profile cleanup checks passed.

A [supplemental initial native CLI smoke](../evidence/milestone1-native-cli-initial.json) records subprocess resume/status/cancel and immutable old outcome; it is not additional Linux qualification.

## Acceptance covered

| Condition | Required observation |
|---|---|
| Manifest reused with changed entries | Startup rejected; no usable socket |
| New revision repurposes an old artifact ID | Startup rejected |
| Manifest revision rolls back | Startup rejected |
| Same request ID retries with a longer duration | Conflict, old deadline unchanged |
| Slow headers/body | TIMED_OUT, no publication, transport and process stop |
| Delayed expiry sweep with parser error from socket shutdown | Official error path resolves TIMED_OUT |
| Queued job expires | No source GET and zero transfer attempts for that job |
| Authority wait exhausts duration | Rejected even if an object became a cache hit |
| SQLite BEGIN wait exhausts admission/publish duration | No late cache-hit admission or new publication intent |
| Byte precharge consumes remaining duration | No new payload write after the recheck |
| Short and long subscribers share work | Old request stays TIMED_OUT; new request may succeed |
| Cancellation/token replay | CANCELLED denies publication; old revision rejected |
| Partial request, daemon SIGKILL/restart | Old deadline unchanged, INTERRUPTED/PAUSED; new explicit resume only |
| Already committed publication intent | Recover to success without resetting old deadline |
| Managed core asked for an unbounded request | Rejected below the CLI layer |
| Unversioned exploration catalog with request history | Rejected and preserved; no automatic interest import |
| Oversized control frame / unsafe config permissions | Rejected; no false ready state |
| Sixteen held control sessions | Excess accepted connection receives OVERLOADED |
| SIGTERM while an accepted frame is incomplete | New admission rejected before draining that frame |
| read/pin/GC CLI flow | Exact bytes, pin blocks GC, explicit unpin/GC releases content |

The earlier core correctness, cancellation/publication, reader/GC, actual
ENOSPC/read-only path and both admission/publish SQLite FULL checks remain in
the qualification. The storage faults remain formal-core tests, not new claims
of faulting the daemon's production filesystem or an installed systemd unit.

## Isolated Linux network and reboot cases

An extra Linux-only case enters a fresh veth/network namespace as an
unprivileged daemon and connects to an unanswered on-link numeric address.
The observer checks namespace identity and unresolved neighbor state. A
0.3-second request must time out and exit within the controlled observation
bound, with zero origin GETs. The namespace and interfaces are removed. This
probes TCP connect cancellation without changing host networking or borrowing
another route. It does not qualify WAN behaviour or arbitrary network stacks.

A normal VM reboot leaves a real daemon's partially checkpointed request.
The observer SIGKILLs and waits for the daemon, records catalog/staging hashes
and WAITING rows, then reboots. Before starting the product in the new boot,
the same bytes/rows must remain. Startup changes the old request to INTERRUPTED
without moving its deadline, performs no automatic GET, and rejects the old
epoch/token. A new explicit resume retrieves only the remainder; actual CLI
read verifies all bytes and the installed console entrypoint runs successfully.

The other retained normal reboot case covers an already durable PUBLISHING
intent in the formal core. Neither scenario runs a product cleanup before
shutdown. There is no systemd product autostart in either case.

## Attempts and corrections

- Milestone attempt 01 passed an intermediate 55-test cohort. It preceded the
  nonblocking-connect fixture and is superseded; raw records remain local.
- Attempt 02 failed Linux `test_slow_headers_total_deadline`: request result
  was FAILED instead of TIMED_OUT. A deadline-triggered socket shutdown could
  surface as an HTTP parser exception before the sweep recorded expiry.
  [Failed JSON](../evidence/milestone1-attempt-02.json) is preserved, with its
  failed stage and verified VM/disk cleanup. Timing bounds were not relaxed.
- The fix rechecks original interest/deadline on the official error path and
  adds a regression with a delayed sweep. Attempt 03 passed 57 tests; attempt
  04 passed 58 after managed/unversioned-interest protections. Both are
  superseded by the final source (shutdown admission has an additional test).
- Attempt 05 passed 59 tests, but a follow-up lifecycle check found the body
  watcher could stop prematurely when http.client detached a Connection: close
  wrapper. Its slow-body fixture sent 1024 bytes per 30 ms, allowing a buffered
  read to finish without proving the watcher stayed alive. The actual pre-fix
  [observation](../evidence/milestone1-body-watch-review.json) records an unfinished
  response with the watcher already done. The final implementation retains a
  separate cancellation socket handle until body cleanup. The fixture now sends
  **one byte per 30 ms**, and an additional direct transport regression checks
  watcher lifetime/body interruption. No timeout threshold was increased.
- The final run is used because its hashes match the committed runtime; no
  fastest-run selection or speedup ratio is used.
- Attempt 06 passed 60 tests after the body-watch fix. Final transaction-boundary
  checks add real SQLite BEGIN lock waits and a delayed precharge test: admission
  and publish intent must recheck duration after waiting, and payload writes
  must recheck after accounting. Test-only busy_timeout is raised to ensure the
  lock releases after the request deadline rather than failing early as BUSY;
  product timeouts and all existing observation bounds are unchanged.

## Measurement interpretation and remaining work

Deadline cases record submission-to-terminal time and normal stop time, plus
actual daemon PID current RSS. Linux reads /proc, macOS reads ps. These samples
exclude the observer/TLS origin and differ from `native_cases`/`linux_cases`,
whose legacy core timings and whole-observer memory remain separately labelled.
No daemon throughput, long-term RSS stability or cross-platform speed ranking
is inferred from these point samples. Test observation bounds are not an SLA.

The fixed deadline covers pre-publication authority and network interest;
blocked synchronous kernel/fsync/SQLite I/O may delay response. Committed
publication cannot be undone by cancellation or a client timeout. See SERVICE.

Still pending: official systemd installation/autostart and restart-limit
qualification, real-model/provenance demonstration, clean installation runbook
qualification, operational log/headroom policy and bounded sustained operation.
History remains finite (64 manifest revisions, 128 IDs, default 256 requests),
with no rollover or remote API. No sudden power-loss, suspend, hardware fault,
boot-zero-gap or long-term qualification is claimed. At that revision GitHub was Private
and no license had been selected. The current project is [MIT licensed](../LICENSE).
