> Historical task; current training optimization definition and execution-plan SOP supersede its forward guidance: [FULLRUN_TRAINING](../../../python/docs/FULLRUN_TRAINING.md#training-performance-belongs-to-the-existing-chain). Original evidence and body remain unchanged.

# 同一训练 Run 的有界自动接续

本轮用户已授权把自动接续直接用于正在学校 L40S 上运行的首批四 Agent 训练，
同时要求说明其上层职责、解耦、维护、扩展和 SOP。root 是唯一实现、当前应用、
远端与预算写入者；基线 develop `33de58751631a19d9e799d98ac3f81487cc5f255`，
独占 topic `codex/training-auto-continuation-20261023`。当前 source/test 候选未验收，
不能称已自动接管；实际训练 worker 仍固定在 f7c77d0d，输入、Run、配方不变。

## 目的、位置和收敛

链路位置：唯一工作图的训练任务到 compute/worker、checkpoint 再回任务生命周期
这一连接；属于已有应用能力扩展。用户表达“在已批准预算与截止内完成这个 Run”，
不是反复提交分段任务。Application 保留这份有界接续授权；同一个 Ops 数据库保留
策略、分段 job/attempt、预约和事件；同一个 Scheduler 校验、观察原请求及派发。
worker 仍只负责一段数值工作和完整保存，provider 仍负责准确启动/终止事实。
用途归原 Curation，数据/模型/研究条件归原 research，不另造控制器或用途账。

自动接续只消费 Scheduler 已经完整校验并持久接受的 PAUSED receipt，使用该
原 attempt 的准确 checkpoint 创建新的确定 intent；接续入队、额度预约与策略游标
在同一 Ops 事务内提交。重启只恢复这份账和原 handle；unknown、失败、取消不自动
开下一段。完成、停止授权、预算/截止/分段上限或身份不符关闭接续；旧分母与 CP 保留。

可复用的是有界授权、持久状态、原句柄核对、原子接续和停止规则。每类训练仍定义
完整 checkpoint、兼容性和实际恢复，仍走已有 workload preflight/worker；本次不抽象
统一模型状态、不加第二个训练引擎，也不声称所有未来配方已验证。不同 provider 的
终止证明和硬超时由其适配器提供，不能从网络断开推测进程停止。

控制应用和执行 producer 明确分开：默认仍要求同源码；可信部署配置可显式固定
`execution_producer`，必须和 immutable target 的 producer 完全相同。应用升级不会
改写 target/Run/checkpoint 的 producer；worker 仍在原源码执行自身身份核验。
本次只改应用/Ops，数值训练、模型、worker、ComputeRequest/Receipt 格式不变。
对旧配方的兼容读验和真实接续需要单列证据，控制升级不转授训练来源身份。

## 验收与停止

最低真实链：正式 CLI/HTTP 记录接续授权 → 原作业真实 PAUSED → 完整 CP 原绑定 →
同 Ops 自动入队 → owner 重建恢复 → 同 Run 数值继续并正常完成。另验 unknown 不重投、
预算/截止/段数/身份/手动停止、原子重复推进和重启后过期排队不启动。稳定候选按
原路由门禁和一次非作者复核；当前正式训练还需准确旧 handle 在应用升级前后不变、
正常开启授权、真实自动跨段并有更新。未发生跨段前只称已配置，不称真实自动接续通过。
当前学校总40小时、费用USD22、已用恢复2/3、诊断2/6和全部历史预约不变。

本轮不做训练性能热改、调 batch/精度、不新增算法/训练 seed、不重启 GPU worker、
不升级日常应用。自动接续不是重试失败策略；真实失败仍按原任务的有限恢复授权处理。
运行中合盖/本机睡眠时，远端 worker 可继续；本地 Scheduler 恢复后按原账追赶。
若要本机长期离线仍立刻接续，必须把同一应用 owner 部署在常在线主机，不能承诺
笔记本睡眠时本地线程仍执行，也不另开第二 Scheduler 抢账。原指南是唯一操作 SOP。
