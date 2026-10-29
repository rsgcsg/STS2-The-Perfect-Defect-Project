分数板（10-29）：起点模型（原 2016 步）在 Managed、A0 Defect、固定 20 种子上 0 胜 / 20 负，平均最高到达 13.75 层；同批另两个模型 12.10 / 11.95 层。下一档目标 L1 = A0 胜率 5%，后续阶梯见[口径](../evidence/CONVERGENCE_BASELINE_20261027.md#模型强度与评估)。
真游戏原 100 槽已由用户 10-29 正式关掉：停在 35 已开（0 胜 / 33 负 / 2 未知），剩下 65 槽不再开，已有结果保留；评估默认走 Friday 上的 Managed。

# 当前：RL 长跑前收口完成，等学校 GPU 和长跑卡

本轮代码固定在 develop `445b9f98`；本收口文档合并后打标记 `convergence-2026-10-29`（用户确认后）；上一基线 `convergence-2026-10-28` 保留。准确入口见 [STATUS](../STATUS.md#最新可恢复入口2026-10-29)。
<!-- Resolve live GitHub refs before action; latest user instructions and live evidence override this file. -->

## 当前三件事

1. **RL 长跑**：代码都在 develop。学习者在学校 L40S，打局和评估在 Friday CPU，两边只通过 R2 交换；Mac 只做部署和用户录制。按节点怎么起、怎么停、怎么续，看[运行图](../../python/docs/RL_LONG_RUN.zh-CN.md)。用户先核学校 GPU 能不能用，再开长跑卡；长跑第一小时就是真实环境冒烟。
2. **采集**：用户日常录制照常最优先。日常安装还是 T2a 那套；H1 混合接管、M2e、最新 Host 等用户下次录制前另装一次。
3. **规矩**：卡的工作规矩见[工作规则](../WORKING_RULES.zh-CN.md)；新开基础设施卡先列现成工具（规矩 13，用户 10-29 批）。

## 现在的默认

| 什么 | 默认 | 实测 / 出处 |
|---|---|---|
| 训练 GPU | 学校 L40S（一个账号同时一个分配，每次最长约 5 小时，从 R2 检查点续） | 宽微批 8，13 轮 1.40 秒/步，比 T2b 快约 2.9 倍（PR278/285/286）；不再用 Modal，Modal 代码保留但不是默认 |
| 打局和评估 | Friday Slurm，冻结部署 `445b9f98` | fc430 每核每小时 22.4 局，c6220 13.5 局（PR283，[手册](../../python/docs/FRIDAY_ACTOR.md)） |
| Managed | Host `1.1.0-rc.58` / Runtime `0.1.0-rc.59`（PR282；PR282 标题写的 Host56/57 是旧名） | 回放覆盖不再开新轮；RL 中 unknown 超过 5% 再开修卡 |
| RL 接口 | 打局者 / 学习者 / 交换合同（PR288，[接口](../../python/docs/RL_ACTOR_LEARNER.zh-CN.md)） | Mac 4 路每小时 136 局、0 unknown；跨节点和学校续训还没在真实环境跑过 |
| 评估账本 | 固定种子前缀（PR287） | D1 60 槽：60 自然负、0 unknown，2 个完整审计通过 |

## Remaining Platform non-claims

还没证明的：真实跨节点 RL 闭环（Friday 写、学校读）、学校 L40S 上从检查点续训、学校空闲断开规则、任何模型通关。离线 NLL 不等于通关能力；20 颗评估种子不能进 RL 训练。
源码在 develop 不等于已安装：日常 Workbench、Mod、模型列表都没随本轮升级。CI 绿不授予 Human 来源、训练资格或模型成绩。各卡准确收据以原报告和 PR 为准，不从本页复活旧任务；未完义务在[待修表](../plans/CONVERGENCE_BASELINE_20261027.zh-CN.md#待修与重构去向)。
