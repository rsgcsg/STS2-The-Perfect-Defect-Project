# 本轮：P1 录制与转换真实缺口修复

root 唯一写入者；用户要求修复三份录制/转换缺口及旧 Skip 投影，并检查以前修复的同类问题。
基线 `origin/develop=8910656134b3a4cc4872a3fd387f926cff68dccf`；topic
`codex/p1-recording-data-repair-20261022`。链路位置为唯一图⑤原生采集→⑥不可变原件到P1→⑦数据消费者，
属于原 owner 的表示/采集实现修复；风险为 G3 原生接缝及 G2 转换消费者，不改变原生规则或上层语义。

交付：修正实际失效的父级关联、完成/取消及映射实现；按实际安装区分已修行为与残留缺口；
用当前真实原件重新转换并验证，旧原件/固定投影保持不可变，新投影带新 Tool 身份；
检查历史同类入口与正常 reader、应用消费者，交准确 PR 和运行/数据分层收据。

验收先用三份问题原件及已知 LastingCandy/Skip 原件复现，再加同机制反例，原 owner 定向检查、
真实 converter→Store→reader、稳定候选所选门、exact-game 构建及非作者复核。
缺失原事实仍留未知，不无据补 next，不将取消或浏览造成功标签；完整 Human 资格不由离线或构建授予。

本轮不训练、不改用途/划分、不付费、不删除资产。PR merge 及未获授权的日常安装在具体候选可审后交用户决定；
正常游戏/应用不因工程收口被停止。首个调查/实现检查点约30分钟，遇新事实继续本批必要修复，不自动扩成全机制重写。
证据写在 `/Users/fire/Documents/Codex/2026-10-22/p1-recording-data-repair/evidence`，原计数快照保在相邻 `current-data-chain-audit`。

当前：原生 carrier 清理和商店 generic selector 父级已在 owner 修正；转换器用明确子决策收据识别
预览后 Smith/移除取消，并按完整原件相等识别战斗牌堆/普通 grid 选中撤回，不造成功标签。
Evidence 修正 targets 回执误读不存在字段的问题，读取 converter 的 `complete` 布尔值。
旧 `a267912d` 跨幕修复实际已在日常安装；本次残留是最终胜利无 ActEntered、长活 RunManager
绑定未随会话清理，不是旧修复根本没装。LastingCandy/Skip 的 SDK rc.12 仍属已合源码、未装日常 Tool。

产品固定候选为 `4b1ebf516da0227ceafb1db447eb6c9a45654944`；其后只更新本任务与CURRENT文档。
Annotator rc.26、Evidence rc.33、Game Mod rc.59及Python实际依赖/组合声明和BOM已同步；
非作者复核接纳主实现，并独立重现三份原件。190项转换/原生接线、35项Store/consumer、
`check:exact-game`（含450 Core及准确v0.111.0统一Mod构建）、另行74项SourceNative测试通过。
完整本地门保留两次失败：先补版本常量/标准依赖环境；第二次4866 passed/9 failed/6 skipped，
9项均因漏同步发布组合Evidence pin，单字段修正后相关12项消费回归通过，其他组件门全部通过。
没有将上述完整门改称通过；最终head完整CI由本批develop PR执行，结论以PR实际回执为准。

固定Tool为 `0464d9e206a3d234678dafeba4e17380afde7d1b36ee4a5eb26ea1701333f340`，
producer为4b1ebf51。沿现有recordings derive→Store→storage preserve→R2完整投影/原件archive回读，
完成此前审计固定的25个declared-human来源分支；正式`inspect_projections`消费完整InputSpec/目录/唯一chosen，
复算10319 N、10183 N with next、9885 N with end、136 N without next、0别名重复。
25个observed groups独立性均未知，15份source conversion complete；这些不是25个独立游戏或研究资格。
旧30个投影payload hash及25个原始manifest均未改变。

| 固定范围 | 旧N / 合格next | 新N / 合格next | 剩余原件事实 |
|---|---:|---:|---|
| 新增八份 | 3118 / 3102 | 3116 / 3113 | 3个N缺next；不补造 |
| 120025 | 414 / 406 | 413 / 411 | 6项缺失附魔父级事件，形成2个N缺next |
| 121856 | 162 / 161 | 162 / 162 | Smith预览后取消现可证明，0阻塞 |
| 132000 | 868 / 861 | 867 / 866 | 既有carrier失败与unknown两项，1个N缺next |
| 已知Skip来源032233 | 337 / 326 | 334 / 334 | 三个物理Skip退回零标签；实际取牌换到完整父意图 |

三份问题来源的新发布N事实及mask逐条等于原件复验；Skip来源全部334个N事实亦同。
两个早期来源还分别恢复38与294个next，属于先前修复随新Tool应用到旧数据的收益。
本批整份旧25来源的合格next为9832，新为10183；不能只报告新增八份的3处剩余而遗漏全库136处。

唯一数据选择入口在证据目录的`repaired-projection-ids.json`，或现有prepare API的
`repaired-source-filters.json`（declared_human及准确Tool release）。不要将旧别名混入此清单。
关键收据为`rederivation-plan.json`、`batch-receipts/`、`repaired-selection-reader.json`、
`published-skip-case-comparison.json`与`candidate-validation.json`；详细失败和原始日志同目录保留。
共享交接为 `/Users/fire/Desktop/SpireAgentProject/claude-handoff-2026-10-13/replies/P1-RECORDING-DATA-REPAIR-20261022.md`。

交付到本分支PR审查/完整CI及逐PR批准；不自动merge或日常安装。原生新行为仍待批准安装后的
准确运行身份与有界Human验证，不把本地构建/离线转换提升为实际运行资格。
未训练、未reserve/use/freeze、未改用途或划分、未付费、未清理资产或控制游戏；正常应用未重启。
