# 当前：RL4.2 长训进行中，RL5 待批

RL4-PREP 已收口；RL4.1（数据面、中央推理、短段学习）与 RL4.2（热路径离开共享盘）后，
11-06 10:28 起在 Friday 跑 RL4.2 长训，负责人运维。用户 11-06 批补到 24 h 训练。
代码在 [PR #382](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/382)，head 即长训 release 源码，**尚未合入 develop**。

<!-- Resolve live GitHub refs before action; latest user instructions and live evidence override this file. -->

## 分数板

Managed、argmax、50 个固定开发种子（用于挑模型，是开发评估，不是独立测试）；对本轮起点 H1 CP522 逐种子配对。
unknown 排除那一对、单独计数；watcher 的 against_baseline 对的是 CP16，不混用。

| 模型 | 完成/unknown | 胜 | 均层 | 过 Act1 Boss | 过 Act2 Boss | 对 CP522 |
|---|---|---|---|---|---|---|
| H1 CP522（起点） | 49/1 | 0 | 21.7 | 23 | 1 | — |
| 训练 4 h | 50/0 | 0 | 22.9 | 30 | 0 | 49 对 +1.1（18 好/13 平/18 差） |
| 训练 6 h | 49/1 | 1 | 27.5 | 38 | 4 | 48 对 +5.9（26/10/12；同组 bootstrap 约 +2.7～+9.2） |
| 训练 8 h | 49/1 | 0 | 28.5 | 37 | 6 | 48 对 +6.3（30/7/11；同组 bootstrap 约 +2.6～+9.8） |

## 在跑与下一步

- 长训：单 A100 学习 + 约 260 路 CPU 打局，30–45 万新决策/h；每 2 h 一次评估；9.2 h 时按计划换卡续跑。窗口 1 硬截止写在 CP 里不可改，
  到截止约 17.7 h；之后按 #382 中 RL_LONG_RUN 的"新运营窗口"续到 24 h。GPU 同时 ≤2 张。
- 停训/续跑/评估逐条记在共享交付目录 `replies/RL4.1/LONG-RUN-LOG-20261106.md`。
- PR #382：在可合入收尾（全量 CI、closeout、模板），合并等用户批。
- RL5（游戏/推理/学习分开、原生存档任务票、无人值守）：草稿在共享交付目录 `replies/RL5/`，待批；先 OPS 与实测 S0。

## 不做

长训期间不改方法与配方，只做运维修复并记录；不热换未验证代码；不新增 GPU、不开新长训，除非用户批。

## 其他已派工作入口

CARD-1A 仍待用户试局与原件/入库；[STATUS](../STATUS.md) 路由原安装证据。RL3 原 unknown 分母保留，#373 不重开。

## Remaining Platform non-claims（运行与证据边界）

unknown 不重试、不当负局；CI/合并、owner 排空、R2 可读、数据资格和模型效果各看原收据。
推理超时与学习端单进程瓶颈未修（归 RL5）。平台其他未完归 [STATUS](../STATUS.md) 及原 Issues。
