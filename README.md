# Cacheweft

A bounded local HTTPS artifact cache with an unprivileged Unix socket daemon
and CLI. Downloads are checkpointed, verified against a trusted size/SHA-256,
then published immutably. Clients can share work, cancel their own request,
stream verified content, pin objects and explicitly reclaim space.

**Current scope:** [bounded closeout](docs/CLOSEOUT.md) checks clean deployment,
180 seconds of same-process operation, capacity refusal and explicit repair.
A [real-model demonstration](docs/MODEL_DEMO.md) verifies
cold download, interrupted continuation, offline read and independent model
execution. Linux systemd deployment adds unprivileged start/stop,
readiness notifications, crash takeover and bounded rapid restart attempts.
[Deployment and repair guide](docs/DEPLOYMENT.md).

**Milestone 1:** persistent manifest versions, fixed operations and resources,
per-request total download deadlines, daemon/CLI and conservative restart
recovery. GitHub remains Private; no license has been selected or granted.

## Install and run

Python 3.12+ and a local POSIX filesystem supporting flock, hard links and
directory fsync are required. Runtime Python dependencies: standard library only.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install .
chmod 600 config.json
.venv/bin/cacheweft serve --root /absolute/private/cache --config /absolute/config.json
```

Run `serve` as an ordinary account; root is rejected. The configuration must
be an owned regular file with no group/other permissions. For configuration,
CLI commands and the complete operation/deadline contract see
[the service guide](docs/SERVICE.md).

```sh
.venv/bin/cacheweft --socket /absolute/private/cache/control.sock info
.venv/bin/cacheweft --socket /absolute/private/cache/control.sock fetch request_001 model-v1 --seconds 30
.venv/bin/cacheweft --socket /absolute/private/cache/control.sock status request_001
.venv/bin/cacheweft --socket /absolute/private/cache/control.sock cancel request_001
```

Fetch returns admission/status rather than waiting for a completed download.
On restart, unfinished bounded requests become INTERRUPTED; continuation needs
an explicit new request ID and fresh deadline. Committed publication intents
are recovered to completion. Old requests and deadlines are never rewritten
into a new download attempt.

## Evidence

- [Final independent review and version bridge](docs/FINAL_REVIEW.md)
- [Corrected-validator qualification JSON](evidence/final-review.json) and [HTML](evidence/final-review.html)

- [Current bounded closeout JSON](evidence/closeout.json) and [HTML](evidence/closeout.html)
- [Closeout procedure, fixed gates and limits](docs/CLOSEOUT.md)
- [Reviewed milestone results, scope and remaining work](docs/MILESTONE1_REVIEW.md)
- [Initial milestone record](docs/MILESTONE1.md)
- [Architecture](docs/ARCHITECTURE.md) and [local VM reproduction](docs/LOCAL_VALIDATION.md)
- [Real-model JSON](evidence/model-demo.json) and [HTML](evidence/model-demo.html)
- [Deployment JSON](evidence/deployment.json) and [HTML](evidence/deployment.html)
- [Previous download/deadline review JSON](evidence/latest.json) and [HTML](evidence/latest.html) (commit `59860ca`; historical runtime snapshot)
- [Earlier exploration review](docs/REVIEW.md) and [archived exploration JSON](evidence/explorations-reviewed.json)
- [Dependency/licensing status](docs/DEPENDENCIES.md)

The current closeout runner separates its observation environment from a clean
production installation, removes runtime source from test import reach, and
runs the same 74 identified tests on native macOS and Linux. Linux additionally
executes the installed systemd service and fixed 180-second workload. The
earlier deployment record at `5404e8a` measured about 1.09 seconds from SIGKILL
to a new running invocation plus live RPC; that is a historical single
observation, not a current recovery bound. Historical download/deadline evidence
at `59860ca` separately includes unresolved-neighbor namespace and normal VM
reboot tests of manually launched daemons; it does not prove systemd autostart.

Earlier benchmark payloads are deterministic 1 MiB test data. The later model
demonstration uses a pinned 4.96 MB SqueezeNet model with synthetic inference
inputs; it does not measure classification accuracy. Daemon RSS
samples are distinct from the older whole-observer measurements. These are
controlled observations, not WAN speed, realtime recovery or long-term claims.

## Boundaries

Trusted administrator manifests determine expected bytes. Hash verification
does not prove publisher identity, model safety or model licensing. Same-UID
or root tampering is outside scope. Payload admission is logical budgeting,
not a filesystem quota. Request/manifest history is finite with no rollover.

Deadlines stop pre-publication authority; synchronous disk I/O can delay a
response. Once publication intent commits, cancellation is TOO_LATE and a
client timeout requires status reconciliation. No arbitrary external URL API,
remote administration, external mmap lease, qualified systemd autostart, sudden power-loss,
suspend or boot-zero-gap qualification is included. The three-minute bounded
observation does not establish long-term stability. GC does not reclaim request
history; no automatic history rollover exists.
