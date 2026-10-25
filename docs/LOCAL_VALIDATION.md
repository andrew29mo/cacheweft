# Local validation

Use Python 3.12+, a current setuptools build environment, Colima/Lima and a
Linux guest with systemd. The runner creates a fresh `cw-exp-*` profile, no host
mounts, two vCPUs and 2 GiB requested RAM. No existing profile is reused or
reconfigured. Root inside the disposable guest is used only for test storage
mounts, dependency installation and reboot, not as a product privilege claim.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install 'setuptools>=77' wheel
.venv/bin/python -m pip wheel --no-build-isolation --no-deps . --wheel-dir results/build
.venv/bin/python -m tools.validate \
  --colima-home /path/to/colima-home \
  --toolchain /path/to/colima-bin-directory \
  --profile cw-exp-fresh \
  --wheel results/build/cacheweft-0.0.1-py3-none-any.whl \
  --output results/fresh
```

The profile/output must be fresh. The guest installs the wheel without network
dependencies; package source is moved outside the test import path. Installed
runtime SHA-256s must equal native runtime hashes and frozen source hashes.
Both platforms run the same identified test suite. The evidence records stage
exit codes, timeouts, source hashes and cleanup. Any failure prevents pass.

The Linux-only storage suite fills an 8 MiB tmpfs until only 32 KiB is free,
uses a real read-only bind mount for path-write failures, and sets a real SQLite
page cap to obtain SQLITE_FULL. A read-only
bind mount is not a claim of failing physical media. Each fault must deny new
work and permit correct explicit repair/restart. The admission-FULL case starts
with a READY object, so it proves rejection of new catalog admission rather
than preventing that earlier publication. A separate publish-FULL case uses a
test-only SQLite allocation trigger at the formal publish-intent transaction:
the page cap causes actual SQLITE_FULL, leaving VERIFYING/WAITING with no
target. Offline trigger removal and restart must complete the exact request
without another origin GET. It is controlled SQLite capacity pressure, not a
physical disk-full event.

For normal VM reboot, a child exits immediately after durable publish intent.
No product process is then alive to clean it up. The catalog hash and staging
digest are recorded. After kernel boot ID changes, the harness checks catalog
bytes unchanged before invoking formal Cache recovery and verifies full content.
The report also carries actual pre-recovery catalog/staging hashes and retained
object/request rows tied to the recovered request.
This tests manual invocation of the formal recovery core after reboot; it does
not test installed systemd autostart or suddenly interrupted power.

Raw stage logs and generated test keys remain under ignored `results/`. Public
JSON/HTML contain sanitized observations and fingerprints, not keys, local
account paths or machine names. On any attempt the runner deletes only its own
VM/data disk and verifies prior profile names/statuses are unchanged.
