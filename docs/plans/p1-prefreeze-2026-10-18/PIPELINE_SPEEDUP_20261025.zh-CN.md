# 数据冻结至学校8步：本轮任务P

当前目标是同一Q冻结文件/配置/Snapshot，五分钟内完成准备和任务提交（目标三分钟），学校首次更新五分钟内，真跑8步→R2检查点→空目录冷读回。唯一源码owner为本对话root，分支codex/pipeline-speedup-20261025，基线origin/develop21fd85ea。只改Store传输、准备和训练启动的实际重复读取及计时；不改模型、配方、权重、原件或unknown不重试规则，不开正式训练。Q保留它的录后自动处理分支，不写共享storage。

用户已批准计算端原始录制只带编号引用。方案位于交接replies/PIPELINE-CLOSURE-PROPOSAL-20261025.md。实现G2：按训练消费者明确载荷清单复制，全部原始manifest/父编号保留；计算输入读回不声明原件完整冷恢复，普通copy_artifact的完整保全行为不变。一次操作内复用精确Store内容读取和已验证Snapshot；当前用途/排斥/曝光仍查原CurationLedger，worker独立核验其实际输入。每次首次读取、跨进程或冷读都保留内容/来源/lock/完整InputSpec身份检查。

Q接口由其原任务拥有：state_dir/cohorts/<configuration_id>/weight-candidates.json，schema=training-data-weight-selection-20261025-v1，status=frozen_user_confirmed，selected_configuration_id、configuration、snapshot_id。P直接消费同文件/同编号；已有snapshot_id不重新freeze。P1PreparationCache只缓存索引和局事实，不含模型Frame；P复用其内容与代码身份绑定、Store流式SHA与lease，SnapshotReader仍完整重现选择、别名、整局、分区。没有跨机可信SQLite回执，也没有单凭以前验证过而跳过接收字节检查的通道。

历史结论：09-15流水线B已有带哈希归档上传与独立接收验证，结果下载按清单不隐藏拉取raw父链；该句首先约束结果下载，不能当P1训练能力证明。当前P1完整搬运由10-21提交66ac22b5接入通用完整copy_artifact而来；这次沿同Store补显式计算载荷清单，保留完整保全入口，不重启旧Hub或另造训练链。10-25计算提速PR239已有R2事件并行读8，属于既有收益，本轮不重计。

验收采用真实Store/生产Snapshot/原Curation/正常package→start-prepared→Ops/Scheduler、相关负例、一轮完整准确候选CI和学校8步。传输反例覆盖原始载荷未复制但来源可追、所需载荷损坏拒绝、warm不重上传仍核SHA、普通完整复制仍补齐原件；读取反例覆盖缓存后磁盘损坏、缓存选择被调用者修改、跨进程重新核验；计时反例覆盖准备与训练分别超时且只产生一个进程/句柄。源码检查、学校安装/运行和数据/效果资格分列，不借CI绿证明学校通过。

开发从2026-10-25 23:59 AEST计4小时，2026-10-26 03:59硬停，03:29开始收尾；纯登录/学校/CI等待另计。00:44和01:59复盘。用户录制时不跑本机重准备，空闲<15GiB停止新增，不用游戏/Modal。学校使用计入本轮8小时总额，前卡C占用保守上界0.5136小时，剩余上界7.4864小时；精确计费量仍未知。当前学校门户已登录但尚未分配机器，旧1800失败尝试已结束，不重投其旧Run。

当前正在相关回归和类型检查。下一步：修复真实回归、稳定候选CI，确认同配置全量准备耗时后才分配学校GPU；报告回交replies/PIPELINE-SPEEDUP-REPORT-20261025.md，耗时表附其后。合并前普通merge最新develop并重验受影响处，按现用户常设授权/已批约定范围普通merge，不推main。
