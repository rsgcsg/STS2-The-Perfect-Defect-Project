# 录完自动维护与一条命令冻结

日常只需打开原来的 Workbench，照常录制并封存。原 `P1RecordingMaintenance`
发现 Close 后，沿原 importer → Store → 固定 Tool 派生 → R2 保全/完整读回 →
P1 准备缓存 → Curation 盘点更新小结。**不自动冻结，也不启动训练。**

## 看小结

Workbench「本机录制来源」的 **录制数据小结（自动更新）** 打开同一服务的
`/recording-summary`。固定文件位于所用 `project.json` 的 `state_dir` 下：
`recording-summary.html`；本页每15秒刷新，断开服务也可直接打开该文件看最后结果。
页面同时显示更新时间、维护是否启用、卡住原因和排队项；静态文件只代表最后写盘状态。

顶部红框显示停用、失败、原操作未知或盘点失败的原因、持续时间、最近确认的待处理份数，
以及需要谁处理；目录不可核实时，份数会明确标成旧观察。状态超过90秒未更新会提示
无法确认维护器仍在工作；正在处理的步骤采用10分钟提示阈值。单个步骤超过10分钟也会
提示核对，不会因此终止、重发或重试操作。页面每秒更新持续时间，状态仍只来自原
`local-recording-import-operation.json` 的 `_maintenance`；没有第二个监控或通知服务。

盘点失败不会把坏投影静默剔除后生成部分 cohort：旧完整计数及其更新时间保留，红框
说明盘点未更新；新封存录制仍可沿原链入库、派生和保全。未知操作、身份/版本不匹配、
空间不足仍服从原硬门。相同失败输入不无限重算；新输入或维护者显式 `cohort refresh`
才再次盘点。修复原因后使用原入口恢复，不修改或替换出错投影。

- 已进入当前盘点的总局数、A0/A10、胜/负/未知、有效决策步和本次新增步数；
- TRAIN/DEV/TEST 局数，以及 DEV/TEST 各自还缺多少 A0、A10；
- 已封存但排队、没有已批准投影或版本不符的来源；权重表和精确配置 ID。

一份 session 可能含多局；未派生验证的封存份数不会冒充完整局数。
没有可用N的录制仍列出原因，不计作可用局，也不补足DEV/TEST缺口。
胜负只用已记录的终局事实，关闭录制或进程退出不会变成胜负。
这些是观察到的分组；未知种子/祖先不授予统计独立性，模型强弱仍看真游戏评估。

**游戏运行时只观察封存元数据，不启动新的打包、转换、上传、盘点或冷却重步骤。**
游戏退出后自动继续，无需敲命令；如果一直开着游戏，小结会显示「等游戏退出后处理」。
已启动的单个 importer 步骤保留原操作，不能通过杀线程或重发来“暂停”；开启下一次
录制前可从小结确认本次处理完成。此调度门证明派发条件，不声称测过所有游戏场景的零卡顿。

## 冻结一次

在所选最新 develop 或已安装的准确发布版 `python/` 中运行：

```sh
uv run --locked --extra cloud python -m spireagent.workbench recordings cohort freeze --config /ABS/project.json
```

同一条命令先打印每局用途、难度、胜负、决策步、TRAIN 权重和缺口，输入 `yes` 才冻结。
也可在页面查看表后使用 `--confirm 页面显示的完整配置ID`；页面已变则拒绝。
不要用自动取「最新ID」的脚本代替确认。冻结会沿原 Application 的
`p1-data/freeze` 发布 Snapshot 并由原 Curation 保留用途；DEV/TEST不进入TRAIN，
既有用途、已训练曝光和已知亲缘仍由该 ledger 检查。

成功打印精确 Snapshot ID 与固定交付文件：
`state_dir/cohorts/配置ID/weight-candidates.json`。
训练准备（P卡）及训练入口统一读这个文件，通过已有 `--cohort-plan` / `--configuration-id`
绑定同一个 Snapshot；不从小结或另一份手写名单选数据。重复同一成功确认只读原收据。
已冻结 `414fb84d…` 的文件与配置不改，新成员/规则/选定版本形成新ID。

## 一次配置与版本升级

这是维护者步骤，录完每局无需执行。原 Workbench 必须运行，使用其原 profile。
保留当前保存 peer 的命令（已有 peer 指向 R2 时不改 prefix）：

```sh
uv run --locked --extra cloud python -m spireagent.workbench recordings maintenance enable --config /ABS/project.json --from-now --origin declared_human --no-cool-recent --cohort-policy /ABS/source/python/configs/p1-cohort-policy.json
```

新 profile 尚无 peer 时，按 [原 Store/R2 指南](DATA_LIFECYCLE.md#daily-p1-store-and-r2-operations)
显式配置其已授权的 peer；不要把别的 profile、整根历史原件或猜测的 R2 prefix 混进来。
`--from-now` 固定启用时的 Close 时间，不上传范围外旧数据。维护策略在原 importer
operation 文件保存，Workbench 正常重启后续接；启动/封存仍归 Recorder。

版本、投影、切分盐、DEV/TEST目标唯一在 `python/configs/p1-cohort-policy.json`。
按来源/原生 run 选择一个已批准版本，优先级明确；已知淘汰版本只展示原因、不并入N。
未列出的新 Tool（包括新版本数据）明确停止，并显示完整 release ID。
升级前核 Tool 的发布/兼容证据，普通 PR 更新这一个文件；不要改25份投影名单，
也不要仅因「版本更新了」自动批准。注册Tool必须匹配实际安装，不能放宽身份检查。

本轮用户已允许PR241卡包转换差异用于新录制，批准固定Tool `5171897f…`；
规则将原 `0464d9e2…` 排在它之前，旧25来源继续选原投影，新来源才使用新Tool。
最终安装由B从含B/Q两边的develop一次完成；原native Mod/Tool固定后不因Python批准清单
变化重发Tool，以原打包器核身份。具体完整ID及变更核对见
[Q任务的B/Q安装段](../../docs/plans/p1-prefreeze-2026-10-18/AUTO_DATA_INTAKE_20261025.zh-CN.md#bq一次安装与tool批准用户10-26确认)。

## Q2 状态区与维护者恢复

页面顶部显示自动维护状态、状态核对与计数更新时间。失败、停用、阻塞会醒目提示；
空闲状态超过90秒（进行中超过10分钟）未更新会提示不能确认仍在工作；
步骤超过10分钟提示核对原操作。
旧静态文件只代表最后写盘结果，不能据它确认服务在线。

看到告警由维护者先核 `maintenance status`、原 Workbench 日志及原 importer 操作。
只有原操作已完成、没有 pending/unknown、身份/哈希及空间检查通过，并已处理停用原因，
才能沿原入口恢复；保持 peer、scope.closed_after、origin、cool_recent 和 cohort 策略，
不要重新使用 `--from-now` 改掉原范围：

```sh
uv run --locked --extra cloud python -m spireagent.workbench recordings maintenance enable --config /ABS/project.json
```

结果未知时先只读核原请求，不重发。身份/版本不符、空间不足、原操作 pending/unknown
或索引未确认时不能直接恢复；盘点失败保留最后完整计数，修因后用原 `cohort refresh`。

Q3 安装使用 Annotator29 固定 Tool `aa168b7e…`，由统一 Mod 提供未来开局进度原件；
旧进度未知不补填。新 Tool 的SDK15新增显式 r1.6 支持；当前转换仍用 r1.5，另核验可选进度附件，
固定版本检查仍有效，旧 Tool 优先级、切分盐、目标与已冻结配置不变。
已完整完成的同一原件在Tool升级后，只沿原封存哈希/身份与原完成记录识别新候选别名，
不重复派生旧局、不重写原入库耗时。改peer/范围/投影会清完成记录，不沿旧完成跳过。
Q小结还依赖锁定的 `local-models` extra（tokenizers）；collection-only kit初始化后，
维护者须在Workbench停止时沿原环境入口补齐：
`uv sync --project /ABS/release/source/python --locked --extra cloud --extra local-models`。
此项不下载模型权重、不训练，也不选择Managed/评估Runtime。
真人自动验收仍等用户正常下一局，完成事实只记在 Q3 报告。

X2 使用 Annotator30 固定 Tool `5afc7d7688d5ac221937cc033ea56a97f059929143b3d2a7d85885f6f56709ba`，只向新封存原件附加真实开局种子、修正完整账号/profile路径后的原样进度；不进玩家快照、P1或模型。缺失仍unknown，旧原件不补填。该Tool仅追加在原优先级末尾，继续沿同原件候选别名避免重复派生，414冻结与用途不变。安装/隔离工程重建及下一局正常录制的验收分别见 X2 报告，不继承旧Human资格。

## 出错与恢复

```sh
uv run --locked --extra cloud python -m spireagent.workbench recordings maintenance status --config /ABS/project.json
uv run --locked --extra cloud python -m spireagent.workbench recordings cohort refresh --config /ABS/project.json
```

`refresh` 只让原维护器下次重算视图，仍服从游戏退出、空间和原操作状态；不重试导入。
原 `python/tools/p1_cohort.py --config /ABS/project.json --output /ABS/new-report` 可从
仓库默认规则导出一次盘点，不再依赖旧Codex目录；`--selection`保留为历史精确复算入口。
导出同样使用原Store/Curation和校验缓存，不保留用途。

| 页面/命令结果 | 下一步 |
| --- | --- |
| 等游戏退出 | 正常退出游戏，原服务自动继续；无需关Workbench或重新封存 |
| 空闲不足15GiB | 停止派发，按已批准清理/容量流程处理；不能扩大删除范围 |
| 缓存暂超上限 | 原缓存策略继续保全可恢复性，盘点仍更新；不据此删旧raw或扩大缓存目标 |
| 盘点失败、下方是旧计数 | 看原日志中的投影ID、原始异常类型/信息/堆栈；修因后显式 `cohort refresh`。入库保全仍可继续，旧计数不是当前完整结果 |
| 状态太久没更新 / 当前步骤超过10分钟 | 核原Workbench及原操作；提示阈值不等于失败结论，不重发pending/unknown，不为提示而杀服务 |
| `disk_full` / `ENOSPC` | 查看磁盘和原服务日志；失败状态保留在内存/API，空间恢复后只补存状态。修因后显式enable，不重启求绿 |
| `installed_artifact_mismatch` / 工具不可用 | 核真实Mod/Tool组合，经原注册入口修复；不跳过匹配 |
| `unapproved_tool_release:…` | 核这个准确版本，再由维护者更新唯一版本规则；未批准前不派生/混入 |
| 原操作pending/unknown/索引未确认 | 先按 [原录制流程](B_PIPELINE_HANDOFF.md#p1-originals-and-named-projections-in-the-existing-local-store) 使用对应reconcile，只读核原结果；不重复写入 |
| 冻结请求结果未知 | 原文件停留`freeze_requested`，再次freeze拒绝；核原Snapshot及用途后，以`cohort reconcile --confirm 原配置ID --snapshot-id 原SnapshotID`只读收齐，不重新publish/reserve |

持久准备缓存位于原Curation ledger旁，绑定投影/输入/实现身份；命中仍由Store读字节、核SHA和lease。
旧数据只免重复解析，分组与历史用途每次从原ledger读取；缓存损坏按原机制重验。
这份缓存、preview和小结都可重建，不是第二个Store、录制账本或用途权威。

## A10 进度权重候选（W1）

在现有小结对应的同一批数据上，`--weight-rule a10-progress-v1` 选择新版；省略时
仍是原规则。先将 `freeze` 换成 `preview` 查看，再核对完整配置 ID 和每局表：

```sh
uv run --locked --extra cloud python -m spireagent.workbench recordings cohort preview --config /ABS/project.json --weight-rule a10-progress-v1
uv run --locked --extra cloud python -m spireagent.workbench recordings cohort freeze --config /ABS/project.json --weight-rule a10-progress-v1
```

新版冻结必须在该次显示的表后输入 `yes`；小结更新后配置 ID 会改变，应重新审表。
规则在原整局权重上：A0 不变；A10 非胜乘 `1 + min(层数/同难度终点层数, 1)`，
结果最高 3；A10 胜乘 2（权重 6）。未知仍按非胜进度计算并单列，绝不认作负。
DEV/TEST 不加权，原用途不变；版本、参数与各局权重一起参与配置 ID。旧 414 及其冻结
文件不改。A1–A9 目前不在原盘点的准入范围，不为假设数据添加规则。
`--confirm` 保留原规则的既有行为；新版须交互输入 yes。若缺口未满足或其他数据准入条件
失败，按原 Curation 处理，权重预览不代表获得训练资格。
