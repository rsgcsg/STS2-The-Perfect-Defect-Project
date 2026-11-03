# 当前：RL4准备与首小时实施

用户已派发RL4-PREP-EXEC v4.1，root唯一执行owner。最新集成基线develop9530d9e7，
PR375/376已正常合并。原任务与长期修复理由看[当前RL4段](../plans/RL_CRITIC_20261101.md)。
v1008/r1.9同run继续；RL2/RL3已结束，不恢复原unknown或旧任务。

<!-- Resolve live GitHub refs before action; latest user instructions and live evidence override this file. -->

## 当前任务与唯一下一步

M0、2048局新头预热、起点100和前16颗MC已完成；CP正常R2空Store替换恢复已在
非零更新上实测。短段、中央推理、SGD与μ发布已有真实运行，不能据此称首小时健康。
当前阻塞在背压/Agent调用期限和闭局预留生命周期：抽查8路首unknown均在129步触发
30秒Agent超时，旧unknown比例停机又缩掉Actor；最新窗口已正常Stop保存，原unknown不改。
当前唯一下一步：完成本层长期修复的精确候选门禁，按原预算从完整CP恢复32路真实健康小时；
后16MC与TRAIN后点仍欠。实际source/config、计数、R2收据和资源占用只看本卡唯一原报告。

用户11-03已批准A100，单张GPU全卡累计≤3小时、含评估≤45游戏位；T0与72小时目标
不因修复/换GPU重置，目标仍11-06约03:42 AEST，24小时进度点不变。
首小时外训练预算未批；结束后停新段、收尾、保存、释放资源，交同run继续命令。
不碰Mac安装或真人录制；原100/48及永久heldout不训练，unknown不重投。

## 其他已派工作入口

CARD-1A仍待用户试1–2局与原件/入库；[STATUS](../STATUS.md)路由原安装证据，本卡不安装。
原RL3最终12unknown的9同因覆盖/3根因未证及原分母保留，#373已合并；不再开诊断批。
U1的Host63/Runtime62/SDK19/r1.9沿正常冻结采用，原件看[U1](../evidence/UNKNOWN_FIX_U1_20261101.md)。

## Remaining Platform non-claims（运行与证据边界）

固定100/48和永久heldout不训练；unknown不重试、不当负局或零层。
Managed、真实安装运行、数据准入和模型效果分别看原收据；CI与合并不代替它们。
平台其他未完归[STATUS](../STATUS.md)、[U1证据](../evidence/UNKNOWN_FIX_U1_20261101.md)及原Issues，不在本卡自动扩范围。
