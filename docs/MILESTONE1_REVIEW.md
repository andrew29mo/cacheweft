# Milestone 1 review: SQLite commit fencing and subscriber deadlines

This is the current qualification. [The initial milestone record](MILESTONE1.md)
and its original JSON/HTML remain archived unchanged. The repository remains
Private with no selected license.

Two read-only reviewers independently examined runtime correctness and evidence.
The author then reproduced the findings with the supported project Python and
actual TLS/SQLite, implemented fixes and executed the fresh VM qualification.
Reviewers did not independently reproduce the complete VM run. Their agreement
is not a replacement for those execution records.

## Findings

1. **COMMIT could cross the request deadline (P1).** The old code checked after
   BEGIN but let SQLite's internal busy handler wait at COMMIT. A shared reader
   held for 150 ms, with the original 200 ms SQLite timeout and a 100 ms request,
   let a cached admission return SUCCEEDED after about 209 ms. Publication
   intent likewise completed after the short cutoff. This was lock waiting,
   not a qualified guarantee about blocking storage/fsync.
2. **A live long subscriber could mask an expired short subscriber (P1).**
   The old transaction checked that at least one subscriber remained live,
   without expiring each member after waiting. A controlled writer lock held
   for 700 ms let short and long both become SUCCEEDED, with intent about
   320 ms past the short deadline. That baseline used the test's explicit
   2000 ms busy timeout; new regressions cover the product's default 200 ms.
3. **Test-name substitution passed the report contract (P2).** Two JSON copies
   could carry the same number of unrelated test IDs and still pass validation.
   This did not invalidate the original real test logs, but it weakened the
   claimed binding between source, cohort and results.

[Before-fix observations](../evidence/milestone1-review-findings.json) identify
commit 705c510. These are controlled contention and modified-report cases,
not unreported VM incidents or WAN measurements.

## Corrections

Deadline-sensitive transactions temporarily set the SQLite busy handler to zero.
Explicit BEGIN/COMMIT retries keep the original busy budget and check the fixed
deadline before issuing each SQL attempt, including after validation. Expiry
rolls back the uncommitted transaction. The prior busy timeout is restored on
success and every exception path.

Publication also expires individual WAITING subscribers inside that transaction
on every COMMIT retry. If updating a subset consumes more time, the fence reads
again. A timed-out short member remains TIMED_OUT while a valid long member can
complete the exact content. A transaction with no live interest cannot publish.
Revision changes are checked once per terminal transition, not per retry.

The verifier now embeds the exact ordered 68-test cohort and the complete
21-input set. It rejects replacement of both test-ID lists, rather than relying
on equal counts. Controlled changes also challenge late admission, revived
short results and missing contention evidence.

## Verification coverage

The new fixtures hold real SQLite reader/shared or writer/reserved locks while
the formal HTTPS path runs. Admission uses the actual daemon. Publication
uses the exact managed backend with a scheduling barrier at verification;
that barrier is not exposed by the daemon API. No replacement downloader,
publication algorithm or simulated database exception is used.

Tests cannot pass merely because the short request expired too early. Holder
acquisition must finish before its cutoff, and the formal SQL path must record
an actual SQLITE_BUSY event before cutoff. Admission requires a positive daemon
COMMIT-counter change. These process-local saturated counters are diagnostic;
they reset on startup, do not authorize work and are not durable audit logs.

The added checks cover:

- Real daemon cache-hit admission blocked by a reader at COMMIT: rejection,
  no persisted request receipt, healthy authority and a later successful hit.
- A sole subscriber at publication COMMIT: TIMED_OUT with no published intent.
- Short/long shared subscribers crossing writer BEGIN and reader COMMIT waits:
  only long succeeds, original short deadline remains fixed, and bytes match.
- A delayed validation callback: no committed probe, no leftover transaction,
  and the original busy timeout restored.

Existing tests are rerun, including actual daemon/CLI, immutable manifest
revisions, slow headers, one-byte slow body, Connection: close watcher lifetime,
queued expiry, cancellation, SQLite FULL and the TCP blackhole fixture. Two
normal VM reboot cases retain the pre-recovery catalog/staging bytes. The
bounded daemon case keeps its old request INTERRUPTED and rejects old tokens;
only a new explicit resume retrieves the remainder.

## Recorded final qualification

Native macOS and isolated Linux each passed **68 identified implementation tests**,
with zero failures/errors/skips. **42 modified report copies** were rejected;
these are observer controls, not additional implementation tests. The exact
ordered cohort, 21 frozen inputs and installed wheel/module hashes are bound
by the v5 report contract.

| Regression | Native macOS | Isolated Linux VM |
|---|---|---|
| Actual daemon admission at COMMIT | Rejected, no receipt, real COMMIT BUSY | Rejected, no receipt, real COMMIT BUSY |
| Sole subscriber publication | TIMED_OUT, no intent | TIMED_OUT, no intent |
| Shared writer BEGIN wait | Short TIMED_OUT, long SUCCEEDED | Short TIMED_OUT, long SUCCEEDED |
| Shared reader COMMIT wait | Short TIMED_OUT, long SUCCEEDED | Short TIMED_OUT, long SUCCEEDED |

Both shared cases prove contention started before the short cutoff and intent
completed after it, while only the long subscriber succeeded with correct bytes.
All cases use the default 200 ms SQLite busy budget; backend cases explicitly
check its restoration after the decision.
Both normal VM reboot witnesses, old deadlines/tokens, process cleanup, owned
VM/data-disk removal and preexisting-profile checks passed. No timing average,
performance improvement or production recovery bound is inferred.

## Evidence interpretation

The latest [JSON](../evidence/latest.json) and [HTML](../evidence/latest.html)
come from the same data. Linux imports the installed wheel after runtime source
is removed from import reach; native and installed module hashes match frozen
inputs. Detailed native/Linux review observations are under `native_review`
and `linux_review`, separate from legacy core timings and daemon RSS samples.

The first review qualification passed before explicit contention witnesses were
added. The final run is chosen because it matches the final implementation and
stronger fixtures, not because it has faster timings. Raw attempt logs remain
local under ignored results. A [native CLI smoke](../evidence/native-cli-smoke.json)
is supplementary and does not claim another Linux reproduction.

This is a software decision cutoff before a new COMMIT attempt and on BUSY
retries. An already-issued filesystem/fsync operation cannot be preempted and
may return late; no physical durability-time or hard realtime guarantee has
been measured. No systemd/autostart, sudden power loss, suspend, real-model use,
long-term rollover or sustained resource stability is qualified by this review.
