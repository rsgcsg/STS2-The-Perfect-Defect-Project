# Agent—协议完整蓝图：统一事件接口、可选边界接口与四种 Agent 原型

最新完整收敛见[G1审阅总册](BASELINE_G1_REVIEW_PACKET.zh-CN.md)、[待批准合同](BASELINE_G1_CONTRACTS.zh-CN.md)及[分阶段路线](BASELINE_G1_EXECUTION_ROADMAP.zh-CN.md)。本文件的详细案例/证据纳入G1候选；冲突按总册优先级解释，G1仍待用户阅读理解后明确批准。

日期：2026-10-07。版本：0.1。状态：P3/P4/P5设计交付候选＋有界合成参考程序；G1未接受，未实现生产协议、训练或操作游戏。当前源码核对基点bfd9cf441b5ae149e175939d00a06c0d298c4b12。

**最新澄清：E1描述封装Agent可以使用的交互能力，不要求单一内部模型同时学会信息取得、记忆、选动作和等待。** 稳定等待可以属于Agent的固定策略、学习模块或联合模型；若稳定化改变Agent外部可见的信息与操作时机，才属于独立协议视图。用户随后已选择A主方向；最新[三链联合评估与14类完整旅程](BASELINE_A_SYSTEM_JOURNEYS.zh-CN.md)补充数据、Host、工程压力和G1待决项。本文件保留详细Act/Await和参考程序；此前综合文档保留捕获、映射和Await的依据。

## 1. 对“B1是不是E1的稳定实现”的准确回答

| 实际改变 | 归属 | 如何命名/比较 |
| --- | --- | --- |
| 环境仍向完整Agent提供同一E事件与能力；Agent内部选择等边界再调用评分器 | Agent实现/调度/模型输入策略 | **B-style Agent on E**；同一协议，不同AgentSpec |
| Agent内部只让主模型消费边界输入，但其他组件仍接收/维护中间信息 | Agent组件图与representation | 整个Agent的信息能力仍按实际接收/使用声明，不能只报主模型看到什么 |
| 环境适配层在Agent之前扣住中间观察、限制提交到边界 | Agent-facing协议视图 | **B-view on E**；底层共享，外部契约不同，需新profile与投影 |
| Host真的暂停/步进游戏等Agent | Host执行条件与协议时序 | 另有身份和资格，不是内部等待策略 |

因此B-style Agent可以看作E允许策略中的一种受限实现。B-view也可以在E之上实现，但它实现的是B契约，不能仅凭共用底层就叫“与E完全相同”。若要证明某个B-style Agent与某个B-view+Agent等价，需要匹配输入筛选、历史消费、边界、时间/成本和行动，不由名字自动成立。

模型性/联合训练是可比较的Agent设计维度，不是Agent定义的必要条件。规则调度＋M2、两个模型协作、单个生成模型都可合法；各组件身份、训练目标和实际效果明确，最终评价完整Agent。

命名保持可追溯：**LN-E1保留为事件Agent-facing合同，LN-B1保留为可选B-view合同；Agent内部稳定调度称B-style/P1，不再借用一个协议名。** 旧B1候选因此仍可实现，但不再要求所有稳定Agent都面对第二种协议。

## 2. 两个完整方案与推荐

### 方案A：一个统一事件协议，时机策略放在Agent内（用户已选主方向）

```text
原生STS2 ← 唯一原生输入执行/规则/队列
    ↕ typed事实、当前原生能力、生命周期
Connector/Host：公开Frame＋完整操作关系＋Act/Await服务
    ↕ 同一个E合同
封装Agent：接收与历史 → 信息/时机策略 → 模型或程序 → 一个输出
    ↕
Runtime：权限、预算、Stop、未知核对；不代选策略
```

原生允许当前输入时，E提供完整当前能力；效果仍在途并不统一禁止下一输入。Agent可以自己等稳定，也可以连续输入。整个Agent可以是固定与学习组件的组合。模型不必接全部原始封套，也不必每帧推理。

优点是环境只有一个主要Agent合同，不因换评分/生成/等待方式反复换接口；Human原件和Agent轨迹也有同一公共语义基底。成本在于事件、时机和状态管理责任必须显式在AgentSpec中落实，不能把一个裸scorer冒充完整Agent。

### 方案B：在相同E底层上增加稳定边界视图（可选）

```text
同一原生/Connector E能力
    ↕
B-view adapter：边界判定、输入投影、请求映射、结果聚合
    ↕ B合同（只在合格边界给决策输入）
封装Agent：可以是简单step式策略，也可以仍有记忆/多模型
```

B-view是明确的环境适配层，不偷偷藏在GUI或runner。它负责：选定MainReady/ChoiceReady/TerminalReady边界；提供target O/C；把B请求映射到原来的E观察、目录和动作；保留中间source记录、错误/取消和投影依据。**不能在提交旧B请求时改绑最新E状态。** 新的选择器必须作为ChoiceReady暴露，不能等parent完成才返回。

B-view可更容易服务固定step训练器、简单模型和某些重表达数据。代价是少了中间信息/早提交机会，数据转换和时间语义增加一层。新字段叫stable不构成原生fence证明，也不能把扣住事件当作没发生。

**推荐顺序：以方案A作为主要工程合同，P1稳定调度Agent作为首个D-M2 N参考消费者；为确实需要固定step外观的研究使用方案B。** 若当前某Agent输入合同只适合B，可以明确接B-view，不强迫它自己重造等待。不要默认建设两套游戏规则、Recorder、数据存储或模型分发系统。

## 3. 第一版服务范围与共享信息/动作语义

初次资格仍建议单人基础游戏Defect标准A0、首个局内选择到总结退出；这是待G1接受的范围，不是上层模型永久限制。范围内内置特殊事件/Hook不能通过删分母变成“完整”。多人、任意Mod和其他版本独立资格。管理侧负责启动/续局/保存恢复/任务配置，普通策略不获得重置逃避结果权限。

信息默认采用**当前公开逻辑页＋已实际公开提示/预览＋公开关系＋历史事件**。typed结构为基础，可读文本按对象展开；模型可再编码成多输入或张量。未知、未揭示、当前不可读、未支持分开。静态去重不改变实际曝光时点。C是当前有限的可提交关系，不是所有对象×所有动词，也不是下一阶段的潜在操作。

共享[L01–L64机制清单](BASELINE_LN_V1_SPEC.zh-CN.md)：详情/升级、目标focus/unfocus/confirm/cancel、药水提示与操作、Peek、完整逻辑列表、各类selector、奖励重访/替代、商店、事件、营火、宝箱、地图、跨幕、教程和终局。两方案不应因模型偏好而删合法候选；B-view改变的是明确的决策边界，不能宣称原生本来没有提前输入能力。

| 场景族 | 公共输入与操作 | 时机/抽象义务 |
| --- | --- | --- |
| 主战斗/药水 | 当前手牌、资源、敌人公开事实、槽位、完整当前原生操作 | 允许输入≠效果已完成；药水弹窗/target/cancel分开 |
| 持牌/目标预览 | 当前牌、焦点、原生显示费用/星费/附魔/正文、真实目标/确认/取消 | 不模拟未公开伤害结果；每次新焦点显示有来源 |
| 牌组/牌堆/地图/物品详情 | 已进入逻辑页完整内容、原生inspect/切升级/翻页/返回 | 不录每个滚轮；源有详情入口才开放；隐藏牌序不出接口 |
| 手牌/网格/生成牌/牌堆选择 | 完整原生候选、selected、有效控件、source pile | 逐次选/撤选；替换/cap/自动完成/empty/cancel不统一猜规则 |
| 升级/移除/变换/附魔/卡包 | 原对象、预览、确认、preview cancel和whole close | 预览不当实际升级；包不拆成任意单卡；child阻塞父也必须可决策 |
| 奖励 | 当前已揭示选项、独立/linked组、真实alternatives | return/reroll/消耗不同；重访是新occurrence |
| 商店/事件/营火/宝箱 | 当前真实目录/报价/选项、原生进入/退出及子流程 | 购后补货/新随机奖励不预读；营火不假设只能一次 |
| 自动推进/跨幕/结束 | 已公开变化、ready、终局与summary控制 | 没选择不造native Wait；game outcome与task complete不同 |
| 异常/接管 | gap、unsupported、stale、unknown、控制状态 | 不自动选第一项救流程；不把人工完成记为Agent独立成功 |

原生/source基础与缺口以已固定身份的[时序](BASELINE_LN_QUEUED_TIMING.zh-CN.md)、[捕获审查](BASELINE_PROTOCOL_V1_SYNTHESIS.zh-CN.md)和[学习审查](BASELINE_LN_LEARNING_INTEGRATION.zh-CN.md)为准。当前H是seam-specific投影，非稳定完整GUI；当前Human text只白名单五种输入，不能假称上述全部已有示范。

## 4. 面对完整Agent的协议合同

这些是建议语义及SDK职责，不强制五次网络调用或五个模型调用。小目录直接inline，大目录可分页；同进程可以是普通函数。

| 能力 | 输入/输出 | 静态/动态/效果 |
| --- | --- | --- |
| Describe/Attach | 协议/IP/操作/事件/Host能力、表示/质量要求、任务控制身份、历史恢复能力 | 版本定义较稳定；实例和权限动态；不改变玩法 |
| Observe/Events | 当前或有序的新公开Frame、结果和事件；带capture范围与独立提供位置 | 来自动态世界的不可变记录；不主动打开UI、不冻结世界 |
| Catalog List/AllowedNext | 某个sealed C的完整列表/分页或条件延伸 | 查询已有当前能力；不是获取未打开页面资料，不是新的游戏步骤 |
| Resolve | 严格结构表达→当前C唯一成员 | 无native效果；no_match/ambiguous/stale不猜替代 |
| Act/SubmitOne | 当前依据、request ID、唯一bound handle→输入回执 | 原子输入写入串行；native队列可多项已知在途；执行时复验 |
| Await | 基于已知游标/时钟的公开事件或状态条件、期限→wake/timeout/abort/gap | 调度操作；不计native gameplay action，不帮Agent决定目标 |
| QueryRequestResult | 原request的已有或更新回执 | 核对不是重试；未知投递不得换ID再发 |
| Lifecycle/control | initialize/consume/cancel/close、可选state export/import、控制权限 | 不把租约/私有witness塞进模型；Stop不保证撤回原生已入队动作 |

### 4.1 输入内容与格式

```yaml
protocol: {family: ln-event, version: proposed-v1}
binding: {environment_generation: g3, observation_ref: o24, event_cursor: e80,
          catalog_ref: C24, clock_ref: q24}
quality:
  scope: current_public_view
  capture_seam: declared_native_seam
  required_fields_status: complete_for_declared_scope
  visual_equivalence: not_claimed
public:
  page: combat
  phase: player_input
  input_ready: true
  boundary: {kind: none, status: not_at_declared_boundary}
  objects: 当前公开卡牌/敌人/资源等对象与原生文字
  relations: 当前公开选择/焦点/归属等关系
  own_requests: [{ref: rA, input_delivery: delivered, public_outcome: pending}]
  newly_supplied_events: [真实新公开事件引用]
catalog:
  complete: true
  count: 当前数量
  groups: 当前操作族及数量
  inline_actions: 可选；小目录直接附带
```

这是外部Agent数据对象，不是必须原样拼进模型prompt。control/binding由Agent客户端保存；模型只用已声明公共信息、局部别名与必要关系。boundary标志只报告所选原生边界检测合同，不能偷偷成为“现在策略上该出牌”的建议。若检测未知就标unknown；P1不能把unknown当稳定。

整Agent可接收所有事件，某个模型只处理其中一部分；必须有固定、可重放的Agent input policy。模型没看到某段数据，不等于整个Agent没使用。typed输入、文本renderer、任何压缩/摘要或模块通信都属于版本化AgentSpec。

### 4.2 输出及当前实现差距

```text
Act(current_action_ref 或待Resolve的严格结构)
Await(basis, triggers, deadline_after_basis_ms)
Abstain(reason)
```

scores、检索结果、生成token和解释为可选诊断，不是所有Agent输出的强制字段。现有Runtime强制scores长度等于完整目录，这是真实旧端口限制；新方案需有版本的decision port/admission，不给未评分动作填假分数。

完整合法目录是环境权威；Agent内部可以检索、分解或不逐项评分。若只打分shortlist，它是该Agent的近似算法，不把环境C改成shortlist。Resolve及Submit最终仍核C成员、native条件、状态和权限。

### 4.3 Await精确语义继承与职责

沿用[总方案的Await规则](BASELINE_PROTOCOL_V1_SYNTHESIS.zh-CN.md)：绑定observation/cursor/clock；next_event为边沿、until_state为当前公共条件；OR匹配；deadline相对原观察发布位置，含计算/传输已消耗时间；注册栅栏内先查缓冲及当前条件；gap不当安静；event/timeout/control只有一个确定终态。Stop/撤权优先于尚未封存的wake。以前满足不等于现在仍满足，返回真实最新观察。

Agent可以由固定模块、轻量时机网络、生成模型或联合策略选Await。协议只落实，不要求每个内部模型都预测等待时间。纯timer唤醒、事件递归状态和连续内部状态演化按AgentSpec定义；不能用重复poll伪造native事件或Human Wait标签。

## 5. 两方案下的完整运行状态

### 5.1 共同Agent与Runtime状态机

| 状态 | 持有内容/允许工作 | 下一状态与拒绝边界 |
| --- | --- | --- |
| Detached | 无控制；检查产物/合同 | Attach失败停止；成功进入Observing |
| Observing | 接收有序公共事件、当前O/C和质量 | gap/代次变进入Suspended；足够输入进入Choosing或Awaiting |
| Choosing | 一个版本的Agent状态和输入；内部评分/生成/检索 | 产生Act/Await/Abstain；新事件有序进入buffer，不并发乱写同一个W |
| Awaiting | 模型/程序选择的等待合同，事件和控制继续维护 | 按实际wake/timeout/abort/gap返回Observing或Suspended |
| Submitting | 唯一request、旧输入依据、native复验 | not_delivered明确回观察；delivered记录pending结果；unknown进入Reconciling |
| Active-with-pending | 已知接受输入可能仍有native效果在途 | 当前native允许时可再选择/输入；不是全局busy锁 |
| Reconciling | 不知道某输入有没有投递；只核原request/owner状态 | 新确切证据/授权恢复；禁止重复或猜测提交 |
| Suspended/Stopped | 撤权、预算、gap、版本不符等 | 保留原始结果与已入队动作来源；显式新segment或有证恢复 |
| Complete | game结果及任务要求的summary退出完成 | 冻结任务状态；新局新generation/reset |

原生世界自己的main/held/target/selector/info/reward等状态与上表不同：`Awaiting`是Agent调度状态，不表示游戏停了；`delivered`不表示效果完成；game outcome不表示summary任务完成。

### 5.2 B-style Agent on E的附加规则

TimingPolicy在Agent内读取已公开boundary状态：MainReady/ChoiceReady/TerminalReady才调用相应选择组件；其他时候选择Await。接收事件的history policy独立声明：P1推荐**保留全部合格公共语义事件并消费到记忆，昂贵动作评分只在边界调用**。若选择仅边界更新模型，产生不同representation/AgentSpec和训练前缀，不能把两者混用。

选择器ChoiceReady不要求parent结束。事件流中的短暂资料即使不触发动作评分，也可以进入Agent记忆；这里存在真实编码/W更新成本，不能把“没调用scorer”称免费。

### 5.3 B-view on E的附加规则

```text
E观察/事件 → 记录source receipt
   ├─ 非目标边界：保留source、等待，不向外部Agent提供这份决策输入
   ├─ 边界未知/错误：显式失败或要求支持，不等到超时装稳定
   └─ 目标边界：发布新的B occurrence、O/C及来源映射
B请求 → 查原source mapping → E原绑定复验/提交
```

B-facing Await只使用B事件游标和已声明的B公共谓词；adapter内部才使用E游标等待边界，不能让B Agent请求被隐藏的E事件作为无说明的信息旁路。时钟沿统一Host单调时基记录，source capture与B实际提供位置分别保留；固定step消费者可用SDK把Act回执＋等待下一B边界组合成step，但不把回执叫效果完成。

B occurrence序号与E source序号分开；省略的E事件仍在source/projection报告。B adapter不得自动打开资料、替选child或把原生未知改成功。只用一条控制授权链，B adapter不是第二个游戏写入者。旧B请求绝不绑定后来的E目录。

B可能导致更多时延或不同的信息可用历史；信息/时机改变属于协议身份。只有显式projection和对应证据才可比较或复用训练数据。

## 6. 四种Agent设计原型

为避免与正式任务P1–P4混淆，下列旧原型P1/P2/P3/P4在新联合评估中分别称AG01/AG02/AG03/AG04。旧参考脚本和回执保留原编号及身份；这是名称消歧，不是新任务编号。

下列原型都可以对接方案A；P1/P3等也可经独立表示/输入验证接方案B。每个完整Agent都是一个对外个体，并非把“模型”与协议等同。

### P1 Boundary-M2：稳定调度模块＋有状态评分器

```text
E客户端 → 按序公共历史/请求账本 → 输入编码/持续W
                   ↘ 固定TimingPolicy(boundary)
                      ├ Await
                      └ 完整C → M2候选只读评分 → Act
```

- 复用意向：LightActionM2页面编码/固定槽W/候选只读结构，不复用未验证的新L-N数据资格；typed编码或全事件输入需要新adapter。
- 时机能力由Agent内固定模块持有，M2不必学习它。规则边界不要求原生所有效果都完成，child-ready可立即决定。
- 数据：实际协议轨迹→P1 input trace；合格无N事件仍推进state；N只在模型负责的选择位置训练。固定Await/返回规则不冒充Human学到的决策。
- 训练：序列N先行；明确reset/carry/TBPTT/输入表示，后续Z/O独立资格。旧Public M2的prior=None/feedback=None等限制不自动改掉。
- 评价：模型选择质量与整个Agent的等待成本、遗漏机会分别报；输入同一时比较carry/reset，不能把固定timing当模型能力。
- 适用：首个可解释的工程与学习基线。限制：放弃部分提前输入，候选全评分仍有成本。

### P2 Modular-Timing：独立时机组件＋动作组件

```text
E公共事件 → 共享或独立状态编码
             ├ Timing component → Await / invoke decision
             └ Action component → scorer或generator → Act
```

- Timing可以是规则、小网络或模型；它的职责是信息/时机策略，不由Runtime猜。两个组件可共享参数、分阶段训练或联合训练，均写入AgentSpec。
- 可以在确定信息足够时连出，在需要等公开结果时Await；Action模型不必同时学“何时醒”。
- Timing训练：来自满足可操作性/时间覆盖条件的派生时间标签、显式标注或Agent RL；没有这些时明确为规则/未训练组件。Human沉默不自动是理性Wait。
- Action训练：同P1或生成式N；训练teacher forcing中的当前动作只进loss。只有实际发生且已可用的请求/反馈进入后续历史。
- 评价：固定Action组件比较Timing版本；再评完整Agent。两模块的推理、缓存、数据先验、错误覆盖和行为概率都计入。
- 风险：模块之间目标不一致、旧状态交接、时机网络过早唤醒/长期不醒。由组件合同和整体任务评价定位，不要求把两者强塞进一个网络。

### P3 Generative-Event：结构化生成动作与可选等待

```text
E公共对象/文字/关系/历史/时间
  → decoder或生成模块（可有独立记忆/Timing组件）
  → Act结构 或 Await结构
  → 当前C约束/唯一Resolve → 提交
```

- 模型输出的是有限原生操作结构和有界调度结构，不执行自由文本。操作参数使用当前公共refs。
- 可以是一体模型，也可以只生成动作、由另一个Agent组件持有等待能力；E协议不规定内部形状。
- 模仿数据：Human真实语义选择→规范化动作token。不是Human写过JSON，也不是Human调用过AllowedNext或给过CoT。
- 时机生成：有资格时可以学习`p(action, next_time | history)`或Await条件；未来可研究连续时间事件生成，首版不强制神经积分器。
- Runtime：生成中有新事件时按版本化输入/计划状态处理；未提交计划可取消，已投递未知不能重发。长动作计划不授权跨未来choice自动执行。
- 评价：结构合法率、唯一绑定率、动作质量、等待/过期、生成token与延迟；不能只看JSON正确率。

### P4 Retrieval-Rerank：大目录提案、重排与可插拔时机

```text
公开O/历史 + 完整C的访问能力
  → proposal/retrieval模块 → k个候选
  → 轻量或深度ranker/规划模块
  → 唯一当前动作
Timing由同一Agent中的独立或共享模块持有
```

- 环境C不剪裁；shortlist是Agent算法。大量候选可避免昂贵逐项推理，但Host构建、索引和轻量扫描仍可能O(N)。
- Human选择可训练提案/重排；漏示范动作须报recall。训练时注入目标要明示，在线不能知道目标。
- 数据与输出：可用文本或typed对象；相同body生成的多候选只是内部假设，不改变W或原生状态。
- 评价：k、提案召回、ranker质量、总成本、任务成功；与小规模全量reference比较，不能宣称一定等同全局argmax。
- 风险：候选索引过期、未评分动作被误认为非法、世界模型预测被当成真实后果。

### 共同AgentSpec与恢复合同

每个AgentSpec至少绑定：protocol/profile、representation、组件图与参数/规则版本、输入/历史消费、TimingPolicy、选择/检索、状态格式、资源上界、训练来源和用途、支持范围。Runtime保存输入游标与Agent状态的对应关系；恢复时匹配generation、组件版本、公开前缀与控制条件。无证连续性则新segment，不用相同画面恢复旧W。

模型可导出状态或参与调试实验，但不意味环境可以私改它的策略。联合训练/整体评价的程度作为实验维度，而非把非端到端Agent排除在协议之外。

## 7. 完整交互例子

### BP-01 同一E页面，两个Agent做不同调度

输入：A已经明确提交、效果pending；当前combat允许开始B；`boundary=not_ready`。

- P1接收并记录输入，TimingPolicy返回Await边界；环境仍保留B的合法操作。
- P2的Timing组件认为当前事实足够，Action组件选择Begin B；原生仍逐次复验。
- 二者使用**同一个E协议**。差别是Agent，不能把P1等了说成E禁止连出。

### BP-02 同一底层轨迹，B-view改变外部经历

底层E发布e1主边界、e2中间公开变化、e3下个边界。P1-on-E可接收e1/e2/e3，B-view外部Agent只收到b1←e1、b2←e3。即使最终Act相同，历史不自动相同。若要对照，必须让P1的模型输入投影与B-view一致，或承认信息/历史改变；不能仅因只在e1/e3打分就说等价。

### BP-03 子选择阻塞父牌

父效果pending，但discard selector成为ChoiceReady。P1与B-view都应提供该child决策；P2/P3也可处理。等parent fully finished会死锁。原生child候选/已选/确认/取消按真实机制，不由Timing组件重算。

### BP-04 目标预览与生成期间状态变化

Begin c1→Focus e1→真实渲染值→可Focus e2/确认/取消。P3正在生成confirm(e1)时目标消失：旧绑定拒绝；Agent用新事件再决定。不能把旧表达“最接近”到e2，也不能把Native preview当最终损伤模拟。两套方案只在各自曝光边界提供已发生内容。

### BP-05 升级详情的固定程序与模型选择

P1可用内部程序在已决定查看后做原生打开/切换/返回；P3也可逐次生成这些动作。前者没有每次scorer调用，但整个Agent确实使用了预览信息和规则程序。模型样本与Human label分开；没有Human查看证据不伪造。

### BP-06 奖励回访与循环

open→看牌→return→再次open，正文相同但occurrence不同。事件/输入正确而仍循环属于Agent策略；丢掉重访或错误reset属于输入/状态实现；原生返回缺失属于Host；Runtime到预算停止不代选下一动作。不自动把所有返回标负奖励。

### BP-07 动态描述、卡牌同名与大列表

两张同名牌有不同实例/当前费用；typed输入/文本都须保留。目录分页绑定同一个C，不增加gameplay步。P4检索只精排k项，剩余仍合法可访问；P3可严格生成当前ref。超限不能偷偷截掉必需候选。

### BP-08 原生拒绝、输入未知、后果pending

已知未投递的stale→新观察后可由Agent重决策；投递unknown→Reconciling，不自动重试；已知投递但效果pending→允许按当前native-ready继续。任何Agent都不能通过换模型绕过这些共同边界。

### BP-09 Await注册之前事件已发生

模型以e10为basis生成等待；e11在生成期间已发布。注册时回放e10之后缓冲，不能只听注册后事件。next_event与until_state分开；匹配过的旧状态不保证现在仍真，Act仍用最新绑定。

### BP-10 Stop和队列中的旧动作

停止新提交、取消未投递生成/等待；已原生接受的A/B可能继续或被native取消。actor仍属于原先Agent。B-view不能为了返回一个漂亮done清空队列，P1也不能把原生后续自动效果标Human。

### BP-11 商店移除完整过程

进入商店→查看商品/移除服务→选牌→preview→取消preview换牌或confirm→实际返回后扣费/移除→新库存/房间观察。P1按每个ChoiceReady决策；P3可生成相同语义操作；B-view不可把选择和扣费压成无取消机会的一步，除非另立OP变体。

### BP-12 营火/宝箱/事件的动态后续

营火一次行动后可能仍有选项；宝箱可能先出现其他reward；事件战斗结束可能回父事件。协议由原生当前owner生成目录。任何P1规则“做完一个就proceed”都属于Agent策略，不能藏在Host流程里或冒称所有游戏规则。

### BP-13 Human快速出A/B转成边界数据

H_B不是S_B；S_B中已排队B的UI holder还可能不同。B-exact只取原本对齐边界；B-derived需队列/手牌归一化及适用假设。若只有最终play标签，没有分步输入，只能用于相应粒度监督或有证派生，不能自动成为P1整个事件前缀。

### BP-14 一段Human数据给四种Agent

同一合格ProtocolTrace可以派生P1的scorer标签、P3的规范化JSON、P4提案/重排target；P2时机目标另需空闲区间/可操作性/censor。共享原件不代表四个训练输入完全相同。保持转换身份、来源和split，不捏造Human内部计算。

### BP-15 静态定义更新与重连

游戏/encoder/参数或信息policy变化时，缓存和状态按身份失效。单纯运输重传去重；实际同页重访是新经历。B facade既要保留source cursor，也要保留target occurrence；恢复不把旧B请求重绑到新E实例。

### BP-16 自动事件但没有可选动作

公开变化可以消费到Agent状态，没有Human choice就无N标签。没有native输入能力时Agent可以Await；catalog空不等于终局。task complete需要声明的终局/总结条件，不能用“没有动作”推断。

## 8. 从真人记录到协议、Agent输入和训练

必须分四层，而不只“原始记录→模型文本”：

```text
RawEvidence（原生事实及捕获质量）
 → ProtocolTrace（E或B，R实现/Π投影和T/R/H/D资格）
 → AgentInputTrace（该Agent各组件实际消费/提供/丢弃规则）
 → ModelTrainingInput（张量、标签、masks、reset、目标）
```

- RawEvidence不记录Human心理。H只是具体seam的契约投影，不保证稳定完整GUI；actor、native binding、显示/模型来源、输入/执行/append时钟分开。
- E trace按目标E需要的公开事件/输入顺序投影，不必每帧保真，但短暂必要信息丢失须gap。
- B trace可来自原本稳定边界（B-exact），或带验证假设的重表达（B-derived）。T目标轨迹、R实际replay、H保真Human时机、D重表达监督分别声明。
- AgentInputTrace是新增强调的一层：P1接收全部E但只在边界打分，和只给模型边界输入，不是同一训练前缀；B-view也有自己的提供语义。固定程序行为不伪称Human策略标签。
- ModelTrainingInput可以有无N事件仍更新state；当前label在当前预测之后才可作为后续实际历史；Z/O、结果和teacher不能混进当前策略输入。

当前Human text五verb白名单不覆盖全部浏览、升级切换、target focus和selector经历。当前canonical更广，但不能按相邻行拼补。因此数据盘点要按来源/机制/目标Agent input policy给资格和分母，不以“旧数据能读”或“API能操作”宣称可训练完整Agent。

| 学习对象 | 推荐初始数据/目标 | 限制和评价 |
| --- | --- | --- |
| P1动作模型 | 合格顺序输入，边界处listwise N；固定Timing不算学习目标 | 原生事件prefix与线上consume一致；只会确认不代表会选目标 |
| P2时机/动作模块 | N＋有资格的时间/等待目标，或之后在线RL；可分阶段联合优化 | Human沉默须区分不能操作/观察缺失；模块组合分布偏移另评 |
| P3生成模块 | Human语义选择派生规范化操作tokens；Await有独立标签资格 | 生成格式不是Human原始语言/CoT；输出概率须对应实际约束 |
| P4检索/重排 | 相同合法关系与选择来源，提案召回与rank目标 | shortlist bias、目标注入、未评分动作的误差分开报告 |
| RL | Agent真实rollout、奖励/终止/截断、行为policy与算法所需概率、历史 | Agent来源不当Human；延迟/规则覆盖/检索改变行为分布；O预测不自动是RL |

训练数据、模型状态恢复和原生游戏恢复是三个合同。BC小loss、成功导出、某次动作投递不能替代实战资格。相同run/共享来源按组划分；不同视图不能把同一局分别放train/test。

## 9. 训练、评估与运行的一条完整旅程

1. **定义任务与AgentSpec**：E或B、范围、输入、组件、时机策略、预算、结束/接管；未接受参数不默认继承旧模型。
2. **准备原件与投影**：Evidence验完整性；数据owner选用途/split；报告T/R/H/D及每个history/choice/successor/outcome mask。
3. **编译Agent输入**：同一renderer、事件消费和固定程序合同在线离线复用；fit仅train；目标/未来信息在独立target通道。
4. **小规模训练与一致性**：序列N及相应组件目标；resume绑定配置、参数、optimizer、游标、RNG和carry；窗口detach不等于新局。
5. **封装与加载**：ModelArtifact＋代码/规则/表示/状态策略形成Agent；端口能力匹配，不能把旧scores-only端口当通用Act/Await已实现。
6. **运行**：Runtime授予窗口与预算，Agent按约定接收数据/选输出；环境native复验；已知/未知/接管保留；工作台只映射用例，不私自预取或代选。
7. **评价**：绑定整Agent、协议、数据、起点、资源/延迟和干预；输出任务结果、各种失败与成本；模型准确率只是一项。
8. **改进**：先定位Host缺能力、capture不完整、projection假设失效、Agent状态bug、策略弱还是资源瓶颈；修对应owner，再新身份迭代。

本地/云/团队部署复用现有artifact/use/job/权限体系；位置不同不改变数据资格和Agent语义。网络/推理延迟属于实际运行条件，不能假称与本地同速。没有授权不启动真实训练/云任务。

## 10. 怎样比较才有意义

| 比较 | 固定条件 | 回答的问题 |
| --- | --- | --- |
| E上P1 vs P2 | 相同公开语义、Action组件/数据、任务范围 | 时机策略是否改善整Agent；同时报额外算力/时间标签来源 |
| P1-on-E vs B-view+P1 | 先匹配模型可用历史/边界/时钟/成本；不匹配就明确两个信息条件 | 调度放置与视图投影的影响，不默认等价 |
| P1全评分 vs P4提案/重排 | 完整C、相同任务与预算、共享训练来源；小规模用全量reference | recall、质量、算力 tradeoff，不要求只有速度 |
| P1文本 vs typed结构化 | 输入事实/曝光时机相同；编码/图不同明确计资源 | 表示可学习性/成本，不把更多信息当编码优势 |
| P3动作生成 vs全评分 | 相同合法机会、公开事实和任务；参数/训练/推理预算披露 | 不同选择算法的完整Agent表现 |
| E/B的数据迁移 | 对相同原件分别统计转换成功/拒绝/假设、闭环效果 | 哪种视图有用，不把转换数量最大当协议最好 |

指标至少包含：场景/连续任务完成与未知/删失/接管；输入/目录/展示正确性；浏览/循环/等待/连出；late cancellation与stale；Human可用choice/prefix/独立run数量；索引/捕获/传输/编码/W/模型/提交/落盘成本；重连/恢复。不要用“所有动作投递成功”代替游戏能力，也不要自动给所有返回/等待负奖励。

## 11. 本轮可执行参考程序：验证了什么

工具：[ln-agent-protocol-prototype.mjs](../../tools/ln-agent-protocol-prototype.mjs)。执行：

```sh
node tools/ln-agent-protocol-prototype.mjs
node tools/ln-agent-protocol-prototype.mjs --json
```

保存回执：[BASELINE_LN_AGENT_REFERENCE_2026-10-07.json](../evidence/BASELINE_LN_AGENT_REFERENCE_2026-10-07.json)。程序只读自身源码计算身份，使用Node标准库、合成Frame与注入的脚本模型；没有游戏、真实数据、权重、网络、子进程或真实timer。

参考实现包括：共享synthetic Env接口、四种Agent组合、完整公共记忆去重、B-view的source binding映射、Await对给定事件历史的求值。输入调用只产生预置回执；后续Frame由fixture注入，不模拟出牌伤害或整个游戏。它不是native simulator，不替现有合法性；模型函数是test doubles，不是已训练M2/生成器/RL。B facade参考仅覆盖Frame筛选与目录/提交映射，没有实现生产B/E Await桥、并发broker、native fence或恢复存储。

| 验证类别 | 范围 | 不能推出 |
| --- | --- | --- |
| Agent composition | 四种输出路径、boundary策略与E并存、empty catalog、child、B facade输入到source提交、不同曝光历史 | 四个真实模型有效或框架已经生产化 |
| Binding reference | stale、重复、unknown、Stop、非法组合、歧义、B旧视图不重绑 | 生产Runtime/native正确或完整安全隔离 |
| History reference | 重传与真实重访、gap/generation | 当前Human数据拥有完整历史 |
| Await reference | 注册前事件、edge/level、deadline、control、retention/past match | 多线程broker、原生实时timer或网络并发已经通过 |
| Scale reference | 10000合成动作仍完整可访问，reference Resolve可不产生10000个model scores | 次线性系统、真实推理延迟、STS2常有10k动作或生成质量 |

条目计数与script SHA以回执为准；不得把所有checks按同一强度叫“生产测试”。这里不测性能，不模拟伤害/资源/规则，不资格化边界检测、H捕获或重表达数据。

## 12. 后续包、停止条件和审查

本轮完成后独立审查，先检查完整文档/参考程序/回执/源引用，再判断设计候选能否交接。G1仍由用户/指定owner接受，不因review通过自动实现。

| 现有任务 | 下一具体包 | 验收/停止条件 |
| --- | --- | --- |
| E1 | E Core端口、native目录/展示缺口、Act/事件/Await；B facade按需求追加 | 原生机制/完整集合/绑定与竞态实测；不为迁就模型删动作 |
| E2 | 面向目标协议的Human capture与ProtocolTrace/AgentInputTrace转换 | before/input/exposure/顺序、T/R/H/D、取消/gap；无依据不能补历史 |
| E3 | P1完整Agent接缝，随后P2/P3/P4接口实现 | 模型/程序身份、W消费、选择与时机、恢复、在线离线一致；旧端口不伪装新能力 |
| E4 | N小闭环、独立合格时机目标/Z/O/RL及评价 | 固定use/split/预算/真实任务；不把合成或工程过拟合当质量 |
| E5/E6 | 同用例的任务资源/产物和CLI/API/GUI/分发 | 单一truth、权限、停止和rollback；无独立GUI策略 |

优先只建设共享底座及一个真实消费者；可用参考P3式非scorer stub检验接口并非锁死scores，不必先训练所有模型。B-view仅当它服务真实消费者且数据投影明确时增加。G2需一条真实Human/Agent数据→小训练→产物→运行链，G3才冻结完整承诺范围。

## 13. 依据与不变的限制

- [P4封装Agent定义](BASELINE_DATA_AGENT_CONTRACTS.zh-CN.md#1-从完整-agent-出发而不只定义一个评分器)本已允许单模型/多模型/固定组件；本轮纠正上一版LN-E1把自主时机过度写成单模型必须拥有的能力。
- [当前Runtime AdapterDecision](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/bfd9cf441b5ae149e175939d00a06c0d298c4b12/components/policy-runtime/src/contracts.ts#L225)强制scores/selected_index，新的Agent端口尚未实现。
- [LightActionM2](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/bfd9cf441b5ae149e175939d00a06c0d298c4b12/python/stpd/models/light_action_m2.py#L153)提供candidate-independent writer/只读候选结构；新history/timing/typed编码不继承旧资格。
- H/source质量、queue admission/execution、ActiveHolders与logical Hand区别、五verbHuman链、假设化数据转换和Await竞态均沿用[上一轮综合审查](BASELINE_PROTOCOL_V1_SYNTHESIS.zh-CN.md)的固定源证据；本轮核对当前components/python与其生产源码无新增差异，没有新raw corpus统计。

本文件与参考程序是设计和合成可执行材料。没有声称原生全覆盖、四种已训练Agent、M2能力提升、10k真实性能、可用RL任务或Human数据全量可转换。
