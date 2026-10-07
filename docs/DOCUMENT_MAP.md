# Document Map

Use the smallest route that answers the task.

## Current baseline design packet

- [P0–P5 full delivery](design/BASELINE_ACCEPTANCE_PLAN.zh-CN.md): D-M2 closure, candidate cost comparison, REQ verification matrix and next acceptance.
- [A system journeys](design/BASELINE_A_SYSTEM_JOURNEYS.zh-CN.md): selected main direction, 14 complete consumer/data journeys, joint Host/recording/learning/operations evaluation and G1 decisions.
- [Agent/protocol blueprint](design/BASELINE_AGENT_PROTOCOL_BLUEPRINT.zh-CN.md): current complete design, Agent-owned timing, optional boundary facade, four reference compositions and end-to-end learning/runtime cases.
- [Protocol v1 synthesis](design/BASELINE_PROTOCOL_V1_SYNTHESIS.zh-CN.md): current consolidated protocol proposal, capture limits, scoped mappings, LN-B1/LN-E1 and learned timing.
- [L-N queued timing](design/BASELINE_LN_QUEUED_TIMING.zh-CN.md): Human input versus execution time, native queues, conversion assumptions, latency and qualification.
- [L-N Human learning integration](design/BASELINE_LN_LEARNING_INTEGRATION.zh-CN.md): actual Annotator and M2 seams, six classic cases, IL/generative/structured/RL data requirements.
- [L-N v1 specification](design/BASELINE_LN_V1_SPEC.zh-CN.md): detailed operation scope, native previews, complete catalog authority with non-scoring Agents, failure ownership and acceptance.
- [Concrete protocol options](design/BASELINE_PROTOCOL_OPTIONS.zh-CN.md): three Agent contracts and four Host mappings, public details, failure/recovery, D-M2 and matched evaluation.
- [Scene specification](design/BASELINE_SCENARIO_SPEC.zh-CN.md): 21 mechanism cards and 60 examples with native/protocol/Agent/management distinctions.
- [P5 evidence and bounded probe](evidence/BASELINE_P5_EVIDENCE_2026-10-06.md): exact source/DLL findings, categorized reference checks, JSON costs and unmeasured scope.
- [Discussion history and corrections](design/BASELINE_DESIGN_HISTORY.zh-CN.md): requirements versus hypotheses/options; not raw private chat.

- [P0 needs and P2 foundations](design/BASELINE_FOUNDATION.zh-CN.md): continuous reading, definitions, responsibility boundaries, examples and unresolved choices.
- [P1 audit](evidence/BASELINE_P1_AUDIT_2026-10-06.md): exact source/candidate/history scope and reuse limits.
- [P3 interaction candidates](design/BASELINE_INTERACTION_CANDIDATES.zh-CN.md): full-game candidates, message/time/recording semantics and G1 choices.
- [P4 data, Agent, learning and execution contracts](design/BASELINE_DATA_AGENT_CONTRACTS.zh-CN.md): model axes, M2, N/Z/O, experiment/evaluation and local/cloud/product connections.
- [Learning and interaction design evidence](evidence/BASELINE_LEARNING_AUDIT_2026-10-06.md): exact historical model/target definitions and bounded implementation status.
- [Baseline task index](plans/BASELINE_TASKS.zh-CN.md): the only new P/E/G/V/R task IDs, dependencies and gates; not dispatch or execution permission.
- [Task and delivery format](AI_COLLABORATION.md#task-packet-and-delivery-format): bounded investigation, implementation, independent review and usable handoff.

## New here

- [README](../README.md): zero-context product boundary and next steps.
- [Complete member and Agent handoff (中文)](NEW_MEMBER_HANDOFF.zh-CN.md): accounts,
  first installation, daily collection, development, operations and incident reporting.
- [New Engineer Guide](NEW_ENGINEER_GUIDE.md): first-day setup and first PR.
- [Collection and maintenance](ANNOTATOR_COLLECTION.md): default workbench handoff, native capture and incident ownership.

## Working on the repository

- [Root agent guide](../AGENTS.md): hard shell and change loop.
- [Engineering Governance](ENGINEERING_GOVERNANCE.md): fact ownership,
  architecture and abstraction review, change classes, test selection,
  Human/Agent collaboration, external dependencies, and cloud evolution.
- [Development Workflow](DEVELOPMENT_WORKFLOW.md): branches, PRs, releases, and evidence reporting.
- [Testing and Evidence](TESTING.md): executable test/evidence ladder and CI placement.
- [Project System](PROJECT_SYSTEM.md): documentation, style, Skills, and anti-drift governance.
- [ADR policy and index](adr/README.md): durable decision admission, status, and supersession.
- [Repository Skill index](../.agents/skills/README.md): high-risk repeatable workflows; ordinary implementation usually needs no Skill.
- Owning component: its local `AGENTS.md` when present, then its README/docs and exact tests.

## Finding technical truth

- [Native logical observation, actions and memory (ADR-0015, Accepted: upper-level scope)](adr/0015-native-logical-interaction.md):
  WEB-01 definitions, single-selector versus multi-reward examples, logical-list
  scope, old-mode compatibility and local refinement boundaries; not runtime proof.

- Current claims and evidence pointers: [Status](STATUS.md).
- [Dataset library and run boundaries](evidence/DATASET_LIBRARY_2026-09-16.md):
  generated lists, personal preview cleanup, Windows integration and application/cloud evidence.
- Semantic evidence storage and measured predecessor baseline:
  [Semantic Evidence Storage Baseline](evidence/SEMANTIC_EVIDENCE_STORAGE_BASELINE_2026-08-29.md).
- Latest normalized Human runtime and storage closeout:
  [Schema-3 Human and data-lifecycle closeout](evidence/SCHEMA3_HUMAN_DATA_LIFECYCLE_CLOSEOUT_2026-08-29.md).
- Latest exact recorder-lag attribution and repair boundary:
  [Recorder causal performance baseline](evidence/RECORDER_CAUSAL_PERFORMANCE_BASELINE_2026-08-29.md).
- Current pre-Full-Run performance baseline and trigger-bound debt:
  [Pre-Full-Run Deferred Debt](PREFULLRUN_DEFERRED_DEBT.md).
- Canonical H/S/A(S)/A/S' calibration and bounded architecture decision:
  [Recorder canonical causality decision](evidence/RECORDER_CANONICAL_CAUSALITY_DECISION_2026-08-29.md)
  and [ADR 0003](adr/0003-serialize-human-input-for-canonical-one-step-evidence.md).
- Current native-semantic discriminator source and bounded Human result:
  [source closeout](evidence/NATIVE_SEMANTIC_RUNTIME_DISCRIMINATOR_SOURCE_CLOSEOUT_2026-08-30.md)
  and [Human closeout](evidence/NATIVE_SEMANTIC_RUNTIME_DISCRIMINATOR_HUMAN_CLOSEOUT_2026-08-30.md).
- Historical serialized-input candidate and native restore/twin decision:
  [source closeout](evidence/SERIALIZED_HUMAN_INPUT_SOURCE_CLOSEOUT_2026-08-30.md)
  [runtime candidate](evidence/SERIALIZED_HUMAN_INPUT_RUNTIME_CANDIDATE_2026-08-30.md),
  and [native restore audit](evidence/NATIVE_RESTORE_AND_TWIN_RUNTIME_AUDIT_2026-08-30.md).
- Bounded active context and next gate: [Current Context](memory/CURRENT.md).
- Product boundary and dependency direction: [Architecture](ARCHITECTURE.md).
- Shared game-side semantics, seam matrix, and migration:
  [Native Foundation](NATIVE_FOUNDATION.md),
  [Native Seam Matrix](NATIVE_SEAM_MATRIX.md),
  [Architecture Example Suite](NATIVE_FOUNDATION_EXAMPLE_SUITE.md), and
  [ADR 0004](adr/0004-native-foundation-and-ritsu-route.md).
- Current Map/Reward/CardReward adapter source and build evidence:
  [Native Foundation Full-Run source closeout](evidence/NATIVE_FOUNDATION_FULL_RUN_SOURCE_CLOSEOUT_2026-08-31.md).
- Current Treasure adapter source evidence:
  [Native Foundation Treasure source closeout](evidence/NATIVE_FOUNDATION_TREASURE_SOURCE_CLOSEOUT_2026-08-31.md).
- Current Human Root/Native Commit/Successor Boundary authority and source gate:
  [ADR 0005](adr/0005-human-root-commit-successor-evidence.md) and
  [causal evidence source closeout](evidence/NATIVE_FOUNDATION_COMPLETION_LINEAGE_SOURCE_CLOSEOUT_2026-09-01.md).
- PR #6's exact Combat successor-owner repair:
  [owner-ready source closeout](evidence/PR6_SUCCESSOR_OWNER_READY_SOURCE_CLOSEOUT_2026-09-01.md).
- Current bounded pre-Full-Run hardening source/build/load gate:
  [hardening source closeout](evidence/PLATFORM_PREFULLRUN_HARDENING_SOURCE_CLOSEOUT_2026-09-01.md).
- Current Recorder hot-path performance source gate and Human OFF/ON canary:
  [recording hot-path performance closeout](evidence/PLATFORM_RECORDING_HOTPATH_PERFORMANCE_SOURCE_CLOSEOUT_2026-09-01.md).
- Ownership matrix: [Components](COMPONENTS.md).
- Portable/runtime evidence meanings: [Testing and Evidence](TESTING.md).
- Component and composition identity: [Versioning](VERSIONING.md) and `platform-bom.json`.
- Active Full-Run matrix: [Full-Run Semantic Coverage](FULL_RUN_SEMANTIC_COVERAGE.md).
- Current product and evidence direction: [Roadmap](ROADMAP.md).

- Closed-session tools, persistent outbox and upload receipt protocol:
  [Evidence delivery](../components/evidence/DELIVERY.md).

## Component entry points

- [Connector map](../components/connector/docs/DOCUMENT_MAP.md)
- [Native Foundation](../components/native-foundation/README.md)
- [Host Runtime map](../components/host-runtime/docs/DOCUMENT_MAP.md)
- [Annotator map](../components/annotator/docs/DOCUMENT_MAP.md)
- [Evidence package](../components/evidence/README.md)
- [Workbench](../apps/workbench/README.md)
- [Platform Game Mod operations](../apps/game-mod/README.md)
- [In-game Live UI boundary](../apps/ingame-ui/README.md)
- [Shared UI and interaction specification](UI_INTERACTION_SPEC.md)

## Historical proof

Dated reports under [`docs/evidence`](evidence/) prove only the exact source,
artifact, runtime, and scope they name. Load the report linked by Status, the
Full-Run matrix, or a PR when exact historical proof is relevant; it is not
default newcomer or Codex context.

- First dedicated Human Close-to-R2 gate and delivery diagnostic repair:
  [bounded audit](evidence/B_PIPELINE_FIRST_HUMAN_UPLOAD_2026-09-13.md).

- [B workflow release evidence](evidence/B_UNIFIED_WORKFLOW_RELEASE_2026-09-13.md): exact cross-repository source/service scope and release receipt routing.

- [Packaging identity correction](evidence/B_WORKFLOW_PACKAGING_IDENTITY_CORRECTION_2026-09-13.md): actual BOM file SHA and separate tool revisions.

- [Windows CollectionTool inventory repair](evidence/WINDOWS_COLLECTION_TOOL_INVENTORY_REPAIR_2026-09-16.md):
  exact Evidence source/test scope, local Windows verification, and current non-claims.

## Project migration

- [Migration and unified application ownership](MONOREPO_MIGRATION.md).
- [Python and research routing](../python/docs/DOCUMENT_MAP.md).
