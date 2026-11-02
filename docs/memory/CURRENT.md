# 当前：RL3已结束；最终12个unknown诊断保全修复

本轮用户卡只处理最终三组各100中的12个unknown；root是唯一执行owner。RL3训练和评估已结束，不恢复RL2/RL3。基线develop10471c76；0新World/0GPU/0训练，不碰Mac游戏、不安装部署，不改原audit/scored/seed/run_id。普通已授权修复自主完成，整体原2小时时间盒不因诊断、审查或CI重置。

<!-- Resolve live GitHub refs before action; latest user instructions and live evidence override this file. -->

## 当前任务与唯一下一步

[本卡原件、代码范围与未完](../evidence/RL3_FINAL12_FIRST_ERRORS_20261102.md)拥有逐槽结论。9槽已有同因修复覆盖，3槽原历史根因未证。第84槽原Runtime59/原expected在正常Store读回原件上40.03秒通过；Friday单CPU复现超时不等于原首错，既有#365同样提示不能倒推。本卡撤回未达标SDK/Runtime优化，仅修正常typed finding的本地及不可变failure artifact保全。相关133项检查及非作者复核通过；唯一下一步是完成最终候选门禁并交可审阅PR，不另开游戏取代缺失证据。日志/诊断保全不称作原运行故障已修复。

RL3原设计与配置仍见[原任务](../plans/RL_CRITIC_20261101.md)、[运行指南](../../python/docs/RL3_RUN.zh-CN.md)。结束前导航原文保存在[10471c76原CURRENT](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/10471c76ec49c35592e834902854281fec9956d9/docs/memory/CURRENT.md)，其中旧续训、接管和最终评估指令均不再执行；原结果、保全及资格只按各自原收据，不补写。

## 其他已派工作入口

CARD-1A / [#340](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/issues/340)仍待用户试1–2局和原件/完整入库；本卡不碰Mac游戏。原source ab816a93 / Workbench cc39dc7f / UI24 / Mod72 / Runtime60的已装与0动作保存范围见[STATUS](../STATUS.md)，不拿它作模型实战资格。

U1的PR359/361/367/370已交；具体Host59/60到最终Host63、Runtime62、SDK19及显式P1r1.9的组合与界限看[U1证据](../evidence/UNKNOWN_FIX_U1_20261101.md)。本卡不自动采用新输入、热换任何运行项或安装。Architect/宝箱/Stop ACK/typed诊断/Reference等未达项仍归原#362–#366；本卡只补正常finding保全，不抹掉旧记录、分母或其他未完。

## Remaining Platform non-claims（运行与证据边界）

- 未来价值loss默认无权重，T2原W1历史不回写；动作W1另由下代配置决定。
- 混合整局不算模型评估；人的标签/片段不因P1可读或入Store自动准训。
- 固定100评估seed永久不训练，原确认20保留；训练监测与独立确认分开。
- unknown不重试、不当负局或零层；结果按原ledger、审计和保存读回判断。
- Managed成绩不替代真游戏实力，严格并行/跨Host等价不从局部检查推出。
- 安装、发布、运行身份看原收据/实际节点，不从develop或CI推导；[STATUS](../STATUS.md)只路由证据。
