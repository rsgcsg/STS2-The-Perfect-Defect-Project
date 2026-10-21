# 第一轮执行：稳定录制与正常数据、模型、GPU闭环

本轮已收口，整体首训baseline未通过。最后准确源码69c4555f/tree4d6ca357原12阶段完整门失败，Python原摘要：== 5 failed, 4699 passed, 6 skipped, 93 subtests passed in 823.70s (0:13:43) ===。本次只收口G0状态文档，组件源码、合同、版本和artifact身份不变，不第二FULL；新文档head不能替代该源门身份，最终head CI须独立读取。 原门Python4699通过/5失败/6跳过/93子测试；pytest失败后的CPU e2e/cloud smoke/uv build未执行。随后只正常合B d382与C82e最小guard/忠实fixture修复，准确4021ccdb上唯一7项targeted通过17.20秒；kit同原sanitized uv一次诊断为ModuleNotFoundError: spireagent，selected-source自有.venv未bootstrap，不安装/绕边界求绿。最新组合未重跑完整门，原FAIL保持。 七源、Human、旧Global/R2和离线字节往返等实物通过，GPU完整恢复/Model/CPU导出、真实热集和Reference20/R20未齐。最新用户延长后06:00UTC（16:00Brisbane）硬止，预算不扩，无自动续轮。原14项和完整六项最终判定见下表。

## 本轮目标、授权和准确起点

一套上层链路保持原[职责与连接要求](CHAIN_PURPOSES.md)：原生游戏→公开环境/P1→完整Agent→受控Runtime；独立记录→不可变原件→固定Tool投影→唯一Store/Curation→研究输入、训练、模型、评估。GUI是同一Application用例的薄映射，默认以游戏内便捷操作为主；不另建录制、训练、证据或用途权威。

仓库`rsgcsg/STS2-The-Perfect-Defect-Project`，新BASE=`8b2c2334e3269cd9899debd20ed392c4d6de6ed6`（origin/develop），tree=`b6bea892b522d0b69852012d34c820357319e65b`。PR206于UTC02:02:22以normal merge合入，父为旧develop733782b1和已审9a9ee1f4，树与9a9完全相同。原准确head CI39479923386自然成功所选source/test门；合并head自动CI39530764717首次观察queued，不能提前称fresh green。旧失败和限定native前缀保原身份，不由merge授冻结、整局、Human、GPU或科学资格。

I独占`work/p1-round1-integration-20261021`、topic`codex/p1-round1-integration-20261021`、CURRENT、此唯一任务、BOM/versions和集成refs。每位writer从新BASE建独立短命topic；normal merge保组件path-scoped来源，无force、无direct-push main/develop，不改其他writer分支。除PR206这次具名工程merge，本轮只交普通topic PR，不代批新PR merge/正式freeze/train/prod。

采用转发prompt的已定选项：六源TRAIN、d74dc5f6/9e64c1d3源DEV、TEST空，r1.5原reader已只读重算3269/446；DEV仅早停、亲缘unknown不作泛化。原M-std FP32、完整目录N、batch64、AdamW3e-4/wd0.01、5%warmup/cosine、最多30epoch/dev patience5及原best/last/checkpoint安排保持。工程GPU合计≤USD5含构建；正式Modal L4上限USD15仅准备命令/预检，本轮不正式开训、不写新真实use。奖励直接连续、R2长期主存、Modal优先/学校后、Continue延期保原决定；数值/模型方案若变先交用户，不用提速偷改配方。

## 绝对时间、资源和停止点

| 检查点 UTC / Brisbane | 必须交付或停止的动作 |
|---|---|
| 02:00:36 / 12:00:36 | 开始，刷新refs、原writer、进程、环境和空间 |
| 02:15:36 / 12:15:36 | I交准确BASE/分支/唯一入口；各owner交第一错误事实、原consumer、短路复用和停止边界 |
| 约02:45–03:00 / 12:45–13:00 | 第一稳定增量/消费者结果；GPU报账户/镜像/Store真实进展，录制路径报窗口条件 |
| 03:30:36 / 13:30:36 | root按实测判断剩余关键路径，不到截止才说赶不上 |
| 05:30 / 15:30 | 停止新增功能和新长任务，只完成已有增量的必要收尾 |
| 05:40 / 15:40 | 候选收口，准确source/artifact/原件/失败/未完交审 |
| 最迟06:00 / 16:00 | 强制最终交付，无自动续轮/重试/重启/无限CI等待 |

本机全门、native游戏和大数据打包/读回共用root唯一重型slot，用户录制优先。轻量代码/文档与云计算可并行，但不得共写文件或共享安装/环境。依赖复用，不重复建大venv/临时全集；唯一私env由I维护并明确source与真实installed Evidence来源。完整门只在最后稳定组合由root明确选一次；失败保原件，改变后是否再选由实际首错和root决定，不自动retry或弱化断言。

真实Human短录须用户亲手操作且由原窗口owner先准备准确安装/rollback；不代玩、不在录制时跑重检查。正常Application、用户游戏和已授权独立服务不强杀；到点如有作业运行，给准确handle/owner/成本/截止和原Stop/恢复办法。unknown不重投。原件/唯一冷档/R2云对象不删；冷却仅正常maintenance具名已验可恢复、无active consumer的展开副本。

## 独立包、requires/provides与当前状态

| owner/写范围 | 上游提供→下游需要；本轮产物 | 验收、停止与当前状态 |
|---|---|---|
| A 环境语义/Tool | 原selector surface事实→SDK准确动作与finish_available→固定r1.5 Tool→七源投影 | r1.5首错和七源正常reader通过3715N/3357next；真实incident准确拒绝，不补造前缀。 |
| G 原生GUI，B Application协同 | 游戏内既有控件→同一Application/TaskBridge/RecordingApplicationService→原RecorderRuntime | 工程生命周期和真人短录/rc14三页已验；rc15/Mod50准确build，最后表示修后未重验实屏，原路径已回滚。 |
| E Runtime/Evidence | 原完整capture/source/Agent/Receipt→去重表示和原预算Stop→同包replay/typed消费者 | 11399原文件全byteexact往返和原typed通过，仅离线表示诊断；新格式真实Runtime/整局未验。 |
| B Application/模型用例 | 既有Model/InputSpec/Runtime安装pin→原model/live registration/start/status/stop/证据查询 | 正常Model/控制source集成及status原HTTP争锁FAIL→9PASS；不授GPU失败因果或新Model实局。 |
| S Store/maintenance | trusted runtime expected+密封Global→原typed verifier→正常Manifest/reader→copy_artifact/R2；原保存→recent-two冷却/恢复 | 七源、Human、旧Global与父闭包正常本地/R2通过；热集首Catalog报根不可用，原因未定，未cool/restore/delete。 |
| C 计算/研究数据 | 同Application/Scheduler/worker+compute Store→真实L4工程恢复/Model；新七源→固定数据/词表/InputSpec预检 | 六TRAIN3269/DEV446/TEST0只读预检；真实GPU额度三次用尽，step1已验，完整恢复/Model/CPU导出未验，账单未知。 |
| D 原指南/接手 | 稳定源码/接口和原历史解法→原guide/SOP→非作者正常入口操作 | 九类SOP/104行和15现行caller维护；一个真实非作者reader通过，全部操作未整体验收。 |
| Q 有界独立复核 | 作者diff/原始结果→公共语义、证据/预算、GPU/consumer的非作者判断 | 已受理最终稳定源及原件的注明范围，不能替root终审或更高native/Human/科学资格。 |
| I 集成 | 已审稳定topic→normal merge→共享身份/原指南/CURRENT/唯一任务→普通topic PR/准确CI | 全部稳定topic normal merge；最后原完整门失败并保准确69c身份，仅一次G0文档收口，普通PR不代merge。 |
| root 统筹/终审 | 直接用户意图+实际实物→依赖、资源、范围、最终14/6判定 | 原14/六项最终按实物部分收口，首训未就绪；不自动续轮/新费用/正式冻结或真实训练。 |

真实依赖只有：A语义→固定Tool→S正常发布→C新快照；G/B原件生产与保存→用户短录；E新格式→S新读（旧T0b桥可先做）；最终native tuple→原生/Reference验收。GPU、Application、旧证据回库、热集、原SOP从开始并行。新录制先入候选，不自动增入首训快照。

## 原14项完整分母与实际判定

| 原14项 | 实际判定 | 已验证实物与下一边界 |
|---|---|---|
| 1 奖励连续 | 当前七源通过 | 原批准直接连续规则保留，r1.5正常Store/R2和语义reader七源3715N/3357next；cancel误标签两点排除。真实缺口与unknown不掩盖。 |
| 2 首错、数值与检查 | 部分 | A实际取消首错、E全字节往返、B正常HTTP争锁先FAIL后修9PASS；同机CPU初始化/输入/分数及GPUstep1原payload相同。完整GPU恢复未通过，最后源码门见本文件结果段。 |
| 3 异常恢复 | 已验证准确拒绝，恢复能力仍有边界 | 真实非空事故副本5accepted/5terminal但缺后继原件，原journal已关闭且无CloseReceipt；当前Tool准确incident拒绝pack，不补造可恢复前缀。两空件正常恢复是N0。 |
| 4 日常录制 | 部分 | 用户亲手短录→Close→原Tool→正常入库→本地及R2按ID原typed读回，686accepted/686terminal/0issue。只证明本段；非无限录制/所有surface。新GUIrc14三页实屏，rc15资料修正仅源码/准确build。 |
| 5 训练控制 | 部分 | 原Application/Scheduler合成GPU真实暂停到step1完整checkpoint；第三次同Run恢复遇本地观察失败，按预算取消并正常reconcile。完整Model/Result未验，不称恢复成功；非新增live-pause。 |
| 6 全部候选 | 部分 | 当前七源完整ID/重复/parent/reader均验；新短录另存，不自动入首训。七结构组亲缘全unknown，已证独立组0，七源不代表全机全部合格人口。 |
| 7 用途与学习问题 | 工程预检通过，研究资格未授 | 已选(b)六TRAIN3269/DEV446/TEST0只读重算；DEV非独立、不能作泛化。旧T0曝光原账仍0claims/1use/1sourceuse；没有本轮真实新fit/use/梯度。 |
| 8 R2保存与空间周转 | 部分 | 七源、新短录、旧Global及完整Model/可信Runtime父闭包正常发布和按ID本地/R2全读通过；真实recent-two热集首Catalog报root不可用，未冷却/恢复/删除。 |
| 9 GPU与提速 | 未达完整工程目标 | 原L4/不可变镜像/Secret/Store实际连通，三次额度用完。暂停step1payload完整且同旧GPU；完整baseline、resume Model/Result、CPU导出和step3前缀未验；最终账单未知，不能据额度估算冒已核费用。 |
| 10 全项目收敛 | 部分 | 原九类SOP、当前路由、历史104行/208路径及15当前caller差额已核，新增代码与原指南同PR；六项细判如下。 |
| 11 稳定组合与运行 | 部分 | 最后准确rc15/Modrc50原exact-game通过；最后源码门见本文件；PR/CI以实时GitHub为准。Reference20/产品R20第二轮未跑；Managed严格/40延期保持。 |
| 12 首训与评估具体方案 | 已准备，实测依据不足 | 保原FP32/M-std、完整目录N、batch64及原配方、用途/版本/预算/恢复点；不能用几步合成GPU外推真实30epoch耗时。 |
| 13 正式冻结与首训 | 未就绪、未执行 | GPU完整闭环、热集及两轮required工程未齐；无正式冻结或真实首训提交，具名批准仍属用户。 |
| 14 模型实用、比较与回库 | 部分 | 正常Model注册/安装/原控制接口source与集成测试、旧T0 Global真实回库已通。旧899输入/900dispatch/393循环仅历史技术停止前缀；新模型比较/整局/新格式真实Runtime未验。 |

## 六项全项目验收与操作覆盖

六项保完整含义，表中通过仅限注明事实，不把作者停止或单个reader通过当全项目完成。

| 六项全项目标准 | 判定 | 依据与真实剩余 |
|---|---|---|
| 职责、上层逻辑清楚 | 部分 | 本次变更均归原owner和requires/provides，图与组件说明一致；全项目每条旧能力的当前实现/替代关系未被本次有限核查穷尽。 |
| 唯一当前导航 | 通过 | README→CURRENT→唯一ROUND1；原DOCUMENT_MAP连接各原指南。外部START只路由，不另立任务。 |
| 全部实际操作SOP | 部分 | 原九类条件/输入/入口/结果/成功失败/恢复说明已维护；GPU恢复和日常热集等实际操作尚未走通，不能用文档写齐替验收。 |
| 历史可直接复用 | 部分 | 复用成熟Recorder、Tool、Store/transfer、Dataset/Curation、Scheduler/worker、旧Runtime包及事故原件；104行路径和15当前caller均有实物，未把所有历史当已迁移/当前合格。 |
| 非作者按指南走通 | 部分 | D真实31MB正常CLI→当前dataset reader/inspect通过，Q独立原件复核；不能外推Model/Global/冷热/全部九类已由新人实操通过。 |
| 代码、合同、入口、指南同PR维护 | 通过（PR/CI以实时GitHub为准） | 同一普通topic使用normal merges保组件来源；原指南、版本/BOM、pin、CURRENT同增量维护，无第二SOP/用途账。 |

## 真实证据层、最后源码门与回滚

最后准确源码69c4555f/tree4d6ca357原12阶段完整门失败，Python原摘要：== 5 failed, 4699 passed, 6 skipped, 93 subtests passed in 823.70s (0:13:43) ===。本次只收口G0状态文档，组件源码、合同、版本和artifact身份不变，不第二FULL；新文档head不能替代该源门身份，最终head CI须独立读取。 原门Python4699通过/5失败/6跳过/93子测试；pytest失败后的CPU e2e/cloud smoke/uv build未执行。随后只正常合B d382与C82e最小guard/忠实fixture修复，准确4021ccdb上唯一7项targeted通过17.20秒；kit同原sanitized uv一次诊断为ModuleNotFoundError: spireagent，selected-source自有.venv未bootstrap，不安装/绕边界求绿。最新组合未重跑完整门，原FAIL保持。

最后完整门准确源为69c4555f53146eef4f2f66817604ecb8ce7ee881/tree4d6ca35730905e30a37cfc0957b527607ab7a984，原阶段及XML/日志保留。原XML实际testcase4710、skip6、failure5、error0；suite tests属性4803包含子测试，不混同独立testcase。条件跳过不算通过。当前G0文档以组件identity前后语义字段相同复核，不重写源门身份。

本轮真正不同的native tuple分别保留：c930 Mod48/Tool9a用于用户短录和七源；fc8 Mod49用于用户实际呈现三页；最后69c Mod50 SHA e5d34e77b4b408972acf91204cd4cf6c575d4a32ab4f88d87d1a44e61dd66c42、MVID3ca7501a-6a87-42e8-a85b-371e57130800与Tool304a209a只完成准确build/候选发布，G437修后未重新load或看实屏。没有新gameplay/Human/正式训练或研究资格。

Evidence32正常VCS安装的canonical source20989e51、uv.lock b0e7f0ce、原RECORD和31源文件真实验证，旧doctor/Node缺依赖/组件文档错误等原失败保留，不重标通过。B43fd仅解决原本地status争锁反例，不授具体GPU失败因果。原目录存在但Catalog报告root不可用，原因未定，热集未cool/restore/delete；GPU完整Model/Result/恢复和CPU导出未验。首训仍未就绪。

本轮只普通topic PR/准确head CI，不代merge、正式冻结或生产提升；CI未完成则明确pending。原source回滚为revert具名topic，安装按原owner确切artifact/config回滚；14paths及三正常launcher字节/权限已恢复，正常App保持。原件与云对象不删，数据恢复沿原Store。外部root收据、全部原始日志/身份/失败链接不成为仓库私有Markdown依赖；当前只有README→CURRENT→此唯一任务，不新立平行计划。

## 历史

[上一轮三小时任务及原失败](THREE_HOUR_EXECUTION_20261021.zh-CN.md)已结束，9a9通过所选CI后依具名C-2合入develop。它的旧截止、私人ACTIVE、旧游戏/请求与报告不派发本轮工作。两轮完整计划和用户转发prompt仍由外部固定计划保存；本轮不自动启动第二轮，未完成不擅增第三/第四轮。
