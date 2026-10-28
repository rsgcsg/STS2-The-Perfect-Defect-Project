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
- 数据池局数和可用步数；新默认不预分 TRAIN/DEV/TEST，也不先补考试集；
- 已封存但排队、没有已批准投影或版本不符的来源；权重表和精确配置 ID。

一份 session 可能含多局；未派生验证的封存份数不会冒充完整局数。
没有可用N的录制仍列出原因，不计作可用局，也不补足DEV/TEST缺口。
胜负只用已记录的终局事实，关闭录制或进程退出不会变成胜负。
这些是观察到的分组；未知种子/祖先不授予统计独立性，模型强弱仍看真游戏评估。

**游戏运行时只观察封存元数据，不启动新的打包、转换、上传、盘点或冷却重步骤。**
游戏退出后自动继续，无需敲命令；如果一直开着游戏，小结会显示「等游戏退出后处理」。
每次新步骤（包括盘点签名后的正式盘点）开始前重新读取原游戏 owner，不能沿用本轮早先的退出观察。
已启动的单步没有安全暂停入口，保留原操作跑完；小结在线页与固定文件显示「正在整理缓存」，
下一轮仍在运行游戏就不开始新步骤。缓存整理完成后原 operation/maintenance 记录本次耗时、
线程 CPU 秒和回收缓存字节；回收量不是磁盘读取量。此调度观察不是游戏启动互锁，不承诺零卡顿。

只读查询使用同一 Application 的短等待状态投影，不执行盘点或读取 Store：

```sh
uv run --locked --extra cloud python -m spireagent.workbench recordings maintenance status --config /ABS/project.json
```

锁忙超过 50 毫秒时返回 `status=busy`、大白话「在忙，稍后再查」，不当作 unknown 或停用；
CLI 读取传输超时也明确显示 busy。控制操作遇 busy 不发送修改，用户稍后显式再操作；
已发送修改的超时仍按原流程核原结果，绝不自动重投。小结每15秒刷新，静态页只代表最后写盘状态。

## 每次训练选局、审权重、冻结一次

录后默认只更新池 `cohort-preview.json`（`stpd/p1-cohort-pool-1`），不申请用途。
原始录制、选定版本的 P1 轨迹照常保存在 Store/R2。每次实验单独确定模型阶段 ID、
父模型阶段、整局成员、DEV/TEST 比例或明确的整局角色；同一局可以进入多个实验的训练。
池变化后已有草稿不自动增补成员。旧 profile 的旧策略仍能读取；要使用新默认，维护者
按下一节原升级步骤选含新版的源码/发布与策略，不把普通合并冒称已安装。

先创建本次请求 JSON，例如从头训练的 T2 草稿（80/10/10 为本次建议，可改）：

```json
{"model_id":"t2-scratch-20261027","parent_model_id":null,
 "split_salt":"t2-draft-20261027-v1","dev_fraction":0.1,"test_fraction":0.1}
```

比例按整局分组数量四舍五入，不按步数/输赢挑选；可用 `selected_group_ids` 选择池内
整组子集，用 `split_by_group` 指定该子集的完整用途。DEV 至少一组、TRAIN 至少一组；
TEST 可为零。比例不适合当前池或未练过的考试局不够时明确拒绝，不偷偷改比例。

在最新 develop 的 `python/` 运行：

```sh
uv run --locked --extra cloud python -m spireagent.workbench recordings cohort preview --config /ABS/project.json --plan-file /ABS/pool/cohort-preview.json --experiment /ABS/t2-request.json --weight-rule a10-progress-v1 --output /ABS/t2-draft.json
uv run --locked --extra cloud python -m spireagent.workbench recordings cohort freeze --config /ABS/project.json --plan-file /ABS/t2-draft.json
```

第一条只产草稿并展示整局表；第二条才请求冻结。Application须运行含新版experiment接线的
版本；仅换CLI而服务仍旧时会明确拒绝，按原升级SOP处理，不重试未知请求。旧414已批
原公式的完整定义可直接复用。新权重定义从未批准或改变时显示表并问
`yes`，批准 ID 存在原 Curation SQLite 内；成员、分配、模型阶段变了而规则定义不变，
明确运行 freeze 即沿用批准，不反复问 yes。preview 永远不记批准，也不冻结。
可给 freeze 传 `--confirm 草稿完整ID` 额外防止拿错表；它不能替代新规则首次 yes。
本次 W1 只交草稿，不为真实规则登记批准或冻结。

冻结仍走原 Application `p1-data/freeze`、Snapshot 和 Curation；每次另生成内容身份，
在同一本台账记录模型阶段/父阶段、TRAIN/DEV/TEST 局、各局权重与完整权重定义。
冻结是计划用途，实际 prepare 记录使用后才计作训练曝光；构造词表本身已属于使用。
成功后同一路径 `state_dir/cohorts/配置ID/weight-candidates.json` 交给训练原有
`--cohort-plan` / `--configuration-id`；配置与成员都绑定该 Snapshot。
未知投递不重试，按下方原 reconcile 核原请求。

模型阶段 ID 每次独立训练/接着练用新 ID；`parent_model_id=null` 只用于真正从头训练。
接着练填写已登记父阶段，考试局不得和本阶段 TRAIN 或任一祖先的实际训练局/已知亲缘
重叠；不同新血统可以重新洗牌。旧权重/检查点不会因填写 null 自动成为从头训练。
旧模型先以原已冻结收据、Snapshot 和已记录训练 operation 绑定历史（不重冻）：

```sh
uv run --locked --extra cloud python -m spireagent.workbench recordings cohort adopt-model --config /ABS/project.json --plan-file /ABS/old/weight-candidates.json --snapshot-id OLD_SNAPSHOT --training-reference OLD_OPERATION_32HEX --model-id OLD_MODEL_STAGE
```

有父阶段时另加 `--parent-model-id`；缺历史拒绝，不猜“没练过”。该入口只绑定已核实的
旧使用。当前训练实现的 `initial_checkpoint` 仍只支持原 Snapshot/config 的历史派生，
**跨新数据集续训尚未实现**；本卡已支持续训分配草稿和隔离台账，不改 T1 的训练实现。
未知种子/祖先仍不授予统计独立性；台账只对已记录的本项目使用和已知亲缘负责。
暂不新增所有血统禁用的公共考试集：池小，当前硬要求可由血统隔离满足；以后若需要
跨模型固定公开评分，再单独确定考试集及曝光规则。

升级时仅将台账中已存在的 training Snapshot 记为历史；新冻结必须有模型实验信息，
不能省略 experiment 绕过批准。历史快照的新版权重会被拒绝，原进度权重仍按原事实核值。
旧已冻结 `414fb84d…` 的成员、权重和文件不改，旧入口可读。它不是新实验固定标签。
新血统训练旧考试局不会污染旧血统；旧模型历史不完整时不能静默给后代考试资格。

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

版本、投影和录后盘点模式唯一在 `python/configs/p1-cohort-policy.json`。
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

I1 日常安装使用 Annotator31 / Mod67 固定 Tool `b9591809c779d05a7adf5c12e7f47ace0a68ba7769f50da94c77036fd2b8c4d4`（源码 bbcad621），新封存原件附带 r1.7 锁镰/永续糖果同次见证；见证只进原件元数据，不进玩家快照或模型。Q 的投影仍是 r1.5。该 Tool 只追加在原优先级末尾，旧原件沿同原件候选别名不重复派生，冻结与用途不变。安装与第一局验收见 I1 报告，不继承旧 Human 资格。

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

## 每次实验的整局权重

`--weight-rule original` 沿用旧规则；`a10-progress-v1` 为新版。A0 不变；A10 在原
整局权重上乘 `1 + min(层数/同难度终点层数, 1)`，非胜最高 3，胜局乘 2 得 6。
未知按非胜进度计算并单列，不变成负。DEV/TEST 始终不加权；版本和参数参与配置 ID。
最高系数 3 只作本轮对照，不是已选配置。A1–A9 目前没有原准入支持，不提前加入。
重算只生成新配置，不改已保存的数据。旧无 experiment 的历史草稿保留原确认语义。
