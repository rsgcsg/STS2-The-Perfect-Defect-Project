# P0–P5 完整交付：D-M2 参考闭环、协议评估与实施验收

日期：2026-10-06。版本：0.2。状态：**P5 有界设计评估交付候选，供 G1 审查；没有新协议实现、训练、实机资格或合并。** 用户本轮明确 D-M2＋时序 N 训练、后续可加入 Z/O 为优先贯通实例。本文不将这个具体实例反过来限制上层所有 Agent。

用户现倾向 L-N，[L-N 第一版具体规格](BASELINE_LN_V1_SPEC.zh-CN.md)进一步明确动作承诺、表示、目标预览、模型无关端口与loop责任；仍待G1范围接受。

新增[排队出牌与异步时序](BASELINE_LN_QUEUED_TIMING.zh-CN.md)：输入可与先前效果重叠，不能默认逐牌结算串行化；H与执行S、input-ready与causal后继分别验收。

新增[Human学习闭环审查](BASELINE_LN_LEARNING_INTEGRATION.zh-CN.md)：现有Annotator text输入白名单与L-N浏览范围有缺口，M2/结构化/生成/RL各有数据资格要求。它将E2被动Human采集和数据投影提升为与E1执行同等必要的首版依赖，未声称已补齐。

最新协议详案：[三套 Agent 契约、四个 Host 组合](BASELINE_PROTOCOL_OPTIONS.zh-CN.md)。它修正了本包早期 C-H 分类及优先推荐：C-H 是查询信息配置，不是独立协议；当前不选默认。

**最新交付入口：**[Agent—协议完整蓝图](BASELINE_AGENT_PROTOCOL_BLUEPRINT.zh-CN.md)：一个事件协议的推荐主线、可选边界接口、四种Agent设计与可执行合成接线、16个完整例子、数据/训练/评估/运行合同。独立审查在本轮材料与验证完成后进行；不代表G1通过。

**捕获与抽象依据：**[第一版综合方案](BASELINE_PROTOCOL_V1_SYNTHESIS.zh-CN.md)。它重新核对历史上层目标和H捕获保证，给出统一Core、LN-B1边界版/LN-E1模型自主时序、假设化数据转换和15个例子；旧“异步必须默认”表述已收窄，不因文档完成而自动接受某profile。

推荐阅读：本文→[逐场景设计册](BASELINE_SCENARIO_SPEC.zh-CN.md)→[P4 学习与工程合同](BASELINE_DATA_AGENT_CONTRACTS.zh-CN.md)。[基础设计](BASELINE_FOUNDATION.zh-CN.md)保留 P0/P2；[P3](BASELINE_INTERACTION_CANDIDATES.zh-CN.md)保留候选；[P1](../evidence/BASELINE_P1_AUDIT_2026-10-06.md)及[学习审计](../evidence/BASELINE_LEARNING_AUDIT_2026-10-06.md)提供历史；[本轮证据](../evidence/BASELINE_P5_EVIDENCE_2026-10-06.md)说明真正检查了什么。

## 1. 本轮完成了哪些工作，哪些没有

- 对确切 STS2 DLL 进行只读机制检查，结合当前 adapter 找出具体行为差异；没有运行游戏、提交原生操作或保存反编译源码。
- 将信息与动作细化成 21 张场景卡和 60 个正常/反例，逐项区分 N-G/N-U/P/A/M/R 来源，形成可实施评审材料。
- 执行一个 stdlib-only 合成设计探针：38 项参考检查、6 组场景×4 种组织的 JSON/交互成本；完整结果和源摘要可重现。
- 明确封装 Agent 及默认贯通例子 D-M2，连接环境、序列学习和本地/云执行三条链。
- 没有执行新的神经网络性能/学习实验、真实 Human 录制、全私有数据迁移、provider 调用、安装或跨 Host 实测。旧 CI 不升级为这些结论。

P5 的本轮范围是“以原生代码、来源和有界合成反例完成设计可行性审查与实施验收方案”，不是提前完成 E/V 的运行资格。成本中仍未测量的项和实施门槛在下文明确列出。G1 可以接受设计或提出修订，不能把本文件签成现有软件已达到新基线。

## 2. Agent 是封装个体，D-M2 是具体参考实现

环境只与一个 Agent 边界交互。Agent 内部拥有输入整理/公开信息取得策略、编码、持久状态、模型、候选计算和选择。GUI、runner、评估器不在外面偷偷补信息、修改 W 或替换最终动作。若增加一个外部 helper，它必须成为 AgentSpec 中明确的组件；否则比较的不是同一个 Agent。

允许的边界：initialize(公开任务条件/协议支持)、consume(已提供公开事件)、decide(当前问题和完整目录)、cancel/close；支持时还有受控 state export/import。内部可以有多个模型、缓存或 notebook，也可以没有记忆。封装是责任/契约边界，不是当前代码已经具有 OS 安全沙箱的证明；不可信扩展需要另行进程/权限隔离。

不允许的旁路包括：直接读取 Host 内存或原始存档、RNG/隐藏牌序、尚未揭示的原始记录、未来 TargetBundle/teacher、云账本、私有 seed、控制 lease；也不允许 GUI 根据 O 头分数替模型换动作。管理控制可以按已声明协议重置/停止/导入状态，但必须记录条件和新身份，不能静默干预。

### 默认贯通例子 D-M2/N

D-M2 表示 D 家族的持续当前表示，不是 D2 两/三遍结构。首个数值复用对象是现有 dsimple.light-action.m2.v1 的 LightActionM2Scorer：先将新公开输入及合格历史写入共享 W，再让每个候选只读同一 W，形成动作条件表示/分数。当前内核支持 K=1/8、宽 384；具体容量与 encoder 在实验 recipe 中固定，不能仅凭 D-M2 名称推导。

首个新 Agent 的流程：

    协议实际提供新信息
      → Agent 验证 event/context 与自身游标
      → 一次更新 W（不能读当前 label 或假设结果）
      → 完整候选只读 W 得到分数
      → 返回一个协议请求或 yield
      → 原生/Read 反馈实际提供后再进入下一次状态更新

合法无 N 标签的历史事件也必须能推进状态。当前 Public M2 port 拒绝 Reads、写入时无 prior action/feedback；不能原样拿它冒称这个新 Agent。保留数值内核和验证机制，更新事件/表示/adapter 时产生新 AgentSpec、数据视图和配方身份。

第一目标为合格时序 N；Z/O 是有独立标签资格的后续辅助，不是默认立即启用。候选 hypothetical 分支、未选动作的预测和 teacher 均不回写真实 W。TBPTT detach 保留值但截梯度，窗口不等于新局；参数更新后的 carry 近似需要明确登记。独立训练 Reset 和测试时清空 W 是两种实验。

## 3. 当前交互候选：两侧契约与完整映射

最新规范提供 AP-L 原生分步浏览、AP-Q 一次资料请求、AP-B 固定资料包三个 Agent 契约，组成 L-N、Q-N、Q-D、B-D 四个可审查配置。Q-N 和 Q-D 的 Agent 请求相同，底层分别走原生浏览与公开直接读取；只有内容、时机、效果、失败和成本满足上层要求时才兼容。

旧 C-H 是 AP-Q 中“每场景固定自动附带哪些资料”的一种 IP 配置，不再作为独立协议。旧合成比较让 C-H 获得更多默认资料，并假定 C-L 导航增加 policy 调用；不能据此证明优先。此前首发推荐在本轮撤回，等待相同信息/合理执行程序下的证据和 G1 选择。

[详细方案](BASELINE_PROTOCOL_OPTIONS.zh-CN.md)已经给出：每个组合的消息/时序/状态、遗物药水卡牌详情、缓存和公开关联、全游戏场景、原生浏览中断/恢复、直接读和一致资料包的前提、记录和 D-M2 时序 N、成本控制及验收反例。四案本轮共用 OP-S 原生选择阶段，避免信息比较中同时偷换动作粒度。

## 4. 实际原生发现如何改变设计

| 发现 | 设计后果 | 实施位置 |
| --- | --- | --- |
| 卡牌 Skip、reroll、牺牲替代有不同原生完成语义 | 不用按钮 label 分类；返外层、留当前、完成 reward 分开 | E1 reward binding；E2 trace/label |
| 宝箱可先出现正常/额外奖励，skip relic 可直接推进 | 不预聚合后续遗物，不把所有 skip 当暂返 | E1 owner/continuation |
| 移除服务在最终选牌返回后才扣费；预览取消和 whole close 不同 | 初版保留阶段，不默认压成一次 remove(card) | E1 shop/selector；E3 Agent history |
| 手选满额会替换，网格满额只拒绝加选 | 目录基于实际机制，不建立统一猜测选择器 | E1 selection capability |
| combat-pile 原生用有效候选数夹住 min/max，adapter 使用 raw min | 当前 source/native 差异需要忠实回归和修复；不能让模型弥补 | E1 第一 owning correction |
| simple-grid 的原生 min=0 确认与 adapter manual 条件有差异 | 以原生实际控件/完成机制为准，调查真实 opening caller | E1，未声称已实机复现 |
| 不同 screen exit 返回空、null 或 Task canceled | 明确结果词汇，不能造统一 cancel-success | E1/E2/E3 |
| Actor 只能 parent_observed 或 controller delivered | 不能升级为 child-await/Commit/causal successor | E1 Native/Recorder；E2 masks |

P5 没有修复这些 production 路径。已知实现缺口不否定目标设计，但在相应 E1/V1 资格通过前不得启用或宣传支持。

## 5. 效率比较：真正测量了什么

命令：node tools/baseline-design-probe.mjs。探针仅访问自己的源码、合成对象和 Node 内存，无游戏、网络、模型或数据存储访问。结果保存在 [JSON 回执](../evidence/BASELINE_DESIGN_PROBE_2026-10-06.json)，绑定 script SHA256、Node/runtime 和 fixtureVersion。

以下保留首次探针的历史原始数值，不作为当前四组合的实测或排序依据。C-H 是 query 信息配置，policy 调用次数是 fixture 假设；AP-L 可用 Agent 内固定程序减少神经调用。

它固定 6 种“需取得哪些公开资料后执行一次 gameplay”的合成工作流，比较 C-L、query、bundle、C-H。每种均取得该工作流指定的全部资料并执行同样数量的 gameplay 请求；bundle 还可能提供不需要的其他资料。C-L 的额外查阅在这些 fixture 中假定需要打开/返回两步；若真实页面本已提供所需信息，该开销应减少。它不是所有 native 页面天然多两步的结论。

| 固定工作流 | C-L 总字节 / 假定 policy 调用 | C-S-query | C-S-bundle | C-H |
| --- | --- | --- | --- | --- |
| 小战斗：需牌堆 | 3485 / 3 | 2873 / 2 | 4991 / 1 | 2861 / 2 |
| 大战斗：需牌堆＋地图 | 15877 / 5 | 14647 / 3 | 24976 / 1 | 14629 / 3 |
| selector：需战斗上下文 | 2581 / 3 | 1969 / 2 | 10376 / 1 | 1347 / 1 |
| 奖励：需牌组 | 6589 / 3 | 5977 / 2 | 11726 / 1 | 5355 / 1 |
| 商店：需牌组＋地图 | 11606 / 5 | 10376 / 3 | 16656 / 1 | 9748 / 2 |
| 无需额外资料 | 610 / 1 | 616 / 1 | 12166 / 1 | 610 / 1 |

数字是 JSON serialization bytes，包含此 fixture 的字段/标记差异；**不是 BPE token、网络包、原生延迟或模型推理耗时**。不带缓存/压缩，也不是实际游戏分布。call 数来自规定的工作流，不是观测到的学习策略。协议名称等 envelope 长度也影响小额差异，不把几字节差异解释成算法收益。

本机 Node v20.20.2、darwin arm64，5 次各 2400 个 reference projections 的中位约 98.74 ms（实际完整值见 JSON）。这只描述该机器本次 JSON reference assembler，不给 D-M2、实际 Host 或 cloud 的性能保证。实际 tokenizer/encoder、native UI guard、查询时序、传输、记录落盘、模型更新和并发负载仍需 E/P5 后续专项测量。

### 实现难度与精度比较

| 候选 | 可复用部分 | 主要新增复杂度 | 精度/风险 |
| --- | --- | --- | --- |
| C-L | 原生页、按钮绑定、text-menu 来源 | 完整逻辑列表、各种 native 页/await、输入时机、长轨迹 | 原生过程对应较直接，但仍不能猜父子因果；导航多/历史复杂 |
| C-S-query | 已有 Read、有限动作、request/Receipt | query-capable Agent port、记录曝光/可用顺序、stale 协调 | 信息少而按需，依赖 Agent 获取策略；不能混入无法在此时取得的 view |
| C-S-bundle | Read 抽取与原生动作 | 多 view 同时点捕获、表示体积、输入超限、Human 转换资格 | 可减少调用但易提前/过量提供；原件不含对应资料则不能转换 |
| C-H（query 配置，非独立协议） | 上述共用事实/绑定 | 每场景固定核心 view 集、其合法性与剩余查询合同 | 避免 selector 丢上下文与全量膨胀；复杂度在信息政策而非额外虚拟页栈 |

难度是本轮依赖/状态机分析的相对判断，不是量过的工时。精度分为公开事实、时点/对象、操作效果、成本/时序、记录转换和因果证据六项，不能用“贴近 UI”或一个总分代替。

## 6. 有界验证结果与反例覆盖

探针的 38 项分为 15 项 reference_transition、4 项 binding_guard、16 项 design_counterexample、3 项 cost_fixture。前两类检查 reference actor/request 行为，第三类是静态设计反例，第四类检查成本 fixture；验证强度不能按同一个 PASS 计数理解。它们涵盖：历史区别与重传、Agent 状态隔离和候选只读、已知在线禁入字段、stale/catalog/generation、有效原生控件与 raw prefs 的反例、取消结果分类、reward return/reroll、O 缺失与计数、独立终点分组、provider/产物/取消/未知区分，以及固定资料成本。

它们是合成 reference checks，其中一些是已知反例的断言；不是现有 production 已执行这些守卫的证明，不是安全隔离证明或真实 D-M2 已学会的策略。所有真实机制和长尾 Hook 必须再经 E1/E2/E3 验证。

曾尝试在已有本地 Python 环境运行 6 个选定的 M2 内核测试，预检因缺 Torch 即停止，没有运行这些测试，也没有安装新 ML 依赖。已有源码/历史 CI 是参考，本轮不将其当神经推理性能或新协议数值一致性实测。这个限制进入 E3 前置条件。

## 7. 三条链共同贯通 D-M2 的具体旅程

### 旅程 A：原生经历到 sequence-N

1. E1 提供选定协议与同时点公开事件。E2 捕获原生 Human 的真实输入/行为及允许的 fact sidecar，标明 actual/derived 信息。
2. 来源验证后形成 ProtocolTraceView；若新协议要求的信息/顺序没有证据，该片段不进入相应完整历史资格，不后补。
3. 数据 owner 固定 use/split/group；tokenizer 只拟合 train。合法无 N 事件仍参与 prefix；当前 choice label 在预测后才可用于后续实际历史。
4. D-M2 内核/adapter 在新 AgentSpec 下训练 sequence-N。窗口与 reset 区分，完整目录不剪裁，候选未来不写 W。
5. CPU/远端 worker 接受同一不可变计划，原 run 条件不变时 checkpoint 可按合同恢复。结果经过验证/选择后才成为模型产物。

这里的 Agent 自己生成轨迹不自动成为优秀 N 示范。它可用于重放、失败分析和合格 Z/O；是否参与 N 及其权重是独立研究决定。

### 旅程 B：产物到真实运行与评价

1. 模型＋表示＋查询/状态/选择组件形成一个 Agent 包；环境只接这个封装个体。
2. 分发核验→注册→加载→实际接口能力核对→明确控制窗口→运行。GUI/CLI/API 调用相同用例，不在外面替选或私自补资料。
3. 以奖励重访、战斗 selector、query-stale、终局/接管等场景记录输入/状态边界和反馈。模型弱、协议失效、监督停止分别计数。
4. EvaluationReport 绑定确切 Agent/环境/任务/数据/资源/介入。实例重连、状态恢复和游戏恢复有不同回执。

### 旅程 C：在已通 N 链上加入 Z/O

1. 只从可证明的实际分支与 horizon 构造 Z/O masks；未知终点不填零，未执行候选没有事实后果标签。
2. 保留 O 的胜利、真实失败层数、战斗末 HP、剩余主动出牌数；进度/协议/查询/推理成本单独定义。预测、偏好和奖励分开。
3. 在相同协议、输入、D-M2 图和数据条件下比较 N 与 N+Z/N+O，报告合格标签数、独立终点数、共享梯度、训练与推理成本。
4. Z/O 改善不能只凭 loss；还需 matched evaluation 与闭环结果。加入新目标产生新实验配置，不透明续用旧 N-only run。

## 8. G1、实施包和估计

### 要求—例子—证据—后续验收矩阵

这里明确“现在支持什么判断”和“下一阶段还要证实什么”。不是将 future check 写成已经通过。REQ 定义见 P0，SX/EX 见场景册，NAT 见本轮证据。

| 要求 | 正常/反例 | 本轮已有依据 | 下一项最低忠实检查与必要证据 | Owner / 不通过条件 |
| --- | --- | --- | --- | --- |
| REQ-01 实际有效 | SX-13 重访后推进；EX-55 guard 停止不等于赢 | 历史循环、参考历史区分，未测新策略 | 固定起点的完整 Agent 场景/连续运行，计任务结果/接管/删失 | 评价＋运行；只拿接口成功或 guard 结果代替任务表现不通过 |
| REQ-02 信息公平可达 | SX-02/08 查公开资料；EX-46 旧 Read、隐藏顺序 | NAT-05、公开 Read source、固定 view 成本 | 逐 view 验原生可达时机/副作用＋捕获一致性；真实 profile 场景 | 环境＋信息 owner；提前揭示/缺核心 view 却标完整不通过 |
| REQ-03 完整交互 | SX-03–21 原生状态；EX-16 raw-min、EX-42 未知按钮 | 确切 DLL 机制与 source 对照，有已知差异 | owning seam 回归→exact build→最短真实路径；长尾 Hook/零选矩阵 | E1；由模型猜操作或未支持格被删掉不通过 |
| REQ-04 封装与扩展 | EX-43/48/49、D-M2 one-writer | reference state/guards、内核源码；0 本轮 Torch tests | 真实内核候选不写 W、重排/游标/恢复、输入隔离、在线离线数值对照 | E3；runner 私补信息/当前标签进 W/不兼容状态续行不通过 |
| REQ-05 可靠记录 | SX-11/12/19；EX-44/60 | 现有 Recorder/Evidence 和 native cancellation 分析 | 原生/Human/Agent bounded trace，缺口和并发来源的实际封存/验证 | E2；把心理知识、后补关系或混合 actor 冒充真实见证不通过 |
| REQ-06 数据转换 | 查牌→目标轨迹；EX-56/57 | P4 资格规则、历史资料限制、静态 mask 反例 | 原始来源→目标 view 的完整转换报告，含拒绝与分母、合格小样回读 | 数据 owner；无同时点依据/未知历史仍拼连续链不通过 |
| REQ-07 学习到运行 | D-M2 sequence-N 旅程；EX-52 | 内核/旧 consumer source 与断点审计 | 同一 view/表示/状态进行小训练、checkpoint、导出、实际加载和回放一致性 | E3/E4；训练输入与运行 profile 不同或原 run 配置改义不通过 |
| REQ-08 可解释评价 | 独立 Reset 与 Z/O；EX-56–58 | 历史横轴、分组/目标设计、静态反例 | target extractor masks/计数实样检查，固定独立分组/曝光/不确定性报告 | 研究/评价；未执行候选借标签、旧 dev 洗成 test 不通过 |
| REQ-09 可编程用例 | 同一 CLI/API/GUI 任务；EX-54 | 现有服务入口与 P4 对应设计 | 实际用例服务→CLI→GUI 的同产物/状态回读，真实异常路径 | 应用 owner；前端另算资格或私自重复 mutation 不通过 |
| REQ-10 本地云团队 | 旅程 A/B、EX-50/51/53 | 已有 journal/outbox 和合成状态反例，无 provider 实测 | 本地 synthetic transport→授权远端小任务；失联/取消/权限/备份恢复 | E5；lease/取消请求当终态、重复提交、撤销语义不一致不通过 |
| REQ-11 日常交付 | SX-01/20/21、EX-59 | package/identity/release 源码、旧独立回执 | 精确推荐组合下载/安装/冷加载/停止/重开/配对回退 | E6/R1；分支或下载成功冒充已加载/新运行不通过 |
| REQ-12 稳健开发 | 原生先查、共享 owner、可派发包 | 当前任务模板、治理与独立复审 | 新工程师按包完成一个有边界任务并交给真实消费者，现有路由检查 | 主管/reviewer；只靠私聊、无限等待/重复测试、语义自改不通过 |

### 三个 gate 的本包材料入口

- **G1：**本册、场景册、历史沿革、P3/P4、P1/学习/P5 证据、未测范围和实施依赖。用户/指定设计 owner 与各领域 reviewer 接受明确范围/推荐或退回；缺核心设计决定不能标通过。
- **G2：**后续必须提交真实环境/协议/Agent、原始记录→合格序列/use/split→D-M2 N 训练/checkpoint→ModelArtifact/AgentSpec→实际运行/停止→评价报告的身份链。仅合成探针、旧 M0 加载或旧 private pilot不能代替这条优先实例。
- **G3：**V1 对所选 REQ/SC 逐格证据、连续旅程、异常/恢复、性能/成本、分发/回退、旧入口迁移和使用文档。原生、数据、学习、应用 owner 各验自己的事实，独立系统 reviewer 核对组合，再由负责人接受。

详细接受角色、否决和通过含义仍由[任务表的 gate 合同](../plans/BASELINE_TASKS.zh-CN.md)拥有；上述链接提供具体材料，不建立第二套 gate 状态。当前三门都没有因本次 P5 文档而自动通过。

建议 G1 接受 D-M2 sequence-N 为第一贯通实例，Z/O 为明确后续能力；从详细候选中接受一个完整 AP/IP/OP/HC/MP 组合及每场景 view/action 规则。选择不会取消其他家族、横轴或 C-L 等实验需求。

| 后续包 | 实际内容与先决 | 相对工作量/不确定性 | 可并行 |
| --- | --- | --- | --- |
| E0.1 | 最终源码组合与旧 PR 唯一增量处置 | 中；取决于冲突/独立证据，不盲合 | 文档/数据能力盘点 |
| E1.1 | 原生约束/取消/parent-await 差异、完整列表；先修已发现 owning seam | 高；exact-game 和长尾机制 | E3 的合成 adapter 核心、E2 schema 设计 |
| E1.2 | 核心 view 与 typed Read、有效捕获/目录和实例边界 | 中至高；原生可达/一致性未实测 | 非共享 owner 的 E1.1 子机制 |
| E2.1 | 事件/sidecar/目标轨迹、迁移报告和负资格 | 高；旧数据可保留多少未知 | 原生机制定版后分来源转换 |
| E3.1 | 封装 D-M2、完整事件 consume、query/action、状态/cursor 恢复 | 中至高；内核可复用，新输入合同不可借旧资格 | E1/E2 共用冻结 fixtures |
| E4.1 | sequence-N 准备/训练/产物/评价用例 | 中；必须先补可用 ML 环境与序列资格 | E5.1；不提前大规模训练 |
| E4.2 | 合格 Z/O target 与头/损失/统计 | 中至高；依赖真实终点与 provenance | N 小闭环后按目标拆包，不一口气全矩阵 |
| E5.1 | 复用 journal/outbox，补任务归属/终态核对/资源与用途接口 | 中至高；真实 provider 和权限验证另计 | E4.1；不让数据/预算 owner 重叠 |
| E6.1 | CLI 先贯通，GUI 映射、分发/注册/加载和回退 | 中；已有服务可复用但生命周期要实测 | 用例接口稳定后并行 |

这是基于复用入口和缺口的工程估计等级，不是未经实验的日历承诺。各包正式派发时补具体负责人、资源、最小回归、预算与预计时长；一项未知导致估计失真时更新关键路径，不循环加补丁。关键路径为：G1→E1 公共事实/事件→E2 合格序列＋E3 封装→E4 N 小训练→真实加载/评价→G2；Z/O、GUI 扩展、团队能力按依赖并行，V1/G3 不能因此漏掉承诺范围。

## 9. 是否能确立新基线

本轮可交付 P5 的原生机制核对、详细设计、合成成本/反例、D-M2 贯穿实例和验收计划。仍需 G1 决定首发范围/候选/预算与 view 集；E/V 执行真实构建、输入成本、记录完整性、模型数值/运行、Human/云恢复和性能资格。

禁止的升级：38 条 reference checks 不是 38 个生产修复；JSON bytes 不是 token；98.74 ms 不是模型/游戏耗时；DLL 代码检查不是已加载/Live；所有 SX 已写设计不是所有原生 Hook 已资格化；已有 D-M2 内核不是已训练新 Agent。

文档可以在用户审查授权和最新检查通过后先合入 develop，不等待所有实现。E 阶段增量持续整合；G2/V1/G3 的证据门仍按任务表；R1 才选正式 main 发布。本轮没有执行合并、部署或训练。
