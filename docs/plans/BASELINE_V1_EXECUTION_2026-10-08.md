# G1-v1到G2-S0当前执行包

日期：2026-10-08。用户授权负责人自主收敛与推进，不限于游戏启停、安装、GitHub和部署示例；model/training付费累计<=20美元。本文固定本轮具体顺序与工作台账，真实状态由回执更新。唯一候选合同：[BASELINE_V1_SPEC](../BASELINE_V1_SPEC.zh-CN.md)。

## 第一正确事实与范围

当前设计文档过多且关键选择冲突；当前Observe/Read重复capture、没有sealed-view读取；结构M2未实现；旧数据/模型不自动满足I/F-off新输入。先修唯一规范和小实例，而非直接拼接所有旧PR。

## 包与唯一writer

| 包 | owning修改 | 验收 | 状态 |
| --- | --- | --- | --- |
| G1-v1 | lead收敛规范、当前授权/路由/旧文档定位，独立review | 明确source/capture/consume、S0关系scope、I/Foff、S最小图、真实闭环及限制 | G1-v1限定S0已接受 |
| E1-S0 | Connector public capsule store/routes、SDK读取/组装、schema/文档 | 深冻结、chunk覆盖/expiry/cursor、零native sealed读、旧提交不变 | capsule/SDK、信息页对象绑定、focus和退役owner修复已实现；382 exact-game测试；最终产物真实采集、界面/逻辑差异与提示范围验证通过 |
| E3/E4-S0 | S张量化/图、序列训练、导出和可信adapter | I/Foff、无身份泄漏、候选/实体绑定与顺序、无标签推进、真实小训练/导出一致 | 160项source回归、40项数据回归；174个实际offer、3epochs/45updates、114行导出一致性及learned实机通过 |
| E0-checks | 精确纯prose路由＋repository cheapguards，必要反例 | CI自身完整检查与独立review，不回溯跳当前PRfull | PR166已通过两OS CI合入；PR167测试fixture隔离复用也已合入 |
| Lead闭环 | Runtime shared acquisition、isolated原生qualification、采集/训练/learned运行/Stop/报告 | exact源码→产物→加载→真实行为；$ ledger；局部范围结论 | G2-S0限定工程闭环已接受；新learned run含11次原生play输入、预算Stop/释放通过，策略循环等限制见证据 |

实施writer各有独立worktree/branch，公共wire只有E1 writer；model读取已封存contract。允许并行开发，实际安装/控制只有lead一位，重本地build/train各一slot。G1规范合入前的依赖topic须显式引用规范exact commit，不改其他旧writer分支。

## 资产与ref处理

当前设计topic从develop `9556d21188b2deea027192567827a339a0ce50f7` 开始。主repo工作区及product/recovery旧worktrees不重置。e302 product和762907 recovery只按所需唯一机制选择，不盲合20个祖先PR；本轮S0不要求PublicM2 campaign/通用cloud恢复先上线。运行在独立profile，保留原shared存档/队列/现有服务及回退产物。开工及集成前刷新实际refs。

## 停止条件

不能确认exact游戏/产物、关键合同互相冲突、native投递unknown不能核清、缺必需权限/密钥、不可恢复数据风险、预算无法限制时停止相关动作并保全证据。发现普通已理解的问题，在包内修复并重验，不逐步找用户。用户允许AI代执行原先安排给真人/用户的功能操作，验收记录AI与实际路径；AI程序产生的数据永不贴真人示范来源，需要真人来源的主张仍按真实操作者判断。

## 预算台账

| 项 | 上限/预留 | 已发生 |
| --- | --- | --- |
| 本轮model/train/API/GPU合计 | 20 USD硬上限 | 0 USD（本轮完成，无付费任务） |
| 本地CPU S0 | 单worker2线程，固定3epochs/max80updates | 实际45updates、17.491秒，180545参数 |
| 外部付费 | 只有本地不能满足且明确计费/上限时才预留提交 | 未提交 |

付费任务若submit unknown，保留预留并核原attempt，不重试/释放预算假装未花费。必要部署优先既有资源，不采购未限定的新服务。

## 本轮完成与下一门槛

源码PR168普通merge与两OS exact-head CI、实际数据/用途/训练/导出/learned输入及限制记录于[G2-S0证据](../evidence/BASELINE_S0_LEARNING_LOOP_2026-10-08.md)。这完成本包的真实最小工程闭环；并不完成全场景V1、G3/R1、Human或科学质量门。奖励推进策略、更多数据及产品/恢复覆盖继续按唯一规范的后续门槛推进，不把小样本loss下降当强策略。
