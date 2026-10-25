# Public repository setup

Cacheweft was made Public on 2026-10-26 (Asia/Taipei) with the MIT license.
The reviewed/licensed source before publication was commit `1fc2af4`.
The runtime and recorded measurements were not changed by this publication.
[The settings snapshot](../evidence/publication.json) records the GitHub API
readback; settings can change later and must be rechecked when needed.

## Maintenance protections

- Secret scanning and secret-scanning push protection are enabled.
- Dependabot alerts and automated security-update PRs are enabled. The
  [version-update configuration](../.github/dependabot.yml) checks the root
  Python project and optional example requirements weekly, with at most one
  open version-update PR per directory.
- [Private vulnerability reporting](../SECURITY.md) is enabled.
- Main requires linear history, rejects force pushes/deletion and applies those
  protections to admins. Conversation resolution is required for reviewed PRs.
- Mandatory PR reviews and status checks are not enabled: there is no remote
  CI workflow, and the owner can continue direct linear commits. Only the owner
  is a collaborator. Updates do not automatically merge.
- GitHub Actions is disabled. The default token permission is read-only, Actions
  cannot approve PR reviews, and all external contributors require approval for
  fork-PR workflows if Actions is later enabled.

GitHub's disabled-Actions readback reports `sha_pinning_required: false` and
omits `allowed_actions`. Setting SHA pinning while disabled did not persist;
this setup does not claim otherwise. Re-enabling Actions requires an explicit
review of allowed actions, full-commit pinning, fork permissions and secrets.
There is no claim of a passing remote CI run while Actions is disabled.

Security-service enablement is distinct from completed scanning. Dependabot and
secret-scanning results are asynchronous; enabled settings do not prove the
absence of vulnerable dependencies or secrets.

## Public-read checks and evidence

Publication verification uses unauthenticated HTTPS requests to the repository,
README, LICENSE, documentation and report files, and compares retrieved bytes
to the committed local files. GitHub may show HTML reports as source/download;
open the downloaded file locally to render it. No Pages deployment is implied.

The [final review](FINAL_REVIEW.md), [licensing record](../evidence/licensing.json),
[bounded operation](CLOSEOUT.md) and [real-model demonstration](MODEL_DEMO.md)
retain their original measured scope. Older descriptions of Private visibility
or no selected license remain historical statements, not current configuration.
There was no new VM, inference, resource or performance run for publication.
Finite request history still has no automatic rollover, and short observations
do not establish long-term, power-loss or boot-zero-gap guarantees.
