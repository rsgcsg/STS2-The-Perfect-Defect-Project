# 当前基线实施规范：完整 G2／V1

版本：v1.5 工作稿，2026-10-09。**实施与验证进行中；完整 G2、V1 均等待用户最终批准。** [执行与验收矩阵](plans/BASELINE_G2_V1_EXECUTION_2026-10-08.md)管理本轮工作；[原任务表](plans/BASELINE_TASKS.zh-CN.md)保留唯一 19 个 P/E/G/V/R 编号。

本文件区分上层不变量、所选交互、具体消费者与验收。历史提案供查证，明确继承的需求不因文件成为历史而取消。冲突在这里和 owning 合同中修正，不通过不断叠加“其余不冲突部分仍有效”解决。规范描述目标及实施选择；实际支持范围看逐项证据。

## 1. 本轮范围与批准权

用户要求完整收敛、补齐、实施、验证和独立评估 G2/V1 及其全部前置工作，完成后交给用户审批。负责人继续自主处理已授权的必要修复与运行；**只有用户明确说通过，才能将完整 G2/V1 标为通过**。源码审查、负责人认为材料可交付、Git 合并都不是最终批准。

此前 `4256f952` 版本及 `ACCEPTED_G2_S0_ENGINEERING_LOOP` 只证明限定 text-menu-v2 兼容实例：封存读取、结构 M2、Agent 示范、小训练和原生出牌/停止。其原件和接受范围不改义；不扩展出 S1/S2 主任务编号，也不把局部证据覆盖到整个 G2/V1。

保留 REQ-01–12、SC-01–16、SX-01–21、L01–64 全部分母，以及产品 A–H 的 G2/V1 义务。单人基础游戏、全部基础角色 × Ascension 0–10 是功能范围；Defect A0 先验证，其他角色须有对应证据。多人、任意第三方 Mod 和未经资格化的新游戏版本不在既定首版范围。教程默认在隔离管理准备中关闭，保留意外出现和专门测试路径。

G3 冻结、R1 正式发行、R2 规模科学比较仍按原定顺序；它们需要的 G2/V1 前置能力、接口、迁移与验收设计必须补齐。约 10k 合格交互的真实训练尝试、独立 K/Reset、backbone、N/Z/O 有界比较是保留的 Stage1a 义务，映射到本轮 E4/V1 的实际证据；不统一推迟到 R2，也不以小训练宣布完成。最强模型是研究目标，工程门不预先承诺胜率。

必要游戏生命周期、隔离准备、安装、GitHub 和部署授权继续有效；累计 model/train/API/GPU 付费上限仍为 20 USD，前包支出 0，不是每个 worker 各有一份预算。共享存档、旧任务、凭据、数据/use/Gold 保持各自 owner。AI 可代执行功能步骤并标明实际来源，不能冒充 Human。

### 设计调整的依据与当前选择

真实需要与整个 Agent 的有效能力决定设计。信息取得、记忆、推理和复杂度可以在协议、
共享服务、Agent 和模型之间重新分配，比较任务能力、泛化、成本和日常操作负担；
不只优化一次模型调用的准确率。保留公平性、真实来源、用途和证据边界，具体协议与实现
可以修订。一个清楚、合法且经过验证的运行模式足以作为首个默认，不要求先支持所有组合。

本轮选择及理由直接记在本规范；具体实现/验收位置只在执行矩阵维护。后续发现反例时
修改此处和 owning 合同，说明受影响消费者与迁移，不能靠继续叠加旧规定解决冲突。

| 决定 | 需要与理由 | 实现边界／复议条件 |
| --- | --- | --- |
| 默认研究候选改为当前决策采样、段内 learned carry | 先取得当前完整公开状态/C，同时让模型学习浏览、重访和进度；逐决策清空 W 会抹掉该任务信息 | **选定模式**，源码与验收进度由执行矩阵维护；旧 publication-memory 包保持旧义，真实样本/原生序列证明后才能成为可用默认 |
| 记忆从实际消费历史学习 | Map→Inspect→Map 可以同页不同历史；固定 visited list 不能冒充模型能力 | I/F-off 为初始选择；只有真实反例和明确 InputSpec 才增加其他输入，不在执行器暗补策略 |
| 训练视图可以重表达、截断或增强历史 | 原始记录、训练曝光和部署曝光不必逐项相同；不同有用假设可以实验 | 必须声明输入/目标/分段/重置和差异、保留分母，不能宣传为完整 Human 注意力或部署逐步重放 |
| 先交付一条方便复用的实际路径 | 保存数据、选择已训模型、看一次建议和控制游戏是有明确目的的用例 | 同一应用 owner 和能力投影；已有安装/登记/数据不因再次使用重做，训练和操作仍有明确意图 |

这些选择不等于已实现、已资格化或用户最终批准，也不删除完整范围内待验的机制。
100–300 个合格选择的小批学习和一次自然整局尝试先给反馈；不把全部最终门禁变成
第一次实用训练的前置。约 10k 和已约定有界比较继续按实际数据与成本完成。

## 2. 上层抽象与唯一责任

整条链可以理解为：游戏决定实际发生什么，Host 能力合同连接具体游戏，Connector
把允许的公开信息与操作实现为交互合同，完整 Agent 据此取得信息并行动，Model
承担其中的计算。记录保存实际发生的事实；研究把记录变成有声明的输入与目标；
应用组织任务、资源和产物。一个用户可以只录制、分析或运行，不必每次走完整条链。

| 层／对象 | 负责的事实 | 连接与边界 |
| --- | --- | --- |
| 世界 | 规则、效果、随机性、原生执行与 Commit | STS2 是当前参照；其他 Host 按具体协议/范围验证，不能推断全世界等价 |
| Host 能力合同 | 公平可捕获事实、精确操作、时钟、实例与支持的管理能力 | Native Foundation 提供语义/生命周期 seam，Connector NativeUi 绑定实际公开 UI；Host Runtime 管进程、隔离与恢复 |
| 公开交互合同／协议核心 | 信息范围、完整操作关系、版本、事件、读取与失败含义 | Connector 保存公开投影、exact bindings、immutable bytes；不含模型或研究策略 |
| 面向 Agent 的环境接口 | 读取、接收公开事件、结构查询/选择、提交、等待与控制 | Connector SDK 等提供交互能力，服务全量评分和不评分的消费者 |
| 完整 Agent | 取得、表示、状态、时机、选择和依赖组合 | 对外交付一个版本化个体；runner 不在外面偷偷补信息或替选 |
| Model | 明确输入/输出、计算结构、固定或学习参数及可选状态转换 | 可以表示、预测、评分或生成；不直接持有 native operand、私有存档或控制权限 |
| 记录与验证 | 实际捕获、来源、顺序、输入见证、结果和缺口 | Annotator/Evidence 不产生行动权威、Human 来源或研究准入 |
| 数据与研究 | 投影、假设、用途、划分、目标、训练和评价 | STPD 消费公共合同及原件；未来标签与输入分开 |
| 应用与资源 | 用例、作业、权限、流通、恢复、安装与展示 | 复用现有 store/use/operations/membership；CLI/API/GUI 调用同一 owner |

这些是逻辑角色，不要求分别部署服务。环境实例是具体世界、Host、交互 profile
与起点的一次组合；相同 JSON 不证明两个 Host 行为等价。这里不用 H 简称 Host，
历史 Human 输入观察 H 保留原义。旧设计的 P/A 是协议核心/环境接口的示意，
不是新增组件，也不等于历史方案 A 或动作集合 A(S)。

面向 Agent 的环境接口与 Runtime–Agent 程序端口分开：前者由 Connector 提供游戏交互能力；
后者是 Policy Runtime 与 Agent 程序之间的消息合同。当前通用
[Agent 端口](../components/policy-runtime/docs/AGENT_SESSION_PROTOCOL.md)支持消费、查询、
Act/Await/Abstain/Close，scores 可选；旧评分端口仍按旧 Manifest 要求完整 scores/index。
改变端口传输形式本身不表示改变游戏信息或操作语义。

完整 Agent 可以由固定规则、一个或多个 Model 与共享库构成，也可以整体作为模型
描述；不要求所有 Agent 经训练或所有组件联合优化。策略是取得、调度或选择的规则，
可以固定或学习。共享库实现分页、校验、去重与传输；AgentSpec 声明使用能力的策略。
环境适配器、模型输入适配器和端口适配器各说明自己的职责，不能用“adapter”隐藏
自动浏览、摘要或替选。训练目标由研究 TargetSpec 固定，不是每个 Model 的定义条件。

任务 Runner 可以在明确 TaskSpec/权限内准备或管理 episode，组合进实验 Agent；
普通局内策略没有任意重开、回档或读取隐藏状态的权限。Policy Runtime 管授权、
消费水位、投递、预算与 Stop；Host Runtime 管游戏进程。这些责任不因名字变化合并。
较早三条责任线及职责分配的理由见[设计沿革](design/BASELINE_DESIGN_HISTORY.zh-CN.md)，
具体目录与依赖见[组件](COMPONENTS.md)。名称调整须消除真实耦合，不为风格重写组件。

共享抽象须由默认结构 M2 和另一实际查询/结构请求消费者共同检验。历史合成例子只能提供反例。新增机制扩 owning adapter，不另建模型仓库、原生动作队列、许可账本或因果 tracker。

## 3. 默认交互语义与场景范围

完整默认采用**原生逻辑操作与公开事件**。当前已进入页的公开内容、实际对象关系和适用操作可以取得；同一逻辑列表免像素滚动。未打开详情/提示、隐藏牌序/RNG/未来分支不能提前提供。必要合法信息须通过当前观察、允许的浏览/查询或合格历史可达。

当前完整关系由 Connector/native owner 产生，不是全局对象动词乘积或模型 shortlist。text-menu-v2 虚拟选牌/目标 cursor 保持兼容身份；不能改名当原生 Begin/Focus/Unfocus/Confirm/Cancel。真实预览、取消、返回、重访及子选择机会必须保留；传输分块和目录索引不是游戏动作。

[L01–64](design/BASELINE_LN_V1_SPEC.zh-CN.md#3-第一版必须提供的动作与信息清单)和[SX01–21](design/BASELINE_SCENARIO_SPEC.zh-CN.md#3-逐场景动作与信息设计)的操作/正常反例清单被本规范明确继承，详见执行矩阵；其中历史状态句不作为当前状态。L64 管理退出不赋予策略随意放弃/重开权限。基础游戏新发现的特殊机制须补映射，不能删分母。

时机采用 owner-ready：当前 owner 有完整原生输入机会即可决定，child-ready 不等父效果全结束；输入可以进入原生队列。输入投递、执行、取消、Commit 与 causal successor 分别记录。不能用固定 sleep、后来的交互状态或通用 Task 完成证明效果结算。

逻辑状态和 UI 呈现可合法不同。动态费用、星费、附魔、目标预览、orb 等保留实际来源，不能把逻辑值冒充当前标签。原生控件 enabled/owner 决定可投递；raw selector min/max 是请求事实，不能额外造确认规则。缺必需关系明确 partial；合法差异本身不推导 settling。

终局区分 `game_outcome_known` 与 `agent_task_complete`；后者在任务要求的 summary/返回菜单完成后才成立。局结束后仍可有当前 summary owner 与导航，不能无条件清空目录。预算停止、断线、未知和进程关闭是任务删失，不是自然胜负。

本轮默认 TaskSpec 为一次标准局至合格、ready 的公开终局总结页，胜负均可自然完成，不要求返回菜单。完整 Agent 在已确认消费的该观察上 Close，保留原 C/context，不为返回菜单制造 N 目标；其他任务终点须另行版本化。TaskSpec 的源码实现、包内组合和实际原生终局证据分别验收。

## 4. 读取、事件和操作合同

新 profile/wire 由 Connector owning 合同单一维护，先核具体字段/样例再实现。旧 snapshot/text-v1/text-v2/S0 的字段、数据和资格保持原义；新客户端无静默 fallback。

| 接口能力 | 必须保证 |
| --- | --- |
| Describe/Attach | 明确 profile、generation、资源、时钟、保留和控制能力；不自动授权或宣称完整前史 |
| ReadCurrent | 请求时取得当前一致 capture；expected basis 不符明确 stale，不补造旧 notice payload |
| ReadSealed/GetFull | 只读指定冻结值，验证同版本、字节、覆盖与 hash；不再次调用 live getter |
| Events(after_cursor) | 有序事实、watermark、覆盖/gap；当前状态不得补成缺失历史 |
| ListActions/AllowedNext | 同一完整有限关系的分页/结构前缀查询；不做策略筛选 |
| Resolve | 结构表达唯一绑定原目录成员；no-match/ambiguous/stale 不猜测 |
| SubmitOne | 原 basis/request/control 授权与 exact leaf 执行复验；unknown 不换 ID 重发 |
| QueryRequestResult | 核原请求真实结果；不能用当前世界倒推旧请求成功 |
| Await | 从原 cursor/clock 核已有事件再订阅条件/期限；可被 Stop/撤权打断，timeout 不伪造观察 |

source notice、capture、publication、Agent received、Model consume/advance 分开。重传不更新 W；同正文的真实重访和 owner/focus occurrence 不能被内容 hash 抹掉。新事件 InputSpec 须同时更新记录、训练与在线端口，不能把 S0 未 offer 的 capture 填回旧前缀。

正文区分公开观察、捕获、渲染帧与模型消费步。Frame/StructuredFrame 是既有协议或
研究数据类型，默认不是屏幕截图或视频帧；capture 冻结其声明范围的公开内容。
ReadCurrent 在请求时捕获，ReadSealed 读取原冻结值。目录查询不等于原生打开详情，
一次提供不等于已经消费，消费 ACK 也不独立证明数值记忆。一次选择、输入投递、
原生执行、Commit 和后果分别计数；Receipt/Result 不因附即时观察就成为因果后继。

短暂且承诺保留的 focus/preview/selection/owner 变化须在确切 seam 冻结 required 公开值。无可靠捕获依据的路径不称完整。可延后大资料只给 source notice，按需读取形成实际当时版本；已冻结内容可共享、分页和延迟编码。全量输入的 global/page/entities/C 必须一致，或有明确的依赖未变证明。

事件、payload、catalog、cache、pin、等待各有数量/字节/期限上限。丢失、过期、溢出和断代明确 gap；正常声明负载的 required gap 是验收失败。静态缓存和 dirty 跳扫须有依赖依据，相同 hash 不能证明没有扫描。控制/停止不能排在昂贵构建后无限等待。

## 5. Agent、Model 和状态

选定的默认完整 Agent 组合当前决策采样、共享取得库、段内 learned carry、结构 M2、选择和受限 Runtime；以下为目标合同，现有 full-reference 包尚未因此改变。I/F 关闭：自身 request/receipt/reason/control/动作日志不改名进入 P/E/W；当前真实 HP、selected、focus、公开总结仍可输入。独立 receipt 不触发 W。

完整端口支持无动作观察消费和 Act/Await/Abstain/Close；scores 是可选诊断。旧评分端口按旧 Manifest 保留。不评分/生成式消费者仍从同一关系 Resolve，不能生成 native operand。程序侧固定时机策略属于 AgentSpec，不冒充模型学会时机。

新 Teacher 与取样 Student 选择一项共享的固定 Map 时机策略：只有实际公开观察严格为
`native_map` / `native_information_page`、内容 schema 为 `map_navigation-1`、surface
为 `map_navigation` 且 `traveling is True` 时，在原输入已知 ACK 后 Await 同一水位/游标，
按既有 250ms 上限重新检查。此时完整 C 仍可含合法资料操作；本 Agent 明确放弃旅行期间
这些信息机会，以等待下一次公开观察。策略不改 C、原生合法性或 Runtime 控制，不用
缺路线、disabled travel、空 next_options 或父效果 pending 推断通用等待。

该 changed、完整且非空 C 的观察照旧消费并推进一次 W；未变/空 C 检查不额外推进，
ready-summary Close 优先。Await 不产生原生 Wait 动作或 N 标签。当前 N 只预测 C 成员，
不能要求它学出未表示的 Await。新 Student AgentSpec 1.1.0 与 Teacher 策略/Agent 身份
明确登记固定时机；目标、summary 和删失不变，新的 TaskSpec 1.1 仅声明此导航组合。
旧 generic/full-reference 与 TaskSpec 1.0 保持原义，旧包继续绑定其原 runner。InputSpec、
特征、K1/D96 图、权重格式、carry/reset 和 N 目标不因该策略改义。其他 pending 机制仍按
各自公开事实与 Agent 策略处理；本选择不是全场景 readiness 引擎或已完成运行资格。

结构模型复用 typed 对象/字段/关系、共享 byte encoder、当前实体与持久 W、候选只读评分。opaque ID、时间、seed、lease、当前标签不是可学习输入；公开顺序与任意 ordinal 分开。新 profile/InputSpec 产生新投影和包身份，旧权重不自动兼容。文本 LightAction 保留为配对参考，不阻止结构模型早期实现。

M2 是持续状态的研究角色，不是一个统一模型产物。Text/LightAction M2 的页面 token
与动作编码器、当前 Structured M2 的 typed tree/局部字段编码/稀疏关系与结构候选评分
是不同计算图；还须固定 K/d、InputSpec、取得历史、reset 和权重。名字相近不能把一条
路线的结果覆盖到另一条，也不能把 publication-memory 与当前取样改称同一种消费历史。

默认 learned Agent 要有全部 mandatory 主要机制族的自主进入/接续/退出及连续自然任务证据；环境可执行、训练见过、实际到达和策略质量分开。teacher/脚本可诊断或采集，不能在 learned 运行外壳暗中替选。奖励循环须修真实输入/监督/策略原因，预算保护不能冒充任务完成。

Agent 状态恢复绑定模型/InputSpec/profile/generation、已消费前缀和状态版本。重连核原请求及实际历史；gap、不同权重/环境或不明位置不能无缝恢复。新租约不使旧动作有效。模型进程恢复、游戏存档恢复、训练 checkpoint 和应用任务恢复各自声明能力。

## 6. 记录、来源、转换与目标

记录原生键鼠/UI 或程序协议交互时，保存操作者声明与输入入口：人操作（本人声明）、
机器操作原生 UI、机器通过 Agent 协议或未知。现有 `declared_human`、`agent_native_ui`、
`agent_protocol`、`unknown` wire 值保持；actor_id 是记录内的关联代号，不要求真实姓名或
新增身份考证。切换有实际 segment 边界，未知不倒推；输入 seam 不机器证明人类身份。
AI 功能录制不能填 `human_origin_attested=True`，旧 Human bundle 保持既有验证规则。

原始记录、Evidence 验证和研究数据视图是不同产物。Source3 保存声明范围的公开捕获
与输入事实，默认不是视频录制；其 publication-memory、recorded-capture 预训练及取样
视图保留各自身份。数据准备中的 cohort 是目标标签来源筛选，默认 declared_human
不判定原件由人操作；机器来源须显式选匹配 cohort。验证完整性不自动授权训练，
应用成功预留 train 分组也不表示已经拟合或登记实际 TrainingUse。

在线客户端与 Annotator 复用 Connector 公开冻结事实。observation、native input witness、delivery、Commit/causal、持久化位置和来源各有身份；新事件流不建立第二个 causal tracker。focus 输入前观察不能包含之后 preview。公开可得、实际显示和心理知识分开；无动作观察不伪造 Wait 标签。

ProjectionSpec 固定原件、目标 profile、字段、行为粒度、取得/消费、reset、假设、mask 和排除分母。允许明确有损/重表达监督，不冒充保真 Human 时机模仿。旧数据可只适用局部 N；缺浏览/preview/前缀时不补完整 sequence。用途/Gold/lineage 继续由既有 owner 判定。

已实现 Source3 publication-memory 视图仍只用有序公开 publication 推进 W；原始输入前 basis 与前一必需 publication 精确匹配才提供 N。显式 recorded-capture 预训练视图保留独立 recipe 和曝光假设。两者的旧身份/准入规则不因新方向改变。

默认采样视图按可核验顺序消费原始 pre-input basis，保持完整 C，段内携带 W；有可靠 basis 但无合格 N 的观察仍可作为上下文，不能只拼接有标签行。它将输入前状态重表达为 Agent 决策样本，不声称 Human 曾逐步消费这些输入。在线实际 offered/ACKed 样本及无输入样本须由既有 evidence/storage 路径保留足够原始内容以核对；不能凭哈希或输入行推造缺失 ModelInput。

采样的完整公开字段/C、NativeUnit 重复/重访规则、carry 递推和明确 reset 规则须在线/离线一致验证。连续重复取得不重复推进 W；消费中间页后返回同页不能按全局内容 hash 删除。当前采样允许不对应 publication index，但必须用新的获取/历史合同，不能伪造旧 publication。第一版不消费不完整或空 C 的等待检查；合格 ready-summary 可作为无 N 的最终观察。其余曝光差异在 ProjectionSpec/AgentSpec 中明确。

真实缺失输入、不可验证 basis、actor 切换、暂停/接管、环境断代或不明 Model 状态切开采样段；后续可靠片段可明确从 W0 开始，保留整局/use/split 关联。只缺 advisory publication 而输入顺序/basis 仍独立成立时，不能机械当作采样史缺口；原件完整性/身份失败仍拒绝。TBPTT 截梯度不清空 W。unknown 请求或控制释放必须先由既有 owner 处置，不能用冷启动逃避未知重试规则。

第一实用采样实例可以不支持跨进程 W 恢复，段内仍有记忆；明确新段从当前公开观察开始，不能显示为恢复原记忆。产品同 W 保存/恢复保留独立验收，既有库能力不等于 GUI 已可用。采样视图、新 recipe/包/运行时与数据路径未共同验证前，不能按旧 recipe 接纳或自动替换旧模型。

划分保存来源组、共同起点/模板、重复关联、角色/场景/长度/候选分布；不同 seed/process/run ID 本身不证明科学独立性。训练拟合、开发选择、测试用途分别登记。N/Z/O/RL TargetSpec 与模型输入分离；直接事实型 Z 只用有合格后果依据的实际分支；推断、假设、teacher、合成或弱监督目标允许另设 TargetSpec，固定生成方法、所据事实、置信/适用限制及评价办法，不改原件或冒充实测反事实。O 固定 metric/unit/horizon、known/censor、continuation policy 和来源。E4/V1 除接口、资格和反例，还完成原定有界训练尝试/比较并报告数据真实不足、负面结果与成本；缺 required 数据或未执行比较保持未完成。无需全笛卡尔积或每变体一万条，科学优势及进一步规模研究仍属 R2。不能伪造标签补数量。

## 7. 学习与共同应用服务

同一 source→use preflight→input/run→attempt→checkpoint/result→Agent 包→注册→实际运行→报告保留全身份链。复用 ArtifactStore/RunReporter/CurationLedger/operations/registry；索引不代替原件或任务终态。

一个 training service/journal 负责 operation/attempt。静态可信 recipe/Agent 注册选择代码内准备、执行、验证、导出与加载适配；请求或下载 manifest 不能指定模块、shell、任意程序。legacy/token/memory/structured 保留旧身份，公共 owner 不复制全部领域编排。研究验证不进入 storage 或 UI。

新 checkpoint 在安全边界保存模型、optimizer、RNG、累计步数、数据位置、W/reset/历史及指标，绑定 run/input/config/producer。v1 final-only checkpoint 不自动变成可恢复产物。resume 显式创建 attempt，先核原执行停止、用途和累计预算；unknown 先 reconcile 原 attempt，不隐式重训。cancel ack、worker terminal、领域验证、selected result 不混用。

低质量数据预训练后转入新数据是一个新训练 run，可显式从旧模型权重初始化，保留父模型、原/新数据与用途链；默认重新初始化 optimizer、RNG、数据 cursor 和 W。不能把不同数据或目标塞入旧 checkpoint resume。此能力须由现有训练 owner 实现并单独验证，不因设计允许就宣称可用。

库、CLI、API、外部和游戏内工作台调用同一用例：采集/import/verify、固定数据、prepare/train/cancel/resume、分析、export/download/register、load/run/pause/end/report、环境与场景。GUI 读能力和状态，不猜模型 schema 或自行启动子进程。独立训练/上传不因关面板/退出游戏消失；游戏绑定推理按 exact 实例停止。

## 8. 本地、团队、远端及恢复

本地基本工作不依赖团队登录；登录增加授权数据、模型和指定计算资源。首版显式选择本地及一个兼容远端资源，不要求智能通用调度器。远端 intent/handle/取消/验证、权限失效、离线、撤销和下载均须真实验收。撤销不承诺擦除已下载字节；派生使用按实际 lineage/grant 判定。

环境管理提供实际支持的 seed、新局、原生存档、场景与重放能力，各自注明边界；同 seed 重开不是任意中途 checkpoint。私有存档/RNG 不进策略。Managed/其他 Host 按声明能力独立验收、对照，不能因 JSON 兼容继承原生资格。

游戏内核心任务须可发现、操作、监测并返回结果，外部窗口保留同一任务上下文。独立后台服务、配置和登录沿原 authority；界面内不运行训练。安装、二次启动、更新、配对回退与数据保全有 actual-user-path 证据；单个浏览器首页链接不满足整个工作台要求。

## 9. 实施、验证与提交审批

优先跑通小规模真实录制→训练→实际 Agent，再扩大至约 10k；小样本可靠前不要求 Human 大量重录。其余必需机制和最终验收继续推进，不作为首次限定训练/运行的统一前置。

先恢复完整要求台账并核事实，再封共享合同/样例；E1 native、E2 capture/data、E3 Agent、E4/E5 学习/资源和 E6 展示按稳定接缝并行。真实反例否定抽象时先修 owner/消费者合同，再修代码，不靠私有字段、旁路 UI 或未知重试补洞。

G2 提交真实可追溯链及必要负路径；V1 同时验环境/记录覆盖、默认 Agent 自主性、产品/本地远端/恢复及实测成本。每格分别写 source/test/build/installed/loaded/runtime、capture/data/learning、失败/未知和 reviewer。全部补齐才标 `ready_for_owner_review`，用户批准后才标通过。

保留原方案的候选稳定性规则：冻结版本，预登记 30 次连续 attempt，保留全部结果；系统缺陷或未修复循环/挂起导致的失败不算通过。自然胜负与预算删失分开，scene-start 与 from-start 分开，不拼片段冒完整局。这是有限工程检验，不宣传 99%/99.9% 可靠性。

健康本机控制反馈目标 250ms、无在途 native 提交时释放目标 1s；pilot 先固定测法、硬件和资源，再冻结最终评估。正常 200/500 项须功能完整；10000 项压力分别验协议安全/完整访问和具体 Agent 能力。capture、编码、传输、consume/W、评分、submit、落盘的 p50/p95/p99、峰值内存、gap/拒绝分母均报告。不可行时保留证据和取舍，不能测后悄悄抬阈值或缩范围。

源码增量经独立审查、相应检查和 PR 合入 develop；这不替代用户最终审批。组件源码普通 merge 保留 provenance。一个原生安装/控制 owner、一个重本地 build/train 槽；开发用最低忠实回归，稳定候选跑完整选定门禁，不重复相同重检查。

最终材料包含大白话导读、层次/连接图、完整需求/机制矩阵、实际旅程、失败/恢复/性能、固定身份、数据/model/use/费用、独立审查和新工程师复现。没有补齐就继续实施；只有真实权限、来源或重大取舍阻塞才停下来说明，不包装成已完成。
