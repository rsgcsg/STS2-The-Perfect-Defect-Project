# ADR-0016: Agent composition and execution request

Status: Accepted (architecture and migration scope only)

Date: 2026-10-11

## Decision status and scope

独立架构审查与 Root 已采纳本决策的有限架构/迁移方向：同一对象模型、组合规则、
生命周期保证与替换判据，在现有应用内形成可执行 `AgentComposition` 与有类型的
`ExecutionRequest`；游戏分支称 `AgentRunRequest`。具体编码/执行路径仍需独立代码
审查与 owning 验证。训练是未来 Recipe workload 映射，不属于当前 P1 A+B 或首个结构门。
这里不建立 universal step、统一 wire、通用工作流引擎或额外平台。`Accepted` 仅表示
上述决策被采纳，不表示实现完成或授予额外启动、原生操作、use/训练、付费、发布及
G1/G2/V1 资格。

[基线第2节](../BASELINE_V1_SPEC.zh-CN.md#2-上层抽象与唯一责任)拥有共同术语/责任；
本 ADR 拥有已采纳的描述/请求方向及后续变更规则。具体环境、端口、Model/Input/Target
合同和原件仍由既有 owner 维护。正在推进的 owning 修复不等待一次全局重构。

## Problem and exact grounding

[最高层源码审计](../evidence/PROJECT_ARCHITECTURE_AUDIT_2026-10-11.md)说明已有共同
权威、应用入口和产物基础；不能把“共用启动服务”当作所有组合已有共同启动合同。
本决策针对的第一缺口是 **应用选择/预检/启动/结果没有统一采用同一完整组合描述**。

- [AgentTaskOutcome](../../python/spireagent/workbench/agent_task_outcome.py)已经
  实现共同结果解释；它读取已封存原件，不是共同 startup contract，不能继续把该结果
  接缝写成完全未实现，也不能由结果投影倒造启动时的 Goal/Input。
- [LocalModelService](../../python/spireagent/workbench/local_models.py)是真正的
  共同加载/控制 owner；[trusted agents](../../python/spireagent/workbench/trusted_agents.py)
  和注册路径仍按已支持 profile/产物解析。注册中的 Model、深层 Task/program 默认值与
  报告分支尚不能自然表达任意合法的零模型/多模型组合。
- [Teacher](../../python/stpd/policy/native_teacher_agent.py)已有无模型程序，
  [Agent-session](../../components/policy-runtime/docs/AGENT_SESSION_PROTOCOL.md)
  允许空 model bindings。这不等于普通产品入口已支持 Teacher；补一个假 Model 不解决
  这个结构反例。
- 历史 public/native TaskSpecs 固定了当时的 strategy、最终观察消费或端口结束表达。
  保留其身份是必要的；把这些混合绑定永久当成唯一 Goal 抽象则限制合法组合替换。

## Accepted architecture and migration decision

### 1. 定义、绑定、原件是三层，不强制三套登记

| 对象 | 一次定义的含义 | 不能混入／推断 |
| --- | --- | --- |
| AgentComposition | 完整程序、Input/取得、history/state、0..N Models、choosing/timing、Env requirements | 本次 runtime instance/seed/placement 不成为 Agent 语义；权重不等于完整 Agent |
| GoalSpec / Task acceptance | 要完成什么、允许起点/管理范围、终点/截断条件、所需见证 | 不规定 raw scores、M2 update 次数或浏览策略；不合并任务、操作终态、胜负与策略质量 |
| Environment contract | 公开信息、行动机会/粒度、事件/时钟、失败/取消与支持范围 | 不根据 JSON/seed 相同推断 native、managed、simulator 等价 |
| ExecutionRequest 与实际 binding | 本次意图、定义引用、环境/资源/预算/权限、实现选择及解析回执 | 不另持 controller/进程/worker 状态机；请求不是任意 shell/module 清单 |
| Artifact provenance | 原程序、模型参数/状态、code/lock、data/use、package 和证据身份 | 同义实现也保留不同原件；workspace SHA 不是通用兼容性判据 |

应用组合与训练定义位于 `python/spireagent` / `python/stpd` 的既有边界；Platform
保持 model-neutral，不导入 strategy、reward、training。STS2/Connector 继续拥有游戏
规则/公开完整关系/执行复验，Host 与 Runtime 保持各自 lifecycle，Evidence 与研究准入
不合并。逻辑角色不要求各自服务、DB 或登记流程。

### 2. AgentComposition 的同一有类型描述

已采纳的 `spireagent/agent-composition-v1` 描述方向由可信代码/已验证 manifest 解析；下表固定
首版必须回答的语义字段，具体编码由实现 owner 在两个结构门前审查冻结。它不是
当前新增的公开 wire，也不接受任意动态插件、反射路径或无类型配置 blob。

| 字段 | 必须表达的内容 |
| --- | --- |
| `identity` | 组合 id/version 与语义内容身份；现有 selection/artifact IDs 通过引用关联，不被合并 |
| `program_ref` | 可信完整 Agent 程序/策略定义，包括获取、浏览、规划等已声明行为；不是隐形 runner 补丁 |
| `input_contract` | 所需公开输入、InputSpec/表示与 acquisition policy；具体可用信息和成本 |
| `state_contract` | history 来源/范围、实际 consume/advance、重复处理、reset、允许 init/recovery；程序状态与 Model 状态可分别存在 |
| `models[]` | 零到多个 `{role, model_artifact_ref, computation_contract_ref}`；保留原参数/计算/状态身份，空列表合法 |
| `choosing_ref`, `timing_ref` | 全评分/生成/Resolve 等选择方式，以及何时观察、调用、等待、行动；仅广告实际实现能力 |
| `environment_requirements` | 所需版本化 profile、信息/行动/控制能力和已声明限制；不代替 Connector legality |
| `legacy_binding`（可选） | 不可拆旧包的原件/TaskSpec/port/default 引用；未知独立元数据不补造 |

这些字段可引用既有 Spec/manifest/library，不能为方便在 registry 和报告再维护一份
不同真相。helper 不必都叫 Model；已声明 Model 可以固定或学习，Teacher 可由程序和
脚本状态组成而 `models=[]`。多模型是可表达的角色列表，不要求首版实现所有网络拓扑。
可信实现明确支持的组合才能 ready；不支持的组合在启动前说明缺哪项能力，不忽略字段
或退回另一条 profile。

`legacy_bound_composition` 是同一描述内的受限兼容形状：可以整体引用不可拆原包，并
调用其现有可信 owner 校验/启动，不要求先发明全部独立字段或重训才继续原用途。
未能验证的子元数据在 catalog/result 中保持 unknown；ready/support 只来自原 verifier
实际能够证明的绑定，不能把 opaque 包猜成具有任意新 Goal/能力的组合。

语义定义与实现产物分开，但不能暗换行为。换参数会改变 Model artifact 和具体 Agent
model binding；新增固定 reward chooser 也是 Agent 变化。纯代码重构/等价 transport
可以保留组合语义身份，同时记录新的 implementation/package provenance。不同合法
初始前缀/seed 是本次条件；改变允许的 history/reset/init 规则则改变组合合同。

语义身份由声明的行为/计算/参数/状态合同决定，不能直接由容器/source SHA 代替；
具体绑定始终记录实际 Model/program artifact refs。只变 provenance 的重新封装，只有在
已验证支持范围内保留全部相关语义时才可沿用定义版本，新的原件/绑定仍分别记录。
相同 weights 或一次数值相同不足以作该判断，也不自动继承旧数据、运行或质量资格。
request/resource/provenance 元数据不因共用对象自动流入 Model 输入或 history；是否使用
须由明确 Input/Agent 合同声明，既有 I/F-off 等组合保持原输入限制。

### 3. 独立 Goal 与 legacy witness binding

GoalSpec 只固定目标/验收、所需起点与允许管理范围、失败/删失及原件要求；声明不
产生额外权限。游戏终点“ready 标准总结页”可接受胜或负，但不声称基本策略达标。
未声明 Goal 是显式允许的旧基线条件，不能自动变成 task completed。

每个实际 Goal 还要匹配 code-owned `witness_binding_ref`：它说明该 environment/driver
怎样用原件证明验收。Public M2 的最终正常消费、program-zero/nullable selection 与
native 的原 body/ACK/Close 分别留在组合/兼容端口绑定，不能硬写为整个 Goal 的本质。
历史 TaskSpecs 含 strategy/port witness 的字段、版本、包与训练绑定不改写；新同目标
关系是显式映射，不按名称相同猜测，也不向旧 M0 补不存在的 consumed-input witness。

一个 Task 可以已完成但随后操作/cleanup 失败；一个正常关闭进程也可以 Task 未知。
继续当前 AgentTaskOutcome 的独立 axes，旧 artifact 不覆盖。观测的输赢、任务验收和
策略评价由各自实际事实决定；training TargetSpec 是学习目标，不是游戏 Goal 的别名。

### 4. 同一 ExecutionRequest，领域 workload 保持有类型

已采纳的 `spireagent/execution-request-v1` 方向固定一次意图；`AgentRunRequest` 是其游戏
workload 的有类型视图，不再保存第二份冲突请求。共享的是引用/权限/资源/预算和结果
关联规则，领域 payload 与执行 owner 不合并。当前 P1 A+B 只推进 Agent 描述与游戏
请求；本表的训练列不要求当前修改 LocalTrainingService/recipe 或统一训练启动。

| 请求部分 | 游戏 `agent_run` | 训练 `training`（未来映射，非当前 P1/首门） |
| --- | --- | --- |
| `workload` | composition/selection ref、独立 Goal ref（可显式空）、witness binding、合法 state initialization ref | recipe、InputSpec/Input ref、TargetSpec、initialization/checkpoint ref、原 use/partition refs；可有明确导出默认组合 |
| `environment_request` | 实际支持的 Host/profile/起点选择；实例/generation/episode 由原 owner 准备 | 无需假游戏环境；只有真实 recipe 需要时引用其领域条件 |
| `resource_request`, `budget` | 已支持 placement/实现选择与领域有限额度 | 已支持 compute placement、wall/scratch/费用等原域单位；不把 scratch 当 RSS |
| `authority_refs` | 现有 member/device/intent/控制授权与资源范围，不保存 credentials | 原数据 use/Gold/训练/compute 权限，不能由公共请求扩大 |
| 实际解析回执 | 原 code/driver/package/contract/lock、Host/Runtime/operation IDs 和接受/缺失项 | 原 worker/provider/attempt/Run/checkpoint 及 input/target/init/package 身份 |

原 operation、attempt、Runtime run、Host episode 和 artifact ID 均保留。准备前固定
请求及真实默认值，实际实例或 provider ID 出现后在已有 owner 回执中关联；不把未发生
的 ID 写成 ready，也不反向修改 intent。预算记录 units/owner/cumulative scope，不能
靠新请求/恢复重置同一授权额度。资源标签与同源代码不证明行为/数值/恢复等价。

未来训练映射继续走现有 LocalTrainingService/recipe/compute owner，不调用虚构 Agent.step。
改变数据/Target/初始化产生其应有新 Run；从旧 checkpoint 初始化新任务与恢复同一
checkpoint 身份分开。旧 recipe 固定的部署策略保持 provenance；若实际 Input/数值/状态
兼容，可形成新 Agent composition/package 引用原 Model，而不是伪装重新训练或 resume。

### 5. 描述必须进入执行路径；共同生命周期是保证，不是新 controller

同一个已验证 descriptor/ref 必须进入 **catalog → readiness → prepare/start → results**。

1. catalog 说明实际 program/models/Goal 默认值和支持能力；无模型条目不伪造 weights。
2. readiness 校验组合、Goal/witness 与实际 program/driver/environment/权限/资源/state/
   artifact 的匹配；展示和执行必须用同描述。Goal 可描述不等于 Agent 已支持它。
   描述改了就重新验证，不能用旧 ready 结果启动另一组合。
3. 旧 API 翻译为共同请求，保留每个入口真实默认值并向调用者显示解析结果；新入口
   直接声明同请求。原注册、存储、controller 与领域进程 owner 继续执行，不复制流程。
4. prepare/start 固定解析出的实现与原件；领域 owner 按其既有控制/停止/预算规则运行。
   共用规则不要求同一 observation/ACK 或 universal causal step；不同 wire/capabilities 合法。
5. terminal/result 关联同一请求/组合/Goal 与实际 owner 原件。停止请求、cancel ACK、
   进程退出、Task 完成、可恢复 checkpoint 和 verified result 分别核验；unknown 不折算
   成 completed/failed，不自动重试。旧结果缺共同请求引用时仍按其原 reader 验证。

这不是一个只用于 UI 的平行 DTO，也不是新增 scheduler/lease/state ledger。结果层
已有共同投影必须复用；启动层的共同合同要用真实接缝证明，不能由类名或共享路由宣布完成。

## Substitution and future change rules

同义 adapter 必须保持合同内的信息及曝光顺序、行动/取消机会、模型消费/advance、
state/reset、重复、错误/Stop/unknown 含义。原合同要求 exact bytes 时仍须 exact bytes；
相同 JSON、shape、M2 名称、seed 或接口签名不够。以下逐项判定，不一次迁移所有组合。

| 变化 | 最小合法单位／验证边界 |
| --- | --- |
| 传输、序列化、分页/chunks、等价计算后端 | 仅在上述语义及原数值合同均保持时是 adapter；保留新实现 provenance 和原 byte/pin 要求 |
| 输入转换、曝光/history、参数/状态或 reset | 明示新的 Input/Model/Agent binding；如有损或假设化派生，研究 owner 记变换、缺口、用途与比较条件 |
| chooser、timing、browser/planner | Agent program/组合变化；原 Model 可复用，不暗标为“adapter”或学到的新能力 |
| 公开范围/动作粒度/中间机会、clock/failure/cancel | Environment owning profile/contract 变化；先生产者/消费者/公平性/记录兼容，再资格化 |
| Goal/验收终点 | 独立 Goal/witness binding 变化；旧 strategy、结果原件与科学结论不随名称迁移 |
| placement、resource/budget、code/package 绑定 | 本次请求/实现变化；验证实际能力、成本/恢复及不变的语义，不强制无关组件同步升版 |
| 上层 schema/组合抽象 | 只有合法需求确实无法表达、反复跨切核心分支/重复权威，或有证据的不成比例成本才触发；版本化并列出最小迁移 |

不预先规定全系统唯一正确实现。新用例先查现有组合能否表达、implementation 是否能
履行以及所需信息是否可达。实现错误修 owner；无法表达改对应合同；确实跨切抽象
才修本 ADR/基线。SDK/Runtime/Connector 可独立保持兼容版本；不要求同一 current SHA，
也不能为省升级删除精确安装/合同/原件校验。
旧 consumer 宽闭包或 whole-checkout provenance 仍按原 checker 执行；若要缩小身份范围，
须单独审查/版本化其身份合同，不能用本 ADR 忽略现在的 mismatch。

每次新增/恢复只在原 task/PR 写一张小变更记录：实际能力收益与反例、改变的上述轴、
owner/复用路径、保留的原件及新身份/用途、最小受影响检查与较高证据、成本/退出/退休
条件。修抽象要说明版本/消费者/defaults/数据与包迁移，不重启所有历史讨论或另建台账。
用户例子用于检验这些规则，不是全部选项或要求先实现全套未来组合。

## Evidence and falsification

共同 startup 合同的首个结构门必须用两个实际不同的组合；可先用既有真实
manifest/producer fixtures 与原服务进程 seam，不为了设计先跑训练或游戏。

| 用例 | 需要证明／最小反例 |
| --- | --- |
| Public M2：有状态 decision-only | 相同 descriptor 经选择/预检/实际领域启动/Stop/结果；原 input/segment/ordinal/digest、一次新观察 update、重复缓存/reset 保持。改描述后复用旧 ready、隐加一次 consume、换 Goal 后借旧 witness 都须拒绝 |
| Teacher：model-less Agent-session | 同一 schema 的 `models=[]` 到真实 manifest/driver/owner；无假 Model/weights/download，Consume/ACK/Close 与脚本 state 明示；该入口若仍强制 model_id，不能宣称结构门完成 |
| 旧入口与独立结果 | 表示各自实际 Task/program/default；同一高层 Goal 经显式 legacy witness mapping，M0 缺见证仍 unknown，Task 完成与后续 cleanup failure 可共存 |
| 正常/负例公共接缝 | 未支持组合、wrong artifact/driver/history、重复/stale、Stop/cancel/unknown/recovery；用最低真实 producer-reader 层验证，不以 mock controller 宣称 native 顺序 |

训练 workload 的验证留给未来独立增量：准确传递原 recipe/Input/Target/use/init/Run，
保持 checkpoint/授权与学习身份，不要求 Agent Model/Goal。它不在上表的当前 P1 A+B
或首个结构门中。

随后映射 M0 固定 chooser 与 native learned 组合，沿实际 caller 验证；不把首门升级
为所有 Actor/GUI/Host/资源能力或真正合理整局。工程可组合、环境可执行、数据可用、
训练 fit、任务自然完成和策略质量分别验收。任何一步缺事实保持其原 unknown/失败。

## Compatibility, migration and retirement

- 在既有 workbench/trusted-support/registry 内先建立 code-owned 描述解析与共同请求，
  沿一个实际 caller 接入 catalog/readiness/start/results。描述不是新数据库或三份登记。
- 不可拆的旧导出保留原 `legacy_bound_composition`。只暴露已验证子引用；历史 TaskSpec
  混合字段、Manifest、操作 ID、失败/未知、data/use/split、weights 与证据不得重写。
- 将已确认 launch/config/metadata 差异留在少量可信 adapter。只有声明和实际行为等价
  才逐个换入口；不为了新抽象强行牺牲旧用途、必要信息、鲁棒性或可维护性。
- 先冻结首门所需的最小 v1 语义；枚举已有支持能力并 fail closed。更多 model count、
  acquisition、Goal 或 placement 待真实需要和 owning 证据，不预建 workflow/plugin 平台。
- 活跃旧入口只有在全部实际 callers 有可验证替代、默认值/负例/Stop/恢复接缝通过且
  支持范围和回滚清楚后才退休；移除 live adapter 不删除归档 manifest/evidence readers。
  归档 reader 的退出须另证旧原件可由保留的版本化工具读取，不改变原 bytes/资格。

## Alternatives, consequences and rollback

只重命名 Model→Agent 或加显示 DTO 不使启动合同成立；强制一个 wire/step 会损失
既有消费/信息/取消语义；各 Model 复制产品/控制服务会制造多份权威；通用 scheduler、
动态任意组合图和全仓一次替换增加无实际用例的成本。推荐以两个结构门逐段收敛。
收益应是新增合法 Agent 不再同时改用户路由、启动核心、默认值、resolver 与结果核心；
代价是显式 descriptor/兼容 reader 与迁移期双入口。若无法降低真实扩展/操作成本，
或上述结构反例仍须例外通道，复议边界而非给框架继续加隐形分支。

回退使用现有 selections/owner API 与原包；新请求/描述无法解析就明确 unsupported。
保留新旧请求/Run/原件和兼容 readers，不回写数据库、删旧权重或把新输入重标旧义。

## Primary comparisons and non-goals

- [Cockburn 原始 Ports and Adapters](https://alistair.cockburn.us/hexagonal-architecture)：
  借鉴以交互目的隔离外部实现；它不证明本项目 action/曝光/记忆同义，也不要求增加层数。
- [Fowler：Branch by Abstraction](https://martinfowler.com/bliki/BranchByAbstraction.html)：
  借鉴经共同边界逐个迁移 caller；不是永久分支、全仓重写或免除语义验证的许可。
- [RLlib 核心对象](https://docs.ray.io/en/latest/rllib/key-concepts.html)：借鉴环境、运行/采样、
  计算模块与学习职责区分；实时 STS2 不因该类比成为 Gym step，不采用其类型或依赖。

外部资料是有限比较，不是代码/原生/Host/科学资格证书。本 ADR 不实现 wire、启动 API、
新 Model/训练、付费资源、一般 scheduler、第二 controller/许可/use/store/因果权威，
不废弃全部旧端口，不改变当前安装，也不标记 G1/G2/V1 或基本策略通过。

## Implementation and later refinement

当前 P1 A+B 已授权实施、尚未验收；具体接口、原 API/默认值、启动/结果接缝及实施
约束由现有 [Local model task flow](../../python/docs/LOCAL_MODEL_TASK_FLOW.md) 拥有，
不在 ADR 再维护一份实现清单。实现 owner 仍须以实际 Public M2/Teacher caller 验证
可信描述/请求引用、legacy Goal/witness 映射与真实默认值，再冻结具体编码和兼容版本。
训练 workload 是未来另行 owning 映射，不是当前 P1 或首门的前置。剩余组合不是强制
清单，existing unknown、原 identity/byte/pin 校验及 archived readers 规则保持；采纳
方向不改变当前失败/资格，其他 owning 修复和更高资格工作保留其授权与证据。
