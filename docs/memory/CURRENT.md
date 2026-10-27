真游戏原固定 100 槽：35 已开，0 胜 / 33 自然负 / 2 结果未知，65 未开；第 35 槽已封存，未重投。七个检查点分别统计，不合成一个模型成绩。
先前固定的 Managed 独立 20 局：19 自然负 / 1 结果未知；与真游戏分开，未证明整局等价。准确版本和全部原件/失败保留在事实收据。
下一档方向目标：L1 为 A0 胜率 5%；后续 A0 20%/80%/100%、A10 5%/50%/95% 见[已定方向与口径边界](../evidence/CONVERGENCE_BASELINE_20261027.md#模型强度与评估)。未达到正式评级，也不由有限样本证明总体 100%。

# 当前：第一批八卡已收敛，T2 训练线进行中

本轮固定版本看[`convergence-2026-10-28`](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/convergence-2026-10-28)，入口与各卡收据见[STATUS](../STATUS.md#最新可恢复入口2026-10-28)；上一基线`convergence-2026-10-27`保留。基线不升级在用程序。
<!-- Resolve live GitHub refs before action; latest user instructions and live evidence override this file. -->

## 当前三件事

1. 采集优先：用户日常录制最优先。日常Workbench已由T2a升级到795c5d24（W1+T1），以用户下一局录制为验收；r1.7（M2a）未装日常，换Mod/Annotator/Tool需另批。实际游戏占用由用户、游戏位登记与现场决定，旧“空闲”不能覆盖用户声明。
2. T2训练线：T2a安装→T2b准备/冻结/训练→T2c评估，按用户当轮派发和明确“开训”执行；本页不启动。W1权重规则与录后入池见PR258，输入v2见[P1输入owner](../../python/docs/P1_T0.md#model-input-v2-and-the-separate-content-sorted-438-projection)。每次训练现挑局/权重，训练过的局不能做同血统考试。
3. 并行修复：E1a管测试/环境反复问题并写[TESTING](../TESTING.md)做法，M2c管其Managed后续；准确进展以各自PR和报告为准。10-28三条新规矩见[工作规则§4](../WORKING_RULES.zh-CN.md#4-验证忠实成本适当)。

## 每轮成本：实测样本与下一步

| 步骤 | 已测多少 / 主要成本 | 下一目标与负责处 |
|---|---|---|
| 录制封存→小结 | Q3 170.611 秒，X2 221.553 秒；含等待、处理、R2，单局样本 / 人工、CPU、存储 | 维持正常无补命令；[X3](../evidence/Q_POLISH_X3_20261027.md)已补短等待/逐步开局门；已开始单步仍可能重叠 |
| 确认冻结→学校提交 / 句柄→首更 | 293.76 / 286.68 秒；一次 8 步短测 / 准备、Agent、GPU、网络 | 已达各 ≤300 秒，180 秒目标未达；`python/stpd` 与原 Scheduler |
| Managed 执行/保存 | [M1R2](../evidence/MANAGED_SAVE_M1R2_20261027.md)同原件保存142.7→5.9秒；[M2b](../evidence/MANAGED_TIERS_M2B_20261027.md)4路正式档8自然负/0未知、约118正常局/小时，轨迹档8局1未知、约123/小时；分档/轨迹格式见[保存SOP](../../python/docs/MANAGED_PRESERVATION.md) | 第二批提速暂不开（T2评估后先做一次6路容量曲线，超4路须用户批）；轨迹不出分，不授RL资格 |

数据小结是滚动的：Q小结10-28 00:15为46局/21,889步（A0 32/A10 14；11胜/32负/3未知），全部是r1.5口径、无r1.7见证；M2a核38源中2源/14 N不能恢复r1.7语义。新用途、权重和考试血统由原Curation每次实验决定，不给录制贴永久标签。

## Managed 状态

[M2a工程收据](../evidence/MANAGED_M2A_20261027.md)与PR266：Host49新40为39负/1未知（2.5%），该未知根因`Node.GetIndex`在Host51另版修，旧槽不改；M051/M052/M053/M056已修；r1.7两字段在Host52/私有Mod67工程前缀48/48一致。[M3](../plans/MANAGED_COVERAGE_20261027.zh-CN.md)录后自动对照已装，个人档整局重演未通过（M055等）。设计原则M-1～M-8见[Host指南](../../components/host-runtime/docs/MANAGED_P1_EQUIVALENCE.md#settled-logic-native-rules-and-replaceable-host-implementation)，差异分诊见[工作规则§5](../WORKING_RULES.zh-CN.md#5-缺陷重构资源)。

## Remaining Platform non-claims

源码、安装、真实使用、Human 来源、数据用途和模型效果分别查各卡收据及[10-27事实收据](../evidence/CONVERGENCE_BASELINE_20261027.md)。develop源码组合（Host52/Runtime53等）不等于日常安装；不随develop变化升级Managed或模型/评估Runtime。旧未知不补造。
当前未完成原 100 槽、Managed 整局等价、个人进度整局重演、输入v2的GPU/游戏效果、下一轮正式训练或RL；工程短验和 CI 都不替它们授资格。[地图](../DOCUMENT_MAP.md)给原操作入口，[待修](../plans/CONVERGENCE_BASELINE_20261027.zh-CN.md#待修与重构去向)保留未完义务。
其它卡的负责人和当前进展以各自原件/实时PR为准，不从本页复活旧任务。
