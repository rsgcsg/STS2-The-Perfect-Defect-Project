> Project migration: application ownership and active layout are defined in
> [MONOREPO_MIGRATION](MONOREPO_MIGRATION.md). Game/evidence authority below remains unchanged.

# Architecture

The current direction is the [P1 forward decision profile](protocols/P1.zh-CN.md),
with the proposed refinement in [ADR-0017](adr/0017-p1-forward-decision-profile.md).
The [earlier baseline](BASELINE_V1_SPEC.zh-CN.md) retains historical obligations
and evidence; it no longer dispatches the former dual learning chains.
This document owns durable
component authority and existing architectural boundaries. Protocol-specific
implementation and compatibility remain in their owning contracts; candidate
qualification is tracked by [STATUS](STATUS.md). Historical design proposals are
linked from [DOCUMENT_MAP](DOCUMENT_MAP.md), not additional current requirements.

## Product Boundary

STS2 AI Platform provides a real-game Host, fair-player Player Environment,
native interaction recording, model-neutral policy execution lifecycle and
strategy-free integration tools. It does not own policy inference, reward,
models, training or research authority.

The [baseline specification](BASELINE_V1_SPEC.zh-CN.md#2-上层抽象与唯一责任)
maps one set of logical roles to these components:

```text
Agent <-> Agent interaction standard <-> Connector mapping <-> Host capability contract <-> world
  program, acquisition, input/history/state, choosing/timing, optional Models

Execution context: composition + Task + actual resources/instances + permission
                   finite budgets, Stop, original request/result/evidence association
```

The Agent interaction standard fixes the selected public information, operations,
ordering and failure semantics. Connector maps that contract to the capabilities
of a concrete Host; the Agent need not know Host implementation objects. The SDK
is an implementation library for this environment interface, not another game
protocol. The Runtime-to-Agent program port is a separate message boundary for
consumption, queries and directives. JSON, HTTP or NDJSON transport alone does
not define a different interaction standard. Model denotes an optional computation
component with fixed or learned parameters; research owns targets through TargetSpec.

The execution context cuts across these roles; it is not a new service, controller
or registry. Workbench, Host Runtime, Policy Runtime and SDK currently implement
their assigned parts. Policy Runtime supplies the current supervised execution
path, not a permanently required process hop in every possible Agent deployment.
A trusted SDK-direct or in-process driver is architecturally possible, but a complete
ordinary product path for it is not established by the existing SDK probes. It must
retain admission, finite budgets, expiry/Stop, control release, unknown fencing,
actual consumption/state boundaries and original evidence under the same owners.
A Host lease or HTTP timeout alone does not supply those guarantees. Blocking or
crashing an Agent must not disable enforcement; isolation or external supervision
is chosen from that failure model. The Runtime must not add undeclared browsing,
input history or choosing strategy outside the complete Agent.

These are logical roles, not mandatory separate processes. [ADR-0016](adr/0016-agent-composition-and-execution-request.md)
owns composition, execution requests and migration. Existing scoring and Agent-session
drivers preserve their original semantics while used; this is not a requirement to
retain both forever. Retirement follows actual consumer, parity, rollback and archival
reader needs, without rewriting old records, identities or qualification.

### Responsibility refinement

目标驱动职责分解；模块、协议、格式和部署实现这些职责。抽象与具体是相对当前边界而言：调用者只需训练器的输入、产物、失败和恢复承诺，维护者可继续展开其学习定义、数值计算、生命周期、执行资源和产物验收。只在真实调用、变化原因或独立验证需要时局部细分，各分支可以有不同深度。不要把每个函数展开进全项目图，也不把每项职责强制变成服务、进程或一个工程Agent。

“下载→训练→上传”是操作步骤，不自动构成三个好模块。模块应封装一起变化的规则和状态；若一个训练对象偷偷修改进程全局Torch配置，它与另一个评分器仍有实际耦合，目录分开并未解决问题。设计要减少使用者必须了解的内部细节，同时明确真实共享状态的owner，不追求零耦合。

每次展开先回答父能力给谁什么承诺、子职责分别需要/提供什么、组合是否覆盖原承诺。内部整理且保持承诺，归原owner实现；改变外部信息、动作、学习目标、用途、失败/恢复含义或上层职责，按下面变更规则先取得具体决定。接口的身份、成功、失败、停止和恢复含义必须明确；JSON、HTTP、文件或机器可以后选。父能力不能表达真实用例时，提出最小上层修订，而非另起一条业务链。

训练各接缝沿现有[组件映射](COMPONENTS.md#training-responsibilities)：原件到模型输入/目标归Projection/InputSpec/TargetSpec；选定训练任务到引擎归原workload/recipe；任务到Modal或以后学校资源归compute/provider；Model到完整Agent归模型包和Agent适配。所谓“适配器”必须先指明这两边，不能借名称新造统管训练、数据用途和云权限的全局层。逻辑细分与运行时协作是两个视图，职责树不表示所有数据必须依次经过的流水线。

### Selected convergence and change rules

Retain one ExecutionRuntime kernel and the current separately supervised process
as the default deployment. ExecutionRuntime names the controlled-execution role;
the existing component remains Policy Runtime. Renaming it or eliminating a process
hop is not a delivery goal. The selected default is:

```text
Agent process <-> AgentProgramPort (current NDJSON implementation)
              <-> ExecutionRuntime <-> Connector SDK <-> Connector <-> Host/world
```

AgentProgramPort is the complete Agent's program boundary, distinct from the
Agent-facing environment contract above. AgentProgram owns acquisition, strategy,
choosing/timing and optional Models; current trusted execution supports zero or
one Model. Runtime owns its admission, budgets/expiry/Stop, controller lifecycle,
unknown fencing, consumption association and evidence duties. It owns neither
inference nor gameplay legality; Connector and STS2 retain their authorities.
The complete Agent may include a declared compatibility driver's fixed timing
or legacy acquisition semantics. The process diagram does not move that behavior
out of the Agent definition or authorize changing it during migration.

For architecture cleanup, first extract narrow Agent-port and environment dependencies within the native
execution owner, keeping REST and NDJSON as current implementations. This is the
selected next convergence, not a completed source change or a general SDK-direct
migration. This refactoring order does not block game/data work whose own prerequisites
are satisfied. There is no benchmark establishing IPC as the principal cost. A later
SDK-direct/in-process driver needs a real capability or measured cost reason, and
the same execution owner/request/permission/evidence with common Stop/deadline,
late-response, unknown and consumption/state guarantees. It must not create an
independent run-management loop or substitute a Host lease for the user's budget.

For the new P1 profile, a reviewed semantic operation may lower to multiple native
steps and explicit protocol commitments. Catalog completeness applies to that
declared profile; underlying native catalogs and legacy contracts remain complete
in their own scope. This is a versioned owning-profile change under ADR-0017, not
consumer-side filtering or a new legality engine. Original microsteps, partial
execution and unknown remain observable in the operation evidence.

Every interaction profile declares both its online requires (environment-to-profile
and profile-to-Agent) and its originals-to-profile requires: observation fields and
source time/public basis, accepted choices, ordering/results/continuity, completeness
and rejection, and the declared projection. A recording source supports that profile
only when its provides, through that projection, cover those requires. The current
[P1 originals contract](protocols/P1_GLOBAL_R1.zh-CN.md#41-录制原件派生p1的requires用户已批准)
and [numbered working diagram](plans/p1-prefreeze-2026-10-18/CHAIN_PURPOSES.md)
instantiate the existing logical roles; adding a source or replacing a profile does
not create another application/control/data chain. An implementation cannot define
downstream semantic requirements on its own.

Every current, historical and future implementation follows the same change rules:

| Change | Owning treatment |
| --- | --- |
| Representation or transport only | Adapt within the existing SDK/port/Input owner; preserve information/exposure order, actions/cancel, consumption/reset and error semantics. |
| Capability or interaction meaning | Revise the owning versioned protocol/profile and its producer/consumer, recording and migration checks. Do not hide changes in an adapter. |
| Component implementation substitution | Match declared requirements and run conformance, including failure and lifecycle guarantees; behavioral/native equivalence needs its own scoped evidence. |
| Repeated duplicate authority, unexpressible real use case or unacceptable measured cost | Revisit the responsible abstraction under ADR-0016 and governance, with a minimal migration and retirement plan rather than preserving implementation shape. |

These rules apply to the selected execution kernel and the logical interaction
chain independently of deployment. Host core/extensions below are normative
obligations to make explicit through real use cases and owning machine contracts;
they do not declare a universal API already implemented or require every optional
capability. Default supervision and legacy compatibility drivers do not freeze
the architecture against evidence-backed future substitution or retirement.

#### Components, contracts, protocols, formats and deployment

P1 is one replaceable interaction contract/profile, not only a JSON format.
Evidence supports a specific proposition on a connection and within its declared
version and scope. Native replay alone does not qualify a P1 projection or another
Host implementation; those connections need their own applicable evidence.

A component implements a bounded responsibility; it may be a library/module,
application owner or separately supervised worker. A contract defines inputs,
outputs and guarantees, including information/source meaning, state/reset,
ordering, known failure, cancellation and unknown. A protocol defines the
conversation that realizes a contract over time. A format encodes its data;
equal JSON fields or bytes alone do not establish semantic compatibility.
Deployment chooses processes, machines and resource isolation; it does not
create a new logical authority or require one process per component.

These distinctions apply to the existing roles in the
[baseline](BASELINE_V1_SPEC.zh-CN.md#2-上层抽象与唯一责任) and
[ADR-0016](adr/0016-agent-composition-and-execution-request.md):

| Boundary | Existing owner | Contract that must remain explicit |
| --- | --- | --- |
| Recording reader/import | Annotator/Evidence and application Source import | Original facts, producer/origin, order, gaps, integrity and immutable references; research admission remains external. |
| Projection/Input/Target | STPD research readers/projectors | Model-facing fields, legal information, action grain, history/advance/reset, targets/masks and declared loss or reexpression relative to original records. |
| Model | Research numerical/inference code | Computation, parameter/state inventory, fixed Input/output semantics and transition law; E/W/N may be jointly trained internal modules, not platform services. |
| Complete Agent | Declared Agent program/adapter and application composition | Acquisition, Model(s), history, timing/choice/Goal and required environment capabilities; outputs bind the unchanged complete source-owned action catalog. |
| Learning | Research trusted recipe/engine plus application TrainingService | Frozen data/use, objective, actual update counting, operation/attempt authority, finite budgets, checkpoint and terminal/result identity. |
| Remote execution/resources | Existing worker/provider adapters and shared training operations | Same TrainRun intent/data/config, real job/attempt, device compatibility, cancellation/recovery, cumulative cost and independently verified returned artifacts. |
| Evaluation | Research metrics and application Task/result readers | Actual scene/run/exposure, completion/censor/unknown, delivery versus effect, quality/cost metrics and the evidence supporting each conclusion. |
| Shared applications | Existing CLI/API/Workbench/native UI owners | The same use cases, capabilities, permissions and operation/result references; interfaces do not create another ledger, controller or training manager. |

Decouple source format from model input through a declared projection, not by
pretending all sources contain the same facts. Offline training need not run a
live SDK; its projector and the Agent's online adapter must agree where matching
input/history semantics are claimed. Preserve missingness, related-group exposure
and original choice bindings. Model computation is separate from gameplay
permission and the complete Agent; learned W is not hidden world state or a
reconstruction of Human attention. Learning and evaluation keep different purposes.

Within an existing environment contract, changing Agent acquisition, waiting or
choosing is an Agent/composition change, not automatically a new environment
protocol. Changing available information, action/cancel opportunities or failure
meaning revises the owning interaction contract and affected consumers. Swapping
CPU/local execution for a provider does not replace data/model management: reuse
relevant historical successful mechanisms through the same TrainRun/checkpoint/
config/result contracts and qualify the new backend. That convergence is selected;
it does not assert a current structured GPU implementation or completed remote run.

Use the change rules above to version the affected boundary and preserve actual
consumers/readers. No new universal schema, global registry or additional service
is implied by this map; unsupported combinations remain explicitly unsupported.

### Host capabilities and requirement matching

Host means the concrete world's capabilities, not the Host Runtime process launcher.
Native Foundation supplies native semantic/lifecycle seams; Connector binds public
facts and operations; Host Runtime owns process isolation, preparation and recovery.
Native UI and headless presentation of the same official game are configurations
of one Host, not two independent implementations proving portability.

| Contract part | Required distinction |
| --- | --- |
| Core obligations | Actual world/implementation/instance/generation identity; versioned capabilities, scope and limits; request/result, known failure, unknown and cancellation meanings; exact observation/object binding and revalidation for each advertised interaction. Unsupported capabilities remain explicit. |
| Optional extensions | Structured catalogs/Resolve, focus/preview, ordered events/history, synchronous step, save/restore/fork/reset, scenario preparation and resource controls are advertised only where implemented. Management and privileged research capabilities have separate permission/information scopes. |
| Requirement matching | Agent declares interaction/Input/history/state needs; Connector declares the Host capabilities and mapping needed to realize that contract, including omissions, failures and cost. Task/runner separately declares management needs. The actual request binds a supported tuple; missing requirements reject before side effects rather than silently changing Agent semantics. |

Conformance has separate levels: structure/message checks, owning mapping positive
and negative cases, selected mechanism/scenario behavior, and exact-build differential
or runtime evidence when native compatibility is claimed. Equal JSON, seed, SDK or
one trajectory does not prove whole-game rules, RNG, timing or performance equivalence.
Delivery, execution, effect, Commit and causal successor remain distinct in an
asynchronous native Host; a synchronous step extension cannot silently collapse them.

Current capabilities/profile contracts, native seams, Managed services and Host
scenario tools implement concrete parts of this mapping. The replaceable minimal
core, extension and requirement-admission map is still distributed, not a completed
universal Host API. The native execution kernel also still binds concrete REST
environment and NDJSON port implementations. Narrow dependency interfaces or shared
SDK release verification are possible owning improvements; this document does not
claim they or a new direct driver have been delivered.

## Accepted native logical interaction direction

This section records the retained ADR-0015/native-logical/text-menu profile
direction. Its entered-page and inspect/defer/re-enter grain applies to those
profiles. The current P1 direction uses [ADR-0017](adr/0017-p1-forward-decision-profile.md)
and [P1](protocols/P1.zh-CN.md) for lawful information aggregation, forward macros
and irreversible group decline; the older grain below does not constrain P1.

[ADR-0015](adr/0015-native-logical-interaction.md) accepts WEB-01's upper-level
direction and conditional examples. It does not itself define a wire contract
or qualify an installed artifact, a complete game, or historical Human proof.
The explicit [text-menu-v2 profile](../components/connector/docs/TEXT_MENU_PROFILE.md) now implements a
bounded interaction path; its source, Host, recording and model qualification
remain separate. This does not imply that every ADR example is implemented.

The target policy input is the current entered native logical page/mode's
authorized content and selection state, its complete applicable action menu,
and model-owned memory of actual history. A page's complete logical list is not
split by scrolling, pixels or virtualized holders; other unopened pages and
hidden outcomes are not automatically aggregated into that input. One semantic
native interaction is an action, not every mouse event or software read.

The ADR's primary contrast is a fully informed single selector with sequential
picks versus multiple reward groups with inspect/defer/re-enter opportunities.
Cancelling a tentative selection, temporarily leaving a reward group, claiming,
and final abandonment are distinct. Their exact native realization remains for
local validation and scoped implementation; the examples are not runtime proof.

Connector still owns observation/delivery, Annotator immutable Human evidence,
STPD research and learned memory, and Policy Runtime bounded execution and
recovery. A future shared capability profile must not become consumer-side
catalog filtering. Receipt observations and UI acceptance do not become causal
successors by renaming. Existing H, execution S, A_public, canonical evidence,
Read-rich inputs and M0 results retain their exact meanings until explicit
versioned migration. Detailed refinements and justified engineering tradeoffs
follow the ADR's review boundary, not an implicit rewrite of history.

## Planes

```text
Native:       STS2 -> Native Foundation -> Connector / Annotator
Environment:  game-side Host <-> Connector <-> consumer
Host control: Host Runtime <-> STS2 process lifecycle
Evidence:     Annotator -> immutable bundle -> verify/store/transfer/receive -> consumer
Policy:       current supervised Agent -> Policy Runtime -> Connector
Operations:   typed application services -> CLI / Workbench / one in-game Live UI
```

The planes may share exact environment identity, but not mutation authority or
transport semantics. The Platform is not a strategy, reward, training, or
research service. This diagram names current component paths; it does not turn
their deployment layout into a permanent upper-level interaction standard.

## Recording Application Plane

The native recorder correlation kernel owns one process-local recording state.
It starts `Ready`, and a typed `RecordingService` is the only application
boundary used to query status, issue lifecycle commands, or follow ordered
events. `StartNewSession` creates a fresh session, timeline and append store;
`Pause` blocks new witnesses without discarding already accepted native
lifecycle witnesses. `Close` stops accepting new roots, terminates bounded
native completion bookkeeping, records any still-unresolved final semantic
root as an explicit terminal unknown, flushes the store, and permits another
isolated session in the same STS2 process. Close never waits solely to invent a
successor and never promotes a final observation into `S'` without a trusted
causal boundary.

`RecordingStatus` is a current projection. Its scope section derives from the
active CaptureProfile and store counters, and separately reports recorded,
native-accepted-but-failed-closed, supported-but-not-observed and
declared-out-of-scope outcomes. A native UI attempt that STS2 rejects creates no
HumanDecision and no capture-failure invalidation. The bounded event stream
supports status-first reconnect followed by events after sequence N; a
retention gap requires a new status query. These operational events are not
durable Human Evidence. RunJournal, current decisions, invalidations, Reads,
semantic evidence and bundles remain the current durable Evidence Plane;
historical native-action-ledger streams are retained only for explicit archival
inspection. Audit, pack, verify, store and transfer never run on the game main
thread.

## Canonical Sequential Evidence Boundary

The Recorder's durable trace and a research transition are different products.
The canonical one-step contract is:

```text
S_t + A(S_t) -> A_t -> S_(t+1)
```

`S_t` is the fair-player semantic state consumed by `A_t`; `A(S_t)` is the
complete same-state semantic action space produced from STS2-owned rules by a
typed Native Foundation provider; and `S_(t+1)` is the next state after the
action and its causally owned automatic continuation. Connector remains the
only public delivery authority and may project an empty `A_public` while input
is settling. `A_public` and `A(S_t)` therefore share native facts but are not
interchangeable evidence. Human observation `H`, native acceptance, lifecycle
completion, and an interactive poll are independently valuable evidence, but
none may be silently relabelled as `S_t` or `S_(t+1)`.

`SemanticBoundaryTracker` is the sole current runtime Human causal authority.
It separately accounts Human Root, exact execution/Commit facts and a later
Successor Boundary. An exact next-Human pre-execution boundary may close the
immediately preceding root; if overlap makes attribution unsafe, gameplay still
continues and the affected canonical transition is omitted rather than guessed.
Typed native owner-ready and legitimate paused PlayerChoice boundaries may also
prove a successor. `GameAction.Finished`, Task completion, UI post-Commit,
periodic status frames, timers, queue-idle and generic later interactivity do
not imply `S'`.

Current Decision records and `canonical-transition` records are non-authorizing
projections from an already `TransitionProved` semantic draft. The current
Decision record retains the frozen Human-time public frame for durable
compatibility and may be omitted when its older interactive-shell constraints
do not fit an otherwise valid modern transition. Current canonical evidence
independently joins the semantic
state, a typed native decision/action catalog captured at the exact action-
binding boundary, exact Human/native correlation, Commit and the tracker-proved
successor. A source-local callback may bind before native admission while a
`GameAction` binds at `BeforeActionExecuted`; both retain their phase and exact
identity. The projection cannot settle a root, authorize legality or a
successor, backfill an unknown or introduce a second causal state machine.
Exact root environment identity retained until projection is metadata only.
Historical Decision V1/V2 and native-action-ledger schemas and validators remain
readable only for prior/additive evidence; the current runtime no longer uses a
mutable native ledger or serialized-evidence admission policy to adjudicate
causality. The `*-2` wire names identify the single current recording format,
not a parallel V2 product.

The current schema-4 timeline is an accounting/lifecycle trace. Exact-source and
exact-runtime analysis found that normal STS2 UI staging withdraws many accepted
affordances before execution, while generic later interactivity does not prove
causal settlement. Schema 4 preserves a content-addressed read-only projection
of the already-authoritative execution semantic state/action fact. ADR 0003
records the withdrawn serialized-input candidate; it is not current gameplay
authority. Native Foundation owns the bounded shared combat semantic catalog
and exact lifecycle adapter. Connector remains the only public state/action and
delivery authority; Annotator records the shared native observation and does
not reconstruct legality or claim a universal successor.

ADR 0005 records the Root / Commit / Successor split and the fail-closed
successor rules. Native lifecycle/task completion never implies `S'`; the
native semantic discriminator is diagnostic-only.

## Hard Shell

STS2 owns rules, RNG, native legality, effects and Commit. Connector publishes
only complete finite Host-bound actions and revalidates exact native operands at
delivery time. Reads are state-bound and non-authorizing. Host control is not a
Player Environment action. Annotator observes accepted native-human actions and
cannot execute them. Unknown delivery is never automatically retried.

The legacy scoring Policy branch receives the complete ordered Connector catalog
and only Manifest-required advertised Reads. It cannot filter or invent candidates.
Its adapter returns scores plus an index, and Runtime resolves that index locally
against the same Snapshot before acquiring the one Connector controller. Shadow
never acquires a controller; One-Step returns to Human; Auto hands off on an
unsupported surface, abstention, or not-delivered Receipt. A Receipt successor
is an immediate post-delivery observation, not causal settlement; stable next
decision evidence remains separate. A transport exception after submission or
an `unknown` Receipt taints the run and is never retried. Adapter failure or a
bounded decision timeout returns to Human before controller acquisition.

The explicit native logical Agent branch uses the same controller/budget/Stop
owner and the versioned [Agent session port](../components/policy-runtime/docs/AGENT_SESSION_PROTOCOL.md).
It supports observation consumption, queries and Act/Await/Abstain/Close. Scores
are optional diagnostics; a submitted handle or expression must resolve to the
original complete Connector relation. This port is distinct from the public
environment interface, and does not grant inference, legality or native operands
to Runtime. Original delivery, execution, effect and cancel knowledge remain
separate; no branch automatically retries an unknown delivery.

## Component DAG

```text
STS2 installation and native runtime
  -> Native Foundation
     -> STS2-owned semantic decisions, lifecycle, and owner lineage
  -> Connector component
     -> Player Environment contract, native UI implementation, SDK, and release identity
  -> Host Runtime component
     -> lifecycle, exact-build admission, probes
     -> public Connector SDK + pinned Connector Host release
  -> Annotator component
     -> Host workstation seam + exact Connector witness artifact
     -> native-human witness recording and session evidence
  -> Evidence component
     -> current and explicit archival verification, content identity, immutable local logistics
  -> Policy Runtime component
     -> model-neutral policy process and Connector consumer lifecycle
     -> append-only Agent-run evidence through Platform Evidence semantics
  -> Workbench application
     -> typed live status, explicit partial fallback, bounded Runtime commands
  -> Platform Live UI
     -> one in-game shell over typed Connector/Runtime/Annotator services
  -> Platform Game Mod
     -> one manifest/DLL packages Connector + Annotator + Live UI source
     -> exact build/install/load identity and rollback only

External consumers -> public Connector SDK / Host Runtime package
STPD              -> public packages + version-pinned Evidence + thin Policy Adapter
SpireAgent        -> consumer integration and policy
```

Native Foundation is semantic/lifecycle infrastructure, not a service,
transport, evidence store, or executor. Connector may filter its facts by
fair-player visibility and deliverability but cannot create semantic legality;
Annotator may observe them but cannot authorize input. See
[Native Foundation](NATIVE_FOUNDATION.md).

The Connector gameplay authority is one component-local path. Host Runtime
owns process lifecycle and may consume the public SDK; it must install the
Connector artifact named by the current Platform BOM/release authority, not an
unrelated branch or predecessor release. Annotator may use the explicitly
declared Platform composition seams for exact native witnessing, but it does
not create a second Connector build or action authority. STPD consumes public
packages and verified evidence artifacts, never Platform implementation
internals. STS2 sees one `STS2_PLATFORM` Mod, while Connector, Annotator and UI
retain separate source provenance and authority inside that assembly. Evidence
validates artifact integrity and transport; STPD alone owns
research admission, splits, labels, B0 and training authorization. STPD owns
checkpoint/Qwen/projection/scoring support but no controller or Connector
lifecycle. Workbench and Live UI own presentation and bounded application
commands, never domain authority or direct gameplay submission.

Portable boundary tests require the Host installer to consume a versioned
Platform Connector release and require Annotator to use only the declared Host
workstation seam plus the exact component-local Connector witness artifact.

## Identity

`workspace_revision` identifies an atomic Platform checkout.
`source_revision` is the latest Git commit that changed a component path;
`component_tree_revision` identifies that path's exact Git tree, and
`component_source_digest_sha256` covers its tracked and non-ignored source
bytes. These identities remain stable across unrelated component changes.
Public contract and artifact identities are separate. Artifact SHA/MVID and
exact loaded runtime remain the final byte/runtime authorities.

## Project applications and research in the same checkout

The one user console is `python/spireagent/console`, served locally by Workbench and
remotely by Hub. Only Hub owns project membership and device authorization; local
credentials authorize one device and are never cloud administrator credentials.
The retained `apps/workbench` endpoint is a diagnostic API, not a second user UI.

Shared JSON, package/source identity and artifact storage live below applications in
`spireagent`. They import neither application UI nor STPD research. Research never
imports Hub or Workbench. Hub composes explicit research jobs, permission services and
a provider-neutral compute interface; the Modal adapter retains provider call handles.
Workbench supervises the policy process; S1 requirements and launch arguments belong
to its model adapter in that existing profile. This is the current deployment,
not a requirement that every Agent be a scoring Model or a separate process.
Unknown delivery never becomes an automatic retry.

Human, program Agents and external sources participate in one declared data and
composition chain:

```text
source/world/actor/information -> immutable records and verified original facts
  -> use/split + declared Projection/InputSpec/TargetSpec -> training Run/attempt
  -> checkpoint/Model -> export/Agent composition -> current execution/result
```

Record origin, actor, information regime, target method/quality and use/exposure
are separate facts. A projection declares reexpression, omissions, history/reset
and target assumptions; it does not change originals or create missing Human,
Commit or successor proof. Runtime originals can supply machine samples through
the existing direct-source contract; Source3 capture is an optional, different
witness rather than a mandatory second recording of every Agent run. Supported
sources and recipes remain bounded by their owning validators; this chain does
not admit arbitrary data by declaration alone. Offline privileged teachers may
provide explicitly qualified research targets, but their private/search/future
facts must not enter fair student inputs or ordinary online Agent capabilities.
Model training ancestry, current input/Agent compatibility and task/strategy
evaluation are independently verified. Reuse existing store, curation, training,
export and execution owners; neither new sources nor a new Model require another
upper-level framework.

One `python/uv.lock` governs the Python environment. Hub installs cloud/data dependencies;
worker images additionally install training dependencies. These are explicit build
profiles of the same source, not two product backends. Image digest and profile remain
part of deployment identity. The CPU Hub cannot be submitted as a GPU worker.

## Unified workbench delivery direction

The accepted UI target is one local/cloud/in-game workbench with the Mod as the
default entry for all functions, including login. Independent local/cloud services
retain their responsibilities and run without the game where appropriate. External
windows are alternate presentations of the same task/data/account system, not a
second product. See [UI specification](UI_INTERACTION_SPEC.md#accepted-direction-complete-in-game-workbench-2026-09-18)
for the target and phased implementation; the currently installed Mod is not yet
the complete workbench. No gameplay, credential or research authority moves into UI.
