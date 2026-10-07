# G1 待批准合同：A 主系统、记录、Agent 与执行

最新默认：[采集与缓存详解](BASELINE_CAPTURE_CACHE_DEFAULT.zh-CN.md)。I/F先关闭，首个Model仅消费声明的当前公开观察与W（候选仅供选择评分）；历史动作/receipt不换名塞入P/E，控制侧照常核对。下文I/F能力与旧实现是通用/历史说明，不表示默认启用。新请求式默认区分按需ReadCurrent与已封存ReadSealed，不承诺每个早期通知都已有全部历史内容。

最新澄清以[RC2交互边界、模型输入与预算](BASELINE_G1_AGENT_BOUNDARY_AND_BUDGET.zh-CN.md)为准：D01与具体协议仍待确定；比较Host侧、协议核心与Agent侧的职责分配，不预先固定Agent管理能力边界。全角色A0–A10为设计目标，Defect A0为首个验收切片；教程默认由任务准备关闭。

日期：2026-10-08。版本：G1-RC2。状态：完整设计候选，**未经用户批准，不是已生效生产协议**。配套[审阅总册](BASELINE_G1_REVIEW_PACKET.zh-CN.md)、[实施与验收路线](BASELINE_G1_EXECUTION_ROADMAP.zh-CN.md)、[独立审计](../evidence/BASELINE_G1_REVISION_AUDIT_2026-10-08.md)。本文件将GD待决项收敛为可接受或退回的具体建议；非继续把关键语义留给实施者猜。

“必须”表示接受本版本后实施所需满足的语义；不是声称当前实现已满足。字段为规范级数据模型，生产schema/SDK在E1–E4按此实现、生成与验证，不在本轮注册wire版本。已有实现/外部方法只作为明确标注的复用依据。

## C01 首发任务与信息范围

推荐设计范围：单人基础游戏全部角色A0–A10，Defect A0作为首个资格切片，固定游戏构建与允许modset，从第一个局内选择到原生胜负结果及summary退出；用户管理侧负责启动/继续/场景准备。支持平台先本机macOS原生Host，Linux/Windows继续portable源码检查；其他实际Host/平台单独资格，不假称都能加载游戏。游戏版本不是永久固定，变更按受影响机制重新资格。

范围内可达的基础游戏卡牌/药水/遗物Hook与特殊事件仍在分母；教程默认由管理/任务准备关闭，显式教程profile另验；不能为了早交付标成扩展后删掉。不在首版承诺内：多人、任意玩法Mod、像素坐标控制、真实视觉输入、可克隆模拟器、自动通用集群调度。它们可以复用底座扩展，不能借未覆盖证明泛化。

信息默认是**当前已进入逻辑页的完整公开内容、实际揭示的提示/预览及公开历史**。typed对象/关系为主合同，确定性可读renderer按对象展开正文；紧凑传输/缓存不改变信息曝光时间。向量、token、模型摘要属于Agent表示，不是唯一环境格式。资料操作依当前原生能力进入/退出页面，不默认把未打开牌堆/奖励组/商店的内容聚合进输入。

保留当前实例/定义/展示上下文的区别；动态卡正文、费用/星费/附魔、目标focus、升级预览、pile身份、公开遗物计数、药水槽位、keywords/preview对象都有类型与来源。隐藏、未揭示、当前不可读、未支持分别表示；确定性排序不是原生抽牌顺序。颜色/图标承载公开语义时须有声明的结构/文字映射，不能静默丢掉。

## C02 责任与三个平面的连接

| Owner | 唯一负责的事实 | 消费/输出合同 |
| --- | --- | --- |
| STS2 | 规则、RNG、native合法性、执行及Commit | 原生只读事实与精确操作入口 |
| Native Foundation / Connector | 原生语义见证；公开Frame、完整当前C、输入绑定/复验、公开Receipt及事件 | Host实现能力→A；不引入研究标签/策略 |
| Host Runtime | 实例、进程、隔离、健康、exact identity、管理恢复 | 管理API；不进入普通Agent动作目录 |
| Annotator | 原生Human行为及捕获质量、exact causal关系 | RawEvidence，不自行授予训练资格 |
| Evidence | 不可变原件完整性/来源验证、封包与传输 | VerificationReport，不制造Human来源或科研资格 |
| Policy Runtime | 授权窗口、唯一控制出口、执行账本、停止与恢复、AgentRun | 消费封装Agent输出，不负责推理/过滤C |
| 封装Agent / Model | Agent直接对接声明协议；Model执行具体计算；组件及职责分配可调整 | AgentSpec、模型输入输出/状态与运行产物 |
| 研究实现（当前STPD） | 数据投影、目标、训练与评价，不等于所有Agent | Projection/Input/TargetSpec、数据/模型/报告 |
| 项目应用 | 用途/资源/作业/产物/共享/分发，统一入口 | typed应用用例；不代签游戏或研究结果 |

数据面承载公开信息/请求/产物；控制面承载授权/生命周期/作业；证据面保存已经发生的事实和关系。逻辑隔离不要求三个进程、三个仓库或三套存储。一个实例只有一个native写入owner；旁观/管理/录制不私建第二局或抢同一stdin。

## C03 Describe / Attach 与能力清单

请求：`TaskSpecRef, AgentSpecRef, required_capabilities, optional_capabilities, expected_instance, resume_claim?`。返回：`protocol/profile/version, instance_id, generation, task_id, actual_build/component/artifact_refs, accepted_capabilities, rejected_optional, control_scope, history_bounds, limits, clock_domain, current_frame_ref`。

能力至少区分：realtime/paused/stepped；当前信息字段与事件范围；capture/render质量；List/Prefix/Resolve；request retention/reconcile；state恢复；管理seed/save/restore；视觉/分支/多玩家。required缺失或unknown即启动失败；不得自动换一个信息更少或游戏暂停的profile。

连接、游戏控制、录制、训练用途是四种不同许可。Agent权限按角色与任务合同决定。普通局内策略不获得任意底层操作；获授权的完整实验Agent/Runner可持有限Prepare/Reset/Save等任务能力，独立于gameplay目录并记录预算/起点/干预。管理凭据、私有queue identity和游戏隐藏状态不得作为普通策略特征。允许的控制元数据保存在客户端审计侧，只有AgentInputSpec明确列入的公共特征进入模型。协议控制版本变化不自动重训，输入/时机含义变化必须新profile或新AgentSpec并重验。

## C04 Frame、事件与时间

| 对象 | 必需语义字段 | 约束 |
| --- | --- | --- |
| Frame | instance/generation、observation_ref、publication_cursor、clock_ref、public page/objects/relations、catalog_ref、quality | 不可变；完整性只针对声明scope；不是原生全世界/完整GUI |
| Event | source_id、event_id、generation、publication_cursor、kind、payload/ref、capture interval、publish time、quality/gap | 每个generation单publisher分配严格递增发布序号；重传同ID，新重访新ID |
| Exposure | event_ref、recipient/segment、provided position/time、component消费引用 | Host捕获、完整Agent接收、模型消费不混成一个时刻 |
| Capture health | attempt/error sequence、seam、coverage、failure range/unknown range、persistence watermark | 独立于成功capture序号；缺采不能因成功记录连续而隐身 |
| Clock | domain/generation、单调单位ms、origin、精度说明、basis publication value | UTC仅关联诊断，不用于跨机排序或Await截止时间 |

最小公开事件族：`view_entered/view_changed/view_exited`、`public_facts_changed`、`input_capability_changed`、`request_status_changed`、`run/combat/task_boundary`、`gap`。控制流另有`control_revoked/session_closed`。focus/selected/preview变更归带明确subtype的view事件；同一原生变化合成一个含完整变更的semantic occurrence，若无法合成则分别发布并有source关联，不能一边去重一边多写W。

事件是已捕获公开事实的记录，不承诺持续时间内所有世界变化都被观测。CaptureSpec枚举必须观察的seam和失败信号；publication连续仅证明所发布序列连续。当前Snapshot.Sequence、capture ordinal、append顺序都不能替代新E游标或证明无漏采。

进入/退出/焦点/目标预览/选择/结果不可latest-only丢弃。仅明确无信息及行动含义的装饰刷新可合并，返回coalesced范围与规则版本。大payload可content-addressed引用；内容hash相同不使新的occurrence成为重传。缺delta基底先取得完整基底；无法恢复时gap，不拼接不同capture的字段。

## C05 缓冲、背压和一致性

Attach必须公布有限`max_events/max_bytes/max_age_ms/max_payload_bytes`及earliest/latest cursor；启动manifest必须填具体值。建议E1本地预检起点：4096 events、64MiB累计buffer、120秒保留、8MiB单payload；这些是**待测配置起点，不是性能保证或数据裁剪许可**。任一限制先到即按明确overflow规则处理。超过单payload可使用完整分块/对象引用；不可截候选后标complete。资源修改保留配置身份，数值在G2 pilot后、V1确认试验前封存。

publisher/SDK/Recorder各自有独立有界队列与健康状态；控制撤权保留独立槽和通道。缺必需语义事件则Agent Suspended，禁止继续假装完整记忆；游戏可继续，记录注明gap。Recorder失效不等于Agent一定失去公开输入，但该段训练/证据资格必须降级；反之亦然。

native热路径只进行有界捕获、复制必要不可变数据和队列登记；不能等待网络或任意落盘。原生对象引用只能短期用于exact correlation，不是已冻结内容。涉及多来源的Frame须声明game-thread/owner fence及捕获首尾版本；不具备全局一致性就如实报告范围与unknown，required一致性不满足则不发布complete。E1测量后才能承诺不扰动游戏。

模型计算期间事件继续按序缓冲；同一W的写入串行。允许取消尚未提交的过时推理，不能取消已经交给原生的动作。新事件不授权SDK把旧输出换绑最新目录；Agent必须重新选择或接受stale拒绝。

## C06 完整C、分页、Prefix 与 Resolve

C是当前**public native input relation**，不是execution semantic catalog。已入队的牌可能仍在logical Hand却没有可输入holder；execution目录仅供法证/研究有资格转换，不直接给Agent重复提交。

C发布时封存`catalog_ref, observation_ref, count, complete, content_digest, schema/profile`。List页包含同一C的opaque cursor、范围与总数；固定序列化次序不得暗藏质量排名。目录改变后，旧页可从旧不可变对象继续读或返回stale；绝不把新旧页面拼成完整C。若实际无法形成完整当前C，标partial/unsupported且不授权从子集自动行动。

`AllowedNext(C,prefix)`只投影C已有关系；`Resolve(C,expression)`返回exact-one/no-match/ambiguous/stale。二者无native效果、不是未来状态推演、不是额外游戏decision。API查询诊断可聚合计数；是否把新取得材料交给模型由AgentInputSpec记录，不必每次内部查表成为训练步。

完整C可不整表进入模型prompt；AG01全评分必须读全，生成式可用结构语法/Resolve，检索式可近似选择shortlist。后两者不改变环境C，Resolve成功也不取消Submit当时原生复验。

## C07 Act 与结果状态机

Act输入：`instance/generation, control_epoch, request_id, request_fingerprint, basis_observation/catalog, resolved_action_ref`。原子输入写入串行；已知在途native效果可以有多个，当前是否仍可输入由native决定，协议不强加“上一牌结算完”。

| 维度 | 值 | 行为 |
| --- | --- | --- |
| admission | admitted/rejected | 语法、权限、basis、完整C及native复验；rejected明确没有进入投递 |
| delivery | not_delivered/delivered/unknown | unknown只核原request，不换ID重发；已知delivered不能因没看到后继改名unknown delivery |
| public outcome | pending/completed/cancelled/rejected_at_execution/unknown | 来自可公开来源；不等同canonical causal proof |
| forensic proof | execution/Commit/successor/eligibility各自结果 | Evidence/研究侧保留；不向普通策略泄露私有身份 |

同generation、同request、同fingerprint重放只返回已有状态；同ID不同内容拒绝。真正的新策略选择即使表达相同动作，也需新request及当前有效依据；它不是对旧请求的补偿重试。旧delivery明确not_delivered，或delivery已知且当前native目录确实仍允许该新输入时，可以按Agent策略作新选择。已知delivered且outcome pending不要求等待效果结束才发下一项；真正delivery unknown仍先核原request并阻止mutation，不可通过模型reset/新segment洗掉。

结果账本寿命至少覆盖声明的重连窗口，进程内幂等不等于跨进程恰好一次。首版不承诺跨原生进程透明续行：重启换generation、旧控制权失效；若没有可核验旧请求持久账本，返回`reconciliation_unavailable`并保留未知。管理重开是显式新任务，不证明旧输入未发生。

已知delivery且outcome pending可继续观察，并在native允许和Agent策略选择时再输入。outcome unknown若影响required public state/控制可信度则Suspended；若仅缺研究causal proof而公开状态及控制仍可验证，不强制全局停机，但该关系不获Z资格。不得将这种区分用于绕过真正未知投递。

## C08 Await、控制与终局

Await输入：已知observation/cursor/clock basis，可为空的触发集合、相对basis publication的非负有限`deadline_after_basis_ms`；合法加法overflow拒绝。触发为`next_event(kind)`边沿或`until_state(public_predicate)`电平，OR连接；谓词只来自当前profile白名单。空触发集合表示纯timer等待，仍必须有有限deadline；非空集合任一匹配可提前wake。等待是Agent调度操作，不生成native Wait或Human行为标签。

在publisher注册栅栏内先核gap、扫描basis之后buffer和当前谓词，再注册；已发布且时刻不晚于deadline的合格event优先timeout。截止已过而没有合格历史命中则立即timeout。Stop/撤权优先尚未sealed结果；结果sealed后不改写过去，另发control事件。返回wake/timeout/abort/gap中的一个终态，并提供最新可用Frame；历史满足不保证现在仍满足。

Stop：先使新增mutation授权无效和取消未提交推理，再核对在途请求及释放owner，返回requested/acknowledged/released等真实阶段。停止模型、关闭游戏、关闭UI、取消训练是不同用例。已入队动作可能继续，不伪造撤销。game_outcome_known与task_complete分开，summary操作仍有合法目录及事件；新局重置W与generation。

## C09 Human capture、投影与数据资格

原生Human不是API客户端。拟捕获：公开view/focus/preview/choice/进入退出、输入可用性、Human输入seam H、exact admission/execution/Commit/successor、run/combat/actor/terminal边界及gap；不录每个像素抖动，不声明人实际理解所有曝光内容。原件与派生内容分开存储，事件/输入/append时序分别保留。

`RawEvidence → VerificationReport → ProtocolTrace → AgentInputTrace → SequenceInput + TargetBundle`保留父来源和转换版本。T目标协议有效、R实际实现/replay、H保真Human时机、D假设性重表达独立标记；稳定重表达可基于明确假设生成探索性D监督，保留丢弃/推断字段和验证状态；不必先证明全轨迹等价才允许研究。若宣称真实目标轨迹可实现或Human时机保真，则需相应证据；不能将queued牌execution S直接冒充原Human H。

| 用途 | 必需证据/准入 | 缺失处理 |
| --- | --- | --- |
| 局部N | 对应公开输入、C、确切chosen关系、来源与曝光条件 | 可保留局部样本，不称完整历史 |
| sequence-N | 声明起点/reset之后完整目标消费前缀＋局部N | gap切断；中途加入标truncated-origin并独立条件，不能补此前W |
| Z-fact/latent | 已执行分支及目标定义要求的有资格后继/字段/teacher | 缺Z只mask Z；不复制给未执行候选 |
| O | 各run/combat终点、计数完整性、horizon/censor及后续actor | 中断不填输、胜利不填失败层0、缺次数不填0 |
| RL | 算法要求的行为来源/概率、时间区间、奖励、终止/截断、next-input与状态连续 | 无相应证据不能改名replay；算法需求分别判断 |

每次投影必须报告原始事件/输入数、完整性合格数、各用途保留/拒绝数、独立run/combat/source组、拒绝原因、假设和未覆盖机制。没有统一valid字段代替全部资格；允许0条符合某一新用途。

先以原始recording/run、相同初始存档或共同来源、重复导出/视图、actor分段及声明seed政策形成泄漏group，再固定train/dev/sealed-test。同seed不必假称物理同局，但其配对/共享起点信息不能跨split逃逸。词表/数值尺度只在train拟合；Gold、用途与撤销沿完整lineage保留。新采集数量需要实施后盘点，G1不承诺旧语料可用条数。

## C10 首个可实现Agent recipe

建议主reference为**AG01 / D-Simple LightActionM2内核 / scratch / sequence-N / K1**，固定TimingPolicy；称新的A recipe，不称旧Public M2续训。默认内核宽384，encoder使用该LightAction路径中受manifest固定的实现；K8与独立Reset-K1/Reset-K8是后续对照，不能把旧width48 ExperimentalDSimple checkpoint与LightAction混装。G2可用小资源验证但不得悄悄换图还叫同recipe；超预算就发布显式缩小诊断recipe。

typed公共对象经确定性展开renderer变成页面/事件、previous actual input、public feedback及候选文本；词表train-only拟合，模型特征白名单排除控制凭据、私有witness、当前N标签和未来目标。完整Agent程序接收控制信息但模型仅消费AgentInputSpec允许字段。首版不强制多模态/预训练、不训练时机网络。

每个已提供合格semantic occurrence按event ID更新W一次；重访保留，transport重传去重。模型消费view/public facts/已实际发生的own-input与公开结果；控制heartbeats、缓存页获取、纯诊断不写W。多个字段同一occurrence合成一次输入；无N事件调用advance，边界选择时全C只读score，不再为候选重复写W。新动作先预测再成为后续实际历史；native取消/unknown本身可作为真实后续公开信息，不改成成功。

边界谓词由环境提供有来源的当前事实：MainReady表示主局内owner可输入、Frame质量与C完整且无持牌/target/child或modal夺取输入；ChoiceReady表示当前held/target/selector/奖励等交互owner已可接受其当前C操作；TerminalReady表示终局/summary控件可输入。它们不承诺所有动画结束，也不以parent canonical successor或所有known-pending效果清空为条件；具体native owner/guard由E1 registry映射并验收。

TimingPolicy只在有依据的MainReady/ChoiceReady/TerminalReady调用chooser；其他位置Await，child-ready不要求parent完成。边界unknown不当ready；达到预算或缺required事件则放弃/停机。循环保护是明确任务预算与报告，不能SDK自动替选破环。首版可放弃部分提前输入机会，这是Agent策略限制而非A删除原生能力。

状态：新run/generation/reset实验、不可恢复gap、actor控制切换或图/renderer不兼容，结束旧segment并按明确新起点初始化；不静默承诺无缝恢复。推理state与cursor/模型/表示身份成对保存，或从合法前缀重建；旧request账本先核。same weights/renderer/输入的重放应一致到预声明数值容差。

训练建议复用**有界episode TBPTT机制**，不是直接复用任一旧trainer接口。无N事件advance、无N chunk不optimizer update；chunk detach不reset，跨参数更新carry是已声明近似。现有`dsimple_sequence_training.py`已有该机制；LightAction trainer每step N/feedback=None需改接。首版N为完整C listwise CE，按有效选择数归一；episode/来源采样规则写入run，不能让长局未声明地支配权重。dev按预先固定NLL及稳定性约束选择checkpoint，sealed-test不参与选择。

G1固定图/输入/状态/目标/选择方法；learning rate、batch、窗口/总token、训练seed及CPU/GPU预算由获批run manifest在训练前固定。取消/恢复需optimizer、RNG、数据cursor和输入/producer一致；改变图/数据/目标是新run初始化，不是透明resume。

## C11 N、Z、O、RL与其他消费者

N先闭环，Z/O资格抽取器可并行设计。N+Z只监督实际执行分支；view-Z与causal-Z分开，latent teacher来源及stop-gradient明确。O保留胜负、真实失败层、同战斗末HP、从当前分支起剩余主动出牌和可选end latent；损失权重不是效用权重。O初期辅助/分析，直接用于动作选择需新AgentSpec和闭环评价。

RL不是把N文件改名。TaskRewardSpec固定事件区间奖励、时间折扣、game/combat/task终止及truncation；gap不能随意bootstrap，行为概率包含时机/检索/规则覆盖的实际组合。先单机同步actor/learner小验证，再把policy lag、异步吞吐和state陈旧作为独立配置比较。

生成式可零项目训练或用Human/教师规范动作tokens微调，不造Human自然语言思考；内部token生成不是游戏步骤。检索可固定或学习，report recall/shortlist偏差，环境C不裁。规划分支不写真实W；视觉需真实图像及曝光同步；多人需actor范围。无训练规则Agent、离线分析器不强制训练端口或游戏控制。

[14类完整旅程AJ01–14](BASELINE_A_SYSTEM_JOURNEYS.zh-CN.md#4-十四种消费者的完整数据与运行旅程)、[N/Z/O完整合同](BASELINE_DATA_AGENT_CONTRACTS.zh-CN.md#6-nzo-的目标语义)、[16个蓝图实例](BASELINE_AGENT_PROTOCOL_BLUEPRINT.zh-CN.md)作为本版本组成部分，非省略的“以后设计”。G1要求全部消费者有数据/控制/扩展结论；只实施首个reference和必要非scorer/离线conformance消费者。

## C12 第二链的服务与产物

| 用例 | 输入 | 输出与完成判据 | 拒绝/恢复 |
| --- | --- | --- | --- |
| Import/Verify | 原件、来源声明、固定collector identity | 不可变bundle、验证报告；research admission另判 | 原件变化/typed验证失败拒绝 |
| PrepareData | source refs、Projection/Input/TargetSpec、用途/split | view/input/targets、排除与lineage报告 | 不满足目标可0样本；不自动另找更宽资格 |
| PrepareExperiment | 固定数据/Agent recipe、目的、预算、评价设计 | immutable run manifest、资源需求、use reservation | Gold/use/兼容/预算拒绝；尚未提交不计已运行 |
| Train/Analyze | manifest、可信worker/环境、可选同run checkpoint | events/checkpoints/terminal result、领域validator | provider结束不代表结果合格；unknown核原attempt |
| Package/Register | 验过的checkpoint、完整AgentSpec、dependencies | Agent artifact/兼容报告/注册记录 | 导出≠加载；权重缺失或不兼容拒绝 |
| Run/Evaluate | exact Host/Agent/task、split/场景/控制窗口 | AgentRun、独立评价、成本与失败分母 | 工程失败/接管/删失不改成模型输或成功 |

仍使用现有ArtifactStore/Manifest/Registry/RunReporter和用途owner；索引可重建，不能取代不可变事实。支持用户从已有数据/模型进入链路；不为“完整”强迫重录重训。每个用例可由库/CLI/API调用，GUI绑定同operation/artifact上下文。

## C13 第三链：资源、作业与权限

首版采用**显式选定目标资源＋兼容预检**，不要求智能通用调度。通用TaskRequest/Receipt属于项目共享应用层；现有compute seam仍引用stpd类型，E0/E5须抽取最小共享合同，由研究模块注册自己的validator，避免通用执行层反向依赖某个研究任务。`TaskRequest`固定workload种类、input refs、purpose/split/use refs、producer/worker/依赖、required capabilities、CPU/GPU/RAM/disk/time与费用上限、输出validator、cancel/resume合同。`ResourceOffer`声明资源/软件、数据可达性、信任域、Host支持/实际位置和价格来源；`PlacementDecision`记录谁选了哪一份offer及预检结果。未选资源或缺预算不能自动扩云。

首版准入可信、固定identity的工作负载：verify/project/prepare、N train、analysis/evaluate、package；Agent控制游戏另由本机Runtime许可。Z/O/RL等worker加入需独立配方验证，不让任意模型manifest成为shell脚本。单机重作业默认一个slot，多个轻量读取可并行；并发训练须资源测量与预算明确。

作业状态：prepared→submit-intent→submitted(handle)→running→terminal-observed→domain-verified→accepted/rejected。unknown submission独立挂起核对，cancel-requested不等于已停止；崩溃保留journal与原attempt。restart只恢复观察不自动重提任务，resume绑定原run/checkpoint/输入/producer。关闭UI不终止独立worker，游戏退出只中断绑定该实例的控制；云离线与本地自有可执行路径分开。

本地自有材料可按已有授权离线工作。共享材料默认：每次新用途准入、提交、resume、下载/发布重新检查当前权限；不能联网核准则暂停该新共享操作。已准入运行作业可按原授权的有限窗口完成，owner明确撤销则请求停止并记录实际确认；完成不豁免输出检查。若确需离线共享执行，必须预发有期限/用途/材料范围的许可，不把普通缓存当许可。

撤销共享后：原件/派生view禁止新下载和新用途；新训练/resume/导出/发布阻止；运行中按撤销策略请求停止；checkpoint/模型/报告保留不可变审计并标限制，通过lineage检查后决定后续可用性。不能远程擦除已下载字节或“撤销已学知识”。归档仅个人列表操作。首版采取上述保守默认，用户批准时可针对业务调整，但必须同时更新各类派生物与离线承诺。

## C14 分发、产品与变更管理

DistributionManifest固定Mod/Host/Connector/Runtime/Evidence/Workbench/Agent及依赖、游戏/平台、数据schema迁移和rollback组合。下载校验→准备→安全边界启用→实际加载核对；游戏内不热换DLL。升级保留原件、队列、用途账、旧可用包；数据库/不可逆迁移须独立备份与回退验证，不能“回退代码”冒充数据恢复。

首版复用原生轻量控制/状态入口＋现有同对象Workbench服务；不新增未验证WebView运行时。核心正常采集/数据/训练/分析/模型流程必须游戏内可发现、可执行、可监测并返回结果；登录/系统文件选择/复杂表格可同上下文外部视图。不能让用户抄ID/开终端完成正常流程；开发CLI仍是必要一等入口。

Stage1a旧A–H产品义务及研究目标按路线中的逐项处置表保留或明确替代；在用户接受前旧义务不因新文件日期更晚而取消。每次改游戏版本/信息profile/Agent图/数据目标/Host时间条件，分别重验受影响链，不把全部历史结果作废或全部沿用。组件source identity仍按原治理，组件源码集成用normal merge，不改BOM规则迁就squash。

## C15 全场景和消费者验收追踪

L01–64操作清单、SX-01–21场景卡、SC-01–16需求场景均纳入本候选。E1/E2/E3实施共同维护每项：`范围/条件、native producer seam、公开字段/动作、capture/input/parent/终点、source/test/build/runtime/Human/data资格、覆盖证据与失败`。条件不触发写not-observed，实际不适用需明确原生依据；未实现写unsupported并留分母。不能用多次普通出牌替代罕见selector或内置Hook。

| 组 | 必须保留的差异 | 验证责任 |
| --- | --- | --- |
| 战斗/目标/药水 | 动态费用与focus、confirm/cancel、输入在途、late cancellation | E1 native输入与E2时序；E3实际策略 |
| 信息/预览 | 完整逻辑列表、升级切换、公开提示、进入退出/重访 | E1曝光/C；E2历史；E3记忆消费 |
| 选择器 | 各原生有效min/max、自动完成/空选、选择替换、父pending | E1机制；E2父子/pile；E3 child-ready |
| 奖励/地图/事件/商店/营火/宝箱 | linked与independent、return与放弃、reroll/reveal、补货和多次机会 | 场景canary＋完整连续任务 |
| 终局/管理 | run/combat/task区别、summary退出、实例/控制/恢复 | E1/E3/E5/E6组合 |
| 数据/产品SC11–16 | 来源/use/split、训练/导出/加载、队列/中断/权限/发行 | E2/E4/E5/E6共同实际旅程 |

最低反例沿用UT01–15及既有蓝图/综合Await参考。G1只确认合同与计划可检查；实现测试、真实数据和连续任务结果由G2/V1/G3提供。测试不能只验证同一个错误假设；关键native机制要有原生或actual runtime依据。
