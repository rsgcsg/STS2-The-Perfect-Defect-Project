# A 主系统：用完整消费者旅程检验协议、数据和执行链

日期：2026-10-07。版本：0.1。源码核对基点：`70637a63a059c396b17db4a7ce8bb011bcfbfe7a`。状态：用户已选择**A作为主系统方向**；本文是P2/P3/P4/P5的进一步设计候选，G1完整设计未接受。没有生产实现、新Human采集、真实语料盘点、训练、游戏操作或部署。

本文优先于旧材料中“尚未选择主方向”的表述；[Agent蓝图](BASELINE_AGENT_PROTOCOL_BLUEPRINT.zh-CN.md)仍拥有详细Act/Await与状态机，[综合方案](BASELINE_PROTOCOL_V1_SYNTHESIS.zh-CN.md)拥有捕获/投影假设，[P4](BASELINE_DATA_AGENT_CONTRACTS.zh-CN.md)拥有N/Z/O和用途语义。这里新增的是**联合评估与修订方案**，不是把所有消费者强行塞进原方案的适配手册。

## 1. 先把问题用大白话说清楚

A准备建设一套主要系统：游戏向封装Agent发有来源的公开事件、当前信息和完整可操作目录；Agent可以选动作、等条件或放弃控制。什么时候行动由Agent负责，它内部可以是程序、一个模型或多个模型。记录、训练、执行和产品共用可核验身份与产物关联，但各自保留职责。

“三条链”是看同一系统的三个角度，不是只能按顺序跑的三个服务：

| 链 | 大白话 | 完整走向 |
| --- | --- | --- |
| 环境/交互/记录 | 世界发生了什么、参与者实际能看见和能做什么、我们凭什么相信记录 | 世界/Host → Connector公开协议 ↔ Human或Agent → 原始事实与各自曝光/请求记录 → Evidence验证 |
| 数据/Agent/学习评价 | 要学什么、用什么材料、得到什么Agent、到底有没有用 | 合格来源 → 用途/划分 → 协议及组件输入 → 目标/训练或无训练构造 → Agent包 → 实际运行 → 评价和修订 |
| 执行/产品/团队 | 谁在哪台机器做这件事、用谁的数据和算力、失败后怎么办、用户怎么操作 | 用例请求 → 权限/兼容/资源 → 作业与产物 → 验收/索引/共享 → 安装加载/停止恢复 → CLI/API/GUI状态 |

运行、录制、研究分析、推理、训练都可以单独发起。不训练的Agent也需要运行材料、验证和评价；只做离线预测的消费者可以完全不拿游戏控制权。三条链可继续拆成数据面、控制面、证据面来实现，不预先增加微服务或第四套权威。

### P2/P3/P4有两种同名含义，不能混淆

正式任务ID不改：**P2画清职责和连接；P3设计交互协议；P4设计数据、完整Agent、学习评价和执行合同。** 比如“新事件丢了由谁负责”是P2；“断流返回gap还是自动补一个当前页”是P3；“gap后的记忆如何重置、数据能否训练”是P4。

上一版四个原型也叫P1–P4，容易误解。本文用AG01–AG04作为清晰别名；旧脚本/回执的P编号保持历史身份，不改写其运行证据：

| 原型旧名 / 本文名 | 大白话 | 实际工作 |
| --- | --- | --- |
| P1 / AG01 | 一个“等到合适时候”的程序，配一个记事并选动作的M2 | 程序看公共边界；M2消费声明历史，只在决策位评分 |
| P2 / AG02 | 一个模块管“现在动不动”，另一个管“动什么” | 前者可规则或学习，后者可评分或生成；整Agent只有一个出口 |
| P3 / AG03 | 直接写出有结构的操作，例如选哪张牌、哪个目标，或等哪个事件 | 输出不是自由文字执行脚本；解析、唯一解析及native复验都要成功 |
| P4 / AG04 | 先在完整合法目录里找值得看的少数项，再精排 | 环境目录仍完整；漏掉好动作是检索算法的代价，必须测召回 |

四种可以组合：AG02的动作模块可以用AG03或AG04；M2可以为其中任意组件提供历史。它们不是四套互斥游戏协议，也不都是“必须从Human数据训练”的模型。

## 2. A给Agent什么、接收什么，以及Host如何接起来

### 2.1 主合同的建议内容

以下是待实现的语义对象，不是声称当前JSON schema已有这些字段。首次Attach固定TaskSpec、协议/信息/操作/事件版本、AgentSpec、Host能力和环境代次；缺必需能力在启动前拒绝，可选能力明确未支持，不能运行时悄悄降级。

| 对象 | 提供什么 | 不能被误读成什么 |
| --- | --- | --- |
| 公共Frame | 当前逻辑页、模式/公开phase、当前公开对象/关系、资源、实际揭示的文字/提示/预览、input-ready、有依据的边界状态 | 不是未打开页面全知视图，也不是完整人类心理或整个世界 |
| 事件批次 | 环境公开变化、公开请求结果、逻辑页进入/退出等；有序cursor、代次、时间来源、gap和捕获质量 | 不是60Hz截图，也不是后端append顺序冒充曝光顺序 |
| 当前C目录 | 完整有限操作关系、sealed catalog身份、计数/分组、小目录inline；大目录支持分页、prefix与唯一Resolve | 不是对象与动词任意笛卡尔积；只展示disabled项不赋执行权 |
| 已发请求状态 | 本Agent此前请求、明确未投递/已投递/未知、公开pending/completed/cancelled结果 | input delivered不是Commit，不是父效果完成，也不必阻塞一切新输入 |
| 质量/来源 | capture seam、字段可读性、缺失/未揭示、对象及payload版本、公开可用位置 | 哈希只能证字节身份；不能证明跨模型/render全局原子 |
| 控制侧元数据 | 授权/租约、版本/审计引用、资源预算、停止、恢复条件 | 不是默认模型特征；私有native witness/未来标签不得泄入策略 |

当前页完整逻辑列表不因滚动丢成员；牌组/牌堆等仍遵守“实际进入/取得”的信息政策。遗物/药水计数只给原生公开部分；卡正文保留当前对象、pile、目标、升级预览及动态来源，不按卡名缓存成永远不变的文本。短暂公开信息必须能进入目标事件/曝光历史；未知必须显式标注。L01–L64的全部场景义务仍见[详细清单](BASELINE_LN_V1_SPEC.zh-CN.md)，本文件不缩减分母。

Agent返回三种结果：`Act`（当前bound handle，或先Resolve的严格结构）、`Await`（观察/cursor/clock依据、公开触发条件和期限）、`Abstain`（原因）。每个mutation有唯一request及原始依据；陈旧、歧义、无匹配时返回类型化失败，不自动换动作。评分向量、解释、候选内部搜索都可记录为诊断，但不要求每个Agent评分全目录。

例如目标预览时，收到持有牌c7、已公开目标e2、该焦点下原生正文和当前C；Agent可从C选`focus(e3)`，收到新的真实预览后再`confirm(e3)`或`cancel`。不能把未预览e3的动态显示提前放进旧Frame。生成模型可以写同一语义结构；局部别名通过当前C映射，不能自己创建原生目标。

### 2.2 Host侧合同与适配层也要验

```text
Native STS2 / 另一明确资格的世界
  ↕ Host capability + exact identity / public capture / native bindings
环境适配器：实现公开信息、完整C、事件及提交语义
  ↕ A event协议
SDK：传输/分页/重连/解析；不代替策略
  ↕ 封装Agent（规则、模型、记忆、规划、时机）

只读事实 → 原始记录/曝光记录 → Evidence → 数据服务
任务/权限/资源 → 应用服务 → Host控制、Agent执行、训练worker
```

| Host能力 | 必需合同与例子 | 缺失时如何处理 |
| --- | --- | --- |
| 身份/生命周期 | 实例、generation、游戏/组件版本、任务起止；模型reset与游戏reset不同 | 重启换代后旧action/state不可直接复用；重置游戏需管理授权 |
| 公开捕获 | 声明seam/字段来源/一致性范围、完整C和payload绑定 | 缺必要字段则不可声明该范围完整；不是模型猜补 |
| 输入/结果 | 精确native owner、当前复验、输入串行化、原请求查询、pending与unknown | unknown核对原请求；不因RPC超时重发mutation |
| 事件时钟/缓冲 | capture、publish、Agent交付三个位置；保留窗口、watermark/gap | 重连可补则按ID重放；不可补则明确中断并按Agent状态策略处理 |
| 场景/子流程 | 父pending时child-ready可单独暴露；摘要退出与胜负分开 | 不等父canonical后继才发布child，不制造死锁 |
| 可选模拟/分支/暂停 | 独立能力、规则版本、随机/存档限制和资格 | 缺时拒绝相应规划/实验；不在主游戏偷偷试走再回档 |

同进程、IPC、网络是绑定方式；相同操作名不保证相同时间/信息合同。保留一份语义规格与conformance案例，按Host binding验证。连接管理不等于接管游戏；连接断开不等于已入队native动作取消。认证、控制租约、录制生命周期和学习用途分别有owner。

### 2.3 本轮建议补强A的六个接缝

这些是案例导出的修订候选；不声称旧蓝图已经实现，也不自动作为G1接受项。

1. **事件与大对象分开传输。** 关键语义事件有不可静默跳过的序列；大页/目录可内容寻址、delta引用和缓存。缺delta基底必须补完整内容或gap；只允许合同列明的无语义展示刷新合并，不能任意latest-only。
2. **Observe与Consume分开记。** Host捕获、协议提供、完整Agent接收、组件编码、模型更新分别可追踪。慢模型期间新事件入有界buffer；容量溢出显式gap/停止，不阻塞game线程等网络或静默丢目标预览。模型输出即使依据已旧，也只能按旧依据提交/拒绝，不能SDK自动重绑。
3. **学习者使用额外研究sidecar。** Reward/discount/behavior policy、目标/censor、use/split属于研究产物；事件协议提供真实公共时间/结果和来源，不把研究奖励塞进Connector规则。线上允许的task指令与事后真值要分通道。
4. **状态恢复有事务边界。** Agent的W/缓存/组件版本与消费cursor成对保存，或从允许前缀重建；请求账本独立核对。模型checkpoint、推理状态、游戏存档、云作业attempt不能互换。
5. **能力匹配覆盖全系统。** 不是只协商Host—Agent；CaptureSpec、ProjectionSpec、AgentInputSpec、TargetSpec、worker配方、Agent包及分发兼容一起检查。无训练Agent可以没有TargetSpec，不能因空值走默认Human训练。
6. **结果必须可归责。** observation缺失、catalog不完整、stale、unknown delivery、Agent超时、模型坏输出、数据用途拒绝、worker崩溃分别统计；一个总“失败”无法决定应该改协议、模型还是产品服务。

## 3. A的人类记录与M2 N首条链，具体怎样做

### 3.1 拟采集什么，以及目前缺什么

原生Human可以照常点游戏，Annotator被动观察；不要求人类通过Agent API操作。采集需要支持后续目标信息/时间合同，而不是事后把canonical行改名成Agent轨迹。

| 原始材料 | 为何需要 | 当前准确边界 |
| --- | --- | --- |
| 各公开页/提示/预览的进入、变化、退出与内容 | 重建“先看过什么”，包括没有动作标签的短暂信息 | 当前Human text仅五verb；浏览/hover/selector全历史未覆盖 |
| 原始输入seam的H、操作、曝光历史及可操作性 | N拟合当时选择；时间学习区分可动/不能动 | H是seam投影；不是保证等同最终确认前完整GUI |
| exact input/admission、execution S、Commit、successor与取消 | 区分选择、投递、执行和结果；支持各自Z/O资格 | canonical链与Human text是不同流，不能按相邻行自动join |
| run/combat/task边界、终点及接管/中断 | reset、O目标、独立划分、删失与评价 | 没有原生结束证据不能补“失败”；中途加入不能伪造此前记忆 |
| 版本、capture质量、原始序列、时钟与gap | 复现投影、数据盘点、工程缺陷定位 | 尚未证明现有语料满足新A；可能某一新用途合格数为0 |

新的目标是“足以投影声明的A轨迹”，不是记录每个像素/鼠标抖动。输入之间的必要公开事件也需覆盖；只在点击时采一张图不保证M2前缀完整。Human接受选择与被拒绝尝试分开，后者可作诊断/另一任务数据，不作为成功执行样本。

原始记录保留；Evidence验证完整性和来源后，研究转换器产生ProtocolTrace与资格报告。T（目标协议成立）、R（实际实现/replay）、H（保真Human时机）、D（重表达监督）独立声明。把排队输入改成稳定边界、合并浏览步骤、由execution状态恢复可输入页，都要有对应假设和验证；不能仅改时间戳。

### 3.2 一个可逐行检查的M2例子

以下为设计示例，不是新Human录制：玩家先查看奖励组A，返回，再看B，最后回A选择。所有名称都是示意局部ID。

| 位置 | 实际已公开/发生 | 整Agent与M2 | 当前训练目标 |
| --- | --- | --- | --- |
| e1 | 外层奖励页，两个尚未打开的组 | W初始；消费e1；边界评分当前C | 若选择来源合格，N=open(A)；不提供A/B未揭示内容 |
| e2 | open(A)输入被接受 | 请求进入真实历史；不重复执行 | 可无N；回执不作父效果Z |
| e3 | A内容实际公开 | 按序更新W；可在边界选return | N=return；A内容已成为后续可用记忆 |
| e4 | 外层页重新出现 | 新occurrence，即使文字与e1相同也更新 | N=open(B)，不能按文本相同删e4 |
| e5 | B内容公开 | W保留A；更新B | N=return，不能让B提前进入e1 |
| e6 | 外层再现 | 使用合法历史比较已看过的A/B | N=open(A) |
| e7 | A重访，当前内容及C重新核验 | 不能使用旧action handle；当前页可能变化 | N=select(a2)；不是把同内容重访当网络重传 |
| e8 | 实际选择的公开结果/后续页 | 后续consume才接收结果 | 独立判定Z；没有合格因果边就mask |

**记录→整理：** 捕获事件和Human选择来源，验证source/exposure顺序；转换时保留无N事件与重访；先按run及共同来源分组划分，再拟合train词表。对AG01的边界输入，若原Human恰在该边界按该信息选择，可标对应N；否则作为经验证的D重表达样本单独评，不称原Human在该边界作了选择。离线编译与在线采用相同事件筛选、renderer、请求反馈和reset合同。

**训练：** 从e1到e8顺序推进W。合格consume更新一次；候选只读同一W。当前选择只用于当前N loss，实际选择按声明可用位置进入后续历史。没有N的事件仍可更新记忆；这需要新trainer接缝，当前Public M2训练循环逐step算N且feedback=None，不能直接宣称支持。TBPTT切段detach梯度而不等于新局reset；checkpoint含参数、optimizer、RNG、数据位置及配置。carry与独立训练reset对照，K1/K8等为待封存实验参数。

**产物→使用：** 输出ModelArtifact，加上encoder/词表、history policy、固定TimingPolicy、renderer、状态格式和端口版本组成Agent包。加载先验证兼容和用途，再从新segment或可证明连续的cursor初始化。收到事件消费历史，TimingPolicy在可决策边界调用M2，读取完整C，选出当前handle，提交并记录结果。失联和未知请求走各自恢复，不自动出第二次牌。

**评价→修订：** 离线比较N准确率/负对数似然、重访切片、carry/reset、转换排除率；闭环再测选组/实际结果、循环、等待、stale、成本和接管。低N loss不证明完整过关。若失败因漏录e3，修capture；若A已记录但线上漏consume，修Agent；若都正确仍忘了A，才是记忆/学习问题。

### 3.3 再用三个不同场景检查这条链

| 场景 | 采集与转换 | 训练/运行/评价后果 |
| --- | --- | --- |
| 目标动态预览 | begin→focus敌人1→正文变化→focus敌人2→confirm；每份预览绑定当时对象/显示来源 | 不能只用begin H训练后续confirm，也不能预填所有目标预览；线上逐事件更新；评目标切换、取消和动态信息利用 |
| 弃牌堆子选择 | PlayCard父输入→child页及pile身份/完整候选→选择/确认→父继续；需要exact父子来源 | continuation证明所选牌不等于有child决策输入。齐全后可训练独立selector N；父未完时Agent仍能处理child；Z按父/child定义分别mask |
| A牌在途时人类又点B，B后来取消 | 保留Human输入顺序H_A/H_B、实际执行S_A/S_B及取消；不把B搬到A结束后冒充Human观察 | 合格H_B可有选择监督；没有成功执行不能填成功Z。AG01若不提前出牌是不同策略/投影；评快速输入覆盖与late cancellation，不能只删失败保留顺序“成功样本” |

这也解释为什么当前canonical rows、Human text inputs、A ProtocolTrace、M2训练step数量不会相等。每步都要有分母、排除理由和父来源，不用一个valid布尔值混过去。

## 4. 十四种消费者的完整数据与运行旅程

所有案例依次列“来源/采集—整理—构造或训练—运行—评价—对系统的反向要求”。既有源码只作为复用依据；这些不是14个已实现或已训练Agent。AJ编号仅是本文件案例编号，19个正式任务ID不变。

### AJ01：AG01，固定时机＋M2序列N

**来源/采集：** 采用第3节有资格Human示范；也可声明使用教师Agent实际轨迹，此时来源为Agent。需要公开历史、完整C和实际选择，不要求每个样本都有终局。不能仅因为文件存在就默认满足新A。

**整理：** run/来源组先划分；生成ProtocolTrace→AG01组件输入→序列张量，分别标choice/history masks；没有标签的合格事件保留。固定等待是程序策略，不伪造Human Wait。

**训练：** 复用候选LightActionM2内核，新增A输入和无N consume接缝；listwise N、TBPTT、carry/reset与K配置固定；训练/导出分别验收。**运行：** 固定时机模块选择Await或评分，M2只读全C选择；按新事件实际更新W。

**评价/修订：** 离线N与真实任务结果、历史一致性、选择器/重访覆盖、时间/内存成本分开。迫使A提供稳定事件身份和可重放消费规则；若边界检测缺child-ready，先修环境合同，不训练模型绕死锁。

### AJ02：AG02，独立时机模型＋动作模型

**来源/采集：** 动作模块可复用AJ01。时机模块可先固定规则，无需数据；若学习Human时间，必须采输入可用区间、公开事件、实际动作时点和缺口。也可完全由自己的RL轨迹学时机，不用Human。

**整理：** 区分“不能输入”“可以但没有输入”“观测缺失”“还在计算”；Human无动作区间不是自动正确等待标签。处理截断/区间删失，不能把append间隔当思考时间。两模块的状态/曝光输入分别生成。

**训练：** 先固定Action训练Timing，或联合优化；若概率分解为选择时机再选动作，记录联合行为分布及固定规则覆盖，不用一个动作softmax冒充整个Agent概率。缺合格时间标签时保持规则，不造标签。

**运行：** 快速时机组件消费新事件，决定继续等待、唤起Action或取消尚未提交的旧计算；已投递native动作不由取消模型推理撤回。Action根据原依据提交。

**评价/修订：** 固定Action比较漏机会、过早选择、空等、延迟和成本，再测整Agent结果。要求A在慢计算期间持续收事件、支持有界缓冲/明确取消与旧依据拒绝。若需要固定游戏暂停，另立Host时序profile，不伪装成内部优化。

### AJ03：M2 N＋Z后继预测

**来源/采集：** N可来自Human或教师；Z另需真实已执行分支与合格后继，可以来自Human或Agent。获得输入回执和新页面不足以建立native因果Z；浏览响应可以定义独立view-Z。

**整理：** 用exact lineage生成executed-action→qualified-successor，逐字段mask对象新增/消失/未读；保留不满足Z但满足N的样本。父child链明确哪个终点；未来target分库存放，绝不进入前缀。

**训练：** N策略加执行分支Y的Z-fact头；Z-latent需另登记teacher与stop-gradient，不能把预训练teacher放进声称scratch的条件。随机结果可以预测分布；未执行候选没有真实Z标签。

**运行：** W只由实际事件写；各候选Y是只读假设。可以仅用N policy head行动，把Z作为辅助；若用预测结果搜索，是新AgentSpec，见AJ11。

**评价/修订：** 同数据/预算比较N与N+Z，独立测字段预测/校准和任务表现。要求记录保留execution与公开反馈两条关系，而不是让A的每次Observe承担“因果后继”承诺。缺足够Z数据时先限定用途，不要求Connector发明结果。

### AJ04：M2 N＋O长期结果，随后可选偏好

**来源/采集：** 来自有终点/计数覆盖的Human或Agent局；中途片段可以有N，但没有整局结果不能猜O。胜负、真实失败层、同战斗末HP、剩余已确认主动出牌数各有不同来源。

**整理：** 按run/combat关联实际分支，标label-known/censor/continuation actor；相同终点回传到多step仍只有一个独立终点。失败层对胜利不填0，断录不填输；剩余出牌包括当前已确认主动出牌，子选择/药水本项计0而不是删掉后续计数。

**训练：** 各头按mask/单位/有效量归一，系数是训练信号权重，不是效用兑换率。先O-predict；若做O-preference，需要可比较起点/条件和预定偏好规则，不能把任意一胜一败配成动作优劣。

**运行：** 可仍由N head选动作，O用于分析或辅助表征；若按预测胜率/多目标效用直接选择，单独固定方法并评估分布偏移。O不是自动RL，也不是最优Q真值。

**评价/修订：** 按独立终点报告校准、误差及完整任务结果，单列接管和未知；早死出牌少不能成高分。要求跨run/combat/task的不同终点与成本语义，并让第三链保存完整结果产物，不能训练进程退出0就宣布评价成功。

### AJ05：在线RL，从随机/规则或已有模型开始

**来源/采集：** 不必使用Human。Agent在获授权环境产生rollout；若BC初始化，单独登记其Human/教师来源。采policy版本、实际条件分布/所需logprob、历史、请求/结果、奖励事件、时间跨度、控制干预和终止/截断。

**整理：** 学习层把真实事件编成decision windows，包含期间其他已知在途动作和公开反馈；奖励按时间区间归集而非把下一条HP变化硬归给最新按钮。独立TaskRewardSpec固定收益、时间折扣和结束含义；bootstrap需要可信next input，gap不是可随意bootstrap的普通time limit。

**训练：** 选定recurrent policy算法、on/off-policy条件、burn-in/状态重建、版本陈旧范围与更新周期。不同长度Await、子选择和输入重叠必须进入行为历史。规则覆盖后的实际行为概率若无法求，就不能假称满足要求准确logprob的算法。

**运行：** actor固定一版Agent采一段；learner产新包，通过安全边界显式切换/新segment；不是每条RPC在线修改一半权重。资源/用途/预算属于作业控制；game unknown核对与provider unknown分别处理。

**评价/修订：** 独立评估policy，冻结探索条件，报告总环境时间、样本效率、干预、崩溃/截断和胜负。A需要通用时间/事件与行为记录，不需懂PPO或奖励。传统step接口若必须等边界，可以用B facade，但其时间/曝光语义独立资格，不能宣称等价原生异步E。

### AJ06：离线RL或回报条件序列Agent

**来源/采集：** 可使用规则/旧Agent数据，不要求Human；需要实际动作、合格状态历史、回报、终止/删失和行为来源。只有N选择标签的片段不足以自动变成离线RL材料。

**整理：** 区分数据支持范围、未见动作和未知结果，按独立局划分；终点回报/return-to-go为target或显式训练条件。线上目标回报是研究设定的目标值，不能注入未来实际回报。自适应时间折扣与长度按TaskSpec固定。

**训练：** 可比较保守离线价值方法或回报条件生成方法，不从一次轨迹给所有候选赋同一Q真值。缺行为概率会限制特定校正/OPE方法，但不自动排除所有离线算法；每个算法列自己的数据要求。

**运行：** 与AJ01/AG03一样通过A执行，回报目标按声明规则更新；只选当前C。不能为了与训练分布一致请求游戏透露未来。

**评价/修订：** 离线拟合/OPE只在假设范围报告，必须另有授权闭环验证改进。要求dataset保留行为来源、时间和支持覆盖；A协议本身不承诺解决离线分布偏移。发现缺数据就调整采集计划或算法，而不是强行补伪Q标签。

### AJ07：AG03，直接用预训练生成模型，不在项目内训练

**来源/采集：** 不需项目Human训练集；仍使用模型供应方预训练产物和可能未知的训练来源。项目需要合成合同例子、允许的静态说明和独立任务评估集。手写few-shot与规则也是先验，须记版本。

**整理：** 按A当前公开事实生成prompt，长历史用声明截断/摘要；示例不可取held-out同局未来。静态规则库/检索资料若超出任务允许信息，属于另一个Agent/信息条件，不可暗加。

**构造：** 不反向传播也要封存模型/接口版本、prompt、工具schema、缓存/摘要策略、解码参数及固定等待组件。无法固定外部服务版本时记录该限制，不假称可完全复现。

**运行：** 生成结构，严格解析→当前C唯一Resolve→Submit；不可解析/no_match作为Agent失败或按已声明预算重新思考，不能自动选目录首项。纯解析失败且明确未投递可以重新生成；投递unknown不属于可重试生成。网络慢时事件继续进buffer。

**评价/修订：** 语法通过率、合法解析率、stale、任务结果、tokens/调用/延迟/成本分别测。要求A允许非全评分输出、支持局部别名和紧凑关系；若只能靠填假scores接入，端口设计需修改。项目零训练不等于数据零来源。

### AJ08：AG03，示范微调/教师蒸馏生成模型

**来源/采集：** Human或教师的真实语义选择加公开前缀；可用AJ07产生教师轨迹，但标teacher来源与版本。不要求有人类自然语言说明，不捏造Human思维链。

**整理：** 把chosen bound action映为规范动作tokens，保留完整对象关系/别名映射；同一步别名随机化若用作增强，要同步全输入输出。约束解码可用同一sealed C；标签不可在当前prompt或候选排序中泄露。

**训练：** 行为token N、合法序列约束和可选Await目标分别资格化；预训练初始化/LoRA或全量更新声明。评估teacher-forcing loss之外的自由生成；当前动作tokens的因果mask防止偷看未来。

**运行：** 生成→Resolve→Submit；不是每个token一个游戏动作。teacher-forcing时真历史与上线自己行动历史会分布偏移；需要新轨迹/教师标注时是额外获授权采集，不从已有轨迹编造纠错。

**评价/修订：** 测多步导航、生成错误恢复、对象一致性、Human/teacher差异和任务质量；单独对比约束解码成本。要求训练与线上共用renderer/schema及版本匹配，而不要求Connector懂tokenizer。

### AJ09：AG04，检索候选＋重排

**来源/采集：** 训练检索/重排可用Human、教师或Agent真实选择；无训练版本可用固定文本检索/规则，相应不声称学到偏好。数据必须带每步原完整C或可核验重建材料。

**整理：** train拟合编码器/索引参数；按原C构造对比项，未选项是训练对比而非已证劣动作。评估shortlist不强塞正确标签；训练若注入positive要披露与线上差异。

**训练：** 先召回后rank，或联合优化；具体k、负采样与hard-negative来源固定。异步建立当前catalog索引必须绑定版本，不能混用上一场景对象。

**运行：** A可给完整目录关系及分页；Agent内部检索一部分、重排后Resolve提交；漏召回/空检索的处理作为Agent规则显式记录。环境不把C改成shortlist。

**评价/修订：** recall@k、全量reference差距、索引刷新/内存/传输、真实任务表现一起测。大目录要求分页/delta与完备摘要；prefix只能查询C现有关系，不得成为训练目标提示或native预测器。10k合成示例不是STS2真实规模测量。

### AJ10：规则Agent、无记忆M0和诊断消费者

**来源/采集：** 固定规则Agent无需训练数据；需要公开字段说明、合同/场景fixture及闭环验证。M0若学习评分仍需合格选择数据，只是不要求历史作为模型输入，不能把它也归零数据。

**整理：** 规则版本与调参开发集固定；M0按run划分而非随机拆同局帧，避免从后续帧泄漏。规则中显式优先级也是策略，不塞到环境。

**构造/训练：** 规则直接封装程序；M0以当前页及C训练独立N，不复用M2 reset的名字掩盖图差异。随机合法策略可以作执行/覆盖诊断，随机种子和采样分布记下。

**运行：** 都从当前C选合法项并处理Await/Stop；不读取私有native字段。无记忆者也需请求账本、幂等/重连控制状态，这不等于学习型世界记忆。

**评价/修订：** 合同覆盖、正常/失败恢复与策略胜负分开；不能用诊断Agent差胜率否定协议。此类消费者要求SDK足够小、非神经也能使用，避免协议强制W、tensor、梯度或训练服务。

### AJ11：世界模型/搜索/模型预测控制Agent

**来源/采集：** 可用Agent探索数据而无Human；学习世界模型需合格实际分支/后继，规划器本身也可固定。或者使用独立已资格模拟Host，此时世界模型训练可省略，但模拟器有其源码/验证来源。

**整理：** 区分真实转移、模拟转移和模型想象，限定信息范围、动作/时间抽象与不确定性。真实未执行分支没有事实target；隐藏状态的模拟root不能偷偷变成当前策略输入。

**训练：** 实际分支训练公开动力学/观测预测，独立测多步误差；planner固定搜索预算、价值/停止方法。每条imagined branch持有私有临时状态，不能写实际W。

**运行：** Agent内部在模型中搜索，再只向主Host提交选中的当前动作；若用真实分支Host，需要声明clone/reset/seed与隔离能力，不能在玩家当前局试走后回档。

**评价/修订：** 同实际预算比较预测准确性、长程失真、搜索收益、算力与游戏结果。**A本身不足以提供可克隆世界**；保持标准Act端口可服务模型内规划，可选Simulation API另有任务/数据资格。若目标变成大量branch训练，另评Host而非强改主控制协议。

### AJ12：视觉/多模态Agent

**来源/采集：** 预训练VLM可零项目训练；微调需图像/时间/动作对齐数据，Human或Agent都可。现有typed trace不能还原从未保存的像素，文本转图片是合成输入而非原生视觉证据。

**整理：** 图像有捕获区间、帧/分辨率、遮挡/UI状态、公开范围与动作关系；图像与typed字段不同步要标明，不称原子配对。保护无关桌面/账号资料，不能为视觉训练录入额外私有信息。

**构造/训练：** 定义“图像＋完整逻辑C”辅助视觉Agent，或“纯视觉决策”的独立信息profile；两者能力不同。有监督微调/OCR预处理/预训练来源分别登记。

**运行：** 当前A若只有typed字段，拒绝声明支持该视觉profile；新增可选VisualFrame能力并验曝光同步后，可仍通过当前C输出动作。若任务要像素坐标鼠标控制，已超出A逻辑动作合同，需要独立协议与资格，不能消费者偷发坐标。

**评价/修订：** 视觉覆盖、同步偏差、带宽/缓存、识别和任务表现分层比较；给予逻辑菜单不能再称纯视觉。该案例直接要求承认A有范围边界：首发可明确排除真实视觉，同时保留扩展接口，不为“万能”提前建设所有图像通道。

### AJ13：分层/多模块协作的一个封装Agent

**来源/采集：** 高层可固定规则、预训练LLM或从Agent轨迹学习；低层可用Human/教师N。不同模块不一定共享数据，更不要求每个模块都训练。模块间计划消息若训练，也要记录来源和可用时刻。

**整理：** AgentInputTrace分别列高层摘要、低层公共前缀、决策权交接和命令作用范围；高层不能读取未来结果再向低层提示。共享模型/语料泄漏仍按整个Agent审计。

**构造/训练：** 可分阶段训练，或多组件联合；固定router也算Agent版本。选择“先拿奖励再走图”的宏计划只是内部目标，不能直接变成一个无复验的native复合动作。

**运行：** 只有一个对外请求/控制owner，内部委派结果按最新合法历史协调；每次子动作重新对当前C确认。并行顾问不能各自提交。同一单人任务的组合Agent不等于游戏多人支持。

**评价/修订：** 测模块冲突、重复请求、旧计划、接管、总资源及任务结果。要求SDK支持明确实例/状态边界，控制权集中在一个封装Agent出口；如果未来真要多玩家，需要新的actor权限/公开信息合同，而不是复用单玩家字段猜测。

### AJ14：离线分析器/评估器，不控制游戏

**来源/采集：** 使用有权限的记录/AgentRun/结果；可能训练失败分类或O预测，也可以纯统计，不必Human。evaluation数据用途不等于训练许可。

**整理：** 验完整来源、每项分母、接管/删失；读Evidence/Dataset/Run接口，不连接活跃游戏偷补历史。重复局/同源不同view归一组。

**构造/训练：** 统计器固定计算代码无训练；分类器则独立划分训练并登记目标依据。算法输出可疑root不改变原始record disposition。

**运行：** 作为批作业消费不可变artifact，产报告；无Act能力和控制lease。GUI打开报告不重新跑分析，也不自动启动训练。

**评价/修订：** 人工/独立来源复核抽样分类、分母正确性和重复执行确定性。要求第三链支持非训练/非游戏job类型；如果所有消费者都必须开游戏/持有控制权才能读记录，系统解耦失败。

## 5. 一张数据需求账，避免把“全流程”误读成“人人先采Human”

| 案例 | Human必需？ | 真正需要的训练/构造材料 | 评价材料 |
| --- | --- | --- | --- |
| AJ01 M2 N | 仅在人类模仿目标下必需 | 合格顺序选择及无N历史；可另选教师Agent | 按run独立示范＋实际任务 |
| AJ02 时机/动作 | 否 | 规则零训练；学习时机需可操作时间/删失，或RL轨迹 | 同Action的时机对照＋闭环 |
| AJ03 N+Z | 否 | N来源＋实际分支及有资格后继 | 预测与策略两类结果 |
| AJ04 O | 否 | 各自已知终点、覆盖计数、后续actor | 独立run/combat终点与闭环 |
| AJ05 在线RL | 否 | 自己的rollout/reward/time/behavior；初始化另报 | 独立冻结policy任务 |
| AJ06 离线RL/回报序列 | 否 | 已有合格轨迹、回报与支持范围 | 有限假设OPE＋闭环 |
| AJ07 预训练生成 | 否 | 外部预训练产物、prompt/工具说明；项目零梯度 | 独立场景/真实任务，不回流调参 |
| AJ08 生成微调 | 否 | Human或教师选择→规范tokens及完整前缀 | 自由生成与多步闭环 |
| AJ09 检索重排 | 否 | 学习式需choice+C；固定检索只需规则/编码产物 | 非注入shortlist召回＋闭环 |
| AJ10 规则/M0 | 规则否；M0按目标 | 规则/fixture或M0合格N | 机制诊断和策略评价分开 |
| AJ11 搜索 | 否 | 动力学轨迹，或已资格模拟器/规则planner | 多步误差、预算匹配闭环 |
| AJ12 视觉 | 否 | 预训练VLM或真正图像—动作对齐材料 | 同曝光条件视觉任务 |
| AJ13 组合 | 各模块不同 | 每模块训练/规则/预训练来源及通信合同 | 整Agent与模块消融 |
| AJ14 离线消费者 | 否 | 分析可零训练；预测器需独立标签 | 报告/标签复核集 |

训练数据、部署时的输入数据、评估数据是三份语义角色，不是三次随意复制。runtime发生的轨迹先是记录，之后取得用途并满足资格才可进下一轮训练。没有Human标签不意味着不需要数据来源、产物身份或评价。

## 6. 第二链与第三链：已有设计和代码，怎么接成一整套

### 6.1 第二链的具体用例

第4节每个AJ都是独立完整旅程；共用的应用入口可叫PrepareData、PrepareExperiment、Train、VerifyResult、PackageAgent、Evaluate。这是语义用例名称，不宣称已有同名API。每个用例接artifact refs与固定配置，输出operation/status/报告及不可变产物引用。无训练消费者跳过Train，不生成虚假checkpoint。

保留四层：RawEvidence → ProtocolTrace → AgentInputTrace → ModelTrainingInput/TargetBundle。Research owner决定N/Z/O/RL资格；Evidence只验原件。fit仅train；held-out/Gold和原use lineage不能因生成新view洗掉。训练worker不能修改Connector动作/游戏记录来消除loss。

### 6.2 第三链的三个完整落地例子

**OP01：本地玩家把新Human记录训练成M2，再在本机试用。** 用户选recording及用途→本地导入核对Human来源和bundle→审查目标A投影与排除报告→选train/dev分组和M2 recipe→资源/用途预检→本地worker训练→核checkpoint/result→导出Agent包→兼容注册→安全边界加载→明确Shadow/One-Step/Auto窗口→AgentRun与独立评价→用户决定是否保留。中途关GUI不等于训练取消；训练完成不等于加载成功；加载不等于策略合格。当前有这些单段实现，但A capture/投影/Agent recipe尚未接通，不能直接点旧M2按钮就说完成新链。

**OP02：团队使用共享材料，在远端训练，再把结果带回指定游戏Host。** 共享owner选择允许材料→验证/传输/索引→用途与split封存→TaskRequest带工作负载/输入/软件/资源和上限→人工或显式策略选ResourceOffer→持久化submit intent→provider handle→运行/检查点/日志→领域validator核结果→发布Agent manifest→目标机器核兼容/访问后下载→核本机Host与包→加载/运行/评价。网络提交unknown只核原attempt；取消先为cancelling，不能收到HTTP成功就宣称进程已停；重启继续同checkpoint需精确身份与显式resume。首版建议显式选资源，不先造通用智能多Host调度器。

**OP03：团队只分发已有Agent并查看评估，不训练、不上传私有记录。** 选有权限且兼容的Agent包→下载逐文件核hash/当前访问→注册→指定实例与控制窗口运行→保存AgentRun→授权后共享结果/报告→他人只读比较；升级按DistributionManifest检查组件/状态兼容，在安全边界切换并保留配对回退。登录不是自动上传历史，下载不是控制游戏，模型旧state不能塞给不兼容新图。归档只隐藏个人列表，不等于撤销、删除或停止已有训练。

### 6.3 当前可复用源码与未接通边界

以下链接固定在本轮核对基点；代码存在是source证据，不是这轮运行/服务资格。

| 接缝 | 固定来源 | 结论 |
| --- | --- | --- |
| 原件导入/验证 | [local_recording_import.py](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/70637a63a059c396b17db4a7ce8bb011bcfbfe7a/python/spireagent/workbench/local_recording_import.py#L198) | 显式Human attestation、打包和typed验证；不是自动research admission |
| 旧M2本地recipe | [memory_training.py](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/70637a63a059c396b17db4a7ce8bb011bcfbfe7a/python/spireagent/workbench/memory_training.py#L21) | observed-input、拟合tokenizer及有界CPU配置；不是新A全事件M2 |
| M2内核/现有trainer | [advance/score](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/70637a63a059c396b17db4a7ce8bb011bcfbfe7a/python/stpd/models/light_action_m2.py#L153)、[训练循环](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/70637a63a059c396b17db4a7ce8bb011bcfbfe7a/python/stpd/models/light_action_m2_training.py#L141) | 可复用共享W/候选只读；无N事件与新feedback管线仍需实现 |
| 计算provider | [compute.py](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/70637a63a059c396b17db4a7ce8bb011bcfbfe7a/python/spireagent/compute.py#L8) | submit/restore/poll/cancel接缝；不证明通用placement已实现 |
| submit unknown与cancel | [scheduler.py](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/70637a63a059c396b17db4a7ce8bb011bcfbfe7a/python/spireagent/hub/scheduler.py#L129) | 原attempt持久化与核对；不换ID掩盖未知 |
| 训练resume | [memory_run.py](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/70637a63a059c396b17db4a7ce8bb011bcfbfe7a/python/stpd/workers/memory_run.py#L522) | checkpoint/run/input/producer绑定；不等于游戏或Agent无缝恢复 |
| 分发当前访问 | [exports.py](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/70637a63a059c396b17db4a7ce8bb011bcfbfe7a/python/spireagent/hub/exports.py#L164) | inventory/payload重查访问；不证明所有离线副本与派生权重可远程撤回 |
| 归档 | [artifact_visibility.py](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/70637a63a059c396b17db4a7ce8bb011bcfbfe7a/python/spireagent/hub/artifact_visibility.py#L16) | 用户列表可见性；不可替代use/撤销政策 |

TaskRequest建议固定workload/输入产物/用途/软件契约/资源约束/输出validator/取消恢复能力；ResourceOffer声明设备、软件/Host范围、可用资源及成本；PlacementDecision记录选择与依据。复用现有journal/store，不把这些角色名字当已部署的三个新服务。库/CLI/API/GUI只调用同一用例；领域validator确认训练产物或游戏结果，scheduler确认作业状态，各自不能代签。

共享撤销需在G1明确对原件、view、运行中作业、checkpoint、模型、报告、已下载离线使用分别意味着什么；现有请求时检查不保证全面传播。第一版可选择在线复核/有期限离线许可等具体政策，但不能承诺远程擦除已下载字节。

## 7. 联合压力测试：不只评价模型分数

| 反例/压力（UT） | 受影响链/案例 | A系统应如何响应或调整 | 后续验证产物 |
| --- | --- | --- | --- |
| UT01 慢模型期间出现短暂预览并消失 | 交互/记录/AG03、视觉 | 有界事件与曝光buffer；不可仅latest Frame；溢出gap | 时序fixture＋真实capture/传输负载trace |
| UT02 目录很大、内容多数不变 | Host/AG04/资源 | sealed分页、内容缓存、变化版本；完整关系始终可访问 | 完备性/陈旧分页反例＋端到端profile |
| UT03 Act请求已到Host但回包断线 | 控制/AgentRun/恢复 | 核原request，保留unknown，不自动重试native | 故障注入请求账本与exact结果 |
| UT04 W已更新cursor未存时崩溃 | Agent/训练/执行 | 成对快照、合法前缀重放或显式新segment | 崩溃点恢复与在线离线等价检查 |
| UT05 Human只有点击无中间浏览 | 记录/M2/数据服务 | history资格拒绝或有限N用途；不能拼canonical补齐 | 按机制/目标的排除报告与新CaptureSpec |
| UT06 父等待selector，调度只认main-ready | Host/AG01/训练 | child-ready单独决策；不存在父完成前置 | child正常/取消/空选/父unknown例子 |
| UT07 奖励结果、summary、预算停止混成done | 数据/O/RL/任务 | game/combat/task/terminated/truncated分别定义 | 标签与控制终点矩阵 |
| UT08 teacher/检索/规则覆盖改变实际行为概率 | Agent/RL/记录 | 保存实际组合行为与可求条件；换适合算法 | behavior replay及概率验证 |
| UT09 云worker完成但产物缺文件 | 产品/学习/分发 | provider terminal≠领域验收；校验失败不发布可运行包 | 损坏/部分上传/重连验证 |
| UT10 撤销共享后已有模型被下载 | use/训练/产品 | 显式派生与离线政策，不能靠archive装撤销 | 权限与lineage案例及非承诺 |
| UT11 换Host/协议同JSON但暂停或曝光不同 | 三链全部 | 新profile/资格及投影；不静默当等价 | Host conformance＋匹配条件比较 |
| UT12 想做视觉、搜索或多玩家而当前Host不具备 | 三链/扩展消费者 | 接受能力拒绝；可选扩展或独立profile，不伪造输入 | capability矩阵与明确范围 |
| UT13 无训练分析器也被迫启动游戏/持控制权 | 执行/数据/AJ14 | 离线artifact服务与游戏控制解耦 | 无游戏/无control权限用例 |
| UT14 日志/落盘慢卡住游戏线程 | Host/Recorder/存储 | capture热路径限额、异步持久化、buffer满显式质量下降/停止 | capture、队列、落盘时延和缺口实测 |
| UT15 训练包能加载但renderer/profile不一致 | 三链/所有学习Agent | 加载前兼容检查、拒绝错误配对，旧包可回退 | producer/input/Agent/Host身份反例 |

测量需要分解capture、编码/目录、传输、组件consume、模型、Resolve/Submit、落盘，以及队列深度、p50/p95/p99、峰值内存、恢复时间和数据排除比例；同时报告完整任务与失败分母。首先收小规模测量再定资源/延迟门槛，没有真实测量前不声称A最快或最稳定。吞吐不能靠丢事件、不落记录、裁候选或去掉unknown获得。

公平比较至少三组：同A合同的简单M2/生成/检索；同Agent在本地/远端绑定下；显式不同曝光/时序profile（A与B/纯查询等）。前两组控制信息和任务，第三组承认信息/时间是自变量，不能把差异全归模型。协议质量还包括实现复杂度、变更影响范围、独立测试性和不支持时是否清楚失败。

## 8. 是否应该改成多个协议，以及当前建议

| 取向 | 何时合理 | 本轮判断 |
| --- | --- | --- |
| A事件主合同＋Agent自有时机 | 异步输入、不同内部模型、需要真实事件历史 | 已选主方向；按六接缝补强并检验，不宣称最优 |
| A上稳定调度Agent | 先服务M2 N，简单可解释 | 首个建议消费者；保持全部声明事件消费，不是第二协议 |
| 外部B边界facade | 固定step库、明确边界数据实验有真实需求 | 同底座可选，但改变曝光/行动时点；独立profile/投影/评价 |
| 直接信息查询/预聚合profile | 希望减少UI动作且愿意定义公平信息/时间成本 | 历史候选保留；若未来数据/成本显示优势可复议，不暗装进A默认 |
| Visual/Simulator扩展 | 实际要多模态或可分支世界 | 先能力声明和资格边界；不要求首版全部实现 |
| 独立坐标/多人协议 | 目标本身变成像素控制或多参与者权限 | 真实语义变化，另立方案；不借“一个系统”强迫一个schema |

建议保持一套主要系统、一个首发主交互合同、一份共享证据与产物体系；支持有版本的能力扩展和少量必要profile。避免两个极端：每个Agent重造环境，或者为了口头统一让一个接口含尽所有隐藏特例。是否新增profile由实际不兼容语义决定，不是按模型名称划分。

## 9. G1之前具体还没完成什么

G1是**接受可实施设计**，不是要求先完成E1–E6和真实训练。用户本次只确认A主方向，未自动确认其他取舍。以下均给具体下一交付；不是再次询问已经确定的A。

| GD | 当前状态 | G1需封存的决定/材料 | 之后才做的执行验证 |
| --- | --- | --- | --- |
| GD01 主方向 | **已确认A** | 在接受记录写明事件合同、Agent自有时机；可选B非首发必做 | E1/E3实际端口 |
| GD02 首发范围 | 建议Defect标准A0、单人基础游戏、首局内选择至summary退出，未接受 | L01–L64与内置特殊机制范围/不支持政策；旧产品目标逐项保留/延期 | G2小链、V1完整范围 |
| GD03 信息/事件/Host | 蓝图＋本轮六修订候选 | 必需字段/事件集合、质量、曝光、buffer/gap/时钟、native边界与完整C合同；Host conformance清单 | E1原生/并发/网络实测 |
| GD04 Human capture/转换 | 原始缺口已明确，新CaptureSpec未冻结 | 按机制的seam/输入/中间曝光/父子/终点矩阵；T/R/H/D、choice/history/Z/O资格和旧数据处置 | E2实采与真实语料统计 |
| GD05 首个Agent | 建议AG01 D-M2 N，未封存recipe | 内核复用边界、renderer/consume/reset/state、TimingPolicy、全量C输出/错误/恢复合同；无N事件trainer设计 | E3实现与数值一致性 |
| GD06 学习与评价 | N优先、Z/O/RL分用途的设计已列 | 本版实际实现目标、次序、数据来源、use/split、主要评价结果、未实现扩展；实验数值参数留具体run冻结 | E4训练与评价；不要求所有AJ都训练 |
| GD07 第三链 | 单段源码存在，A接线未实现 | TaskRequest/ResourceOffer/PlacementDecision首版字段、显式placement、job/admission/result validator、取消/恢复与CLI/API/GUI一致用例 | E5/E6本地/远端/产品路径 |
| GD08 共享/发行 | 已有用途/访问/分发机制；全面派生撤销未定 | 原件/view/作业/模型/离线各政策、包兼容、支持Host、升级和配对rollback | 产品授权下实测 |
| GD09 工程性能与失败预算 | 无本轮实测基线 | UT01–15每项owner、测量计划、预算制定方法和必须零容忍的语义错误；不虚构毫秒目标 | 后续限定资源下定量门槛与验证 |
| GD10 整体独立审查/接受 | 本文需审查；G1保持未接受 | 19任务不改，需求/旧目标/场景/案例→合同→责任→测试矩阵；明确接受/退回和执行授权范围 | 按获批E包实施，不默认训练部署 |

不能留到实现者随便决定的是语义、信息/来源、责任和失败/恢复承诺；可以在具体实验前冻结的是batch size、learning rate、样本预算等实验配置。性能绝对值在没有测量时可接受有界测量/回退计划，但不能用“以后再说”掩盖内存无上限或丢记录才跑得动的设计。

建议下一最小设计收敛包是GD02–05和GD07–09的合同/失败矩阵，随后完整G1审查。实施先共享底座＋AJ01一条真实链，并用不评分的AG03 stub检验端口和AJ14检验解耦；案例矩阵用于验设计，不强制第一版训练14种Agent。Z/O按各自数据资格增加，RL/视觉/模拟扩展按真实需求进入后续包。

## 10. 外部方法依据与本轮证据界限

- [DAgger原论文](https://arxiv.org/abs/1011.0686)：策略行动会改变随后遇到的数据分布。本设计据此要求示范拟合之外的闭环评价；没有声称已采用DAgger或安排新Human标注。
- [CQL原论文](https://arxiv.org/abs/2006.04779)：离线学习存在数据与策略分布偏移问题。这里据此要求AJ06披露支持范围，不把离线Q拟合视为新策略已变强。
- [Decision Transformer原论文](https://arxiv.org/abs/2106.01345)：回报条件序列模型是可选消费者，因此轨迹/回报与在线目标条件需分开；不代表本项目已实现该模型或保证其STS2效果。
- [Gymnasium 1.0 Env合同](https://gymnasium.farama.org/v1.0.0/api/env/)及[time-limit说明](https://gymnasium.farama.org/v0.26.3/tutorials/handling_time_limits/)区分termination/truncation；本系统进一步区分game/combat/task、gap和接管。借鉴语义而不把异步STS2直接宣称普通MDP，也不把诊断info里的私有字段送入Agent。

本轮只有源码/设计核对及文档验证；沿用的25项合成Agent参考检查仍限原[脚本与回执](BASELINE_AGENT_PROTOCOL_BLUEPRINT.zh-CN.md#11-本轮可执行参考程序验证了什么)。本轮新增AJ/UT是可审查案例和后续验证要求，不是新增已执行测试。没有实测吞吐/时延、可用Human样本数、新模型能力、云/GPU或游戏资格结论。
