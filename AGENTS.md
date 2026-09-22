# SpireAgent Project Engineering Guide

## Mission

Maintain one project for the STS2 environment, project applications and STPD research.
One repository and workflow do not merge game, evidence, operational or research authority.

## Required Read Order

1. `README.md`
2. `docs/memory/CURRENT.md`
3. `docs/ARCHITECTURE.md` and `docs/COMPONENTS.md`
4. the relevant component `AGENTS.md` or guide and exact code/tests

For current product work, Stage 1a is the mainline: read
[Stage 1a product delivery](docs/STAGE1A_PRODUCT_DELIVERY.zh-CN.md) and its narrow task plan.
All AI collaborators read [AI collaboration](docs/AI_COLLABORATION.md). The local
supervisor owns planning, delegation and integration coordination; implementation and
the supervisor's own changes receive independent review. Authorized bounded packets
include their prerequisites, checks and understood local repairs without a human relay
at every substep. Five minutes is a passive-wait checkpoint, not a limit on active work.
Use a real, bounded observer when available and continue independent authorized work;
stop for a genuine human/access/authority boundary, never invent background monitoring.

Before ordinary development, also read `docs/DEVELOPMENT_WORKFLOW.md` and
`docs/TESTING.md`. Read `docs/ENGINEERING_GOVERNANCE.md` when the task changes
architecture, authority, a public contract, evidence/identity, cross-layer
behavior, test strategy, Agent collaboration, Skill policy, or cloud/runtime
promotion.

Normal work starts from current `origin/develop`, uses one short-lived topic
branch and targets `develop` by pull request. Do not direct-push `main` or
`develop`, share a writable branch between agents, or create permanent component
develop lines. Ordinary tasks finish at reviewed develop integration; promote a selected
batch to main only when release is in scope. Matching trees need no empty sync PR.
TESTING.md owns scoped checks and verified execution reuse; never copy a green status
or equate a branch head with the installed/deployed producer. Use `npm run project:context -- --component <name>` for a bounded
routing map; load `docs/STATUS.md` and dated evidence only when current claims
matter.

## Hard Shell

- STS2 owns rules, RNG, effects, native legality and Commit.
- Connector owns fair-player Snapshot, Read, complete finite BoundAction,
  execute-time revalidation, Receipt and successor semantics.
- Host Runtime owns process lifecycle, profile isolation, exact identity,
  recovery and qualification tooling, not gameplay legality.
- Annotator owns native-human witness correlation and immutable recording
  evidence, not action authority or research admission.
- Evidence owns typed verification and immutable transfer, not Human origin or
  research admission.
- Policy Runtime consumes Connector-owned finite actions and stays
  model-neutral; it owns no inference, legality, or native operands.
- Game Mod owns one production package, exact install/load identity, and
  rollback; packaging never merges component authorities.
- External consumers own strategy, research projection, training and
  evaluation.

Platform components are the model-neutral foundation. `python/spireagent` owns
project applications and shared infrastructure; `python/stpd` owns research.
Both use declared component APIs. Platform must not import model, reward,
training or research semantics. See docs/MONOREPO_MIGRATION.md for cutover gates.
Root governance overrides historical repository workflow descriptions in imported docs.

Never add hidden-state leakage, coordinate/index mutation, arbitrary reflection,
a second legality engine, consumer-created native operands, silent fallback or
automatic retry after an `unknown` delivery.

## Component Identity

The workspace commit is provenance, not a component semantic identity. Every
component has its own path-scoped source revision, source digest, contract
digest, version and artifact identity. An unrelated component edit must not
silently change another component's source identity.

The current path-scoped `source_revision` is commit provenance. A normal merge
commit preserves the topic component revision; squash/rebase integration does
not. Until that provenance contract is deliberately changed, any PR that
changes component source uses a normal merge commit. Docs/governance-only PRs
that do not change component source may be squashed. Never weaken BOM/identity
checks merely to accommodate a provenance-rewriting merge method.

## Change Loop

Classify the change (`G0`-`G6`), identify the first incorrect fact and owning
layer, preserve dependency direction, add the lowest-cost faithful regression,
run the component and selected root gates defined in docs/TESTING.md, then report evidence at its exact level.
Hosted CI must remain source/test-only; game-bound changes additionally use the
local exact-game/build/runtime gates defined in `docs/TESTING.md`.

Never commit game files, decompiled source, raw human data, `.local/`,
credentials, model weights or installed artifacts. Worker or conversation
output is not evidence until the lead verifies exact code, tests, refs, and
runtime records.

Record the base branch/SHA, latest head, workstream, change class, owning fact,
cross-repository pin, evidence level, rollback and non-claims in every PR. A
merge never promotes source/test evidence to build, loaded, runtime, Human or
qualification evidence. Run `npm run project:closeout` before the PR so
documentation, evidence, contract, version, and governance impacts stay visible.
