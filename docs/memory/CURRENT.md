# RL4.1 I1：恢复后SGD已通过，内存修复待提交

用户11-04续跑2；f213bd9c已在Friday新隔离release用全新seed补验：真实CP16从362更新到374保存，只换learner、同actor/World继续至388正常退出，385更新CP已在R2完整读回，未决episode/lease均0。全部短测在40分钟内结束、GPU0、无活动作业；本轮18World中的15unknown和1save_failed原样保留，另1saved/1censored，未重投旧意图。
内存大头是CP对共享slab逐Tensor deepcopy及整minibatch叠加encoder图；源码已改浅展开编码和按组重算，CPU实形状、回归及非作者复核通过，未提交/未部署真实链。另对齐Friday Evidence37依赖并在bootstrap核实际consumer导入。唯一下一步由Claude提交并重绑候选、接回原卡G1准备，不自动开G1/H1或GPU；预算不重置。接口、四种状态及SOP见[RL SOP](../../python/docs/RL_ACTOR_LEARNER.zh-CN.md)，冻结范围见[F0](../plans/RL4.1/F0-INTERFACES.md)。

## 原轮结果与剩余授权

RL4-PREP按未达标结卡，准确实物、原件replay超时、低吞吐和前后测边界由[RL4任务](../plans/RL_CRITIC_20261101.md)及其原收据负责。已运行source2d77049e/Runtime63/r1.9；#378/#379/#380已合；cache033a48ed只有CPU/CI证据，未热换。362SGD/CP16和旧unknown分母不被I1升级，RL2/RL3不重开。
用户原批单A100、GPU累计5小时，原12个GPU作业累计10911秒、余7089秒；排队不计使用。含评估仍≤45游戏位；首小时外正式长训未批，T0与原72小时目标不重置。I1本轮只用CPU，不自动进入G1/H1，不删除资产、不碰Mac安装/真人录制，固定评估及永久heldout不训练。

<!-- Resolve live GitHub refs before action; latest user instructions and live evidence override this file. -->

## 其他已派工作入口

CARD-1A仍待用户试1–2局与原件/入库；[STATUS](../STATUS.md)路由原安装证据，本卡不安装。
原RL3最终12unknown的9同因覆盖/3根因未证及原分母保留，#373不重开。
U1的Host63/Runtime62/SDK19/r1.9沿正常冻结采用，原件看[U1](../evidence/UNKNOWN_FIX_U1_20261101.md)。

## Remaining Platform non-claims（运行与证据边界）

unknown不重试、不当负局；CI/合并、owner排空、R2可读、数据资格和模型效果各看原收据。
平台其他未完归[STATUS](../STATUS.md)、[U1证据](../evidence/UNKNOWN_FIX_U1_20261101.md)及原Issues，不在本卡自动扩范围。
