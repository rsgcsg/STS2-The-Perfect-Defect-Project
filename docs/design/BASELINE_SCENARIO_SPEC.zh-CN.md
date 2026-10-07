# P5 场景设计册：原生事实、抽象信息与逐步操作

日期：2026-10-06。版本：0.2。状态：**设计审查候选；原生源码检查与合成探针不是实机资格。** 本册细化 P0 的 SC-01–16，不创建新的任务编号。SX 是场景卡编号，EX 是例子编号。

完整阅读入口：[P0–P5 交付与验收](BASELINE_ACCEPTANCE_PLAN.zh-CN.md)。候选及信息策略见 P3；D-M2、记录/训练/工程连接见 P4。最新[三套契约与四个组合](BASELINE_PROTOCOL_OPTIONS.zh-CN.md)明确 AP/IP/OP/HC/MP；C-H 归查询信息配置，此前首发推荐撤回。本文的原生机制仍保留，资料默认提供与否以选定 IP 为准；本轮四案共同 OP-S 保留真实原生选择阶段。

L-N首版动作范围、缺口和完整合法关系详见[具体规格](BASELINE_LN_V1_SPEC.zh-CN.md)。原生目标focus引起的渲染数值变化、卡牌inspect升级切换属于首版要求；64行承诺不是当前已运行支持数量。

排队出牌和延迟取消按[异步时序专题](BASELINE_LN_QUEUED_TIMING.zh-CN.md)解释。场景中的“反馈后继续”不要求前一效果完全结算；保留原生input-ready和Human输入时点，不用执行S重写Human看到的H。

## 1. 哪些是原生，哪些是我们的抽象

| 标记 | 含义 | 例子 |
| --- | --- | --- |
| N-G | 游戏本身拥有的规则、选择或效果 | 扣费、打牌、选牌结果、奖励领取、地图旅行 |
| N-U | 原生呈现/输入行为 | 打开牌堆、Peek、目标焦点、原生确认/返回按钮 |
| P | 协议提供的表示、聚合、请求和事件包装 | 结构化资料、有限目录、对象别名、显式结果种类 |
| A | 封装 Agent 内部计算/状态 | D-M2 更新 W、候选推演、查询策略、选择 |
| M | 管理与运行监督 | 建局、控制权、保存恢复、预算停止、安装 |
| R | 研究与数据解释 | N/Z/O 标签、切分、指标、实验条件 |

协议动作名称本身均为 P 表示；其实际执行可能对应 N-G、N-U 或纯 P。一个操作可以有多种效果，不用单一标签掩盖 UI 暂停、游戏效果或异步延续。P/A/M/R 不能创造 N-G 合法性。

## 2. 每张场景卡都采用同一公共封套

必须有：环境/协议/profile/代次；观察与公开事件位置；当前问题/输入 owner 绑定；完整当前操作目录；公开内容及缺失/不支持原因；父子关系的已知程度；最近请求状态。私有绑定证明、native 类型、lease、原始 seed、路径不进入模型。

对象字段分开：稳定的公开别名、类别、公开名称/描述、当前可见属性、选中/可操作状态及其来源。两张同名牌保留两个对象，预览 clone 不是执行 operand。数值/说明使用当前原生公开显示语义，不由 Agent 或协议另算游戏规则。

**选择器字段：**选择来源/公开 pile、prompt、完整逻辑候选、已选集合及原生有意义顺序、请求 min/max、原生实际可选/可撤选/可确认/可关闭/可预览集合、阶段、手动确认与自动完成条件的已知事实。请求阈值和原生实际可用状态分开，不能自己用 raw min/max 重建合法性。

**结果词汇：**请求拒绝、已投递、投递未知；正常选中结果、正常空结果、正常 null 目标取消、仅退出预览、Task 取消、绑定不可读、父过程仍在等待、已观测新 owner、已证明因果后继分别记录。空目录不自动表示终局。

**资料规则：**只有“此时原生玩家可合法取得、提取不改变声明游戏效果/机会”的资料才可纯 Read/自动附带。当前战斗/牌组等资料是否附带由所选 IP 明定，不由 runner 猜何时给；原生可达证明失败不能读取内部数据补足。AP-L 按原生展示，AP-Q 按登记 Inspect（可走原生或直接读取），AP-B 按一致资料包；有原生 UI 效果的 Inspect 不能冒充纯 Read。固定自动 view 和 Agent 请求都记录实际曝光及成本。

**失效：**每次真正原生变化、选项重建或阶段变化都重核 binding/catalog；查询结果保留原时点，旧动作不因字符串仍存在而有效。轮询重传不变成新经历，真正重访不能按相同内容去重。原生明示的输入接受时机必须遵守，不能以自定义 sleep 猜 owner-ready 或因果完成。

## 3. 逐场景动作与信息设计

### SX-01 新局、继续、接管和管理恢复（SC-01/12）

- **入口与信息：**任务已指定角色/模式/起点；公开首个 owner、实际环境身份、起点类型、历史可用范围。选择种子和导入存档属 M，不自动给策略私有存档或随机种子。
- **步骤：**M 创建/连接→验证实际实例→提供公开初始观察→A 初始化或按已证历史恢复→领取控制→按 gameplay 目录行动。新局与 Agent 新 segment 是两件事。
- **恢复：**同一实例重连必须证明连续性和事件位置；没有可靠历史时 reset/censor，不装成从开局一路运行。旧请求被代次隔离。
- **EX-01 正常：**已有模型接同一个 Host 实例，工作台不另开一局。**EX-02 反例：**相同种子重开并导入旧 W，声称半局无缝恢复。

### SX-02 地图资料与旅行（SC-01/03）

- **信息：**公开节点/连接/类型、当前和已走位置、原生可旅行目的地、当前旅行可用性；节点隐藏的事件结果不提供。
- **操作：**P.choose_destination 绑定 N-G 当前目的地及原生旅行路径；P.read_map 是有资格的资料读取。C-L 另有 N-U open/close map，close 不等于 travel。
- **步骤：**选择目的地→原生 vote/旅行接受→自动进房/等待→新 owner；不能把 enqueue 当到达。Hook 可能限制继续。多人成员投票/ping 是另一模式，首版未资格化时明确拒绝。
- **EX-03：**战斗中合法查图后继续当前战斗。**EX-04：**按地图坐标直接修改游戏位置或提前提供节点事件内容。

### SX-03 普通、精英、Boss 战斗（SC-02）

- **信息：**当前回合/阵营/阶段、公开 HP/block/能量/其他资源、手牌完整逻辑列表与当前费用/描述/升级、敌人公开属性/意图/状态、药水和公开持有物、牌堆计数。牌堆内容按 profile Read/附带规则，不含隐藏顺序。
- **操作：**play(card,target-or-none)、end_turn、use/discard potion 只列原生合法且可交付的确切组合。目标种类差异由原生 validator 决定，不把所有 TargetType 简化成“敌人或 null”。
- **步骤：**全目录冻结绑定→A 对全部候选读取同一 W→P 提交选择→原生复验→投递/拒绝/未知→原生执行或子选择→后续公开事件。普通无中间独立问题的机械输入可压缩，但现有未完成原生阶段必须处理。
- **EX-05：**两张同名不同升级/费用的牌分别绑定。**EX-06：**回合切换后仍提交旧手牌动作，必须未投递/失效而非猜新牌。

### SX-04 已开始的卡牌目标或确认（SC-02/04）

- **信息：**确切 pending card、目标管理器对应本次操作的证据、当前可命中/可选择目标、是否在原生可确认位置。鼠标和控制器机制差异留在 Host，不让模型学设备私有类型。
- **操作：**N-U focus/target/confirm/cancel 当前卡牌过程；无目标或全体目标的原生确认不创造虚构 target。取消 target 返回 null，可导致调用者取消卡牌，不是 Task canceled。
- **禁止：**不能仅凭全局 target manager 存在绑定上一个动作；不能把 native null-target 分支当任意目标跳过。
- **EX-07：**pending 全体攻击只提供原生确认/取消。**EX-08：**原生目标已消失，不能将对象序号移给另一个敌人。

### SX-05 药水弹窗与目标过程（SC-02）

- **信息：**确切 slot/potion、公开说明、use/discard 实际可用性、当前用药条件及目标目录。弹窗关闭、目标取消、药水消耗分开。
- **步骤：**打开弹窗（N-U）→use 启动 holder 流程并可能先移除 popup→target await→选 creature/player/merchant 等确切目标或原生取消→真正 enqueue/use。discard 有独立原生 action。
- **动作粒度变体（不属当前 OP-S 默认）：**只有确切完整绑定与逐机制等价依据成立时才可合并 use/discard 流程；TargetedNoCreature 等仍保留真实目标阶段，不能随便传 null。Self potion 与普通 null-target 也不混用。
- **EX-09：**目标取消未 enqueue，不标药水已使用。**EX-10：**仅关闭 popup 就给 O/状态记一次消耗，属于错误。

### SX-06 手牌简单选择与手牌升级（SC-04）

- **信息：**原生筛选后的当前手牌、已选容器、prompt、原生确认状态、Peek 所需公开战场。被筛掉的牌不能作为可选项；未选手牌的公开内容是否可见由当前信息规则决定。
- **操作：**select/deselect/confirm；简单手牌满选时原生可以撤下最后一张并替换，这与网格“满了不能加”不同。手牌升级使用真实 preview 和替换行为。
- **自动路径：**无合格牌可直接空返回；某些非手动或单候选情况直接选取，不造虚拟 Human click。NPlayerHand 退出可能以空结果完成等待，而不是 Task canceled。没有证据就不提供通用 cancel 按钮。
- **EX-11：**已满一张再选另一张，记录原生替换。**EX-12：**复用网格 max-cap 规则阻止原生可用的替换。

### SX-07 简单网格与生成牌（SC-04）

- **简单网格：**完整候选、prompt、selected、min/max 和实际 enabled 控件；toggle 加选受 max 限制，不采用手牌替换。达到自动完成条件或手动确认时返回结果。
- **生成牌：**候选必须来自已发生的原生生成，显示公开详情与实际 canSkip；select 返回牌、skip（若原生允许）返回空。原生命令零候选可能直接返回；屏幕退出可取消未完成 Task。
- **时机：**NChooseACardSelectionScreen 有原生 opening guard，不能仅看到 holder 就宣称动作已可接受；观察该机制，不通过一次外部计时推断完成。
- **EX-13：**min=0 时原生 enabled confirm 保留。**EX-14：**把零候选不分原因统一标 terminal，或在生成前提供备选牌。

### SX-08 战斗牌堆选择（SC-04）

- **信息：**当前 pile 类型、完整逻辑有效候选、过滤后 selected、动态牌状态、实际 confirm/select/deselect，公开战斗上下文；draw 展示排序不等于真实抽取顺序。
- **操作/动态变化：**toggle；满选不可额外加牌；牌堆变化会移除不再有效的已选牌。原生确认条件使用有效当前候选下的阈值，不能仅用原 prefs。空有效 pile 或已选完全部有效牌可自动完成。
- **结果：**确切选择列表或空结果；退出未完成的 grid Task 可以 canceled。父 CardSelectCmd continuation 单独归属；不能从 selected card 推测未经记录的 pile 来源。
- **EX-15：**raw min=2、当前有效牌只有 1、原生 confirm enabled，协议保留 confirm。**EX-16：**当前 adapter 用 raw min 拒绝该确认，是待修的 source/native 差异，不能由模型补救。

### SX-09 牌组通用选择与商店移除子过程（SC-04/07）

- **信息：**确切 eligible 原牌、选择范围、selecting/preview 阶段、当前预览和可用控件；预览副本不能作为最终执行对象。
- **步骤：**select/deselect→达到条件或按确认进入 preview→preview confirm 返回原牌；preview cancel 清选并回 selecting；whole close 仅原生允许时空返回、移除 overlay。退出 Task canceled 与主动 close 不同。
- **商店移除：**服务打开→本 selector→最终确切返回牌后才扣金、移牌、增加移除次数。取消不提前收费。
- **EX-17：**预览取消后继续换牌，父效果仍等待。**EX-18：**提供 remove(card) 复合操作却跳过一次有意义的预览/取消机会，未证明等价前不得采用。

### SX-10 牌组升级、变换与附魔（SC-04）

- **升级：**原生 upgradable 集；单选可直接进入 upgrade preview，多选按原生 max 规则；取消预览回网格，最终确认返回原牌。不能拿 generic min 条件代替升级的实际完成条件。
- **变换：**确切原牌、原生 transformable 条件；preview 只包含原生已经展示的内容，不主动调用变换函数以窥视未来 RNG；取消预览清状态，确认返回。
- **附魔：**当前 enchantment 的公开名称/效果/数量、可附魔牌、selecting/preview、原生实际 preview/confirm/cancel/close 控件。现有 adapter 是 bounded source，不自动证明任意附魔组合。
- **EX-19：**原生显示的升级预览作为公开事实，执行仍绑定原牌。**EX-20：**把预览 clone ID 当原牌，或在原生未揭示时生成变换结果。

### SX-11 卡包、遗物选择和扩展 selector（SC-04/08）

- **卡包：**包为选择单位，保存每包成员和对应原生 bundle owner；不把一个包拆成可任取的单牌。目录来自原生当前集合，不能按显示位置自造 bundle。
- **Boss/其他遗物选择：**确切 parent command、当前 relic 集、skip 能力与 completed 状态；选/跳过的原生结果和 parent continuation 分开。不能用宝箱 skip 语义解释所有 relic selector。
- **未知新 selector：**显式 unsupported 并保留观察/原因，不调用任意反射、不借最近 root；它留在覆盖缺口，不算“已覆盖”。
- **EX-21：**两个包包含同名牌，按包身份选择。**EX-22：**遇到未支持 selector 自动选第一项以保证流程继续。

### SX-12 外层奖励：独立组与终结（SC-05）

- **信息：**当前 RewardsSet、terminal/nonterminal、各 reward 的公开类型/状态、独立与互斥 linked group、当前 skip/proceed 可用性。
- **操作：**claim/open 对应 reward；非终结页面可有最终 SkipLocalRewardsSet；最后一项领取可自动关闭非终结页。终结奖励可能先标完成但保留 Proceed。
- **信息配置约束：**只附带已合法公开内容和当前牌组等约定上下文；不把已经在内部生成但仍未授权揭示的卡牌提前给出。
- **EX-23：**独立奖励 A 查看后返回，再处理 B。**EX-24：**将所有外层 continue 都解释成“直接到下一房间”。

### SX-13 卡牌奖励、reroll 与特殊替代（SC-05）

- **信息：**当前完整 offered cards、原生 alternative 的公开 label/实际操作类别、选择资格、确切 parent/cards generation；新 profile 附带牌组必须有同时点合法来源。
- **原生区别：**普通 Skip 可为 EndSelectionAndDoNotCompleteReward，关闭内页且保留外层 reward；REROLL 可为 DoNothing 加 Reroll，更新当前目录；特殊 SACRIFICE 可 EndSelectionAndCompleteReward 并触发其他效果；不能从本地化文字猜含义。
- **步骤：**选牌→原生加牌/Hook→可能继续选择或完成；reroll→新候选/alternative generation→重新捕获；普通返回→外层 reward 仍 pending→Agent 可凭历史决定再次进入或最终离开。
- **EX-25：**首次看牌后返回，第二次列表同内容但事件不同。**EX-26：**把 sacrifice 当普通 return，或者复用 reroll 前的候选 ID。

### SX-14 Linked reward（SC-05）

- **信息：**明确互斥 group 与 children 的公开内容、当前可选子项；不能把 linked group 标成独立可全部领取。
- **步骤：**选择某 child→原生成功领取→原生收走 group、执行其他项的 skipped 处理。父 group 完成不代表此前所有 child 都被玩家逐一选择。
- **EX-27：**只领取一项，其他项因原生互斥被移除。**EX-28：**转换记录时为每个被移除 child 生成 Human skip 标签。

### SX-15 商店入口、库存与购买（SC-07）

- **信息：**room/inventory 阶段，当前 card/relic/potion/removal offers、原生价格/stock/affordability、金币、药水空间与当前特殊条件。
- **步骤：**N-U open inventory→房间按钮暂不可用→purchase wrapper 检查 stock/gold→真实购买与 after-hook→可能售罄/补货/新问题→重新捕获；close 恢复 room，proceed 可先出现 FTUE。
- **后续映射变体：**只有证明原生时机和副作用等价才可用结构化库存替代展示步骤；当前四案不自动执行开店，但 purchase 必须继续调用原生并重新验证。补货内容在原生发生前不可提供。
- **EX-29：**购买后价格/目录改变，旧绑定失效。**EX-30：**打开移除服务就扣费，或模型根据未生成补货选择购买。

### SX-16 事件（SC-06）

- **信息：**当前标题/正文、选项文本、公开锁定状态、公开警示/hover/relic 信息、当前已选状态；不提供未来段落或内部随机分支。
- **操作：**只执行当前 exact option；Proceed 与需要同步的选择分开；无普遍 cancel/back。选择可能进入战斗、奖励、selector 或 custom layout。
- **步骤：**选择→原生事件/同步→新正文/options 或子过程→重新捕获。嵌入战斗后的终结奖励可能返回父事件，不是地图。
- **EX-31：**事件选项触发战斗，胜后回事件继续。**EX-32：**把所有事件结束都当房间退出，漏记返回的选择。

### SX-17 营火（SC-08）

- **信息：**当前 RestSiteRoom.Options、公开名称/描述/可用性；Hook 提供的选项原样纳入完整目录，不预设固定两项。
- **步骤：**选择 option→效果/子选择/VFX→成功后刷新 remaining options 并提供真实 Proceed；可能还允许再次选 option。Smith 取消选牌可以返回 false，不当作锻造成功；Dig 等新随机结果不提前公开。
- **EX-33：**支持多次行动的条件下继续提供剩余原生选项。**EX-34：**第一次行动后强制自动走地图，或把取消升级计成已升级。

### SX-18 宝箱与宝箱遗物（SC-08）

- **信息：**closed/opening/奖励子过程/relic collection/resolving/completed 阶段，已揭示的候选与原生 skip/proceed。
- **步骤：**open chest→DoNormalRewards→DoExtraRewardsIfNeeded→才初始化可见 relic collection；pick 原生同步；空宝箱可自动结束；单人 SkipRelicLocally 可直接走 terminal-reward proceed/map。
- **EX-35：**额外奖励 overlay 先处理，之后再见宝箱遗物。**EX-36：**提前附带尚未生成/揭示遗物，或把宝箱跳过当卡牌奖励的暂时返回。

### SX-19 跨幕、自动延续与等待（SC-09）

- **信息：**当前 terminal reward、boss/act readiness、已有公开 continuation；没有 owner 时 settling，不造“默认继续”动作。
- **步骤：**native Proceed→可能请求 act change readiness并禁用按钮、第二 Boss 分支、父事件恢复或 map；等待原生新 owner。投递、Commit、act entered 分别有证据。
- **EX-37：**已发 readiness 后不因画面没变重复提交。**EX-38：**把队列空、等待超时或 Task 完成当成已证明的跨幕后继。

### SX-20 游戏结束与退出（SC-10）

- **信息：**原生胜利/失败/放弃和仍未知的项目、终局展示阶段、公开 summary；task-censored 与原生 loss 分开。
- **操作：**原生允许的 continue/view summary/return menu；进入新局必须重新建立 generation。结束报告由 R 绑定任务与介入，不让 Agent 读尚未发生的终局目标。
- **EX-39：**原生死亡有确切终局，O 标签可按对应链资格生成。**EX-40：**关闭游戏或人类接管后获胜，直接标成原 Agent 自主胜利。

### SX-21 教程、异常 overlay 与模式差异（SC-01–10）

- **信息/操作：**只识别已限定的当前 native modal、公开说明和可确认控件，保留返回原 owner 的关系。管理侧可以按声明任务配置教程偏好，但不能临时跳过一个正在影响交互的页面而不记录。
- **边界：**custom event、额外 Hook、多人 vote/ping/授予机制都必须单独覆盖。未支持时交还控制/报告 gap，不让“默认最强 Agent”自行绕过原生规则。
- **EX-41：**商店 Proceed 遇教程，先处理已支持 modal 再重新观察。**EX-42：**把未知 overlay 的任意按钮坐标加入候选目录。

## 4. 共用跨场景反例

| 例子 | 正确处理 |
| --- | --- |
| EX-43 相同事件重传 | 去重，不重复写 D-M2 状态 |
| EX-44 相同页面的新重访 | 新 occurrence 保留，允许状态不同 |
| EX-45 同 ID 换内容 | 拒绝并诊断，不无声覆盖 |
| EX-46 Read 期间状态变化 | 保存来源时点，动作重新绑定；不能把旧响应当当前 |
| EX-47 某个超限候选 | 拒绝整个不可表示目录或使用已声明完整分块，不能静默 top-k |
| EX-48 候选 what-if | 只读当前 W；不能写实际 Agent 历史 |
| EX-49 当前 Human label/teacher | 训练 target，不进入当前 writer/在线输入 |
| EX-50 丢提交回复 | 查原 request/attempt；不换 ID 重发 |
| EX-51 cancel-requested | 未确认终态前仍不是 stopped |
| EX-52 训练 checkpoint 改配置 | 新 run/明确初始化，不冒称原样恢复 |
| EX-53 共享撤销与本地副本 | 按逐类政策核对后续 use，不能宣称远程消灭字节 |
| EX-54 游戏暂停/模型停止/面板关闭 | 各自生命周期分开，不互相冒充 |
| EX-55 Guard 停止循环 | 监督通过，Agent 任务可能失败，分别计数 |
| EX-56 当前层/最后 HP | 不是失败层/战斗末 HP，缺对应终点就 mask |
| EX-57 一次终点复制多步 | 按同 run/combat group 计有效终点，非独立样本倍增 |
| EX-58 短局早死少出牌 | 可预测计数，不自动得到更高策略效用 |
| EX-59 宿主只支持部分 profile | 明确拒绝或不同身份，不静默换协议 |
| EX-60 新 Actor 接管但历史有缺口 | 标记 gap/reset/删失，不能靠当前图像恢复旧 W |

## 5. 证据、精度与覆盖的准确范围

上述原生描述基于本机 v0.111.0 / 41cef1ea 对应 DLL SHA256 9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4 的只读反编译和 c5ddc267 对应源码。主要符号与复核方法见 [P5 证据](../evidence/BASELINE_P5_EVIDENCE_2026-10-06.md)。没有提交游戏或反编译文件。

这是机制级详细设计，不宣称穷尽每张卡、每个 relic hook、每个 custom event 或所有多人组合。协议通过动态原生目录覆盖数据变化，通过明确机制注册覆盖行为变化；未知机制进入 gap 清单，不能假装本册已证明支持。E1/P5 后续运行验证必须在真实上层调用者、超长列表、特殊 Hook、取消路径及终态上检查。

精度分别评价公开事实保真、对象/时点绑定、操作效果、时序/成本、记录转换和因果资格；不汇总成一个任意百分比。原生信息集合相同不足以证明 UI 导航、免费聚合和查询在所有任务下等价。
