# Final recorded-evidence review

The final audit found no reproducible defect in the reviewed runtime change,
but found two classes of fail-closed evidence-validation gaps. They are fixed.
The review at `891495d` changed no product feature, license or visibility;
the repository was Private with no project license then. It remains Private
and is now [MIT licensed](../LICENSE). See the licensing update below.

[Qualification JSON](../evidence/final-review.json) and
[HTML](../evidence/final-review.html) bind the corrected validators to the exact
measured records at commit `c460d7de3dcd87c8cb9dc4cddfa398d47a35ee89`.

## Independent first pass

Two reviewers separately inspected runtime correctness and recorded evidence.
The runtime reviewer read the source/diff before the author's explanation and
ran nine targeted formal-core tests: completion-lease reclamation, old/new and
same-generation callbacks, late cancelled chunks, retired workers, cancellation
versus durable intent, partial GC crash, storage errors and request capacity.
No reproducible correctness regression was found. The nine tests were also
rerun during this final review and passed.

The evidence reviewer checked source/wheel/module hashes and raw guest readback
against published records. The measured model archives match `b3597f7` byte for
byte. Current closeout/model JSON and HTML match the `c460d7d` snapshots and
original result files. Neither reviewer reran a complete VM qualification.
Reviewer agreement is not a substitute for those recorded execution results.

## Confirmed validation gaps and fixes

1. **Sample count was insufficient coverage evidence.** A forged 600-element
   trace made from only five duplicated timestamps could pass after summaries
   were recomputed. Validation now requires finite, strictly increasing sample
   times, observations near the start/end, no gap exceeding four target sampling
   intervals (1 second), and coverage of every comparison window. This extra
   data-quality check was added during review; the original RSS/FD/thread and
   duration thresholds were not loosened.
2. **Missing/unknown/impossible values could pass.** Missing epoch, UNKNOWN
   fault-state, negative FD/thread summaries, matched negative model-process
   groups and matched UNKNOWN inference digests were accepted. Validation now
   requires valid identities, numeric domains, actual unfinished request state
   WAITING, and correctly formed digests/addresses. Unknown values do not stand
   in for successful observations.

The actual measurements did not contain these corruptions. The original
714-point soak spans approximately 0.00019 to 179.82582 seconds, with maximum
sample gap about 0.26735 seconds. All measured values satisfy the added checks.
There is one saved epoch: its presence/format is now required, while equality
at the end was checked by the original producer against the live service.
The record cannot independently reconstruct an unstored second epoch value.
The raw PID/start ticks and systemd invocation have separate continuity evidence.

## What was rerun

- Nine targeted runtime tests, without runtime source changes.
- Twenty-four review-specific tests, including sparse/duplicate/nonfinite
  traces, missing identities, negative values, invalid states/digests and stale
  qualification-output handling. Normal and optimized Python both passed.
- Corrected closeout/model corruption controls: **33 / 28 rejected copies**.
- Verification of both original JSON/HTML pairs, same measured wheel/runtime,
  historical archive equality, package contents and local privacy-pattern scans.

The review tests live under `review/`, separate from the measured 74-test
runtime cohort; these counts are not added together to inflate measurement
coverage. No VM, new model inference or performance measurement was executed
for these validator-only changes. The short soak and model results remain
observations of the measured runtime at `c460d7d`.

## Exact version bridge

The raw measured JSON/HTML files were deliberately **not rewritten**. Their
source maps still identify the code used when the measurements ran. The
qualification verifies those bytes against Git, verifies current runtime files
match the measured runtime, and records changed validator hashes separately.
The only producer edit removes trailing whitespace from `closeout/scenario.py`;
its Python AST and all non-trailing-whitespace bytes match the measured file.
It is not presented as having the same byte hash.

This explicit bridge avoids claiming that a changed validator was part of an
older measurement run. Fresh future VM runs use the stricter validators. A
qualification failure writes a failed result rather than leaving a previous
successful qualification looking current. Measured input files cannot be used
as the qualification output path.

```sh
.venv/bin/python -m review.validate --output evidence/final-review.json
.venv/bin/python -O -m unittest discover -s review -t .
```

The recorded Git commit must be available locally. This is offline source/data
qualification; it does not query GitHub or run a service.

## Repository and deployment boundaries

The GitHub read-only check during review found `andrew29mo/cacheweft` Private,
main as default branch, only the owner as collaborator and no selected license.
There are no tracked Actions workflows. Actions is enabled with read-only
default workflow token permissions and cannot approve pull-request reviews;
allowed-actions is currently all and SHA pinning is not required. These are
observed settings, not a claim that future public workflows are hardened.
Settings were not changed in this review.

The product wheel contains the five runtime modules and packaging metadata,
without tests, observers, model weights, databases, certificates or private keys.
Tracked-file/privacy-pattern checks did not find personal workspace paths or
credential/private-key material. This is a bounded inspection, not a security
certification. The subsequently approved MIT adoption is described below;
publication remains a separate decision.

The three-minute observation is not proof of no leaks, a long-term plateau,
automatic request-history rollover, power-loss recovery, boot autostart or
boot-zero-gap protection. GC does not reclaim request history, and raising its
configured cap provides finite additional headroom only.

## Subsequent MIT metadata update

MIT adoption adds `license = "MIT"` and `license-files = ["LICENSE"]` to the
package configuration and the root LICENSE. The version bridge now permits
exactly those two additions relative to the measured packaging configuration;
it rejects changes to dependencies, runtime/build requirements or the license
file selection. Three extra metadata guard tests bring the current qualification
to **27 review tests**; the original review's 24-test count above remains a
historical description. Both normal and optimized verification pass.

The current qualification hashes LICENSE and pyproject.toml as well as the
validators. Original measurement JSON/HTML remains unchanged. A newly built
MIT wheel/source distribution and a fresh install were checked separately in
[licensing.json](../evidence/licensing.json); their runtime bytes match the
measured version. No VM, model inference or performance run was repeated for
this licensing-only change. See [DEPENDENCIES.md](DEPENDENCIES.md) for independent
model and optional-library terms.
