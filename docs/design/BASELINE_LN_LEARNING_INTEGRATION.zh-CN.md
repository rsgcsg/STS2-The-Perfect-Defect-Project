# L-N 的 Human 学习闭环：Annotator、M2、生成模型与 RL

日期：2026-10-07。状态：P3/P4/P5 设计与只读源码审查；没有改生产代码、读取新原始 Human 数据、训练、游戏或部署。当前检查基点 50546a1ae359680cf6dcecbf9d25083254fdfa7e；另明确标记的 Public M2 候选只读引用 e30247defd859dfc75708fea4612c668e93cdbd2。不把任一源码身份当已加载模型、当前训练任务或数据资格。

本文纠正讨论重心：用户所说“记录”主要是 **Annotator 原生 Human 示范能否支持模型学习 L-N**，并非需要多少 API 调试日志。目标依[REQ-04–08](BASELINE_FOUNDATION.zh-CN.md)：封装 Agent、真实低干扰记录、可解释转换、训练/运行一致、可归因评价；首条优先链仍为 D-M2＋时序 N，之后再按资格加入 Z/O。RL、生成式、结构化输入等是保留的扩展能力，不因可表达就称已实现。

## 1. 必须补上的闭环，而非只让 Agent 能操作

```text
游戏当前原生公开表达
   ├─ Human实际输入 → Annotator被动见证
   │                    ↓
   │       输入前公开视图＋当前动作关系＋真实选择＋顺序/缺口
   │                    ↓
   │       typed验证 → L-N目标轨迹资格 → 数据用途/划分
   │                    ↓
   │       文本/结构化表示 → 有状态序列训练 → Agent产物
   │
   └─ Host/Connector → 同一L-N公开语义 → 封装Agent
                                             ↓
                  模型/程序选择 → 唯一绑定 → 原生执行
                                             ↓
                          Agent经历/结果（不是Human示范）
```

共享的是公共视图/动作含义与可检验映射，不是强迫 Human 调用 ListActions/Resolve。Annotator 不能为了补记录而替 Human 打开提示、切换预览或干预输入；Host 被动捕获也不能把当前原生没有展示的内部事实放进 L-N 输入。

**操作可用和Human示范可学是两个覆盖矩阵。** 前者证明 Agent 能够按协议使用；后者证明这个操作在真实 Human 路径上留下合格的输入/选择/历史。每个 L01–L64 承诺都要关联这两张矩阵。M2 学习闭环不能只验前者。

## 2. 当前源码真正支持什么

### 2.1 Human text-input side stream：有限输入监督，不是完整经历

`HumanTextInputObservationContract.VerbForMechanism` 的正向白名单只有五类公开 verb：

- begin_card_play；
- confirm_card；
- cancel_card_play；
- confirm_target；
- end_turn。

不同控制器/鼠标机制可以映射到这些 verb，但当前白名单**没有** reward open/back、focus_target、卡牌 inspect/升级切换、selector child 等。begin 路径在原生输入前冻结独立 Connector root text snapshot/完整菜单，并按 native owner/card/holder 精确唯一映射。它证明“人在这份输入前视图中做了这个输入”，不证明 card Commit 或因果后继。

Python import 明确发布为工程用途 `bc_input_observation`；`observed_input_sequence._human_view` 固定 Human 为 `human_witness_only`、delivery_mask=false、successor_snapshot=None、successor masks=false、`partial_human_input_stream`、trajectory_complete=false。capture_ordinal 与 physical append order分开，未知捕获位置或缺观察会隔离/reset；ordinal 本身不是全游戏事件时钟，不能以连续序号推导所有浏览均被捕获。

### 2.2 Canonical 主链范围更广，但不能自动填平上述缺口

canonical CaptureProfile 有战斗、奖励、地图、商店、营火和 nested selector 等较广声明；NativeNestedSelectorPatches 按确切上下文绑定 parent。它们属于自己的 schema、tracker 和证据条件，不等于 text Human stream 已支持所有原生浏览步骤。

可以根据确切 event/owner/object/version 建立跨流转换或关联，但不能按相邻行、最近 root、FIFO 或时间接近拼出缺失输入。Read-rich 捕获可以是当时技术上可得的公开事实，不证明 Human 展示/取得过相应资料，更不证明其心理理解。`H != S`；输入选择证明、Commit、之后观察和因果后继分别保留。

本轮未重新审计某个当前数据集，因而不报告“已有多少条完整 L-N M2 可用数据”。源码 partial 也不意味着所有局部样本无用；用途分别判断。

### 2.3 当前有多条 M2 路径，不能合称已经具备新 L-N 能力

| 路径与确切范围 | 当前输入/计算 | 数据与学习限制 |
| --- | --- | --- |
| 当前 worktree 的 text-menu observed-input → ExperimentalDSimpleM2 | 当前页文本token、候选文本、持续W；history profile可接已合格的先前输入见证；core维度的1/8 slots，候选只读评分 | bridge区分observation/choice等mask；数值训练支持无choice-loss步继续advance。Human来源仍partial；bridge public_feedback=None，特殊settling需opt-in并从模型step过滤，不能称新L-N全事件消费已实现 |
| 当前 worktree 的 LightActionM2Scorer／canonical sequence | 页面token→384维1/8槽W；动作独立UTF-8字节编码；候选分支读W评分；内核可接受prior actual/feedback | canonical sequence依已验证链，旧trainer使用listwise N、feedback=None；不是所有浏览/无标签事件的通用投影 |
| e30247d 的 Public M2 候选 | public_compact页面token＋完整BoundAction动作文字/字节；observation-only；维持W后全评分 | port要求reads=[]；调用step时previous_actual_action=None、public_feedback=None；N-only，不能将新L-N输入/端口直接改名接入 |
| 新 L-N 参考 D-M2 | 目标是实际曝光事件、原生浏览、合格prior/feedback、完整合法关系上的选择；文本或typed输入可分别登记 | 是新AgentSpec/数据视图/表示/recipe；复用内核不转移旧实验和运行资格 |

“M2”描述持续内部状态，不固定输入必须是文字，也不固定输出必须全量scores。上述代码里的候选中间向量有的叫future/transition，但只有N监督时不能据命名声称它已学成可信世界模拟器；Z/O头、奖励和RL优化也不由这个名字自动存在。

## 3. Annotator 为学习真正需要留下的事实

| 事实层 | 最低语义 | 为什么需要 |
| --- | --- | --- |
| 当前公开展示 | native page/phase、对象、原生文字/数字、焦点/暂选/预览、当前已展示提示；捕获位置与display context | 避免把动作后预览当动作前输入；支持不同表示 |
| 当前动作关系 | L-N scope内完整current relation、对象绑定、原生可达条件与缺口 | 全量N、分解mask、生成合法目标和后续对照；不只是Human选中的那一项 |
| 实际Human选择 | exact native mechanism、owner/对象/输入关联、唯一语义动作、accepted/rejected/unmapped等 | 能教begin、查看、选择、取消；不靠之后结果猜之前动作 |
| 曝光顺序 | 页面出现、焦点切换后的真实显示、重访occurrence、自动公开事件、实际发生关联 | M2前缀必须包含当时可用的信息，不能仅把若干最终动作排序 |
| 分支/生命周期 | parent/child已知程度、原生等待/继续、task/run/actor边界、gap | 子选择是新决策问题；被截断历史不能冒充完整局 |
| 后续与结果 | input delivery、Commit、之后观察、因果关系、terminal 各自已知/未知 | BC、Z、O、RL使用不同条件；不能统一一个success |
| 身份与用途 | 游戏/producer/profile/representation/source，Human/Agent/接管来源，use/split | 避免跨版本数据被错误重解释、同局泄漏、Agent行为伪装专家 |

这不是要求 Annotator 记录 Human 思维，也不是每帧都做模型输入。需按已定义原生语义事件捕获可学习变化：进入/离开详情、升级切换、target focus变化、真实选牌等；动画帧/重复轮询不造choice。短暂hover也可能真实揭示资料，不能为了减量任意丢掉然后称历史完整。是否把偶然hover作为N目标可以由研究mask决定，但曝光事实和缺口仍保留；停留时间启发式不是原生因果证明。

同一公开视图的readonly投影和legal relation builder应被在线Agent与Human passive capture复用，减少两套语义漂移；Human origin、原生接受和持续性仍由Annotator独立见证。禁止通过实际驱动游戏来“替Human完成采集”。

## 4. 同一原始轨迹可以有不同学习资格

| 用途 | 最少需要 | 不必强求/不能混用 |
| --- | --- | --- |
| 局部行为克隆N | 合格输入前观察、当前合法关系、Human实际选择、来源/use | 不必把每个查看都证明成game Commit＋causal successor；局部N不能宣称完整M2前缀 |
| M2序列N | 上述目标＋按输入合同完整或明确截断的可重放前缀、合法无标签事件、reset边界 | 不需要Human隐藏记忆向量；不能删无N事件后仍称同历史 |
| 结构化输入N/M2 | 同时点对象类型/实例、数值/文本/关系及语义来源 | 仅旧文本不一定能无损恢复结构；能确定重建的才转换，其余拒绝/缺失另profile |
| 生成式动作监督 | 合格Human选择映射到公开操作/refs的规范化输出序列，模型输入与在线一致 | 不需要Human写JSON；规范化token是派生标签，不是人思考顺序/工具调用/CoT |
| Z后继监督 | 目标所要求的实际执行分支和后继证据 | 普通after-image不自动是causal successor，未执行动作无事实反事实标签 |
| O长期结果 | 同一任务/战斗可证终点、horizon、统计口径和删失 | 未结束不填失败，退出不当死亡，少动作不自动高价值 |
| 在线RL | Agent真实rollout、算法所需policy概率/历史、奖励定义、terminal/truncation/介入 | 这些是Agent来源，不是Human；不同算法对logprob和值的要求不同 |
| 离线RL | 固定数据的观察/实际动作/可用后继/奖励/终点与序列资格 | Human行为概率通常未知；不是所有算法都需要它，也不能为需要者捏造；覆盖不足不能靠格式解决 |

应当分别报 observation_mask、choice_mask、history/reset/coverage、successor-observation、causal-successor、各outcome-target masks。当前代码已有部分这样的区分，但并未完整实现本表新L-N覆盖。

注意N数据本身也存在状态别名问题：相同页面在不同先验知识/未捕获历史下Human可能选不同动作。可选处理是限定从足够早的合格前缀训练、明确prefix_unknown的新segment、或仅作有范围的局部监督；不能声称模型凭缺失输入就应复制每次Human决定。

## 5. 六个经典例子：该记录什么、能学什么、当前缺什么

### HL-01 奖励进入、返回、重访：我们的记忆判别例

```text
O0 外层奖励 → a0 打开奖励A
O1 当前三张牌 → a1 返回
O2 外层奖励 → a2 处理其他奖励
O3 外层奖励 → a3 再进奖励A
O4 相同三张牌 → a4 领取或再返回
```

每个O是当时输入，a是Human实际原生选择的语义标签。O1与O4正文可以相同，但历史不同；不能按内容hash去掉O4。M2可学到有条件的不同选择，但“已经看过”不意味着必定应该领取，Human也不是无误专家。

当前text Human白名单缺这些导航标签；canonical中的claim/select不自动补出open/back序列。因此现有局部奖励记录可做什么用途要按自身证据另判，不能直接声称具备这个完整M2例子。需给native open/return及曝光事件添加被动见证，再验证原始→轨迹→训练→在线同前缀。

### HL-02 持牌指向敌人看数值：确认标签不是整个策略

```text
O0 战斗 → begin(c1)
O1 持牌 → focus(e1)
O2 原生显示目标e1相关数值 → focus(e2)
O3 原生显示目标e2相关数值 → confirm(e2)
O4 原生结果/继续
```

应在focus前记录输入、在真实渲染后记录新曝光；不能将O3数值前置给O1，不得从最终confirm倒填中间查看。当前begin/confirm有Human输入见证，但focus没有该text机制/witness，after-focus也不是完整side-stream结果。

observational-only模型仍可能通过之前见过的页面学到某些历史差异；但若两条轨迹的观察序列相同、实际动作不同，就不能期待只输入观察的模型凭空区分。新的prior-action合同是在解决这一表达能力边界，不能直接沿用旧模型资格。

只有确认标签，可能主要教会“确认当前已经指向的目标”，不等于教会选择查看哪个敌人、比较预览或最初选目标。覆盖应针对这些不同决策分开验证。跨链最终play(card,target)标签可以服务另一个动作粒度的研究，不默认与本L-N分步监督等价。

### HL-03 查看卡牌升级：信息取得本身也需要学习

```text
当前页 → inspect(c1)
普通详情 → toggle_upgrade
升级详情 → back
来源页 → 实际游戏选择
```

查看、切换、返回都可以是合格N目标；原生展示的两份正文是历史输入。预览不是实际升级，不产生“卡已升级”标签。模型可以学会何时查看，也可以由Agent内部固定查看程序承担，但那是不同AgentSpec。

专家可能本来就熟悉卡牌，因此很少查看详情；即使捕获完好，浏览示范也可能稀少。开放操作不会自动产生训练覆盖，需要单独报告查询/预览样本，可用明确的示范采集、合格规则知识或Agent探索补学习路径，不能伪造Human曾查看。

当前native信息页面和toggle操作存在，不代表Human text标签存在。若旧记录只保留最终升级选择，不能恢复当时是否查看过、看了多久或比较了哪些牌。也不能用新版本计算的升级说明回填旧输入。

### HL-04 父出牌与弃牌堆子选择：两个决策位置

```text
O0 当前战斗 → Human开始/确认原生出牌
原生效果进入child
O1 真实discard selector及完整候选 → Human选中/确认x
父效果恢复 → O2
```

O1可能在随机效果或其他变化后才出现。模型必须先看到O1再选择x；不能将x放进O0作为监督输入或预先输出的必填参数。canonical parent/continuation事实有价值，但必须取得目标L-N子页的输入、动作关系、pile来源和时序，才能形成独立子决策N样本；只证明selected card不够。

不同证据schema对root/child如何计数保留原义。是否形成两个训练位置由目标协议及证据决定，不由“文件里有一条还是两条canonical”直接决定。

### HL-05 自动公开事件、缺失和接管：没有动作标签也可能有历史价值

抽牌、原生效果续执行、已展示状态变化，如果属于在线event-consume合同，就需在离线相同位置消费；这里没有Human choice，不造一个“等待”标签。重复poll不写W。捕获漏失则history资格受影响，即使下一次出牌标签准确也不能假装中间没发生任何事。

已有数值训练支持无choice-loss的advance，但现有bridge会在限定配置下过滤settling观测；不能把它直接称作新L-N全部自动公开事件的实现。新capture schedule、projection和online adapter必须联合定义并核验。

Human接管Agent可产生后续真实Human选择，但保留mixed-actor来源/前缀/权限；不得把整段当纯Human专家局或无介入Agent成功。训练划分按同一run/共同来源分组，避免前半在train后半在test。

### HL-06 10000候选与生成/检索：不要求Human演示模型内部算法

Human真正做了`inspect_card(c7)`，原始记录有此前O、完整C和exact选择。可以派生规范化标签：

```json
{"operation":"inspect_card","subject":"c7"}
```

这里c7是该输入中的公共局部ref，保持同名实例区分。生成器以O/合法schema为输入，在teacher forcing下预测输出token；当前target只进loss，不能进入当前W。在线生成完整结构→Resolve当前C唯一成员→Submit原生复验。grammar控制来自当前C，不靠模型重建游戏合法性。

Human没有实际调用AllowedNext/ListActions/Resolve，也没有给10000项打分。无需虚构这些Human标签：它们可以是确定性Agent算法。若要学习“先检索哪一组”“什么时候请求更多目录”，这是Agent内部额外策略，训练需要自己的目标、可验证派生方法或Agent rollout，不能说从Human点击直接观测了内部检索路径。

检索可能漏掉示范动作，需报retrieval recall/排除分母；训练时强插Human目标以算loss是显式训练策略，在线不能知道这个目标。条件生成也不保证最优策略；只保证正确绑定的结构可被环境理解。

## 6. 当前M2、结构化M2与生成式模型怎样共享接口

### 当前M2处理的是历史条件下的候选评分

概念上：

```text
此前W + 当前公开页面 + 合格的先前实际输入/反馈（按具体profile）
    → 更新后的W
更新后的W + 每个当前候选
    → 分数
```

Human动作是N训练target。它不作为同一步writer输入；真正发生且按合同已知后才可能进入之后历史。1/8槽、384或core width是具体实现参数，不是M2概念本身。W是模型学出的内部状态，不是已验证的完整游戏状态，也不是Annotator需要记录的人类记忆。

时序训练要按事件顺序重建W；窗口截断梯度不等于新局/reset，参数更新后carry是明确的训练近似。无需保存每个Human时点的W；从合格公开前缀和指定模型计算。只有恢复实际Agent执行或特定数值调试需要对应模型状态/游标证据。

### 结构化M2：先改输入，不必先改整个M2定义

输入可以分开为typed对象/公开数值/原生文字/关系/实际反馈，再融合到持续W；动作候选也可用operation＋对象关系编码。多输入并不强迫多个独立大网络，也不意味着强制建立游戏规则引擎。

如果原始记录保留这些事实，可以换renderer/encoder重新形成新模型输入；如果只有一段丢失身份/数值上下文的文本，不承诺能逆向还原。稳定内容可去重，但两个同名实例、多重性、公开顺序、未知状态不能丢。

输出仍可全评分，也可接生成头。若进一步改为按对象存储的可变长记忆，那是另一图/状态合同，不只是把现有384维W换个名字。均需新AgentSpec/representation/recipe，旧权重兼容和效果另验。

### 生成式策略不需要虚构解释链

一条Human选择可用于训练规范化动作token，不需要Human写JSON或提供内心理由。输出的是命令结构，不默认训练长篇推理。信息取得类动作同样可以生成：`focus_target(e2)`、`toggle_upgrade_preview`、`return_card_inspect`。每个真实native阶段仍是新的环境输入，不能因一次生成多项计划就提前消费未来曝光。

当前Runtime要求完整scores；新Choice port才允许直接提交唯一选择。内部生成、检索和评分可以共用L-N语义、记录基础与Resolver；具体模型输入/输出训练目标有版本，不能伪称旧scorer接口原样支持。

## 7. RL能否处理：能设计接入，但不是“有N/O就是RL”

### 在线RL

RL环境adapter在合法控制窗口内把同一L-N语义映射为观察和动作。`step`提交一个原生语义动作，等待其已知反馈/所定义的下一观察；native child选择保持后续step。Obs不是完整Markov世界，递归策略需要自己的历史/state。

奖励/目标由任务与研究定义；Connector不提供“应该得几分”。learner reset请求由有授权的Host管理执行，不能从普通gameplay接口任意改存档或绕过任务。只读目录查询是Agent计算，不是获得新游戏资料；Inspect/Focus/Back则是实际L-N交互。

Agent rollout需保留算法实际要求的行为信息：policy/version、选定动作、概率/logprob（算法需要时）、观察/历史位置、奖励来源、episode termination与truncation、接管/未知。若策略含生成token、约束mask、检索、规则或宏，概率必须对应**实际执行策略**；不能拿未经过mask或覆盖前的模型概率冒充行为概率。规则干预与Human接管分段/标记，不透明并入on-policy训练。

每个browse是否计一个折扣step、是否按原生时间或语义时间折扣、宏动作时长如何处理，必须在RL任务合同中定义。若每次信息查看都加惩罚或额外折扣，等于改变信息取得的激励，不是中性工程优化；长期目标和成本先明确。unknown delivery/缺失后继不能填假reward或terminal success，需按算法资格拒绝/删失。

### 离线RL与Human示范

RL需要其任务定义下的真实观察/动作/奖励顺序；不是每个UI查看都必须有game Commit，但不能把来源不明的间隔编成确定转移。普通之后观察只有其自己的时序资格，不自动变成较强的native因果后继。

Human轨迹可以用于预训练BC，也可能支持某种离线RL；但仅有输入选择不够构造任意Bellman训练。需要相应后继/奖励/终点与历史资格，且要面对数据分布外动作的值估计问题。不能因action在合法目录里，就给它复制Human实际选中动作的结果。

Human动作概率通常未知；有些离线RL不要求真实行为logprob，有些重要性加权/OPE方法有额外要求。估计行为模型不是拿到了真实Human概率；相应假设和不确定性必须报告。

O预测胜利/失败楼层/战斗末HP/剩余主动出牌，是监督学习目标；它可以帮助表示，但不自动变成RL。若用预测O直接选动作，是另一个决策算法，需要偏差/校准和实际策略评价。

### 其他保留的情况

| 方法 | L-N接口如何服务 | 新增资料或限制 |
| --- | --- | --- |
| BC预训练→在线RL | 同一公开语义和动作绑定，切换学习目标 | 行为来源分开；新rollout概率/奖励/介入必需 |
| DAgger/交互式纠正 | learner进入新状态后，人给真实合法选择或接管 | 标为该状态上的专家反馈；只给建议未执行时属于annotation而非native Human执行；禁止隐瞒混合控制 |
| 偏好学习 | 从有可比较上下文的轨迹/选择形成明确偏好任务 | Human选了A不直接证明B必差；别把一次选择当所有未选项的结果标签 |
| return-conditioned生成 | 序列模型以公开历史＋目标return条件生成动作 | 需要定义合格return；在线给期望目标，不泄露实际未来；不等同普通动作生成或在线RL |
| 世界模型/规划 | Agent内部用学到的模型推演，再提交当前合法动作 | AllowedNext/Resolve不是模拟器；环境不给未执行分支结果，真实分支数据无法自动监督全部反事实 |
| 分层Agent/固定浏览程序 | 高层决定目标，内部程序按L-N执行真实语义步骤 | 不能跳过新的独立选择；行为概率、成本、宏终止和数据映射单独定义 |

经典研究分别支持这些方法问题的存在，不是本项目已经实现的证据：[DAgger](https://proceedings.mlr.press/v15/ross11a.html)处理策略导致的观察分布变化；[CQL](https://arxiv.org/abs/2006.04779)讨论离线RL分布外估值；[Decision Transformer](https://arxiv.org/abs/2106.01345)使用return条件序列建模。这里不因论文存在就承诺方法在STS2更优。

## 8. 存储与训练规模：该压缩什么，不能删什么

保存原始证据和内容寻址的公开视图/动作关系，真实occurrence引用它们。静态定义/相同内容去重、受验证delta＋周期锚点可用于储存；重访事件和资料何时曝光不能按内容重复删除。历史游戏显示不能日后仅凭当前DLL再算。

无需在Human数据保存模型score/logits/W、token级AllowedNext或Resolve调用，因为Human没有这些内部运算。为新表示预留有来源的对象事实，不能为了未来可能性无限抓取隐藏内部对象或每帧全部状态。来源/version/capture profile、缺口和原生见证是必需；具体模型张量可以按输入身份派生并缓存。

模型训练时间还可能被大量浏览N标签占据。可按公开family分层采样/加权、分别评价浏览与gameplay，但改变loss权重必须明示；不能为了缩短训练而删掉会影响W的无标签历史，再称在线离线一致。总事件数、N标签数、独立run数、完整prefix比例、各family覆盖与字节/落盘耗时分别报告，不用一个总record数代表质量。

录制性能也属于资格：变化事件驱动、复用只读捕获和content去重、异步持久化保留确切顺序/失败；capture或append预算耗尽则显式gap/不健康，不静默丢Human事件以保流畅。不做新的运行测量就不给“几MB/局”或低开销保证。

## 9. 下一步应验什么，才真正支持“学人类”

1. 给L01–L64增加Human capture对应：before-page、exact choice、display exposure、order/continuity、after/result各项；明确原生不存在、源码缺口和已资格化。
2. 优先完成HL-01奖励重访、HL-02目标预览、HL-03升级查看、HL-04嵌套selector四条最短Human路径，加HL-05自动变化/gap负例。先源码与忠实检查，实机由独立授权包执行，不让人盲打整局。
3. 同一原始片段产生：局部N、M2 prefix、结构化输入、生成式规范化动作标签；逐项核对允许/拒绝和来源，绝不先假定都可用。
4. 在线/离线使用同一公共表示与event-consume规则：同一可证明输入前缀的W/输出在声明数值容差内匹配；这证明实现一致，不证明策略好。
5. 做独立来源划分的小N闭环，报告示范动作准确率、浏览/确认/实际策略选择分别表现，以及真实回访/loop/接管情况。只有“确认按钮准确率高”不能代表学会选目标。
6. 结构化/生成/检索模型可以再使用同一合格轨迹与新表示/目标；RL另立奖励、rollout/return/终点和预算资格，不凭N/O标签直接升级。

设计判断：L-N可成为共同交互基础，能够表达这些模型所需操作；**当前主要缺口之一是Human原生浏览与曝光历史尚未覆盖新L-N范围**。必须把Annotator及数据投影列为与环境执行同等重要的首版依赖，而不是等Agent端完成后再补日志。

## 10. 固定源码入口与审查范围

以下路径在50546a1基点主作者直接核读关键部分，并由独立只读调查核对Human链；不引用旧PR25统计作当前数据证据。

- [Human机制白名单](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/50546a1ae359680cf6dcecbf9d25083254fdfa7e/components/annotator/src/STS2HumanAnnotator.Core/HumanTextInputObservation.cs#L20)、[输入前捕获](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/50546a1ae359680cf6dcecbf9d25083254fdfa7e/components/annotator/src/STS2HumanAnnotator.Mod/RecorderRuntime.TextInput.cs#L57)。
- [Human import用途](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/50546a1ae359680cf6dcecbf9d25083254fdfa7e/python/stpd/fullrun/text_menu_human_import.py#L156)、[partial Human view和masks](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/50546a1ae359680cf6dcecbf9d25083254fdfa7e/python/stpd/fullrun/observed_input_sequence.py#L384)。
- [canonical capture范围](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/50546a1ae359680cf6dcecbf9d25083254fdfa7e/components/annotator/src/STS2HumanAnnotator.Core/CurrentCaptureProfiles.cs#L16)、[exact nested关系](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/50546a1ae359680cf6dcecbf9d25083254fdfa7e/components/annotator/src/STS2HumanAnnotator.Mod/NativeNestedSelectorPatches.cs#L163)。
- [observed-input bridge](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/50546a1ae359680cf6dcecbf9d25083254fdfa7e/python/stpd/fullrun/memory_sequence_bridge.py#L196)、[settling处理](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/50546a1ae359680cf6dcecbf9d25083254fdfa7e/python/stpd/fullrun/memory_sequence_bridge.py#L369)、[有标签/无标签训练步](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/50546a1ae359680cf6dcecbf9d25083254fdfa7e/python/stpd/models/dsimple_sequence_training.py#L188)。
- [ExperimentalDSimpleM2](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/50546a1ae359680cf6dcecbf9d25083254fdfa7e/python/stpd/models/dsimple_memory.py#L35)、[LightActionM2](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/50546a1ae359680cf6dcecbf9d25083254fdfa7e/python/stpd/models/light_action_m2.py#L33)、[其N trainer](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/50546a1ae359680cf6dcecbf9d25083254fdfa7e/python/stpd/models/light_action_m2_training.py#L143)。
- [另一个候选的PublicM2 port](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/e30247defd859dfc75708fea4612c668e93cdbd2/python/stpd/policy/public_m2_port.py#L241)、[其N-only协议](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/e30247defd859dfc75708fea4612c668e93cdbd2/python/docs/research/PUBLIC_M2_N_ONLY_PROTOCOL.md)。

没有对当前私有corpus计数、资格或训练产物作新声明。下一工程任务仍在既有E1/E2/E3/E4编号内；G1、实现和Human资格没有因本文通过文档检查而完成。
