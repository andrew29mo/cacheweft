# Cacheweft

A bounded local HTTPS artifact cache with an unprivileged Unix socket daemon
and CLI. Downloads are checkpointed, verified against a trusted size/SHA-256,
then published immutably. Clients can share work, cancel their own request,
stream verified content, pin objects and explicitly reclaim space.

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

- [Milestone results, scope and remaining work](docs/MILESTONE1.md)
- [Architecture](docs/ARCHITECTURE.md) and [local VM reproduction](docs/LOCAL_VALIDATION.md)
- [Current JSON](evidence/latest.json) and [HTML](evidence/latest.html)
- [Earlier exploration review](docs/REVIEW.md) and [archived exploration JSON](evidence/explorations-reviewed.json)
- [Dependency/licensing status](docs/DEPENDENCIES.md)

The harness installs a wheel in a fresh isolated Linux VM, removes runtime
source from import reach, and runs the same identified tests as native macOS.
Daemon deadline cases use actual subprocesses and TLS sockets. Linux also tests
an unresolved-neighbor connect in an isolated network namespace. Normal VM
reboot retains old catalog bytes before the official daemon starts again.

All payloads are deterministic 1 MiB test data, not a real model. Daemon RSS
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
remote administration, external mmap lease, systemd/autostart, sudden power-loss,
suspend or boot-zero-gap qualification is included. A real-model example and
bounded sustained operation are later milestones.
