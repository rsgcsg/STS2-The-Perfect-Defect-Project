真游戏原固定 100 槽：35 已开，0 胜 / 33 自然负 / 2 结果未知，65 未开；第 35 槽已封存，未重投。七个检查点分别统计，不合成一个模型成绩。
先前固定的 Managed 独立 20 局：19 自然负 / 1 结果未知；与真游戏分开，未证明整局等价。准确版本和全部原件/失败保留在事实收据。
下一档方向目标：L1 为 A0 胜率 5%；后续 A0 20%/80%/100%、A10 5%/50%/95% 见[已定方向与口径边界](../evidence/CONVERGENCE_BASELINE_20261027.md#模型强度与评估)。未达到正式评级，也不由有限样本证明总体 100%。

# 当前：T1 输入第 2 版与 T2 依赖，M1/X3 已独立收口

本轮固定版本看[收敛基线](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/convergence-2026-10-27)；文档主包为PR256普通合并`ec03de11`，资产处置见[执行收据](../evidence/CONVERGENCE_DISPOSITION_EXECUTED_20261027.md)。基线不升级在用程序。
<!-- Resolve live GitHub refs before action; latest user instructions and live evidence override this file. -->

## 当前三件事

1. 采集优先：日常录制与Q按现装组合继续；X3已在用户明确批准且游戏退出的窗口完成Workbench升级及隔离短验，未重数；实际游戏占用由用户与现场决定，旧“空闲”不能覆盖用户声明。
2. 收敛收口：A/B/C已批准，文档已集成；6个旧PR已关闭、34条指定分支均先核归档tag再删除。原件、模型、本机目录、安装/回滚和R2保留；审批闹钟已按用户要求删除。
3. 用户已派发T1，研究输入与训练读取归本卡；v2和438独立排序投影的定义与入口见[P1输入owner](../../python/docs/P1_T0.md#model-input-v2-and-the-separate-content-sorted-438-projection)；准确检查/集成状态见[PR263](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/263)。正式T2等待W1权重/每模型选局、数据与明确“开训”。M1调查/早停实跑与[下一版方案](../plans/MANAGED_SPEED_NEXT_20261027.zh-CN.md)已交，准确[事实](../evidence/MANAGED_SPEED_M1_20261027.md)归本轮；后台保存未做，不自动接M2。[X3](../evidence/Q_POLISH_X3_20261027.md)只修Q状态与重活调度，源码PR260已普通合入develop并按用户批准安装；隔离两操作起录/封存通过，Q已恢复。M3对Q文件的依赖已解除；T1/W1/M3/T2沿[原依赖](../plans/CONVERGENCE_BASELINE_20261027.zh-CN.md#收敛后待派发不是执行许可)及各自实时授权处理，正式T2仍等明确“开训”，不从本页自动启动。

## 每轮成本：实测样本与下一步

| 步骤 | 已测多少 / 主要成本 | 下一目标与负责处 |
|---|---|---|
| 录制封存→小结 | Q3 170.611 秒，X2 221.553 秒；含等待、处理、R2，单局样本 / 人工、CPU、存储 | 维持正常无补命令；X3补短等待/逐步开局门；已开始单步仍可能重叠 |
| 确认冻结→学校提交 / 句柄→首更 | 293.76 / 286.68 秒；一次 8 步短测 / 准备、Agent、GPU、网络 | 已达各 ≤300 秒，180 秒目标未达；`python/stpd` 与原 Scheduler，T1 沿用 |
| Managed 执行/保存 | M1原路径1路/4路自然局总中位211.65/279.41秒；27新槽17自然负/10未知，含3诊断槽；完整保全见原报告 | 同snapshot早停已实跑，保存仍串行；本地暂存/并行读回/流水线下一版待选，正式证据不减 |

数据小结是滚动的；本轮未重数或改库。先前 X2 验收快照为 31 局 / 13,029 有效步（6 胜 / 23 负 / 2 未知），不是旧 414 冻结的 25 组 / 10,319 步。新冻结、用途和数据资格仍由原 Curation/确认操作决定。

## M3 个人原件与录后诊断

[M3任务与入口](../plans/MANAGED_COVERAGE_20261027.zh-CN.md)已交：PR262普通合入develop，Host47按原件个人进度启动；用户批准后Workbench固定releasec75ecd42/源码e9452e11已安装，原Mod/Tool不变。Q原策略已恢复，已有真人原件自动重演、严格停止、analysis保全和完整R2读回已验，独立页面从原maintenance ledger派生。原X2 A10为13比12一致；首份自动样本830计划/73已比/72一致，不能称个人档整局重建。M055时钟/锁镰与M056事件可用性仍保留，normal时钟试验未安装。录制优先，未知不重演，后续诊断服务不等于有人后台人工监测。

## Remaining Platform non-claims

源码、安装、真实使用、Human 来源、数据用途和模型效果分别查[事实收据](../evidence/CONVERGENCE_BASELINE_20261027.md)及其原证据。日常Workbench为M3的releasec75ecd42（源码e9452e11），Recorder仍为原Annotator30/Tool5afc7d76；不随随后develop变化升级Managed或模型/评估Runtime。旧未知不补造。
当前未完成原 100 槽、Managed 整局等价、个人进度整局重演、输入v2的GPU/游戏效果、下一轮正式训练或RL；工程短验和 CI 都不替它们授资格。[地图](../DOCUMENT_MAP.md)给原操作入口，[待修](../plans/CONVERGENCE_BASELINE_20261027.zh-CN.md#待修与重构去向)保留未完义务。

T1止于输入基础与T2准备，原包/权重和录制原件保留，检查与集成看PR263；M1下一版等待用户另行派发；不自动续跑旧100、M2、训练或RL。X3已安装、确认可录并释放游戏位；日常Q启用/up_to_date/待处理0，本卡收口不接新任务。其它卡的负责人和当前进展以各自原件/实时PR为准，不从本页复活旧任务。
