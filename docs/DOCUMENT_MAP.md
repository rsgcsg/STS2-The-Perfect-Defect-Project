# Document map

This is navigation, not a work queue. Start with [CURRENT](memory/CURRENT.md) and
[shared working rules](WORKING_RULES.zh-CN.md). CURRENT alone links the current
assignment; product plans below are background/backlog until assigned.

## 按“我要做什么”进入

人和Agent使用同一业务流程。人的GUI优先游戏内Platform/工作台，外部窗口为备选；agent用受支持CLI/API/内部库。下表给操作的唯一现有owner；其中状态页、合同和历史证据仅解释边界，不另维护命令副本。进入指南后先核该组合是否受支持，按其完成判断与失败路径操作；找到入口不代表当前候选已验收。

| 我要做什么 | 进入原操作指南 | 结果与边界在哪里看 |
| --- | --- | --- |
| 安装、升级、核游戏加载或回滚 | [固定开发工具安装](../python/docs/DEVELOPER_KIT_INSTALL.md)，原生生命周期由[Game Mod](../apps/game-mod/README.md#lifecycle)执行 | 精确发布清单、install/load回执；源码编译不代装载 |
| 正常录制、查状态、Pause/Close、开始下一局 | [游戏内Platform/采集操作](../apps/ingame-ui/README.md)→[采集指南](ANNOTATOR_COLLECTION.md#current-p1-daily-collection)，同一应用入口见[日常录制](../python/docs/PROJECT_CONSOLE.md#daily-collection) | 原生身份、session状态、Close结果与原件；页面关闭不是Stop，真人来源由操作者声明 |
| 异常录制、保留前缀、封包和恢复导入 | [采集故障定位](ANNOTATOR_COLLECTION.md#detect-and-route-a-problem)与[同一应用导入](../python/docs/PROJECT_CONSOLE.md#本机资料) | 原始失败、prefix/封存范围和Tool结果；普通重连/旧semantic recover不能冒充P1异常恢复，Continue另行保留 |
| 选择数据、查谱系/用途、固定数据集 | [正常资料与固定数据集操作](../python/docs/PROJECT_CONSOLE.md#prepare-the-existing-library-and-fix-a-dataset)，合同/设计见[数据与来源](../python/docs/DATA_AND_PROVENANCE.md)、[固定数据管理](../python/docs/research/DATA_MANAGEMENT.zh-CN.md)；用途账恢复见[原账备份/按ID恢复](../python/docs/DATA_LIFECYCLE.md#private-curation-ledger-recovery) | Curation用途/分组与来源事实；目录数、内容校验和上传成功不授予训练资格 |
| R2保全、按ID取回、管理本地热集与恢复 | [真身/工位/删除条件](../python/docs/DATA_LIFECYCLE.md#项目存储真身工位与本机删除条件)及[数据生命周期操作](../python/docs/DATA_LIFECYCLE.md#daily-p1-store-and-r2-operations)，P1日常入口由[项目应用指南](../python/docs/PROJECT_CONSOLE.md#本机资料)拥有 | 原件/派生/产物的Store身份与回读结果；释放须有可恢复artifact及实际consumer，不据旧清理快照删除 |
| 训练start/status/pause/resume/cancel/reconcile | [正常训练服务与控制](../python/docs/PROJECT_CONSOLE.md#non-gui-training-control) | 同一Application操作、实际handle、累计预算和结果；具体支持能力以capabilities为准，unknown不重投 |
| 运行P1训练、checkpoint恢复，准备GPU执行 | [P1 N-only workload](../python/docs/STRUCTURED_WORKLOAD.md#p1-n-only-m-std-workload)；[P1模型、恢复与GPU准备](../python/docs/P1_T0.md)（T0本身是CPU工程验证） | 同任务输入/执行身份、更新/checkpoint/模型/结果；正式GPU实际闭环及数据/配方/费用决定未具备时不能按T0启动首训 |
| 导入/注册模型、组合Agent并在游戏使用 | [正常P1 Model操作](../python/docs/PROJECT_CONSOLE.md#p1-model正常下载注册与使用)；原[模型/Agent编排](../python/docs/LOCAL_MODEL_TASK_FLOW.md#prepare-and-load)及[工程绑定](../python/docs/P1_T0.md#private-installation-and-existing-runtime)保各自范围 | 模型注册/兼容性、AgentComposition、环境及Runtime回执；register不等于激活或真实运行 |
| 固定模型离线评分、Managed/Reference比较 | [模型交付与评分](../python/docs/STRUCTURED_MODEL_DELIVERY.md)、[评测suite](../python/docs/MANAGED_SUITE_EVALUATION.md) | 具体模型/分组/seed与完整分母；Reference窗口由原owner分配，技术完成与策略结论分开 |
| 开发功能、修bug、选择或维护测试 | [工程入门](NEW_ENGINEER_GUIDE.md) → [工作流程](DEVELOPMENT_WORKFLOW.md)、[测试选择](TESTING.md#local-feedback-and-candidate-checks) | 真实owner和首错、最低忠实反例、稳定候选所选门；不为小文档改动重跑游戏/训练 |
| 修改协议、版本或迁移旧consumer | [架构变更规则](ARCHITECTURE.md#selected-convergence-and-change-rules)、[版本](VERSIONING.md)、[原ADR](adr/README.md)，再到对应合同 | 具体语义/表示差异、生产者/消费者和迁移证据；不靠同名schema或新SHA授资格 |
| 审查、CI、合入、发布与rollback | [开发/发布流程](DEVELOPMENT_WORKFLOW.md#normal-change-and-release-sequence)、[测试与证据](TESTING.md)、[版本](VERSIONING.md) | 精确PR/head与源/构建/装载证据；具名合入/发布按已有授权，旧green不转贴 |
| 账号、设备、凭据恢复或访问事故 | [账号与设备](../python/docs/IDENTITY_PROTOCOL.md)、[本机恢复](../python/docs/PROJECT_CONSOLE.md#credential-recovery)；operator看[Hub运行手册](../python/deploy/hub/RUNBOOK.md#normal-operations-and-incident-handling) | 成员、设备、录制/分享同意分别处理；账号登录不能替Human声明或数据用途 |
| 容量、备份、服务不可达、灾难恢复 | [数据保留](../python/docs/DATA_LIFECYCLE.md#invalidation-and-retention)、[Hub运维](../python/deploy/hub/OPERATIONS.md)、[暂停态恢复](../python/deploy/hub/RUNBOOK.md#restore-into-paused-state) | 当下进程/容量、备份和恢复验证；本地P1原件仍由Store路径处理，不强制经Hub |
| 写文档、复用旧解法、人/Agent协作续接 | [共同工作规则](WORKING_RULES.zh-CN.md)、[文档归属](PROJECT_SYSTEM.md)、[AI专属协作](AI_COLLABORATION.md)、[旧解法](RESOLVED_ISSUES.zh-CN.md) | 原owner同PR维护；CURRENT只链接一份当前任务，历史不派工 |

九类支持操作由这些原指南共同覆盖。未实现操作须在对应guide/当前任务留owner和下一步；本导航不将缺口改成“未来”来宣布全项目收敛，也不要求现在实现已明确延期的Continue或未知学校接入。详细功能去向仍只维护在[原功能表](plans/p1-prefreeze-2026-10-18/FEATURE_STATUS.md)。

## One project route

The [P1 topic map](plans/p1-prefreeze-2026-10-18/README.md)
connect the existing Architecture, Components and ADR-0016 in this order:

| Read for | Existing owner / route |
| --- | --- |
| Goal | A useful full-game Agent that can improve; current work is the pre-training P1 freeze, then Q1–Q3 |
| Upper chain | [Architecture](ARCHITECTURE.md): world/Host → environment → complete Agent, and immutable sources → projection → learning → Model/Agent → execution/evaluation |
| Protocols and contracts | [P1 r1](protocols/P1_GLOBAL_R1.zh-CN.md), [ADR-0016](adr/0016-agent-composition-and-execution-request.md); record grammar, environment agreement, Agent program port and Model Input/Target stay distinct |
| Functions and capabilities | [Feature status table](plans/p1-prefreeze-2026-10-18/FEATURE_STATUS.md): one historical capability per row, owner, evidence and scoped status |
| Components | [Components](COMPONENTS.md) and the component routes below |
| Why each part exists for the first training run | [Chain purposes](plans/p1-prefreeze-2026-10-18/CHAIN_PURPOSES.md): online loop, independent recording DAG and research loop; reuse API/library/CLI and thin GUI, leave Hub/team/cloud pages at their existing status |
| Formats | [Recording history](plans/p1-prefreeze-2026-10-18/RECORDING_HISTORY.md), [format/cost table](plans/p1-prefreeze-2026-10-18/RECORDING_FORMATS_AND_COST.md), [P1 section10](protocols/P1_GLOBAL_R1.zh-CN.md#10-记录与转换); current Agent directories and legacy single-file records are different lanes |
| Status and gaps | [Scope v0.3 / coverage](plans/p1-prefreeze-2026-10-18/SCOPE_v0.3.zh-CN.md), [actual Managed differences](plans/p1-prefreeze-2026-10-18/MANAGED_DIFFERENCES.md), [P1 exceptions](protocols/P1_GLOBAL_R1.zh-CN.md#17-登记的协议例外必须经用户审核才能新增) |
| Historical assets/documents | [Asset ledger](plans/p1-prefreeze-2026-10-18/ASSETS.md), [current document routes](plans/p1-prefreeze-2026-10-18/DOCUMENT_INDEX.md), [archive index](plans/p1-prefreeze-2026-10-18/ARCHIVE_INDEX.md); navigation labels and proposed destinations do not mean anything was moved or closed |

The [scope definition](plans/p1-prefreeze-2026-10-18/SCOPE_v0.3.zh-CN.md)
retains its selected requirements and explicitly proposed details. Current implementation
authority comes from the user instruction and CURRENT-linked task; implementation
authorization alone does not freeze or qualify a candidate. [Cloud](plans/p1-prefreeze-2026-10-18/CLOUD_MINIMAL.md) and
[Workbench](plans/p1-prefreeze-2026-10-18/WORKBENCH_POSITION.md) reuse existing owners;
uploads/paid work and complex GUI/team expansion are not authorized by the map.
The [asset ledger](plans/p1-prefreeze-2026-10-18/ASSETS.md) distinguishes proposed
dispositions from the exact [K3 executed receipt](plans/p1-prefreeze-2026-10-18/K3_EXECUTION.json).
Remaining acceptance is recorded in the CURRENT-linked task. These are routes into
existing owners, not another protocol, identity store, registry or governance checker.

## P1 forward decision profile

- [Selected decisions and supersession](DECISIONS.md)

- [P1 public information and decision grain](protocols/P1.zh-CN.md)
- [P1 Host and wire mapping](protocols/P1_HOST_AND_WIRE.zh-CN.md)
- [P1 global r1 executable contract, evidence and gaps](protocols/P1_GLOBAL_R1.zh-CN.md)
- [Offline C1 archival comparison](P1_CAPTURE_COMPARISON.md)
- [ADR-0017: P1 forward profile](adr/0017-p1-forward-decision-profile.md)
- [P1 plan and bounded history](plans/P1_FORWARD_2026-10-15.zh-CN.md)

These documents distinguish the user-selected direction, proposed implementation
contract and actual qualification. Historical plans retain evidence, not a second
current work queue.

Start with README → [CURRENT](memory/CURRENT.md) → the active task and owning
specification. Read only the route needed for the next decision. This is a map,
not another requirements, identity or status registry.

## Historical baseline and retained architecture

- [V1 specification](BASELINE_V1_SPEC.zh-CN.md): earlier required outcomes,
  design, rationale, assumptions and unimplemented choices; current selection is above.
- [Agent composition/execution decision](adr/0016-agent-composition-and-execution-request.md):
  accepted architecture/migration direction; actual common startup/application seams
  are owned by [Agent execution](../python/docs/AGENT_EXECUTION_CONTRACT.md).
  Structural implementation, complete P1 acceptance and future training mapping are separate.
- [Execution and acceptance matrix](plans/BASELINE_G2_V1_EXECUTION_2026-10-08.md):
  historical packets, dependencies and exact remaining evidence; no new authorization.
- [Task index](plans/BASELINE_TASKS.zh-CN.md): stable P/E/G/V/R IDs.
- [Boundary round](plans/BASELINE_BOUNDARY_REGRESSION_ROUND_2026-10-10.md):
  historical supervisor authorization, native failure accounting and finite legacy
  PR entry points; historical restrictions below its updates stay historical.
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
| Why test, when add/reuse/retire, and what must run? | [Test lifecycle](TESTING.md#test-design-and-maintenance), [CI/cost decisions](TESTING.md#ci-responsibility-and-cost-decisions), existing `check:plan` and owning tests |
| How are branches, integration, release and deployment handled? | [Workflow](DEVELOPMENT_WORKFLOW.md), [Versioning](VERSIONING.md) |
| How is work scoped, stopped and accepted? | [Shared working rules](WORKING_RULES.zh-CN.md); [AI-specific collaboration](AI_COLLABORATION.md) |
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
