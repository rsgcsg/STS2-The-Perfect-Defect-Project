# L-N 排队出牌与异步时序：Human 输入、执行状态和学习不能合并

日期：2026-10-07。设计/只读审查基点：40cc4e02632f274faf3691943bb34c84a5183b64；确切原生DLL SHA256：9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4。没有启动游戏、运行新Human、训练或改生产实现。本文补充[L-N v1](BASELINE_LN_V1_SPEC.zh-CN.md)和[Human学习闭环](BASELINE_LN_LEARNING_INTEGRATION.zh-CN.md)，明确修正“一步提交→必须等完整结算→下一决定”的过强理解。

**结论：原生允许输入/排队与先前效果执行重叠；事件形式仍可离散，但不能强加每步结算完毕的栅栏。** 最新[第一版综合设计](BASELINE_PROTOCOL_V1_SYNTHESIS.zh-CN.md)将稳定边界LN-B1与模型自主时序LN-E1作为同一底座的两个正当profile；原生时间保真不是唯一设计目标。串行化可以是明确协议选择，数据可按公开假设重表达，但不冒称原生本来只能串行或Human当时看到目标新输入。

## 1. 确切原生行为：是队列，不是后进先出堆栈

本轮只读核查得到：

1. `NCardPlay.TryPlayCard` 调 `CardModel.TryManualPlay`；后者检查当前 `CanPlayTargeting` 并请求入队 `PlayCardAction`。
2. 成功提交后，持牌UI操作调用 `Cleanup(true)`，其节点进入删除流程。`NPlayerHand.InCardPlay` 只判断持牌操作节点是否仍有效，不等于之前卡牌效果是否结束。
3. 手牌 `CanPlayCards/AreCardActionsAllowed` 检查持牌状态、extra turn、PlayerActionsDisabled、Peek等原生门，不存在统一“ActionExecutor正在运行就不能开始另一张”的条件。
4. 单人/host请求经原生synchronizer入队；特定战斗阶段可延迟admission。每个player queue按队首执行，eligible fronts中取host分配ID最小者；不是LIFO。执行器通常等待当前action完成再取下一项，PlayerChoice有自己的暂停/取消规则。
5. `PlayCardAction.ExecuteAction` 才再次取得确切card/target，检查牌还在Hand、`CanPlay`和`IsValidTarget`，然后消耗资源并执行效果。
6. 在所查提交/入队路径没有预留能量/星费的事务；读取的是当前资源，扣除在实际执行。排队后资源、目标或牌的位置变化可以使之后执行失败。
7. 取消时，本地排队牌只有在model仍属于Hand等原生条件下才恢复手牌UI；否则可能淡出。不能承诺“任何失败都退回手牌”。

因此不能用“已经接受输入”保证“以后必定能执行成功”。也不能由consumer额外预扣资源、预测前面卡牌结果，或建立自己的替代队列。真实规则仍由STS2执行。

“上一张没完全结算”还应区分游戏逻辑执行、异步效果、UI动画和因果证据完成；这些边界不相同。动画仍播放时可能逻辑已结束，输入恢复也可能早于效果完成，必须按原生状态而非视觉静止判断。

## 2. 两条时间线：Human何时决定，原生何时执行

下表是可能的顺序示意，不是本轮运行轨迹；字母表示确切对象关联，不靠时间戳猜配对。

| 顺序 | Human/输入侧 | 执行/证据侧 |
| --- | --- | --- |
| t0 | 捕获H_A；人提交A | 原生请求/接受入队A |
| t1 | 持牌UI释放，仍可继续看/操作 | 在真实执行边界捕获S_A、A_sem(S_A)，开始A |
| t2 | A效果尚在执行；捕获H_B；人提交B | 原生接受B入队；**此刻不把H_B当S_B** |
| t3 | 人可能继续操作其他允许控件 | A完成相应Commit；还不自动具有已证明因果后继 |
| t4 | 无须重新虚构人作一次B决定 | B真实执行前捕获S_B和执行目录；如证据门满足，此边界也可关闭A的因果边 |
| t5 | 实际公开结果陆续出现 | B按当前条件执行，或取消/中止；保留真实分支 |

H_B可能缺少A稍后产生的新牌、伤害、资源变化；S_B已经含有它们。将Human选B的标签移到S_B上，可能是在用**人选择之后才出现的信息**解释他的选择。

每张牌至少区分：输入前展示、Human输入/确认、native request、入队、执行开始、原生检查、Commit/取消、公开后续变化、已证因果边界。不同牌的这些阶段可以交错。输入接受顺序、执行开始顺序、物理append顺序不是一个序号。

## 3. 当前Annotator已有的保护，不要误报成全新问题

当前source已将H与执行S分开：

- H来自某个Human输入capture seam，原始投影保留；可能是StartCardPlay staged或后续fallback，不保证完整稳定GUI或最终确认前视野。acceptance明确不绑定semantic pre。
- RequestEnqueue Prefix关联确切native对象；OnEnqueued在原生赋ID后、通知执行前登记，保留action witness/native queue ID和原始H。
- `BeforeActionExecuted`重新捕获S及Native Foundation的执行动作空间，不能沿用admission目录当执行合法性证明。
- Started有独立execution sequence；Finished在相应已登记Commit seam记录完成事实，不自动捕获S'。
- A已Commit且B来到其**精确执行前**边界时，这个尚未受B效果污染的帧可以同时作为A的S'和B的S。它不是t2的H_B，也不是执行B后找一个相似页面回填。
- B只是接受入队不会迫使A立即settle。若效果确实跨越另一action的执行而关系不能证明，tracker保留unknown/failed-closed，不能强造全序因果链。

当前取消分类也有边界：before-start cancellation、after-start cancellation、missing-card execution abort不全相同；后来的Finished不能把已取消/中止动作复活成成功canonical。排队Human输入可通过`native_input`形式保留，即使当时public BoundAction未映射；这不反向授权Agent提交缺少公开绑定的动作。

历史`serialized_evidence_overlap`是重要的调查线索，但不能把旧session数字直接移到当前源码。本轮没有审计新session或确认当前loss率。现有相关回归名称包括`QueuedUiInputDoesNotSettlePriorUntilItsExactExecutionBoundary`、`RapidA1A2A3UsesBeforeExecutionBoundariesWithoutFalseAttribution`、`RapidLethalKeepsCancelledPrecommitButOnlySettlesExecutedAction`；本轮阅读了实现/测试引用，没有重新运行测试。

## 4. 到底要记录什么

| 记录事实 | 必需内容 | 不能用什么代替 |
| --- | --- | --- |
| Human输入时刻 | 原始H/当前公开页面、当前输入目录或明确缺映射、确切输入选择、capture顺序 | 执行时S、最后结果、事后完整牌组 |
| 入队关联 | exact native action/request关联、原生admission/cancellation、actor来源 | 最新root、FIFO猜配对、墙钟接近 |
| 执行事实 | exact action执行开始、独立S/A_sem(S)、参数/合法性检查、取消/Commit | 当初可提交、UI已释放、队列长度减少 |
| 公开经历 | queued card的原生可见表现、已实际给出的资源/目标/页面变化、子选择与返回 | 将内部完整队列和未来效果直接公开给模型 |
| 效果与后继 | 已证边界/依赖及未知，原生terminal与ordinary observation分开 | 下一次Human按键、普通poll、Finished单独当S' |
| 持久化/连续性 | 输入capture、nativeexecution、append各自顺序；gap、接管、原生版本、source身份 | 用文件排序重排Human历史、用无错误记录数证明完整 |
| 时间资料 | 同运行可靠相对时间/输入与发布位置、必要的间隔与观测延迟 | 用毫秒阈值证明owner/因果关系，跨进程墙钟强造全序 |

模型的Human模仿历史首先按输入/曝光顺序组织。执行轨迹按真正execution边界组织。两者通过确切关系相连，而不是选一种排序覆盖另一种。

不必每帧保存完整Snapshot，也不必向模型显示所有内部生命周期。可按原生输入、native admission/start/cancel/finish、公开视图变化捕获，内容寻址去重；但是某个快速输入/短暂预览丢失后必须报告gap，不能降采样以后仍称原始完整时序。记录同步捕获、序列化/落盘开销可能扰动排队窗口，需实测录制开启前后的匹配条件，不能只看文件大小。

## 5. 离散接口并不只有“等结算完再走一步”一种

### A. 事件离散、原生运行异步：建议L-N保留的语义

观察、选择、输入确认、公开变化用离散事件表示；游戏在模型计算期间继续运行。只要原生恢复可操作owner，就可以作下一次输入，不等待前一个效果完全结束。这样不需要连续输出鼠标坐标，也不需要每帧调用模型。

这保留异步性，不自动保证捕获了每个很短的机会：采样/发布/网络/推理延迟仍须记录、测量和纳入资格。

### B. 同一协议下，Agent自己选择等

Agent可以决定在自己的先前输入达到声明的已知进度后才继续。它是更保守的策略/调度配置，协议仍保留原生能提前输入的机会。要记录这一限制，并评价失去的时机/吞吐或策略能力；不称已经模仿Human快速排队。

### C. 环境强制每张效果完成才开放下一步

这是改变交互机会的串行profile。它可以作为有意简化的实验条件，但不与原生异步L-N等价。即使最终牌序/胜负相同，也不能证明每一步观察、取消机会、条件合法性和反馈相同。

若串行门要求“先拿到A的已证明S'才允许B”，还可能循环依赖：A的合格S'本来需要B的精确pre-execution边界。不得把离线canonical资格倒灌成在线原生输入门。原生执行终结、可接受下一输入、因果后继已资格化是三个不同条件。

### D. 冻结游戏/step-controlled Host

暂停原生推进等模型、固定模拟tick或特殊Host可降低时序压力，但它改变了运行条件，需要单独Host/profile与行为对照。不能在原生公平运行里暗中冻结，而把结果称同一native实时任务。当前本轮没有建立这样的Host资格。

## 6. 转换异步Human记录到串行训练，必须声明什么

不能一般地把`t2 H_B选择B`移到`t4 S_B选择B`，也不能把一串Human输入直接打包成t0的预先计划。Human可能在t1/t2之间见到新信息。

| 转换 | 需要的依据 | 结果的正确名称 |
| --- | --- | --- |
| 保留Human实际输入/曝光顺序 | 各H、choice、公共事件/时序关系、pending事实资格 | 异步交互的BC/M2轨迹；离散事件仍忠实 |
| 只选真实没有相关重叠的片段 | 按明确原生/公开条件证明该片段满足所选串行合同 | 有分母与选择偏差的串行子集，不是整个Human corpus |
| 延迟执行同一个已决定的B | 当初H_B/意图与之后执行关系保留；当前复验 | 预先决定动作的延迟执行，不冒充人在S_B上重新决定 |
| 把执行帧S_B作为输入配B | 有资格的execution frame/action；承认决策时点重表达 | 执行条件动作建模或特定派生监督，不默认Human当前观察策略 |
| 压成t0的动作块[A,B] | 所有成员在块决定时已可表达；没有使用稍后新信息；原生阶段/取消与反馈映射成立 | 有证明的macro表示；不是默认转换 |
| 宣称串行与异步行为等价 | 信息、原生机会、操作效果、顺序/取消、任务时间成本等条件的机制级证据 | 只在已证明范围成立；匹配一条轨迹不是普遍证明 |

如果A只是剩余装饰动画，而所有相关公开状态、可操作机会和B的效果条件已相同，可以研究简化；不能凭“看起来只是动画”统一处理。不同输入/执行合法性、资源变化、目标死亡、selector打断都可以推翻简单串行化。

## 7. 接口应如何表达：可输入，不等于上一动作已完成

第一版建议在语义上明确：

- `Observe/Events` 独立提供当前公开输入位置和变化；`input_ready`按原生owner/控件决定，不按“所有效果都完成”。
- `SubmitOne` 仍一次只投递一个确切语义操作，原子校验和输入调用串行，避免同时操纵两个持牌owner。
- Submit先返回已知输入投递/请求状态；后续进度通过确切关联事件提供。**等待效果中的native actions可以有多个**，不等于允许两个未知输入并发写入。
- 只有已知前一输入处理结果且当前原生目录允许，才发下一输入。`delivery=unknown`仍进入核对/停止边界，不能用异步支持作为重发或继续猜执行的理由。
- 不在Connector里创建第二个gameplay队列，不预留资源，不自动重排已入队牌；native队列和取消政策原样保留。
- “已提交但结果尚未公开”的自己请求可以作为Agent历史；native可见队列按公平投影提供。private native queue IDs、隐藏任务状态/未来分支只进入法证，不直接成为策略输入。

示意输入（设计字段，不是现有wire）：

```yaml
public:
  page: combat
  input_ready: true
  visible_hand: [card_B, card_C]
  visible_resources: 当前原生公开值
  visible_queue: 原生此刻确实展示的排队卡信息
  own_recent_requests:
    - ref: rA
      input_delivery: delivered
      publicly_known_outcome: pending_or_unknown
catalog:
  ref: C2
  complete: true
  actions: 当前可开始/查看/确认的原生语义操作
```

pending_or_unknown outcome不能与unknown input delivery混为一谈：前者可能已明确接受，但尚未有后续结果；后者不知道输入究竟投递没有。允许更多已知输入的基础是原生当前能力和一致控制，不是猜测前一张必定会成功。

BeginCard/Focus/Confirm仍各有原生阶段。模型内部规划两张牌不一次越过这些新观察；固定Agent程序可以串行发起，但逐步绑定、保留中断和实际曝光。

### 当前代码是否真的强制等待所有效果？不能这样断言

当前Connector的CanBegin/CombatTurn readiness和NativeCombatDecisionProvider没有通用executor-running gate；它们可能在前一效果仍运行时重新发布可用操作。

当前Policy Runtime在投递后调用`stableSuccessor`，实际条件是“同环境、更新的Snapshot、status不是settling”等，不是证明所有效果已结束，也不是canonical S'。因此不能把它描述成已经实现严格结算串行化；它也不是已经验证了本文件完整异步契约。应核它的观察栅栏、request关联、超时和pending账本是否保留短暂可输入窗口，别让函数名造成资格误解。

新的接口/Runtime需明确区分input receipt、后续公开变化、native结果和causal proof；现有字段含义不能无版本重命名。当前设计里“一次提交一个请求”指输入写入串行，不应解释成“最多一张native效果在途”。

### 停止与接管

Stop撤销后续Agent提交，不保证已被原生接受的A/B立即消失。原生队列可能继续执行，或被原生战斗结束/PlayerChoice规则取消。保留原actor来源及在途结果；不能把停止后的Agent-origin效果记成新Human动作。除非有单独原生合法取消能力及授权，不靠清空队列实现停止。接管者拿到的当前输入和已知历史要反映这一边界。

## 8. 模型怎样处理，为什么不必每帧跑大模型

| 方法 | 合理组织 | 成本/限制 |
| --- | --- | --- |
| 事件驱动小型M2/GRU/结构化策略 | 新公开语义事件更新状态；原生可输入时决定；轻量状态与当前候选 | 不跑每帧像素；仍需低延迟捕获/编码，效果依实测 |
| 当前文本M2 | 用实际事件顺序和公开prior/反馈；可共享静态编码、批量候选 | 当前Public observation-only路径不自动拥有pending输入历史；需要新表示/消费合同，不能复制旧资格 |
| 生成式模型 | 只在决策机会生成一个操作；原生继续运行 | 大模型耗时可能错过排队窗口，旧观察易失效；“协议能表达”不等于模型来得及 |
| 快慢两层Agent | 慢模块形成公开依据的计划，快模块以当前输入决定/取消计划/重新绑定 | 快模块的决策属于Agent策略，不是无责任胶水；都要入身份与评价 |
| 已声明的条件程序/短宏 | 相同确定意图的机械输入低延迟执行，遇新选择/失效停下 | 不提前消费未来信息；不能隐式依赖隐藏状态或自动重试unknown |
| 主动选择等待的Agent | 较少决策，降低时序压力 | 可以是首个工程消费者，但不证明快速Human行为等价或完整模仿 |

系统可以持续低成本观察原生回调与公开变化，不代表持续昂贵推理。模型事件调度需固定：同一capture/曝光前缀只消费一次；事件在推理期间到达时由Agent维护有序缓冲和模型状态版本，不能异步写同一个W造成竞争。当前推理基于哪个输入位置、提交时是否已过期均有记录。新公开信息会改变决定时不能无说明复用旧计划。

事件合并只适用于证明没有丢掉目标输入语义的冗余更新；为了降频把短暂目标预览/可输入机会删掉，会形成不同信息或时间条件。缓冲溢出是gap/中断，不是静默丢历史。

相关成本分开量：原生输入窗口、capture/投影、事件发布、传输、推理、动作绑定/提交、stale拒绝、排队到执行延迟、missed opportunity，以及录制本身扰动。没有这些测量不能保证某毫秒阈值，也不能先断言慢模型必输或所有排队仅提高速度。

## 9. 学习：BC、M2、执行模型和RL各自用哪条线

**BC/M2：**目标是Human在H_i与其真实过去下选择输入a_i。动作后来取消不抹掉该选择事实；能否作为“优质示范”是研究筛选，不能删原证据。pending请求、之后取消和真实曝光按可用位置进入历史；不让未来execution frame反向进入当前writer。训练数据中的执行结果可能比输入晚很多，物理append先后不决定何时可用。

若Human在t2提交B，而模型推理到t4才出结果，它面临不同交互条件。离线准确率没有验证这种时序迁移。需测观测延迟和动作延迟，或明确采用不同串行任务条件；不能只用行为标签准确率声称已学会原生队列控制。

**执行模型/Z：**用独立execution S、实际执行分支和合格后继建目标。入队但未执行的B没有成功执行后果；“没有成功执行”也不是随意填一个自环。可以单独学习执行成功/取消风险，但标签和可用输入必须准确，不公开私有未来。

**RL：**可以在每个实际决策/公开事件边界组织离散样本，但环境仍在异步演化。内部真实state可能包含队列/进度，Agent仅看允许的公开部分，因此通常应按部分可观测、有延迟的交互处理。仅改成可变step时长的SMDP并不会自动解决重叠提交、推理期间变化和隐藏队列问题。

例如A的伤害在B已提交之后才出现，不能在数据里宣称它是B的即时独立效果。RL可以在明确定义的真实观察/奖励时间线上处理延迟credit，但要保留pending/actor/时间合同。discount使用语义事件数还是实际时间、接口计算是否计成本、unknown/截断如何处理，均入Task/Experiment配置；因果证据强度另标。行为logprob必须是实际带延迟/缓存/规则覆盖的策略概率，不能偷换为旧观察下原始模型分布。

公开相对时间可以在声明的representation中提供；源private执行标记和机器路径不直接进入模型。时间用于表明间隔/延迟，不用于替代native exact relation。

## 10. 最小反例与下一步资格

| 例子 | 必须看到的结果 |
| --- | --- |
| QT-01 A执行中B输入 | H_B留在其真实位置；B入队不让A自动settle；原生可输入不因generic busy被删 |
| QT-02 A改变资源，B执行时取消 | 保留B输入和取消；无B成功Commit/伪S'；回手按实际model位置 |
| QT-03 A击杀B原目标 | B晚期target无效按native取消；不自动选另一个敌人 |
| QT-04 A打开selector，排队B被native取消 | 保留parent/child和队列政策，child是新输入；不强行继续B |
| QT-05 A/B/C快速提交 | capture顺序与execution顺序各自可查；状态边界不跨后续效果错误归因 |
| QT-06 native投递回复丢失 | 查原请求，不因为支持异步就发更多未知输入或重发 |
| QT-07 Stop时队列未空 | 不再发新请求；已入队原生效果仍按真实来源/结果记录 |
| QT-08 慢模型算完时状态改变 | 旧绑定拒绝或按明确新观察重决策；不静默换目标/套最新目录 |
| QT-09 已知input accepted但效果结果尚未公开 | 可以依当前native-ready决定下一输入；不等同delivery unknown |
| QT-10 canonical S'依下一execution边界 | 不以尚无S'阻止产生那个边界；后继证明与在线输入门解耦 |

实现顺序建议：先读现有精确队列/见证机制并增加必要faithful回归；明确非阻塞输入receipt和公开事件关系；补Human曝光/队列关联用途；用最快、最小可解释Agent验证上述窗口和取消；再评价M2/生成式的延迟与学习。可以先用等待策略完成有界工程链，但必须保留原生能力和原始异步证据，不提前将整个Human数据串行化。

## 11. 固定源码与研究依据

- [原生输入提交/hover入口](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/40cc4e02632f274faf3691943bb34c84a5183b64/components/connector/host/PlayerEnvironment/TextMenu/NativeTextMenuCombat.cs#L43)、[CombatTurn readiness](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/40cc4e02632f274faf3691943bb34c84a5183b64/components/connector/host/LiveHost/CombatTurnSurfaceReader.cs#L77)、[NativeCombatDecisionProvider](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/40cc4e02632f274faf3691943bb34c84a5183b64/components/native-foundation/src/NativeCombatDecisionProvider.cs#L91)：没有通用executor-running禁令。
- [Recorder执行前捕获](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/40cc4e02632f274faf3691943bb34c84a5183b64/components/annotator/src/STS2HumanAnnotator.Mod/RecorderRuntime.cs#L2073)、[入队登记与H](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/40cc4e02632f274faf3691943bb34c84a5183b64/components/annotator/src/STS2HumanAnnotator.Mod/RecorderRuntime.cs#L2808)。
- [SemanticBoundaryTrace Accept/BeforeExecution](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/40cc4e02632f274faf3691943bb34c84a5183b64/components/annotator/src/STS2HumanAnnotator.Core/SemanticBoundaryTrace.cs#L368)、[取消分类](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/40cc4e02632f274faf3691943bb34c84a5183b64/components/annotator/src/STS2HumanAnnotator.Core/SemanticBoundaryTrace.cs#L523)。
- [现有queued输入回归](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/40cc4e02632f274faf3691943bb34c84a5183b64/components/annotator/tests/STS2HumanAnnotator.Core.Tests/SemanticBoundaryTrackerTests.cs#L140)与[rapid A/B/C](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/40cc4e02632f274faf3691943bb34c84a5183b64/components/annotator/tests/STS2HumanAnnotator.Core.Tests/SemanticBoundaryTrackerTests.cs#L539)：测试源码存在，本轮未执行。
- [Runtime投递后观察](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/40cc4e02632f274faf3691943bb34c84a5183b64/components/policy-runtime/src/runtime.ts#L715)、[stableSuccessor实际条件](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/40cc4e02632f274faf3691943bb34c84a5183b64/components/policy-runtime/src/runtime.ts#L1019)：ordinary updated observation，不是native全结算或causal资格。

原生行为来自本机上述确切DLL中NCardPlay.TryPlayCard/Cleanup、CardModel.TryManualPlay/SpendResources、NPlayerHand.CanPlayCards/AreCardActionsAllowed、ActionQueueSynchronizer.RequestEnqueue、ActionQueueSet.GetReadyAction/PlayerChoice取消、ActionExecutor.ExecuteActions、PlayCardAction.ExecuteAction/CancelAction、NCardPlayQueue.RemoveCardFromQueueForCancellation；主作者复核了输入清理、执行重验/扣费、FIFO队首和条件回手代码。没有将反编译源码提交到Git。

方法参照：[Reactive RL in Asynchronous Environments](https://arxiv.org/abs/1802.06139)指出环境可在策略计算时变化；[Handling Delay in Real-Time RL](https://arxiv.org/abs/2503.23478)研究观测延迟与模型计算权衡。它们支持需要明确时序和延迟合同，不证明STS2适合某个速度/模型或已通过运行资格。
