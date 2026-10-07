# 第一版协议总方案：捕获保证、原生抽象、稳定边界与模型自主时序

日期：2026-10-07。版本：0.1，G1审查候选。依据：当前用户重新审定的上层目标、历史原始讨论、a001c34a0a2f786349f960fbd84ad28f8360bffa源码及之前注明身份的本机原生机制。**设计不要求与原生GUI同构；合理的抽象、重表达和假设可以采用，但必须说明适用范围和结果的含义。** 没有生产修改、Human采集、模型训练或新运行资格。

这是当前第一版设计的综合入口，协调[L-N操作清单](BASELINE_LN_V1_SPEC.zh-CN.md)、[Human学习](BASELINE_LN_LEARNING_INTEGRATION.zh-CN.md)、[排队时序](BASELINE_LN_QUEUED_TIMING.zh-CN.md)。之前“保留原生异步应作为默认”的建议收窄为一个候选方向；稳定边界协议同样是正当方案，不需先证明原生无法实现才可选择。固定旧代码不是目标，真实任务有效、可学习、可验证和可维护才是目标。

## 1. 回到历史上层抽象

重新阅读的用户原始讨论包含以下明确方向：

- 世界真实状态X不等于观察O；模型参数、历史和合法信息取得可以共同满足能力，不强制四个固定输入槽。
- 协议规定与Agent及Host的交互语义；Host不必与原生内部一致，只在声明协议范围内研究对应关系。
- 记录保存事实，可派生成一种或多种目标协议轨迹；不必保存每个鼠标/画面变化，也不必把所有数据都叫S-a-S'。
- 一份轨迹在目标协议下通顺、能重放、可做行为监督是不同判断；对原生作有范围的抽象是允许的。
- Agent是与协议交互的封装个体，可以整体作为可训练模型；“何时等、何时行动”也可以由统一模型决定，而不固定塞给外层启发式。
- 当前首个贯通实例仍是D-M2和序列N，随后独立合格Z/O；允许旧数据不适用和必要重构。

由此得到本方案的评价准则：是否支持目标任务、信息是否公平足够、动作是否可原生实现、数据转换是否可解释、训练与运行是否一致、计算/记录是否有界、更新后是否容易定位问题。没有一个“越像原生越好”或“越少动作越好”的单一指标。

## 2. H究竟是什么：纠正“冻结”的过强含义

### 2.1 本轮核实的真实保证

| 检查项 | 当前源码事实 | 不能推出 |
| --- | --- | --- |
| canonical卡牌H | 首选StartCardPlay Prefix捕获，factory绑定确切holder；后来TryPlayCard尽量复用同generation/同卡frame，失败才fallback当时capture | H一定是最后确认前的GUI，或覆盖期间所有hover/target变化 |
| text Human begin | 独立在StartCardPlay Prefix冻结菜单/映射；finalizer才serialize/hash/append | 它与canonical H是同一个观察合同，或时间戳是整帧显示时刻 |
| confirm/cancel输入 | 有单独callback前capture | 捕获发生在OS输入产生前、人做决定前，或有全显示帧栅栏 |
| SubmissionGate | text witness在Connector共享gate内构建snapshot/menu/ordinal | 暂停了所有native异步、动画、渲染和其他线程写入 |
| canonical witness capture | 直接BuildSnapshot，未见相同全局锁或render fence | 所有来源同时原子读取 |
| 内容来源 | 一部分读模型公开字段/formatter，一部分读实际渲染node text；多阶段组装；ObservedAt在snapshot组装处取值 | framebuffer复制、所有遮挡/动画/图形细节完整、字段同一个渲染时点 |
| immutable内容身份 | 序列化内容寻址、摘要和碰撞检查保存记录完整性 | 抓取时与人所见完全相同，或人实际理解了它 |

例如canonical H可能是人开始拿牌时的投影，后来才选择目标；text continuation可以另抓确认前页面。原生对象reference equality证明关联对象是谁，不证明对象所有内容未变。当前record/JSON类型也不是递归不可变保证，但本轮没有发现某个真实session被篡改或混帧的运行证据，不能仅凭实现形式宣布corruption。

准确说法应是：**保存了某个已声明捕获位置的协议观察投影，并保留其来源和映射。** 稳定GUI、完整Human可见性、字段一致性、精确时间和心理知识都需要另证；名字H本身不授予这些保证。

### 2.2 新数据需要拆开的质量维度

`capture_seam`、capture开始/结束位置、可用的native/render tick或epoch、model-derived/rendered来源、display context、fallback/缺失、source前后是否一致、owner/reference匹配、覆盖范围、serialize位置各自保存。拿不到的写unknown。不能把当前`complete`一个值替代全部维度。

可以设计有界的一致捕获：同一获准线程/边界内复制目标字段，或用覆盖相应来源的可靠前后版本验证；字段跨来源就保留各自版本/时点并验证允许关系。不能用重复Snapshot相同、静止若干毫秒或捕获锁的名字当证据。动态界面不自动使逻辑捕获无效，缺少render fence也不自动证明逻辑状态读错；要按目标协议需要的内容来验。

## 3. 原生覆盖自定义协议：需要的是实现与投影，不只是集合满射

令N为原生交互过程，P为目标协议。我们不要求知道完整X，也不先假设O为Markov状态。

- **实现R**：在有效当前条件下，将一个P操作实现为一个有界、可中断、有结果语义的原生操作过程。它可能是多步，不必一个目标动作只对一个鼠标事件。
- **投影Π**：将一段含必要状态/输入/效果证据的原生记录，转换为P的观察—操作—结果/事件序列。允许是部分映射；证据缺失、假设不成立时明确拒绝该用途。
- **观察抽象α**：定义目标O哪些字段从什么时点、哪些公开来源获得；需要历史时以历史为输入，不强造单帧Markov。
- **对应要求**：在已支持范围内，执行R后再Π应满足P声明的结果语义；可以忽略已约定的设备路径、装饰动画或ID，但不能事后为了通过任意删掉差异。

用户提出“原生能覆盖我们的操作”抓住了可实现性的必要条件，但须区分：

| 覆盖说法 | 为什么不同 |
| --- | --- |
| 每个目标动作都有某个原生实现 | 只证明动作类型的可实现；不证明每个状态都能这样做 |
| 每个目标合法状态/历史中的动作都能实现 | 还需正确前置条件、原生绑定和失败定义 |
| 每条允许的目标轨迹都能由原生实现 | 还需顺序、信息时机、嵌套/取消和组合可执行性 |
| 每条收集的原生轨迹都能投影成完整目标轨迹 | 取决于记录覆盖与抽象；通常不能保证，部分转换是正常设计 |
| 两者对所有策略的结果分布相同 | 这是更强的行为/随机性对应，不由前面几项或单次replay推出 |

同一自定义动作可由多个native traces实现；同一记录也可能有多个合理投影。协议必须规定canonicalization或保留歧义，不能把“存在某映射”当成“已经有唯一可学习标签”。无须为了正常工程先证明完整bisimulation，但也不能把只证明动作覆盖命名为状态过程同构。

## 4. 四种数据声明：有假设的重表达可以是合法研究产品

| 声明 | 具体含义 | 最低要求 |
| --- | --- | --- |
| T：目标轨迹符合 | 数据满足P的对象、合法动作、信息、时序规则 | 来源映射与假设清单；目标state/action一致 |
| R：实际重放符合 | 在匹配起点通过R执行，得到声明范围的结果 | 实际replay；随机/起点/时间条件明确；一条不是普遍证明 |
| H：保真Human时机模仿 | 输入对应Human真正可用的记录历史，标签保留原选择时机 | 捕获/曝光/顺序覆盖和Human origin；不声称注意或理解 |
| D：重表达监督 | 动作确为Human-origin，但目标观察、粒度或选择位置按规则改变 | 明确derived任务、转换算法/版本、假设及局限；不能冒称H |

D不天然无效。例如“用执行前状态预测实际执行的Human-origin动作”可以训练一个有用的模型；它学的是该重表达分布，不自动证明Human在那个状态重新决定过。目标是实战能力时，可以允许D作为初始化/监督来源，再用匹配P的闭环评价判断是否有效。

公平可取得但Human未曾打开的事实，也可在另一个信息profile中加入；必须有目标时点的来源/可信重建依据，且标明信息条件改变。不能从未来才出现的真实随机结果倒填过去，或把新增隐藏信息称为合理假设。

**假设不是免责句。** 每个假设至少有ID、适用机制/版本、影响的目标字段/标签、可观测反例、当前依据、违反时处理、验证计划。数学充分性、源码依据、单条重放、实测统计与尚未检验分别标明。

## 5. 统一第一版底座：一个对象/动作体系，两个时序profile

核心接口仍面向封装Agent。内部模型可以是评分式、生成式、结构化M2、递归策略或组合模型。

### 5.1 公共底座

- **当前公共观察O**：对象/区域、原生文字、公开数值、关系、选择/焦点/预览、当前公开进度、来源/缺失。协议声明model-public与rendered-public哪些允许，不声称像素复制。
- **完整有限原生语义动作关系C(O)**：64行机制清单按所选profile的当前前提实例化；List/AllowedNext/Resolve是同一关系的访问方法，不是新游戏动作。动作不按策略价值过滤。
- **Act**：唯一绑定、当前复验、一次输入投递；receipt不冒充效果完成。
- **事件/结果**：输入处理、已公开变化、所声明边界、取消/terminal、gap/接管。private queue identity/证明保持法证用途。
- **Await**：由Agent选择何时再决定，具体见LN-E1 profile。它是调度选择，不是native gameplay action，也不进入native legality引擎；合法游戏动作仍完整有限。等待参数按TaskSpec有界。
- **连续性/记录**：共同身份、实际source时间位置与提供位置、mutable世界与immutable记录分开；控制、诊断和模型输入分开。
- **表示身份**：typed公开对象为基础，可读文本是确定性renderer；模型tensor适配另登记。相同JSON形状不代表相同输入语义。

第一版保留现有OP-S的真实目标、暂选、预览和取消机会，避免同时改变时序和动作粒度。完整card/target意图或短宏可作为以后OP变体；若模型内部预定目标再走原生步骤，仍记录实际事件，不能伪造对应Human中间决定。

### 5.2 LN-B1：稳定边界决策profile（边界版）

LN-B1是一个明确的简化环境，不强称等同原生全部时序。Agent在宣布的可决策边界收O/C，执行一个操作后，到下一个合格边界再作模型决策。游戏不因此自动暂停；若需要冻结时间必须另有Host能力和profile。

边界分三类，不能只写“等稳定”：

| 边界 | 第一版定义要求 | 必须排除的错误 |
| --- | --- | --- |
| MainReady | 原生主输入owner ready；上次操作的相关自主执行/排队进程已到声明fence；目标公共字段可一致捕获 | 把普通input-ready、相同快照或延时当全结算 |
| ChoiceReady | 真实目标/预览/selector等需要参与者继续选择的owner ready；相关显示已可捕获，parent可仍未完成 | 等parent结束才开放child，造成死锁 |
| TerminalReady | 原生终局/总结控制达到声明阶段 | game outcome与任务总结退出混为一谈 |

具体fence由typed native mechanism和作用域定义，如对应action/queue生命周期、owner/控件、相应deferred effects；不靠consumer重建规则。全局executor idle也不能自动证明所有延迟任务完成。对未登记/无法判断机制返回boundary_unknown/unsupported，不伪造稳定。若采用“已登记主效果结束后不再自主改变相关字段”的工程假设，必须入A-B1并验证反例。

操作示例：BeginCard→ChoiceReady(target)；FocusTarget→ChoiceReady(updated display)；Confirm→MainReady或新的ChoiceReady(child)；OpenInspect→ChoiceReady(details)；toggle upgrade→ChoiceReady(preview)。不是每次都等整张牌和所有父流程完成。

LN-B1支持各种模型，尤其便于先做时序N与结构化输入的受控对照。代价是改变了原生可提前排队的机会、状态分布和可能的策略空间。它可以是正式产品profile，或初期科学/工程基线，不是“失败了才退而求其次”。

### 5.3 LN-E1：事件驱动、模型自主时序profile（时序版）

LN-E1提供当前公开事件、输入就绪和对应C；先前效果未结束也可以继续输入。**模型同时决定做什么与什么时候再决定**，外层不固定“出牌后必须等完”或“有合法动作就必须立刻出”。

建议首版输出的逻辑形式：

```text
Act(action_ref)
Await(basis = 观察/事件游标/时钟引用, triggers = 公开事件或状态条件, deadline_after_basis_ms = 有界间隔)
Abstain(reason)
```

`Await`可选择下一公开变化、自己的公开结果、原生choice-ready等条件；条件必须是已登记、可以客观观测的公共事件/状态，不能请求“等到最优牌出现”这种由壳子替做策略判断的oracle。模型也可请求有界纯时间唤醒。Stop、权限改变、环境失效等控制事件始终中断，不受策略屏蔽。

**Await首版精确调度规则：**

| 字段/情况 | 确定语义 |
| --- | --- |
| basis | 同一environment/actor generation下的observation_ref、after_event_cursor和clock_ref；SDK可从实际模型输入填充，不能伪造未来cursor |
| next_event(kind) | 等该cursor之后的下一次匹配公开事件；不是“当前状态已经满足” |
| until_state(predicate) | 等已登记公共谓词为真；安装时当前条件已真且仍在期限内则立即完成，不等第二次边沿 |
| deadline_after_basis_ms | 相对clock_ref对应的Host发布观察单调时钟位置，包含生成/传输已消耗的时间；不是安装后又重新计满一段时间。数值/精度/上界由TaskSpec限定 |
| 安装期间的竞争 | 同一事件发布/等待注册栅栏内，先验证generation/权限/游标，再扫描cursor之后的已缓冲事件并检查当前公共谓词，然后订阅未来事件；不能只监听注册后的新事件 |
| 超时与事件 | cursor之后、deadline时刻或之前发布的匹配事件优先完成；无此事件且期限已过则timeout。注册已经晚于deadline时，不能用之后才变真的当前谓词假称准时满足；返回timeout及真实当前观察 |
| 控制中断 | 在一次wake结果封存前已生效的Stop/撤权/代次失效优先abort；完成后的控制变化不改旧回执，但阻断后续Submit。事件/timeout/abort只允许一个终态，顺序与胜因记回执 |
| 缓冲缺口/重复请求 | 无法覆盖basis之后的事件区间返回gap并要求新观察，不当作安静等待；同request同参数返回原结果，不重新安装新的等待 |

triggers列表采用OR语义，任一条件匹配即完成，不支持任意代码谓词。若缓冲事件证明某状态曾满足但现在已改变，回执分别给匹配事件/状态引用和真实最新观察，不能宣称该条件现在仍真；无历史谓词依据就不能补出曾经满足。

此处的先后按协议发布时钟和有序事件记录，不能冒称更早的native内部因果顺序。状态谓词只来自声明公共字段/事件，不计算策略价值。安装时谓词已真造成立即唤醒是如实结果；模型若想“等下一次变化”应选next_event，不能由壳子猜其意图。纯时间等待用空triggers和deadline，依然有控制中断和预算。

执行器只落实模型的唤醒计划、保证绑定/权限/预算，不能在模型等待时擅自替它出牌。事件持续记录；Agent是否更新轻量记忆、是否立即调用昂贵生成器由登记的统一模型/状态策略决定。事件丢失、缓冲溢出或模型状态版本冲突显式处理。

这满足“状态明确时连续出牌，需要更多信息时等”的目标：模型可以在A已知提交、当前B可操作时输出B，也可以选择Await某个公开变化。输入写入仍串行，native效果可多个在途。unknown input delivery仍阻断猜测/重发；已知输入投递而效果pending不等同unknown delivery。

LN-E1不要求每帧重新推理。最小实现可以让一个共享递归状态处理事件，再用动作头和等待头共同决策。昂贵内容编码可按确切内容/版本复用；不把动态目标预览按卡名缓存。共同目标、梯度/训练数据和整体评价决定模型的统一性，不要求所有程序代码都变成神经网络。

### 5.4 两版如何统一、首版如何推进

共享字段、合法关系、原生实现、结果分类、记录原件、数据用途和模型接入；profile只明确改变边界/事件提供、可提交机会和等待含义。不要为LN-B1/LN-E1分别建一套规则、账户、数据存储或业务服务。

**本轮建议：公共底座从一开始保留LN-E1的非阻塞receipt、事件和Await表达；LN-B1作为可选择的边界profile。先把LN-B1下的最小D-M2 N工程闭环作为清晰对照，同时用小型策略验证LN-E1的连出/等待能力，再训练LN-E1的统一时序策略。** LN-B1的选择是研究/工程条件，不把模型外壳固定成永远等待；LN-E1的时机学习是目标能力，不用“接口能等待”冒充已学会。

这里的第一版交付包括：Core合同、LN-B1/LN-E1明确schema/profile、64机制适用映射、两个数据投影规则、最小统一Act/Await模型接缝和下述验收。不是立即要求完成两套大规模模型训练。具体选择哪个profile作为产品默认仍需G1接受，之前的异步首选与Defect/A0首验范围都是建议，不能变成不可更改的用户要求。

## 6. 从原生记录到LN-B1/LN-E1：给出可操作的转换方案

### 6.1 LN-E1投影：原生时序抽象，不需要逐帧保真

捕获协议相关的native输入、目标/页面/提示变化、排队关联、原生结果和可靠相对时间；按LN-E1的公开信息规则得到O、choice及waiting interval。无信息/选择含义的鼠标移动、装饰帧按预定stutter规则省略。窗口内出现了目标preview或影响下一输入的变化，就不能省掉后仍称相同LN-E1轨迹。

Human没有点过Await按钮。可以从已证明输入可用期间没有发生输入的区间派生时机/生存分析标签，但必须标derived，并区分系统禁用、人在看信息、记录缺口、计算/设备延迟、会话结束的censor。沉默不能直接标成人“理性选择等待”。没有时序覆盖时只提供动作N，不训练等待头。

### 6.2 LN-B1投影B-exact：只取满足目标边界的原始片段

原生记录里本来就存在MainReady/ChoiceReady，并且Human在该边界后、没有改变目标输入的遗漏事件下作决定：直接按α与动作映射转换。可省略无关动画和设备步骤；字段/时点/动作关系与历史仍核对。报告可用与拒绝分母，这不一定覆盖全部原始局。

### 6.3 LN-B1投影B-derived：允许明确的执行状态重表达

对排队A/B例子，可以用确切S_B和Human-origin B构造执行条件监督，不必假装H_B已经完整可靠。但是**S_B不能直接假定是“空队列、尚未提交B”的目标页面**：B在native模型Hand中可能仍存在，却已从ActiveHolders移到排队UI，public目录不能再次提交它。

当前源码的区别是明确的：public hand/提交检查使用ActiveHolders；Native Foundation execution catalog使用logical Hand.Cards。因此需要明确的normalization N，且只能在适用机制上使用：

1. 去掉B/C已经提交造成的pending表现，恢复目标所需逻辑手牌展示/状态；保留实际版本和对象对应。
2. 证明或以明确可反驳假设约定：推迟这些admissions不改变此前相关资源、目标、随机过程、触发器、可见信息和选择状态。若enqueue本身有相关效果，就不能仅删JSON字段。
3. 重建目标C并证明目标动作在这种状态有真实native realization；不能拿execution catalog冒充可点击目录。
4. 若目标仍是OP-S分步动作，需要合格的中间视图/步骤，或明确的可验证派生规则。只有最终play(card,target)标签时，可以先用于对应粒度的意图模型，不自动制造所有begin/focus/confirm的Human示范。
5. 取消、missing card、target已死亡等分支按目标合同处理；不能把原本失败的B替换成另一个成功目标。

得到的结果根据证据标为D监督、T轨迹，实际replay另给R；不自动给H。若上述normalization未成立，仍可保留“execution-conditioned (S_B,B)”研究数据，不宣称已得到完整可执行LN-B1轨迹。把这条路叫重表达监督是正当设计，不需要全部丢弃；好不好由目标匹配的闭环实验判断。

### 6.4 记录侧的最低目标

为所选目标协议保存足够事实，不要求原生所有输入都作为学习动作。优先保留：有版本的公开逻辑状态捕获、动作/参数精确来源、生命周期/边界、必要的展示变更、队列/取消关联、时间与quality、gap。内部队列ID可以法证使用，不自动进入部署策略；不读取隐藏结果来补过去输入。

现有canonical3/trace4、Human text side stream和公开frame/read对象都可作原始来源；逐种能力核对，不信名字就升级。原件不可变；转换器输出来源引用、normalization、assumption IDs、接受/拒绝/不确定和用途。数据管理不替研究批准假设，Annotator不输出策略价值判断。

## 7. 假设登记表：合理假设怎样变成可审查设计

| ID | 内容/用途 | 反例/验证与失败处置 |
| --- | --- | --- |
| A-C1 | 所选捕获seam下目标字段是coherent的契约投影；不要求像素稳定 | 跨epoch字段、render/model明显错位；记录质量并拒绝需要该保证的用途，不宣布所有旧数据损坏 |
| A-B1 | 登记的MainReady/ChoiceReady fence足以得到LN-B1所需稳定边界 | 未登记timer/异步效果继续改变目标字段；修owner barrier或缩小明确范围，不能用静止毫秒补证 |
| A-B2 | 某类queued admission可延后且经N恢复目标状态，不改变相关语义 | 资源预留、enqueue hook、target/牌状态变化、selector取消；机制核对/匹配replay，未过则只留D或拒绝T |
| A-D1 | execution-conditioned Human-origin动作是对LN-B1任务有用的监督信号 | 模型依赖重表达偏差、在线不匹配；独立LN-B1任务实战/消融，不称Human当时基于重表达后的观察推理 |
| A-I1 | 所加公开资料在目标时点确实可获得/定义上授权 | 未揭示奖励、晚读回填、版本变更；按来源/时点拒绝，不以模型可能知道来补事实 |
| A-E1 | LN-E1事件采集和提供覆盖影响该profile决定的变化 | 快速预览/输入窗口漏失、抖动、buffer丢事件；gap/删失，测capture与运行延迟 |
| A-T1 | 用于时机学习的空闲区间具有可操作性及完整观测依据 | 原生禁用、记录暂停、人在其他页面、输入设备延迟；mask/censor，不伪造专家Wait标签 |
| A-R1 | 重放匹配的起点/规则/随机/时间条件足够 | 同seed不同native history/排队先后；记录差异，不能以seed相同宣称必然重放 |

每条假设都有自己的验证状态，不能用“假设存在”给数据统一PASS。可以发布带已声明未检验假设的研究视图，但产品/科学结果必须明确其限制；运行授权和隐藏信息边界不能通过假设绕过。

## 8. 大量具体例子：在两个profile下分别怎样解释

| 例子 | LN-B1稳定边界 | LN-E1模型自主时序 | 记录与转换判断 |
| --- | --- | --- | --- |
| V1-01 单卡效果结束且只剩装饰动画 | 已满足fence可给下一O，不必等画面所有像素静止 | 可立即Act或Await | 动画按stutter省略，不称H是稳定截图 |
| V1-02 A未结束，人提前提交B | LN-B1环境通常不提供提前提交机会 | 模型可学连出或选择等 | B-exact不直接收；B-derived须N/A-B2等，LN-E1保留真实H顺序 |
| V1-03 B已入队，执行前logical Hand有B但UI没有 | 不能把原frame当尚未提交B的ready页 | 当前已知pending事实保留 | 队列抹除/手牌恢复是实质转换，须资格，不是改timestamp |
| V1-04 A击杀目标，B取消 | LN-B1里对应目标已不可选 | LN-E1里已提交B可被native取消 | 保留取消；不可造成功B，也不可自动换target |
| V1-05 A要求弃牌堆子选择 | ChoiceReady必须开放，parent可未结束 | 新child事件唤醒/中断原等待 | 不能等parent完成造成deadlock；子选择单独输入 |
| V1-06 持牌从敌人1移到2，数值更新中 | 等该view所需display-ready，再给捕获 | 模型可以决定Await下一公开变化或使用已知输入 | 不混model getter和旧render值冒充同一完整view |
| V1-07 卡牌详情toggle升级 | view就绪后下一O；实际卡未升级 | Act toggle后可Await显示变化 | 只捕获逻辑展示边界，不要求全部动画帧；Human toggle来源另核 |
| V1-08 奖励看过返回重访 | 新边界保留新occurrence | 新事件保留历史 | 内容去重不去经历，循环可学但不靠环境代选 |
| V1-09 直接附带当时可查牌组 | 可另立信息profile，原生查看步骤可按规则省略 | 同样可选择别的IP，不强制与原生相同 | 有同时点事实才派生；新信息条件不证明Human曾看过 |
| V1-10 模型一次生成“出A，出B，等” | 若作为macro是另OP；普通LN-B1仍在边界反馈 | LN-E1可以输出计划，但逐原生输入复验并响应新事件 | 计划token不是已执行动作；若B用后来信息不能回填到计划前 |
| V1-11 H captured atStartCardPlay，之后拖动才确认 | H只证明起始seam，不叫最终确认前画面 | 需要不同事件的capture/exposure | 捕获字段来源/时间质量决定可用性，不能靠字母H断言 |
| V1-12 没有Human操作的长间隔 | LN-B1中可能是环境执行等待，不训练成策略Wait | LN-E1可学习时间，但需A-T1、可用性和censor | 沉默不是心理意图；界面禁用时间不能作为自由等待标签 |
| V1-13 10k合法动作，生成式输出一项 | 同Core Resolve/Submit，LN-B1边界下选择 | 同Core，另决定何时Act/Await | 元数据访问不是Human行为；完整authority不要求全量model score |
| V1-14 Stop时还有已排队native动作 | 停止新提交，原生后续如实记录 | 同左，中断Await/生成 | 已入队效果不承诺取消，actor来源不改成Human |
| V1-15 旧记录只有执行状态S与最终play | 可做限定D，完整OP-S中间样本不自动获得 | 不具备真实时机LN-E1的全部历史 | 不全盘删除，也不自动给T/R/H资格 |

这些例子用于决定方案是否满足目标，不能用合成断言替代真实native机制和数据来源检查。

## 9. 生成式／连续时间模型如何统一决定动作与等待

### 9.1 推荐第一步：事件递归状态＋动作/等待联合输出

```text
输入：新公共事件、最新O/C访问、允许的相对时间、模型自己的持续状态
共享模型状态更新
输出：Act(current_action) 或 Await(public_event_condition, bounded_deadline)
```

同一模型决定信息是否足够、是否继续排队、是否等待结果，不由外部“如果pending就等”的启发式替代。控制器保留合法性、安全停止、时钟唤醒与I/O职责，这些是执行约束，不是策略老师。当前旧M2的event-consume-v1只是一个reference方案，不限制所有新Agent的状态演化。模型选择的timer唤醒或连续内部state演化需要自己的时间输入/状态合同；它们不是重复poll变成新的native事件。

训练可先有动作N，再加入独立有资格的等待/时间目标或在线RL。Human未提供Wait按钮不妨碍对有条件的实际等待时间建模，但标签是行为时间的派生，不是人思考过程。无合格时机数据时，不能宣布整Agent时序已端到端学会；可以保留LN-B1基线和另标未训练的LN-E1接口能力。

### 9.2 自回归生成

模型可生成结构化事件，例如：

```json
{"kind":"act","operation":"begin_card_play","subject":"c2"}
```

或：

```json
{"kind":"await","basis":{"observation_ref":"o17","after_event_cursor":"e85","clock_ref":"q17"},"triggers":[{"mode":"next_event","kind":"public_observation_changed"},{"mode":"until_state","predicate":"choice_required"}],"deadline_after_basis_ms":200}
```

200只是语法示例，不是推荐延迟或测得预算。允许值/精度/上界属于TaskSpec；游戏合法动作C仍是finite native relation，Await是独立有界调度输出。模型可用共享主干生成操作和时间，最终Resolve/Submit保持唯一绑定与复验。

生成期间世界继续变化。第一版选择必须绑定输入版本，明确失效则取消未提交计划、用新输入再决定；未知已投递结果不重发。模型已经生成的一长串动作文本不授予future native actions执行权限。可以重用未失效的上下文/状态，但不能绕过新选择或把旧计划偷换到最新目标。

### 9.3 更接近连续时间的生成策略

可进一步建模`p(动作类型、参数、下一行动时间 | 已有公共历史)`，或以事件强度/发生率描述何时输出某类动作；内部state随时间或事件演化。动作标记仍可离散，时间连续，不要求预测每帧像素/鼠标路径。

连续时间点过程模型说明“离散事件＋连续时间”可以统一生成，但事件预测不自动是最优控制。需定义可行动风险区间、被强制禁用的时间、censor、推理/传输延迟和任务回报。首版不建议直接用复杂连续时间神经积分器；先对比小型结构化M2/GRU＋时间编码/等待头与生成式decoder，测延迟、时机质量和任务结果。

如果采用快慢模块，仍可联合训练/整体评价并共享目标；固定快模块替挑目标应单独标为策略组件。模型性强不是“没有任何程序”，而是关键选择——包括等待——由声明的可学习系统负责，整个Agent按任务结果评价。

## 10. 成本、可维护性与后续验收

| 维度 | LN-B1 | LN-E1 |
| --- | --- | --- |
| 在线样本数量/模型调用 | 通常边界少，易固定N比较；不保证native barrier本身便宜 | 按事件/模型选择调度，可能更多；不必每帧推理 |
| 原生实现成本 | 需验证fence、display-ready、子选择例外和timeout | 需非阻塞结果、事件顺序/缓冲、pending和延迟处理 |
| Human数据可用性 | B-exact可能少；B-derived增加利用率但有归一化假设 | 保真时机需要更完整capture/exposure，当前五verb链不足 |
| 泛化/维护 | 新机制影响barrier和投影；不能“稳定”一词兜底 | 新机制影响事件/动作与时序机会；不能“原生”一词兜底 |
| 模型能力范围 | 好的受控游戏决策基线；失去部分提前输入机会 | 可学连续出牌、主动等待；训练时机与真实延迟都更重要 |
| 科学评价 | 要报告输入重表达/选样偏差 | 要报告延迟、错过机会、等待/连出、介入与事件覆盖 |

第一版验收包：

1. **Capture-Q**：staged/fallback/confirmation各seam；render/model来源；动态目标预览与捕获区间；缺保证明确unknown，不用旧hash当视觉证据。
2. **Mapping**：每个目标操作的native realization、每种记录投影的接收/拒绝、stutter/normalization/assumptions；多步组合反例，不只证明单动作可调用。
3. **LN-B1边界**：主效果、持牌、selector、奖励、terminal；不能等待父完成或canonicalS'；timeout不伪证settled。
4. **LN-E1时序**：Act连出、主动Await、已满足谓词、注册前已发生事件、缓冲gap、event/timeout/Stop竞争、生成中stale、unknown；时机必须来自模型或明确profile，不暗加策略规则。
5. **学习**：B-exact、B-derived、LN-E1分别有数量/排除原因与scope；M2在线离线同输入合同；结构化/生成式的监督标签不伪造Human过程。
6. **实际任务**：稳定LN-B1与自主LN-E1在各自条件下闭环评价；匹配公开事实/预算的对照；不要把改变信息/时间条件的提升全部归因于模型。

当前首验Defect/单人标准A0仍可作为小范围提案，范围内发生的native机制不能悄悄排除。没有数据/latency测量就不承诺性能阈值。任务继续沿用P3/P4/P5及E1–E4，不另造阶段编号或自动开始实施。

## 11. 固定证据与研究依据

源码基点a001c34a；以下为本轮直接核查或独立审查后主作者复核的入口：

- [StageCardPlay捕获](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/a001c34a0a2f786349f960fbd84ad28f8360bffa/components/annotator/src/STS2HumanAnnotator.Mod/RecorderRuntime.cs#L806)、[复用staged/fallback](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/a001c34a0a2f786349f960fbd84ad28f8360bffa/components/annotator/src/STS2HumanAnnotator.Mod/RecorderRuntime.cs#L1146)、[实际序列化帧](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/a001c34a0a2f786349f960fbd84ad28f8360bffa/components/annotator/src/STS2HumanAnnotator.Mod/RecorderRuntime.cs#L2309)。
- [Text witness capture gate](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/a001c34a0a2f786349f960fbd84ad28f8360bffa/components/connector/host/PlayerEnvironment/Witness/ProcessLocalTextMenuWitness.cs#L16)、[canonical witness capture](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/a001c34a0a2f786349f960fbd84ad28f8360bffa/components/connector/host/PlayerEnvironment/Witness/ProcessLocalNativeWitness.cs#L257)。
- [模型描述与fallback](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/a001c34a0a2f786349f960fbd84ad28f8360bffa/components/connector/host/LiveHost/LiveContextReader.cs#L125)、[Snapshot组装时间](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/a001c34a0a2f786349f960fbd84ad28f8360bffa/components/connector/host/PlayerEnvironment/Observation/SnapshotBuilder.cs#L236)、[内容持久化](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/a001c34a0a2f786349f960fbd84ad28f8360bffa/components/annotator/src/STS2HumanAnnotator.Core/CurrentRecordingStore.cs#L272)。
- [ActiveHolders手牌](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/a001c34a0a2f786349f960fbd84ad28f8360bffa/components/connector/host/LiveHost/CombatTurnSurfaceReader.cs#L59)、[实际提交要求visible holder](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/a001c34a0a2f786349f960fbd84ad28f8360bffa/components/connector/host/LiveHost/CombatTurnSurfaceReader.cs#L322)、[execution语义用logical Hand](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/a001c34a0a2f786349f960fbd84ad28f8360bffa/components/native-foundation/src/NativeCombatDecisionProvider.cs#L105)。
- stableSuccessor、队列、late cancellation、H/S等精确入口和原生DLL身份沿用[排队专题](BASELINE_LN_QUEUED_TIMING.zh-CN.md)，本轮不复制旧session统计作新证据。

上层历史依据是本chat原始用户讨论（世界/Host/协议/记录/数据/模型）：2026-10-05至06相关对话，已回读原文；公开交接只概括需求，不上传完整私人聊天。当前foundation第5–6节本来已允许多协议投影与分级声明，本方案将其具体化。

研究参照：[Options时间抽象原论文](https://people.eecs.berkeley.edu/~russell/classes/cs294/f05/papers/sutton%2Bal-1999.pdf)说明有持续时间、内部策略和终止条件的操作可以成为抽象；[Neural Hawkes](https://proceedings.neurips.cc/paper/2017/hash/6463c88460bd63bbe256e495c63aa40b-Abstract.html)说明离散事件可以在连续时间中生成。这些只支持建模方式，不证明STS2具体映射、控制质量或旧数据资格。完整严格的MDP/SMDP同态有额外状态/奖励/转移条件，本文不冒称已满足。
