# 当前：RL4准备与首小时实施

用户已派发RL4-PREP-EXEC v4.1，root唯一执行owner；最新已集成develop4fca716d，
实际已运行source1f57f47b。原任务与当前未完见[当前RL4段](../plans/RL_CRITIC_20261101.md)。
RL2/RL3已结束，原unknown不重投；参数可按当前用户目标和真实证据有理由调整。

<!-- Resolve live GitHub refs before action; latest user instructions and live evidence override this file. -->

## 当前任务与唯一下一步

M0、2048局新头预热、起点100和前16MC已完成；短段、SGD、μ采用已有真实运行。
最新窗口在内部120秒容量等待失败后正常Stop，累计189SGD，预留/活动局均0；
非零GPU CP经空Store替换恢复与R2完整读回通过，完整健康小时仍未通过。
当前唯一下一步：第一批容量等待/一致性/归档依赖修复独立PR，精确CI绿后正常合并；
再做恢复消费闭环与CPU故障注入，然后Actor编码/双缓冲/推理stream，冻结后验健康小时。
后16MC与固定TRAIN后点仍欠；source/config、实际计数和收据只看本卡唯一报告。

用户11-03批准单A100并追加2小时，GPU总限5小时；已用7052秒、余10948秒，
含评估仍≤45游戏位。T0不因修复重置，目标11-06约03:42 AEST，24小时进度点不变。
首小时外正式长训时长未批；结束后停新段、收尾、保存、释放，交同run继续命令。
不碰Mac安装或真人录制；原100/48与永久heldout不训练，失败历史保留。

## 其他已派工作入口

CARD-1A仍待用户试1–2局与原件/入库；[STATUS](../STATUS.md)路由原安装证据，本卡不安装。
原RL3最终12unknown的9同因覆盖/3根因未证及原分母保留，#373已合并；不再开诊断批。
U1的Host63/Runtime62/SDK19/r1.9沿正常冻结采用，原件看[U1](../evidence/UNKNOWN_FIX_U1_20261101.md)。

## Remaining Platform non-claims（运行与证据边界）

固定100/48和永久heldout不训练；unknown不重试、不当负局或零层。
Managed、真实安装运行、数据准入和模型效果分别看原收据；CI与合并不代替它们。
平台其他未完归[STATUS](../STATUS.md)、[U1证据](../evidence/UNKNOWN_FIX_U1_20261101.md)及原Issues，不在本卡自动扩范围。
