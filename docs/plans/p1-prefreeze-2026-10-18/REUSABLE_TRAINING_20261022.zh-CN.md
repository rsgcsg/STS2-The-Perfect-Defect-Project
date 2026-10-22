# 可复用数据与四 Agent 训练工程轮

2026-10-22 Brisbane 19:13:36 开工，22:13:36 硬截止（UTC 12:13:36）；
21:43:36 功能截止，留 30 分钟验证与收口。本轮目标是持续新增数据可以增量准备、
冻结输入可以沿正常训练和产物链复用，随机/频率/线性/M-std 共享应用入口。
本轮不启动正式长训，不改变正在录制的应用、Mod 或在用训练组合。
本文件保留这次有界任务的交付与缺口，不自动派发后续工作。源改进已交，
“除数据外全部ready”尚未达成；正式四Agent训练和同条件评价未开始。

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

PR #212 已建立，source review接受范围仍只到其准确证据层；精确PR head和门禁须查live ref。
所选路由为full，不能借focused tests或旧head声称新导航候选portable全过。正常prepared数据响应限16MiB、
请求限1MiB，超限及未知冻结结果先查原Snapshot，不自动重投。日常App/录制/配置未变。
所有本机恢复沿用累计wall/scratch/attempt预算，不表示本机美元收费。

云短验仅在root核准精确head、synthetic输入、剩余费用和停止计划后执行：现有qualified
immutable Modal parent经Git bundle刷新成准确候选，独立Application/Ops/peer，CPU2/8GiB、
L4单容器/零retry/每调用至多300秒，最多一个Run的stop1与同Run恢复两次调用。
build原入口900秒超时，费用计入本轮US$3总额；实际收据留外部artifacts与PR，
不改旧Run/target或借旧GPU收据给新source，也不启动正式训练。

实际云候选固定在 `64afd35ff7b84aef80c267028dc154b4302ff211`，同lock3ebe1850，
image `im-EapjcjEbGg2gUmqh7bp9Ea`；fixture Snapshot `ae1e809d...91c6` 为
TRAIN64/DEV8/TEST8，Run `b1910b88...66170`。首次调用真实L4/CUDA13完成step1，
完整67,224,986B checkpoint `773eb457...94eec8` 已由正常reconcile取回并用原owner
完整读字节、身份和Run绑定验证。源码/配置/输入身份保留，不授GPU提速或轨迹等价。
第二次同Run恢复调用停在provider PENDING，task_id空，无该attempt started事件；
Scheduler在原300秒deadline请求取消，原handle后独立读到TERMINATED。正常reconcile
仍保留Ops `cancelling/result_unavailable`，没有业务completed、新Model或恢复完成证明。
没有第三次调用或重投；最终provider统计0runner/0running_input/0backlog，隔离Application
已正常stop/进程exit0，日常App/录制未动。US$0.50工程包络在原US$3内，配置时长保守
compute估计约US$0.2641含startup/scaledown余量；未读取provider账单，不称实账。
这次67MB checkpoint不能替代正式全量输入/候选的容量与时限检查。

64候选首次full CI的Linux2通过，Linux1在全424源文件strict mypy处报两处baseline
类型错误，聚合失败；原窄mypy从仓库父目录运行，未加载Python项目strict配置，不能冒充
strict通过。B补丁只增加metadata参数注解和JSON descriptor类型声明，无数值/控制逻辑
改变；集成后按Python项目显式配置复验全量mypy并由最新head自然触发fresh full CI。
本机完整424文件strict复验因缺transformers/peft/duckdb报5处环境导入错误；
38个变更源文件strict亦有4处同类缺依赖，均保留原log，不改ignore或称通过。
随后功能候选 `e38a388d79b094d2aa08345ddde2e6ddd49b9044` 的
[CI39764176405](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/39764176405)
两Linux shard与portable聚合实际通过：标准all-extras环境strict424文件通过，
Python合计4798 passed、5 skipped、93 subtests。跳过分别是实际CUDA、Linux root
UID资格、Windows文件共享、可选已安装Runtime包、Windows batch执行，均不算通过。
独立docs/repository/Windows/windows-failures jobs按workflow路由跳过；repository
等源门由full阶段执行。runner merge head `f00409cd` 的tree `b4e54520` 与e38一致，
两个runner前后clean。这是source/test证据，未授安装、native、Human或模型效果。
最后导航收口另版提交，其自身检查只以PR最新head为准，不提前声明通过。
最终新增类型注解/文档的producer与64云producer分开，未为类型注解重建镜像或再训练。
真实全量remote start仍同步传整个父闭包，可能超过600秒响应；未知只查原intent，
未实现durable transfer-phase或异步prepare。本机Application/Scheduler仍需运行，
这轮小fixture不证明真实1GB闭包无等待、全云无人值守或四Agent正式训练已完成。

后续四 Agent 离线比较可复用同 Snapshot 和每个 Run 的最终 evaluation 原件：
baseline 含 DEV/TEST N、score-softmax loss、并列均匀的期望正确数，M-std 含最终
TEST N/loss/correct/multi 指标。普通详情现沿同一 summary API 映射两类 DEV 摘要，
baseline 仅读 Model/Run/Input/Snapshot metadata 绑定并核 DEV N/有限值/并列语义，
TEST封存；统一四方 TEST 对照消费者和指标口径仍未齐，不称四 Agent 评分已完成。
Reference 串行 dispatcher 已有 ref-20 固定旧版本manifest、精确selection、阶段/总
预算及全部20项失败/未派发分母，但不保证本轮P1候选匹配该manifest，也无自动四方
配对统计。实际入口与缺项归回
[原操作指南](../../../python/docs/PROJECT_CONSOLE.md#四-agent-后续评估的现有边界)，
不以Managed evaluator、工程canary或本轮source/test代替正式同条件游戏比较。

原用户范围、17 项和历史依据分别见外部当前执行原件
`P1-REUSABLE-TRAINING-3H-EXECUTION-20261022.md`、
`P1-FOUR-AGENT-FIRST-BATCH-PLAN-20261022.md` 与
`PRETRAINING-DATA-COMPUTE-HISTORY-REVIEW-20261022.md`，同位于
`claude-handoff-2026-10-13/replies/`。仓库日常操作从
[PROJECT_CONSOLE](../../../python/docs/PROJECT_CONSOLE.md)、
[数据生命周期](../../../python/docs/DATA_LIFECYCLE.md)进入。
