# Linux systemd deployment

The operational procedure remains applicable. The original qualification below
belongs to commit `5404e8a` (71 tests and its original runtime hashes). Check out
that revision to reproduce that exact historical runner. Current deployment and
74-test closeout evidence is in [CLOSEOUT.md](CLOSEOUT.md).


This milestone installs the official wheel under a dedicated unprivileged
`cacheweft` account. The administrator owns the executable environment and unit.
The daemon owns only its private state and configuration. This is a local Unix
socket service, not a remote administration API. Same UID/root remain trusted.
No runtime third-party Python dependency is added. That recorded milestone had
no selected project license; the current project is [MIT licensed](../LICENSE).

## Install, start, inspect and stop

Requirements: Linux with systemd, Python 3.12+ with venv/pip, util-linux and
shadow account tools. Build the wheel from the intended checkout on a trusted
build machine. Transfer that wheel together with this checkout's `deploy/`
files; the installer does not download or authenticate a release. Verify the
source/wheel hashes through your trusted transfer channel. It accepts trusted
administrator inputs only.

Prepare the configuration described in [SERVICE.md](SERVICE.md). Keep a custom
CA in a persistent readable location such as `/etc/cacheweft/ca.pem`, not in
`/home` or `/tmp`: the unit protects home directories and has a private `/tmp`.
The configured numeric source address must actually reach that TLS origin.
The administrator provisions that address/CA and immutable artifact hashes.

```sh
sudo sh deploy/install.sh /absolute/cacheweft-0.0.1-py3-none-any.whl /absolute/config.json
sudo systemd-analyze verify /etc/systemd/system/cacheweft.service
sudo systemctl start cacheweft.service
sudo systemctl status cacheweft.service
sudo -u cacheweft /opt/cacheweft/venv/bin/cacheweft --socket /var/lib/cacheweft/control.sock info
sudo -u cacheweft /opt/cacheweft/venv/bin/cacheweft --socket /var/lib/cacheweft/control.sock fetch request_001 model-v1 --seconds 30
sudo -u cacheweft /opt/cacheweft/venv/bin/cacheweft --socket /var/lib/cacheweft/control.sock status request_001
sudo systemctl stop cacheweft.service
```

The installer refuses an active/activating/deactivating/reloading service,
creates a system account without a login shell, installs the root-owned venv
and unit, installs an account-owned mode-0600 config, then reloads systemd.
It does **not** start or enable the unit. Stop before upgrading; do not run two
installers concurrently. Do not use an unrelated preexisting `cacheweft` account.
It preserves state, catalog and the stable lock inode. `StateDirectory` creates
`/var/lib/cacheweft` mode 0700 at service start. Only the service account and root
can use its mode-0600 control socket. Use controlled sudo access for operators;
there is no RBAC or per-user audit layer.

`Type=notify` marks the service running only after manifest binding, formal
journal recovery, socket creation and readiness-hint publication. Failure to
send the readiness notification fails startup and cleans the socket/hint. A
successful `info` RPC is still the live check: an old `ready.json` left by
SIGKILL is not a liveness signal. DEGRADED is an explicit RPC field; systemd's
active state is not a guarantee that mutations remain permitted.

`KillMode=control-group` covers the service's processes. On SIGTERM, admission
closes, transports are cancelled, accepted handlers/readers/workers are joined,
the socket/hint is removed, and the writer lock is released. The service has a
20-second stop timeout; systemd can SIGKILL a stuck process after that. That
forced path is not graceful completion or proof of a hard bound on blocked
kernel I/O. `TimeoutStartSec=30` bounds systemd's startup wait, not recovery cost
under every storage failure.

The unit drops all capabilities, sets NoNewPrivileges, protects the runtime,
home and kernel/control-group settings, uses a private temporary directory,
and restricts socket families to Unix/IPv4/IPv6. It needs no privileged port,
network configuration or host filesystem mounts. This is a targeted hardening
profile, not hostile-code containment or an independent security certification.

## Crash takeover and repair

`Restart=on-failure`, `RestartSec=1s`, `StartLimitBurst=3` and a 30-second
start-limit window bound rapid retries. Invalid configuration is deliberately
used in the VM test to exercise that limit. A fast deterministic startup
failure is not an OOM, segfault or every possible poison-input test. Failures
spaced beyond the window can still restart indefinitely; no claim of globally
finite retry count is made.

An instance restarted after SIGKILL runs the same conservative recovery:
uncommitted bounded requests become INTERRUPTED and partial objects PAUSED.
No old deadline is extended and no new origin GET occurs until explicit resume.
Committed publication intents follow the existing recovery contract. Old epoch
and request tokens are refused. Use a new request ID and fresh token to resume.

```sh
sudo journalctl -u cacheweft.service --no-pager -n 100
sudo systemctl stop cacheweft.service
# Repair the identified configuration or storage fault offline; preserve the catalog.
sudo systemctl reset-failed cacheweft.service
sudo systemctl start cacheweft.service
sudo -u cacheweft /opt/cacheweft/venv/bin/cacheweft --socket /var/lib/cacheweft/control.sock info
```

Do not remove catalog/lock files or reset request history to manufacture recovery.
Runtime DEGRADED does not automatically crash-loop: repair/restart is explicit.
Automatic boot enablement and formal systemd recovery across a VM reboot are
outside this milestone. Earlier reboot evidence used manually launched daemons;
it must not be described as testing this unit's autostart. Sudden power loss,
suspend, boot-zero-gap protection and long-term operation remain unqualified.

## Reproduce deployment verification

Use the same trusted Python/build and Colima/Lima prerequisites as
[LOCAL_VALIDATION.md](LOCAL_VALIDATION.md). Build a wheel matching current runtime:

```sh
.venv/bin/python -m pip wheel --no-build-isolation --no-deps . --wheel-dir results/deployment-build
.venv/bin/python -m tools.deployment_validate \
  --colima-home /absolute/colima-home \
  --toolchain /absolute/colima-bin-directory \
  --profile cw-exp-deployment-fresh \
  --wheel results/deployment-build/cacheweft-0.0.1-py3-none-any.whl \
  --output results/deployment-fresh
```

A fresh VM/output is mandatory. No host directories are mounted. The guest
installs the wheel and removes runtime source from test import reach. Root
installs the package/unit and observes faults; the real systemd service runs
as the dedicated account. Native macOS runs the same 71 identified tests,
including three real-daemon notification tests; it does not execute systemd.

The VM runs the unmodified shipped installer/unit, checks runtime hashes and
unit bytes, inspects live UID/capabilities/NoNewPrivs, downloads through a real
TLS fixture, SIGKILLs MainPID with a partial checkpoint, observes WAITING in the
retained journal before recovery, then observes a new InvocationID/PID/epoch.
Old tokens fail, explicit resume and the installed CLI return exact verified
bytes. A normal stop during slow headers must empty the cgroup and remove the
socket/hint without replacing the lock inode. Invalid config must hit the
shipped restart limit; offline repair plus reset-failed must start a new instance
without reviving old requests. The final service and dedicated VM/disk are removed.

JSON and HTML derive from the same verified record. A nonzero stage, timeout,
missing required scenario, wrong runtime/test cohort, bad privilege observation
or failed cleanup prevents pass. Corrupted evidence copies challenge the verifier.
Timing is one controlled observation per event, measured by the root observer
around systemctl plus a successful RPC. RSS/VmHWM and FD samples refer to the
service MainPID inside the Linux VM, not native macOS or host memory. There is
no performance comparison or recovery SLO in this milestone.

## Failed attempts and diagnostic corrections

The first attempts remain in `evidence/deployment-attempt-01.json` through
`deployment-attempt-04.json`, all explicitly failed; their VM/disk cleanup passed.
Attempts 01–03 have incomplete deployment observations and must not be cited as
successful integration runs. Attempt 01 stopped on reset-failed for an unloaded
inactive unit. Attempts 02–04 timed out looking for a literal `Result=start-limit-hit`.
A reporting-path variable was also overwritten by an unused read-output variable;
this was corrected so attempt 04 retained the full diagnostic sequence.

Attempt 04's raw systemd 255 journal shows three distinct cacheweft processes
rejecting invalid configuration, followed by PID 1's "Start request repeated too
quickly" event. The service was failed with MainPID 0 and NRestarts 3, while
Result remained `exit-code`. The final contract therefore requires the positive
manager event, three distinct failed-client PIDs, configured restart policy,
failed/no-PID state and unchanged properties for at least 2.2 seconds (more than
two RestartSec intervals). It does not infer rate limiting merely from exit-code
or relax the requirement to stop retrying. It also requires explicit repair and
reset-failed to succeed with old requests still INTERRUPTED.

## Recorded result

The final [JSON](../evidence/deployment.json) and [HTML](../evidence/deployment.html)
record 71 identical identified tests passing on each of native macOS and Linux,
with no failures, errors or skips. All 13 stages completed without timeout,
and 18 deliberately corrupted evidence copies were rejected. Source,
wheel and installed runtime hashes matched. The dedicated VM/disk were deleted
and preexisting profiles retained their original state.

One controlled Linux VM observation measured 0.055 s from start command
to live RPC, 1.093 s from the SIGKILL command to a new running invocation
and live RPC, and 0.223 s for normal stop plus cleanup checks. These
include observer/systemctl overhead; they are not distributions or upper bounds.
The restarted service MainPID sample was 24604672 bytes current RSS,
24604672 bytes VmHWM and 6 file descriptors. These are VM process
measurements, not host memory, long-term peaks or a native macOS comparison.
The restart-limit witness remained unchanged for 2.209 s after limiting.
