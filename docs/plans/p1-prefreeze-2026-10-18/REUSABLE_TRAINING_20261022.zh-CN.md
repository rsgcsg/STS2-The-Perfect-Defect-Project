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

已在同一链补齐 P1 增量 prepare/freeze CLI，数据缓存只复用核验后的投影解码结果；
随机/频率/线性 recipe、worker、远端 ComputeRequest、Model 下载注册与正常 Agent
消费均复用原 Application、Store、Ledger 和 Runtime。G1/G2 无 optimizer update；
G3 完整 Adam 状态与同源 checkpoint 精确恢复，DEV 选优/TEST 最终保留。
G3 无交叉项的线性模型不能利用候选间相同的全局常量改变相对偏好。
M-std 按长度分组批处理，不截输入；CPU 同条件64N样例 advance 中位数由0.68718秒
变为0.41541秒（1.6542倍），单条/批量前向最大差1.04e-7。这不证明旧 dropout/RNG
轨迹或GPU提速，旧 producer checkpoint 仍拒绝跨源码冒充精确恢复。

实际正常 Store 两次 prepare 为46.911秒（22次解码）和1.877秒（22次命中/0次解码），
每次仍核验1,041,228,140原投影字节；7206唯一N、1922别名、17unknown、0conflict。
此只读准备未改变原ledger数据库/行摘要或manifest集合。按观察组草稿不是固定研究划分，
未freeze/reserve，也不提升Human或研究资格。原件收据在本轮外部outputs目录。

正常 CLI→HTTP→私有child→Store→Model注册测试通过；native envelope/contract仍是
fixture，不是实际安装或游戏运行。真实 producer 生成的7210唯一N/1922别名规模回归
覆盖完整大于8MiB的CLI输出和小于1MiB冻结请求，固定Snapshot读回不因后续prepare改变。
初始fixture短ID仅6.05/7.51MB，未达到其规模断言；补真实长度/alias后又暴露fixture自身
额外source点漏算，均修fixture并保留失败XML，不把这些失败称产品性能问题。
baseline缺tokenizers在正常preflight拒绝、无operation或manifest写；无需safetensors。

剩余验收是稳定候选的所选门禁、PR source review及准确新source的有限云短验。
所选路由为full，不能借本轮focused tests声称portable全过。正常prepared数据响应限16MiB、
请求限1MiB，超限及未知冻结结果先查原Snapshot，不自动重投。日常App/录制/配置未变。
所有本机恢复沿用累计wall/scratch/attempt预算，不表示本机美元收费。

云短验仅在root核准精确head、synthetic输入、剩余费用和停止计划后执行：现有qualified
immutable Modal parent经Git bundle刷新成准确候选，独立Application/Ops/peer，CPU2/8GiB、
L4单容器/零retry/每调用至多300秒，最多一个Run的stop1与同Run恢复两次调用。
build原入口900秒超时，费用计入本轮US$3总额；实际收据留外部artifacts与PR，
不改旧Run/target或借旧GPU收据给新source，也不启动正式训练。

原用户范围、17 项和历史依据分别见外部当前执行原件
`P1-REUSABLE-TRAINING-3H-EXECUTION-20261022.md`、
`P1-FOUR-AGENT-FIRST-BATCH-PLAN-20261022.md` 与
`PRETRAINING-DATA-COMPUTE-HISTORY-REVIEW-20261022.md`，同位于
`claude-handoff-2026-10-13/replies/`。仓库日常操作从
[PROJECT_CONSOLE](../../../python/docs/PROJECT_CONSOLE.md)、
[数据生命周期](../../../python/docs/DATA_LIFECYCLE.md)进入。
