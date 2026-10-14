# 当前状态（2026-10-14）

模型方案冻结：开（对象：首轮训练配置和任何新模型方案；工程实现与路径冒烟不受限）

实时事实（分支、PR、CI、安装、运行）以 GitHub 和原始记录为准，本文件可能过时。

## 当前选择

- 一套环境协议 P1，目标修订 r1（[规格草案](../protocols/P1.zh-CN.md)）。Managed Exact 做实验和批量评测，Reference 真实游戏做验证。
- 当前组合、首个瓶颈和交付卡都在[活动计划](../plans/P1_ROUND1_PLAN_2026-10-14.zh-CN.md)。旧 BASELINE_V1_SPEC 和 G2/V1 执行矩阵不再派发任务。
- 第一轮训练配置草稿已撤回，等 P1 r1、数据准入报告和批量/GPU 路径后重写。

## 现在的位置

- 能力：当前组合还没有套件结果。历史单局（不同身份，不可比较）见 [SCOREBOARD](../SCOREBOARD.md)。
- 首个瓶颈：没有能报告完整分母的整局套件执行器；没有可执行的 P1 修订和两端映射。

## 正在做

1. D-min：套件执行器 + 随机合法基线（Managed `smoke-20`）。
2. J-min：批量训练与 GPU 路径装配（复用 legacy seam，≤ USD 1 远端验证）。
3. C：P1 r1 逐场景事实（只读）。
4. PR178（本规则）独立审查后合并，然后 R-178b 改旧大文档的入口段。

## 预算与边界

- GPU：本轮累计上限 USD 20，已花 USD 0。
- 不做：生产部署、main/release、删除原始数据或历史资产、未批准的花费、未确认的训练配置。

## 去哪里找

- 规则与角色：[ENGINEERING_RULES](../ENGINEERING_RULES.zh-CN.md)；决策：[DECISIONS](../DECISIONS.md)；分层：[SYSTEM_MAP](../SYSTEM_MAP.zh-CN.md)。
- 已实现与证据：[STATUS](../STATUS.md)；旧计划与历史证据：[执行包](../plans/AGENT_RUN_COMPLETION_2026-10-11.md)。
