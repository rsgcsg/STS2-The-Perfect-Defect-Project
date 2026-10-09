# Document map

Start with README → [CURRENT](memory/CURRENT.md) → the active task and owning
specification. Read only the route needed for the next decision. This is a map,
not another requirements, identity or status registry.

## Current baseline

- [V1 specification](BASELINE_V1_SPEC.zh-CN.md): required outcomes, selected
  design, rationale, assumptions and unimplemented choices.
- [Execution and acceptance matrix](plans/BASELINE_G2_V1_EXECUTION_2026-10-08.md):
  active packets, dependencies, authorization, budget and exact remaining gates.
- [Task index](plans/BASELINE_TASKS.zh-CN.md): stable P/E/G/V/R IDs.
- [Status](STATUS.md): dated claim/evidence index, including the latest bounded
  Source3 learning/Model delivery and historical operating combinations.
- [Data/Host/model planning](plans/BASELINE_DATA_HOST_MODEL_NEXT_2026-10-09.zh-CN.md):
  scoped asset inventory and optional directions, not current dispatch authority.

## New here

- [README](../README.md): product, layout and setup.
- [New Engineer Guide](NEW_ENGINEER_GUIDE.md): source contributor's first PR.
- [Member handoff (中文)](NEW_MEMBER_HANDOFF.zh-CN.md): choose the relevant account,
  collection, research or operations role; not every role is an onboarding gate.
- [Collection workflow](ANNOTATOR_COLLECTION.md): collecting and investigating
  native evidence. The installed combination must have its own qualification.

## Working on the repository

| Question | Owner |
| --- | --- |
| What instructions apply? | [AGENTS](../AGENTS.md), ancestor/local guides and `npm run project:context` |
| Where should a fact or new idea be written? | [Project System](PROJECT_SYSTEM.md) |
| Who owns the fact and is the abstraction justified? | [Architecture](ARCHITECTURE.md), [Components](COMPONENTS.md), [Governance](ENGINEERING_GOVERNANCE.md) |
| Which tests and evidence does this change need? | [Testing](TESTING.md), existing `check:plan` and owning tests |
| How are branches, integration, release and deployment handled? | [Workflow](DEVELOPMENT_WORKFLOW.md), [Versioning](VERSIONING.md) |
| How are tasks delegated, reviewed and resumed? | [AI collaboration](AI_COLLABORATION.md) |
| Is a durable architecture decision needed? | [ADR policy and index](adr/README.md) |
| Is there a reusable qualification workflow? | [Skills](../.agents/skills/README.md); ordinary implementation needs no Skill |

## Component entry points

- [Native Foundation](../components/native-foundation/README.md): typed native
  facts; [seam matrix](NATIVE_SEAM_MATRIX.md) and
  [example suite](NATIVE_FOUNDATION_EXAMPLE_SUITE.md) for specific mechanisms.
- [Connector](../components/connector/docs/DOCUMENT_MAP.md): public observation,
  full finite actions, delivery and profile contracts.
- [Host Runtime](../components/host-runtime/docs/DOCUMENT_MAP.md): process,
  environment and exact qualification.
- [Annotator](../components/annotator/docs/DOCUMENT_MAP.md): recording and causal evidence.
- [Evidence](../components/evidence/README.md) and
  [delivery](../components/evidence/DELIVERY.md): typed verification and transfer.
- [Policy Runtime](../components/policy-runtime/README.md): control and Agent lifecycle.
- [Python applications and research map](../python/docs/DOCUMENT_MAP.md): shared
  Workbench/Hub services, data, learning and model consumers; apply `python/AGENTS.md`.
- [Game Mod](../apps/game-mod/README.md), [in-game UI](../apps/ingame-ui/README.md)
  and [UI specification](UI_INTERACTION_SPEC.md): packaging and user entry.
- [Diagnostic API](../apps/workbench/README.md): retained typed diagnostic surface.

## Finding technical truth

Current code/contracts establish implemented behavior; the owning spec establishes
intended behavior. A disagreement needs a repair or an explicit design revision.
[STATUS](STATUS.md) routes qualification claims; `platform-bom.json` and existing
manifests own component/artifact identity. Refresh actual runtime state before use.
[Full-Run semantic coverage](FULL_RUN_SEMANTIC_COVERAGE.md) and
[canonical data chain](FULL_RUN_DATA_CHAIN.md) retain their causal/legacy scope;
they do not replace the current baseline matrix.

## Historical proof

[`docs/evidence`](evidence/) preserves dated exact source/artifact/runtime results.
Read the report cited by a current claim or relevant PR, not the entire archive.
A later candidate needs its own evidence; a historical PASS is not a current status.
The [monorepo migration](MONOREPO_MIGRATION.md) explains source history and cutover,
not daily application development or an instruction to repeat migration.

## Baseline design history

[Discussion/design history](design/BASELINE_DESIGN_HISTORY.zh-CN.md) routes earlier
alternatives and corrections. [Foundation](design/BASELINE_FOUNDATION.zh-CN.md),
[scenario cards](design/BASELINE_SCENARIO_SPEC.zh-CN.md), and
[L-N operation inventory](design/BASELINE_LN_V1_SPEC.zh-CN.md) remain useful references.
Only portions explicitly retained by the current specification are normative.
Accepted [ADR-0015](adr/0015-native-logical-interaction.md) preserves its upper-level
native logical-page/action direction; profile/wire definitions have their own owners.
