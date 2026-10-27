# SpireAgent Engineering Guide

## Mission and startup

Deliver useful STS2 Agents through a faithful environment, usable recording/data
and a maintainable product path. One repository does not merge gameplay,
execution, evidence, application and research authority.

Initial route: this file → `README.md` → `docs/DOCUMENT_MAP.md` →
`docs/WORKING_RULES.zh-CN.md` → `docs/memory/CURRENT.md`. Then read the linked current task and only the owning
specification/component guide and exact code/evidence needed for it. A four-file
orientation is not a ban on relevant investigation; it is not a requirement to
read all historical plans before doing useful work.

开工先沿README唯一链路核本次交付的完整上下游需求、已有实现和对应SOP，再选有界改动；调查限本批真实路径与可复用成果，有限阅读不等于只查单组件，也不要求泛读全部历史。

Before ordinary development consult relevant `docs/DEVELOPMENT_WORKFLOW.md` and
`docs/TESTING.md` sections. For authority, public contracts, cross-layer semantics,
identity, test policy or governance changes, also consult the relevant
`docs/ARCHITECTURE.md`, `docs/COMPONENTS.md` and
`docs/ENGINEERING_GOVERNANCE.md` sections. `npm run project:context -- --component <name>`
provides a bounded route. Do not infer current work from an old chat title/report.

## Work boundary

The shared work rules apply to engineers, architects, managers and Agents.
One assignment has a purpose, deliverable, scope, acceptance and stop condition.
The lead owns prioritization and acceptance; each worker has one bounded outcome.
Do not turn every discovered problem into a new task or substitute repeated tests,
audits or safety arguments for consumer-visible progress. Reassess stalled work;
ask the user about genuine unresolved goals, tradeoffs or authority, not routine
implementation choices. AI-specific delegation/recovery lives in
`docs/AI_COLLABORATION.md`; material changes receive its scoped non-author review.

## Hard shell

- STS2 owns rules, RNG, effects, native legality and Commit.
- Connector owns fair-player Snapshot/Read, complete finite BoundAction,
  execute-time revalidation, Receipt and successor semantics.
- Host Runtime owns lifecycle, isolation, exact identity and recovery, not legality.
- Native Foundation owns native facts/lineage, not transport or input authority.
- Annotator owns native-source/Human witness correlation and immutable records.
- Evidence owns typed verification and transfer, not Human origin or research admission.
- Policy Runtime owns controlled execution, budgets/Stop and control release;
  it owns no inference, legality or native operands.
- Game Mod owns package/install/load/rollback; packaging does not merge authorities.
- `python/spireagent` owns applications/infrastructure; `python/stpd` owns research,
  strategy, models, training and evaluation through declared Platform interfaces.

Never leak hidden game state, manufacture native operands, create a second legality
engine, silently fall back, or automatically retry an unknown delivery. Preserve
original data and failures. Protect actual control/data/permission boundaries;
do not build speculative defense machinery unrelated to the task.

## Git, identity and completion

Use current `origin/develop` for normal short topics; explicitly identify any
required unmerged dependency. Reuse a suitable clean checkout, one writable branch
per writer. No direct push to main/develop, force push or overwrite of another
writer. PR merge/close follows the user's current scoped authorization; without
an explicit grant, use per-PR approval. The working-rules permission table owns
the standing grant and task-specific restrictions. Existing scoped runtime
permissions persist; new spending, research configuration, production publication
or destructive operations need their own authorization.

Workspace SHA is provenance, not every component's identity. Keep path-scoped
source revision/digest, contract version and actual artifact identity distinct.
Component-source changes use normal merge commits; docs-only changes may squash.
Never weaken identity/BOM checks for an incompatible merge method.

Classify G0–G6 under Governance, repair the owning fact, use the cheapest faithful
checks while editing, then the selected gates for the claim. Full runs belong on
stable candidates, not every small edit. Before a PR run `npm run project:closeout`
and `git diff --check`; preserve required CI and exact-game/Human checks when those
claims are requested. Tests/build/installed/runtime/Human/strategy quality are
separate; none is inferred from a merge or a worker summary.

A PR records purpose, base/latest head, scope/non-goals, owning change, validation,
contract/version impact, rollback and known limitations. Hand off a compact result
and stop at the task boundary. Do not start the next backlog item unassigned.
Never commit game binaries/decompiled source, raw Human data, `.local/`, secrets,
weights or installed artifacts. Documentation placement and rule maintenance are
owned by `docs/PROJECT_SYSTEM.md`, not a new status or policy registry.
