# Application integration: cached model → HTTP inference

This native macOS pilot starts the existing, unmodified
[tiny-batch-service](https://github.com/andrew29mo/tiny-batch-service/tree/31e9e720418c09e4a1438db07df4d55bfbe59693)
from a Cacheweft-managed model. It demonstrates an application startup boundary,
not external adoption, production readiness or an inference performance gain.

## Startup contract

The optional repository adapter `integrations/launch_batch.py` uses the installed
Cacheweft CLI to fetch an administrator-manifest artifact under a request ID and
fixed download deadline. It waits for SUCCEEDED plus available content, compares
the result with the application's trusted release SHA-256, explicitly pins it,
materializes a local file and verifies that file before replacing itself with
the existing inference service. Failed preparation never starts the service.
The adapter is not included in the Cacheweft runtime wheel.

With an already running Cacheweft daemon, a trusted manifest entry, and the
consumer's documented environment installed:

```sh
python3.12 integrations/launch_batch.py \
  --cacheweft /absolute/cache-env/bin/cacheweft \
  --socket /absolute/cache/control.sock \
  --artifact digits-v1 --digest TRUSTED_64_CHARACTER_SHA256 \
  --request-id application_start_001 --seconds 30 \
  --model-dir /absolute/private/application-models \
  --service /absolute/consumer-env/bin/tiny-batch-service \
  --port 8080 --batch-size 4 --capacity 16
```

Replace the digest and paths; the displayed digest is deliberately a placeholder.
Use a private, application-owned model directory. The consumer listens on
loopback and has no authentication; this does not expose a remote inference API.
The download deadline is not a bound on all local disk I/O or service startup.

## Reproduce the pilot

Install Cacheweft into an isolated environment, and set up tiny-batch-service
according to that repository's instructions. From this repository, run:

```sh
python3.12 -m integrations.try_batch \
  --cacheweft /absolute/cache-env/bin/cacheweft \
  --consumer /absolute/tiny-batch-service \
  --output /absolute/pilot.json
```

The runner requires a clean consumer checkout with its `.venv` installed. It
runs that project's official frozen-model exporter into a temporary directory,
checks the installed consumer modules against its Git commit and the installed
Cacheweft modules against this checkout, then uses a controlled loopback HTTPS
origin. It makes real CLI calls and real HTTP inference requests; it does not
substitute a mock cache or model backend. Temporary certificates, databases,
model files and input/reference arrays are removed after the run.

## Observed results

[Recorded JSON](../evidence/batch-integration.json) binds the adapter source,
installed runtime module hashes, consumer commit and model digest.

- Cold startup fetched and pinned the 9,890-byte Digits model. Seven HTTP
  responses matched the consumer's independently generated reference at
  `rtol=1e-4, atol=1e-5`; maximum absolute probability error was
  `5.960464477539063e-08`.
- After stopping the origin and restarting Cacheweft, a fresh consumer worker
  loaded the same model digest. All seven responses matched again, with zero
  additional recorded transfer attempts or accepted body bytes. This pilot did
  not trace system calls; source shutdown and formal transfer counters support
  the offline claim.
- A wrong application release digest failed before the consumer started.
- After explicit unpin and GC, replaying the old successful request ID failed
  because its content was unavailable; historical success was not treated as
  current availability.
- Both consumer workers exited after graceful shutdown. Cacheweft exited
  normally and removed its socket and readiness file.

## Operational friction and limits

A pin is a **shared boolean**, not a consumer lease. The adapter deliberately
leaves it set after exit or later failure. The operator must coordinate retirement,
explicit unpin and GC; automatically unpinning on one process's exit could remove
another owner's protection. The materialized application copy is outside cache
accounting and GC and must be removed separately after the service retires.

A fresh request ID consumes finite persistent history. Reusing an ID preserves
its original outcome/deadline; it does not redownload GC'd content. Interrupted
requests require explicit recovery with a new ID. No history rollover is added.

The application loads a separate model copy: this is not zero-copy sharing.
Only seven selected test rows per startup were checked, not the complete
450-row test set or a new accuracy benchmark. The model is too small to qualify
range continuation; the separate [SqueezeNet demonstration](MODEL_DEMO.md)
provides that evidence. This integration adds no VM, WAN, throughput, latency,
RSS, long-term, power-loss or boot-zero-gap qualification.

## Third-party attribution

The consumer's code and frozen model carry its MIT notices; see its
[third-party notices at the tested commit](https://github.com/andrew29mo/tiny-batch-service/blob/31e9e720418c09e4a1438db07df4d55bfbe59693/THIRD_PARTY_NOTICES.md).
The Digits subset derives from Optical Recognition of Handwritten Digits,
E. Alpaydin and C. Kaynak (1998),
[UCI / DOI 10.24432/C50P49](https://doi.org/10.24432/C50P49),
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
The consumer uses fixed selection, normalization and generated reference
probabilities; this pilot samples seven rows and records aggregate errors only.
No weights, pixels, labels or reference arrays are bundled here. External
inference libraries retain their own licenses and are not Cacheweft dependencies.

## Application review

The reviewed rerun retains the same four application cases and probability
error. It fixes two runner failure paths: preflight errors now overwrite any
previous successful output with `passed:false`; readiness exceptions clean up
a newly started daemon before ownership transfers to the outer runner. These
were evidence-tool defects, not changes to Cacheweft's runtime or the consumer.

Run `python3.12 -m unittest integrations.test_failure_paths -v` for the two
regressions. They challenge stale report replacement with an actual failing
preflight, and inject a query timeout while a real child process is running to
verify cleanup. They are separate from the full installed-service pilot and do
not substitute for it. Both also pass under `python3.12 -O`.
[The review record](../evidence/application-review.json) binds these test sources
and the rerun JSON. The existing 27 evidence-validation tests and 61 corruption
controls passed again; the earlier VM measurement files remain unchanged.
