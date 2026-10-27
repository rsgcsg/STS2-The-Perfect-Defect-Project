# 项目地图：有什么、在哪、怎么做

本页负责导航和能力概览，当前派工只由 [CURRENT](memory/CURRENT.md) 指向的任务负责。状态按本轮核到的支持范围写；“能用”不表示全部场景合格。准确版本和计数在[本轮事实收据](evidence/CONVERGENCE_BASELINE_20261027.md)，操作前仍须核实际安装和运行项。

## 一套职责，按需展开

在线时，游戏提供世界，Host 管实例，Connector 提供公平信息/动作，P1 表达决策，完整 Agent 选择并由 Runtime 受控执行。录制可独立保存事实，再派生为 P1；Curation 决定用途和切分，研究投影决定模型输入/目标，训练产出模型，再接回 Agent 和评估。

[原链路图和连接要求](plans/p1-prefreeze-2026-10-18/CHAIN_PURPOSES.md#唯一上层图与连接要求新任务入口2026-10-19)拥有这套关系，[组件表](COMPONENTS.md)拥有实现归属。位置说“负责哪件事”，节点说“跑在哪里”；不要求每项功能另起进程，也不要求每次经过所有环节。真实需求表达不了时，可以带证据重拆职责，不能另长一套业务链。

## 按“我要做什么”进入

人用最方便的受支持界面，Agent 用 CLI/API/库；它们映射同一业务能力和状态。表中只给入口，必要参数、成功判断和失败恢复由原指南维护。

| 功能 / 当前支持范围 | 链路位置、负责目录 | 操作入口和说明 | 剩余 / 去向 |
|---|---|---|---|
| 日常本人录制：能用，正常局已实证 | 采集；`components/annotator`，`apps/ingame-ui`，应用 `python/spireagent/workbench` | [游戏内 Platform](../apps/ingame-ui/README.md) → [开始本人录制 / Pause / Close](ANNOTATOR_COLLECTION.md#current-p1-daily-collection) | 跨局沿同一应用；中途接管/任意 Continue 的支持边界看原指南，不用关页面代替 Close |
| 录后自动入库、派生、R2、小结：能用 | 原件→派生→用途；`python/spireagent/workbench` | [Q 的小结与冻结 SOP](../python/docs/RECOUNT-SOP.zh-CN.md)；正常局封存后自动处理，权重确认后才冻结 | Q2/Q3 已修停用可见性；查询超时、已开始重活遇下一局仍待 X3 |
| 种子和原样开局进度：新局能存 | 采集见证；`components/annotator` | [采集指南](ANNOTATOR_COLLECTION.md)、[X2 收据](evidence/CONVERGENCE_BASELINE_20261027.md#录制和自动入库) | 私有附件只在原件；不进 P1/模型，旧未知不补。个人存档的 Managed 重演仍待 M3 |
| R2 保存、按 ID 恢复、本地热集：能用 | 保存与传输；`python/spireagent/storage`、Workbench Store | [存储与容量 SOP](../python/docs/DATA_LIFECYCLE.md#项目存储真身工位与本机删除条件) | 有完整读回和实际 reader 才谈冷却；R2 是已保全内容的长期家，未保全本机原件仍须保护 |
| 选数据、用途、整局切分和权重：能用 | 派生→训练；Curation 与 `python/stpd/p1` | [数据管理](../python/docs/research/DATA_MANAGEMENT.zh-CN.md)、[预览/确认冻结](../python/docs/RECOUNT-SOP.zh-CN.md) | 旧冻结 414 不变；新 A10 权重预览归 W1，DEV/TEST 不加权 |
| 学校 GPU 训练与恢复入口：能用，有 8 步短测 | 训练；`python/stpd`、原 TrainingService/Ops/Scheduler | [准备包→提交→看进度→检查点](../python/docs/FULLRUN_TRAINING.md#下一轮-m-std先准备再一条命令开训) | 先 package，明确“开训”后 start；短测不算新正式训练。输入 v2/T2 准备归 T1 |
| 模型下载、注册、完整 Agent 接入：能用 | 模型→运行；`python/stpd/p1`、Workbench | [模型/Agent 操作](../python/docs/PROJECT_CONSOLE.md#p1-model正常下载注册与使用)、[运行入口](../python/docs/LOCAL_MODEL_TASK_FLOW.md#prepare-and-load) | 注册不等于激活；旧 438 输入仍受排列影响，权重和旧包保留 |
| 真游戏 Reference 评估：能续接，原 100 槽未完成 | 运行→评估；Workbench `evaluation_ledger` / `reference_suite` | [可续跑评估链路](../python/docs/LOCAL_MODEL_TASK_FLOW.md#可续跑的评估链路) | 35 槽已开/2 unknown 均保留；第 35 槽已封存，未重投，65 未开；不自动续跑 |
| Managed 模拟评估和对照：部分可用 | Host 实现与同一评估账本；`components/host-runtime`、Workbench | [Host 对照指南](../components/host-runtime/docs/MANAGED_P1_EQUIVALENCE.md)、同上评估入口 | 与 Reference 是同一 Host 角色的两种实现；只在已验证范围作工程替身，成绩分开；M1/M2/M3 待派发 |
| 开发、修缺陷、测试、集成：现有流程 | 各功能 owner；根 `tools` 负责工程工具 | [工程入门](NEW_ENGINEER_GUIDE.md) → [工作流程](DEVELOPMENT_WORKFLOW.md) / [测试](TESTING.md#local-feedback-and-candidate-checks) | 缺陷归最先出错的层；[待修和重构](plans/CONVERGENCE_BASELINE_20261027.zh-CN.md#待修与重构去向) |
| 安装、升级、核加载、回滚：现有入口 | `apps/game-mod` 与原安装 owner | [开发工具安装](../python/docs/DEVELOPER_KIT_INSTALL.md)、[Mod 生命周期](../apps/game-mod/README.md#lifecycle) | 用户录制优先；先核占用/登记和匹配 Tool；安装不随文档合并发生 |
| Hub、队列、团队账号、云页面：保留冻结 | `python/spireagent/hub`、`python/deploy/hub`、`python/docs` | [现有 Hub 运维](../python/deploy/hub/RUNBOOK.md)、[账号恢复](../python/docs/IDENTITY_PROTOCOL.md) | 当前单人本机/R2/学校链不依赖整个团队产品；有旧 caller，不等于废除或当前已部署 |

## 跑在哪

| 节点角色 | 现在怎么分 | 数据如何连接 |
|---|---|---|
| 采集 | 本机真人游戏、封存、录后后台处理、冻结和打包 | 原件/派生经现 Store 保全到 R2，保留来源和用途 |
| 训练 | 学校 GPU；Modal adapter 保留为已存在的可选实现 | 从 R2 读取具名输入，检查点和结果回 R2；分配、预算、开训另有明确授权 |
| 运行 | 本机真游戏 Reference；Managed 同机可并行，具体并发由任务按资源限制 | 正式成绩须有完整证据读回；不能挤占用户录制 |
| 存储 | R2 长期存储；本机为有界缓存和未保全工作集 | 冷取回仍走原 reader，不用散落 tar 代替 Store 身份 |
| 控制 / 开发 | 本机代码、配置、调度、检查 | 一台机器可兼多个角色；云 CPU 按需，当前不建持久服务器 |

## 新事放哪、旧事怎么找

- 新任务按[工作规则](WORKING_RULES.zh-CN.md)回答目标、位置、历史复用、变更种类和停止点；规格/ADR 管含义，功能指南管 SOP，任务管本轮，CURRENT 只指路。模型输入和游戏协议是不同合同。
- [当前待办和卡顺序](plans/CONVERGENCE_BASELINE_20261027.zh-CN.md#收敛后待派发不是执行许可)：T1/W1 是模仿线，M1→M2 是 Managed/RL 基础，M3 是覆盖，X3 可选。没有正式训练或 RL 执行许可。
- [已解决问题](RESOLVED_ISSUES.zh-CN.md)给原解法和边界；[DECISIONS](DECISIONS.md)给已定事项与替代；[架构](ARCHITECTURE.md)和 [ADR](adr/README.md)给长期理由。
- [旧资产账本和本轮增量去向](plans/CONVERGENCE_DISPOSITION_20261027.md)区分建议、已执行和独有成果。旧PR24的Workshop L1发行边界和PR25的L2 staging已按用户批准关闭，原分支已归档后删除；独有源码保在精确tag，未冒称已接入当前，见[执行收据](evidence/CONVERGENCE_DISPOSITION_EXECUTED_20261027.md)。`p1-prefreeze-2026-10-18/` 留原处作参考，不是新任务队列。

## 按需参考

[工程治理](ENGINEERING_GOVERNANCE.md)管 G0–G6 和边界；[文档归属](PROJECT_SYSTEM.md)管文件放置；[AI 协作](AI_COLLABORATION.md)管委派/续接；[技能目录](../.agents/skills/README.md)只在适用时用。长手册仍保其约束，不是每轮全读。旧 [功能细表](plans/p1-prefreeze-2026-10-18/FEATURE_STATUS.md)、[格式/成本](plans/p1-prefreeze-2026-10-18/RECORDING_FORMATS_AND_COST.md)、[设计历史](design/BASELINE_DESIGN_HISTORY.zh-CN.md)保留原版本范围；[STATUS](STATUS.md)路由各时点证据，不能把旧 PASS 转给今日组合。

源码按[组件](COMPONENTS.md)、[Python 地图](../python/docs/DOCUMENT_MAP.md)和对应局部 AGENTS 进入；公平信息/有限动作归 Connector，生命周期归 Host/Runtime，原件归 Annotator/Evidence，输入/模型/训练归研究。图中出现一个角色，不表示必须新建服务或为它开一个 Agent。
