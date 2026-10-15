# 系统地图：分层、统一逻辑与工具

状态：2026-10-13 起的导航文档。读完这一页应能回答：项目由哪些层组成、每层统一什么、出了差异该改哪里。
本页不记录动态状态：当前选定的组合和下一步看 [CURRENT](memory/CURRENT.md) 与[活动计划](plans/P1_ROUND1_PLAN_2026-10-14.zh-CN.md)，
已实现和已验证的东西看 [STATUS](STATUS.md)。细节在各层的拥有文档里。
上层职责的完整论证保留在 [ARCHITECTURE](ARCHITECTURE.md) 和 [ADR-0016](adr/0016-agent-composition-and-execution-request.md)。

## 1. 一句话

所有新旧方案都是同一条链上的不同组合：

```text
世界（Host） → 环境协议（profile） → Agent（程序 + 0..n 个模型） → 执行与记录
     ↑                                                              ↓
   评测 ← 模型 ← 训练 ← 数据集（投影） ← 原始记录（轨迹 / 标注）
```

一个新想法要么是某一层换了组件，要么是某一层的协议升了版本；只有当合法需求在这条链上表达不了，
或者同一种补丁反复跨层出现，才修改链本身（写 ADR）。不另起一条独立链路。

## 2. 每层统一什么、允许什么不同

| 层 | 必须统一（所有组合都遵守） | 允许不同（就是"组合"） | 当前组件 |
| --- | --- | --- | --- |
| 世界 / Host | 规则、RNG、合法性、Commit 的权威是游戏代码；每局记录游戏版本、Host 实现身份（含补丁）、种子 | Reference：真实 Godot 游戏 + Mod；Managed Exact：同一个 `sts2.dll` 在 TestMode 下运行，带投影和运行补丁，去掉渲染，跑得快 | `components/connector`、`components/host-runtime`（含 `experiments/managed-exact`） |
| 环境协议 | 快照 + 完整有限的合法动作目录 + 执行前复验 + delivered/not_delivered/unknown + unknown 不重试 + 声明信息范围 | 信息给多少、动作粒度多细；当前只做 P1 | P1 草案（目标修订 r1，适配器待建）；暂停：`native-logical-v1` |
| Agent | 只消费声明的协议；每次从目录里选一个动作（或等待/结束）；不读隐藏状态、不自建合法性 | 程序、模型、记忆方式、选择规则 | 随机合法、d6 M0、Public M2、Structured M2、机械 Teacher |
| 执行 | 有限预算、Stop、unknown 隔离、每局留原始记录 | 单独的 Policy Runtime 进程（Reference）或进程内批量驱动（Managed 评测/训练） | `components/policy-runtime`、`python/spireagent` |
| 原始记录 | 不可变；声明来源五个维度（谁操作、证明了什么、可得信息、目标含义、用途权限） | 录制格式 | H/Snapshot1、Source3、AgentRun 样本 |
| 数据集 / 训练 | 派生数据可从原件重建，固定 manifest 与使用/曝光记录由 Store/Curation 保留；每个 run 有 config + 代码版本 + 数据哈希 | 投影、Input、Target、模型、算法 | Store/Curation、LocalTrainingService、`python/stpd` |
| 评测 | 固定种子套件 + 同一个评测工具 + 完整分母 + 结果进 [SCOREBOARD](SCOREBOARD.md) | 被评测的 Agent | 现有 `stpd.evaluation`（B0–B7）+ 待建的套件执行器 |

"协议"在本项目只指**环境协议**：Agent 能看到什么、能做什么、失败怎么表达。传输（HTTP、NDJSON、进程内调用）、
文件格式、模型 Input 都不叫环境协议；改它们不需要升协议版本，只要不改变信息、动作机会和失败含义。
但它们各自仍有版本化合同（Read、Agent 端口、Model Input、录制格式），不能因此省略。

## 3. 环境协议：P1

一套协议是从 Host 到 Agent 的完整约定：Agent 看什么、能做什么、得到什么反馈（Agent 面），
以及适配器向 Host 取什么、怎么交互（Host 面）。Agent 只认识 Agent 面；Host 面每种 Host 各有一份实现。
两个 Host 在某协议下等价，指它们给出完全相同的 Agent 面观察、动作和反馈。

**当前只做一套协议：P1**，目标是先做出最强能力。以后可能增加其他协议（包括原生交互型、甚至图像），
它们是并列的组合，不和 P1 冲突。P1 的完整规格见 [protocols/P1](protocols/P1.zh-CN.md)，要点：

- 信息：每个决策点给出玩家此刻在原生界面上能看到、且查看不产生效果的全部信息（牌组、牌堆、遗物、药水、当前页面全部内容、敌人意图等）。
  已由 RNG 生成但尚未揭示的内容不给。
- 动作：保留游戏真实的选择阶段，只省略滚动、坐标这类设备步骤。
- 时机：只在稳定决策点调用 Agent，等待和结算由适配器处理。
- 不加程序推导的提示；P1 不保证马尔可夫，Agent 默认要有记忆。

P1 可以大幅修改，名字不变，按修订号演进（r1、r2…）。Reference 上用 `snapshot-1` + Reads 取事实，是 P1-H 的实现方式，
不是一个独立的 P1 版本；旧 Human 录制是可选复用资产，按 r1 逐项判定能用的部分。
名称提醒：历史任务表的"P1（现状审计）"和组合启动"P1-A/B"不是这个协议。
暂停保留的旧协议：`native-logical-v1`（原生交互型）、text-menu-v1/v2、S0。

## 4. 数据来源在框架里怎么放

训练来源按下面两类区分；它们不穷尽原件（见本节末）：

| 类型 | 含义 | 例子 | 关键字段 |
| --- | --- | --- | --- |
| 轨迹 | 某个行动者在环境里真的操作了 | 真人、学习型 Agent、机械 Teacher、LLM Agent | 行动者类型与版本、协议、Host、种子、每步观察/目录/动作/回执 |
| 标注 | 某个标注者给已有状态打标签，自己不操作 | 战斗 solver/搜索、LLM 评审、人类专家 | 标注者、信息条件（公平/特权）、方法与预算、标签类型（最佳动作/分布/价值） |

外部组件（solver、LLM、外部数据集如 AgenticSTS）只产生这两类数据，不作为被评测的 Agent。
两类都通过投影变成训练样本。**特权信息只能进标签，不能进学生输入。** 例如在 Managed Host 上用存档/回读做搜索的
战斗 solver 看得到隐藏 RNG，所以它是"特权标注"；学生模型照样只看 P1 观察。
格式不全的外部数据（例如没有完整合法目录）可以作为声明了假设的弱标签使用，但必须单独标注用途，不能冒充完整样本。

原件不止这两类：环境侧原件（冻结的 frame/Read、失败诊断、start/resume/end journal、close receipt）、Agent 交互原件
（原始输入字节、提供/消费声明、ACK、directive/result 关联、被中断的 payload），以及可选的内部诊断和管理记录（协议、run 事件、
结果、分析、用途与费用）。它们由各自 owner 保存，观察可以没有动作；训练投影按用途从中选择，不为此另建格式或服务。
见 [FULL_RUN_DATA_CHAIN](FULL_RUN_DATA_CHAIN.md) 与 [AGENT_SESSION_PROTOCOL](../components/policy-runtime/docs/AGENT_SESSION_PROTOCOL.md)。

## 5. 改哪一层，以及两个 Host 怎样比较

| 现象 | 改哪里 |
| --- | --- |
| 只是字段编码、分块、传输不同 | 适配器，不升协议；证明信息、顺序、消费和失败含义不变 |
| Agent 能看到的信息、动作机会或失败含义变了 | 环境协议出新修订，旧修订保留；数据、Agent、评测随之调整 |
| 换模型或记忆计算 | Model / Input / Agent recipe，不动游戏合法性和原件 |
| 训练从 CPU 换到 GPU | 训练后端与执行配方；仍是同一个 TrainingService，验证数值、恢复和产物 |
| 合法需求表达不了，或者同一种补丁反复跨层出现 | 修改上层抽象，写 ADR，带具体反例和迁移计划 |

同一个 `sts2.dll` **不保证**两个 Host 行为相同：执行上下文、补丁、投影和调度都可能造成差异，历史成对运行出现过效果时点分叉。
所以两个 Host 的关系只在声明的范围内检查，分三步（对应 [ARCHITECTURE](ARCHITECTURE.md#host-capabilities-and-requirement-matching) 的一致性分级）：

| 检查 | 含义 | 怎么做 |
| --- | --- | --- |
| 协议一致 | 两边都符合同一 P1 修订：字段、可得性、目录完整、回执和失败含义 | 同一套一致性检查分别对两个 Host 跑 |
| 成对一致 | 同一 seed manifest、同一动作序列，观察和目录相同（忽略声明过的 ID） | 成对运行，报告第一个分叉点和原因（现有 semantic-differential / cross-host 工具） |
| 结果差异 | 某个 Agent 在两边的结果差多少 | 同一 manifest 两边各跑；预先声明容差和方法；样本不足就写不足，不把"落在置信区间内"当作等价 |

Managed 的训练数据和评测结论只在已检查一致的范围内代表真实游戏；范围外的差异要列出来。

## 6. 资产分类

动态数量和逐项处置不写在这里，见 [STATUS](STATUS.md) 和活动计划。分类原则：

| 类别 | 含义 | 例子 |
| --- | --- | --- |
| 当前组合在用 | 活动计划里选定的组件 | Reference Host + Connector、Managed Exact、Store/Curation/TrainingService、`stpd.evaluation` |
| 可选复用资产 | 有价值，按当前合同判定能用的部分 | 旧 Human 录制、d6 M0 及其 Modal 训练流程、Structured M2 数值实现、冻结的 Read 版结构化输入、远端训练候选 A/B/C、legacy GPU seam |
| 暂停保留 | 不在当前主线，原件和代码保留 | `native-logical-v1`、AgentSession/policy3 原生链、Source3 录制、两条学习链的旧配置 |
| 历史只读 | 已被替代 | text-menu-v1/v2、S0、Public M2 17eb、LightAction、旧 Platform/STPD 仓库 |

处置历史 PR、分支、worktree 和候选提交：先核实唯一增量、消费者和数据依赖，再经用户批准；不按年龄或数量自动删除。

## 7. 工具（固定）

| 用途 | 工具 |
| --- | --- |
| 游戏侧 | C# / .NET 9，Harmony |
| Host、Connector SDK、Runtime | Node 20+ / TypeScript |
| 应用、研究、训练 | Python 3 + uv，PyTorch；GPU 用现有 Modal 适配 |
| 代码与 CI | 本仓库 + GitHub Actions；`npm run check` 系列 |
| 原始数据与派生数据 | 现有 Store / Curation；训练与评测输出用 run 目录（见 [ENGINEERING_RULES](ENGINEERING_RULES.zh-CN.md#3-研究的运行数据和评测怎么管)） |

不为新需求引入新的服务、数据库或框架，除非现有工具确实做不到并有记录。
