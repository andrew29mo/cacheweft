# Cacheweft

A bounded local artifact download/cache core. This repository contains four
technical explorations toward a usable service, not a deployed cache daemon.
No open-source license has been selected or granted.

The core downloads a trusted manifest entry from an explicitly configured HTTPS
origin/address, checkpoints resumable bytes, verifies exact length and SHA-256,
and publishes an immutable object. Callers share an in-flight download. Local
read references and persistent pins prevent eviction; GC is explicit.

## Current interface

```python
from cacheweft import Cache, Spec
from cacheweft.http import HTTPS

entry = Spec("model-v1", "https://artifacts.example/model.bin", expected_size,
             expected_sha256)
transport = HTTPS("https://artifacts.example", configured_numeric_address)
with Cache(private_directory, [entry], transport, budget=payload_budget) as cache:
    cache.submit("request_001", "model-v1")
    outcome = cache.wait("request_001")
    if outcome["available"]:
        with cache.read(entry.sha256) as content:
            consume(content)
```

Values in this example are application inputs, not a runnable public model demo.
The manifest is trusted administrator input. Hash verification proves equality
to that input, not publisher identity, model safety, or licensing rights.

## Reproduction

Python 3.12+, OpenSSL CLI, POSIX local filesystem with flock, hard links and
directory fsync are required. No third-party runtime Python dependencies.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install .
mkdir -p results
.venv/bin/python -m tools.unit --output results/native.json
.venv/bin/python -m tools.cases --output results/native-cases.json
```

See [local validation](docs/LOCAL_VALIDATION.md) for a fresh, no-host-mount Colima
VM that installs the wheel and removes the runtime source from import reach.
Linux VM metrics and native macOS observations are separate.

## Evidence and scope

- [Architecture and state machines](docs/ARCHITECTURE.md)
- [Exploration results and remaining acceptance criteria](docs/EXPLORATIONS.md)
- [Review findings and corrections](docs/REVIEW.md)
- [Recorded JSON](evidence/latest.json) and [HTML](evidence/latest.html)
- [Third-party tools](docs/DEPENDENCIES.md)

The measured workload is a deterministic 1 MiB binary over a controlled local
TLS origin. It is not a real model or WAN performance benchmark. A single
observation per mode supports correctness/accounting inspection, not a speedup
claim. Memory includes the observer and TLS fixture in the same process.

No formal daemon/API deployment, systemd recovery, sudden power-loss recovery,
long-term operation, NFS/shared filesystem, external mmap leases, P2P, arbitrary
URL forwarding or automatic history rollover is qualified. Cache data is
evictable; this service is not a backup. Trusted root/same-UID tampering is
outside its integrity boundary.
