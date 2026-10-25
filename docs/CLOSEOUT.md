# Bounded closeout and deployment acceptance

This iteration closes the existing scope. It adds no remote API, alternate
origin, automatic eviction/rollover or new product operation. The repository
remains Private and without a selected project license.

The current evidence is [JSON](../evidence/closeout.json) / [HTML](../evidence/closeout.html).
It must distinguish unit tests from installed-service observations and native
macOS from Linux VM resources. Earlier evidence retains the source hashes of
its recorded version; it is not silently relabeled as testing the current code.

## Acceptance fixed before observation

[contract.json](../closeout/contract.json) is an input to the run and report
verifier, not generated from measured maxima. The fixed workload is 90 cycles,
starting every two seconds over at least 180 seconds. Each cycle executes:

- Download and CLI read with an exact SHA-256 check; pin, verify GC excludes it,
  then unpin and explicitly reclaim it.
- Receive a real partial TLS body, expire the old request without extending its
  deadline, explicitly resume with a new ID, verify/read, then reclaim.
- Reach a held source with another request, cancel it, verify CANCELLED, then
  reclaim. Each cycle adds exactly four durable request records.

The first 30 seconds warm the process. Early medians use seconds 30–60 and late
medians seconds 150–180. All samples remain in the record, including warmup and
active transfer phases. No samples or failed cycles are selected out.

| Resource gate for this workload | Fixed threshold |
|---|---:|
| Daemon current RSS, any sampled point | 96 MiB |
| Late minus early median RSS | 8 MiB |
| Consecutive 30-second median increases exceeding 1 MiB each | At most two |
| Sampled daemon FD count / median increase | 24 / 2 |
| Sampled daemon thread count / median increase | 16 / 1 |
| Cache metadata allocated bytes | 8.25 MiB |
| Logical payload admission budget | 2 MiB |
| Scheduled cycle start lag | At most 5 seconds |

These are engineering gates for this bounded workload, not universal resource
limits, proof of zero leaks or cgroup enforcement. Samples approximately every
250 ms can miss short peaks. The record includes RSS window medians and the
actual increase, even when it passes. Catalog growth from retained request
history is expected and reported separately from payload reclamation.

The service must keep the same MainPID, kernel process-start ticks, instance
epoch and systemd InvocationID for the entire soak, with NRestarts remaining
zero. Root observer/source/CLI processes are excluded from service RSS, FD and
thread counts. CLI read output/plan files live outside the cache root so they
cannot be mistaken for product metadata. Transient file moves/unlinks make the
sampled directory footprint non-atomic; settled payload checks use the formal
usage API after explicit GC.

## Clean installation and operator workflow

The disposable Linux VM has no host mounts. The observation environment is
separate from the production venv. Before running the documented installer,
the production venv, account, unit, config and state directory must be absent.
The installer must then create them, and installed module/unit bytes must match
the wheel and recorded source. Runtime source is outside test import reach.

The observer executes these documented interfaces against the official unit:

```sh
sudo sh deploy/install.sh /absolute/cacheweft-0.0.1-py3-none-any.whl /absolute/config.json
sudo systemd-analyze verify /etc/systemd/system/cacheweft.service
sudo systemctl start cacheweft.service
sudo -u cacheweft /opt/cacheweft/venv/bin/cacheweft --socket /var/lib/cacheweft/control.sock info
sudo -u cacheweft /opt/cacheweft/venv/bin/cacheweft --socket /var/lib/cacheweft/control.sock fetch run_001 a --seconds 5
sudo -u cacheweft /opt/cacheweft/venv/bin/cacheweft --socket /var/lib/cacheweft/control.sock status run_001
sudo -u cacheweft /opt/cacheweft/venv/bin/cacheweft --socket /var/lib/cacheweft/control.sock read DIGEST --output /owned/output.bin
sudo -u cacheweft /opt/cacheweft/venv/bin/cacheweft --socket /var/lib/cacheweft/control.sock pin DIGEST --value true --request run_001
sudo -u cacheweft /opt/cacheweft/venv/bin/cacheweft --socket /var/lib/cacheweft/control.sock gc-plan
```

The VM fixture supplies trusted hashes and a local TLS CA for three deterministic
artifacts (two 1 MiB, one 2 MiB). The observer uses `runuser -u cacheweft --` as
the root VM equivalent of the shown `sudo -u`; it does not bypass the CLI or
invoke an in-process replacement cache. A custom CA lives under `/etc/cacheweft`,
not the service's private `/tmp` or protected home directories. The service
runs as the dedicated account, with no capabilities and NoNewPrivileges.

`cancel`, `resume`, unpin, GC plan/run, stop and repair are also exercised. The
JSON retains command arguments, exits and errors for the operator/capacity
phases; repeated successful soak calls are summarized per cycle. Every
unexpected failure prevents pass and is retained. No observer writes SQL into
the catalog: read-only ledger queries only count/hash retained request rows.

## Real capacity limits, not simulated success

1. **Logical capacity:** preserve a pinned 1 MiB object; a new 2 MiB artifact
   cannot fit the 2 MiB budget. It must be refused before a request ID is
   admitted or anything is deleted. After explicit unpin/GC, the 2 MiB artifact
   must download and read correctly.
2. **Outstanding work:** hold two real TLS transfers with jobs=2. A third new
   job must be refused without a receipt, while cancellation of both existing
   requests remains usable. No test disables the configured limit.
3. **History:** actually fill all 512 configured slots using distinct accepted
   IDs. New IDs must be refused. Reads, pins and explicit GC must preserve the
   ledger; a normal stop/start must retain the same ledger hash and refusal.
   Offline configuration increase to 768 followed by restart may admit a new
   ID while retaining all 512 old rows. Old-epoch operations remain rejected.
   This buys only 256 additional slots; it is **not** reclamation or rollover.
   The maximum supported configuration remains 4096 records.
4. **Filesystem ENOSPC:** stop the service and use a separate, disposable 8 MiB
   tmpfs backing its existing StateDirectory path. Keep a pinned verified
   object and fill that filesystem until exactly 32 KiB remains (less than one 64 KiB payload chunk). Admit and checkpoint the transfer behind a controlled body gate before filling,
   then release the remainder. Trace the
   actual service and require a payload staging write to return ENOSPC;
   a root-side probe or injected Python exception is insufficient. The new
   object must remain unavailable, new work/read must fail under degraded
   authority, and the pinned bytes must remain intact. After stopping, removing
   only the filler and restarting, the old unfinished request must become
   INTERRUPTED with its old deadline. Explicit new resume/read must succeed;
   old-epoch operations must fail. The fault filesystem is unmounted afterward,
   and the original catalog beneath it must retain the exact pre-fixture hash.

The full-filesystem case is a controlled tmpfs allocation failure, not damaged
physical storage, a host-disk-full incident or sudden power loss. Read-only
storage and SQLite page-cap errors remain separately scoped earlier/core tests;
this run does not pretend that its staging-write ENOSPC covers every I/O fault.
Capacity-repair restarts occur after the soak and are labeled separately; they
cannot be used to flatten the soak's resource trace.

## Generation-retirement correction

Code inspection found that finished generations retained Lease objects until
process exit. The finite request-history cap bounded their total number, so
this was not proof of unlimited growth, but retaining inactive transport
interests was unnecessary. [The reproduction](../evidence/closeout-lease-comparison.json)
uses installed before/after wheels, real TLS and a managed manifest. After
eight fetch/GC generations, the earlier code retained leases 1 through 8;
the corrected code retained zero after each generation, preserving all eight
request rows. This is supplemental core evidence, not a systemd measurement.

Completed futures now retire work-set membership and their lease after transport
cleanup. The callback checks ownership so an old callback cannot remove a newer
generation's lease or a same-generation resume's replacement lease. READY cache
hits create no transport lease. Three additional formal-core tests cover repeated
reclamation, a delayed retired worker with a new generation, and a delayed
callback while the same generation has resumed. Existing deadline, cancellation,
publication and restart tests remain in the cohort.

## Reproduce

Use Python 3.12+, a setuptools build environment and Colima/Lima, as described
in [LOCAL_VALIDATION.md](LOCAL_VALIDATION.md). The runner creates and deletes a
new `cw-exp-*` profile and data disk; it refuses existing profiles/output folders.

```sh
.venv/bin/python -m pip wheel --no-build-isolation --no-deps . --wheel-dir results/closeout-build
.venv/bin/python -m closeout.validate \
  --colima-home /absolute/colima-home \
  --toolchain /absolute/colima-bin-directory \
  --profile cw-exp-closeout-fresh \
  --wheel results/closeout-build/cacheweft-0.0.1-py3-none-any.whl \
  --output results/closeout-fresh
```

The fixed duration/thresholds cannot be overridden by a shorter CLI flag. Source,
wheel, installed module and unit hashes must agree. Both native macOS and Linux
run the same 74 identified tests, with no skips. Only the Linux VM executes
systemd and the fixed sustained workload. The verifier independently recomputes
window medians/resource gates and cycle accounting from raw samples; corrupted
record copies test rejection of skipped tests, timeouts, hidden restarts, altered
thresholds, lost history and fake disk failures. JSON and HTML use the same data.

## Remaining boundaries

This is a three-minute controlled operation and a finite history demonstration,
not a long-duration soak, an indefinite-service claim, automatic history rollover,
boot autostart/reboot qualification, power-loss recovery or boot-zero-gap proof.
Native macOS unit-test success is not evidence that the Linux systemd unit ran
on macOS. Earlier real-model results are independently versioned; they are not
pooled with this deterministic-payload workload to construct a speedup claim.

## Failed development attempts

[Attempt 01](../evidence/closeout-attempt-01.json) completed the fixed soak and
history checks but failed because the test expected the string DEGRADED where
the production mutation guard returns Authority unavailable. The explicit
info.degraded check and rejection requirement were retained; the literal was
corrected. [Attempt 02](../evidence/closeout-attempt-02.json) did not obtain the
required payload-write ENOSPC witness with 64 KiB free, so it also failed.
Its full-filesystem result must not be cited as that witness. The revised fault
fixture checkpoints a real request before filling, leaves 32 KiB (below a
64 KiB chunk), and requires the actual traced staging-write error. These changes
strengthen fault-boundary evidence; the soak duration and resource gates remain
unchanged. The final clean-install contract additionally requires an absent
production venv/account/unit/config/state before the documented installer runs.

## Recorded result

The final clean-install record passed all 13 stages without timeout. Native macOS
and Linux each passed **74 identified tests**, with no failures, errors or skips.
The same corrected core/wheel was used by the observer and installed service.
The verifier rejected **21 corrupted record copies**. Normal and optimized
Python verification both pass.

The service completed **180.016 seconds / 90 cycles / 360 admitted requests**
without a changed PID, start ticks, epoch, InvocationID or systemd restart count.
The raw record contains 714 daemon samples.

| Resource | Early median | Late median | Sampled maximum |
|---|---:|---:|---:|
| RSS bytes | 26193920 | 26275840 | 26378240 |
| File descriptors | 6 | 6 | 10 |
| Threads | 4 | 4 | 6 |

RSS median increased by **81920 bytes (80 KiB)**; it was not perfectly flat.
FD and thread medians remained unchanged. These observations pass the fixed
gates but do not rule out smaller leaks or establish a long-term plateau.

Sampled metadata allocation grew from 36864 to
163840 bytes as 360 request records were retained.
Sampled metadata maximum was 184320 bytes; sampled payload maximum was
1048576 bytes. Every cycle explicitly reclaimed payload; it did
not erase history.

The history test reached exactly 512 records, actually evicted the explicitly
unpinned object, retained the same ledger hash after GC and restart, and still
refused new IDs. The historical request remained SUCCEEDED but unavailable.
Explicit capacity increase to 768 preserved all 512 rows and admitted one new
request, ending at 513. This is finite operational headroom, not rollover.

The real disk-fault witness includes a staging write returning ENOSPC in a
service worker. It caused degraded authority with the request still unfinished,
no READY target and no new work. Pinned bytes survived. Repair/restart preserved
the old deadline and marked the old request INTERRUPTED; fresh resume/read
succeeded and stale authority was rejected. The original catalog under the
fault mount was unchanged. Service/source/mount and VM/data-disk cleanup passed.

[Attempt 03](../evidence/closeout-attempt-03.json) was deliberately interrupted
while strengthening the history test from a no-op pinned GC to an actual
eviction with history still full. It is failed/aborted evidence, not a completed
soak. The final run restarted from a fresh VM; no resource gate was relaxed.
