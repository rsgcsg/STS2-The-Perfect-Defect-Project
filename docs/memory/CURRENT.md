# 当前：RL4.2 长训窗口 2 在跑，RL5 S1/S3/OPS 在做

RL4.2 长训 11-06 10:28 起在 Friday 跑，负责人运维；用户 11-07 批总训练补到 36 h。窗口 1 在 11-07 06:48 的绝对截止结束，训练 55,364 s（15.4 h）。窗口 2 预算 72000 s，11-07 约 11:27 起跑，中途按同窗口续跑换过几次 release，每次都用 `tools/friday/release_swap.py` 重冻结身份并跑一局真游戏验证。
11-08 01:11 起是 c13e：release `rl42-p5-2c880813`，在前面的 [#386](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/386)–[#388](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/388) 之上，又加了 [#399](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/399)（被排除节点的 unknown 只报警）、[#400](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/400)（评估节点池、放回节点）和 [#403](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/403)（计划文件宽限、stop 通道）。c13e 继承了一个误判失败的评估，导致它不再评估；修复是 [#407](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/407)，合并后换成 c13f。操作走 [RL 接口与 SOP](../../python/docs/RL_ACTOR_LEARNER.zh-CN.md) 与 [长训指南](../../python/docs/RL_LONG_RUN.zh-CN.md)（含"换 release 续训"）。

<!-- Resolve live GitHub refs before action; latest user instructions and live evidence override this file. -->

## 分数板

Managed、argmax、50 个固定开发种子（用于挑模型，是开发评估，不是独立测试）；对本轮起点 H1 CP522 逐种子配对。unknown 排除那一对、单独计数；watcher 的 against_baseline 对的是 CP16，不混用。平均奖励分用整局奖励 v2。
12 h、14 h 两次评估因保存失败或排除过多没有闭合（记为 skipped），不进表；排除局多在第三幕，偏低不偏高。15.4 h 起平均奖励分并排报"保守分"（unknown 局按到达层计分）；17.4 h 那次当时被旧规则作废，表里是用原件重算的。每局墙钟上限 11-07 晚起是 1800 s。

| 模型 | 完成/排除 | 胜 | 均层 | 过 Act1 Boss | 过 Act2 Boss | 平均奖励分 | 对 CP522 |
|---|---|---|---|---|---|---|---|
| H1 CP522（起点） | 49/1 | 0 | 21.7 | 23 | 1 | 0.167 | — |
| 训练 6 h | 49/1 | 1 | 27.5 | 38 | 4 | 0.272 | 48 对 +5.9（26/10/12；bootstrap 约 +2.7～+9.2） |
| 训练 10 h | 48/2 | 0 | 29.8 | 44 | 5 | 0.307 | 47 对 +8.1（32/5/10；bootstrap 约 +4.7～+11.3） |
| 训练 15.4 h | 45/5 | — | — | — | — | 0.374（保守 0.382） | 5 个 unknown 是每局 900 s 墙钟上限，不是环境缺口 |
| 训练 17.4 h | 46/4 | 0 | 33.5 | 38 | 15 | 0.387（保守 0.412） | 对 10 h +0.082 [+0.008, +0.154]（26/5/13）；4 个 unknown 同为墙钟上限 |
| 训练 21.4 h | 49/1 | 1 | 34.6 | 44 | 15 | 0.406（保守 0.406） | 对起点 +13.3 层；对 15.4 h +0.021 [−0.057, +0.097]，约 15 h 后在 50 种子噪声内趋平 |

## 在跑与下一步

- 窗口 2：单张 GPU 学习加 CPU 打局，约 260 个 actor，每 2 h 评估一次。评估跑在单独的评估节点池上，不跑 actor。smp-9-10 被自动排除（kthread −4）。之前静态排除的 smp-9-0/9-1/9-7/9-8/9-12/9-19/9-20/c01 经 OPS 逐台核查后已放回。
- 预计 11-08 白天跑完预算，之后写 36 h 总报告：评估分布加配对比较。停训、续跑、评估逐条记在私有交付目录的长训记录里。
- RL5（游戏、推理、学习分开，原生存档任务票，无人值守）：方案 v10.4 已冻结。S1 同进程传输 ③a-1 是 [#406](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/406) 草稿，实测约 1.00×，收益要靠 ③a-2。S3 学习端拆进程，rows-v2 已合进 [#401](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/401)，但默认不开，还差在线门。OPS 做节点核查和学习端崩溃处理。S2/S4/M 等 S3、OPS 收口后再开。

## 不做

长训期间不改方法与配方，只做运维修复并记录；不热换未验证的代码；不新增 GPU、不开新长训，除非用户批。

## 其他已派工作入口

CARD-1A 仍待用户试局与原件/入库；[STATUS](../STATUS.md) 路由原安装证据。RL3 原 unknown 分母保留，#373 不重开；U1 原件看 [U1](../evidence/UNKNOWN_FIX_U1_20261101.md)；RL4-PREP 失败与恢复缺口归[原任务](../plans/RL_CRITIC_20261101.md)。

## Remaining Platform non-claims（运行与证据边界）

unknown 不重试、不当负局；CI/合并、owner 排空、R2 可读、数据资格和模型效果各看原收据。
推理超时和学习端单进程瓶颈未修（归 RL5）。平台其他未完项归 [STATUS](../STATUS.md) 及原 Issues。
