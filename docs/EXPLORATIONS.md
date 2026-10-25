# Four explorations and MVP acceptance

This is a formal minimal core, not the complete planned product. Tests import
the same `cacheweft` package that the guest installs from a wheel. TLS origins,
fault barriers and temporary mounts are observer fixtures; they do not replace
the transfer/publication/GC implementation.

The initial qualification was reviewed and corrected. The original EOF-range
fixture masked a complete-checkpoint recovery defect; use the reviewed evidence
below, not the old pass flag, for that behaviour. See [review findings](REVIEW.md).

## Decisions reached

1. **Continuation:** Python's direct HTTPS client exposes untransformed response
   bytes. Exact single-range/ETag checks and complete trusted hash verification
   work with a controlled TLS origin. Weak/missing ETag restarts; invalid range,
   416, encoded or wrong-length responses fail. This narrow contract deliberately
   rejects other valid HTTP server behaviours (e.g. chunked-only responses).
   Complete local checkpoints skip the origin and are rehashed/published even
   if the last download attempt was consumed or no ETag exists.
2. **Sealing/publication:** synchronous writes under authority close their FDs
   before verification. Hard-link publication refuses an existing destination;
   persistent intent bridges file/catalog crash windows. Recovery verifies
   content rather than accepting a digest-shaped filename.
3. **Readers/eviction:** persistent request subscriptions, epoch/generation/
   revision tokens and live reader references serialize with GC. Cancelling a
   subscriber does not cancel other subscribers. Retired network work cannot
   write to or block a new generation; it still consumes a worker slot.
4. **Storage/recovery:** actual payload ENOSPC, EROFS via read-only bind and
   SQLite FULL retire mutation authority. FULL is tested both at catalog
   admission and at the formal publish-intent transaction. Explicit repair/restart completes the
   operation. Normal VM reboot retains the unfinished catalog byte-for-byte;
   formal core recovery then publishes correct content.

## Recorded attempts

- Attempt 01: failed at read-only fault setup. Remounting a tmpfs read-only
  while SQLite held a writable file failed (exit 32); this was not a passed
  read-only test. The VM/data disk were removed and previous profiles preserved.
  [Diagnostic JSON](../evidence/attempt-01.json).
- Attempt 02: an intermediate 34-test/platform qualification passed, including
  actual storage faults and normal reboot. Raw evidence remains local in
  ignored results. It is superseded by the final run.
- Attempt 03: 36 tests/platform and strengthened boot-state/report checks
  passed. It is likewise superseded. A subsequent source review found a missing
  staging-file cleanup in DELETING recovery, requiring a new regression/run.
- Initial 37-test attempt: [archived JSON](../evidence/initial-01.json) and
  [HTML](../evidence/initial-01.html). These are unchanged historical records;
  their complete-checkpoint and coalescing evidence had the review gaps noted above.
- Reviewed final attempt: [latest JSON](../evidence/latest.json),
  [HTML](../evidence/latest.html). The exact frozen inputs, test IDs, module
  fingerprints, stage outcomes, measurements and cleanup are recorded there.

These are observations from the author-run harness; no independent VM
reproduction or hosted CI is implied.

## Fault recovery matrix

| Interrupted condition | Required result | Evidence |
|---|---|---|
| File fsync before checkpoint commit | Trim uncommitted tail, restart/resume safely | formal crash-window test |
| Checkpoint committed | Correct Range from persisted offset | TLS origin request log |
| Complete checkpoint at last attempt, with/without ETag | Local hash and publish, no GET | formal regression tests |
| File shorter than checkpoint | Restart from zero | formal test |
| Verify/publish windows | Complete hash before READY, no half artifact | eight crash positions |
| Final subscriber cancelled, old chunk returns | No write/publication | delayed chunk test |
| Old generation delayed while new generation starts | New result remains correct | interleaving test |
| GC intent before unlink, including staging remainder | Complete owned deletion, keep request history | formal abrupt-exit test |
| GC plan precedes read/pin or instance restart | Reject stale deletion | reference/token tests |
| Disk full / read-only / SQLite FULL | No false success; deny mutation, explicit repair | actual isolated Linux fault suite |
| EDQUOT/EACCES at controlled write boundary | No network retry; DEGRADED rejects mutations | injected-error regression, not real quota device |
| Normal VM reboot with PUBLISHING intent | Catalog unchanged before recovery; correct final bytes | before/after boot IDs and catalog hash |

Crash-window children terminate via `os._exit(73)` at test barriers. This is
abrupt process termination without Python cleanup, not an electrical power cut
and not literally a SIGKILL signal. Normal VM reboot is separate evidence.

## Measurement interpretation

Final qualification: **42 implementation tests on each of native macOS and
isolated Linux**, zero failures/errors/skips. Twenty-six modified
evidence copies were rejected by the report contract; these are observer
controls, not additional implementation tests.

| Environment | Cold complete | Interrupted/resumed complete | Four-subscriber complete | Memory observations |
|---|---:|---:|---:|---|
| Native macOS | 38.82 ms | 41.25 ms | 31.36 ms | Whole-observer peak RSS: 31.25–32.69 MiB |
| Isolated Linux VM | 92.07 ms | 91.58 ms | 42.30 ms | Whole-observer current RSS: 26.62–26.82 MiB |

Each final cache contains 1,048,576 logical payload bytes and zero remaining
reservation. Complete cache-file allocation was 1,069,056 bytes on macOS and
1,069,056–1,073,152 bytes in Linux (metadata/block rounding differs).
All three modes sent 1,048,576 origin body bytes; resumed mode used two GETs,
with the second starting at 131,072, while the four-subscriber mode used one.

These are separate observations, not a macOS-versus-Linux performance ranking.
The latest run was selected because it matches the final source, not because
it was the fastest. Single observations do not establish a stable latency.

The fixture is deterministic 1 MiB binary data, not a real model. Each mode has
one observation per environment, with a fresh cache directory and the same
content: cold, interruption after 128 KiB, and four coalesced subscribers.

Cold completion includes TLS connection, checkpoint fsyncs, full verification
and publication. Read timing is a subsequent warm read, not download latency.
Coalescing is verified by four WAITING subscribers of one generation before
the origin response is released, then four successful outcomes and one GET.
Its completion time includes that controlled barrier and all four submissions;
it is not a speedup comparison against the unblocked cold path.
Continuation is verified by an initial GET plus `bytes=131072-` and exactly
1 MiB of origin body sent across the two requests. No timing-derived speedup
or confidence interval is claimed.

Linux memory is current RSS for the observer process; macOS uses ru_maxrss peak.
Both include the core, TLS origin fixture and observer allocations. These are
not isolated daemon RSS, whole-VM memory or interchangeable memory metrics.
Per-root allocation excludes TLS fixture files and the guest image. Logical
payload, remaining reservation, allocated payload, complete cache-file
allocation and filesystem free bytes are distinct fields.

## Remaining MVP acceptance (not passed yet)

- Installed, unprivileged daemon with Unix socket API/CLI and explicit framed
  request/response limits; systemd start/stop/crash behaviour.
- A total network deadline that defeats slow header/body trickle; cancellation
  must remain safe while its transport is terminated. Current socket inactivity
  timeout is not that guarantee.
- A real redistributable small model demonstration, exact provenance/license,
  independent load/inference after download.
- Explicit operational storage headroom, bounded log/control metadata policy
  and configuration validation. Logical admission is not a physical quota.
- A persistent manifest revision policy for administrator changes between
  instances. Current request IDs bind full parameters across restart, but
  manifest name mapping itself is fixed only for the current instance.
- A bounded sustained run without restart and repeated observations if a
  performance comparison is to be published.

No automatic history rollover, remote management, multi-source support, NFS,
external mmap leases, power-loss, WAN availability, long-term endurance or
boot-zero-gap guarantee is included in this milestone.

## Technical basis

- [HTTP If-Range and Range](https://www.rfc-editor.org/rfc/rfc9110.html)
- [File and directory synchronization](https://man7.org/linux/man-pages/man2/fsync.2.html)
- [Hard-link creation](https://man7.org/linux/man-pages/man2/link.2.html)
- [Unlink and open descriptors](https://man7.org/linux/man-pages/man2/unlink.2.html)
- [Process lock lifetime](https://man7.org/linux/man-pages/man2/flock.2.html)
- [SQLite atomic commit](https://www.sqlite.org/atomiccommit.html)

These describe primitives; they do not replace the recorded platform tests.
