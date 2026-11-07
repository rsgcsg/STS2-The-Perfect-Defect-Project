# 当前：RL4.2 长训窗口 2 在跑，RL5 方案已冻结

RL4.2 长训 11-06 10:28 起在 Friday 跑，负责人运维；用户 11-07 批总训练补到 36 h。窗口 1 在 11-07 06:48 的绝对截止结束，训练 55,364 s（15.4 h）。窗口 2（c13）11-07 约 11:27 起跑：
release `rl42-p5-795622dc` 含 [#386](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/386) 节点自动排除、[#387](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/387) 评估 save_failed 不停整批、[#388](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/388) 截止窗口接续；
预算 72000 s，从 update 6787 的 CP 接续，换 release 时身份已重冻结，并用一局真游戏验过。操作走 [RL 接口与 SOP](../../python/docs/RL_ACTOR_LEARNER.zh-CN.md) 与 [长训指南](../../python/docs/RL_LONG_RUN.zh-CN.md)（含"换 release 续训"）。

<!-- Resolve live GitHub refs before action; latest user instructions and live evidence override this file. -->

## 分数板

Managed、argmax、50 个固定开发种子（用于挑模型，是开发评估，不是独立测试）；对本轮起点 H1 CP522 逐种子配对。
unknown 排除那一对、单独计数；watcher 的 against_baseline 对的是 CP16，不混用。平均奖励分用整局奖励 v2。
12 h、14 h 两次评估因保存失败或排除过多没有闭合（记为 skipped），不进表；排除局多在第三幕，偏低不偏高。

| 模型 | 完成/排除 | 胜 | 均层 | 过 Act1 Boss | 过 Act2 Boss | 平均奖励分 | 对 CP522 |
|---|---|---|---|---|---|---|---|
| H1 CP522（起点） | 49/1 | 0 | 21.7 | 23 | 1 | 0.167 | — |
| 训练 4 h | 50/0 | 0 | 22.9 | 30 | 0 | 0.190 | 49 对 +1.1（18 好/13 平/18 差） |
| 训练 6 h | 49/1 | 1 | 27.5 | 38 | 4 | 0.272 | 48 对 +5.9（26/10/12；bootstrap 约 +2.7～+9.2） |
| 训练 8 h | 49/1 | 0 | 28.5 | 37 | 6 | 0.283 | 48 对 +6.3（30/7/11；bootstrap 约 +2.6～+9.8） |
| 训练 10 h | 48/2 | 0 | 29.8 | 44 | 5 | 0.307 | 47 对 +8.1（32/5/10；bootstrap 约 +4.7～+11.3） |

## 在跑与下一步

- 窗口 2：单张 GPU 学习加 CPU 打局，每 2 h 评估一次；smp-9-20、9-7、9-12 已排除，节点自动排除已开。预计 11-08 白天跑完预算。
- 停训、续跑、评估逐条记在私有交付目录的长训记录里。
- RL5（游戏、推理、学习分开，原生存档任务票，无人值守）：方向已批，方案 v10.4 已冻结，卡在私有交付目录；先做 OPS 和 S0 实测，再开 S1。

## 不做

长训期间不改方法与配方，只做运维修复并记录；不热换未验证的代码；不新增 GPU、不开新长训，除非用户批。

## 其他已派工作入口

CARD-1A 仍待用户试局与原件/入库；[STATUS](../STATUS.md) 路由原安装证据。RL3 原 unknown 分母保留，#373 不重开；U1 原件看 [U1](../evidence/UNKNOWN_FIX_U1_20261101.md)；RL4-PREP 失败与恢复缺口归[原任务](../plans/RL_CRITIC_20261101.md)。

## Remaining Platform non-claims（运行与证据边界）

unknown 不重试、不当负局；CI/合并、owner 排空、R2 可读、数据资格和模型效果各看原收据。
推理超时和学习端单进程瓶颈未修（归 RL5）。平台其他未完项归 [STATUS](../STATUS.md) 及原 Issues。
