# P1 r1 训练前冻结定义 v0.3（仓库同步副本，待用户批准）

本页同步唯一共享待批定义；canonical精确路径为 `/Users/fire/Desktop/SpireAgentProject/claude-handoff-2026-10-13/P1_r1_SUPPORT_SCOPE_v0.3_2026-10-18.zh-CN.md`，其SHA见输入索引 (`/Users/fire/Desktop/SpireAgentProject/claude-handoff-2026-10-13/FREEZE_INPUTS.json`)。两处按同一次文档集成同步，不独立派生要求。它不是冻结批准，也不批准训练、上传、云费用、历史资产处置或新的协议取舍。用户已定的要求继续执行；尚待决定的范围在第8节明确列出，不能把Claude建议或本次文档合并当成用户已定。


**2026-10-19当前方法方向已获用户批准**：以合适粒度和开发速度完整服务P1，复用成熟Human真实原生动作入口及exact owner关联；按用户新批准的执行S/A(S)派生P1，点击H仅保留来源，执行边界补同snapshot完整public-basis-1/全部Reads/catalog；Source4主要服务P2/后续，退出P1当前强制主线。**方向、开录门及本定义§4.1–4.2（原件/派生整局完整与新路线E2/E3适用性）已由用户明确批准；整份冻结定义与最终冻结PR仍待用户批准**，不把方法/开录门批准或文档合入写成整份冻结定义已获批准。英文≥3真实完整局、全幕/第三幕与胜利、独立信息复验、最终40+20、产品/R20、历史收敛及经授权的最简云等既定结果均保留。

**目的**：给第一轮训练一个确定、可复验的P1环境、采集/解释、独立Agent接入和可定位的项目全貌；快速复用已有成果，不扩建复杂GUI/团队平台。硬截止仍为2026-10-20 16:30 Brisbane；未完成交实际剩余清单，不暗改标准或延期。

历史查过：既有Architecture/Components/ADR0016/DOCUMENT_MAP、K0/K1/K2/K4、版本与数据owner、原用户消息的有界审计、K5和K6，以及当前已审同树源码。旧输入文档的完整原文和SHA已保留为合并附件；原记录/失败/旧资格不改。

## 1. 11条进度、依据和首训价值

以下状态是第19轮实际快照。11行是总报告的共同表头，不意味着每项细化均已获用户批准。全幕/英文/P1整局、严格重放、最终40/20、原产品/R20与收敛已有用户依据；覆盖口径和冻结前独立信息复验已按用户2026-10-18附条件批准（见第6节）；云具体消费门等实际待决仍按第8节确认。

| # | 验收项 | 当前状态与真实缺口 | 为什么服务第一轮训练／依据层 |
|---|---|---|---|
| 1 | 全部幕Reference正确性 | **没开始**：最终候选同条件英文合格整局0/3，真实第三幕/胜利行仍缺 | 新示范公开事实与结局可靠；全幕/英文/Act3目标/尽量赢为直接要求，≥3为采纳计数口径；不是三局都赢/都到Act3 |
| 2 | Managed一致与最终40/20 | **进行中**：A03两loss完整terminal equal；选择点170/293、236/432，整轨不严格；新英文严格0/3，最终候选40/20未跑 | 保证训练/评估环境对照真实游戏；严格到底、最终40/20有直接用户依据 |
| 3 | 机制覆盖 | **进行中**：用户已批准共同机制新候选合计至少一次、Act2/3五类幕特有各自实到及三证同时PASS；42+4行逐行填原件，新候选证据待齐 | 把首训环境实际能力和遗漏说清；全幕不自动等于每种共同机制每幕都必须重复验 |
| 4 | 英文 | **进行中**：旧候选英文可见，旧Source4失败保持原义；新P1-native原件/离线派生/Managed短重放开录门尚未通过，不能开录 | 与第一轮文字输入一致；用B短段/整局共用证据，不再暗增额外一局 |
| 5 | 采集/转换路径 | **进行中**：已转成熟Human动作入口+完整PB1的P1-native路线；核心/短段仍在实现与自测，原件完整和派生完整分验；旧Source4 complete不再是P1硬门 | 原生示范保存可重派生的P1必需原件；格式/源码存在不等于短段或整局已通过 |
| 6 | API/库/CLI产品路径、Workbench薄映射、R20 | **进行中**：rc38/Host32已取得一次有界Application动作/证据闭合；这不是游戏资格，最终tuple产品范围与双Host R20仍未齐 | 训练后完整Agent能从同一入口运行、Stop、查原件；保留原短验要求，不做复杂GUI |
| 7 | 文档与closeout | **进行中**：本定义待批；CURRENT本轮同步合入/装载/缺口，docs检查与复核待完成 | 消费者能找到准确身份、操作与差异，不再双份范围书 |
| 8 | K收敛 | **进行中**：K0/K1/K2/磁盘/K5/K6材料已交；K4既有9/10，B操作单仍缺；K3表24项均已exact tag远端核验后关闭；19项以后复用已逐条入地图标实际tag（未迁入源码保留） | 入口定位owner/状态/出处/去向；不是承诺全史穷尽或重验所有旧结论 |
| 9 | R2+Modal最简云 | **进行中**：已有存储/派发机制与K5方案；新P1→R2回读→Modal消费尚未实测，上传/费用需另批 | 首训能读同一获准数据；用户已定最简方向，具体对象/只读探针与细门待确认 |
| 10 | Agent公开信息复验 | **没开始**：已批准冻结前由非作者agent共用新英文记录，每出现机制至少一次画面对照完整P1；原native提示/预览+CUA路径已定位，live待小验 | 防止人看游戏能选而Agent缺必需公开事实；不把“之后再复验”自动升级为无界审查 |
| 11 | 组件/协议/格式的位置和原因 | **进行中**：已补三路链及逐行用途/首训价值，等root/K4复核 | 避免重复实现、错误依赖和权威混淆；复用已有组件框架 |

依据[审计附件](FREEZE_AUDIT.md)：U14是全幕/英文/采集/严格/40+20/产品要求；U15采纳提案§4/§6计数和时刻；UK要求K及资产批准边界；U19要求首训导向、API/库/CLI、GUI薄映射、最简云、审计合成与11条表。原消息ID/记录SHA见审计附件。旧中文Boss“最多3局”窗口不是本次英文“至少3局”的同一数字依据。

## 2. 三路链与本次交付边界

```text
在线：完整Agent ↔ Agent程序端口 ↔ Policy Runtime ↔ SDK(P1) ↔ Connector ↔ Host/STS2
      Host Runtime管启动/隔离/恢复；执行请求/权限/预算/Stop与证据关联横切
      Reference=真实游戏+Mod，Managed=无界面Host路径；Mod是包装

独立记录：真实accepted原生动作/UI选择＋H来源＋执行S/A(S)完整PB1 → Annotator原件 → Evidence → 共享SDK派生P1轨迹
          Source4原始输入/publication来源保留给P2/后续，退出P1当前强制主线
          Agent/选择器 → Runtime AgentRun原件 → 原typed verifier/replay（独立路线）

研究：获准来源/用途 → Projection/InputSpec/TargetSpec → 数据集/划分/TrainingRun
      → checkpoint/Model → 完整Agent组合 → 在线新运行 → 固定评估
      R2承载对象，Modal承载获批作业；不决定来源/游戏/研究准入语义
```

每个组件、协议、格式的位置、为什么存在、首训价值与保留/占位状态，见项目地图的[链路用途表](CHAIN_PURPOSES.md)。现行API/库/CLI是主操作面；GUI映射原应用用例，保留已有显示/确认/配对适配，不扩复杂GUI/团队/Hub。

“P1记录→研究消费”是本次交付的**阶段线**，不是已经冻结一个统一数据消费合同。固定的是发布组合、在线P1语义、可校验原件及解释身份；Projection/Input/Target、来源admission/use、数据集/划分/门槛、词表、训练配置和Q1方案仍属Q链owner后续决定。本批选用冻结P1，不废止其它历史合格来源或actual consumer。

既有Q3第一批第3版 (`/Users/fire/Desktop/SpireAgentProject/claude-handoff-2026-10-13/Q3_BATCH1_PLAN_2026-10-17.zh-CN.md`)已获用户批准在P1完成后执行：1a六Agent工程链、1b既定对照问题保留；本定义不撤销或重设它。P1前仅低优先级Q3-M0；实际数据划分/门槛、一次Modal基线之外费用和配置变更按原限制确认，不借文档合并提前fit。

## 3. 源码锚点、保留身份与发布原则

develop已由普通PR205正常合入至 `733782b119967828160f893893aaf5bff3b540e4`（tree `2669e97f0fd292a0e8269747c836672653f32762`），full CI39124068850 PASS；父结点ea7c/cbbea和202/204/203原合入身份均保留，不是冻结发布。此前语义定义文档从rootI committed `c931292e3e59d2d9a791da764b7f2a51f3dcb820`派生；本次上层连接文档基于 `4b489108238d1a17bef1dae421d289b29c5883dc`，核心已实现但工程开录门仍未通过；最新实际源码/装载/原件从CURRENT与root当前回执核对。旧Source4专项取消包与分支原样保留，退出P1新增预检/投入；此前rootI本地合入的Source4取消专项已由root以824847fb反向剥离，不作为本批待推develop内容，本文不改源码或refs。旧fullPython4485 PASS/5skip+93subtests仍绑定原8967生产者，原环境失败/纠正保留，不追贴当前组合。

以下身份表保留此前核验范围，不作为最新安装清单或当前预检派发入口；新P1-native核心尚未取得新编译/装载/短段资格。旧d64/04及其后0f2/05–06失败均保持原件，当前路线只按4.1与CURRENT推进。

| 层 | 保留的已核验事实及冻结时要固定的内容 |
|---|---|
| 游戏 | 标准单人Defect/A0、正式新记录英文；DLL SHA `9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4`；最终模式/语言/模板和版本必须对应原件 |
| 旧Reference Mod证据 | native04实际installed/loaded `d64e57b0b872c2ecbfb6db8cfdc61409ae9fb252dfe91750c5b4cecdb7d7c273` / MVID `494e234f-d6f9-4aaa-9624-a8211066b682`，build79ba、Connector source60d14222；rollback `2026-10-18T12-12-49.442Z` 保留6份旧4d文件。producer471源码交付时未实装；其后0f2候选实际loaded05–06但整体Source4仍FAIL；不向d64或新P1-native转贴资格 |
| Managed | 新实测native `0426384c8c38e1a147dcfb2ba39c599ebdebe191f979acaaab8a4e302060943b` / MVID `1115859e-f311-4e2e-b103-0b3c91168250` / patch `43e223e567c1a8610bbc80b377033e9579b72e1ece7081d5f9b27b36c211a3c8`，buildf434；同04原progress/actual seed的初始Neow完整P1+12Reads严格equal，只有单点，不是选择/整轨/整局。A03 a70/cd47及1482/S05保持旧身份 |
| P1/SDK/驱动/Runtime | P1 r1.3唯一语义owner；固定共享核心/Host mapping/Agent端口/比较器和实际发布包身份。typed tooltip用原全局双射，仅已声明typed shop detail尾部无序，usable_potions有序，500000工作上限保留 |
| 采集/转换 | 当前P1-native按已批准的成熟Human accepted来源/执行S同snapshot完整PB1、run journal、Close/pack与共享核派生；格式由原owner实现，本文4.1–4.2完整性定义已批准，实际验收仍待。旧Source4 profile/bundle/report-v3保留原范围，不作当前P1强制门 |
| 模板/显示 | Reference normal mature全解锁/FTUEoff/instant已在04 prepare与run UnlockState原生读回PASS；actual seed `SWNBPYW69BAC` 与menu请求值不同，严格比较用实际原件。准备数据私有，不进Agent/P1/训练标签；旧模板不改称成熟profile |
| 校验/产品/文档 | 原verifier/replay/comparator、API/库/CLI调用链、外包R20、范围/覆盖/差异和K入口；冻结时填实际成功原件，空格不当ready |

此前功能源码/消费者快照（保留原时点，不是最新安装/整局资格，实际发布仍沿原组件manifest/BOM）：

| 层/消费者 | 已实现/已验的源码或原件 | 实际消费者/资格缺口 |
|---|---|---|
| Reference normal baseline | source60d14222；native04 prepare/run解锁读回PASS | d64实际loaded，但原Source4整体FAIL |
| Source4 producer（历史） | source47157680；Host39、NativeLogical179、SourceNative65、Game-Mod55 PASS | 后续0f2实装的05–06仍FAIL；04九个capture throw内部根因不能逐个追认；不为本lane新增预检 |
| Managed native act selection | 0426384c/patch43e223e5；原生seeded act算法 | 初始Neow fullP1+12Reads equal单点；未整轨严格 |
| Python受信progress consumer | source42a3ca47；HostPython31、Workbench70 PASS，rc32 | prepare/reset/proof私有链源码已交；新installed Application待验 |
| Runtime GLOBAL startup | source8a918d72、rc38；624PASS/3skip与Python175 PASS | 其后Runtime38/Host32已私有安装并取得一次有界Application动作/证据闭合；不替代最终双Host R20或游戏资格 |
| 旧证据与组件框架 | Evidence/Store/Training/API等原owner和版本规则保留 | 旧Workbench壳/旧来源/旧资格不推广成当前安装或新训练资格 |

用户确定的Agent初始默认是普通成熟全解锁Standard单人新局/seed，教学独立、Reference instant。原owner实现已在d64 native04正常prepare与实际run UnlockState中核验；开局前administrative provenance不进Agent/P1。旧starter模板、真实玩家历史与正式Human来源不被改写。Managed已接同份真实progress的源码与单点比较，当前整轨/新installed产品资格仍待验。

版本修订按真实公开语义、实际consumer兼容、解释和数据影响决定，不能机械用“r1.x就事实保持/r2才会影响数据”判断。原件/producer身份不改，新解释另记；r1.x也可能须重派生/重验。冻结不永久禁止架构演进；只有当前发布组合受批准的范围约束。

## 4. 记录、转换、比较与计数

Record grammar ≠ P1环境合同 ≠ Agent程序端口 ≠ Model Input/Target。通用环境采集不限Human或鼠标；直接Agent原件独立于Annotator/Source4。当前Runtime写 `sts2.policy-runtime/agent-session-run-1`目录（global_source、frame/Consume/ACK、Receipt/Result），旧 `sts2.p1/episode-record-1`单文件保留原converter/reader；不能混成一个当前格式。

### 4.1 新P1-native完整性定义（用户已批准）与已确定的开录门

用户2026-10-19已批准本节与4.2，并进一步明确恢复历史rc4的执行边界S/A(S)作为P1派生决策点，点击/输入H只作为来源；这次变更在记录链⑤⑥，不改在线③④、两Host或Agent动作能力。批准要求不表示已实现或实际通过，整份冻结定义与最终冻结PR仍待批准。唯一细则见[P1协议§4.1](../../protocols/P1_GLOBAL_R1.zh-CN.md#41-录制原件派生p1的requires用户已批准)；具体字段由Annotator/adapter owner声明，不在范围书另建schema。

**原件整局完整**：真实原生新局到自然终局，所选run全部玩家来源native accepted保留原始H/来源、精确key/全部operands/owner与接受证明；另在执行边界S以同snapshot身份保留完整PB1、全部advertised Reads及完整catalog A(S)。H只是原点击来源，不称作P1决策时点，不从后来的S回填H。nonqueued证明H/S在同一原生边界；queued明确`queued=true`并披露H→S顺序和间隔。public action/native input沿原合同二选一，不制造H BoundAction。执行时public catalog缺该暂存牌的BoundAction本身不等于完整S不可得；允许按完整原生A(S)/native input声明派生，真实S/PB或可派生目录拿不到才停止实现报告。保留结果/后继、起终、run/parent/save/continuity及Close/pack内容校验。取消、目标变化、失效、执行不匹配、真实效果与unknown分别保留issue/disposition，不能改成成功或当作未提交拿牌取消丢掉。未提交拿牌取消本无accepted决策/label；内部敌人/自动GameAction只保留事实/效果，不制造玩家label。

**派生完整**：P1选择基于执行S/A(S)：真实accepted native key和全部operands与该执行catalog精确匹配，沿在线同一共享核恢复完整P1观察、catalog、choice/聚合/commitments、结果/后继和终局，逐项可回执行原件。每个native accepted都有派生决策或明确issue；有issue/unknown、必需S信息缺失或决策遗漏不能填派生完整。不能用仅captured数量/Close成功声称全局没有静默漏录。原件完整、派生完整、Managed严格轨迹/终局、Human来源与研究准入分别报告。

**adapter暂缺与旧数据**：只有执行S/A(S)及所需原件已完整保存，暂缺adapter才可边录边补并重新派生，显式列待解释机制；不能改原件或用后来的状态补旧H/旧S，旧gap/FAIL保持原义。历史rc4的H/native input→执行S/A(S)方法复用，524/524及47rapid handoffs只属于原rc4原件，不转贴本次P1资格。当前P1执行边界接线、字段实现和真实短段仍待B/root交付/验收。

**开录短段门**：邀请用户正式录整局之前，新候选必须有一段实际短记录，覆盖真实Play、一次未提交拿牌取消且0label、EndTurn，以及至少一项其它非出牌决策（如地图/事件/奖励）。该段的原件完整、共享核离线校验/派生、Managed严格短重放都通过，保留同一tuple/实际seed/原件/报告链接；仅源码fixture、取消0label或单侧动作成功不够。工程自测短段按真实操作者/工程来源标注，不能冒充正式Human局。root工程自测短段实际通过开录门后，用户直接开始正式英文整局，**不再单独要求真人短段**。第一局开始后优先对开头已经保存的一段原件做转换核对，作为真人验证；不要求用户另开一局或为核对中断正式局，开头段通过也不冒称该整局完整。**正式整局仅由用户操作**；联系不到用户、用户不方便或找不到入口/记录器时，正式录制及必需的用户决定都等待用户，不代录正式局，可独立的其它工作并行继续。

**本轮实际验证安排**：在新候选上验证回合开始排队、出牌中排队、取消、目标死亡；具体字段与实现由B/root给，暂存牌/手牌PB1差异由C核后交Curation，文档不宣称这些检查已完成。用户已去睡觉；做到需要用户操作时停止，待用户可操作后做5–10分钟快速连点工程自测，再核无未解释派生gap及Managed严格回放。修好并自测通过前不开正式局，不替用户操作该次自测或正式局。主线按第7节最新2小时时间盒收敛。

**逐局处理（用户2026-10-19明确）**：每局约30分钟，不需录制中途实时盯；每局结束立即转换核对，首局优先核已经保存的开头段。发现原件缺口立刻报告缺什么、在哪一步，由用户决定继续或重开；同时修录制器出新candidate。缺口原件完整保留，不计至少3整局；缺口前片段只有同次Reference正确记录、离线验证与Managed严格重放三证同时通过才可计机制覆盖。用户指定的四项完整性检测必须逐项说明已实现/缺失，静默漏录抓不到时列known risk，不能用完整性目标冒充实现保证；详见[P1协议§4.1](../../protocols/P1_GLOBAL_R1.zh-CN.md#41-录制原件派生p1的requires用户已批准)的源码状态表。

### 4.2 E2/E3适用范围（新路线适用性用户已批准）

- E2保留原Source4无输入启动waiting的原合同和旧报告解释。新路线行政准备不产label，也不要求它先满足Source4 raw前缀分类；但真实执行决策S缺同snapshot完整PB1/全部Read/catalog、或native key/全部operands不匹配仍不合格，不能借“启动”排掉真实accepted。
- E3保留原Source4同owner/card相邻select→deselect的原合同。新路线未提交拿牌取消本来就没有accepted决策，不产生label，不再要求Source4 raw净零分类作为P1前置。已经真实accepted的选择、效果、取消/中断和unknown仍按原始事实保留，不能把它们混成“只是拿牌”丢掉。
- H来源与执行S分存不是Source4新的启动或取消例外；执行前取消、目标改变、失效和不匹配保持原生结果/issue，不派生成成功。native accepted的公开action/native input互斥，不用执行S制造旧H动作。
- 旧Source4报告、取消证明包、分支及native01–06失败事实均保持原义。上述范围调整不回填旧FAIL，不静默改旧report-v3的complete含义。当前不为Source4再开新预检或新增投入。

### 4.3 保留的历史Source4解释

Source4 report-v3可显式 `--run-continuity`选择运行域：原菜单和域外事实仍保留并有原因，不能借scope排除运行内partial、unknown、gap、遗漏输入或终局。`complete`表示所选/声明范围的保留capture分类完整；`whole_run`另需真实new-run起点、连续来源及完整自然terminal，最终真整局证明不能由bounded complete、Close或fixture替代。Human来源、完整性、P1解释、轨迹/行为监督和研究训练准入分别由原owner判断。 仅本次附条件批准的有界启动waiting可按§8及P1§17 E2单列非决策段；它不把原NativeLogical缺失变成完整。

在保留的Source4 lane中，用户已批准限定selector reversal离线解释，唯一语义登记为[P1§17 E3](../../protocols/P1_GLOBAL_R1.zh-CN.md#17-登记的协议例外必须经用户审核才能新增)：同owner/同原始card相邻select→deselect，全部complete before/after、all Reads及known原生接受证明恢复原状且无任何效果，才可context_only、不产生P1 choice/训练label；首select暂缓至pair分类，不能先发标签后删。Agent仍append-only、不加deselect；missing/unknown/换owner/复杂交错仍unresolved，不回填old04FAIL。**批准不等于旧报告或整局通过**；该旧lane不再派发P1新预检，新路线适用性以4.2已批准定义为准。

正式计数继续用同一最终候选、英文、≥3完整Human局，环境录制/选择器分来源列；自然loss可算完整局，不能当第三幕/胜利覆盖。不同候选/语言、locator诊断、原not_started跳过、Agent预检和save恢复片段不拼成最终三局；存档续玩保留父局/存档SHA/new continuity，数据按同谱系分组。

**Console批准边界**：仅Reference独立诊断可用；整份console-assisted session排除正式≥3Human、覆盖与训练，保存后续玩也不能洗掉console祖先。当前Managed没有同一Console准备链；跳幕/战斗debug会改变RNG/visited/history，不能凭同seed/一帧相等称严格等价。来源事实和原件保留，不以pause掩盖console操作。

**保存续玩仅批准评估，方案未实施。** 用户同意的资格是：非console真实parent/save起点的suffix，只有该次Reference记录正确、离线校验PASS、Managed严格重放PASS三证实际齐备后，才可补对应覆盖；不能计≥3自然完整Human局/whole_run。parent与suffix按同谱系同组，console-assisted祖先永久排除覆盖/训练。保留parent原件/save SHA、来源候选、两Host loaded tuple及新continuity；旧S05只证1482 Managed，当前a70拒旧descriptor，Reference原生Map保存解码不支持，不能直接宣称跨Host续玩。只读评估 (`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/p1-console-coverage-20261018/saved-continuation-evaluation.md`)已列准确MapRoom接缝与普通Combat/Event边界替代；具体实施/成本尚未获本次批准，不删除pre_finished_room、不改旧descriptor、不用EnterAct代续玩。

严格比较固定tuple/语言/角色/难度/种子/起点，在Managed重放Reference真实选择，核完整公开观察、目录、选择、承诺及完整terminal。原全局实体双射、重复实例、owner/关系和文本保留；只有合同已声明集合可无序；different/incomparable/unknown/预算/技术中断/自然终局分别计。

旧中文Reference缺陷按最新root转交的用户指令登记，新英文整局承担严格验收，真实Managed缺陷继续修。A03两个终局equal是实质进展，但中间170/293、236/432，不是整轨严格；不能把所有description差异都推广为已证旧缓存。原Human01 occurrence41同typed卡4→新6、药水顺序原事实均不改。终局后Return/timeline仍是产品限制；当前P1胜负终局不新增为必须执行结算后导航的门。

## 5. 工作台与最简云

按[K6定位](WORKBENCH_POSITION.md)，工作台是现有Python Application用例层及可选人类面板，复用环境/完整Agent登记、prepare/start、one_step、Stop、查原件；GLOBAL已有API/库/浏览器入口，CLI没有同名register子命令，不虚构命令。`model stop`停Agent，`project stop`停Application服务，不混用。原Workbench短验和外包R20两Host要求保留；现成2684B外包与harness可复用，材料/源码不当实际验收。复杂GUI/团队系统本次不做，既有薄映射/信任边界保留。

云按[K5最简建议](CLOUD_MINIMAL.md)复用storage库/现有push/pull：原始/P1产物及来源清单→ArtifactStore→R2→完整回读同hash→Modal消费获准同一对象。R2分块/manifest库可直接用，不强制Hub登录；现Modal派发器消费旧FeatureJob/TrainingRun，不自动等于新P1训练reader。K5查到的本地合成分块检查只证明局部机制，不是当前R2/Modal实测。

Hub、Cloudflare登录、Evidence delivery、campaign/团队账号和Workbench云页面保留现状/接口，不扩建也不先迁全系统。两个历史大数据根合计约40.286GiB逻辑大小属于保全候选，不是自动训练数据；先批具体源/目的prefix/清单/校验，再传，原地删除另批。8MiB块/原blob限额、内存archive_bundle和临时整payload复制各有范围，不能为40G迁移直接放宽全局上限。

云详细样本/读回/Modal CPU消费、上传对象及费用需明确批准后才执行；当前没有新上传/Modal消费实证。其目标是第一轮可读的最小接缝，不顺带启动训练或消耗Q3未来基线额度。

## 6. 已批准覆盖与冻结前独立信息复验（42机制行+4跨幕/终局行）


用户2026-10-18已选覆盖方案1并附条件：**共同机制在新候选记录合计至少出现并验过一次**；不要求每个共同机制在每幕重复验。**第2幕、第3幕各自的普通战、精英战、该幕事件、Boss战、Boss奖励五类必须实际验到**，两幕不能互相代替。每次计PASS必须同时具备该次Reference记录正确、离线校验PASS、Managed严格重放PASS三项证据；到达、源码修好或单项PASS都不能填通过。

**A0第三幕BossReward口径已获用户批准**：允许真实自然空奖励/Proceed，再走原生结局（TheArchitect对话/Proceed→WinRun→OnEnded victory）；Boss击败本身不等于终局。该奖励页及实际victory后继同一点的Reference正确原件、离线校验PASS和Managed严格重放PASS可共同支持奖励/胜利行，不制造不存在的有奖奖励。若打不出胜利，第三幕Boss与BossReward两行都保留gap交用户，不能凭打到Boss勾PASS。A10末幕双Boss是未来项目目标，本轮仍A0。

下表保留42个逐幕机制行和4个跨幕/终局行，逐行记录实际候选/来源/位置、三项证据路径和结果。共同机制只须合计一次，别幕对应行可明确引用同一已通过实例，不伪称该幕发生；幕特有必验行必须用对应幕自身原件。当前新候选三证尚未齐，下表不预填PASS。3局未遇共同机制时，先用同种子前缀重放或用户有意路线补验；仍未到达就作为冻结前缺口交用户决定，不算PASS，也不无限卡住冻结。第三幕、胜利及跨幕/终局原要求保留，取得困难的实际剩余范围交用户决定。

**冻结前独立信息复验已选方案1并附条件**：由非作者agent共用这批新英文记录，对每个实际出现机制至少一次，将游戏画面与同次完整P1逐项对照；必须包含悬停关键词解释、升级预览、敌人意图和遗物能力说明。采用事后独立Reference同种子、原选择前缀复现取得真画面，不插入用户录制、不要求用户额外操作。复用既有native-logical目录的 `show_card_tips` / `show_power_tips` / `show_intent_tips` / `show_relic_tips` / `inspect_relic`、`inspect_deck_card` / `toggle_card_upgrade_preview`，沿原 `nativeFocus` / `MouseEntered` / `NHoverTipSet` / Inspector，再由CUA `getScreenshot`取得真实渲染画面；不另建截图产品框架。**既有代码路径已定位，live前缀复现与rendering仍待小验，尚未完成画面复验**；Read或离线replay都不能替代真画面。K4按非作者验收者角色执行短方案。所需合同内信息缺失由原owner修；扩信息范围或改协议先交用户决定。独立复验保存画面、P1/来源绑定、检查者与结论，不能仅凭作者源码自检代替。

旧证据：H1=中文Human01，317输入、Act1 Boss后Act2 floor22 loss；H2=中文Human02，434输入、Act1 Boss后Act2 floor28 loss。M1=C11、M2=C09均为诊断新执行，分别67/293、47/432公开图相等，终局图也不同。原件索引与逐occ归因 (`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/outputs/P1-round12-Managed-diagnostic-2026-10-18-details.md`)。本表历史列只覆盖H1/H2这组P1原件及注明的M1/M2诊断，不是全机器、全历史记录穷尽；其中无Act3不能写成全史无Act3。本表按幕核对了原轨迹的选择位置，完整索引见 历史动作索引 (`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/p1-sprint-root-20261018/historical-act-mechanism-index.json`)。索引说明原始选择/catalog与场景；只有对应Receipt/Result和后继才能证明效果完成，旧not_started不计成功。不从“打到某幕”推断所有该幕机制都遇到。

| 幕 | 机制（按批准口径取证） | 已有记录及真实状态 | 新冻结验收状态 | 到达办法 |
|---|---|---|---|---|
| 第1幕 | 普通/精英战：出牌、目标、结束回合、公开状态与牌堆 | H1/H2 Act1普通战实录；精英种类仍需逐场核，不从combat_turn推断 | 共同机制合计至少一次；三证待齐 | 正常推进并选精英路线；每场核完整观察/选择 |
| 第1幕 | 战斗药水使用、丢弃及槽/目标顺序 | H1 occ119/120是可用药水列表顺序反例；实际potion.use选项见78/131/158，需分场核Result | 共同机制合计至少一次；三证待齐 | 保留药水至有合法目标的战斗；验证后继与库存 |
| 第1幕 | 地图、跨房间、非地图公开map | H1/H2通过Act1并在Act2终局；H2 occ321地图→新事件 | 共同机制合计至少一次；三证待齐 | 同种子公开前缀；记录房间变更和owner |
| 第1幕 | 金币/遗物/药水奖励、满槽与离开 | 历史普通奖励已见；满槽/linked/mandatory分支未逐项证明 | 共同机制合计至少一次；三证待齐 | 保留满槽；按实际奖励分支记录，未出现不填通过 |
| 第1幕 | 卡奖领取、跳过/替代、生成卡详情 | H1 Act1 occ16/29等领取卡，occ159 REBOOT生成选择；拒绝/替代逐项待核 | 共同机制合计至少一次；三证待齐 | 战斗后自然奖励；保留完整详情与后继牌组 |
| 第1幕 | 营火休息、离开 | H1 Act1 occ100–102旧rest修复链；H2 occ136/137等rest.choose；休息具体意图核原件 | 共同机制合计至少一次；三证待齐 | 路线选营火，分别记录休息和可继续页 |
| 第1幕 | 升级选择、预览、确认 | H1 Act1 occ101/116/142/195、H2 occ166选择upgrade.choose；预览/后继待最终验收 | 共同机制合计至少一次；三证待齐 | 进入升级营火并选合法卡，核Preview/Result/后继 |
| 第1幕 | 附魔选择、预览与结果 | H1 Act1 occ75、H2 occ54选择enchant.choose；旧公开summary差异在修 | 共同机制合计至少一次；三证待齐 | 遇附魔机会保留原始前缀；不能按名字推支持 |
| 第1幕 | 商店购买、移除等服务、关闭再开及离开 | H1 Act1 occ104–108、145–147商店链，含原RNG/投影反例；服务效果待逐项核 | 共同机制合计至少一次；三证待齐 | 有金币的商店路线；核售罄/价格/服务后继 |
| 第1幕 | 该幕事件：选项、继续、owner与嵌套 | H1 Act1 occ49/50、74/76；H2 occ77事件及78/79八选二；不是Act2证据 | 共同机制合计至少一次；三证待齐 | 各幕真人有意走事件；每类出现的选择都校验 |
| 第1幕 | 宝箱打开、取遗物、跳过 | H1 Act1 occ110–113；H2 occ132–134宝箱动作，原H1跳过not_started另记 | 共同机制合计至少一次；三证待齐 | 路线选宝箱；不在开箱前揭示未来遗物 |
| 第1幕 | 单选、多选、生成卡、选堆/包选择 | H2 Act1 occ77事件八选二、89弃牌选择；H1 occ159生成选择，其他类型待核 | 共同机制合计至少一次；三证待齐 | 复用已发生公开前缀，其他类型仅在真实出现后登记 |
| 第1幕 | 移除/变换及后继牌组 | 旧事件/商店有入口线索；未逐幕证明全部效果 | 共同机制合计至少一次；三证待齐 | 使用自然服务/事件；核实体消失或新实体及后继 |
| 第1幕 | Boss战斗、掉落与Boss遗物选择 | H1/H2确实经过Act1 Boss；Act2/3 Boss未有合格证明 | 共同机制合计至少一次；三证待齐 | 真人目标第三幕，保留Boss战及奖励完整链 |
| 第2幕 | 普通/精英战：出牌、目标、结束回合、公开状态与牌堆 | H1 Act2 occ243起/H2 occ255起普通战实录；精英种类待核 | 本幕普通战、精英战各必验；三证待齐 | 正常推进并选精英路线；每场核完整观察/选择 |
| 第2幕 | 战斗药水使用、丢弃及槽/目标顺序 | H1 Act2 occ310选择potion.use；场景与Result须原件验收；H2 occ323事件喝药不代替战斗用药 | 共同机制合计至少一次；三证待齐 | 保留药水至有合法目标的战斗；验证后继与库存 |
| 第2幕 | 地图、跨房间、非地图公开map | H1/H2通过Act1并在Act2终局；H2 occ321地图→新事件 | 共同机制合计至少一次；三证待齐 | 同种子公开前缀；记录房间变更和owner |
| 第2幕 | 金币/遗物/药水奖励、满槽与离开 | 历史普通奖励已见；满槽/linked/mandatory分支未逐项证明 | 共同机制合计至少一次；三证待齐 | 保留满槽；按实际奖励分支记录，未出现不填通过 |
| 第2幕 | 卡奖领取、跳过/替代、生成卡详情 | H1 Act2 occ265、H2 occ279特殊奖励；H2 occ281/315/390领取卡、357拒绝组 | 共同机制合计至少一次；三证待齐 | 战斗后自然奖励；保留完整详情与后继牌组 |
| 第2幕 | 营火休息、离开 | 尚无确认的Act2营火操作链；不从Act1迁移 | 共同机制合计至少一次；三证待齐 | 路线选营火，分别记录休息和可继续页 |
| 第2幕 | 升级选择、预览、确认 | H1 Act2 occ237–240选择upgrade.choose；不迁移Act1证据 | 共同机制合计至少一次；三证待齐 | 进入升级营火并选合法卡，核Preview/Result/后继 |
| 第2幕 | 附魔选择、预览与结果 | H2 Act2 occ252选择enchant.choose；最终完整比较待验 | 共同机制合计至少一次；三证待齐 | 遇附魔机会保留原始前缀；不能按名字推支持 |
| 第2幕 | 商店购买、移除等服务、关闭再开及离开 | H2 Act2 occ287仅shop.leave，不能当购买/移除完整链 | 共同机制合计至少一次；三证待齐 | 有金币的商店路线；核售罄/价格/服务后继 |
| 第2幕 | 该幕事件：选项、继续、owner与嵌套 | H2 Act2 occ320–323旧event→map→新event反例，原322 not_started；888定点短测越过 | 本幕事件必验；三证待齐 | 各幕真人有意走事件；每类出现的选择都校验 |
| 第2幕 | 宝箱打开、取遗物、跳过 | H2 Act2 occ360–362 treasure.open/activate，需核各自Result与奖品后继 | 共同机制合计至少一次；三证待齐 | 路线选宝箱；不在开箱前揭示未来遗物 |
| 第2幕 | 单选、多选、生成卡、选堆/包选择 | H1 Act2 occ248/289、H2 occ301/346/400 selector.choose；具体类型按原scene逐项核 | 共同机制合计至少一次；三证待齐 | 复用已发生公开前缀，其他类型仅在真实出现后登记 |
| 第2幕 | 移除/变换及后继牌组 | 旧事件/商店有入口线索；未逐幕证明全部效果 | 共同机制合计至少一次；三证待齐 | 使用自然服务/事件；核实体消失或新实体及后继 |
| 第2幕 | Boss战斗、掉落与Boss遗物选择 | H1/H2无Act2 Boss完成记录；两局均在floor22/28自然loss | 本幕Boss战、Boss奖励各必验；三证待齐 | 真人目标第三幕，保留Boss战及奖励完整链 |
| 第3幕 | 普通/精英战：出牌、目标、结束回合、公开状态与牌堆 | H1/H2这组P1原件无Act3；不代表全史无记录，不迁移资格 | 本幕普通战、精英战各必验；三证待齐 | 正常推进并选精英路线；每场核完整观察/选择 |
| 第3幕 | 战斗药水使用、丢弃及槽/目标顺序 | H1/H2这组P1原件无Act3；不代表全史无记录，不迁移资格 | 共同机制合计至少一次；三证待齐 | 保留药水至有合法目标的战斗；验证后继与库存 |
| 第3幕 | 地图、跨房间、非地图公开map | H1/H2这组P1原件无Act3；不代表全史无记录，不迁移资格 | 共同机制合计至少一次；三证待齐 | 同种子公开前缀；记录房间变更和owner |
| 第3幕 | 金币/遗物/药水奖励、满槽与离开 | H1/H2这组P1原件无Act3；不代表全史无记录，不迁移资格 | 共同机制合计至少一次；三证待齐 | 保留满槽；按实际奖励分支记录，未出现不填通过 |
| 第3幕 | 卡奖领取、跳过/替代、生成卡详情 | H1/H2这组P1原件无Act3；不代表全史无记录，不迁移资格 | 共同机制合计至少一次；三证待齐 | 战斗后自然奖励；保留完整详情与后继牌组 |
| 第3幕 | 营火休息、离开 | H1/H2这组P1原件无Act3；不代表全史无记录，不迁移资格 | 共同机制合计至少一次；三证待齐 | 路线选营火，分别记录休息和可继续页 |
| 第3幕 | 升级选择、预览、确认 | H1/H2这组P1原件无Act3；不代表全史无记录，不迁移资格 | 共同机制合计至少一次；三证待齐 | 进入升级营火并选合法卡，核Preview/Result/后继 |
| 第3幕 | 附魔选择、预览与结果 | H1/H2这组P1原件无Act3；不代表全史无记录，不迁移资格 | 共同机制合计至少一次；三证待齐 | 遇附魔机会保留原始前缀；不能按名字推支持 |
| 第3幕 | 商店购买、移除等服务、关闭再开及离开 | H1/H2这组P1原件无Act3；不代表全史无记录，不迁移资格 | 共同机制合计至少一次；三证待齐 | 有金币的商店路线；核售罄/价格/服务后继 |
| 第3幕 | 该幕事件：选项、继续、owner与嵌套 | H1/H2这组P1原件无Act3；不代表全史无记录，不迁移资格 | 本幕事件必验；三证待齐 | 各幕真人有意走事件；每类出现的选择都校验 |
| 第3幕 | 宝箱打开、取遗物、跳过 | H1/H2这组P1原件无Act3；不代表全史无记录，不迁移资格 | 共同机制合计至少一次；三证待齐 | 路线选宝箱；不在开箱前揭示未来遗物 |
| 第3幕 | 单选、多选、生成卡、选堆/包选择 | H1/H2这组P1原件无Act3；不代表全史无记录，不迁移资格 | 共同机制合计至少一次；三证待齐 | 复用已发生公开前缀，其他类型仅在真实出现后登记 |
| 第3幕 | 移除/变换及后继牌组 | H1/H2这组P1原件无Act3；不代表全史无记录，不迁移资格 | 共同机制合计至少一次；三证待齐 | 使用自然服务/事件；核实体消失或新实体及后继 |
| 第3幕 | Boss战斗与Boss奖励（A0可自然空奖励/Proceed） | H1/H2这组P1原件无Act3；不代表全史无记录，不迁移资格 | Boss、BossReward分别留行级结果；同点三证及实际victory待齐，不能赢则两行gap交用户 | 自然Boss→空奖励/Proceed→原生结局→实际victory；不以击败Boss代终局 |
| 1→2 | 跨幕选择、地图与状态连续性 | H1/H2实际进入Act2，旧中文Reference已见；Managed仅诊断执行 | 最终待验 | 完成Act1 Boss奖励后跨幕，核进入新幕前后 |
| 2→3 | 跨幕与Act3初始化 | H1/H2这组P1原件未到Act3；不作全史无记录结论 | 最终待验 | 真人完成Act2 Boss并进入Act3 |
| 终局 | 自然loss、完整终局公开图与释放 | H1 Act2/22、H2 Act2/28均真实loss；M1/M2到loss但终局图不同 | 最终待验 | 新英文整局自然终局，核胜负/HP/Receipt/Stop/释放 |
| 终局 | 胜利 | 未有最终候选合格记录 | 必验缺口；可与A0第三幕自然空奖励/Proceed共享同点三证 | 原生结局至实际victory；不能赢则第三幕Boss/BossReward两行一起列gap交用户 |

A0第三幕合并机制行在此拆开记验收结果（不增加机制种类，42行主索引仍保留）：

| 验收行 | 同次实际原件要求 | 当前结果／未胜利时处理 |
|---|---|---|
| 第3幕Boss战 | 实到自然Boss并核Reference正确、离线PASS、Managed严格PASS；原生结局仍须继续 | 新候选三证未齐；若不能胜利，Boss行gap交用户 |
| 第3幕BossReward | 自然空奖励/Proceed及实际victory后继同点三证；可与胜利行共用原件 | 新候选三证未齐；若不能胜利，BossReward行gap一并交用户 |

关联/强制奖励等只有特殊模式证据的机制，先列实际标准局可达实例；不凭fixture宣称标准模式覆盖，也不因暂未遇到而宣称不存在。多人、未知身份与隐藏未来仍为当前不支持范围。



### 当前路线与真实预检状态

当前P1按用户新批准改为成熟Human真实accepted来源＋执行S/A(S)完整PB1原件＋共享核离线派生，H只保留来源，核心及工程自测短段仍在进行，没有开录PASS。旧native01–06的Source4转换失败、unknown、未完成以及正常Close/pack/进程退出分别保留，不重命名成功；新方向不要求修完旧Source4 raw取消完整性后再服务P1。当前没有Source4新预检/投入。新P1-native实际短段必须先过4.1用户已确定的开录门，才可请用户录正式整局。

## 7. 收敛、发布与停止边界

项目地图应让新人定位owner、状态、出处、适用范围、资产去向和当前操作，不承诺穷尽历史全部知识，也不重验每条旧结论。复用K1的2063资产行、K2的100功能/401文档基准索引；135其它Codex目录仅metadata、语义迁入未核实等限制保留。

用户2026-10-18已批准K3表24项：逐项先创建exact归档tag并远端核验，再关闭旧Draft；不删branch/worktree/data。K1逐项执行回执现已核24/24 exact tag远端解引用匹配、24/24 CLOSED且未合并，原branch/worktree/data保留；[紧凑执行快照](K3_EXECUTION.json)。19项以后复用已逐条进入K2对应链路并标实际tag；[#141/#147/#150/#157/#158/#161/#163](CHAIN_PURPOSES.md#k3历史复用来源按链路逐项定位)必须在Q-CHAIN-0与Q3开工前先查，不改变已批准训练配置。历史数据/资产迁移/删除不由本定义自动授权。冲刺普通已合topic自动删branch和新Mod安装（先备份）已有本轮单独许可，与历史未合分支/原始记录分开。

**本次执行边界主线时间盒**：用户批准自2026-10-18 16:57:56 UTC到18:57:56 UTC（Brisbane 10-19 02:57:56→04:57:56）最多2小时（用户后续已将原3小时上限收紧为2小时）；届时交实际结果/剩余缺口，不能靠换worker或反复重跑重置时间。不因此改最终冻结截止、放宽开录门，或把新解释批准当测试PASS。

**硬检查点：2026-10-19 09:00 Australia/Brisbane（2026-10-18 23:00 UTC）。** 届时必须报告P1-native核心和自测短段的实际结果：原件、离线派生、Managed短重放各自通过/未通过及证据。未过就给具体卡点、已知最短反例、剩余工作与时间估计，随后继续授权范围内的普通修复，不停工等待批复，也不宣称PASS；改变计划/范围仍由用户决定。不暗延长检查点，也不降低最终2026-10-20 16:30 Brisbane门槛。

检查点自动化最后一次在2026-10-20 09:00 Brisbane触发后停止；2026-10-20 16:30的唯一截止报告只由deadline自动化负责。root已按本次用户指令更新自动化，本文不修改自动化。暂停检查点自动化只停止定时触发，不暂停当前工程任务。

如果2026-10-20 16:30用户仍在录制或某局仍在转换，不得中断游戏、打断该次转换或丢弃原件。沿原owner正常完成Close/pack和必要收尾，在唯一截止报告如实写pending及当时缺口，收尾后补实际结果；pending不算PASS，也不暗改冻结验收口径。

如果连续两次预检因不同原因失败，先复查路线与实际上层消费者、成熟旧入口和实现成本，再决定后续修复/有界预检，不能只逐个补丁反复开局。审查与验证以合入、冻结和真实变更风险所需为准；不把每个SHA、每一步都新增独立复核门。状态文件用完整句子写清正在做什么、实测过什么、卡在哪里和下一步，避免缩写电报造成范围误解。联系不到用户、用户不方便或未找到入口时，正式录制及必需用户决定都等待用户；其它独立事项继续并行，不代录正式局。

发布继续用原版本/组件BOM、游戏/Mod/Managed/模板/工具原件和build/install/load身份；“develop冻结PR+tag、是否推main”的交付形式留用户确认，不先开新发布/registry。最终冻结PR用户亲批；普通source修复依已有冲刺授权推进。源码/CI、已装、真实游戏、Human来源、研究准入和策略强度各据实证。

B/root当前关键路径是执行边界P1-native核心、S的同snapshot完整PB1/all Reads/catalog、精确accepted原件与工程自测短段，再做共享核离线派生及Managed严格短重放。root自测短段通过开录门后，由用户直接完成正式英文整局，不再单独真人短段；第一局开头已保存段先转换核对作为真人验证；原3English/全幕/INFO/final40+20/R20等结果不取消。真实缺失在原owner修，原件完整而adapter缺的机制可并行补并重派生。旧Source4取消包不再阻挡P1。

## 8. 提交用户确认的实际取舍

保留的Source4格式中，用户2026-10-18已批准**有界启动waiting离线解释例外**，唯一协议登记为[P1§17 E2](../../protocols/P1_GLOBAL_R1.zh-CN.md#17-登记的协议例外必须经用户审核才能新增)：从**同run的native setup→Launch原见证**起，到首份完整、可决策公开观察前，可保留明确settling且原始失败/缺失的帧为非决策启动语境；原缺失和失败完整保留，不以后帧补齐，不发Agent，不声称NativeLogical缺失前缀变完整。期间混入任何玩家输入（即使未响应）必须unresolved，整局不得complete；该段必须以完整decision结束，崩溃或等不到都不得complete；每局报告单列帧数和时长，不算决策。first decision起的完整性与full strict门不变。**批准条件已登记；startup源码已root独审、22 focused PASS；01原真实包仍地图unmapped；02已Map/EndTurn严格match、E2 1帧7235ms/0input完整，但FTUE使overall false；03 ChangeSource未pause在输入前失败。** 旧MapNativeSourceWitness漏附原件不补写；原owner补附源码已入候选，等新录制验。


1. 本合成冻结定义整体及最终发布形式（原BOM、freeze PR/tag、main另定）；方向、开录门以及§4.1–4.2原件/派生完整与新路线E2/E3适用性已经用户批准，不再列待批。
2. 覆盖口径已按第6节批准；补验后仍未到达的具体机制，冻结前列缺口交用户决定，不预填PASS。
3. 云最小对象/目的prefix/费用与一次有界Modal读取的授权和细门；40G历史迁移独立表，本地删除另批。
4. 冻结前独立信息复验范围/时点已按第6节批准；截图具体方法已按第6节定位原native提示/预览+CUA，live前缀复现与rendering待小验，不提前称取证已通过。扩信息范围/改协议仍交用户决定。
5. 胜利终局确实取得困难时，由用户决定剩余范围；旧中文缺陷的记录保留，新英文严格口径按最新指令执行。
E2/E3原Source4规则、已定覆盖与冻结前独立信息复验继续保留；新路线原件/派生整局完整及E2/E3适用定义已批准；开录门已确定。Source4退出当前关键路径不取消其余11项结果、42行覆盖和终局口径，也不宣布任何运行已过。

首轮训练的数据划分/门槛、P1 Input/Target/词表及实际执行绑定、新费用、Q1固定评测方案仍在冻结后确认；已有Q3第3版模型/配方不因本定义被撤销，若要改变仍先请用户确认，本定义不代批。到截止未齐就交11条实际剩余表，不把待定/未测写成通过。

## 9. 依据、历史输入与当前入口

- 合并前完整原文：较新replies输入 (`/Users/fire/Desktop/SpireAgentProject/claude-handoff-2026-10-13/replies/P1-r1-support-scope-v0.3-2026-10-18-attachments/replies-original.md`)、旧root输入 (`/Users/fire/Desktop/SpireAgentProject/claude-handoff-2026-10-13/replies/P1-r1-support-scope-v0.3-2026-10-18-attachments/root-original.md`)。历史证据完整保留，不从旧append恢复旧任务。
- Claude冻结提案/全景仍作审计过的背景底稿；本定义没有将它们的待定条件默认为批准。
- 当前操作/状态：START (`/Users/fire/Desktop/SpireAgentProject/claude-handoff-2026-10-13/00_START_HERE.md`)与项目[文档入口](../../DOCUMENT_MAP.md)；K4完整操作单仍待B真实预检闭环。
- 已装6c原件：`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/p1-round19-20261018/installed-6c-provenance.json`；合并/CI回执由root保留；A03实际两局报告：`/Users/fire/Desktop/SpireAgentProject/claude-handoff-2026-10-13/replies/P1-round16-A-new-identity-Managed-diagnostics-2026-10-18.md`。

此定义等待用户批准；文档合并/本地检查不发布冻结、不声明最终英文或R20/云已完成。
