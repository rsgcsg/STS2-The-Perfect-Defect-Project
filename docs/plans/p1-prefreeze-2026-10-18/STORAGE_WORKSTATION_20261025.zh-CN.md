# 项目存储归位与长任务容量（2026-10-25）

root 是本轮唯一源码、BOM与清理写入者；开发从 00:31 AEST 到 04:01 AEST，上传/读回等待单列，01:16 自查。基线 `develop@9abab77a66eefc0761df4d9b9044bd76731f48b4`；短分支 `codex/storage-workstation-rules-20261025`。类别为 G3：给既有写入owner加容量准入，含Native录制writer；不改游戏、训练配方、P1/unknown或数据用途含义。

交付是[原数据生命周期里的存储页](../../../python/docs/DATA_LIFECYCLE.md#项目存储真身工位与本机删除条件)、同一处容量操作、现WORKING_RULES第8节和原入口的容量保护；不另建Store、账本、调度器或自动清理服务。源码/测试与在用服务的实际安装保护分开。旧Runtime单局512MiB容量缺陷不在本轮修复，不重投第25局unknown。

用户直接批准归位表v1的A+B+C，A仅111个具名依赖叶；B/C只有R2完整闭包、空目录全量读回和原Store/codec核验通过后才冷却objects。主Store已有2GiB上限，保留两个明确R2 peer，不改20G；不碰workbench/replay使用目录。B另获直接批准：按完整payload分批物化、每批留收据，临时Store仅R2，批量不超过实时空闲减至少6GiB余量（后续用户更新），全部对象逐项对齐后才整行冷却；任一失败整行保留。未获本轮merge授权。

当前实物报告和逐路径表由用户指定的共享 `claude-handoff-2026-10-13/replies/DISK-CLEANUP-AND-RULES-REPORT-20261024.md` 与 `DISK-CLEANUP-TABLE-20261024.md` 拥有；执行收据在本轮任务目录 `work/`，收口给可恢复R2指针。任何du估算不能当释放量，每批前后df才是系统净变化，达不到40GiB不扩表外范围。

已核历史：10-20/21已交Store/P1保全与展开副本冷却；10-22补同ID冷读与lease保护的缓存逐出，明确模型/checkpoint/result和保护内容能超预算。今主Store实际在10-20任务目录，非卡中假定的~/.local主Store。10-24 ENOSPC与更早global_storage_capacity分别保留原故障。上层/decoupling原话已回核：数据定义、管理、使用关联但可局部解耦，GUI映射同一业务owner；本轮改变存储位置与物理准入，不改这些合同。

7天规则已核直接用户原话：Claude原记录 `1218223a-5a59-4d3d-a5d0-806e0fb7fd58` 第20984行，2026-10-24T14:01:24Z，消息 `97289472-e1f3-456f-ab09-ae9df8c397db`；其中“7天保留…可重建的东西…不用每次问…可以”，整目录仍须批。不得从Claude草稿的转述单独扩大权限。

验收：新读者只读存储页判断五个路径；正常Source/API/CLI路径对高余量拒绝，无world/child/use副作用，评估第二局前正常停止并保分母；既有缓存冷读与保护测试；相关Native/应用/root门、非作者复核及project:closeout。清理另凭逐项状态与R2完整恢复收据，不以源码绿代替恢复、安装或40GiB结果。完整换Mac恢复仍待验。


用户后续明确评估剩余75局优先占盘：本聊天已停止本机full门（SIGINT，partial/cancelled保留），不再启动重测试。B后续批次改至少6GiB实时余量，每批只R2物化，不足停止且B原件整行保留；不再触碰任何`2026-10-24/checkpoint-evaluation-*`目录及评估App。C此前已按原条件完成，旧5GiB收据不回写成6GiB。exact-game构建/完整本地门保留未完成，不借远端CI或窄测授安装/运行保护。

首轮远端CI因为系统临时卷只有约7.76GiB而正确拒绝8GiB需求；工作区有约81GiB。第一次把TMPDIR放checkout内部后，目录fixture继承外层Git导致governance测试失败；路线复查确认还须在源码Git之外。最终沿已有RUNNER_TEMP/spireagent-portable，显式实际TMPDIR/TMP/TEMP，原容量检查/余量不变；两次失败保留，不改governance断言或降门。
