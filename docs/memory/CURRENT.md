# 当前工作：第一轮稳定录制与正常数据、模型、GPU闭环

更新于2026-10-21，Brisbane。用户已在两轮完整计划后直接授权现在执行；旧“只规划”及上一轮停止状态不再派发本轮。
Resolve live GitHub refs before relying on this snapshot. 最新用户原话和现场证据 override this file。
唯一当前任务是[本轮目标、owner、原14项和六项完整分母](../plans/p1-prefreeze-2026-10-18/ROUND1_EXECUTION_20261021.zh-CN.md)。

## 当前做到哪里、谁在做

PR206按用户C-2于UTC02:02:22 normal merge入develop作工程集成，非冻结/生产发布。新BASE `8b2c2334e3269cd9899debd20ed392c4d6de6ed6`，树与已审9a9相同，保733782+9a9两父来源。
原9a9 CI39479923386自然成功所选source/test门；merge-head CI39530764717首次观察queued，准确状态需刷新，不借旧green授新能力。
I独占新topic `codex/p1-round1-integration-20261021`、CURRENT/任务/BOM/versions/refs，原9a9树不再改。各writer从新BASE建独立短命topic，稳定已审增量normal merge。
A做P1 cancel/finish首错、r1.5/固定Tool与新七源；G/B先做游戏内P1原服务薄映射和正常Model用例；E做Global旧读新写去重与原Stop无进展；S做正常Global登记/reader/R2和maintenance recent-two；C做真实GPU工程/新数据预检；D/Q随稳定实物原guide与非作者走查。Root只统筹、排资源和最终审计。
用户误暂停后于UTC02:11:31恢复，原05:00:36截止不变。I已normal merge A r1.5语义/忠实测试、G/B游戏内P1原服务薄映射和source-candidate launcher、Source3/4原agent_protocol、C r1.5 InputSpec与保留旧模型原版本、B正常P1 Model/isolated sourcecandidate整支977、D原guide/Skill。首exact-game在SDK通过后遇component docs表示首错，原FAIL保留；原owner修复后root明确另选exact02，已自然PASS：SDK586、native182、Annotator445及13/19 Node检查、统一Mod build零warn/error。A唯一固定Tool release `9a489cd024e601472ac25608a8fb6857c40b4393ae8b2277b9d57281ac2f3ef6`已含同Mod发布。首批候选版本Connector1.3rc26/SDK1.3rc11、Host1.1rc34/Policy0.1rc39、UI0.1rc13/Mod0.2rc48与锁/BOM一致；Python后增量与native原sourceSet匹配，旧I dist和历史身份保持。现在冻结整个I源码/refs至G sourcecandidate Application/GUI工程窗口结束，先验证准确安装/业务；尚未install/load/实际录制，不称用户可录。Human仍由用户亲手，七源r1.5派生在跑，不贴旧计数。E/S后续成熟包不挡当前窗口。

## Remaining Platform non-claims 与时间、资源边界

本轮UTC02:00:36（Brisbane12:00:36）开始；04:30:36（14:30:36）停新功能/长任务，04:40（14:40）候选收口，最迟05:00:36（15:00:36）最终交付，无自动续轮/重试/重启。
本机只有root一个重型slot，用户录制优先；I不自启完整门/游戏/重IO，依赖复用不建多个大venv。游戏内P1路径目标60–90分钟准备准确候选和10–15分钟用户短录，Human由用户亲手，正常保存/Close/下一局与空间未验齐只报准确可录范围。
数据已选六TRAIN/一DEV/TEST空，新r1.5后重算；DEV只早停、亲缘unknown。原M-std FP32配方保持，工程GPU合计≤USD5含构建，正式USD15仅备命令/预检。无正式freeze/train/新真实use；数值/模型改变另交方案。
本轮只授权206这次merge，其他普通topic PR不代批合入/生产。unknown不重投、原件不删，正常App/用户游戏不强杀；已有服务/模型/历史native身份不因source merge自动升级。
原Reference20/Reference产品R20与完整六项仍在两轮计划，Managed延期/Continue延期不复活。正式首训未就绪，真实工程缺口不能概括只等批准；旧失败与旧证据保原身份。
外部root当前入口和固定报告由root同步，本仓只从此CURRENT进入唯一任务。操作前刷新Git/进程/原件，历史任务/私人ACTIVE不重启旧请求。
