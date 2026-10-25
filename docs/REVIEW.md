# Exploration review

Historical review at 61739b6. Evidence in this document refers to
[archived exploration JSON](../evidence/explorations-reviewed.json) and HTML.
Current milestone implementation/evidence is described in MILESTONE1.

Scope: formal minimal runtime, tests, four exploration fixtures, raw VM logs,
JSON/HTML consistency and stated limitations. Two read-only reviewers made
independent first passes on logic and evidence. The author reproduced the
counterexamples, implemented fixes and ran the new qualification. Reviewers
did not independently reproduce the complete VM suite.

## Findings and corrections

1. **Complete checkpoint recovery (P1).** The old fixture returned an invalid
   empty 206 for `bytes=size-`. Real TLS reproduction with a 416 response caused
   a fully downloaded artifact to become FAILED after restart. The core now
   seals/verifies/publishes a complete checkpoint locally before considering
   network attempts. The fixture returns compliant 416 at EOF. New regressions
   cover exhausted attempt budget, absent ETag and same-size corruption; the
   latter must fail hash verification without publication.
2. **Local storage errors misclassified as network retries (P2).** A controlled
   EDQUOT at the formal write boundary previously exhausted transfer budget
   without entering DEGRADED, and GC still accepted mutation. Local controller
   file/catalog operations now retire authority on OSError/SQLite errors,
   regardless of errno; transport operations remain outside that context.
   EDQUOT/EACCES regressions verify no network retry and refusal of mutations.
   These are injected errors, not a real quota filesystem qualification.
3. **Coalescing observation ambiguity (P2).** Delaying leader submit until it
   completed made the old measurement accept three later warm hits as a
   four-subscriber coalesced case. The revised fixture holds the response until
   four persisted WAITING requests share one generation, then records all four
   successful outcomes and the single GET. The controlled hold is included in
   completion time, so no cold/coalesced speed ratio is claimed.
4. **SQLite FULL scope (P2).** The old page-cap case measured catalog admission
   after an artifact was already READY. It did not prove prevention of an
   interrupted publication. That admission case remains explicitly scoped.
   A separate test-only allocation trigger now causes actual SQLITE_FULL at
   the production PUBLISHING transaction under a 16-page cap. It must roll back
   to VERIFYING/WAITING, leave no ready target and reject new work. Offline
   trigger removal/restart completes that exact request without an origin GET.
5. **Evidence validation gaps (P2).** Modified JSON copies with infinite
   latency, duplicate modes, missing native stage, empty provenance, missing
   usage or negative memory/allocation were previously accepted. The v3
   contract requires the complete frozen input set/runtime module set, finite
   timings, mode-specific counts/ranges, metric scopes, usage values and the
   concurrent subscriber witness. Boot proof now includes both actual catalog
   hashes, staging hashes, retained states and recovered request identity.

[Controlled before-fix observations](../evidence/review-findings.json) refer to
commit `db72d1d`. They are synthetic counterexamples or injected faults, not
unreported VM incidents. [Initial JSON](../evidence/initial-01.json) and HTML
are preserved byte-for-byte; the reviewed run supersedes their pass claim for
the affected behaviours.

## Evidence and limits

Archived reviewed JSON/HTML were generated from the same data. Both platforms executed the
same identified 42 tests; the Linux guest uses installed wheel code after
runtime source is removed from import reach. Runtime bytes, tests/tools/build
input fingerprints and wheel digest are recorded. Report rejection controls
also run under optimized Python; they are not additional implementation tests
or a cryptographic authenticity guarantee.

The normal reboot test kills the child with `os._exit(73)` before shutdown,
retains PUBLISHING/WAITING state, verifies catalog/staging hashes before formal
recovery in a new kernel boot, then validates the resulting bytes. This rules
out product shutdown cleanup as its explanation. It tests manual core recovery
after normal reboot, not systemd autostart or power loss.

Current measurements are local TLS, deterministic 1 MiB data, one observation
per mode/environment. macOS peak and Linux current RSS include fixture/observer
allocations; there is no isolated-daemon or cross-platform speed claim. Long-term
operation, total slow-trickle deadline, real model use, real disk quota and
sudden power-loss recovery remain unqualified. Repository stays Private with
no selected license.
