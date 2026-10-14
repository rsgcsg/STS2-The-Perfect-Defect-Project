# 系统地图：统一逻辑、当前基线与工具

状态：2026-10-13 起的入口文档。读完这一页应能回答：项目由哪些层组成、每层统一什么、
现在用哪些组件、哪些暂停、出了差异该改哪里。细节在各层的拥有文档里，本页只做导航和定调。
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
| 世界 / Host | 规则、RNG、合法性、Commit 都来自原版 `sts2.dll`；每局记录游戏版本、Host 类型、种子 | Reference：真实 Godot 游戏 + Mod；Managed Exact：同一个 `sts2.dll`，去掉渲染，跑得快 | `components/connector`、`components/host-runtime`（含 `experiments/managed-exact`） |
| 环境协议 | 快照 + 完整有限的合法动作目录 + 执行前复验 + delivered/not_delivered/unknown + unknown 不重试 + 声明信息范围 | 信息给多少、动作粒度多细；当前只做 P1 | P1 适配器（v1：`snapshot-1` + Reads）；暂停：`native-logical-v1` |
| Agent | 只消费声明的协议；每次从目录里选一个动作（或等待/结束）；不读隐藏状态、不自建合法性 | 程序、模型、记忆方式、选择规则 | 随机合法、d6 M0、Public M2、Structured M2、机械 Teacher |
| 执行 | 有限预算、Stop、unknown 隔离、每局留原始记录 | 单独的 Policy Runtime 进程（Reference）或进程内批量驱动（Managed 评测/训练） | `components/policy-runtime`、`python/spireagent` |
| 原始记录 | 不可变；声明来源五个维度（谁操作、证明了什么、可得信息、目标含义、用途权限） | 录制格式 | H/Snapshot1、Source3、AgentRun 样本 |
| 数据集 / 训练 | 派生数据可从原件重建；每个 run 有 config + 代码版本 + 数据哈希 | 投影、Input、Target、模型、算法 | Store/Curation、LocalTrainingService、`python/stpd` |
| 评测 | 固定种子套件 + 同一个评测工具 + 结果进 [SCOREBOARD](SCOREBOARD.md) | 被评测的 Agent | 评测工具待建 |

"协议"在本项目只指**环境协议**：Agent 能看到什么、能做什么、失败怎么表达。传输（HTTP、NDJSON、进程内调用）、
文件格式、模型 Input 都不是协议；改它们不需要升协议版本，只要不改变信息、动作机会和失败含义。

## 3. 环境协议：P1

一套协议是从 Host 到 Agent 的完整约定：Agent 看什么、能做什么、得到什么反馈（Agent 面），
以及适配器向 Host 取什么、怎么交互（Host 面）。Agent 只认识 Agent 面；Host 面每种 Host 各有一份实现。
两个 Host 在某协议下等价，指它们给出完全相同的 Agent 面观察、动作和反馈。

**当前只做一套协议：P1**，目标是先做出最强能力。以后可能增加其他协议（包括原生交互型、甚至图像），
它们是并列的组合，不和 P1 冲突。P1 的完整规格见 [protocols/P1](protocols/P1.zh-CN.md)，要点：

- 信息：每个决策点给出玩家此刻通过纯查看能知道的全部信息（牌组、牌堆、遗物、药水、地图、当前页面全部内容、敌人意图等）。
  打开未打开的奖励组等"揭示"不算纯查看。
- 动作：保留游戏真实的选择阶段，只省略滚动、坐标这类设备步骤。
- 时机：只在稳定决策点调用 Agent，等待和结算由适配器处理。
- 不加程序推导的提示；P1 不保证马尔可夫，Agent 默认要有记忆。

P1 可以大幅修改，名字不变，按版本演进（v1 = `snapshot-1` + 全部 Reads 组装；v2 = 统一观察文档）。
暂停保留的旧协议：`native-logical-v1`（原生交互型）、text-menu-v1/v2、S0。

## 4. 数据来源在框架里怎么放

原始记录只有两类：

| 类型 | 含义 | 例子 | 关键字段 |
| --- | --- | --- | --- |
| 轨迹 | 某个行动者在环境里真的操作了 | 真人、学习型 Agent、机械 Teacher、LLM Agent | 行动者类型与版本、协议、Host、种子、每步观察/目录/动作/回执 |
| 标注 | 某个标注者给已有状态打标签，自己不操作 | 战斗 solver/搜索、LLM 评审、人类专家 | 标注者、信息条件（公平/特权）、方法与预算、标签类型（最佳动作/分布/价值） |

外部组件（solver、LLM、外部数据集如 AgenticSTS）只产生这两类数据，不作为被评测的 Agent。
两类都通过投影变成训练样本。**特权信息只能进标签，不能进学生输入。** 例如在 Managed Host 上用存档/回读做搜索的
战斗 solver 看得到隐藏 RNG，所以它是"特权标注"；学生模型照样只看 P1 观察。
格式不全的外部数据（例如没有完整合法目录）可以作为声明了假设的弱标签使用，但必须单独标注用途，不能冒充完整样本。

## 5. 改哪一层，以及两个 Host 怎样算等同

| 现象 | 改哪里 |
| --- | --- |
| 只是字段编码、分块、传输不同 | 适配器，不升协议 |
| Agent 能看到的信息、动作机会或失败含义变了 | 环境协议出新版本，旧版本保留 |
| 换一个实现但承诺不变 | 换组件，跑一致性检查 |
| 合法需求表达不了，或者同一种补丁反复跨层出现 | 修改上层抽象，写 ADR，带迁移计划 |

两个 Host 在同一协议下分三级等同（对应 [ARCHITECTURE](ARCHITECTURE.md#host-capabilities-and-requirement-matching) 的一致性分级）：

| 级别 | 含义 | 怎么验证 |
| --- | --- | --- |
| 协议等同 | 同一份协议：字段、信息范围、目录完整性、回执和失败含义都符合 | 同一套一致性检查分别对两个 Host 跑 |
| 功能等同 | 同一起点、同一动作序列，得到相同观察和目录（忽略声明过的 ID 等字段） | 固定种子和场景库上的成对差分（现有 semantic-differential / cross-host 工具） |
| 结果等同 | 对某个 Agent，两边的结果分布在置信区间内一致 | `ref-20` 与 Managed 套件结果对比 |

Managed Exact 用同一个 `sts2.dll`，规则天然相同；差异只可能来自它的呈现适配和投影。
只有达到功能等同的场景，Managed 的训练数据才可直接当真实游戏数据用；评测结论至少要有结果等同。

## 6. 资产状态（2026-10-14）

| 状态 | 资产 |
| --- | --- |
| 主线在用 | Reference Host + Connector；Managed Exact Host（2026-08 冻结为 STPD v0 环境，单环境约 200 决策/秒，M4 上 24 环境约 2,400 决策/秒，108 万决策 0 unknown）；P1 的 `snapshot-1` + Reads；旧 Human 录制（H Snapshot1 + Reads，就是 P1 格式）；d6 M0；Structured M2 数值实现与冻结的 Read 版结构化输入；Store/Curation/TrainingService；`stpd.evaluation` B0–B7；USD 20 GPU 额度 |
| 暂停保留 | 原生交互协议 `native-logical-v1`、AgentSession/policy3、Source3 原生录制、两条学习链的旧配置 |
| 历史保留（只读） | text-menu-v1/v2、S0、Public M2 17eb、LightAction、旧 Platform/STPD 仓库 |
| 待清理 | 26 个历史草稿 PR、约 120 个本机 worktree、38 个只在本机的候选提交：逐个判定 合并/吸收/关闭，不批量删除 |

## 7. 工具（固定）

| 用途 | 工具 |
| --- | --- |
| 游戏侧 | C# / .NET 9，Harmony |
| Host、Connector SDK、Runtime | Node 20+ / TypeScript |
| 应用、研究、训练 | Python 3 + uv，PyTorch；GPU 用现有 Modal 适配 |
| 代码与 CI | 本仓库 + GitHub Actions；`npm run check` 系列 |
| 原始数据与派生数据 | 现有 Store / Curation；训练与评测输出用 run 目录（见 [ENGINEERING_RULES](ENGINEERING_RULES.zh-CN.md#3-研究的运行数据和评测怎么管)） |

不为新需求引入新的服务、数据库或框架，除非现有工具确实做不到并有记录。
