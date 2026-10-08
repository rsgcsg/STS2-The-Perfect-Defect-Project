# 完整 G2／V1 执行与验收矩阵

日期：2026-10-08。起点 `develop@4256f952e6db3983b259f805121571c8d71a7f6c`；主 writer 分支 `codex/g2-v1-convergence`。**实施与验证进行中，G2/V1 未获用户批准。** 本包包括原定门之前的全部必需工作，不用 S0 代替完整范围。

[唯一规范](../BASELINE_V1_SPEC.zh-CN.md)拥有技术选择；[任务表](BASELINE_TASKS.zh-CN.md)拥有 19 个编号/依赖；本文件管理执行与验收位置。实际 source/runtime/data 原件才是证据，独立审查见[本轮审计](../evidence/BASELINE_G2_V1_AUDIT_2026-10-08.md)。

## 1. 完成与审批规则

每项区分设计、implemented、source-tested、exact-built/installed/loaded、runtime、capture/data/learning、独立 review 和用户批准。基线、actor、setup/自主、sampled/完整曝光不同不能互借。`不适用`需要确切原生/配置理由和独立核对，不能代替未实现。

历史 S0 的[实证报告](../evidence/BASELINE_S0_LEARNING_LOOP_2026-10-08.md)保留 174 offers/172 labels、45 updates、11 native play、确认 Stop 等原范围证据。新 profile/产物/数据重新核受影响资格。全部 required 格补齐后才标 `ready_for_owner_review`，用户批准后才填 G2/V1 通过。

## 2. 工作包、连接与顺序

| 原任务子包 | 第一错误事实／交付 | 依赖、writer 与当前状态 |
| --- | --- | --- |
| P0–P5/G1 对账 | 原需求未完整映射，旧状态仍称未启动；分清抽象/协议/实例 | lead 汇总独立审计和历史修正；完整规范/矩阵工作稿 |
| E0.1 | 旧 PR/组件的唯一增量、复用/改造/保留/替代依据；治理与测试经济性 | lead 盘点，不清空旧资产或盲合 stack |
| E1.1 | native selector enabled 与额外 raw-min/manual veto 冲突 | native writer 独立分支；精确原生核对后修复/回归中 |
| E1.2 | 完整 native logical relation、读取/分页/Resolve、事件、终局 | 先共享 wire/源时钟/失败样例；Connector 一 writer，合同细化中 |
| E2.1 | 原生 UI 来源与 Human 来源混同；观察/输入/持久化未统一 | shared freeze→Annotator→Evidence additive source-aware 流；待 wire |
| E2.2 | 原件→投影/历史/目标/use，无动作/缺口/假设资格 | 与新 InputSpec 同版；旧数据不改义，待实现 |
| E3.1 | 普通奖励 teacher 与公开 verb 不匹配；runner 丢 Host close 回执 | runner writer 独立分支，两个 owning correction 分别回归 |
| E3.2 | 通用端口无 no-action consume/Act/Await，Agent 状态恢复不全 | Runtime/SDK/研究 adapter 同一合同，待实现 |
| E4.1 | 结构训练仅 final checkpoint、无 exact resume | model writer 独立分支；安全边界/身份/attempt 契约已讨论，实施中 |
| E4.2/E6.1 | 新模型不在普通 prepare/train/export/register/load 用例 | 单 operation/journal＋静态可信 recipe/Agent adapters，合同细化中 |
| E4.3 | 原 Stage1a 约 10k 合格交互训练尝试、K/Reset、backbone、N/Z/O 有界比较 | 按数据/目标资格和累计预算安排实际执行；Gate/R 条件触发，缺项未完成 |
| E5.1 | 本地/远端 intent/attempt/cancel/reconcile、权限/用途/派生传播 | 复用既有 owner；指定资源实际链与故障矩阵待完成 |
| E6.2 | 游戏内核心用例未贯通，浏览器首页链接不足 | 同 backend 的 CLI/API/外部/native UI，待 service 合同 |
| V1.1 | 全机制、角色难度及真实场景资格 | 最短原生用例→连续默认 Agent；setup 单列 |
| V1.2 | 全用户旅程、恢复、安装回退、远端和权限 | 同一推荐组合；AI 功能操作如实记录来源 |
| V1.3 | 资源、200/500 功能、10000 压力、30 attempt 稳定性 | pilot 固定测法/资源，再冻结候选与批次，保留全部结果 |
| V1.4 | 冷工程师复现、跨层复核和最终材料 | required 格完成后独立审查，最终交用户批准 |

共享合同先审；独立 owning correction 可先做。一个安装/控制 owner，一个重本地 build/train 槽；writer 不改他人的工作树，lead 自己的改动同样独立审查。已有必要操作授权继续，付费累计上限 20 USD；当前累计 0，无新付费任务。

## 3. 要求矩阵（12 项）

| ID | 真实需要 | 可观察结果 | 状态 |
| --- | --- | --- | --- |
| REQ-01 | 玩家与研究者需要实际有效的 Agent | 能完成声明任务；报告结果、失败、接管和成本 | 待全范围实现/证据，按场景和产品交叉核验 |
| REQ-02 | Agent 需要公平且足够的信息能力 | 重要合法信息通过声明的观察、操作或历史路径可达；隐藏信息不混入 | 待全范围实现/证据，按场景和产品交叉核验 |
| REQ-03 | Agent 需要可靠完整的交互能力 | 声明范围内有完整目标操作，确切绑定，失效与未知可解释 | 待全范围实现/证据，按场景和产品交叉核验 |
| REQ-04 | 模型研究需要封装 Agent 与不同计算/状态设计 | 对外是一个个体；信息取得、表示、状态和选择在登记边界内；D-M2 为当前贯通实例，其他设计仍可扩展 | 待全范围实现/证据，按场景和产品交叉核验 |
| REQ-05 | 采集者需要可验证且低干扰的记录 | 保留实际事实、来源、顺序、缺口；可以判断支持哪些用途 | 待全范围实现/证据，按场景和产品交叉核验 |
| REQ-06 | 数据使用者需要可解释的转换 | 原始证据能按明确规则形成目标协议轨迹、样本或被排除 | 待全范围实现/证据，按场景和产品交叉核验 |
| REQ-07 | 学习者需要训练与运行语义一致 | 同一输入与状态规则在线、离线相符，产物可回到环境 | 待全范围实现/证据，按场景和产品交叉核验 |
| REQ-08 | 研究者需要可归因的评价 | 控制信息、历史、数据、资源和环境条件，记录曝光与独立性 | 待全范围实现/证据，按场景和产品交叉核验 |
| REQ-09 | 开发者与 coding Agent 需要可编程工具 | 内部库、CLI、API 可完成主要用例，GUI 调用相同服务 | 待全范围实现/证据，按场景和产品交叉核验 |
| REQ-10 | 本地用户和团队需要产物流通与计算 | 相同任务可在符合条件的资源上执行，权限、预算和恢复明确 | 待全范围实现/证据，按场景和产品交叉核验 |
| REQ-11 | 用户需要可靠安装和长期使用 | 推荐组合、实际版本、停止、更新、回退与数据保全可核验 | 待全范围实现/证据，按场景和产品交叉核验 |
| REQ-12 | 工程师需要快速稳健的开发 | 开工有依据、责任和复用位置；小步验证、清楚交接、有限等待 | 待全范围实现/证据，按场景和产品交叉核验 |

## 4. 完整场景矩阵（16 项）

每格最终链接 native/contract source、faithful tests、exact artifact/run、capture/data、默认 Agent 行为与失败分母。未到达场景用有来源的 setup 单独验证，不能填成自然到达。“待验”不是不支持，也不是通过。

| ID | 完整过程 | 必需边界／失败 | 状态 |
| --- | --- | --- | --- |
| SC-01 | 开局/继续、会话交接、地图旅行 | 新局身份、旧请求、可走路径；管理与 gameplay 区分 | 待完整验收 |
| SC-02 | 普通/精英/Boss 战斗、目标、结束回合、药水 | 完整候选、过期对象、不可用操作、自动效果 | 待完整验收 |
| SC-03 | 查牌堆、牌组、地图、说明等信息取得 | 时机/副作用/上下文成本；隐藏顺序；遗漏与失效 | 待完整验收 |
| SC-04 | 手牌/生成牌/战斗牌堆/牌组/多选选择器 | 父子关系、暂选、确认、取消、零选和未完成效果 | 待完整验收 |
| SC-05 | 奖励组、卡牌、替代选项、返回与重访 | 暂时离开与最终放弃的差异；同页不同历史 | 待完整验收 |
| SC-06 | 事件及后续选择 | 新信息、随机效果、连续页面、未知结果 | 待完整验收 |
| SC-07 | 商店购买、移除、退出 | 价格/资源变化、嵌套选牌、重新进入 | 待完整验收 |
| SC-08 | 营火、宝箱、遗物选择 | 原生条件、选择/跳过、返回与后续流程 | 待完整验收 |
| SC-09 | 房间结束、自动推进、跨幕 | 输入尚未就绪、异步延续、不能靠轮询猜因果 | 待完整验收 |
| SC-10 | 胜负、放弃、终局与退出 | 原生结果、任务删失、接管后结果不得算纯自主 | 待完整验收 |
| SC-11 | 原生 Human→封存→验证→数据视图 | 无法捕获的内容、缺口、映射失败、来源混淆 | 待完整验收 |
| SC-12 | Agent 状态、接管、断线、停止与恢复 | 重复事件、历史缺失、状态不兼容、未知投递 | 待完整验收 |
| SC-13 | 数据→训练→checkpoint→评估→模型 | 输入不一致、历史泄漏、共同来源、恢复条件改变 | 待完整验收 |
| SC-14 | 本地和远端执行、任务归属与中断 | intent/handle/结果丢失、取消未确认、资源不足 | 待完整验收 |
| SC-15 | 团队加入、一次配置、同步、下载、权限与用途 | 离线、撤销、旧数据不自动上传、派生权限与字节保留 | 待完整验收 |
| SC-16 | 游戏内/CLI/API 工作流、推荐组合、更新重开与回退 | 面板关闭不丢任务、运行版本不等于分支、失败保全 | 待完整验收 |

## 5. 原生机制场景卡（21 项）

正常/反例沿原[场景册](../design/BASELINE_SCENARIO_SPEC.zh-CN.md)执行，EX-01–60 不删；新增反例进入 owning case。环境能执行、训练见过和 learned 自主到达分别记录。

| ID | 机制与上层关系 | 状态 |
| --- | --- | --- |
| SX-01 | 新局、继续、接管和管理恢复（SC-01/12） | 待 source/test/runtime、数据与消费者核对 |
| SX-02 | 地图资料与旅行（SC-01/03） | 待 source/test/runtime、数据与消费者核对 |
| SX-03 | 普通、精英、Boss 战斗（SC-02） | 待 source/test/runtime、数据与消费者核对 |
| SX-04 | 已开始的卡牌目标或确认（SC-02/04） | 待 source/test/runtime、数据与消费者核对 |
| SX-05 | 药水弹窗与目标过程（SC-02） | 待 source/test/runtime、数据与消费者核对 |
| SX-06 | 手牌简单选择与手牌升级（SC-04） | 待 source/test/runtime、数据与消费者核对 |
| SX-07 | 简单网格与生成牌（SC-04） | 待 source/test/runtime、数据与消费者核对 |
| SX-08 | 战斗牌堆选择（SC-04） | 待 source/test/runtime、数据与消费者核对 |
| SX-09 | 牌组通用选择与商店移除子过程（SC-04/07） | 待 source/test/runtime、数据与消费者核对 |
| SX-10 | 牌组升级、变换与附魔（SC-04） | 待 source/test/runtime、数据与消费者核对 |
| SX-11 | 卡包、遗物选择和扩展 selector（SC-04/08） | 待 source/test/runtime、数据与消费者核对 |
| SX-12 | 外层奖励：独立组与终结（SC-05） | 待 source/test/runtime、数据与消费者核对 |
| SX-13 | 卡牌奖励、reroll 与特殊替代（SC-05） | 待 source/test/runtime、数据与消费者核对 |
| SX-14 | Linked reward（SC-05） | 待 source/test/runtime、数据与消费者核对 |
| SX-15 | 商店入口、库存与购买（SC-07） | 待 source/test/runtime、数据与消费者核对 |
| SX-16 | 事件（SC-06） | 待 source/test/runtime、数据与消费者核对 |
| SX-17 | 营火（SC-08） | 待 source/test/runtime、数据与消费者核对 |
| SX-18 | 宝箱与宝箱遗物（SC-08） | 待 source/test/runtime、数据与消费者核对 |
| SX-19 | 跨幕、自动延续与等待（SC-09） | 待 source/test/runtime、数据与消费者核对 |
| SX-20 | 游戏结束与退出（SC-10） | 待 source/test/runtime、数据与消费者核对 |
| SX-21 | 教程、异常 overlay 与模式差异（SC-01–10） | 待 source/test/runtime、数据与消费者核对 |

## 6. 操作与可学习记录矩阵（64 项）

每行同时验操作可达、公开完整关系、记录/投影及实际条件。条件能力不存在须由 exact native/caller 证明。下表继承原 L 清单的操作和条件，不继承旧状态。

| ID | 操作／公开结果 | 原生适用条件 | 已知缺口与当前状态 |
| --- | --- | --- | --- |
| L01 | 打开本局牌组；返回真实先前 owner | 必需，原生入口此时启用 | 待默认 profile、native/record/data/Agent 联合验收 |
| L02 | 打开抽牌堆；返回 | 必需，原生战斗入口启用；提供完整公开成员，不给真实抽取顺序 | 待默认 profile、native/record/data/Agent 联合验收 |
| L03 | 打开弃牌堆；返回 | 必需，同上，保留 pile 身份 | 待默认 profile、native/record/data/Agent 联合验收 |
| L04 | 打开消耗堆；返回 | 必需，同上 | 待默认 profile、native/record/data/Agent 联合验收 |
| L05 | 打开地图查看；原生允许时关闭地图 | 必需；关闭与选路分开 | 待默认 profile、native/record/data/Agent 联合验收 |
| L06 | 对当前可 focus 的卡牌显示原生 tooltip；解除/转换焦点 | 必需，按手牌、网格、商店、奖励等实际原生能力 | 兼容实例有同卡提示重访；新 profile/来源链待验 |
| L07 | 进入卡牌详情；关闭到真实来源页 | 必需，在原生可进入详情的每一承诺页面；不为原生没有详情入口的卡凭空开页 | 待默认 profile、native/record/data/Agent 联合验收 |
| L08 | 在卡牌详情中切换升级预览开/关 | **必需且条件于原生开关可用**；提供切换后原生显示，明确 preview 非实际升级 | 待默认 profile、native/record/data/Agent 联合验收 |
| L09 | 卡牌详情上一张/下一张 | 必需，原生箭头可用；保留 native 列表与顺序 | 待默认 profile、native/record/data/Agent 联合验收 |
| L10 | 遗物 tooltip 显示/解除/切换 | 必需，原生可 focus | 待默认 profile、native/record/data/Agent 联合验收 |
| L11 | 进入遗物详情、上一件/下一件、返回 | 必需，原生 controls 启用 | 待默认 profile、native/record/data/Agent 联合验收 |
| L12 | 药水 tooltip 显示/解除/切换 | 必需，与使用弹窗分开 | 主动 potion-hover 待核补 |
| L13 | 角色/敌人状态效果、意图 tooltip | 必需，原生可显示的各对象 | 待默认 profile、native/record/data/Agent 联合验收 |
| L14 | 球与其他公开资源 tooltip | 必需，所选角色/Hook 中适用 | 待默认 profile、native/record/data/Agent 联合验收 |
| L15 | 原生角色/敌人本体的其他独立信息提示 | 条件必需：若确有与 L13 不同的公开内容和入口则纳入；若原生无此能力则明确 not_native_capability | 先核确切原生能力，不能凭名称造接口或删要求 |
| L16 | 顶栏 HP/金币/楼层/Boss/地图/牌组等提示 | 必需，原生显示的提示 | 待默认 profile、native/record/data/Agent 联合验收 |
| L17 | selector 的 Peek 打开/保持/返回 | 必需，原生提供此功能时；提供实际可见战场，返回原 child | Peek 对称 open/return 待补 |
| L18 | 已实际显示的关联关键词、卡牌预览、图标语义 | 当前视图的一部分，通常**不另造点击动作**；原生确有二级交互才登记额外操作 | 待默认 profile、native/record/data/Agent 联合验收 |
| L19 | 已显示的提示随焦点消失/替换、窗口自动刷新 | 公开事件；原生真实解除操作必要时目录提供，纯布局/动画不另造策略动作 | 待默认 profile、native/record/data/Agent 联合验收 |
| L20 | 开始出牌，进入原生持牌/目标或确认阶段 | 必需，原生当前允许 | 旧虚拟 staging 不替代真实 held-card |
| L21 | 将持牌焦点移向一个当前原生目标，**不确认** | **必需**；产生原生 target-dependent 预览 | 待默认 profile、native/record/data/Agent 联合验收 |
| L22 | 在不同目标间切换焦点 | 必需；提供每次真实渲染后的说明/数字、焦点对象 | 待默认 profile、native/record/data/Agent 联合验收 |
| L23 | 从目标移开/清除目标焦点 | 条件必需，原生允许；恢复 native 未定目标预览 | 独立 unfocus/clear 待补 |
| L24 | 确认当前目标；或原生一步指向并确认目标 | 必需，按已登记原生语义；可以选择先预览，也可以直接确认，不强制读每个目标 | 待默认 profile、native/record/data/Agent 联合验收 |
| L25 | 无单体目标/全体目标的原生确认 | 必需，有实际确认阶段才提供；显示 native multi-target 预览 | mouse/controller 无目标分支待核补 |
| L26 | 取消持牌/目标过程并回到原生阶段 | 必需，原生允许；不用药/出牌是否已发生按真实反馈 | 待默认 profile、native/record/data/Agent 联合验收 |
| L27 | 持牌期间可用的信息入口 | **条件必需**：此时原生仍可取得的必要提示/Peek/资料必须保留 | held early-return 可能漏可达信息 |
| L28 | 结束回合 | 必需，原生 enabled/ready | 待默认 profile、native/record/data/Agent 联合验收 |
| L29 | 打开药水操作弹窗 | 必需，原生当前允许 | 待默认 profile、native/record/data/Agent 联合验收 |
| L30 | 选择使用药水 | 必需，原生当前允许；可能进入真实 target stage | 待默认 profile、native/record/data/Agent 联合验收 |
| L31 | 药水目标的焦点/预览、确认、取消 | 条件必需：按该原生目标机制实际提供的机会 | 待默认 profile、native/record/data/Agent 联合验收 |
| L32 | 丢弃药水 | 必需，原生允许，走其确认阶段（若有） | 待默认 profile、native/record/data/Agent 联合验收 |
| L33 | 关闭药水弹窗 | 必需，原生可关闭 | 待默认 profile、native/record/data/Agent 联合验收 |
| L34 | 接收公开自动效果、敌人行动、新回合、原生排队/取消的公开表现 | 事件；原生重新input-ready即可决定，不必等先前效果全部结束；没有选择不造native no-op | required 事件、队列/取消与数据历史待补 |
| L35 | 手牌选择一张、撤选、原生满额替换 | 必需，原生候选与操作 | 待默认 profile、native/record/data/Agent 联合验收 |
| L36 | 手牌选择确认 | 条件必需，实际控件启用 | native enabled/自动完成须独立验证 |
| L37 | simple grid 选择/撤选/确认/整页取消 | 必需，按具体控件 | min0/manual 差异由 E1.1 修复核验 |
| L38 | 生成牌选择/原生 skip | 必需，生成和 opening-ready 后 | 待默认 profile、native/record/data/Agent 联合验收 |
| L39 | 战斗牌堆选择/撤选/确认/原生取消 | 必需，Draw/Discard/Exhaust 按实际来源 | raw min/effective candidates 差异由 E1.1 修复核验 |
| L40 | 通用牌组选牌/撤选/进入预览 | 必需，eligible 原对象 | 待默认 profile、native/record/data/Agent 联合验收 |
| L41 | 通用牌组预览确认/取消；整个选择过程关闭 | 条件必需 | 待默认 profile、native/record/data/Agent 联合验收 |
| L42 | 升级选牌/撤选/升级视图/预览确认/取消 | 必需 | exact caller/preview 条件待核 |
| L43 | 变换选牌/撤选/原生预览/确认/取消 | 必需 | 待默认 profile、native/record/data/Agent 联合验收 |
| L44 | 附魔选牌/撤选/预览/确认/取消 | 必需 | 待默认 profile、native/record/data/Agent 联合验收 |
| L45 | 卡包预览、包内原生详情、确认整包、取消预览 | 必需 | 待默认 profile、native/record/data/Agent 联合验收 |
| L46 | Boss/其他遗物选择、原生 skip | 必需，范围内发生时 | 待默认 profile、native/record/data/Agent 联合验收 |
| L47 | 多层 child selector 的上述操作 | 必需 | 待默认 profile、native/record/data/Agent 联合验收 |
| L48 | 空结果、零选、全选自动完成、退出取消 | 真实原生事件或已登记取消动作 | 待默认 profile、native/record/data/Agent 联合验收 |
| L49 | 选择地图目的地、退出原生地图标注模式（若当前有） | 必需/条件 | 待默认 profile、native/record/data/Agent 联合验收 |
| L50 | 外层奖励打开/领取 | 必需，当前原生项目 | 普通 reward teacher 修复审查中；环境/记录仍待验 |
| L51 | linked reward 子项选择 | 必需，原生互斥组 | 待默认 profile、native/record/data/Agent 联合验收 |
| L52 | 卡牌奖励选择 | 必需，完整当前候选 | 待默认 profile、native/record/data/Agent 联合验收 |
| L53 | 卡牌奖励返回/Skip、reroll、特殊 alternative | 必需，各真实 alternative | 返回/最终跳过/reroll/alternative 公开语义待完整核验 |
| L54 | 外层奖励继续/原生最终放弃 | 必需，controls 启用 | teacher progression 与 learned 自主性待实证 |
| L55 | 打开商店库存、关闭、房间 Proceed | 必需 | 待默认 profile、native/record/data/Agent 联合验收 |
| L56 | 购买商店卡/遗物/药水 | 必需，当前报价/库存/native affordability | 待默认 profile、native/record/data/Agent 联合验收 |
| L57 | 打开商店移除服务 | 必需，后接 L40–41 | 待默认 profile、native/record/data/Agent 联合验收 |
| L58 | 事件对话推进、选择选项、原生 Proceed | 必需，包括范围内内置专用布局 | 待默认 profile、native/record/data/Agent 联合验收 |
| L59 | 营火选项与 Proceed | 必需，动态完整 native options | 待默认 profile、native/record/data/Agent 联合验收 |
| L60 | 开宝箱、处理先行奖励、选择/跳过遗物、Proceed | 必需 | 待默认 profile、native/record/data/Agent 联合验收 |
| L61 | 教程启用/禁用选择、上一页、继续/关闭 | 默认管理准备关闭；教程profile可选 | 默认管理关闭；意外出现/专门测试仍登记 |
| L62 | 跨幕/第二 Boss 等原生继续与 readiness | 必需，范围内实际发生；高进阶扩展另资格 | 待默认 profile、native/record/data/Agent 联合验收 |
| L63 | 胜负/放弃公开结果、总结推进、返回菜单 | 必需 | 终局清空 leaves 缺口待修 |
| L64 | 局内退出/放弃确认流程 | 管理侧显式任务停止/用户控制；本版默认**不给策略自行重开/放弃来优化计数** | 管理权限，非默认策略动作；退出/来源/结果待验 |

## 7. 产品 A–H 与完整用例

| 门 | 必需实际证据 | 当前状态 |
| --- | --- | --- |
| A 基线可恢复 | code/package/install/model/game/service 配对；旧记录/队列、回退 | S0 局部身份已有；完整组合待验 |
| B 游戏内入口 | 配置/连接；任务可发现、操作、监测、回结果；外部上下文一致 | 完整入口未实现 |
| C 采集到数据 | start/pause/close、封存、队列、上传/核验/下载、来源/质量 | AI 功能和真实 Human 来源分开；完整链待验 |
| D 数据到训练 | 不可变数据/use/split、真实本地任务、progress、checkpoint 恢复/失败 | S0 数值链已有；普通应用链待补 |
| E 训练到模型 | dev/错误分析、来源/配置、导出/下载/注册/加载/切换 | 普通新默认路径待补 |
| F 全场景连接 | 全机制、默认 Agent 输入/动作、自主连续旅程、Stop/重连/终局 | reward 循环等缺口，未完成 |
| G 日常可用 | 无终端串流程；重开不丢任务；离线/权限/错误清楚；UI 不阻塞 | 待实现与 actual-user-path |
| H 交付管理 | source/CI/native/service/更新回退/用户签收分开 | 本包交用户审批；R1 正式发布另门 |

实际入口包括：新采集→验证/用途→固定数据→训练/恢复→分析→导出/注册→游戏运行/停止→报告；已有数据/模型直接进入且不重复安装/登记；本地独立使用；指定远端任务→产物验证→下载→本机运行；同场景比较与恢复。旧产品 A–H 的责任不能因选用 CLI 实例被取消。

## 8. 失败、恢复与性能批次

必测：旧 catalog/不同 generation、unknown 不重发、无动作观察、丢事件/gap、同正文重访、parent pending/child-ready、模型慢/挂/崩溃、Stop 与提交竞争、训练中断/错 checkpoint/旧 writer、半产物/索引失败、关面板/游戏/服务、登录失效/断网、权限撤销/派生使用、下载缺块/错 hash、更新/配对回退。实际故障注入与 source fixture 标不同等级。

稳定性候选沿用 30 个预登记连续 attempt，冻结版本、Agent、起点、资源与干预规则，全部结果保留；系统缺陷、未修自主循环/挂起不得以 budget Stop 算完成。from-start 到自然胜负/summary 的完整局与各主要族 scene-start 均须有证据。30 次不是高可靠率统计证明。

控制反馈目标健康本机 250ms、无在途提交 release 1s；pilot 先封测法/资源再封最终批。200/500 为功能常规，10000 分别验协议完整访问/压力保护与具体 Agent 能力。测 capture/编码/传输/consume/W/评分/submit/落盘 p50/p95/p99、峰值资源、机会错失/拒绝/gap。阈值不能追着失败改；recording 开/关做匹配非干扰对照。

## 9. 原目标处置与最终材料

默认新 Agent 全范围＋历史模型兼容矩阵保留，旧四模型不全部重训。约 10k 合格交互真实训练尝试、K/Reset、backbone、N/Z/O 有界比较仍属原 Stage1a 义务，本轮 E4/V1 要有实际尝试、比较、资格/分母和成本证据；不借“科学结果”之名一并后移。数据真实不足或缺 required 比较保持未完成，不合成补量。无需每配置各一万条、无需全笛卡尔积；Gate/R 按既有触发条件，科学优势与进一步规模实验保留 R2。多人、任意第三方 Mod、任意状态快照和通用智能调度不成为此前未承诺的新前置。其余 required 缺项不因成本或模型弱而删除。

最终提交：导读、完整分母与证据链接、可复现用例、source/artifact/data/model/producer 身份、预算、测试/失败/未知、独立审查及冷工程师复现。用户未批准前保持待审批；本文件不承诺任何尚未执行的结果。
