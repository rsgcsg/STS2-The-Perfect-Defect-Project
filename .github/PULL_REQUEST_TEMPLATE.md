## Change identity

- Repository: `rsgcsg/STS2-The-Perfect-Defect-Project`
- Base branch / exact base SHA:
- Head branch / latest head SHA:
- Workstream / primary owner:
- Stable task ID / owning design and version (separate from change class):
- Change class (`G0`-`G6`) and why:
- Owning fact / layer:

## Problem and decision

- Observed problem:
- First causal defect or missing fact:
- Implemented change:
- Alternatives or hypotheses rejected:
- Relevant history / producer-consumer path / existing mechanism reused:
- Non-goals:

## Contracts and identity

- Affected public contracts:
- Component identity impact:
- Cross-repository dependency and exact pin:
- Merge method: normal merge for component source; squash permitted only when no component source changes.

## Tests and evidence

- Test shape and failure family covered:
- Source/test commands and results:
- Exact build/artifact/runtime/Human/cloud identities, when applicable:
- Evidence level actually proved:
- Latest-head CI/status SHA:
- Remaining non-claims:

## Delivery and next consumer

- Artifact or capability delivered and how the next consumer uses it:
- Actual external effects (or none):
- Submitted / independently accepted / integrated / runtime-qualified scope:
- Remaining decision or gate, owner and recovery/monitor when applicable:

## Rollback

Describe the source revert, artifact/runtime rollback, cloud rollback, or evidence
quarantine path.

## Checklist

- [ ] The branch was based on current `origin/develop` or the documented release/hotfix base.
- [ ] The change has one primary responsibility or one indivisible causal repair.
- [ ] Authority and dependency direction remain intact; no second truth was added.
- [ ] Behavior changes have a faithful regression, or the PR explains why existing coverage is exact.
- [ ] Focused, root, and required higher evidence gates match the change class.
- [ ] CI/evidence belongs to the latest head and does not inherit predecessor proof.
- [ ] `npm run project:closeout` and `git diff --check` were reviewed.
- [ ] No proprietary files, raw evidence, secrets, local artifacts, or model weights are committed.
