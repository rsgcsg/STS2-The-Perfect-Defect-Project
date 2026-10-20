> Project migration: application ownership and active layout are defined in
> [MONOREPO_MIGRATION](MONOREPO_MIGRATION.md). Game/evidence authority below remains unchanged.

# Components

For the selected cross-system direction, read [P1](protocols/P1.zh-CN.md) and
[ADR-0017](adr/0017-p1-forward-decision-profile.md); the earlier
[baseline](BASELINE_V1_SPEC.zh-CN.md) retains its historical scope.
The [chain-purpose map](plans/p1-prefreeze-2026-10-18/CHAIN_PURPOSES.md) explains
each retained component/protocol/format's position, purpose and first-training value.
The table below owns component placement and authority; the execution packet and
STATUS separately track implementation and qualification. Historical proposals
do not move these boundaries or establish current capability.

[Architecture](ARCHITECTURE.md#product-boundary) maps the logical Agent interaction
standard through Connector to Host capabilities, with an execution context across
those roles. Host is not synonymous with Host Runtime; SDK is an implementation
library, and the Runtime-to-Agent program port is distinct from the environment
interface. The table names current components, not mandatory deployment hops.
The [selected convergence](ARCHITECTURE.md#selected-convergence-and-change-rules)
retains a common ExecutionRuntime kernel and separately supervised default;
Policy Runtime remains the component name. Native dependency-interface extraction
precedes any future SDK-direct driver, under the same execution guarantees.

> Accepted direction: [ADR-0015](adr/0015-native-logical-interaction.md) owns the
> native logical-page/action/memory target and the two contrasting selection
> examples. Acceptance covers that upper-level direction and its stated
> conditions, not a complete-game interface or runtime qualification.
> The explicit [text-menu-v2 profile](../components/connector/docs/TEXT_MENU_PROFILE.md) and bounded
> Host/STPD paths are implemented. Connector/Native Foundation own page and
> operation mappings; Annotator/Evidence own faithful records and compatibility;
> STPD owns input/history projections and learned memory; Policy Runtime owns
> bounded execution and confirmed feedback.
> Shared capability scope must be coherent across catalogs, UI, execution and
> capture eligibility, without adding a second legality or causal authority.
> Broader page mappings and cross-Host, native, Human and model qualification
> remain scoped follow-up work. Existing boundaries and historical evidence
> below retain their original meaning.

| Component | Path | Owns | Must not own |
|---|---|---|---|
| Native Foundation | `components/native-foundation` | STS2-owned semantic decisions, native lifecycle, process-local owner lineage | transport, evidence, public action authority, input execution |
| Connector | `components/connector` | Player Environment contract and mapping to Host capabilities, native binding/execution, REST/MCP, SDK implementation | process lifecycle, strategy, annotation |
| Host Runtime | `components/host-runtime` | discovery, isolation, launch/reset/stop, headless/managed experiments, qualification | gameplay legality, research models |
| Annotator | `components/annotator` | native interaction/source recording; native-human witness and one semantic causal tracker for their declared contracts; derived Decision/canonical projections, audit/export/bundle, workstation | action authority, research admission, a second causal adjudicator |
| Platform Evidence | `components/evidence` | typed verification, content identity, immutable store, transfer/receiver receipts | research eligibility, corpus policy, mutation |
| Policy Runtime | `components/policy-runtime` | current supervised Agent drivers, Human/Shadow/One-Step/Auto, controller lifecycle, finite budgets/Stop, stale/Receipt/successor and Agent-run evidence | model inference, legality, native operands, candidate filtering |
| Platform diagnostic API | `apps/workbench` | typed live status, explicit filesystem fallback, bounded Policy Runtime commands | gameplay submission, evidence admission, model loading |
| Platform Live UI | `apps/ingame-ui` | in-game Environment/Policy/Human Data/Diagnostics presentation and typed application commands | packaging/deployment, direct BoundAction submission, legality, recording writes |
| Platform Game Mod | `apps/game-mod` | one manifest/DLL, explicit component initialization, exact build/install/load/rollback provenance | gameplay legality, Human witness semantics, UI domain logic |
| Platform tools | `tools` | composition, component identity, migration/boundary checks | native operands, policy |
| Project applications | `python/spireagent` | one local/cloud console, members/devices, upload/export, operations and shared artifact infrastructure | legality, recording proof, research labels |
| STPD | `python/stpd` | ResearchTransition, Dataset Views, representation, Qwen, training/evaluation | Host implementation or legality |

## Training responsibilities

下表展开研究与应用的既有职责，不新设组件或第二套训练系统。职责的内部细分仍受[架构细分规则](ARCHITECTURE.md#responsibility-refinement)约束；具体是否可用和本轮验收从CURRENT进入。

| 接缝与职责 | 现有实现与操作owner | requires → provides |
| --- | --- | --- |
| 原件与学习表示 | 原Annotator/Evidence/Store；`stpd` P1 reader/projection，见[数据与来源](../python/docs/DATA_AND_PROVENANCE.md)与[P1输入说明](../python/docs/P1_T0.md#input-targets-and-provenance) | 原始身份、公开时点、chosen/缺口、谱系及用途 → 固定Input/Target和mask；不补造Human或独立分组 |
| 决定数据用途 | `spireagent.p1_curation`与既有Curation，见[数据管理](../python/docs/research/DATA_MANAGEMENT.zh-CN.md) | 有依据的来源/亲缘/曝光与用户选择 → 用途/固定分组；存储成功和GPU可用不授予准入 |
| 数值训练与保存 | `stpd.models.structured_training`、原P1训练和worker，见[workload](../python/docs/STRUCTURED_WORKLOAD.md) | 固定recipe、输入、执行身份和明确checkpoint → 实际更新、全状态checkpoint、模型与结果；不是应用权限owner |
| 任务生命周期 | 既有Application/LocalTrainingService，见[正常训练控制](../python/docs/PROJECT_CONSOLE.md#trusted-local-training-service-contract) | 具体请求、权限、累计预算、操作/attempt身份 → start/status/支持的pause/resume/cancel/reconcile及持久结果；关客户端不是Stop |
| 执行资源 | `spireagent.compute`及现provider/worker接缝，见[结构化执行](../python/docs/STRUCTURED_WORKLOAD.md) | 同一具名任务/输入/产物承诺、资源和恢复能力 → 本地/远端实际handle与返回产物；unknown不换provider重投 |
| 产物到Agent、评估 | 原Model注册、policy adapter与Runtime，见[模型交付](../python/docs/STRUCTURED_MODEL_DELIVERY.md)、[Agent执行](../python/docs/AGENT_EXECUTION_CONTRACT.md) | 兼容权重/Input、完整动作目录和明确Task → 完整Agent执行与固定条件结果；模型包不自获游戏控制权 |

T0是CPU工程验证入口。正式GPU任务复用相同的用途、任务、checkpoint、Store和产物消费者；当前后端是否完成实证由其运行证据决定，不能从上述映射或旧GPU训练推出。学校资源在接入资料到齐后适配真实差异，不预设调度器或跨设备exact resume。

The user's 2026-10-20 freeze requirement covers every historical/current function,
including small utilities, recovery, viewing, downloads and cloud operations.
Each function resolves to one responsible component, a supported entry and its
actual core implementation, connected requires/provides, protocol/format versions,
evidence level and disposition in the existing
[feature table](plans/p1-prefreeze-2026-10-18/FEATURE_STATUS.md) and K asset ledger.
GUI/CLI/API may be multiple thin surfaces over that same owner. Historical items
must be evidenced as retained, replaced, archived or unfinished; an archive tag or
inventory count alone does not complete migration. This is not a request to merge
all components, rebuild all experiments or create another ownership registry.

One function has one business meaning, fact authority and core state machine.
The application owner orchestrates existing components for a composite use case;
GUI, CLI and Agent surfaces map into that use case rather than copying its flow.
Historical code must meet today's contract: adapt representation at the existing
seam, repair/replace an implementation that misses requires, and version the
owning contract when authorized information/action/timing/failure/use semantics
change. An unexpressible real use case, repeated ownership overlap/consumer
patches or measured unacceptable cost warrants an evidenced upper-level proposal,
not another adapter merely to retain the old implementation. The four change
classes and approval boundary remain in Architecture. Different role formats
remain distinct; a future supported combination is never an implemented claim.

Application/use-case and resource organization cuts across the online, recording
and research chains. Workbench GUI is the optional human mapping; the existing
`python/spireagent/workbench` directory also contains the local Application and
services, rather than defining another upper-level authority. `apps/workbench`
is the retained diagnostic API, console is presentation and Hub is a remote
application/permission implementation. A running use case may need an application
service without a browser; closing its page is not Stop. Direct offline library
calls keep their owning contract and need no forced HTTP/Application hop.

Every project GUI business function and its status/failure handling must have a
supported, discoverable non-GUI entry through CLI, API, library or agent tooling.
Agents do not operate the Workbench GUI to execute business use cases. These entry
types may divide the supported surface; they need not each duplicate every command.
A private function requiring permission bypass, database editing or a private daily
script is not a delivered entry. GUI presentation QA and native Human gameplay
are distinct from this business-entry requirement. The dated K6 investigation is
limited to its original scope, not a finding that all current functions satisfy it.

For the current pre-training freeze, internal API/library/CLI calls are the main
operation surface. Existing browser and native UI remain thin mappings to the same
application use cases, including their presentation/confirmation/pairing adapters.
The complete historical in-game/team/cloud UI target is not a requirement to build
new complex GUI or team systems now. Hub/cloud components retain their current
interfaces/status under the [minimal cloud proposal](plans/p1-prefreeze-2026-10-18/CLOUD_MINIMAL.md).

Native Foundation is deliberately compiled into the game-side Connector Host
and unified Mod rather than published as a second runtime service. Its typed
providers own read-only projections of STS2 semantic state and action space.
Connector consumes those facts to project fair-player visibility and current
deliverability; `A_public` may be empty while input is settling without erasing
the execution semantic action space. Annotator preserves the same typed
observation for evidence without gaining legality or mutation authority.
Inside Annotator, `SemanticBoundaryTracker` is the sole current runtime causal
authority. Current Decision and canonical-transition files are non-authorizing
projections from an already proved semantic transition. The
[Full-Run data chain](FULL_RUN_DATA_CHAIN.md) owns the exact causal lane versions
(trace4/canonical3/bundle3), restricted record2 compatibility projection and
archival readers. Source3/4 and direct Agent evidence retain their separate
grammars. Historical Decision V1/V2 and native-action-ledger contracts are not
current runtime admission state machines; retained readers do not restore that authority.

Each imported component retains its focused `AGENTS.md`, tests and operational
documentation. Those files add component-specific constraints but cannot
override the root hard shell.

Standalone component tests compile Annotator against the exact Connector build
artifact. The production Game Mod instead compiles both source authorities into
one assembly and disables their standalone initializer attributes; it does not
copy or wrap either implementation.

The versioned Host Runtime package is the public programmatic boundary for its
Node driver/CLI and strategy-free Python client. Consumers provide external
candidate artifacts; they do not import a sibling source checkout.
The broader Host capability contract includes native/Connector facts beyond this
launcher. Its core/extension/Agent-and-Task requirement mapping remains distributed
across owning contracts; shared API conformance does not establish cross-Host behavior
or native qualification. See [Host capabilities](ARCHITECTURE.md#host-capabilities-and-requirement-matching).

The versioned Evidence Python package is the portable evidence-integrity
boundary. It verifies current and explicitly archival Human bundles and owns
local transfer mechanics;
research consumers remain responsible for their own admission semantics.

The Policy Runtime is a Connector consumer. The legacy Policy Manifest names exact
model, adapter, representation, Reads, support and environment requirements.
That scoring adapter returns an ordered score vector and selected index; Runtime
resolves the index against the unchanged current Connector catalog. The explicit
Agent Manifest instead selects the [generic Agent session port](../components/policy-runtime/docs/AGENT_SESSION_PROTOCOL.md),
with declared consumption/query behavior, Act/Await/Abstain/Close and optional
scores. Its selection still binds the original Connector catalog through an exact
handle or unique Resolve. Both branches share one control/lifecycle owner. Workbench
and Live UI issue only typed Runtime or Annotator application commands. STS2
loads one `STS2_PLATFORM` manifest; logical authority does not follow DLL count.

SDK-direct or in-process Agent execution is a possible deployment within that
same execution context, not a second protocol or a currently qualified ordinary
product driver. It must preserve the existing admission, expiry/Stop, unknown,
state/consumption and evidence guarantees. Existing drivers need not be retained
forever; [ADR-0016](adr/0016-agent-composition-and-execution-request.md) governs
consumer migration and retirement without rewriting historical artifacts.

The [baseline specification](BASELINE_V1_SPEC.zh-CN.md#2-上层抽象与唯一责任)
owns the common upper terms. Host capability, Agent-facing environment interface
and Runtime-to-Agent program port are different boundaries mapped to existing components;
they are not additional services. A complete Agent may compose fixed rules,
optional Models and shared libraries. A Model does not itself acquire lifecycle
or gameplay permissions, and a research TargetSpec does not enter current inputs.
