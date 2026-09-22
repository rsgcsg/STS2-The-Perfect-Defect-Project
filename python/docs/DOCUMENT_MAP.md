# Document Map

Routing only: exact source/contracts/tests and scoped artifact/runtime evidence establish
facts. Canonical documents describe supported claims; ADRs/plans define accepted intent;
working memory and historical conversations do not prove implementation. Separate evidence
classes are defined in [Engineering Governance](ENGINEERING_GOVERNANCE.md).

## Entry and engineering

| Document | Responsibility |
|---|---|
| [Unified task flow (中文)](UNIFIED_TASK_FLOW.zh-CN.md) | candidate user journeys across game, local and cloud; permissions and truthful readiness |
| [Full member and Agent handoff](../../docs/NEW_MEMBER_HANDOFF.zh-CN.md) | installation, engineering and operational onboarding; root governance links |
| [Default project workflow](B_PIPELINE_HANDOFF.md) | existing download/install, account/device, upgrades and incidents; exact release controls |
| [New Engineer Guide](NEW_ENGINEER_GUIDE.md) | first checkout and ownership orientation |
| [Status](STATUS.md) | implemented/measured state and non-claims |
| [Project System](PROJECT_SYSTEM.md) | context/check/closeout and bounded memory |
| [Development Workflow](DEVELOPMENT_WORKFLOW.md) | branches, PRs, releases and exact dependencies |
| [Engineering Governance](ENGINEERING_GOVERNANCE.md) | ownership, failure models, review and evidence |
| [Testing](TESTING.md) | complete portable gate and external qualification boundaries |
| [Code Style](CODE_STYLE.md) | language, typed interfaces and determinism |
| [Architecture](ARCHITECTURE.md) | research/environment dependency direction |
| [Interfaces](INTERFACES.md) | versioned environment, data, model and artifact contracts |

## 当前研究设计与阶段计划

以下是设计／实施计划，不是已训练或已部署状态；现行合同与 owner 源码继续有效。

- [研究路线与历史维护](research/RESEARCH_ROADMAP.zh-CN.md)：阶段问题、交互作用、条件结论和重新验证。
- [S0 第一阶段：数据基础与 S01 最小闭环](research/S0_STAGE1.zh-CN.md)：历史小样独立评分验收和后续十二配置地图，不代替 1a 真实游戏验收。
- [统一数据管理设计](research/DATA_MANAGEMENT.zh-CN.md)：逐决策索引、固定数据集、可调整普通隔离、Gold与统一归档。
- [STPD 四家族四横轴模型设计](research/MODEL_DESIGN.zh-CN.md)：完整研究路线，B/C单主干与后续22配置。

## Research and operations

| Document | Responsibility |
|---|---|
| [Data and Provenance](DATA_AND_PROVENANCE.md) | admission, zones, splits and external data |
| [Data Lifecycle](DATA_LIFECYCLE.md) | canonical storage, derived features and staging |
| [Human Corpus](HUMAN_CORPUS.md) | profiles, verified bundles and corpus admission |
| [Qwen Integration](QWEN_INTEGRATION.md) | pinned backbone and cache contract |
| [Qwen L2 Operations](QWEN_L2_OPERATIONS.md) | exact weight admission and owner gates |
| [Live S1 Operations](LIVE_S1_OPERATIONS.md) | historical S1 policy adapter/parity |
| [Benchmarks](BENCHMARKS.md) | B0-B7 mechanics and evidence scope |
| [Scientific Protocol](SCIENTIFIC_EXPERIMENT_PROTOCOL.md) | historical combat-v0 protocol |
| [v0 Plan](V0_EXECUTION_PLAN.md) | retained combat-v0 study |
| [Roadmap](ROADMAP.md) | priorities and phase definitions |
| [Pre-Qwen Operations](PRE_QWEN_OPERATIONS.md) | historical L1 handoff |

## Evidence, decisions and memory

[AgenticSTS Audit](evidence/AGENTICSTS_DATA_ADMISSION_AUDIT_2026-08-22.md) and
[Data Lifecycle Closeout](evidence/DATA_LIFECYCLE_ENGINEERING_CLOSEOUT_2026-08-29.md)
remain scoped historical records. The Platform Annotator schema is externally owned;
stpd/data/human_annotator.py consumes it without making this map a second schema authority.

[ADR Index](adr/README.md), [ADR-0001](adr/0001-project-boundaries-and-current-smoke.md),
[ADR-0002](adr/0002-versioned-unified-human-serialization.md),
[Memory Instructions](memory/README.md), [Current Context](memory/CURRENT.md),
[Decisions](memory/DECISIONS.md), [Open Questions](memory/OPEN_QUESTIONS.md),
[Latest Handoff](memory/HANDOFF.md), and [Machine Contracts](../schemas/README.md).

Platform owns model-neutral environment/runtime/evidence contracts. STPD owns research,
data, representation, training and evaluation. Components share a repository and use declared package APIs. Historical release
pins retain their exact identities; they are not private cross-component imports.

[Local-First manifest ADR](adr/0003-local-first-manifest-research.md)

[Full-Run Research](FULLRUN_RESEARCH.md)

[Full-Run Training](FULLRUN_TRAINING.md)

## Pre-Full-Run convergence

- [Local operations and exact Worker launch](PREFULLRUN_OPERATIONS.md)
- [Local DuckDB and dashboard](LOCAL_WORKBENCH.md)
- [Gold and E0-E7 engineering tooling](FULLRUN_GOLD.md)

- [Pre-Full-Run AI closeout](PREFULLRUN_AI_CLOSEOUT.md)
- [Human and external-input handoff](PREFULLRUN_HUMAN_HANDOFF.md)

## Default project workflow and cloud B implementation

- [Unified task flow (中文)](UNIFIED_TASK_FLOW.zh-CN.md): the current candidate's user-facing
  route; implementation and deployment/Human qualification are explicitly separate.
- [Collection preparation and persistent upload pause](COLLECTION_FLOW.md): one consent
  action composing existing configuration, native and queue owners.
- [Local model tasks and explicit shared reports](LOCAL_MODEL_TASK_FLOW.md): supported
  preparation, native handoff, background finalization and separately authorized Agent sharing.
- [Selected decision unions](adr/0008-selected-decision-unions.md): exact selected rows,
  bounded verification reuse, durable task observations and qualification limits.

- [Shared local/cloud project console](PROJECT_CONSOLE.md)
- [ADR-0005: original console authority and authentication](adr/0005-local-cloud-console.md)
- [ADR-0006: Hub members, sharing and local model boundaries](adr/0006-project-members-and-local-models.md)

- [Download, collect, view, maintain and freeze the training plan](B_PIPELINE_HANDOFF.md)

- [B operations and external gates](CLOUD_PIPELINE_B.md)
- [Execution scope and acceptance plan](CLOUD_PIPELINE_B_PLAN.md)
- [ADR-0004: durable Hub](adr/0004-developer-cloud-hub.md)

- [B architecture and operational closeout review](evidence/B_PIPELINE_QUALITY_CLOSEOUT_2026-09-12.md)

- [First dedicated Human upload and projection audit](evidence/B_PIPELINE_FIRST_HUMAN_UPLOAD_2026-09-13.md)

- [Unified workflow release and bounded account/service qualification](evidence/B_UNIFIED_WORKFLOW_RELEASE_2026-09-13.md)

[Account and device protocol](IDENTITY_PROTOCOL.md) defines invited membership, approval,
personal sessions, current member/admin permissions, scoped devices, credential recovery and
operations schema-4 migration. The [Hub runbook](../deploy/hub/RUNBOOK.md) owns explicit bootstrap,
Access identity policy, exact deployment, backups and compatible rollback.
Its [host operations companion](../deploy/hub/OPERATIONS.md) covers operator-only SSH,
verified source-IP changes, lost access and daily capacity/backup inspection.

[Packaging identity correction](evidence/B_WORKFLOW_PACKAGING_IDENTITY_CORRECTION_2026-09-13.md) records exact BOM file bytes and distinct tool revisions.

[Measured Hub access and capacity recovery](evidence/HUB_ACCESS_CAPACITY_REPAIR_2026-09-14.md)
records the predecessor incident separately from later candidate qualification.

- [Developer kit initial installation](DEVELOPER_KIT_INSTALL.md): operator-assisted exact binary installation and rollback.
- [Daily collection Human audit](evidence/B_DEFAULT_COLLECTION_HUMAN_AUDIT_2026-09-15.md): actual transfer/access PASS and one unresolved native event Continue; not Full-Run PASS.

[Bounded B workflow acceptance](evidence/B_WORKFLOW_BOUNDED_ACCEPTANCE_2026-09-15.md) records the new zero-failure continued-run audit and owner-authorized engineering integration; continuous Full-Run remains unclaimed.

- [Fixed decision datasets and version updates](adr/0007-fixed-decision-datasets.md): permissive
  decision selection, immutable versions, run/fragment classification and exact compatibility.

- [S01 operator workflow](research/S01_WORKFLOW.md): fixed allocation, portable encoding, resume, export and archival.

- [1a/1b approved execution plan](research/STAGE1A.zh-CN.md): four local configurations, actual game entry, later 10k/Modal training.
- [1a local token workflow](research/STAGE1A_WORKFLOW.md): shared inputs, bounded training, checkpoint resume and standalone export/scoring.
- [Stage 1a token input and query execution ADR](../../docs/adr/0012-stage1a-token-input-and-query-execution.md): train-only tokenization, shared input lineage and frozen-prefix execution.
