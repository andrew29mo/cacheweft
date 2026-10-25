# Security

## Reporting a vulnerability

Use [private vulnerability reporting](https://github.com/andrew29mo/cacheweft/security/advisories/new)
from the repository's Security tab. Please avoid disclosing credentials or
unpatched exploit details in public issues. Reports are reviewed by the single
maintainer; no response-time or remediation SLA is offered.

## Supported scope

The current main branch is an experimental local-service implementation.
The daemon runs unprivileged and exposes a private Unix socket. Root and the
service UID are trusted. Manifests, numeric origin addresses and TLS trust are
administrator inputs. SHA-256 verifies expected content; it does not establish
publisher identity or model safety.

Read the [service contract](docs/SERVICE.md), [deployment guide](docs/DEPLOYMENT.md)
and [measured limits](docs/CLOSEOUT.md) before use. Finite request history has no
automatic rollover. Power-loss recovery, boot-zero-gap protection and long-term
resource stability are not qualified by the short tests.

Dependency alerts and update PRs assist maintenance; they are not evidence that
all vulnerabilities are detected. Proposed updates require maintainer review
and relevant local validation before merge. There is no automatic merge policy
or remote CI workflow; GitHub Actions is disabled.
