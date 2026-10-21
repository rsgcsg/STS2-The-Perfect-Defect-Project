# 当前五小时工程交付：2026-10-22

本轮由用户明确授权立即执行，替代旧 Batch1/Round3H 的派工状态。目标是修复普通日常录制与派生、累计数据到真实短 GPU 训练、Store/R2与容量和正常模型消费者，并在准确门/CI、非作者复核、无活动录制、可回滚后交付日常安装。正式长训练、完整 P1 冻结、未验 Human 和策略质量不因工程交付自动通过。

本轮唯一主管 root；R 独占 native/派生实现，T 独占 STPD 数据/训练，S 独占 Store/Application 映射，I 独占集成分支、根导航与正常安装。各 writer 独立 topic/worktree，以当前 origin/develop 为共同基线；初始准确基线为 `9561d262b27a619174cc735116930d2bbb067306`。组件源码 normal merge 保 path-scoped 来源。直接相关 PR 的本轮条件批准不扩大到其他历史任务。

硬截止为 2026-10-22 07:18:15 Brisbane；稳定候选检查点 06:15、停止变更 06:40，I 最迟 07:05 保据并释放资源。重型/native/游戏只有一个本机槽，由 root 排期；当前正常服务和用户数据不得因任务收口擅停。每个领域先交实际消费者增量，最后组合门只在稳定准确候选运行。

I 集成分支为 `codex/p1-fiveh-integration-20261022`。正常初始 Application 使用永久 941ae84d release/c47 来源，游戏初检 PID8720、录制与 Close 均 closed；这只是开始时点，安装前重核。旧 G 私人检查点不再派发动作。当前本轮源码/构建/安装/装载/模型消费者均待完成，不能复用旧报告直接授予资格。

操作入口保持 [日常导航](../../DOCUMENT_MAP.md#按我要做什么进入)，现场安装按 [原 installer](../../../python/docs/DEVELOPER_KIT_INSTALL.md) 与 [Game Mod lifecycle](../../../apps/game-mod/README.md#lifecycle)，模型按 [原 Application](../../../python/docs/PROJECT_CONSOLE.md#p1-model正常下载注册与使用)。各实现负责人同步其原功能指南；I 只汇总实际交付与未完成义务，不另造流程。
