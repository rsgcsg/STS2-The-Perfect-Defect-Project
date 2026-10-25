# 录后自动维护：本轮任务

当前：root 已完成源码修复和125项相关Python回归、340项控制台回归，尚未安装或验收下一局。目标是一局封存后沿原维护器导入、派生、R2保全、增量盘点，固定一页小结；用户查看权重后，一条命令冻结。基线为 `origin/develop` 的 `21fd85ea`，写入分支 `codex/auto-data-intake-20261025`，唯一写入者为本对话 root。

链路位置：⑤ Store → ⑥ P1派生 → ⑦ Curation，属于原组件实现修复和已有能力接入。上游提供 Recorder 的不可变 Close 原件；下游读取同一 Curation 整局用途、Snapshot 与精确 cohort 文件。失败保留原意图和原件，unknown 不自动重试。

## 范围与停止

只改封存后的原 importer/maintenance、P1准备缓存与盘点、CLI和小结映射。不改录制开始/停止/Close（B卡）、不训练、不改已冻结 `414fb84d…` 或权重公式，不恢复Hub/Gold/模型血统，不删数据。暂不改共享 `spireagent/storage/`；如需改公共函数，会先在此说明。游戏运行时仅观察封存元数据；每个新重步骤等待游戏退出。空闲不足15GiB停止派发。

开发窗口从2026-10-25 23:13 AEST起，5小时硬限至2026-10-26 04:13 AEST，收尾预留30分钟；45分钟和3小时复盘。等待用户正常录制、CI的时间另计。验收为受影响回归、一次准确候选完整CI、普通merge最新develop、下一局用户真实录制自动进入小结。仅已有录制验证不能替代“下一局”验收。

## 已核原件与决定

- 旧服务日志真实失败为 `ENOSPC`，保存失败状态再次写盘抛错，维护线程退出。旧磁盘报告的disabled不是当前磁盘配置的完整事实：磁盘上policy仍enabled，后来记录的目录状态为installed_artifact_mismatch，注册Tool为486d…；修复两者，保留身份检查。
- 卡A盘点依赖临时checkout与固定25投影。现行Curation已经按整局/同源/已知亲缘检查DEV/TEST与TRAIN用途并保留历史曝光；本轮复用，不能重分已训练组。
- 9月 `decision_index.py` 已有持久校验索引，`decision_cache.py` 绑定代码和输入身份；当前 `P1PreparationCache` 已将相同模式用于P1。本轮接入它，缓存解析和局事实；每次使用仍经Store校验字节，分组用途实时查原ledger。
- 9月B流水线把Close之后的打包、验证、传输留给外部进程；本轮沿原长期Workbench/serial importer接，不把工作塞进游戏线程。Hub/队列/账号按10-18 CLOUD_MINIMAL继续冻结。

## 当前下一步

真实25组已复算：A0 18、A10 7，5胜/18负/2未知，共10319 N；TRAIN 23组/9315步，DEV 1/446、TEST 1/558，DEV/TEST各缺A0 3＋A10 3。与卡A的成员、权重、分组完全相同，仅新增holdout目标/缺口字段形成新预览ID `0e94cd63…`，未冻结。第一次解析65.515s；缓存命中25份/0重新解析、仍校验1,308,163,988字节，3.372s。原`414fb84d…`未改。

新增回归用真实Store/cache/Curation/Snapshot验证增量N、新整局先补缺口、原曝光用途不移动、未知结果保留；磁盘ENOSPC二次保存失败时线程仍活且不重发，游戏未确认退出/不足15GiB不派发，新Tool停止，冻结丢回执只读核对、不重复publish/reserve。游戏观察/HTTP和fixture转换器为替身，不授实机/Human/R2资格。主风险为G2应用编排/消费者连接；本卡明确无需另开复核员，以相关回归和完整CI、真实下一局验证收口。

下一步为稳定候选准确CI、普通同步最新develop；安装前重新核游戏进程、GAME-SLOT与B卡实际进度，避免竞写同一Workbench profile/Tool。23:47时安装槽归B，Q未改服务/原profile/Tool。真实下一局是待完成验收，不能用现有25组或合成数据代替。

## 历史能力如何处理

| 以前有过 | 当前是否有 | 本轮处理 |
| --- | --- | --- |
| 9月持久来源索引/有界增量构建 | legacy `decision_index` 和 P1准备缓存都在 | 接P1原缓存，缓存局事实，重用原Store字节核验；不搬legacy格式 |
| 整局/同源/已知亲缘分组，永久用途与曝光检查 | `dataset.py`、`p1_curation.py`已支持 | 沿原ledger，不另建账本；DEV/TEST缺口用卡A规则填新组 |
| 封存后外部发现/打包/校验/上传 | 原maintenance和serial importer已支持，但线程已死 | 修ENOSPC故障路径，保留原操作和unknown门，接小结 |
| 固定预览、确认后冻结 | Snapshot/Curation与训练cohort adapter已在 | 一条CLI显示权重/确认/原freeze，训练读同一固定文件 |
| Hub、队列、团队账号/云控制台 | 历史实现保留，10-18决定冻结 | 不恢复，复用本机Workbench与R2 |
| Gold/继承保护/模型血统/质量标注 | 历史Curation部分已有 | 不接入本轮流程，不增加门槛 |
