# 统一抽象与持续演进：历史要求、当前差距和收敛路径

日期：2026-10-11（Australia/Brisbane）。当前代码核对基线为
6d809fbf656c71e7475cf3f4874bb034ae9d02d0；上轮架构审计的 b7274c87 结论保留原范围。
本报告解释项目的共同逻辑与变更边界，不把设计方向、source/test、实机结果或策略能力混成一个完成状态。
活动运行和交付状态由 [CURRENT](../memory/CURRENT.md)、
[当前执行包](../plans/AGENT_RUN_COMPLETION_2026-10-11.md)单一维护。

后续进展注：下文保留 `6d809fbf` 时的判断。P1 的共同组合、请求和执行接线现已实现，
并在 `6dc0f722` 的 Auto5 中验证了原请求、实际运行和结果的关联，见
[实现合同](../../python/docs/AGENT_EXECUTION_CONTRACT.md)及
[Auto5 与当前修复包](../plans/AGENT_RUN_COMPLETION_2026-10-11.md#auto5-closure-and-current-repair-packet)。
Auto5 和该候选的 CI 均出现失败，后续修复尚未接受；这不构成整局能力或所有组合的可运行证明。
既定计划中的分层、多模块、检索、搜索和视觉方案仍可按本文的逻辑角色表达，
但需要各自的组件、环境能力或资源实现与验证；未来组合不因此成为当前支持范围。

## 1. 核心判断

**可以并且应当使用同一套对象关系、组合规则、生命周期保证和变更判据，表达新旧方案。**
目前统一权威、应用服务和产物基础已经存在，但统一的可执行 Agent 组合与运行请求尚未落地。
这是上轮“已有共同服务”判断没有充分回答的部分。

用户持续要求的是：更换模型、协议、Host、记忆、训练方法时，已有成果能继续积累，
改动有明确范围，而不每次重新接一条产品链。这个理解既来自历史核对，也已被最新用户指令明确确认。
“有没有同一个界面”“能否调用同一个启动函数”只能回答其中一小部分。

稳定的共同逻辑也允许被反例修订。它的价值在于让变化可解释、可验证和成本可控；
既不保证当前所有组件永久保留，也不要求现在实现所有未来组合。

## 2. 历史核对说明

本轮流式检索了同一线程 10月1日至11日的233条用户消息，排除自动环境包装，
区分用户原话与用户粘贴的旧助手结论。独立历史审查提取15条短原话，
负责人逐条对照原始消息验证文字、消息身份、时间和位置；没有把旧助手进度当作当前证据。
私有审计保留完整检索定位，不将整份对话或原始游戏记录提交到仓库。

其中几条反复出现的要求足以说明方向：

- “注意上层定义必须是抽象的”
- “区分agent也就是直接对接协议的封装对象和agent内部的处理具体任务的模型。”
- “模型做打开读取返回还是一次操作是一个部分，底层是读取还是原生是另外一个部分。”
- “不强制拆成三套登记系统”
- “丢失什么东西或者有多少失真我们清楚”
- “不能把其中一种实现方式直接写成整个系统唯一正确的答案，可以把一种实现方式当成唯一合法的运行方式”

据此保留四项纠正：

1. 单一生产模式可以严格固定；它不成为顶层唯一可表达的模式。
2. 能力由完整 Agent 与环境共同提供，不必全部塞进一个模型，也不能由外部执行器悄悄补上。
3. 训练派生可以有损、截断、采用假设或只监督一部分；必须说明方法，不能改写原始事实。
4. 牌堆读取、奖励循环、巨大目录等例子是设计探针，不是固定且穷尽的协议菜单。

## 3. 一套整体逻辑应当怎样表达

项目组织目标、权限、资产和实验。一次运行固定任务、完整 Agent、环境及资源，
由已有执行 owner 完成，并留下可核对的结果。数据处理和训练也属于项目执行，
但使用各自的 recipe、InputSpec、TargetSpec 和初始化合同，不必伪装成玩游戏的 Agent。

~~~mermaid
flowchart TD
    P[项目：权限、资产、实验] --> T[任务定义：目标与验收]
    T --> R[一次执行请求]
    A[完整 Agent 组合] --> R
    E[环境合同与具体 Host] --> R
    B[资源、预算与授权上下文] --> R
    R --> O[现有执行 owner 与选定 driver]
    O --> F[结果、原始记录、成本和失败]
    F --> D[有声明的数据视图]
    D --> L[训练或评价任务与 recipe]
    L --> M[模型产物]
    M --> A
~~~

图是目标对象关系；不是声明已有一个通用调度器，也不要求每个框另建服务或数据库。

| 对象 | 回答的问题 | 应保持的边界 |
| --- | --- | --- |
| Task／Goal | 做到什么、如何判定结束与验收 | 与选择策略、端口结束消息分开；自然失败可完成一次游戏任务，但不是策略成功 |
| AgentComposition | 谁取得信息、维护状态、选择并行动，使用哪些依赖 | 程序、输入／历史、状态／reset、选择／时机和零到多个模型都可声明 |
| Model | 执行什么计算，使用哪组参数与状态转换 | 不因被重新组合就改写原权重、训练或产物来源 |
| Environment contract／profile | 能看什么、何时取得、能操作什么，失败和取消意味着什么 | Connector／原生 owner 保持权威；不同动作粒度与曝光不自动等价 |
| Implementation binding | 由哪些代码、adapter、driver、包实现 | 不把同接口、同 shape 或同名字当作等价实现证明 |
| Run request／instance | 本次选哪个组合、环境、起点、资源、预算和恢复来源 | 与组合定义分开；复用已有 operation、attempt、Runtime run、Host episode，并建立关联 |
| Data view／Training recipe | 原件如何成为输入和监督，怎样训练或评价 | 复用 store/use/ledger，保留假设、失真、原 split、初始化与新 Run 身份 |
| Result | 操作怎么结束、任务是否完成、看到什么胜负、策略是否有效 | 多轴解释；unknown 不因统一界面变成成功或失败 |

共同描述必须在启动前生效，并用于选择、校验、启动和结果关联。若只是事后增加一个展示 DTO，
下层继续各自决定 Task、预算和终态，最高层仍未收敛。

## 4. 用同一结构看旧与新

| 组合 | 环境／输入 | 内部行为与状态 | 模型 | 当前程序端口 |
| --- | --- | --- | --- | --- |
| Public M0 | Snapshot1、原公开表示、完整候选目录 | 评分与 chooser；可明确选择奖励选卡程序；无 learned carry | 原 M0 | decision-only NDJSON1 |
| Public M2 | Snapshot1、原 segment／ordinal 历史 | 新观察更新 W、重复缓存、原 reset | 原 Public M2 | decision-only NDJSON4 |
| 旧 text M2 | 所声明 text profile 和历史 | 原文本／记忆／交互确认组合 | 对应旧模型 | 原 decision-only driver |
| native M2 | native-logical、声明的 sampled 或 publication 历史 | Consume／ACK、查询、选择、Await／Close、carry | native structured Model | Agent-session NDJSON1 |
| Teacher | native-logical、明确的程序 InputSpec | 固定取得／浏览／选择程序和脚本状态 | 无模型 | 同一 Agent-session |

这些是共同逻辑中的不同组成。它们不意味着所有组合能互换，也不意味着每个组合都已接到普通产品入口。
换成不同协议可以是实现替换，也可能改变环境语义；必须先区分变化究竟发生在哪个边界。

尤其有两个常被混淆的边界：

- **环境交互合同：**定义公开信息、行动机会、动作与失败含义。
- **Runtime—Agent 程序端口：**定义怎样交换输入、消费进度、分数或指令。

把旧评分器包装进新消息端口，不自动要求改变它面对的 Snapshot／候选／记忆语义。
把旧权重放进新采样、分步操作或新记忆程序，则形成一个新 Agent 组合，应重新验证，不能叫原行为的忠实恢复。

## 5. 遇到差异，具体改哪里

| 情况 | 首先修改的位置 | 验证重点 |
| --- | --- | --- |
| 只改 HTTP／stdio、编码、分块 | transport／端口 adapter | 信息、顺序、重复、错误和取消含义不变 |
| 相同合法输入转文本／tensor | 表示与 InputSpec adapter | 线上／离线一致；不额外浏览、不隐藏裁剪 |
| 换权重或模型内部计算 | Model 及必要加载组件 | 输入／输出／状态约束、资源、产物和数值行为 |
| 改浏览、等待、chooser、规划或多模型协作 | Agent program／composition | 完整行为明确；改善不能归功于未改变的模型权重 |
| 改信息范围、动作粒度、中间选择／取消机会 | 环境 profile／协议合同及实现 | 公平性、合法关系、实际时序、记录与消费者迁移 |
| 原合同已有能力，但实现违背它 | 原 owning component | 最早错误事实与忠实回归；例如 End Turn 原生顺序、无输入过渡状态 |
| Model 为必填，无法表达合法无模型 Agent | 上层组合／启动抽象 | 同一入口能表达、校验并运行两个结构不同的真实实例 |
| 同样的 Stop／恢复保证反复在两个分支各修一次 | 共同生命周期职责与必要端口 | 先确认语义相同，再提取共享 owner；不硬合并观察／ACK 算法 |
| 旧数据缺中间步骤但有可用目标 | 研究 ProjectionSpec／InputSpec／TargetSpec | 说明能监督什么、缺什么、如何假设；保留原件和用途 |
| 新来源模型下载后无法走现有导出 | 现有 artifact resolver／安装集成 | 原产物、许可与必要父对象；无需改游戏协议 |

修改上层抽象的充分理由包括：合法用例无法诚实表达；同一责任不断跨层复制；
核心服务长期充斥同类特判；为一个合理变体付出的迁移成本显著超过收益。
“新的”本身不是修改理由，“旧的仍能跑”也不是永久保留理由。

旧外壳可以被适配或重构；保留的是有用计算、数据和证据的真实含义，不是所有历史目录和接口。
若目标新能力无法在旧合同中表达，应修合同或设计明确的新组合，而不让 adapter 秘密增加能力。

## 6. 当前五个实质差距

1. **启动入口仍偏 Model。** 普通 Application 注册接收 model_id／environment_kind，
   Task 和 program 默认值存在更下层；无模型 Teacher 不在普通可信选择目录。
   [注册入口](../../python/spireagent/workbench/developer_server.py)、
   [可信目录](../../python/spireagent/workbench/trusted_agents.py)。
2. **共同结果先于共同执行输入。** AgentTaskOutcome 已真实实现，不能继续称为完全缺失；
   但它主要从各来源原件解释组合，尚不是启动前共同合同。
   [结果投影](../../python/spireagent/workbench/agent_task_outcome.py)。
3. **一些 TaskSpec 混合目标与做法。** 标准总结页目标与 greedy／map timing、
   Port4 最终消费、Port1 零分弃权等实现约定绑定在一起。历史合同保持原义；
   新共同 goal 应通过明确 witness／program binding 对应它们。
   [native Task](../../python/stpd/policy/native_task.py)。
4. **变点散落在公共服务。** launch 参数、profile、支持模块和导出来源仍有多处 family 分支；
   部分差异合理，但新增组合不应同时复制用户流程、启动、Stop、存储和结果处理。
   [运行服务](../../python/spireagent/workbench/local_models.py)、
   [导出来源](../../python/spireagent/workbench/local_model_export.py)。
5. **部署组合、训练和资源的耦合仍需逐步拆清。** 当前训练／checkpoint 和导出包绑定具体 execution_policy，
   能保证原 provenance，但不应据此推断权重永远只能用于那个 Agent 程序。通用资源能力也尚未完整。
   [训练请求](../../python/spireagent/workbench/recipe_contracts.py)、
   [compute seam](../../python/spireagent/compute.py)。

Public M0 的消费代码摘要仍过宽，无关应用变化会迫使重新登记；Public M2 已有更明确的闭包。
这也是本轮实际遇到的变更传播成本。改进时版本化闭包、保持旧身份，不为省事取消校验。

## 7. 在什么条件和环境下收敛

**概念与变更规则现在收敛；实现按真实接缝逐段收敛；广泛资格按证据扩大。**

第一个共同启动合同至少需要 Public M2 的有状态 decision-only 和无模型 Teacher 的 Agent-session 来检验，
再映射 M0 和 native learned Agent。只比较两个评分模型容易做成统一模型加载器。
可以先用现有真实 manifest、已验证原件和进程 seam 验证，不要求为了接口设计先训练新大模型。

实机先在当前已核准游戏版本、隔离的 Defect A0 环境中验证。一个默认可用组合应以连续推进、
合理选择和成本来决定；完整角色／难度范围逐项保留，不能用这个起点替代它们。
未来 simulator 或其他 Host 的等价是相对于声明环境合同和机制的等价，不由同 JSON／seed 推出。

收敛完成的可检验标准：

- 相同目标和条件下，可经同一组合／请求流程选择不同合法 Agent，并得到一致解释的结果。
- 新增一个 Agent 主要增加自己的可信实现与组合条目，不复制登记、加载、停止、数据和报告服务。
- 更改合法的 chooser／运行程序可以引用原 Model；是否需要新数据、重训或新环境合同有明确理由。
- 无关编辑不持续使无关消费者身份、已安装包和历史模型全部漂移。
- 共同 Stop、预算、unknown、恢复保证在所选 driver 上实际成立。
- 替换后旧适配层有明确继续支持／迁移／退役条件；保留历史数据 reader 和原件。

不能用一个任意 JSON／callback 总线实现表面统一；它只会把类型、职责和验证推迟到运行时。
也不需要把所有执行压成同步 step：实时 STS2 的投递、执行和效果不是同一个事件。

## 8. 外部实践提供的校准

- [Ports and Adapters 原文](https://alistair.cockburn.us/hexagonal-architecture)按交互目的组织端口，
  让技术 adapter 可替换，并用隔离测试检查应用逻辑是否泄漏。本项目对应“取得环境输入”
  “运行一个已声明 Agent”等目的，而不是按 M0／M2 年代各建产品。
- [Branch by Abstraction](https://martinfowler.com/bliki/BranchByAbstraction.html)允许新旧实现通过共同边界逐步并存、
  比较与迁移，旧实现不再需要时删除。它支持渐进收敛，不支持永久堆积兼容分支。
- [Kubernetes CRI](https://kubernetes.io/docs/concepts/containers/cri/)以有限插件合同连接不同容器运行实现。
  [RuntimeClass](https://kubernetes.io/docs/concepts/containers/runtime-class/)进一步表达配置差异。
  对本项目的启发是把共同用例和可替换实现分开，且能力兼容须明确；并非引入 Kubernetes。
- [RLlib 核心对象](https://docs.ray.io/en/latest/rllib/key-concepts.html)区分实验组织、环境运行、模型和学习器；
  [env-to-module pipeline](https://docs.ray.io/en/latest/rllib/env-to-module-connector.html)单独承担输入变换。
  可借鉴职责分解，不照搬同步环境假设、奖励语义或集群。
- [Gymnasium Env](https://gymnasium.farama.org/api/env/)将终止与外部截断分开，是“旧合同表达不足时应修改合同”
  的具体例子。本项目据此检查结果含义，但不把异步 STS2 包装成未经证明的因果 step。

这些资料只提供比较方法。是否采用设计仍由本项目的真实消费者、失败、维护成本和游戏能力决定。

## 9. 实施方向与当前推进

先在现有 workbench 内建立小型 AgentComposition 与 AgentRunRequest，
由可信支持实现解析原 manifest／package；旧入口翻译为同一请求，现有 LocalModelService 等 owner 继续执行。
描述直接参与准备和启动，结果引用相同选择；对不可拆的旧包保留明确的 legacy-bound 组合，
不伪造不存在的独立 Model 元数据。无模型 Agent 不填假 Model。

这一共同输入合同尚未实施完成；当前正在收敛具体实施边界和相应 canonical 决策。
它不要求先合并所有 wire、另建 Task 服务、复制 registry、统一全部资源调度或重训旧模型。
后续再收束可信 launch／metadata reader 的重复分支、缩小合理身份闭包、补跨机器 Public 模型入口，
训练继续复用同一 recipe／operation owner。

真实能力工作同时推进：旧 M0／M2 已恢复并实际运行；共同多轴结果已实现，
终止尾部 handoff reason 的小修正已合入6d809。Auto3 仍在14.155秒、15次提交后以完整空动作状态交还控制，
没有奖励进展或总结页；它促成了正在实施的 Connector 有界无输入分类修正。
该修正使用原生 owner 事实，保留各 profile 真实可用的查看操作，不增加 Runtime 超时或隐藏操作。

已有小训练与数据核对不能替代策略能力：奖励配对两组各40次 TRAIN 更新已完成；
Defect 现存1537 TRAIN／8 DEV 不足以构成有力的独立整局评价。
恢复、工程闭环、连续任务、学习收益、完整 G2/V1 分别验收。最新 PR／CI 和运行结果继续在活动包更新。

最终衡量收敛的是：下一次合法新组合能否少改公共代码、少重复接线，保留旧成果含义，
并更快产生可验证的真实游戏能力。若一个抽象持续增加这些成本，就应再次审查和简化。
