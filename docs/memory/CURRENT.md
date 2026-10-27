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

W1源码与T2草稿由PR258交付，集成状态看PR；录后入池、每次实验选局/权重并按血统隔离考试，只合代码不冻结。 W1下一步：用户审T2分配/权重草稿，新规则确认后另行冻结，正式训练仍等明确“开训”；本卡收口。

## 每轮成本：实测样本与下一步

| 步骤 | 已测多少 / 主要成本 | 下一目标与负责处 |
|---|---|---|
| 录制封存→小结 | Q3 170.611 秒，X2 221.553 秒；含等待、处理、R2，单局样本 / 人工、CPU、存储 | 维持正常无补命令；X3补短等待/逐步开局门；已开始单步仍可能重叠 |
| 确认冻结→学校提交 / 句柄→首更 | 293.76 / 286.68 秒；一次 8 步短测 / 准备、Agent、GPU、网络 | 已达各 ≤300 秒，180 秒目标未达；`python/stpd` 与原 Scheduler，T1 沿用 |
| Managed 执行/保存 | M1原路径1路/4路自然局总中位211.65/279.41秒；27新槽17自然负/10未知，含3诊断槽；完整保全见原报告 | 同snapshot早停沿用；[M1R2](../evidence/MANAGED_SAVE_M1R2_20261027.md)接默认本地暂存和每局新R2并行读回，保存仍同步、正式证据不减；流水线归M2b |

数据小结是滚动的。W1本轮正常reader快照为38局/16,656有效步（6胜/29负/3未知），另5份未有获批投影不计；旧414仍为25组/10,319步。新用途、权重和考试血统由原Curation每次实验决定，不给录制贴永久标签。

## M3 个人原件、录后诊断与M2a修复

[M3任务与入口](../plans/MANAGED_COVERAGE_20261027.zh-CN.md)已交：PR262普通合入develop，Host47按原件个人进度启动；用户批准后Workbench固定releasec75ecd42/源码e9452e11已安装，原Mod/Tool不变。Q原策略已恢复，已有真人原件自动重演、严格停止、analysis保全和完整R2读回已验，独立页面从原maintenance ledger派生。原X2 A10为13比12一致；首份自动样本830计划/73已比/72一致，不能称个人档整局重建。M055时钟/锁镰与M056事件可用性仍保留，normal时钟试验未安装。录制优先，未知不重演，后续诊断服务不等于有人后台人工监测。

[M2a工程收据](../evidence/MANAGED_M2A_20261027.md)与[PR266](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/266)交付本轮执行修复和用户已批首两字段r1.7：固定Host49新40为39负/1未知（2.5%）、40份完整R2读回；新未知的原生GetIndex依赖另版Host51修，旧槽不改。M056两种按钮事实另版52修后，原830点源新验证前80点80/80，旧73/72不改。最终Host52与私有Mod67的新同启动r1.7工程前缀48/48观察/选择一致，旧Human r1.5仍13比12；38源有2源/14 N不能完整恢复新语义。日常Mod/Tool/Workbench、默认438不切，19种其它计数不改义，未授整局/Human/训练资格；集成与准确门禁看PR，安装另批。

## Remaining Platform non-claims

源码、安装、真实使用、Human 来源、数据用途和模型效果分别查[事实收据](../evidence/CONVERGENCE_BASELINE_20261027.md)及其原证据。日常Workbench为M3的releasec75ecd42（源码e9452e11），Recorder仍为原Annotator30/Tool5afc7d76；不随随后develop变化升级Managed或模型/评估Runtime。旧未知不补造。
当前未完成原 100 槽、Managed 整局等价、个人进度整局重演、输入v2的GPU/游戏效果、下一轮正式训练或RL；工程短验和 CI 都不替它们授资格。[地图](../DOCUMENT_MAP.md)给原操作入口，[待修](../plans/CONVERGENCE_BASELINE_20261027.zh-CN.md#待修与重构去向)保留未完义务。

T1止于输入基础与T2准备，原包/权重和录制原件保留，检查与集成看PR263；[M1R2](../evidence/MANAGED_SAVE_M1R2_20261027.md)已完成保存/读回实测，流水线仍归后续M2b；不自动续跑旧100、M2、训练或RL。X3已安装、确认可录并释放游戏位；日常Q启用/up_to_date/待处理0，本卡收口不接新任务。其它卡的负责人和当前进展以各自原件/实时PR为准，不从本页复活旧任务。
