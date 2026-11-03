# 当前：RL4准备与首小时实施

用户已派发RL4-PREP-EXEC v4.1，root唯一执行owner。基线develop3ffd2161；
使用v1008/r1.9新run，改原Actor—交付—学习—发布链，完整范围看
[原任务中的RL4段](../plans/RL_CRITIC_20261101.md)。RL2/RL3均已结束，不恢复旧任务。

<!-- Resolve live GitHub refs before action; latest user instructions and live evidence override this file. -->

## 当前任务与唯一下一步

新头预热已完成：2048个旧TRAIN自然局、整seed10%DEV，A30实际1013秒；正常CP/R2空目录读回及替换进程恢复通过，不能据此宣称当前策略有效。
短段、中央推理、完整封批、KL门、辅助标签与有界CP已接通CPU工程链；跨μ战后HP在提交即回填，非作者复核未见阻塞。
原起点100局已全部offer；最后两槽也已完成，最终99负/1unknown/0未启动；两次快照失败保留，0World保存恢复已完成，unknown不重投。
原始块已沿同段Store发布/读回和previous父链接入；异步CP公告、RNG与半批末θ发布已修并测。
前后32颗新MC永久留出已冻结，与历史100/48及预热2048均无重叠。
唯一下一步：冻结源码候选并完成选定门禁/构建，0World起点CP及前16MC后，再实测profile/首小时。

本卡A30累计≤3小时、含评估≤45游戏位；目标11-06约03:42 AEST前，24小时检查进度。
首小时外训练预算未批；结束后停新段、收尾、保存、释放资源并交同run继续命令。
不碰Mac安装或真人录制；unknown不重投，原100/48及永久heldout不训练。
实际运行、计数与资格只看本卡原报告/收据，不能由本CURRENT推导部署或模型效果。

## 其他已派工作入口

CARD-1A仍待用户试1–2局与原件/入库；[STATUS](../STATUS.md)路由原安装证据，本卡不安装。
原RL3最终12unknown的9同因覆盖/3根因未证及原分母保留，#373已合并；不再开诊断批。
U1的Host63/Runtime62/SDK19/r1.9沿正常冻结采用，原件看[U1](../evidence/UNKNOWN_FIX_U1_20261101.md)。

## Remaining Platform non-claims（运行与证据边界）

固定100/48和永久heldout不训练；unknown不重试、不当负局或零层。
Managed、真实安装运行、数据准入和模型效果分别看原收据；CI与合并不代替它们。
平台其他未完归[STATUS](../STATUS.md)、[U1证据](../evidence/UNKNOWN_FIX_U1_20261101.md)及原Issues，不在本卡自动扩范围。
