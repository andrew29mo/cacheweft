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
page cap to obtain SQLITE_FULL. These are three distinct faults; a read-only
bind mount is not a claim of failing physical media. Each fault must deny new
work, avoid publication and permit correct explicit repair/restart.

For normal VM reboot, a child exits immediately after durable publish intent.
No product process is then alive to clean it up. The catalog hash and staging
digest are recorded. After kernel boot ID changes, the harness checks catalog
bytes unchanged before invoking formal Cache recovery and verifies full content.
This tests manual invocation of the formal recovery core after reboot; it does
not test installed systemd autostart or suddenly interrupted power.

Raw stage logs and generated test keys remain under ignored `results/`. Public
JSON/HTML contain sanitized observations and fingerprints, not keys, local
account paths or machine names. On any attempt the runner deletes only its own
VM/data disk and verifies prior profile names/statuses are unchanged.
