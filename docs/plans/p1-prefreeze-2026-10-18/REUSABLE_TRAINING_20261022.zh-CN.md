# 可复用数据与四 Agent 训练工程轮

2026-10-22 Brisbane 19:13:36 开工，22:13:36 硬截止（UTC 12:13:36）；
21:43:36 功能截止，留 30 分钟验证与收口。当前目标是持续新增数据可以增量准备、
冻结输入可以沿正常训练和产物链复用，随机/频率/线性/M-std 共享应用入口。
本轮不启动正式长训，不改变正在录制的应用、Mod 或在用训练组合。

## 基线、负责人和范围

基线 `origin/develop=1e5d83100a8222f211a0e633120ea8123dd06427`。
root 负责取舍与最终验收；I 独占共享 CLI、registry、dispatch、训练操作、
Model 注册与本任务/当前导航，并在 `codex/p1-reusable-integration-20261022` 集成。
A 独占增量数据模块和 P1 Curation/Training Data；B 独占 P1 baseline、Agent、
export/installation；C 独占 P1 数值训练/input/model/workload/preparation/worker。
各 owner 交稳定提交后由 I 核实 diff、消费者和检查再集成，不共享可写分支。

用户本轮新增云费用总上限 US$3，I 是唯一付费执行 owner；每次真实 submit/deploy
先报准确输入、动作、费用和停止条件，由 root 核剩余额度再执行。unknown 只查原请求，
不自动重投。未授权 merge、生产安装或原生游戏控制。独立 fixture 不授真实游戏或研究资格。

## 同一条链与验收

原件/Store → P1 投影及 Curation → 固定成员/Input/Target/recipe → 既有 Run/worker →
compute/provider → checkpoint/Model → 正常 Agent。复用 Application、Ops、Scheduler、
Store 和正常操作，不另造 campaign、用途账或控制器。新录数据只进入下一版快照。
Gold、曝光和亲缘 unknown 保留；N-only 不因无 next/end 整局排除。

本轮验收逐项保留实际证据：增量发现和核验复用；可读回分类/固定成员与使用历史；
随机/频率/线性完整程序及正常产物路径；M-std 无回归和有测量的性能修复；
普通准备、监视、停止/支持的恢复、结果和 Model 注册消费者；受影响原指南可照做。
完整四 Agent 正式训练、同条件实战比较、Human/全机制/模型质量仍是后续义务，
不以本轮 source/test 或短验代替。

## 已复用、当前差额与下一步

开工核实既有 `training_cli` 已映射 Application 的 prepare/start/status/progress/
resume/cancel/reconcile；`RemoteTrainingService` 复用 Scheduler/Ops，已有 Model
下载注册入口。旧 M0/M2 的完整 checkpoint、handle 和冻结输入经验继续使用，
不恢复私有分段 campaign。原短 GPU 收据只保其原身份。

当前共享缺口是 P1 recipe/Agent/Model 注册仍仅识别 M-std，增量数据缺正常 CLI 映射。
I 下一步与 A/B/C 固定具体 API 后补现有入口，先跑最短 fixture 消费者，再选稳定候选门。
最终提交、运行收据、实际失败和剩余义务在此更新；本段不是验收通过声明。

原用户范围、17 项和历史依据分别见外部当前执行原件
`P1-REUSABLE-TRAINING-3H-EXECUTION-20261022.md`、
`P1-FOUR-AGENT-FIRST-BATCH-PLAN-20261022.md` 与
`PRETRAINING-DATA-COMPUTE-HISTORY-REVIEW-20261022.md`，同位于
`claude-handoff-2026-10-13/replies/`。仓库日常操作从
[PROJECT_CONSOLE](../../../python/docs/PROJECT_CONSOLE.md)、
[数据生命周期](../../../python/docs/DATA_LIFECYCLE.md)进入。
