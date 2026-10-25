> Historical task; current training optimization definition and execution-plan SOP supersede its forward guidance: [FULLRUN_TRAINING](../../../python/docs/FULLRUN_TRAINING.md#training-performance-belongs-to-the-existing-chain). Original evidence and body remain unchanged.

# 首批训练性能候选与检查点切换准备

root 负责本轮完整交付，基线 `origin/develop=58d445f534c608260fbf825bdc5ae204772f8011`，
独占分支 `codex/training-performance-switch-20261023`。现有 f7 学校训练及
31fa 控制应用继续；本轮独立工作树不修改现场、原 Run、检查点或已发布证据。

## 用户目标与停止边界

用真实 TRAIN 样本确定端到端瓶颈，交付有实测收益的数值实现候选、必要的内部
校准/测量和检查点暂停接口、完整状态迁移/恢复验证及原操作指南。CPU、输入准备、
批处理、同步、库/后端与执行参数均可调查，但不以 GPU 占用率代有效吞吐。
模型架构、N-only 学习目标、固定输入、优化批64、LR、seed、DEV/TEST职责不改。

最新用户明确：候选通过后须先报告实测结果、风险和能否在下一个明确检查点
保存后切换；**停止现有训练、选定实际切换点、切换新版均须用户另行明确同意**。
本轮可实现并隔离验证这些能力，不能部署/调用它们来提前停止当前训练，也不能
关闭其自动接续。未获得切换同意不妨碍完成独立工程、实验、审查和交付。

新增优化实验额度 USD10 是上限，不是消费目标；含构建、GPU、CPU、失败尝试和
传输，独立于原训练 USD22。学校累计40小时上限不变。原训练资源不可被抢占或
用于并行竞争实验。第二学校 GPU 仅在门户规则/配额明确允许时考虑；否则用独立
Modal 短实验，记录实际候选、硬件、每次上限和结果，不按免费额度假定无费用。

## 有界交付与验收

1. 原正常 Store/用途/recipe 链中可调用的真实 TRAIN 短校准，记录完整输入身份、
   阶段 wall、CPU/CUDA时间线、有效微批、显存峰值和真实更新吞吐。原 synthetic-only
   observer 保持限制，不伪装真实数据为 fixture。实验不发布为正式选优 Model。
2. 一次只加入有依据的优化，保留完整图/候选和每次更新的样本/权重；比较前向、
   多步梯度/AdamW、随机状态和完整保存/替换进程恢复。记录数值差异和原失败，
   改变执行轨迹时明确另版，不能声称同轨迹恢复。
3. 检查点控制和显式迁移只补原 Application/Ops/worker 接缝，不新造训练控制器、
   用途账或存储权威。原请求 unknown 只查原 handle。不可放宽 producer、输入和
   checkpoint 身份校验来接旧状态；原件不改写，派生保存完整亲缘关系。
4. 必要定向检查、稳定候选规定门禁和非作者复核通过，原 SOP、组件入口和 CURRENT
   同步。source/test、实际GPU、恢复、收益和可切换性分别报告。
5. 给用户可审查的结论：实际完成、实验结果/失败、端到端加速及适用边界、成本、
   风险、可选检查点和回退。不承诺正好赶上开发期间的下一个百分比点。

首次60分钟检查新增事实与最短GPU对照是否成立；2–4小时复核净收益、完整验证
成本和资源余量。这是成本检查点，不是允许省略验收的硬期限。连续两种失败或
缺少有效收益时收缩到已证明的修复，不堆更多框架或消耗完预算。父任务四Agent、
最终TEST和40次Reference保持未完成，本候选交付不替代其验收。

## 最新用户范围澄清

用户明确不限于本轮或完全按迁移考虑：查清所有训练链路的卡点，分别回答当前
后续能否换、未来训练的经验和提速方案。原训练仍继续；实际停点和切换仍待批准。
因此保留精确恢复候选的原严格检查，同时可隔离测量面向未来训练、明确改变FP32
累加轨迹的实现；旧 packed 原型失败不能改写为 exact-resume 通过，也不据性能
将新实现授予质量或当前切换资格。新模式 `experimental-packed-v1` 仅作为显式
实验执行选择，默认仍 reference，原模型、完整输入与优化batch64保持。

## 实测结果与本轮交付边界

本轮具体数值优化、真实 TRAIN 校准工具、正常新 Run 的显式执行配置/完整保存恢复/
模型包及 CPU 消费链路，与训练运算的架构/SOP 归属一起交付。**原 f7 Run 跨源码的
完整状态迁移尚未实现或资格验证，本任务的该验收项仍未完成，不能称下一检查点可直接切换。**
原训练不停止、不关闭接续、不热换配置。用户最新两项结论诉求不取消这项缺口，
父任务的最终 M-std、固定 TEST、四产物、40次 Reference、R2恢复验收仍独立未完。

| 实验 | 同轮对照与结果 | 能证明与不能证明 |
| --- | --- | --- |
| 串行事件读取→8并发 | 固定同一641项，73.089→9.037秒，8.09倍 | 完整索引/manifest核验保留；只是该读取阶段，不是整体训练8倍 |
| row-fused-v1 | A/B/B/A，12.540→10.538秒/更新，1.190倍吞吐 | 256真实TRAIN、4更新、1热身后计时；该短测model/AdamW/RNG摘要一致，不外推所有输入同轨迹 |
| experimental-packed-v1，singleton | A/B/B/A/B，19.949→2.186秒/更新，9.128倍 | 同次独立L40S容器、CPU2、batch64、FP32；模型/AdamW摘要不同，RNG一致，4帧分数最大差约5.01e-6；不是长期等价/质量/完整总墙时收益 |
| packed微批2 | 短测约13.13倍 | RNG改变、分数最大差约0.183，未过原严格容差；保留为失败当前替换候选，不能选其最大数字宣传 |
| 早期packed原型CPU3步 | 最大分数差0.00068666，超过原预定容差 | 原失败保留；后续重新引入为明确另版实验，不改写exact-resume结论 |

真实训练模式的一帧 CUDA trace，原实现21958 kernels、21907 launches，packed为
1622/1571；select_backward从3548降到23。Profiler在所有吞吐窗口之后运行，
只覆盖一帧/反向，不含整批optimizer；不拿instrumented毫秒当全训练墙时或GPU利用率。
大部分张量数学本来就在GPU；CPU/Python逐实体组织并提交大量小GPU操作与细碎反向图，
才是本轮主要稳态浪费。减少调用、批量聚合比直接加显存/CPU核数更有根据。

原训练接续的真实准备另有verify约201秒、population约192秒；606卡点采样98.876%
落在网络poll，同步索引+manifest串行读。周期保存encode约0.25秒，而发布30–48秒；
DEV完整446样本约31秒。优化训练主体后，这些固定开销占比会上升，必须继续按原阶段
wall观察，不能把9.13倍当总任务承诺。换更快CPU可能改善Python提交，但未做隔离
CPU型号/核数对照；不能用不同容器11–20秒基线证明CPU收益。多核适合独立读取/预投影，
不是直接并发更新同一AdamW。异步传输与编译/融合是后续按profile触发的候选，均未授收益。

五次独立Modal GPU调用和相关CPU构建已结束，原容器不存在；原学校训练继续。
累计配置保守成本上界USD2.8657428（含预算传输准备金，非账单），新增USD10为上限而非目标。
未为了用尽余额追加实验。学校累计40小时及原USD22不变。

不可变performance原件（R2 Store artifact ID；原输入/producer/trace/hash归其parents/payloads）：

- row ABBA：`b9439af6eadaf678f4a4c63e8a3c7d83f9dda1ff7dcae6edd744cfaa975df833`，执行source `b638125c17109c7736c5add26c1a1b6cf0447d59`。
- packed ABBA+B：`7dc2b4d0e62fe63ffd029693f9f327759b81ab1784ebc80082a6d4781d4003d7`，执行source `8a7cffab4629027c24f375c590e550815fdb41a8`。
- packed trace原字节7050805，SHA256 `50105f231add3a6eaaac49a82b21f113a463daa59de3b7d7b52f2bbee32fe8b7`。
- 共同真实TRAIN校准input `488f669757f4ddf3ec80b10727481b18d8fa38cef877fd1f9d52607615877796`；256条来自原CP312后续sampler，未forward或调优DEV/TEST。

上述报告/trace均通过正常Store读取校验自己原字节；没有递归重读并授予所有历史parents新资格。

## 在项目中的位置和后续落实

原[架构职责](../../ARCHITECTURE.md)保持：研究recipe/model拥有数值执行与完整input/output语义；
Application/TrainingService/Curation/Ops/Scheduler拥有同一用途、intent、预算、attempt和接受链；
Store/Reporter拥有不可变数据与校验，Compute provider只管资源/启动/终止。
优化是贯穿这些负责层的性能工程工作，不新增顶层“加速器服务”或第二训练控制器。
GUI只是同CLI/API的映射。模型质量属于原研究与评价协议，不由吞吐自动升级。

可复用的[训练生命周期/SOP](../../../python/docs/FULLRUN_TRAINING.md#training-performance-belongs-to-the-existing-chain)
规定训练前短真实校准/完整恢复，训练中阶段观察与原句柄，训练后固定质量/CPU使用/实战/成本。
相同配置独立复跑有新Run/use/cost，可复用仍适用profile；同Run恢复只有新attempt，
不算新seed实验。不同shape/model/data/library/device/dtype只使受影响证明失效。
未来RL沿用共享生命周期，但actor/learner/buffer/策略版本和lag/环境步数评价属于未来RL recipe；
本轮不实现RL，也不为每种假想训练建设框架。

切换建议：packed值得进入未来新Run的受控质量评估；当前f7在完整迁移+真实CUDA替换进程恢复+
非作者复核/门禁之前继续原法。即使row短测状态一致，也不能跨源码普通restore。
一旦迁移候选实际通过，再给用户准确可行的停点、成本和回退选项；得到用户同意后才执行。
本轮不虚构已经可切换，也不静默取消原迁移义务。

外部原始资料仅用于解释候选方向，不借其示例数字：PyTorch
[性能指南](https://docs.pytorch.org/tutorials/recipes/recipes/tuning_guide.html)
建议减少细碎调用/同步并按实际条件测量；
[数值说明](https://docs.pytorch.org/docs/2.14/notes/numerical_accuracy.html)
明确数学等价的batch/slice、实现或平台仍可能有浮点差异；
[异步传输说明](https://docs.pytorch.org/tutorials/intermediate/pinmem_nonblock.html)
列出pinned memory、独立stream/DMA的并行条件，手动pin不保证净收益。
文档当前2.14，实测执行器为2.13.0+cu130；这些链接是方向参考，不是新版库资格。
