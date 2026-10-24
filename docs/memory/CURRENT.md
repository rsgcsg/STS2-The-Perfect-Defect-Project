# 当前：存储归位与长任务空间规则，尚未整体验收

当前任务是[工位存储归位](../plans/p1-prefreeze-2026-10-18/STORAGE_WORKSTATION_20261025.zh-CN.md)。root唯一写入，基线develop9abab77a，短分支codex/storage-workstation-rules-20261025；2026-10-25 00:31–04:01 AEST开发硬限，上传/读回等待单列，01:16自查。存储页归原DATA_LIFECYCLE；先核原上层/SOP/decoupling、历史Store冷读与磁盘故障再实施。

用户已批逐路径表v1 A+B+C：A仅111个具名可重建依赖叶；B/C须完整R2闭包、空目录全量读回和原Store/codec核验后才冷却objects，metadata与恢复指针保留。B可按完整payload分批、每批至少5GiB余量，全部对象对齐才整行冷却。A已实测df净增1.87GiB，不借du宣称10.79GiB释放；B/C未验完不删。实际主Store已有2GiB上限和两个具名peer，不改20G。

现有workbench、训练App和评估replay服务保留；旧replay仍recovery_required，不碰其目录或重投unknown。此源码候选只加新局/准备/录制写入/测试前容量准入，尚未merge或安装，不能声称在用旧版本已有保护。唯一下一步是完成正常入口反例与必要门、R2完整恢复、非作者复核和独立收据，给可审PR；合并仍待用户同意。共享replies里的指定报告/清理表记录现场，任务收口更新此段。

之前的[检查点评估](../plans/p1-prefreeze-2026-10-18/CHECKPOINT_REFERENCE_EVALUATION_20261024.zh-CN.md)保留其失败和未启动分母；单局Runtime512MiB限额与实际ENOSPC不同，不从本轮清理恢复旧派工或资格。源码、构建安装、真实运行、Human/研究准入和模型效果分别验，不以正常退出或CI替代。
