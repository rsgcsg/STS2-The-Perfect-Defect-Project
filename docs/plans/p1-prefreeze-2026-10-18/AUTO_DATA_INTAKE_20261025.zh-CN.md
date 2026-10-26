# 录后自动维护：本轮任务

> 2026-10-27状态头：原Q已由Q3正常真人局自动入库→派生→R2→小结验收完成；Q2/Q3已安装，X2再升级为Annotator30。仍未证明零卡顿，查询超时/下一局重活归可选X3；本轮不动生产Q。 最新[事实收据](../../evidence/CONVERGENCE_BASELINE_20261027.md)与[CURRENT](../../memory/CURRENT.md)替代下面旧任务派工；历史正文和分母保留。

当前：root 已完成源码修复和127项相关Python回归、340项控制台回归，已普通并入B的develop `9b87d215`，正在最终组合CI阶段。目标是一局封存后沿原维护器导入、派生、R2保全、增量盘点，固定一页小结；用户查看权重后，一条命令冻结。起点为 `21fd85ea`，写入分支 `codex/auto-data-intake-20261025`，唯一源码写入者为本对话 root；B唯一负责共同版本安装。准确合入/安装/下一局结果以本轮固定 `replies/AUTO-DATA-INTAKE-REPORT-20261025.md` 与B安装报告为准，未取得的结果不由源码授予。

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

首次启用复查：实际Store缓存3,078,893,106字节，超过原2GiB目标。缓存暂不能清到目标时，原维护器的`cache_over_budget`不能饿死小结刷新；现在保留提示、在游戏退出/空间门后按已核数据盘点，不改缓存上限或删raw。零可用N的封存录制保留在页面原因列表，不计入可用局数或补足DEV/TEST缺口；旧手写测试补真实索引本来就有的N字段。只读冻结reconcile也在首次Snapshot读取前加载原cloud环境，让冷payload沿原Store恢复。三处边界修复已纳入上述127项回归。

当前交付顺序为最终组合CI通过→Q普通合入→B从共同develop一次安装启用→用户正常下一局自动更新。安装前由B核游戏进程和GAME-SLOT，Q不竞写同一Workbench profile/Tool。真实下一局不能用现有25组或合成数据代替。45分钟复盘已在23:59统筹时完成：离“录完不用管”还差上述规定门和联合安装/下一局；不扩成Hub、训练或治理任务。

## 历史能力如何处理

| 以前有过 | 当前是否有 | 本轮处理 |
| --- | --- | --- |
| 9月持久来源索引/有界增量构建 | legacy `decision_index` 和 P1准备缓存都在 | 接P1原缓存，缓存局事实，重用原Store字节核验；不搬legacy格式 |
| 整局/同源/已知亲缘分组，永久用途与曝光检查 | `dataset.py`、`p1_curation.py`已支持 | 沿原ledger，不另建账本；DEV/TEST缺口用卡A规则填新组 |
| 封存后外部发现/打包/校验/上传 | 原maintenance和serial importer已支持，但线程已死 | 修ENOSPC故障路径，保留原操作和unknown门，接小结 |
| 固定预览、确认后冻结 | Snapshot/Curation与训练cohort adapter已在 | 一条CLI显示权重/确认/原freeze，训练读同一固定文件 |
| Hub、队列、团队账号/云控制台 | 历史实现保留，10-18决定冻结 | 不恢复，复用本机Workbench与R2 |
| Gold/继承保护/模型血统/质量标注 | 历史Curation部分已有 | 不接入本轮流程，不增加门槛 |

## P卡读取的准确接口

用户10-26补充：P读取Q冻结结果构建训练包；Q把已有证据位置/格式说清，不为P重排计划。可以消除相同内容的重复解析/相同环境的重复验证，但“曾验过”不能直接变成任意接收端字节或训练准入的资格。真实传输仍由原Store校验当前接收字节；原Curation用途/曝光及输入版本检查继续由消费者负责，不授予删校验的泛许可。

| P需要什么 | Q实际提供、位置和绑定编号 |
| --- | --- |
| 冻结交付文件 | 同一profile的 `state_dir/cohorts/<configuration_id>/weight-candidates.json`；正常profile实际根为 `/Users/fire/.local/share/spireagent/workbench/p1-development`。不是旧Codex outputs目录。 |
| 冻结格式 | 沿已有 `training-data-weight-selection-20261025-v1` envelope：`status=frozen_user_confirmed`、`selected_configuration_id`、`configuration`（原 `stpd/p1-cohort-plan-1`）、`snapshot_id`。plan含sources/selection/splits/runs/definition/partitions；TRAIN权重同局一致，DEV/TEST无权重。configuration_id为去掉自身字段后的完整plan canonical SHA256，原 `checked_cohort_plan`复算；Snapshot ID是原Store不可变artifact ID。P/训练CLI用既有`--cohort-plan`与`--configuration-id`读同一个文件；不得读preview代确认。 |
| 原件导入时校验 | 原固定CollectionTool核完整文件清单/字节与Close/原生journal，原 `publish_p1_original`保存archive+transfer等payload及原件逐文件SHA；准确原件ID=`plan.sources[].source_id`，其manifest保存`content_id`、`tool_release_id`、`raw_file_sha256`、Close、run_journal和原始Recorder producer。它保全原件，不证明Human或完整局资格。 |
| 派生时校验 | 原 `derive_p1_recording`从已核archive物化，用准确Tool转换；`publish_p1_projection`核来源content、run/branch、投影schema/profile/revision、全输出SHA及回读标志。准确投影ID=`plan.sources[].projection_id`；该manifest的 `derivation_receipt` payload含source_content_id/tool_release_id/payload_sha256/full_readback/owner_diagnostics，`conversion_summary`保留转换完整性/缺口。projection parameters还绑定`tool_source_revision`与`core_inventory_sha256`。完整性false/unknown不会因字节正确而升级。 |
| R2保全时校验 | 原 `storage preserve`复制原件/投影闭包，远端完整payload读回，archive逐member与transfer清单核长度/SHA；原operation文件 `_storage_receipts[operation_id]`保存artifact_id、peer、storage_action、storage_receipt.full_readback/verified_at，投影收据内含原件source_archive回读。这里是原存储操作UUID，不是另建证据编号；旧历史批处理回执不会伪装成这次维护的新回执。 |
| 盘点时校验 | 原 `inspect_projections`真实InputSpec校验完整公开catalog、唯一chosen、N mask、native event/decision ID、输入投影；合并索引做同源去重/整局及已知亲缘分组。每次从原Curation读用途/训练曝光，填新整局DEV/TEST缺口。最终freeze仍由原Application发布/re-read Snapshot并原ledger保留用途。 |
| P1PreparationCache位置 | 原Curation ledger旁：`ledger.with_name(ledger.stem + '.p1-preparation-cache.sqlite')`。本profile实际为 `/Users/fire/Documents/Codex/2026-10-20/users-fire-desktop-spireagentproject-claude-handoff-2/work/p1-store-readback01/store/.curation.p1-preparation-cache.sqlite`，不是新数据库权威。`projection_cache`每行以投影artifact ID定位，identity绑定`preparation_identity()`（依赖文件/uv.lock哈希）、投影ID和InputSpec，payload_sha256及body SHA独立保存。body是`{index, quality}`，quality新增cohort_facts；命中仍经原Store stream hash/lease读当前payload。P可复用原owner接口，不能导出私有SQLite当跨机可信回执。 |

替换性视图：`state_dir/cohort-preview.json`是未确认plan，`recording-summary.json/html`是最后盘点；它们不保留用途、不记录training use。冻结请求先在固定配置目录记`freeze_requested`，丢回应拒绝重发，`recordings cohort reconcile`仅核原Snapshot/ledger/weighting后收齐原文件。

## B/Q一次安装与Tool批准（用户10-26确认）

由B唯一安装同一Workbench/profile；两边各自CI通过、普通merge develop，后合者先普通并最新develop。B从含两边的准确develop生成Workbench kit；Q不写安装、注册、launcher或原profile。若Q赶不上B03:13 AEST硬限，B先装，Q以后从develop再装，不能互相覆盖。下一局用户正常录制同时验B与Q，未发生前均不冒充验收。

B本身未改P1主转换器，但相对Q旧批准Tool `0464d9e2…`，新固定Tool `5171897f083a5abd77bad879ad7314c2c47fbd3633a19f07eaaa27f1489bf227` 的完整P1 inventory仅admission/reducer及对应d.ts/maps、SDK package版本不同，来自已合PR241的c520d39c/902b2fb4：整包选择/确认映射selector.choose/selector.finish，补bundle形状/完整性，显式排除preview cancel。Q先停止批准并向用户说明，用户明确允许这项PR241差异仅用于新录制、旧25保持原版；现规则优先0464，其次5171，按每来源/run选一个，不重转或覆盖旧25。

新Tool目录为B的 `work/collection-tool-8f4d`（已核固定release/member SHA）；workspace_revision=8f4de36c、Annotator source_revision=ff7bce35。Mod/Tool先固定，Q把准确ID写入Python policy后不重发Tool，最终Workbench kit复用这份字节、由原sourceSetMatches/packager核编译输入身份，避免workspace_revision自引用。任何后续native或转换依赖变化都重新核准确对象；新的转换差异不在此次批准范围。
