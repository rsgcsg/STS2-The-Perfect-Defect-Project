# 能力记分板

只记录能力评测结果。当前选择和下一步看 [CURRENT](memory/CURRENT.md)，可用组合和证据看 [STATUS](STATUS.md)。
只有固定种子套件的结果能写进主表，数字必须来自评测工具输出并附 eval id，并带完整分母。
套件和阶梯定义见 [ENGINEERING_RULES 第 1 节](ENGINEERING_RULES.zh-CN.md#1-目标评测与能力记分板)。

当前阶：**L0 未达成**（套件执行器在 PR179，未合并；它的第一个结果 Managed 随机合法 smoke-20 为 8/20 自然终局，见[活动计划](plans/P1_ROUND1_PLAN_2026-10-14.zh-CN.md)第 3 节，合并后再进主表；真实游戏还没有套件结果）。

## 主表（Defect A0）

| 日期 | eval id | 套件 / Host | Agent / 输入 profile | 计划 / 自然终局 / 删失 / unknown | 胜率（95% CI） | 平均 / 中位层 | 到达第二幕 | 决策/秒 | 游戏版本 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| — | — | — | （尚无套件结果） | — | — | — | — | — | — |

## 历史单局参考（不是套件结果；Agent、Host 和输入身份各不相同，不能比较优劣）

| 日期 | 记录 | Agent / 模型 | 结果 | 备注 |
| --- | --- | --- | --- | --- |
| 2026-10-11 | Auto6 | d6 M0（808 万参数，人类 BC）+ 必拿牌程序 | 第 11 层死亡，score 161 | 固定单一种子；272 次提交中 63 次 stale |
| 2026-10-11 | Auto7 | 同上，headless + Instant | 第 11 层死亡，score 161 | 154 次程序调用、153 次唯一提交，0 次 stale，用时约 52 秒 |
| 2026-10 | Auto4 | Native137 Structured M2（18 万参数，137 条 Teacher 样本） | 第 5 层死亡，score 50 | 285 次动作中 104 次 not_started；结束清理状态 unknown |
