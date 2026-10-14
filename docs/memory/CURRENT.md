# 当前状态（2026-10-14）

模型方案冻结：开（短期约束；关闭后按 T2 处理）

实时事实（分支、PR、CI、安装、运行）以 GitHub 和原始记录为准，本文件可能过时。

## 现在的位置

- 能力：还没有套件结果。最好的单局是 d6 M0 加"必拿牌"程序，Defect A0 单一种子到第 11 层；
  Structured M2（Native137）到第 5 层。见 [SCOREBOARD](../SCOREBOARD.md)。
- 环境：Reference Host（真实游戏）可以跑完整局；Managed Exact Host（同一个 `sts2.dll`，无渲染）
  8 月冻结为快速环境，之后主线没有用它。两者都基于游戏 v0.111.0。
- PR177 已于 2026-10-14 合并进 develop。
- 两条 Structured M2 学习链都没正式训练：Source3 只有 8 个合格样本，旧 Human 有 1465 个候选样本未完成准入。
- 方向：只做一套协议 P1（[规格](../protocols/P1.zh-CN.md)），一条主线；Managed 训练与评测，Reference 验证。计划见 [P1 第一轮](../plans/P1_ROUND1_PLAN_2026-10-14.zh-CN.md)。

## 下一步（按顺序，可并行的已标出）

1. 本地 Agent 独立审计规则、P1 规格、计划、第一轮配置（AUDIT-1），主管按审计修正，用户确认后合并规则 PR。
2. 并行：B 资产对账、C 逐场景信息边界、F 反复重开的决定、D 评测入口与基线；D 之后 E 等同性。
3. G P1 适配器、H P1 人类数据集、J 批量训练与远端 GPU → I 第一轮训练（配置待确认）→ 套件评测。
4. 强化学习（远端 GPU）；K 界面接入；按 B 的处置表清理。

## 预算与边界

- GPU：本轮累计上限 USD 20，已花 USD 0。
- 不做：生产部署、main/release、删除原始数据、未批准的花费。

## 去哪里找

- 规则与角色：[ENGINEERING_RULES](../ENGINEERING_RULES.zh-CN.md)；决策：[DECISIONS](../DECISIONS.md)。
- 旧计划与历史证据：[STATUS](../STATUS.md)、[执行包](../plans/AGENT_RUN_COMPLETION_2026-10-11.md)。
