# L-N 第一版：操作范围、完整合法目录与模型无关的 Agent 接口

2026-10-08范围修订：[G1-RC2](BASELINE_G1_AGENT_BOUNDARY_AND_BUDGET.zh-CN.md)以全部基础角色A0–A10为设计目标、Defect A0为首个验收切片。教程默认准备时关闭，L61保留可选管理/专门测试接口，不作为主策略学习必需项；其余范围内机制不得省略。

日期：2026-10-07。版本：0.1。状态：**首选方向的详细设计候选；未接受为 G1 默认，未实现、未运行资格化。** 用户早期倾向 L-N；最新要求允许稳定边界与声明合理假设的原生抽象，按[第一版综合方案](BASELINE_PROTOCOL_V1_SYNTHESIS.zh-CN.md)比较LN-B1/LN-E1，不把最接近原生作为唯一标准。本文件把上一版的方向收敛为第一版承诺、明确暂缓项、接口和验收，不把源码存在写成已安装/Live/Human 支持。

源码基点 cf65def910f1c2475a7c938d814e3cd49789f2fe；本轮原生核对的 DLL SHA256 为 9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4（此前登记 v0.111.0 / 41cef1ea）。原生源码只在本机合法安装和临时目录中核查，不入库。本文与[完整候选](BASELINE_PROTOCOL_OPTIONS.zh-CN.md)、[机制场景册](BASELINE_SCENARIO_SPEC.zh-CN.md)、[P4](BASELINE_DATA_AGENT_CONTRACTS.zh-CN.md)共同阅读；对 L-N v1 粒度和 Agent 接口的进一步约束以本文为准，旧运行 schema 不因此改变。

Human学习配套：[Annotator、M2、生成模型与RL](BASELINE_LN_LEARNING_INTEGRATION.zh-CN.md)。当前text Human side stream只有五类输入verb；环境动作可执行不等于已有完整Human示范。L01–L64必须另验被动capture/曝光/连续历史与各用途资格。

关键时序补充：[排队出牌与异步时序](BASELINE_LN_QUEUED_TIMING.zh-CN.md)。原生输入可在先前效果未结束时恢复；一次提交一个输入不等于最多一张native效果在途。Human输入H与执行S分开记录，不能默认转换为结算后才决策的示范。

本文件64行是可复用原生逻辑机制承诺库。LN-B1按稳定/子选择边界提供机会，LN-E1允许模型自主Act/Await；下面原生input-ready即可连续输入的规则适用于LN-E1，不禁止LN-B1明示限制提前排队。Await游标、edge/level和时钟语义由综合方案拥有。

当前接入方案见[完整蓝图](BASELINE_AGENT_PROTOCOL_BLUEPRINT.zh-CN.md)：优先一个E事件协议，多种Agent实现；内部稳定调度不是新协议，外部扣住信息/提交机会才是B-view。64行操作义务仍按选定合同和范围验收。

## 1. 目标、理念与明确范围

真实目标是：让封装 Agent 在公开玩家信息边界内完成游戏，拥有策略所需的原生查看、预览、选择、取消和推进能力，形成可记录、可学习、可评价的经历；在这个前提下降低无意义交互和计算成本。不是模仿每个鼠标轨迹，不是最少 API 次数，也不是把所有 Agent 限定成一个全目录 scorer。

五条设计原则：

1. **保留信息与选择机会。** 原生目标预览、升级查看、返回后重访等可能影响决定，不能因为模型弱或动作多就删掉。
2. **语义步骤与设备步骤分离。** 打开、焦点改变、选中、确认等是语义；按下/释放、滚动到 holder、动画等待是实现细节。设备差异只有经过等价核对才能隐藏。
3. **环境保证能做什么，Agent 决定做什么。** 没有价值不等于非法；循环保护不改游戏规则。
4. **完整可达不等于全部打分。** 环境保留完整有限动作权威；Agent 可以生成、分解、检索、规划或评分。
5. **缺口和妥协有身份。** 缺必要信息/动作就不满足该范围，不用“可选”洗掉；默认不退化到直接 Read，不自动改用另一输入条件。

建议第一资格实例固定为 **单人、基础游戏、Defect、标准新局、Ascension 0，从首个局内选择到原生终局及总结退出**。这只是可审核的首发范围提案，不擅自取消其他角色/高进阶/多人目标；其他配置需要自己的覆盖回执，同一机制可复用。开局前的任务参数和启动/续局由管理侧已有接口负责，模型不能通过 reset 逃避失败。游戏、profile、解锁/教程等起点条件写进实例 manifest；首个局内选择不得被设置流程跳过。

在该范围内自然可能出现的基础游戏事件、Hook、selector 和额外奖励都是必需覆盖，不能把“未知 custom layout”静默从正常局分母中删除。当前尚无穷尽该模式全部 caller 的运行证据，所以不能宣称已完成第一版 Full-Run。若某内置机制仍未覆盖，只能交付有名称的较小 canary 范围，不称第一版完整资格。

## 2. 先分清“合法”“可提交”“有用”

| 层次 | 由谁判断 | 例子 |
| --- | --- | --- |
| 原生允许的操作 | STS2 当前规则、控件、输入 owner | 当前牌可否打、可否取消、按钮是否接受点击 |
| 公平公开范围 | Connector 按 profile 和原生公开来源核对 | 不能因内部有奖励对象就提前公开 |
| 本版声明支持的语义机制 | L-N profile 与机制 registry | 单人标准局内的浏览/目标/selector；设置页是管理范围 |
| 当前可提交动作集合 C(o) | Connector 的完整绑定投影 | 当前 owner、阶段、公开对象、原生可达且可投递的操作 |
| 是否现在有提交权限 | Runtime/Controller | 目录存在但人类已接管，Agent 也不能执行 |
| 是否有价值、是否应再次查看 | 封装 Agent 的策略 | 开关同一页面可能合法但低效 |
| 是否触发运行预算停止 | Runtime 按预登记任务限制 | 超时/重复循环停止不把操作改为非法 |

C(o) 不是所有游戏对象上的所有动词乘积，也不是全局所有按钮；只描述当前原生交互位置。底层输入信号不作为独立候选。无可交互 owner 时允许空集合和 settling；unsupported 与原生本来暂不可用分开。可见禁用按钮保留为页面事实，不是可提交动作。

**完整性有两个检查。** 一是范围内原生语义操作是否都有投影（覆盖）；二是发布的当前目录是否完整、准确绑定（当前集合）。不能通过不支持某必要原生行为来缩小集合，再称“剩下的目录 complete，所以完整”。

本文的清单是**操作类型/原生机制的完整 v1 承诺表**；每一时点的实际完整清单必须由其动态实例化给出。例如“确认目标”在某状态生成两个 target-bound 实例，在另一状态为零。没有固定文本能列出所有未来局面的具体 card IDs。验收要对实际原生能力做反向覆盖核对，不仅检查下表行号齐全。

## 3. 第一版必须提供的动作与信息清单

状态：**必需**＝第一版范围内适用时必须可达；**条件**＝原生此时确实有该控件/机会才提供，原生不存在时不凭空造；**机械**＝由 Host 内部完成；**暂缓**见第 4 节。下列名称是设计语义，不保证等于当前 wire verb。每行同时写“现有基础/缺口”，均不代表 Live 资格。

### 3.1 浏览、提示与图形信息

| ID | v1 操作与结果 | 范围/条件 | 当前依据与缺口 |
| --- | --- | --- | --- |
| L01 | 打开本局牌组；返回真实先前 owner | 必需，原生入口此时启用 | 已有 open_run_deck/return；需核各 overlay 时机 |
| L02 | 打开抽牌堆；返回 | 必需，原生战斗入口启用；提供完整公开成员，不给真实抽取顺序 | 已有入口；列表来源、空堆可达性和长列表需资格化 |
| L03 | 打开弃牌堆；返回 | 必需，同上，保留 pile 身份 | 已有入口 |
| L04 | 打开消耗堆；返回 | 必需，同上 | 已有入口 |
| L05 | 打开地图查看；原生允许时关闭地图 | 必需；关闭与选路分开 | 已有入口/返回；旅行阶段不发明 Back |
| L06 | 对当前可 focus 的卡牌显示原生 tooltip；解除/转换焦点 | 必需，按手牌、网格、商店、奖励等实际原生能力 | 已有 show_card_tips；当前 overlay/holder guards 有范围，须逐 owner 核对 |
| L07 | 进入卡牌详情；关闭到真实来源页 | 必需，在原生可进入详情的每一承诺页面；不为原生没有详情入口的卡凭空开页 | 目前 deck/bundle 有 opener；pile/hand/reward/shop 的实际原生入口需逐类补核，不能由同名 card reader 代替 |
| L08 | 在卡牌详情中切换升级预览开/关 | **必需且条件于原生开关可用**；提供切换后原生显示，明确 preview 非实际升级 | 已有 toggle_card_upgrade_preview；开关、动态费用/星费/附魔、原卡归属需完整捕获 |
| L09 | 卡牌详情上一张/下一张 | 必需，原生箭头可用；保留 native 列表与顺序 | 已有 previous/next；不靠数组索引构造 native operand |
| L10 | 遗物 tooltip 显示/解除/切换 | 必需，原生可 focus | 已有 show_relic_tips；只给原生公开计数，未知 tooltip 子类不装完整 |
| L11 | 进入遗物详情、上一件/下一件、返回 | 必需，原生 controls 启用 | 已有 inspect_relic；读 actual rendered 文案及 seen/unlocked，不用内部完整模型绕过限制 |
| L12 | 药水 tooltip 显示/解除/切换 | 必需，与使用弹窗分开 | **无专门可控 potion-hover leaf**；已有被动提示不证明 Agent 能主动取得，需补 |
| L13 | 角色/敌人状态效果、意图 tooltip | 必需，原生可显示的各对象 | 已有 power/intent tips；分对象和原生实际显示内容，不暴露内部未来计划 |
| L14 | 球与其他公开资源 tooltip | 必需，所选角色/Hook 中适用 | orb 有入口；其他机制逐类 registry，不泛称所有资源已支持 |
| L15 | 原生角色/敌人本体的其他独立信息提示 | 条件必需：若确有与 L13 不同的公开内容和入口则纳入；若原生无此能力则明确 not_native_capability | 当前无 generic enemy/body leaf；不得靠名称假设其一定存在或直接排除 |
| L16 | 顶栏 HP/金币/楼层/Boss/地图/牌组等提示 | 必需，原生显示的提示 | 已有 topbar tips；同一控件点击与 hover 效果分开 |
| L17 | selector 的 Peek 打开/保持/返回 | 必需，原生提供此功能时；提供实际可见战场，返回原 child | **目前只登记 close Peek**，没有对称 open；需要补 exact owner 入口 |
| L18 | 已实际显示的关联关键词、卡牌预览、图标语义 | 当前视图的一部分，通常**不另造点击动作**；原生确有二级交互才登记额外操作 | 已有两类 tooltip 投影；不无限遍历内部图鉴，不把预览当持有实例 |
| L19 | 已显示的提示随焦点消失/替换、窗口自动刷新 | 公开事件；原生真实解除操作必要时目录提供，纯布局/动画不另造策略动作 | passive hover 已有来源；模糊归属/未知 subtype 必须显式缺口 |

只有属于同一已进入逻辑列表的内容可以免滚动完整提供。**不能因为整份牌组可列出，就假定每张卡详情的原生 holder 已存在。** 第一版必须做完整逻辑对象→合法 native inspect 入口映射；可以在 Host 内定位/滚动实例化 holder，但须不额外揭示/改变选择机会并重新核身份。未证明之前该能力是缺口，不把尚未显示 holder 的卡悄悄删掉。

### 3.2 战斗、持牌、目标预览与药水

| ID | v1 操作与结果 | 范围/条件 | 当前依据与缺口 |
| --- | --- | --- | --- |
| L20 | 开始出牌，进入原生持牌/目标或确认阶段 | 必需，原生当前允许 | 已有 begin_card_play；不要另造纯虚拟暂选并称 native |
| L21 | 将持牌焦点移向一个当前原生目标，**不确认** | **必需**；产生原生 target-dependent 预览 | 已有 focus_target→OnNodeHovered；资格化 exact card/target/owner |
| L22 | 在不同目标间切换焦点 | 必需；提供每次真实渲染后的说明/数字、焦点对象 | 现有链可更新 description；focused-target 明确字段、完整显示需补 |
| L23 | 从目标移开/清除目标焦点 | 条件必需，原生允许；恢复 native 未定目标预览 | 现有 NCardPlay 有 unhover/clear 原生机制，协议 leaf 尚需补核；不把 cancel whole card 当同义替代 |
| L24 | 确认当前目标；或原生一步指向并确认目标 | 必需，按已登记原生语义；可以选择先预览，也可以直接确认，不强制读每个目标 | 已有 confirm_target，内部先 focus 再原生 select；不能把返回未知当 Commit |
| L25 | 无单体目标/全体目标的原生确认 | 必需，有实际确认阶段才提供；显示 native multi-target 预览 | controller 路径有入口；untargeted mouse 续接缺口，不能自动归成功 |
| L26 | 取消持牌/目标过程并回到原生阶段 | 必需，原生允许；不用药/出牌是否已发生按真实反馈 | 有 cancel_card_play；鼠标/控制器等价和恢复需资格化 |
| L27 | 持牌期间可用的信息入口 | **条件必需**：此时原生仍可取得的必要提示/Peek/资料必须保留 | FrameBuilder held early-return 目前只给 cancel/focus/confirm，需核是否漏原生可达操作 |
| L28 | 结束回合 | 必需，原生 enabled/ready | 已有 end_turn；等待敌方/自动效果，不逐帧造 Wait 动作 |
| L29 | 打开药水操作弹窗 | 必需，原生当前允许 | 已有 open_potion_popup |
| L30 | 选择使用药水 | 必需，原生当前允许；可能进入真实 target stage | 已有 choose_potion_use；popup 消失不证明已消耗 |
| L31 | 药水目标的焦点/预览、确认、取消 | 条件必需：按该原生目标机制实际提供的机会 | 当前 select/cancel 已登记，独立 focus 是否有新公开内容需逐机制核对；不从卡牌推断所有药水行为 |
| L32 | 丢弃药水 | 必需，原生允许，走其确认阶段（若有） | 已有 discard_potion；不把丢弃当查看 |
| L33 | 关闭药水弹窗 | 必需，原生可关闭 | 已有 close；不等于已使用/丢弃 |
| L34 | 接收公开自动效果、敌人行动、新回合、原生排队/取消的公开表现 | 事件；原生重新input-ready即可决定，不必等先前效果全部结束；没有选择不造native no-op | 输入、入队、执行、取消/Commit与因果后继分开；参见异步时序规格 |

**“拖到敌人身上看数值”具体包含：**BeginCard→FocusTarget→读取原生当前卡显示→可换目标/移开/确认/取消。第一版必需提供，不要求模型控制像素路径。原生链 NTargetManager.CreatureHovered→NCardPlay.OnCreatureHover→NCard.SetPreviewTarget→UpdateVisuals 使用 target 更新动态显示，包括代码明确提及的 Vulnerable 条件。Host 应读取 actual rendered title/energy/star/enchantment/description 和已绑定 focus；不自行模拟最终伤害、敌人剩余 HP、隐藏抽牌或整段战斗。

同目标重复 focus 也不能先验当纯 no-op：NCard.SetPreviewTarget 有相同目标不刷新的 guard，但 target manager 仍可能重跑 Hook、信号和 VFX。上下文变了也不保证同目标 focus 就刷新数字。第一版保留实际能力和结果；对重复请求的去重只依据同 request ID，不依据“看上去没用”。

### 3.3 选择器、暂选与预览

| ID | v1 操作与结果 | 范围/条件 | 必须保留的不同语义 |
| --- | --- | --- | --- |
| L35 | 手牌选择一张、撤选、原生满额替换 | 必需，原生候选与操作 | 每选一步重新观察；不枚举组合，不套用 grid cap |
| L36 | 手牌选择确认 | 条件必需，实际控件启用 | 自动完成时不补一次假确认；min=0 不等于 cancel |
| L37 | simple grid 选择/撤选/确认/整页取消 | 必需，按具体控件 | 满额禁止与手牌替换不同；空/Task canceled 保留 |
| L38 | 生成牌选择/原生 skip | 必需，生成和 opening-ready 后 | 新候选不提前提供；无候选可自动返回 |
| L39 | 战斗牌堆选择/撤选/确认/原生取消 | 必需，Draw/Discard/Exhaust 按实际来源 | 有效候选数、动态 selected 清理、clamped 约束；不是普通浏览 |
| L40 | 通用牌组选牌/撤选/进入预览 | 必需，eligible 原对象 | 原生满额/确认进入 preview，不自算另一个选择器 |
| L41 | 通用牌组预览确认/取消；整个选择过程关闭 | 条件必需 | preview cancel 回 selecting，whole close 返回原生空/取消，二者不同 |
| L42 | 升级选牌/撤选/升级视图/预览确认/取消 | 必需 | 看升级与实际升级不同；选择原牌，preview clone 不执行 |
| L43 | 变换选牌/撤选/原生预览/确认/取消 | 必需 | 只展示原生已经展示的结果，不提前运行 RNG |
| L44 | 附魔选牌/撤选/预览/确认/取消 | 必需 | 原生修饰显示、实际可附对象、取消层次 |
| L45 | 卡包预览、包内原生详情、确认整包、取消预览 | 必需 | 包为单位，不把成员变成任意可选单卡 |
| L46 | Boss/其他遗物选择、原生 skip | 必需，范围内发生时 | skip 后继依实际 caller，不统一按宝箱解释 |
| L47 | 多层 child selector 的上述操作 | 必需 | 父等待、child 新输入、child 完成后父原生续执行；不重发父动作 |
| L48 | 空结果、零选、全选自动完成、退出取消 | 真实原生事件或已登记取消动作 | 空、null、Task canceled、正常完成不同，不能统一 success |

当前明确 source/native 差异：combat-pile raw MinSelect 与原生有效候选 clamp；simple-grid min0/manual 条件；deck-upgrade raw min 还需确切 caller 对照。这些是实现修复入口，不是允许把确认动作删掉的理由。

### 3.4 局内非战斗与终局

| ID | v1 操作与结果 | 范围/条件 | 必须保留的边界 |
| --- | --- | --- | --- |
| L49 | 选择地图目的地、退出原生地图标注模式（若当前有） | 必需/条件 | travel 与 map close/annotation exit 分开；坐标不是 consumer mutation operand |
| L50 | 外层奖励打开/领取 | 必需，当前原生项目 | 独立组、已解决状态、暂未揭示内容分开 |
| L51 | linked reward 子项选择 | 必需，原生互斥组 | 一项成功导致 group 消失，不伪造其他项 skip 决策 |
| L52 | 卡牌奖励选择 | 必需，完整当前候选 | 多次选牌 Hook 后重新观察，不默认一次结束 |
| L53 | 卡牌奖励返回/Skip、reroll、特殊 alternative | 必需，各真实 alternative | 返外层不一定完成；reroll 更新；消耗/牺牲可能完成并触发新效果 |
| L54 | 外层奖励继续/原生最终放弃 | 必需，controls 启用 | nonterminal 自动关闭、terminal proceed、父事件恢复分别处理 |
| L55 | 打开商店库存、关闭、房间 Proceed | 必需 | 进入库存是原生阶段；原生教程可能打断 Proceed |
| L56 | 购买商店卡/遗物/药水 | 必需，当前报价/库存/native affordability | 新 Hook/补货后重新捕获，不能预读新货或复用旧报价 |
| L57 | 打开商店移除服务 | 必需，后接 L40–41 | 最终原生返回选中牌后才确认扣费/移除；预览可取消 |
| L58 | 事件对话推进、选择选项、原生 Proceed | 必需，包括范围内内置专用布局 | 未揭示页不前置；事件中战斗后可能回事件 |
| L59 | 营火选项与 Proceed | 必需，动态完整 native options | 不固定只有休息/升级；可多次行动时不强制提前结束 |
| L60 | 开宝箱、处理先行奖励、选择/跳过遗物、Proceed | 必需 | 原生 normal/extra rewards 后才 relic collection；skip 与卡牌返回不同 |
| L61 | 教程启用/禁用选择、上一页、继续/关闭 | 默认管理准备关闭；教程profile可选 | 意外出现按已声明管理/任务能力处理并记录，不静默代选；不是默认模型核心训练目标 |
| L62 | 跨幕/第二 Boss 等原生继续与 readiness | 必需，范围内实际发生；高进阶扩展另资格 | 无输入时等待；已经提交不因画面静止重发 |
| L63 | 胜负/放弃公开结果、总结推进、返回菜单 | 必需 | task stop/断线不当 native loss；**当前 FrameBuilder 在 !IsInProgress 时清空 leaves，必须补 terminal/summary owner 路由，不能因局结束而漏掉总结退出承诺** |
| L64 | 局内退出/放弃确认流程 | 管理侧显式任务停止/用户控制；本版默认**不给策略自行重开/放弃来优化计数** | 这是任务权限限制，不说原生没有此操作；若研究允许 surrender，需新任务/profile 明示并记结果 |

**终局生命周期固定为两个边界：**`game_outcome_known` 结束本局游戏结果/horizon；`agent_task_complete` 在任务包含的总结导航和返回菜单完成后才结束 Agent 任务。两者之间仍用有身份的 summary owner、公开输入、绑定目录和同一个持续状态消费入口处理导航，W 不在第一次看到胜负时提前冻结；summary 不算新战斗/新局，也不改已确认的胜负。到 task complete 才冻结状态；开启新局必须新 generation/reset。若只评估游戏胜负可在第一个边界生成结果，但不能称本版完整旅程已经完成。原生 summary 状态无法绑定时记录 gap/未完成导航，不伪造自动返回。

L01–L64 中的查询/浏览实例也属于真实行为历史，不因“没有 game Commit”被删掉。它们是否需要 N 标签、是否调用昂贵 scorer，是第 7 节的 Agent 选择。

## 4. 可以省略、暂缓与不能隐瞒的内容

| 项目 | 第一版决定 | 原因/补偿 |
| --- | --- | --- |
| 鼠标路径、像素拖动、屏幕滚轮、控制器按键细节 | Host 机械步骤，不成为模型动作 | 仍保留 Begin/Focus/Unfocus/Confirm/Cancel 的真实信息与选择机会；非等价设备分支不能隐藏 |
| holder 实例化、窗口动画、原生 opening guard | Host 等待/绑定/捕获 | 必須用 native-ready 依据；不能自定义 sleep 当完成；相关效果记法证 |
| JSON 分块、分页传输、目录 prefix 查询 | 运输/目录访问，不是游戏页面动作 | 绑定同一完整目录，不能偷偷读别的未开页面；不因网络 chunk 更新 W |
| 文本重复、静态定义重复、协议 ID/lease | 传输去重/Agent 编码；控制元数据不进策略特征 | 实际公开内容与同一性不丢；引用须可解析，不能让模型猜字典 |
| 卡牌插画、纯装饰动画、音效波形、光标造型 | 首发语义表示不保真像素/声音 | 图形中有信息的类别/数值/状态必须保留；若独有决策信息无法提取则是范围缺口，不能称装饰丢掉 |
| 设置、音量、语言切换、存档导入、退出进程、账号/队伍管理 | 管理接口，非局内策略动作 | 与 Agent 的任务/控制权限分开；不构成漏游戏选择 |
| 全图鉴、历史局浏览、未获得物品百科 | 暂缓独立浏览功能 | 不阻止查看当前合法对象的详情；不能私下把图鉴当静态词表全送模型 |
| 多人投票/ping/授予/实时协同 | 首发范围外，后续 profile/qualification | 不把单人暂停/owner 模型直接推广 |
| 任意第三方 mod/新版本未知机制 | 未资格化，不自动支持 | 能保留未知公开描述不等于可执行；版本变动触发重新核对 |
| 基础游戏范围内新的专用事件/selector | **不能作为完整首发的永久暂缓项** | 缺失时交还控制并记录；该范围尚未完成，不删分母 |
| 自动查完所有目标伤害后替模型选最优目标 | 不做环境功能 | 会加入 Agent 策略；Agent 自己显式规划可做，计实际交互/曝光 |
| 直接计算最终敌人 HP/未来牌序代替原生预览 | 不做 | 新推演或隐藏信息，不是 native display |

当前代码没有证明 L-N 存在不可解决的根本工程障碍。已发现的是有限的投影、owner、设备续接、完整列表与资格化缺口。第一选择是修 owning seam；只有明确证明某条件下不可稳定实现或成本超出实际预算，才提小范围妥协。妥协单必须含原生路径、复现/测量、失去的信息或机会、替代方案、版本和数据影响及恢复目标；由 owner 接受，不默默改成 Q-D。

## 5. 合法目录可以完整，但不必全部写进模型 prompt

### 5.1 第一版的协议选择

**C(o) 由 Connector 在当前观察上完整构建并封存。** 第一版仍复用完整 materialized finite catalog 作为 authority reference，不引入只靠模型猜合法的执行入口。对 Agent 提供两种无损访问方式，均是同一集合：

- `ListActions(catalog_ref, cursor)`：完整条目、稳定分页、count/digest，允许全量 scorer 或调试器取完整集。
- `DescribeActions/AllowedNext(catalog_ref, prefix)`：按语义 family、当前对象或参数前缀返回完整可延伸集合；由已封存 C(o) 投影产生，不另写游戏规则。
- `Resolve(catalog_ref, operation, public_refs)`：将严格结构表达解析为 C(o) 中**唯一**动作，返回 bound handle；歧义、不存在、参数多余拒绝，绝不 nearest-match 或“猜用户意思”。
- `Submit(request_id, observation_ref, catalog_ref, bound_handle)`：当前权限、owner、参数和 native legality 再验证，执行一次；结果沿用明确 delivery/unknown。

名称仅为设计。前两项是目录元数据访问，不是 AP-Q 的游戏资料 Inspect，也不产生原生事件或推进模型 W。只能返回当前 C(o) 已获准暴露的语义描述，不能顺便展开未展示卡牌提示、未来页或私有原因。Agent 可以在同一完整授权目录下按需访问，**不用先把每个条目都下载到模型上下文才能选择**。目录是否完整在 Connector/Runtime 验证；Agent 是否拿了全部条目另有访问记录。全目录 scorer 专用 port 才要求全量输入已 materialize。

这细化并替代旧候选“全部目录块到达后才开始任何模型选择”的一刀切表述：新规则是**authority 集合必须完整，完整列表消费者必须收全；生成/分解消费者可以使用同一完整集合的有界访问**。当前 SDK/Runtime 的 strict schemas 还不支持这套端口，需版本化实现，不能直接改旧 complete 含义。

分组不是原生新页面。模型在内部选“信息类→遗物→某对象”，期间没有 game action，不能伪造三次 native transition。若真正点击进入遗物页，那才是 L-N 环境步骤。

### 5.2 为什么只给一个个参数的独立集合还不够

假设当前合法关系只有 `(操作A, 对象1)` 和 `(操作B, 对象2)`。分别给操作 `{A,B}`、对象 `{1,2}` 并允许任意组合，会产生非法的 `(A,2)`。`AllowedNext(prefix)` 必须从完整关系里取仍有合法延伸的分支，最终 Resolve/Submit 仍验证。前缀合法也不授权执行半条命令。

语法正确不等于语义可执行，生成约束不是原生合法性权威。card/target 等参数用当前公开 refs，不是数组索引、坐标或 native 对象。生成自然语言可作为 Agent 内部计划，但执行前必须变成严格结构且唯一绑定。

### 5.3 输出合同不强制 scores

完整 Agent 的新输出候选是：

```text
Choice = SubmitOne(current_bound_action)
       | Yield(wait_for_public_event)
       | Abstain(public_reason)
```

SubmitOne只约束一次输入写入；已知接受的原生动作效果可以多个在途。后续是否可输入由当前native owner/controls决定，不能等canonical S'才放行。结果未知的输入仍须核对，不能重发。

`Resolve` 属提交前的非 mutation 绑定步骤；`scores`、token 概率、候选 shortlist、推理文字可选记录为模型诊断，不是授权必需字段。不能把 abstain 当游戏 skip，不能把 yield 当无限重复 no-op。Runtime 控制信息与策略输入分开。

**当前实际限制：**Policy Runtime `AdapterDecision` 强制 `scores.length == complete candidate count` 和 `selected_index`；换一个会生成 action ID 的内部模型，若仍输出该旧接口就还受它约束。设计需要新增有版本的 Agent decision port/manifest/admission，而不是给未评分候选填假分数。旧评分端口保留原义，环境 native authority 不变。

因此：模型从评分换为生成，在已经模型无关的新 Agent port 下可只换 Agent 内部；在当前严格 scorer port 下还要改端口。若连原生交互粒度或曝光时机一起改了，才是 L-N AP/IP/OP 本身的变化，不能只称换模型。

### 5.4 Agent 实际收到什么、怎样请求

`Observe/Events` 分为策略公共内容与控制封套。公共内容是一份明确的 representation：当前native page/phase、公开对象及原生文本/数值、选择/焦点关系、实际已展示的tooltip/preview、缺失状态、前次公开反馈和语义事件位置。控制封套持有原始ID、代次、租约/连续性证明；模型只用必要的公共局部别名及关系。不要把候选元数据、gameplay事实与私有法证一股脑拼成prompt。

```yaml
# 建议语义示例，非现有API；数值与refs仅示意
binding:
  observation_ref: o17
  environment_generation: g2
public:
  page: held_card
  phase: targeting
  card:
    ref: c1
    displayed_cost: {kind: energy, text: "1"}
    description: 当前原生目标相关正文
  focus: {target_ref: e2, source: actual_native_focus}
  visible_targets: [e1, e2]
  feedback: {request_ref: r16, result: target_focused}
  facts_complete: true
catalog:
  ref: C17
  complete: true
  total_count: 5
  groups:
    - {family: focus_target, count: 2}
    - {family: confirm_target, count: 2}
    - {family: cancel_card_play, count: 1}
  access: [list, allowed_next, resolve]
```

该示例只演示五项的绑定；真实此刻原生还有L23/L27等可操作机会就必须纳入总数，不能按示例固定为五个。持牌信息只从当前公开显示取得，不因有目标列表就附带所有目标的未来预览。目录metadata可以缓存同一C，访问次数计成本但不制造native事件；生成器的临时解码状态与M2的真实历史W分开。

```text
模型/Agent内部提出: operation=confirm_target, target_ref=e2
Agent→Resolve(C17, structured_proposal)
环境→唯一bound_handle a4，或明确no_match/ambiguous/stale
Agent→Submit(r17, o17, C17, a4)
环境→not_delivered/rejected、delivered或unknown＋实际公开后续状态
```

Resolve与Submit之间仍可能变化，Submit必须复验。结果包含公开owner/phase/等待/child（只给已获准关系），但native Commit/因果证明走法证通道。没有结果就查原request，不生成“再试一次”的新动作。只有明确未投递的proposal错误才允许在TaskSpec有界预算内重新生成；未知投递零次自动重试。

Agent的`consume(public_event)`和`decide(current_public_input, catalog_access)`是逻辑职责，不强制特定框架。后者可以返回Choice，也可以在其内部做若干非mutation目录查询；Runtime在已过期/撤权时终止待提交选择。目录分页/掩码不是模型免费无限查询：TaskSpec单独限制元数据请求/字节/推理预算，避免把无穷循环从gameplay挪到Resolve。

## 6. 对不同模型和 10000 动作的具体含义

| Agent 内部方法 | 怎样使用 C(o) | 成本/保证 | 对外协议 |
| --- | --- | --- | --- |
| 当前 D-M2 全量 scorer | 编码公开输入、更新 W 一次；对全 C 评分，选一项 | 共享观察，不代表候选免费；现代码每条 action 经轻量 encoder，再共享 W 分支评分 | 新 Choice port 可包装；旧 scorer port 仍有全分数约束 |
| 其他全量 scorer | 全目录批量/分块计算 | 可减少峰值内存，仍 O(N) 候选工作；不是 N 次完整 LLM 调用的必然要求 | 同上 |
| 分解/自回归策略 | 根据 prefix mask 选 family/对象/参数，只提交最终唯一动作 | 不打全部复合候选分支；每个头仍有 domain cost，mask 构建也可能 O(N) | 同一个 Choice/Resolve/Submit |
| 生成式模型 | 生成受 schema/合法 prefix 约束的操作表达或当前 ref | 词表打分和生成长度有成本；schema 合法不保证 native-ready，最终再核 | 同一接口；不运行自由文本命令 |
| 检索＋重排 | Agent 内部从完整公开 C 建索引/产生 shortlist，只对 k 项用昂贵 scorer | 可能漏最优，需测 recall/质量；索引更新、轻量全扫描不一定消失 | 环境 C 仍完整；shortlist 是声明的 Agent 算法，不改 authority |
| 规则/规划/混合 | Agent 内部固定查看流程、搜索或模型选择 | 记录策略组件和实际请求；不能把脚本决定冒充模型独立学会 | 同一接口 |

**10000 是压力场景，不是已测得 STS2 经常出现的数量。** L-N 通常把真正的多阶段选择保留：当前手牌 Begin、目标阶段 Focus/Confirm、selector 逐张 toggle，避免人为生成“任意卡×目标×所有子选择组合”。但不能以“应该不会很多”不设计上限。

第一版明确：协议支持完整有限关系的目录分页和前缀访问；10k 合成条目用于 transport/binding/生成/分解的压力验证。Connector 可以仍花 O(N) 构建 authority registry，网络和对象编码也可能 O(N)；**不承诺总成本次线性**。不评分每个动作可以降低昂贵模型工作，但与没有枚举/存储任何动作不同。后续若 Host 构建成为实测瓶颈，再单独审查符号化完整关系，不能现在凭空说已无需物化。

任意黑箱 scorer 在没有可用结构/界限时，不看某动作就不能保证找到全局最大值。生成、层次选择、检索都是策略/算法选择，可能改变分布或最优性；不能把它们包装成无损的算力优化。分块批量若数学等价则可做同算法优化，仍需数值/排序测试。

初版 P4 时序 N 的 full-catalog cross entropy 只对该全量参考 scorer 成立。生成式可用合格实际动作的 token/prefix 监督，分解式用条件因子监督，检索式另报 shortlist 命中和训练目标；不能拿 sampled/subset loss 冒称原全目录 N loss。真实轨迹/输入来源不因模型改变自动合格或失效，需逐接口/曝光核对。

## 7. Agent、内部模型、Runtime 与环境的分工

| 主体 | 拥有的决定 | 不承担的事情 |
| --- | --- | --- |
| STS2 | 规则、原生选择与效果、RNG、原生可操作条件 | 模型效率和研究标签 |
| Connector/Host adapter | 公平事实、当前完整语义目录、native 输入映射、显示绑定、复验和 delivery | 按战略价值筛动作、替 Agent 选目标或自动领奖 |
| L-N 表示/事件层 | 对象/区域/文字/关系、曝光时机、当前目录访问语义、事件身份 | 不把未展示资料自动补入，不把内部推演当原生事实 |
| 封装 Agent | 获取信息、缓存/编码、模型调用、计划、shortlist、固定程序、最终选择 | 不新增 native 合法性，不越过公开接口 |
| 内部模型 | 当前被交付的任务：编码、评分、生成、检索或记忆 | 不直接持有控制租约或 native 句柄，不一定看到全部控制消息 |
| Runtime/任务监督 | 权限、预算、Stop、人接管、执行安全、恢复与运行状态 | 不“帮助破循环”自动挑一张牌或假宣布成功 |
| Recorder/Evidence | 原生见证、Agent 曝光/提交/结果、完整性和 immutable lineage | 不判最优策略、不伪造 Human origin |
| 数据/研究/评价 | 用途、转换资格、N/Z/O/损失、指标和协议对照 | 不修改已发生的原生/曝光事实 |

### Agent 收到与模型看到可以不同，但必须说明是谁作了处理

默认表示是当前逻辑页的公开对象＋原生文字＋公开关系＋反馈，合法目录独立可访问；文本 renderer 与 typed tensor adapter 是不同 representation 身份。不能把 source hash、lease、private witness/seed 送进策略。不同输入表示不改变 L-N 必须原生查看才能取得资料的时机。

- Host 内部按下/释放、动画 ready、目录 chunk、租约消息：Agent 控制器/日志处理，不进入模型语义事件。
- Agent 请求打开资料页：实际得到的新资料属于 Agent 曝光，不能只存在于一个未声明外部 helper。
- Agent 内部固定程序负责返回：可以不用 scorer 选 Back；实际新页面/反馈仍按登记的状态消费规则处理。
- Agent 可以选择不给模型重复的静态文本，只复用已编码内容；缓存依内容/上下文/encoder 版本失效。不能按卡名复用动态目标预览。
- 若一个 Agent 模块读了资料后决定目标，另一个“主模型”没读，这仍是**整个 Agent 使用了该资料**，不能在评价中称无此信息。
- 禁止为了记录简短，把原生独立目标预览/取消/新的选择发生隐藏于协议之外。只省掉不带独立信息或选择的机械步骤；其他合并需要新 OP/AgentSpec 和等价/范围审查。

参考 D-M2 继续沿用 event-consume-v1：每个新公开语义事件消费一次；重复运输零次；实际请求在后续入站事件中作为 prior actual 消费；候选假设不写 W；gap 显式 reset。新的 typed encoder 或内部固定程序若改写该事件策略，需新 AgentSpec，不静默沿用旧权重/状态身份。

## 8. 无限循环、无效操作与效率：逐例定位第一错误事实

“无效”必须分开：非法输入、原生拒绝、旧绑定、结果未知、无 game 状态变化的合法信息动作、策略无进展。它们不是同一错误。

| 例子 | 首先核什么 | owning 责任与处理 |
| --- | --- | --- |
| EX-L01 奖励打开→返回→再打开，不断重复 | 曝光事件/真实重访是否正确，模型是否得到合格历史 | 若输入正确而仍循环，是 Agent策略/训练；Runtime 预算停止，不能删除原生返回 |
| EX-L02 明明刚看过，但每次模型状态都相同 | 去重是否错误把真实重访抹掉，W/cursor 是否重置 | 事件/Agent状态实现 defect；不是“模型需要更多RL”即可解释 |
| EX-L03 查看升级→关→开，反复切换 | 原生checked与说明是否改变、身份是否正确 | 输入正确则策略低效；若checked没更新则 Host投影错误 |
| EX-L04 focus A→B 后说明仍用 A 数值 | exact target、render refresh和捕获位置 | Host原生显示映射/时序；不得由模型自己补伤害计算掩盖 |
| EX-L05 同目标 focus 无新数字 | 原生 same-target guard、上下文变化与真正渲染 | 不是必然模型无效动作；不得把原生未刷新伪造成新有效预览 |
| EX-L06 需要看药水说明，但目录没有入口 | 原生此时可否 hover，profile是否承诺 | 原生能看而协议漏了是 Connector覆盖缺口；原生不能看则是环境条件，Agent应规划别的时机 |
| EX-L07 打开资料输入已投递但owner不可确定 | 当前 request delivery、native实际owner | Host绑定/观测失败；unknown不重发，Runtime安全交还 |
| EX-L08 已选满，模型请求再选一张 | 原生是手牌替换还是网格cap | 目录漏合法替换为Host问题；模型自造非法对象为Agent输出问题 |
| EX-L09 模型总生成不存在的对象名 | 当前完整公开ref是否可得，parser是否过期 | Agent生成/绑定处理；非投递的解析错误可有界修正，不能最近邻猜着执行 |
| EX-L10 动作已发后网络超时，又生成一次 | request ledger是否先核原attempt | Agent/Runtime请求管理错误；不能当普通“生成修正” |
| EX-L11 十万字提示耗时大 | 是否重复serialize、控制元数据入prompt、未使用共享对象、真实内容规模 | 表示/Agent编码/传输分别优化；不删未读原生必需内容假称完整 |
| EX-L12 10000候选评分慢 | Host目录生成、文本编码、候选encoder/scorer各耗时 | 环境若重复无意义膨胀修环境；模型计算贵优化Agent算法；合法性权威不随速度转移 |
| EX-L13 全量scorer分块重复更新W | writer是否错误在每个candidate/chunk执行 | Agent实现 defect；W应按公开事件更新，候选只读 |
| EX-L14 每次照全局脚本自动返回，错过原生新选择 | fixed program是否越过声明的机械范围 | Agent程序设计或Host偷偷代选；保留中断，不能宣布等价浏览 |
| EX-L15 原生禁用时模型反复尝试按钮 | 禁用是否仍被当可执行动作 | Host目录错误；如果模型绕过目录发私造参数则Agent错误 |
| EX-L16 读取目录的10个分页被当10次经历 | metadata传输是否误用semantic event ID | 协议/Agent消费错误；分页不是原生动作 |
| EX-L17 10k检索漏掉获胜操作 | shortlist是否来自完整C、未评分项是否仍可访问 | Agent近似策略/评价问题；报告recall与任务质量，不说环境动作非法 |
| EX-L18 未知内置事件临时自动点第一个 | 是否绕过coverage gate | Host/Runtime越权和覆盖缺口；停止并记录，不能为流程畅通代选 |
| EX-L19 目录未变但模型反复yield | 是否真有pending native效果 | 无pending且有可执行目录仍yield是Agent停滞；预算监督，不造native Wait刷历史 |
| EX-L20 guard打断后人工赢了 | actor/continuity/termination是否正确 | 评价与日志；不得计为Agent无干预成功 |

### 第一版如何有界停止，又不把“循环检测”变成策略老师

运行前 TaskSpec 必须给：总墙钟/操作预算、每决策模型预算、连续已知未投递错误预算、无进展诊断窗口和安全停止方式。没有完整预算则拒绝 Auto admission；具体数值按本地实测/任务条件制定，不把任意次数写成普遍游戏规则。

可以检测短周期重复序列，但“无进展”只能是**公开诊断信号**：同 owner/phase、public/game facts、选择状态、已揭示资料内容和待决请求条件下重复；不能只比较HP或正文。不声称证明整个原生内部状态无变化，不用隐藏RNG帮助策略判loop。一次新资料曝光、动态计数改变、真实新选择或待完成原生过程，应避免粗暴归为旧循环。

软告警/冷却是否进入 Agent 是 AgentSpec/任务条件，必须声明；基准模式不向模型注入“改打这张牌”的提示。到硬预算 Runtime 停止后续提交并保存证据，结果为 interrupted/budget_exhausted；若原生效果仍进行中先保留真实状态，不冒称已回滚/已停止游戏。不得自动随机选动作、自动结束回合、删除最近访问项或奖励“不看信息”。

合法重访、反复对比目标、查看升级不是自动失败；大量学习初期循环也不证明协议错。但若同一必要任务需要无法取得的公开信息，或机械步骤被过度拆碎造成系统性负担，就回到协议/映射 owner 修正。

## 9. 多模型交互例子

### 9.1 看目标预览后出牌

```text
O0战斗 → Agent Submit(BeginCard c1)
O1持牌 → Agent Submit(FocusTarget e1)
O2原生显示(已绑定c1,e1) → 可切换e2/移开/确认/取消
O3确认后的公开反馈 → 原生效果/child选择 → O4
```

M2可每步对当前小目录评分；生成模型可以输出严格 `focus_target(ref=e1)` 再 Resolve；规则程序可按自身策略先查看两个目标。三者调用同一原生行为和合法目录。环境不预先替它们展开所有未来目标数值。

### 9.2 10000个当前可查看对象/操作（合成压力例）

```text
O0: 当前公开对象＋catalog_ref(total=10000, complete=true)
Agent内部:
  全量scorer → ListActions全部分页 → 共享编码＋分块评分 → 一项
  分解策略 → AllowedNext(verb) → AllowedNext(verb, subject) → 一项
  生成策略 → 生成结构/ref → Resolve唯一成员 → 一项
  检索策略 → 当前合法索引 → k个候选精排 → 一项
Submit同一个bound handle语义 → 原生复验 → 反馈
```

`AllowedNext` 不凭空创建环境页面，Resolve失败没有游戏效果。泛化新物品依公开type/文字/关系，不能把全游戏固定物品ID词表当唯一输出空间。10k压力测试只证明接口/预算/绑定机制，不证明真实游戏发生过10k动作或模型会选得好。

### 9.3 卡牌详情和升级查看

原生牌组页→InspectCard c1→当前详情→ToggleUpgrade→升级预览→ToggleUpgrade/Next/Back。Toggle是独立公开变化，不是实际升级。Agent固定程序可以不调用scorer选择机械Back，但原生预览内容仍属于该Agent曝光和状态合同；训练不能把该固定选择冒充Human模型监督。

### 9.4 不给内部模型看全部动作

Agent收到完整目录引用和当前公开对象；轻量模块提出“查看遗物r3”。生成/小模型只处理相关公开对象和操作schema；Agent用当前prefix/Resolve核对，再提交。未展示资料仍必须通过原生打开才能得到。若轻量模块决定了筛选/目标，它属于Agent策略并计成本；主模型没有看过不等于整个Agent没有使用。

### 9.5 原生reward回访

第一次开→看牌→返回→处理别的reward→再次开。内容可同但occurrence新。Agent可以记忆；环境不标“这次必须领取”。若一直原地循环按第8节定位：事件丢失修接口，状态错误修Agent，输入/状态正确修策略；安全停止不是策略修复。

### 9.6 模型内部选family和真实分步动作不同

模型在同一O0内部生成 `operation=inspect_card, subject=c1` 是一项行为，两个token/两个head不产生两条原生动作。相反 BeginCard 后出现新的 target preview/取消机会，是环境真的发生新阶段，不能因为一个模型能一次输出 card+target 就自动跨过；若Agent内部程序串行做，仍保留实际AP事件和中断机会。

## 10. 第一版如何评价、修正和决定是否妥协

| 门 | 必交付的证据 | 失败如何处理 |
| --- | --- | --- |
| 语义覆盖 | L01–64 每项按适用/原生不存在/源码缺口/已测标注；范围内native caller/controls反向核对 | 必需原生动作漏映射不能发完整profile；不删除失败行 |
| 信息保真 | 卡牌详情/升级/目标切换、星费/附魔/tooltip、Peek、同名实例、未揭示页、长列表 | 不一致追原生显示/绑定/字段owner，不由模型估算填空 |
| 合法集合 | 全量C与prefix/List/Resolve集合一致；错pair、歧义、stale、代次、权限、unknown反例 | 守卫失败阻断发布；不能放宽原生验证 |
| Agent通用接口 | 全量scorer、确定性非scorer生成stub、prefix chooser、abstain/yield均能绑定同一C；旧端口不变 | 不用假scores伪装新Agent支持；无需先训练所有模型 |
| 规模与成本 | 合成10/100/1000/10000目录；实际局面尺寸分布；Host/传输/编码/W/scorer/落盘分测 | 找实际瓶颈；合成不充当实机延迟/策略证据 |
| 连续性/恢复 | 打开/返回失败、目标消失、stale、接管、重复运输、loop stop、断线/原attempt核对 | 保留失败原件，未知不重发，必要时新segment |
| 学习接入 | 逐项关联L01–L64的Human capture、输入前页/目录/实际选择/曝光顺序；再验合格prefix→相应N目标→在线离线一致；无标签事件仍消费 | 不伪造Human查看、未来信息或未执行分支标签 |
| 任务有效性 | 目标选择、升级对比、嵌套selector、奖励回访、商店移除、终局；之后固定范围完整局 | 同时报成功、loop、介入/未知/删失、信息取得和资源成本，不只报动作投递率 |

比较模型时固定L-N语义、信息曝光规则、任务范围与数据划分；Agent获取策略不同可评价整个Agent，但不能叫纯模型架构消融。是否使用原生文本/typed输入、预训练、缓存、固定浏览程序、shortlist和额外loop提示，均入AgentSpec/ExperimentPlan。

公平比较全量评分和检索/生成时，报告候选访问数、实际昂贵评分数、索引/mask/编码成本、非法proposal率、最优项/示范项召回（有对应reference时）、任务质量与分组不确定性。完整合法目录保证可用操作，不保证任何策略最优。加入导航惩罚、自动返回或动作禁访属于新策略/任务条件，不能当普通“修loop”不登记。

提出妥协的门槛：至少有一个明确机制的确切源码/忠实反例或授权实测证明当前目标不能满足；先比较修原生映射、只降设备细节、Agent内部固定程序、无损表示/批量、模型算法调整，再评估改变信息/交互语义。**目前证据支持补覆盖和解耦scorer port，尚不支持整体放弃L-N。**

## 11. 当前源码依据和研究参照

### 当前源码：实际有与实际缺

全部链接固定在本轮基点，不代表最新installed producer。

- [NativeTextMenuCombat](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/cf65def910f1c2475a7c938d814e3cd49789f2fe/components/connector/host/PlayerEnvironment/TextMenu/NativeTextMenuCombat.cs#L157)：FocusTarget原生hover与ConfirmTarget分离；mouse owner currently限定AnyEnemy，controller/untargeted另有路径。
- [NativeTextMenuFrameBuilder](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/cf65def910f1c2475a7c938d814e3cd49789f2fe/components/connector/host/PlayerEnvironment/TextMenu/NativeTextMenuFrameBuilder.cs#L185)：持牌stage早返回；[显示读取](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/cf65def910f1c2475a7c938d814e3cd49789f2fe/components/connector/host/PlayerEnvironment/TextMenu/NativeTextMenuFrameBuilder.cs#L484)只要求title/energy/description，需补星费/焦点等；无active run或!IsInProgress目前无text leaves；所以本版策略入口明确局内，且必须新增终局/总结owner路由来完成L63，不能借这一guard省掉尾段。
- [NativeTextMenuInformation](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/cf65def910f1c2475a7c938d814e3cd49789f2fe/components/connector/host/PlayerEnvironment/TextMenu/NativeTextMenuInformation.cs#L1005)：deck inspect opener只遍历CurrentlyDisplayedCardHolders；[详情和升级开关](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/cf65def910f1c2475a7c938d814e3cd49789f2fe/components/connector/host/PlayerEnvironment/TextMenu/NativeTextMenuInformation.cs#L1048)有实际native capture/controls。
- [NativeUiActionRuntime](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/cf65def910f1c2475a7c938d814e3cd49789f2fe/components/connector/host/NativeUi/NativeUiActionRuntime.cs#L989)：close Peek已登记，未由此证明open可达；其他场景还需specialized registry和reader，不能只查一张verb表。
- [Runtime AdapterDecision](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/cf65def910f1c2475a7c938d814e3cd49789f2fe/components/policy-runtime/src/contracts.ts#L225)与[验证](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/cf65def910f1c2475a7c938d814e3cd49789f2fe/components/policy-runtime/src/contracts.ts#L442)：确实强制全候选scores，不是模型无关的新Choice端口。
- [M2 score](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/cf65def910f1c2475a7c938d814e3cd49789f2fe/python/stpd/models/light_action_m2.py#L182)：候选逐项轻量编码，读取同一W；不是每个候选完整重跑页面主干，也不是已消除候选开销。

本轮独立只读调查加主作者复核了原生NCard.SetPreviewTarget/UpdateVisuals、NCardPlay.OnCreatureHover、NTargetManager.OnCreatureHovered和上述关键入口；没有启动游戏、构建、安装或做新Human recording。无法从这些代码推出每张卡、所有target类型或显示字段均已运行合格。

### 外部研究只作架构依据

- [OpenAI Five论文附录动作空间](https://arxiv.org/html/1912.06680v1)：primary action＋参数化输出与mask是已有路线；其一些非法组合采用no-op，并非本项目应复制的执行策略。我们仍strict resolve/revalidate，不以论文成功授权放松边界。
- [Deep Reinforcement Learning in Large Discrete Action Spaces](https://arxiv.org/html/1512.07679v2)：先产生动作表示、检索子集再重排提供减少昂贵全评分的研究依据；近似和召回会影响结果，不是无损argmax证明。
- [Transformers生成接口](https://huggingface.co/docs/transformers/main_classes/text_generation)：prefix_allowed_tokens_fn支持按前缀约束生成；仅是实现工具，项目的合法前缀来自Connector完整关系，不能把语法约束当原生合法性。

后续仍沿用E1环境/接口、E2记录、E3封装Agent、E4学习/评价及G1/G2/G3的既有任务编号。此文完成的是可审查的v1规格与范围决策材料，没有修改任何生产合同、BOM、模型或运行数据。
