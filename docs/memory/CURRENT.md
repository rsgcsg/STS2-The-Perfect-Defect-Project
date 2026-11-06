# RL4.2 长训进行中；RL4.1/4.2 集成等待检查和合并批准

本轮任务是把长训源码收成可合入 develop 的 [PR #382](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/382)。
11-06 派工确认运行 release 为 `rl42-p5-af57b882`；集成修复不热换进长训。
集成负责人为 MERGE382/Codex，Claude 审查并推送，合并仍由用户批准。
准确候选、全量 CI、修复与剩余条件只看共享交付目录 `replies/RL4.1/MERGE382-REPORT.md`。
操作仍走[RL 接口与 SOP](../../python/docs/RL_ACTOR_LEARNER.zh-CN.md)和[长训指南](../../python/docs/RL_LONG_RUN.zh-CN.md)。

## 当前边界与下一步

RL4.2 长训由 Claude 运维；本卡不接管作业、不调整预算、方法或配方。
长训尚未结束，6 h 固定种子评估属于开发结果，不是独立 TEST、稳定胜率或真游戏资格。
尚未解决的推理超时和学习端瓶颈留给 RL5；本卡不启动下一项研究或训练。
下一步由 Claude 核精确 head 的完整 CI 和本卡报告，再交用户决定是否正常 merge。
源码、测试、安装、真实运行、数据资格和模型效果分别看各自证据；合并不升级运行或质量声明。

## 其他在册义务

CARD-1A仍待用户试1–2局与原件/入库；[STATUS](../STATUS.md)路由原安装证据，本卡不安装。
原RL3最终12unknown的9同因覆盖/3根因未证及原分母保留，#373不重开。
U1的Host63/Runtime62/SDK19/r1.9沿正常冻结采用，原件看[U1](../evidence/UNKNOWN_FIX_U1_20261101.md)。
RL4-PREP 原失败与恢复缺口仍归[原任务](../plans/RL_CRITIC_20261101.md)及原收据，
I1/G2/H1 与 RL4.2 各轮结果留在共享交付目录各自报告；后续源码和结果不改写旧失败。

## Remaining Platform non-claims（运行与证据边界）

unknown不重试、不当负局；CI/合并、owner排空、R2可读、数据资格和模型效果各看原收据。
平台其他未完归[STATUS](../STATUS.md)、[U1证据](../evidence/UNKNOWN_FIX_U1_20261101.md)及原Issues，不在本卡自动扩范围。

<!-- Resolve live GitHub refs before action; latest user instructions and live evidence override this file. -->
