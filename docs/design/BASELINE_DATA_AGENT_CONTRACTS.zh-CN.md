# P4：记录、Agent、学习评估与执行连接设计

日期：2026-10-06。版本：0.2。状态：**设计交付候选；已按 P5 的封装 D-M2 参考闭环细化，不是新数值实现、训练或 G1 接受。** 三条链的具体旅程见 [P5](BASELINE_ACCEPTANCE_PLAN.zh-CN.md)，场景语义见[设计册](BASELINE_SCENARIO_SPEC.zh-CN.md)；精确模型历史见 [依据审计](../evidence/BASELINE_LEARNING_AUDIT_2026-10-06.md)。

## 1. 从完整 Agent 出发，而不只定义一个评分器

任务规定目标、环境能力、起点/分布、预算、结束/接管和评价。AgentSpec 规定实现该任务的组件图、模型/编码器/配置身份、信息获取策略、状态机制、选择方式及资源需要。AgentRun 才是特定环境中的实际执行。相同权重换状态管理、查询策略或控制器，会形成不同 AgentSpec，不能只按模型文件比较。

推荐可复用的概念连接是：

    允许的新事件/观察
      → 输入与合法信息取得
      → 表示及可选状态更新
      → 评分/生成/预测/规划等计算
      → 选择当前协议内请求
      → 环境反馈
      → 后续状态与记录

这些是角色，不要求每个角色独立进程，也不强制加入检索器、规划器或循环网络。完整 Agent 可以是单一学习系统或组合模型；最终必须评价整体任务。联合训练不是 Agent 的定义条件，但局部目标与整体目标的关系必须说明。

对外 Agent 是一个封装个体：上述输入取得、表示、W/历史、规划/候选计算和选择均在登记的 AgentSpec 内。环境依约提供观察，生命周期服务依约控制运行；GUI/runner/评估器不能在外部增加未声明资料、改写状态或替换选择。同进程不取消此边界，调试/实验干预必须记录。该逻辑封装不是已实现的不可信代码安全沙箱。

模型任务合同声明输入/输出、参数与依赖、可选状态及生命周期、长度/资源限制、支持任务和验证范围。通用模型可以返回表示、预测或分数；Agent 运行边界返回协议请求/让出控制，不能让模型发任意原生命令。分数向量是现有 scorer 的输出约定，不是所有未来 Agent 的强制输出。

### 最小可实施的对象合同

这些字段是待 G1 接受的语义要求，物理编码可复用现有 manifest；不是本轮已发布的新 schema。所有引用指向不可变内容或明确 owner 的在途操作，缺必需字段拒绝而非猜默认。

| 对象 | 必需内容 | 主要验证者 |
| --- | --- | --- |
| ModelArtifact | 图/参数/编码依赖身份、输入输出任务、权重 payload、训练 run 和 checkpoint parents、支持的状态格式 | 模型 owner |
| AgentSpec | 组件图及每个组件身份、protocol/profile、representation、查询/选择策略、state policy、支持范围、依赖/资源上界 | 运行适配 owner，环境能力另核 |
| StatePolicy | 更新事件种类及 available-before 规则、reset 原因、重复/缺口处理、保存/恢复能力、假设分支隔离 | Agent owner |
| SequenceInput | 来源/trace/表示身份、事件与观察顺序、输入位置、choice mask、reset/continuity、完整 action keys、targets 的独立引用 | 数据 owner |
| TargetBundle | 目标定义与单位、horizon、source refs、各项 mask/censor/terminal、teacher、分组和规范化参数 | 标签/用途 owner |
| ExperimentPlan | 问题、Agent/model 配置、横轴、数据/split/use、损失/采样/优化/RNG、资源/停止/resume、评价协议与选择规则 | 学习/实验 owner |
| EvaluationReport | 被测模型/Agent/环境/数据/协议身份、实际覆盖和分母、未知/接管/删失、各指标与不确定性、成本、结论范围 | 评价 owner |

因 schema 可解析而接受输入不够：例如标签引用自己的未来输入、不同协议的动作计数、旧状态格式进入新图、wrapped held-out 祖先，都必须在对应 owner 的语义验证中拒绝。

## 2. 保留历史四家族与横轴，不把它们变成所有组合都要训练

历史 [模型总纲](../../python/docs/research/MODEL_DESIGN.zh-CN.md)仍是研究来源。下面保留名称和含义，具体实现/训练状态由审计分开列出。

| 家族/结构 | 计算含义 | 需要避免的误读 |
| --- | --- | --- |
| A | 状态与一个动作联合编码后评分 | 不是所有只有一个主干的模型都另算 A |
| B | 共享观察、候选隔离、动作读出位置评分 | 不能在候选循环里隐含重复整份观察开销 |
| C1/C2 | 动作条件后果表示；C1 单读出，C2 多个后果位置 | 相同图/目标的 B 与 C1 不重复算独立实验；多 slots 不是多个已识别随机未来 |
| D-Simple | 公共当前向量＋轻量动作转移＋评分 | 候选后果向量不自动是正确世界模型 |
| D1 | 当前多槽整理与动作转移用独立模块 | 不等同历史 S2-SDT，只因名称相近不能复用资格 |
| D2 | 同一主干形成当前状态、动作后果，可再次评估 | 两遍/三遍是模型计算，不是两步/三步真实游戏 |

| 横轴 | 可登记条件 | 实验解释 |
| --- | --- | --- |
| 骨干/训练范围 | PF 预训练冻结；PL 预训练适配；RF 同架构随机冻结；S 从头训练 | RF 不是打乱文本；PF 对 S 改了多因素；可训练 query 的梯度可能要穿过冻结主干 |
| 记忆角色 | M0、M1 独立备忘、M2 持续当前表示；内部/外部/组合实现分别登记 | 是职责不是向量形状或强弱等级；上层不要求历史 M1-H4 路线复活 |
| 监督 | N；Z-fact/Z-latent；O-predict/O-preference；明确组合 | 不因为名字 FUTURE/VALUE 就取得预测或价值含义 |
| 表示约束 | R0、经声明的 R-VC 等 | 非塌缩不证明语义正确、分布可辨或策略更好 |

Profile、公开事实/历史范围、tokenizer、图、候选组织、精度、训练预算、数据及终点资格也是实验身份；它们不是可以藏在上述字母后面的开关。模型结构、参数个数、tokens、FLOPs/时间和结果要一并报告。

PF/PL/RF/S 的共享可训练路径必须明确。PF 的 backbone 冻结不表示可将训练 query 的前向全包进 inference_mode；PL 更新参数后不能复用旧激活；S 组不能暗用预训练 teacher/tokenizer。特殊 teacher 对照另登记，不能再称严格 scratch。

## 3. 当前 M2 有多条历史，不是一个统一既成结果

| 路线 | 已有范围 | 新设计怎样使用 |
| --- | --- | --- |
| 旧 native-text M2/Reset、K1/K8、confirmed interaction | 有不同文本/观察/反馈与小样工程记录；不能把一条结果推广到全部控制组 | 保留原身份；复用更新、候选只读、序列/恢复测试机制 |
| light-action M2 核心 | 固定槽更新，可选先前动作/反馈，候选读取同一更新结果；reset_each_step 是明确条件 | 可作为首个有状态 adapter 的候选；输入/事件合同变化需新图/配方身份 |
| 最新 Public M2 | 产品候选为 observation-only、no-prior-action、no-feedback、scratch、N-only | 不能直接称完整交互历史 Agent；不得用 runtime control metadata 偷作模型特征 |
| 正式 A06/A08/A02 | 选定 carry8/window4 的三个训练规模；此前交接 accepted 为 0 | 是旧实验计划，不是 M0/Reset/记忆对照，也不自动恢复执行 |

用户现已指定 D-M2、时序 N、随后 Z/O 为优先贯通实例。首个数值复用对象是 LightActionM2 的 D-Simple M2 内核，不是 D2；slots/窗口/encoder/预算仍须在新 recipe 固定。其他家族与无历史/独立 Reset 对照保留，不要求先训练全部组合。旧权重可作初始化或原条件参照，不能把改了协议的运行冒称原实验续训。

## 4. Agent 状态、输入与训练状态合同

Agent 可声明 stateless、内部状态、外部历史或组合。其状态操作为 initialize、consume、decide、cancel、close；支持 checkpoint 时再声明 export/import。状态由 owning 组件维护，可受控检查/实验干预；干预生成明确实验条件。

- consume 接收已提供且顺序有依据的事件；同一事件重传不得写两次。新 observation 内容相同也可能是新的真实事件，不能按文本去重。
- 当前候选假设计算不修改实际持久状态。训练 target 不进入当前状态写入；真实选择及其反馈只有按可用时刻进入后续输入。
- 模型更新与 cursor 的提交要有明确恢复策略；进程在二者之间崩溃时，要么由已存状态和游标恢复，要么从合法前缀重建，要么显式 reset 新 segment。不能声称凭网络实现全局恰好一次。
- initialize/reset 的原因包括新任务、已证新局、历史缺口、模型/表示不兼容或显式实验控制；每一种分别记录。模型 segment 重置不是游戏重开。
- TBPTT 切窗口只截梯度，不自动清空 carry。burn-in、前缀重放、跨参数更新的状态近似都属于已版本化训练方法；不可复用与当前参数不相符的缓存后假称完全等价。
- 不支持状态导出时明确拒绝无缝续行；不能把空状态说成恢复。unknown 原生投递进入监督/核对路径，不自动产生第二次执行；允许保留未知事件本身作为历史。

Agent 输入包只包含声明的公开观察、已经取得的资料和合法历史。任务许可、预算、lease、源 SHA、训练标签与未来结果保留在控制/审计层；公开对象关系以可验证的局部别名保持。模型输出必须映射回当前完整目录，绑定复验仍由环境负责。

## 5. 记录到训练的产物链

    RawEvidence
      → VerificationReport
      → ProtocolTraceView（含映射、缺口和资格）
      → DatasetSelection / Use / Split
      → Representation + SequenceInput + TargetBundle
      → ExperimentRun / TrainingCheckpoint
      → ModelArtifact + AgentSpec
      → AgentRun / EvaluationReport

以上是语义角色，不预先建立另一套存储 schema 或账本。尽量映射到现有不可变 manifest、用途 owner、Registry、RunReporter 和操作 journal。每个派生物保留 parents、配置/转换身份、资格报告与 exclusions；索引可重建，不取代原始事实或权限。

ProtocolTraceView 要分别描述 observed-before、available-before、confirmed-input、same-continuity、native-causal-successor。录制 append 顺序、环境捕获顺序、Agent 获得顺序、native Commit 顺序不可混成一个时间戳。

每个事件/样本可以有独立的 observation、choice、history、feedback、successor、run-outcome、combat-end 与 counting masks。缺 N 标签的合法历史事件仍可能参与后续状态；缺 Z/O 不影响独立合格的 N 样本。没有一个统一 valid 布尔值代替用途判断。

先按 native run/共同起点/重复来源/完整历史依赖 group 切分，再在 train 内拟合词表、数值尺度和采样规则。物理游戏独立性未知则保持未知。Gold、撤销、先前曝光和用途账不能通过新视图洗掉。

## 6. N、Z、O 的目标语义

### N：行为监督

[L-N v1](BASELINE_LN_V1_SPEC.zh-CN.md)补充模型无关的Choice端口与目录访问提案。当前参考全量scorer的N学习实际选择在完整候选集中的条件分布，默认可用listwise cross entropy；它不自动表示人类最优策略。信息重表达后的 Human 标签仍是行为来源，不声称 Human 读过全部新输入。对查询、导航和选择器是否提供 N 标签由目标协议映射决定，派生动作须标明 derived，不能冒充 native Human occurrence。

生成/分解/检索式Agent可以采用独立声明的动作token、条件因子或shortlist目标；这不等于旧全目录N loss，也不改变Connector完整合法集合。完整目录不必整表进入每个模型上下文；新端口的prefix/Resolve与旧Runtime强制scores合同分开版本化。

### Z：实际后继的监督

Z-fact 预测有资格的真实后继公开字段，按对象对应、适用条件和缺失逐字段 mask。Z-latent 的 teacher/编码、更新方式、stop-gradient 和输入可见范围必须登记；不能将持续历史状态强迫等同于只看当前页的 teacher 向量。

Z 只监督实际执行且结果关系符合目标定义的分支。未执行候选不复制该结果。查询响应/界面导航的可预测性可研究，但需单独的 view-transition 目标；不能因为有后续响应就冒称原生 causal successor。父操作尚待子选择时，不把即时页当父效果最终后继。

点估计、MSE/余弦和非塌缩不保证区分不同随机分布。需要概率或分布表示的实验应另定义编码、proper scoring/分布检验及可识别条件，不能把 Y 命名为 future 就省掉这一步。

### O：长期结果预测、偏好、奖励和评价必须分开

历史 O-chain 已提出胜利、真实失败层数、战斗末 HP、剩余主动出牌数与战斗终点 latent。本设计保留这些研究目标，分成有独立资格的目标，不把它们简单相加成奖励。

| O 目标草案 | 标签和范围 | mask / 删失 | 解释与风险 |
| --- | --- | --- | --- |
| run_win | 已确认整局终点的胜利与 termination kind | 无结束证据、断录/失联不能填输；放弃是否记已知非胜利由预登记语义决定 | 包含后续行为和随机性，预测的是声明行为条件下的结果，不是最优 Q |
| failure_floor | 已确认真实失败的 act/floor 与规则版本 | 胜利、未知、没有失败证据的放弃不填 0 | 保留历史失败位置目标，不把当前层当最终失败层 |
| run_progress | 到达/完成的位置及观察终点，区分确切终点与已知下界 | 中断为 censored/lower-bound，不冒充最终层 | 不同地图/版本层号未必可直接比较；与 failure_floor 不是同一字段 |
| combat_end_hp | 同一确切战斗终点的 HP、max_HP、生死及资源上下文 | 未到终点、跨战斗、未知接管结果按条件 mask | 牺牲生命换收益、低血机制、最大生命变化使“血越多越好”不是普遍偏好 |
| remaining_active_plays | 从当前实际执行分支的决策位置到同一合格战斗终点，剩余已确认主动出牌数 | 断链/未知次数不填 0；计数范围须有完整证明 | 自动重复效果和子选择不重复算；少出牌可能是早死或放弃，不能独立奖励 |
| semantic/information/control costs | 各协议操作数、Read/导航、推理次数、重试/拒绝、接管、时间等独立计数 | 来源和完整性分别标记 | 是评价/可选预测量；不同协议粒度不可混比，不由旧 active-play count 代替 |
| combat_end_latent | 合格战斗终点通过声明的 teacher/编码形成的目标 | 无相应终点则无标签；编码身份与预训练来源明确 | 是可选辅助，不能暗中破坏 scratch；不是胜率或奖励 |

每个 TargetBundle 必须声明：metric 定义/单位、起点、horizon、terminal predicate、计数规则、source refs、label-known、censor kind、actor/continuation-policy 条件、适用字段、分组权重与用途。run 与 combat 的 horizon 不混用；不同指标分别有分母。

主动出牌数沿历史 O-chain 的起点口径：从当前决策前起算，若本条实际操作已确认是主动出牌则计 1，再加合格后续链的主动出牌数；药水、结束回合、子选择本条计 0，但仍可有后续牌数标签。自动触发不重复计数。若研究选择不含当前操作的其他口径，须另立 target 版本，不能混在同一标签中。

O 标签由真实已发生结果经有依据的链回传；后续包含什么行为必须披露。Human 数据预测行为分布下的结果，不证明换成新 Agent 后同动作仍有相同胜率；未执行候选没有事实反事实标签。接管/混合策略可做另有身份的研究数据，不作为无干预 Agent 的结果。

### 如何从目标得到损失和选择

O-predict：各项单独输出和评价。已知胜负可用二元概率目标；层数/成本用声明的分类、分布或回归方法；HP 同时报绝对与预定义归一化含义。缺失先按 mask 去掉，再按各自有效量归一化；不能因某终点被复制到数百步就获得数百份独立结果权重。按 run/combat/来源 group 平衡，报告有效终点数。

为给实现一个具体参照而不丢失历史，保留旧 O-chain 的候选起点：实际分支 Y 经共享 MLP(d→256,GELU) 接四个头，胜利用 BCE，失败层/HP 用 train 尺度 Huber，剩余主动出牌以 softplus 非负读出后用固定尺度 Huber；各目标独立 mask、按局/战斗平衡。旧 end-latent 是独立 q_end 与固定 Qwen teacher 的余弦目标。它是已有设计配方，不是已实现 O 训练；新 profile 的对象/时间/teacher 兼容须核对。严格 S 条件不能暗用该 Qwen teacher，应移除该头或另登记无预训练先验的目标方案。旧系数 1 是历史待试起点，不是本轮批准的最优权重或新 run 配置。

总训练损失可以组合 N、Z、各 O 头和 R，但所有系数、归一化、采样与梯度共享路径须在训练前登记。损失系数平衡学习信号，**不是“胜利值多少滴血/多少次动作”的效用兑换率**。若辅助头与策略没有可训练共享通路，它只是在做 probe，不能声称改善策略表示。

O-preference：只在可比较起点、信息能力、结果范围和资源条件下，用双方真实结果及预先固定的规则形成偏好。不同未知起点的一胜一败不能直接成为动作偏好；不能从一次结局推导最优动作。偏好数据也是另一种资格，不由 O-predict 自动生成。

策略选择默认由声明的 policy head/方法产生，不自动把 O 头加权后替代它。若要直接用预测胜率或多目标 utility 选动作，必须登记新的 AgentSpec、校准/离线偏差检验和独立实战评价。不要固定惩罚所有返回/查看；它们可能是合理的信息取得。

评价建议以预登记任务成功/胜负为主要结果，同时完整报告层数、战斗 HP、主动出牌、协议/查询/推理成本、失败和接管。若用户需要不同效用权衡，在 G1/实验计划中明确，不由实现者私设一个总分。RL/奖励塑形是后续独立方法，不由 N+Z+O 自动产生，也不能仅增加 HP/层数奖励就声称保留原目标。

## 7. 一条有状态训练与运行序列

    观察奖励列表 → 更新合法状态 → 对目录预测 N
    实际选择打开 → 记录请求与反馈
    观察奖励内容 → 更新状态 → 对目录预测 N
    实际返回 → 反馈进入后续历史
    再见相同列表 → 使用已保留的经历作选择
    后来真实合格结果 → 仅作为训练 target，不回到早先在线输入

有 N 无 O 的片段仍可用于相应训练；有 run 结果但缺可靠历史的片段不自动满足 M2。Z/O 标签需要各自关系，不能靠同一文件或相邻行证明。旧 Public M2 的 exact-execution-edge 条件保持原义；新历史资格从目标协议轨迹重新构造。

训练 checkpoint 与推理状态不同：前者还需要 optimizer/scheduler、数据游标、RNG、采样与训练配置；后者需要 Agent/表示身份、事件游标和环境连续性。改数据、目标、编码或图不是同 run 的透明 resume；允许以旧权重初始化新 run，并明确 parent。

## 8. 第一版实验组织：先回答问题，不跑笛卡尔积

| 问题 | 建议比较 | 固定与报告 |
| --- | --- | --- |
| 持续历史是否有用 | 相同 M2 图的 carry 与独立训练 reset；另可做推理时重置扰动 | 相同协议、公开信息、编码、slots、训练预算/数据；扰动不冒充独立 Reset 训练 |
| slots 容量是否有用 | K1/K8 等有据配置 | 记录新增参数/成本，不称严格等计算；本轮 P5 因缺 Torch 未测数值可行性，列为 E3 有界预检前置 |
| 数据规模影响 | 同图、同目标、同 encoder 的嵌套 train tiers | 与最新 A06/A08/A02 的旧实验区分；旧 dev 不变新 held-out |
| 预训练是否有用 | 匹配 PF/RF；PF/PL 或 PF/S 分别说明系统差异 | 同输入和可训练模块/初始化条件；PL 成本与激活重用重新核对 |
| Z 是否帮助策略 | N 与 N+Z-fact；条件合格后 Z-latent | 同图/目标头与数据规则，报告标签覆盖、共享梯度、后继指标和实战 |
| O 是否帮助策略 | N 与 N+O-predict；必要时 N+Z+O | 各目标 masks、终点数、分组与权重；不能用 loss 改善代替策略结果 |
| 结构是否有收益 | 选有明确问题的 B/D-Simple 或其他结构 | B/C1 同图不重复注册；C2/D1/D2 的新增结构单独验证 |

最小工程顺序以封装 D-M2 的 sequence-N 闭环为主；无历史参照和独立 Reset 用于匹配检验，不代替主链。其后是有资格的 Z/O target→同主体目标消融→扩大结构/骨干和规模。旧 Qwen/LoRA、N/Z/O 和真实训练里程碑保留，在 G1 明确本版安排；本表不是取消旧目标或授权开跑。

已有 Public M0 与 Public M2 使用因果/双向编码等不同内部设计，两者现成结果不能作为纯记忆因果对照。旧小样、prepared、synthetic、正式训练、GPU、实际加载、独立游戏表现分别报告。

## 9. 评价合同

| 评价层 | 主要检查 | 不证明什么 |
| --- | --- | --- |
| 组件/表示 | 输入输出绑定、候选排列一致性、状态隔离、梯度/复现、线上线下等价 | 不证明策略强 |
| 离线 N | top-1/MRR/NLL、可接受动作/严重错误（需独立标签） | Human 选择不是普遍最优；小 dev 不证明泛化 |
| Z/O | 字段误差、概率校准/适当评分、终点/轨迹覆盖、censor 分布 | 准确结果预测不是因果动作优势或最优 Q |
| 历史能力 | 当前页相同历史不同；独立 Reset、换局/缺口、重复事件与候选假设写入 | 拥有状态接口不等于学会记忆 |
| 闭环场景/整局 | 成功/胜负、到达位置、HP/资源、操作/信息/推理成本、循环/接管 | 单次运行不证明统计优势 |
| 工程/监督 | 安装、加载、延迟/资源、停止/恢复、guard 误报与触发 | 保护器成功停止不等于 Agent 完成任务 |

统计单位优先使用独立 run/共同来源 group，并报告训练 seed 与环境随机性。步级样本及重复终点不能假装独立；相同种子也不保证不同 Host 同轨迹。比较使用预登记的任务/起点、信息/操作计数、预算、介入规则与不确定性方法；测试集不用于反复选择模型。未知独立性的数据仍可作工程诊断，但不能给科学独立泛化结论。

参考方法仅用于设计依据：[Agarwal 等关于少运行次数与统计不确定性的研究](https://arxiv.org/abs/2108.13264)。具体样本量和停止规则由 P5/实验计划决定，本轮没有建立或执行新 benchmark。

## 10. 第三条链：库/CLI 优先的应用服务与执行合同

这些设计属于 P4 的工程连接工作面，不让模型研究 owner 一人包办。学习、执行、数据权限和发行各有 owner，共同审查接口；内部算法/GUI 排版留给 E 阶段，不在这里创建通用插件框架。

| 应用用例 | 输入与输出 | 责任与复用 |
| --- | --- | --- |
| inspect/prepare-data | 来源和用途要求→资格/转换报告、固定视图、排除项 | 数据 owner；不自动修改旧 use/Gold；复用 store/curation |
| prepare-experiment | 数据/模型任务/目标/评价/资源→不可变实验计划、预检结果 | 学习 owner；不从 GUI 拼私有 provider 参数 |
| execute/status/cancel/reconcile | 已批准计划和资源→实际 attempt/handle/终态 | 执行 owner；复用 Hub/本地 journal，不新建第二事实账 |
| resume | 合格 checkpoint＋原 run 条件→新的执行 attempt | checkpoint 验收属学习 owner，外部执行归执行 owner |
| accept-result | 候选输出→已验证、被选择的产物或拒绝原因 | 领域 owner；provider 完成不自动 accepted |
| evaluate | 固定 Agent/model、环境/数据、评价计划→独立报告 | 评价 owner，记录曝光和身份，不在界面重算含义 |
| export/register/load/run | 模型/Agent 包与兼容环境→各阶段独立回执 | 发行/本机运行 owner，不自动接管游戏 |
| share/download | 产物与当前授权→传输与本地验证回执 | 访问/用途与存储 owner；GUI 只投影状态 |

### 任务与资源

ExperimentRun 的语义身份与执行 Attempt 分开。TaskRequest 引用输入/配置/代码、发起主体、授权与用途、资源要求、预算和领域验收规则；ResourceOffer 描述实际支持的 CPU/GPU/内存/依赖/存储位置及有效期；PlacementDecision 记录为何选择某资源。第一版可明确人工或有界规则选择，不必实现通用集群优化。

共享执行 seam 只理解 prepare/submit/inspect/cancel/reconcile、target、handle 和终态观察。训练/评价的标签、数值结果与 checkpoint 格式由受信任任务 adapter 验证。注册受信任 workload kinds，不接受任意用户代码或第二套原生操作入口。

操作状态和产物状态分开：prepared、submit-intent、submitted、submission-unknown、running、cancel-requested、terminal observation；candidate、verified、accepted/rejected/selected。任务可以远端已结束而结果尚未验收。超时、失联、lease 到期不证明 provider 停止；unknown 先核原 attempt，不能自动重提。预算预留不等于账单，释放条件必须有 owner 依据。

已有本地任务、Hub Operations、上传 outbox 与专用 campaign 各有事实 owner。共同应用服务路由 owner_ref/operation_id 并投影状态，不能复制一个万能作业 ledger。跨 owner 的稳定预检前置；短时授权/账户证据临近需要它的步骤核对，不靠自动续期掩盖延迟。

### 权限、数据流通与分发

本地私有数据可本地处理；团队资源遵循成员、设备、共享、用途和预算的各自权限。同一次配置体验不等于万能密钥，也不要求每进一个页面再授通用权限。旧私有资料不因登录自动上传；离线缓存不洗掉未知用途。

源共享撤销对 dataset、训练/恢复、checkpoint、模型和报告的影响必须在 policy 中逐类明确，并由共同 admission owner 执行；不能从当前 dataset 专门检查推导所有派生物均被撤回。已下载字节不可远程抹除；保留下载/使用凭据、策略版本、最后核对时点与离线允许范围。具体撤销政策由 G1 接受，产品不得作超出能力的承诺。

DistributionManifest 引用可核验的 Mod/Host/Connector/Runtime/Evidence/Agent/依赖与支持范围，沿用现有 BOM/发行 owner。先下载核验、在安全边界启用、保留配对回退；不每次更新 Mod 就部署 Hub，不从分支 latest 推断运行版本。注册、安装、加载、实际执行分别有回执。

CLI 与 API 使用相同应用用例，返回 operation ID、机器可读状态/错误及产物引用；GUI 可组合步骤和保留上下文，但不得私自重试 mutation 或改变完成含义。关闭面板不取消独立任务，关闭游戏影响游戏绑定控制但不自动终止独立云训练。

## 11. 旧资产与当前推进的处置

| 资产/工作 | 建议用途 | 必需新判断 |
| --- | --- | --- |
| 当前完整 M0 产物 | 原条件重现、加载链与编码/评分参照 | 改输入或外壳后是新 AgentSpec；不冒充具有历史能力 |
| native-text M2/Reset/K1/K8 | 独立工程案例、状态/序列机制复用 | 不将小样/旧 dev 推广为 memory superiority |
| Public M2 numerical/Port4/campaign | 候选内核、checkpoint/transport/consumer 机制 | 新历史和目标需新合同；正式旧 campaign 不自动重开 |
| N/Z 旧模型实现 | 目标函数/执行分支 mask 的有界复用 | 与新观察/后继定义匹配；不能把旧图名当新家族 |
| O-chain 与横轴计划 | 保留研究问题、目标语义与反例 | 目标抽取/头/损失/评价需各自实现验证，不能宣称已存在完整 O 训练 |
| 原始记录与 dev | 按目标用途生成新视图或明确拒绝 | 信息时刻、历史、终点、独立性、用途与成本；允许 0 条满足某种新资格 |

顺序是先验明源与新需求、贯通小链，再扩大训练。数值实现、真实数据迁移、实验启动、GPU 和安装都不在本轮设计权限内。

## 12. P4 完成条件与 G1 待决事项

本文件提供完整第二条链的设计，以及第三条链的关键用例/状态/产物/权限接口。完成设计交付不等于所有新组件已经实现。P5 应验证每个角色的正常/反例、成本与资源估计，将发现的编码/阈值缺口交回 P3/P4 owner 修订，不能自行改变契约语义。最终编码与限额随 G1 选定候选封存；E4/E5/E6 依据受审版本实现。

G1 需接受：P3 profile 与首发范围；新历史/数据资格；首个 Agent 图与表示/状态合同；N/Z/O 的本版目标与来源；主要评价目标和其他指标使用方式；旧里程碑保留/延期；作业/权限/恢复与发行边界。具体实验的数值超参数、目标权重、样本量和预算必须随后封存，不从本文示例自动产生。

最低反例集合：缺序仍当 M2；当前标签提前写入；候选假设污染状态；未知胜负填失败；用当前层冒充失败层；跨战斗借 HP；早死因出牌少得高分；给未执行候选复制终点；同终点复制样本当独立；旧 checkpoint 接新输入仍叫续训；取消请求当已停；GUI 重试未知操作；资料撤销政策不一致；新包加载而模型不具备声明能力。

外部研究仅支持方法边界，不作为本项目实验证据：[Ng、Harada、Russell 的 reward shaping 研究](https://ai.stanford.edu/~ang/papers/shaping-icml99.pdf)说明额外奖励的策略不变性需要条件，不能将一般 HP/进度/步数加权直接称为原目标的等价优化。这里没有假定 STS2 的公开观察满足完整 MDP，也没有采用某个 reward shaping 公式。
