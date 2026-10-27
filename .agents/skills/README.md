# Repository Skills

This directory contains stable, bounded workflows for high-risk or repeatedly
non-obvious Platform work. The Skills do not replace `AGENTS.md`, canonical
docs, code, tests, current PRs, or exact runtime/evidence records.

Ordinary implementation, tests, documentation, and architecture review normally
need no repository Skill. Start with `npm run project:context`, then read the
owning docs and exact code/tests.

## Available Skills

- [platform-human-evidence](platform-human-evidence/SKILL.md): prepare, capture,
  audit, and classify exact native-Human evidence; it stops for the Human owner
  and does not grant research admission.
- [platform-runtime-qualification](platform-runtime-qualification/SKILL.md):
  qualify exact source/package/install/load/probe/runtime identities without
  promoting lower evidence.
- [repo-skill-maintenance](repo-skill-maintenance/SKILL.md): explicit-only
  admission, creation, update, split, deprecation, and validation of repository
  Skills.

## Admission boundary

Do not create a new Skill merely because a task is important. Prefer code/tests
for behavior, CI for machine invariants, canonical docs or ADRs for engineering
rules, Status/evidence for mutable claims, and a task or PR for one-off work.
Use the admission and update rules in [Project System](../../docs/PROJECT_SYSTEM.md)
and [Engineering Governance](../../docs/ENGINEERING_GOVERNANCE.md).

## Retained workflow history

The [archived runtime-qualification workflow candidate](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/archive/convergence-20261027/pr-172-c358d2b44260/.agents/skills/platform-runtime-qualification/SKILL.md) remains available for selective reuse. It is not the active Skill and grants no current qualification or permission. Its disposition is in the [executed asset receipt](../../docs/evidence/CONVERGENCE_DISPOSITION_EXECUTED_20261027.md); use the current Skills above for work.
