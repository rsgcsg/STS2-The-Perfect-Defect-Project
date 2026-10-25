> Historical task; current training optimization definition and execution-plan SOP supersede its forward guidance: [FULLRUN_TRAINING](../../../python/docs/FULLRUN_TRAINING.md#training-performance-belongs-to-the-existing-chain). Original evidence and body remain unchanged.

# f7 M-std 完整状态显式派生（2026-10-23）

本任务只交源码候选、正常 CPU 链检查和隔离 CUDA 资格验证工具。原长训继续，
没有选实际切换点，没有停止/hold 原任务，没有部署、付费调用或新预算授权。
负责人 training_state_derivation，独占分支 `codex/training-state-derivation-20261023`，
基线 `1fadf3f2aa4e4c33dc46293eab2ecb7b0f4d933d`（依赖 PR226 候选，非 develop 通过声明）。
原性能任务的“跨源码完整迁移未验证”缺口在真实 CUDA 资格验证前继续保留。

## 为什么存在、在哪一层

链路仍为 Application/Curation → recipe Run → Ops/Scheduler → ComputeRequest →
worker → immutable Store/Receipt。原严格 restore 正确拒绝 f7 与新源码身份差异；
不能删校验、把旧 CP 当新 Run 的普通 resume，或只拷权重后重置优化器/采样器。
本次属研究 recipe 的实现与显式格式扩展（G3/G6），不改变 Platform 权威或目标函数，
不新增服务、数据使用账、控制器或通用迁移框架。

以前已有正常完整 CP/new-process 恢复，严格绑定同源码/Run，直接复用；
此前校准 clone 允许做实验，不能产生可被正式结果审计的完整新 Run，不能冒称续训；
本次在原 recipe 补单一 f7 来源适配，保留原检查器和正常消费者。

## 支持契约与身份

原来源严格固定 repository `rsgcsg/STS2-The-Perfect-Defect-Project`、
source `f7c77d0df11f591a8c5e285ce5b0586418428981`、
lock `1e1067532fbea42ccc18501c1703bf0e6ea0e86540bcb0356689b773fbb75bd8`；
训练器五文件及 tensor-tree codec 的准确 SHA256 在 `stpd.p1.derivation` 固定，
并回查过该 Git 对象的原字节。拒绝未知源码/锁/闭包，不猜测向前兼容。

新 Run schema `stpd/p1-mstd-derived-run-1` 显式父 `original_run` 和
`original_checkpoint`，新 producer 为实际当前源码。Snapshot、配置、词表、
InputSpec、TargetSpec、分区、架构、总步数和 codec 不变。执行政策只支持：

- 完整原默认 `reference` / CPU2 / microbatch 上限8 / 原 area / defer=false；
- 明确实验 `experimental-packed-v1` / CPU2 / singleton1 / 原 area / defer=false。

CPU 要求相同实际数值 runtime。CUDA 只接受观测过的原 Python3.11.15、
Torch2.13.0+cu130、CUDA13.0、L40S，目标 Python3.11.15 或3.11.17，
其余记录完全相同（设备、dtype、线程、GPU 属性、确定性设置）。
具体实际 CUDA 组合仍须另验，允许 Python patch 并不自动授数值资格。

源 CP 必须处于 training、正整数有效 step、TEST 未开封、无 stop_reason。
新 worker 构造实际目标 trainer 后，仅替换 checkpoint identity：模型、AdamW、
Torch/Python/CUDA RNG、sampler generator/order/cursor、epoch/step/phase、
stop reason、history、DEV/best/early-stop 及 TEST-null 全部保留。
每个非 identity 字段参与 canonical tensor-tree SHA256；转换前、转换后和
普通严格 restore 再编码后必须相等，未知状态字段拒绝，不能静默丢字段。

所有被引用的 prior-best/里程碑 DAG（最多128 CP）拓扑派生为
`stpd/p1-mstd-derived-checkpoint-1`，保留每个 `original_checkpoint` 父、
原 payload SHA、完整状态 SHA 和实际 runtime 兼容记录。后续普通保存/恢复及
最终 best/model/result/export 仍走原审计路径。旧 reader 会拒绝未知 schema。

新 Run 继承原训练曝光和旧失败/尝试分母；它不是新独立种子或研究重复。
旧 DEV 值、history 和 best 选择来自原 reference 执行，没有重算成 packed DEV；
未来 DEV 和最终 TEST 按目标执行政策运行，结果不能伪装成全程同实现轨迹。
起始完整状态精确保留，不要求未来模型/AdamW 更新逐位相同。少量有效更新只证明
工程有效性，不能证明模型质量或最终收益。原始 artifacts 一律不改写。

## 正常入口

现有 remote `prepare` / `start` JSON 增加可选 `initial_checkpoint`，只用于
M-std，且必须带上上述完整显式 `execution_policy`。`source_id`、`config`、
`intent_id`、`max_seconds`、`stop_after` 等沿用原训练控制语法。
Application 仍先完成 Curation 使用记录再读数据/拟合词表；转移完整父闭包、
Ops 入队、Scheduler/worker/Store/Receipt 均复用原路径。
后续 `resume` 输入新 Run 的普通目标 CP；旧 CP 不能混入普通 resume。
本任务没有扩展私有本地 child 的请求语法，CPU 工程测试走正常 remote 映射及
明确 synthetic CPU provider，GUI 不承担新权限或另一套逻辑。

转换不复制、不重置、不改动原 Ops continuation、费用和资源政策。实际操作人必须
把原累计费用、已用 segments/上限、32 reservation 余量、学校40小时累计与绝对截止
带入原 Ops admission；不能因新 Run/attempt 重新获得原已花预算。
本工具不自动选切换 CP，也不授 hold、stop、切换或部署权限。

## 隔离 CUDA 资格验证（待 root 单独安排）

只用获准隔离 clone：step312，artifact ID
`05303485980068a49f97914fff9fdbcdba61e8281d58809f9dad19f87718af6c`，
它不是已选择的实际切换点。原运行、原控制应用和原自动接续不触碰。
先固定稳定源码/锁/镜像并非作者复核。先验 reference；packed 独立再验，
不能把 reference 的通过转授 packed，也不必再做性能扫参。

1. 隔离 Application/Store/Ops、实际 GPU target 与有限一次性预算。正常 Curation
   reservation/use 后，以 `initial_checkpoint` 创建目标 Run，并完整读回来源和父闭包。
   写清原/目标 producer、Python patch、Torch/CUDA、L40S、执行政策和预算/截止。
2. `python -m stpd.p1.derivation_qualification initial --store-binding <private.json>
   --run-id <derived> --attempt-id <isolated> --output <proposal.json>` 只生成请求提案，
   不调 provider。正常 Application `start`/Ops/Scheduler 的已保存 ComputeRequest
   是真正权威，不把提案当入队或另造调度。两次调用均固定 `stop_after=2`。
   工具要求原 cursor 到 epoch-end 严格剩余超过4个完整 batch，避免正常 recipe
   在末尾进入 TEST；不能缩样本或忽略保护来跑通。
3. 首调用实际2步/完整保存/paused receipt 后，用 `replacement` 子命令核原保存
   request/receipt 并生成下一请求提案；正常 Application `resume` 以新 attempt
   在新进程/容器严格恢复，再2步。原 request/result 未知时不重发。
4. `report` 子命令读取两次真正的请求/receipt，核派生初始全状态 SHA、来源/DAG、
   总4步、有限模型/AdamW/RNG/sampler、模型和 AdamW 确有更新、TEST 始终 null。
   在已授权的隔离 GPU worker 运行 `fixed-dev`，用同一最新权重、完整固定 DEV
   做 reference/target 描述性对照；不改历史选择、不调参、不读 TEST 评分。`fixed-dev` 是受信任隔离 worker 的工具：
   normal Application 须预先 require/reserve/use 并准备此 Run；工具不能从远端
   Store 自行检查或授予本机 Curation 账权限，单独运行不能取代这一步。
   Store 的 Snapshot 完整性验证会物理读取所有分区，不等于 TEST 数值使用。
5. 完整冷读回目标 CP 与 typed receipt、实际原/目标身份、worker 终止/费用和未完成项，
   再由 root 决定是否已达到可提交给用户的具体切换候选。实际切换须用户进一步批准。

工具绝不调 GPU API、SSH、原 continuation、付费 provider 或游戏。它仅核输入、生成
request 提案和审计原收据；是否分配资源、真实调用和终止证据仍由原 owners 提供。

## 验证记录与收口

首实现 CPU：12 passed /49.26s。用例明确以合成 modern 状态构造 legacy boundary，
不是实际 f7 字节证明。真实目标链覆盖 Store/ComputeRequest/worker、全状态 hash、
DAG、暂停、新 Python 进程恢复、最终 best/result/导出/model2，及 source/lock/code/
TEST/phase/Snapshot/config/runtime/partition/sampler/未知字段反例。
`0625d156` clean 候选扩展 CPU 16 passed /80.86s，原 workload/transfer/Scheduler
回归17 passed /25.07s；两政策、正常 Application/CLI/Curation/Ops、旧 best
最终保留、实际 CPU inference 与资格工具都走实际消费者。10源码 mypy、ruff、
closeout、diff-check 通过；closeout 选择包含 PR226 依赖的 full gate，未在本子任务
另跑全套或称 CI 通过。非作者已复核并提出两处真实缺口：显式政策应强制、派生
compute-read 库存需含原 CP DAG。修正及相关重验绑定后续 head，原16+17结果不
覆盖这些新增改动。

修正后的 clean `c847e55e`：迁移16 passed /75.58s；原普通 inventory 单项
1 passed /0.69s；10源码 mypy、ruff、closeout、diff-check 通过。非作者复查
A/B 修正、共享 DAG、normal CLI 四 phase，独立验证漏 policy 在 source 读取前
拒绝，并核全部 f7 code/codec 常量与原 Git 字节一致；源码范围可接受。
原17项中未受本轮修正影响的路径沿原收据复用，没有虚称全部重跑。
原件和绑定 producer/日志 SHA 的收据位于作者独立目录
`training-state-derivation-evidence`，root 须亲核后集成；完整选定 CI 仍归组合候选，
实际 f7 checkpoint 字节、CUDA、新部署和原训练切换继续明确未验证。

派生准备库存现在与实际转换共用有限元数据 DAG 遍历，把原 root/best/里程碑
CP payload 纳入 compute-read unique bytes，完整 provenance copy 仍沿原 Store。
逐个加载和释放数值状态，不在历史递归栈保留多份70MB checkpoint。

此轮用例已通过后，仅移除作者专属 `pytest-301` 的4.2GB合成夹具，
`df` available 从1.0GiB变4.4GiB（实际净增约3.4GiB），未碰其他测试/数据/缓存。
新用例逐项清理可重建合成 Store；未提交原数据、权重、CP 或凭据。
验收到可复核 CPU 源码候选及具体 CUDA 工具即停；真正 f7 byte/CUDA、部署、原运行
切换、完整训练/质量/最终 TEST 都仍未授通过。合入/推送/PR 由 root 另行安排。
