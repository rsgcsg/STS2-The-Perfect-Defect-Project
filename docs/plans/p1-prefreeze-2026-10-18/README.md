# P1 训练前收敛：项目地图与当前执行入口

第19轮，2026-10-18。沿用 [Architecture](../../ARCHITECTURE.md)、[Components](../../COMPONENTS.md)、[ADR0016](../../adr/0016-agent-composition-and-execution-request.md) 和 [Document map](../../DOCUMENT_MAP.md)；本页是它们的任务导航，不另建协议、全局registry或治理检查器。**当前还未冻结：本批定义已合成、待用户批准；K0/K1/K2/K5/K6材料已交，K3表24项已exact tag核验后关闭、19条复用已入地图，K4既有9/10、B实际操作单仍缺。** 最终冻结PR仍需用户批准。硬截止2026-10-20 16:30 Brisbane，未完成交剩余清单，不降低门槛。

历史查过：四份既有框架、设计沿革、旧19项任务、组件/Python地图、Full-Run data chain、Source3/4、Data/Provenance、PR25 rc4、K0/K1/K5/K6、第19轮原用户来源审计与当前同树源码。复用已有owner、采集链和来源证据；旧green、旧派发和旧资格不转贴当前组合。

历史调查、审计和收敛与开发同属本轮任务，最高优先级是首批训练前P1冻结及最相关工作。按需结合现行仓库文档、已有账本/地图、审计索引、旧仓库、PR与交接入口查证；Claude各卡片、目标和方法是参考，可能过时或可延后，不自行增加验收或授权。按用户直接决定与实际证据取舍，不要求先重建全部历史。

当前方法已转为**成熟Human真实accepted动作关联＋同次完整PB1原件＋共享核离线派生**；Source4主要服务P2/后续，退出P1强制主线，新取消包/旧FAIL及分支保留，不为其再开预检。方向和开录门已按用户最新指令确定；新原件/派生整局完整及E2/E3新路线适用性见唯一[冻结定义4.1/4.2](SCOPE_v0.3.zh-CN.md)，**这些具体定义及整份冻结定义仍待用户批准**，开录门已定不等于实际开录PASS。2026-10-19 09:00 Brisbane硬检查点报告核心＋自测短段；未过报卡点/剩余时间后继续普通修复，不等批复、不称PASS，改计划用户定。

## 目标

长期目标是能打完整局、持续变强的Agent；下一阶段要在固定条件下可复验地优于随机。当前先完成P1技术组合与历史收敛，再做Q1评测、Q2用途/划分、Q3输入适配和训练链。P1是当前环境合同，不是永久唯一格式。详见唯一[冻结定义v0.3同步页及覆盖索引](SCOPE_v0.3.zh-CN.md)；源canonical精确路径/合并输入SHA见[来源索引](FREEZE_INPUTS.json)，本页不另增门槛。

## 上层链路

逐个组件/协议/格式的**位置、why、首训价值、实际消费者和状态**见[链路用途表](CHAIN_PURPOSES.md)。在线闭环、独立记录DAG和研究闭环相连但不串成强制服务链；Mod是包装、Host Runtime管进程、P1是环境合同、Agent端口是程序边界、Source4是独立来源。

| 段 | 现有owner | 交付与边界 |
|---|---|---|
| 世界→原生事实 | STS2、Native Foundation | 游戏规则、原生语义/生命周期/lineage；不由模型或录制器另判合法性 |
| Host→环境 | Connector；Host Runtime管准备/隔离/进程 | 公开Snapshot、同状态Reads、完整原生目录、执行复验及Receipt；Reference是真游戏+Mod，Managed是无界面Host |
| 环境→P1 | Connector SDK `p1Global`共享核 | 当前合法可知完整公开观察、前进动作、显式承诺；不预演隐藏随机结果 |
| 执行↔Agent | Policy Runtime、P1 bridge、完整Agent | 原控制/预算/Stop/unknown/消费证据owner；Agent管取得/记忆/策略/选择，Model可选 |
| 采集→不可变原件 | Annotator/RecordingService、Runtime AgentRunEvidence、各来源producer | 来源事实/原字节/顺序/缺口/身份；**通用环境采集不限Human或鼠标** |
| 原件→校验/存储 | Evidence、应用ArtifactStore/Curation | 完整性/收据与独立用途/划分；verify或上传不授研究资格 |
| 原件→模型输入/目标 | STPD source-specific admission、Projection/InputSpec/TargetSpec | 声明字段/历史/粒度/损失/监督；不强迫每源都具S-a-S′ |
| 学习→Model→Agent→运行/评估 | 原TrainingService/engine/worker/provider、注册/Composition/Request、Runtime、Task/result reader | 原Run/attempt/checkpoint/预算与证据身份；Model不等于完整Agent；Task、终局、操作和策略质量分验 |

**本次交付阶段线**位于P1发布/可校验原件及解释与Q链研究消费之间；不是统一数据消费合同已经冻结。固定游戏/Mod/Managed、P1/SDK/驱动/Runtime、来源/转换/校验和最小产品路径的实际身份；Projection/Input/Target、admission/use、数据集/划分/门槛、词表、训练/评测配置仍由后续Q链owner决定。版本按实际语义/consumer兼容和数据影响判断；r1.x也可能要新派生/重验，不机械用版本号代替分析。

## 协议与合同

| 边界 | 说明 | owner |
|---|---|---|
| Record grammar | 什么事实被保存、原顺序/身份/关联/缺口/完整性；不保证Agent实际消费或可用于某监督 | [记录链](../../FULL_RUN_DATA_CHAIN.md)、[Source合同](../../../components/annotator/docs/SOURCE_SESSION_PROFILE_V3.md)、[Agent证据](../../../components/policy-runtime/docs/AGENT_SESSION_PROTOCOL.md) |
| P1环境合同 | 此刻合法知道什么、可选什么、时序/承诺/失败/unknown | [P1_GLOBAL_R1](../../protocols/P1_GLOBAL_R1.zh-CN.md)，r1.3唯一P1语义owner |
| Agent程序端口 | Runtime与完整Agent进程的consume/query/choice/control；NDJSON是实现形式 | [Agent-session](../../../components/policy-runtime/docs/AGENT_SESSION_PROTOCOL.md)、P1第9节 |
| Model Input/Target | 模型字段/编码、历史/advance/reset与训练监督；Target不进入当前公开输入 | [Data and provenance](../../../python/docs/DATA_AND_PROVENANCE.md)、研究recipe/InputSpec/TargetSpec |

同为JSON不证明语义兼容；Host不等于Host Runtime启动器，SDK不等于另一协议，程序端口不同于环境合同，Goal不同于训练Target。

## 功能与能力

[100行功能状态表](FEATURE_STATUS.md)逐行给现行/归档/废弃/未完成、owner、证据和限定；[CSV](feature-status.csv)可检索。现行表示当前职责/合同/保留机制，不等于所有场景当前资格。归档表示退出P1默认导航但保留原用途/实际consumer/reader，不等于代码退休；废弃仅指明确退出当前权威的路线，原件仍留；未完成表示所述验收尚未闭合。

旧text-menu、PublicM0/M2、Qwen、Full-Run和B管线按其实际消费者保留；Source3仍是Workbench默认及旧研究lane。ledger运行权威和阻断普通UI的全局serialized-input候选已退出当前权威，不恢复来凑完整。K1另有语义迁入未核实的外部候选，不能把本表当全机器历史功能已穷尽。

## 组件与文档

[Components](../../COMPONENTS.md)拥有组件职责：[Native Foundation](../../../components/native-foundation/README.md)、[Connector](../../../components/connector/docs/DOCUMENT_MAP.md)、[Host Runtime](../../../components/host-runtime/docs/DOCUMENT_MAP.md)、[Annotator](../../../components/annotator/docs/DOCUMENT_MAP.md)、[Evidence](../../../components/evidence/README.md)、[Policy Runtime](../../../components/policy-runtime/README.md)、[Python应用/研究](../../../python/docs/DOCUMENT_MAP.md)。UI/CLI/API复用同一业务owner，不新增控制/存储/训练权威。

[现行文档索引](DOCUMENT_INDEX.md)和[归档索引](ARCHIVE_INDEX.md)是develop@60dc同树基准的401文件导航（132现行参考、269历史）；[文件CSV](document-inventory.csv)含SHA及调查深度。索引存在不等于逐结论重新验收；全部原路径保留。新加入本目录的收敛文档不追填进旧基准计数。

## 格式与采集

| lane | 格式/记录位置 | 适用范围 |
|---|---|---|
| Human原生因果 | manifest2、trace4、canonical3、action-space3、Read2、journal2、close1、bundle3 | PR25 rc4原候选已有两自然败局/524 canonical；不转移到当前Mod/P1 |
| 兼容/归档Human | record2/bundle2兼容；旧trace1–3/canonical2/ledger1–2/record1/bundle1 reader | 原consumer保留；record2不保全canonical3所有子选择，不升级旧bytes |
| Source3 | 原输入basis/ordinals、publications/declarations、Source3 bundle | 有序来源事实，非Human证明/因果后继；旧STPD视图原义保留 |
| 当前P1-native方向 | 真实accepted动作/同次完整PB1、run journal、Close/pack及内容身份 | 复用Human owner，B/root核心与短段在做；新原件/派生完整定义待批，开录门已确定但尚未取得开录PASS |
| Source4（P2/后续） | source-v4/bundle-v4、frozen public-basis/全部Reads/carrier/Task | 原格式/report-v3与失败保留，退出P1强制主线；不再以raw取消全分类阻断P1 |
| 当前P1 Agent/chooser | **目录** `sts2.policy-runtime/agent-session-run-1`，profile `p1-global-r1`，adapter `p1-session-bridge` | `tools/p1-suite.mjs`→bridge→外部stdio Agent→现有Runtime；events.jsonl、global_source原件、frame/Consume/ACK/Receipt/Result与封存清单；独立于Annotator/Source4 |
| 旧P1直接记录 | **单文件** `sts2.p1/episode-record-1` | 原converter/reader的独立保留lane，不是当前Agent CLI唯一格式 |

当前CLI按 `sts2-p1 replay --run-directory`验Agent目录，typed证据由 [Evidence Agent-session reader](../../../components/evidence/README.md)独立核验。研究训练从已获来源资格的immutable原件经 [STPD admission](../../../python/docs/DATA_AND_PROVENANCE.md)读入；Source3与direct-Agent样本各走原reader。P1新Q链消费者仍须在冻结后验，不能以目录可验推出训练已可用。

Source4当前report-v3区分声明范围的`complete`与`whole_run`，可显式选run-continuity且保留菜单/域外原件；不能排除运行内gap/partial/unknown/遗漏输入。经用户批准的有界启动waiting仅按[P1§17 E2](../../protocols/P1_GLOBAL_R1.zh-CN.md#17-登记的协议例外必须经用户审核才能新增)单列非决策段，不补原缺失；bounded完整分类不等于整局；真实new-run、连续来源及完整natural terminal另核。chooser真人会同时看游戏画面，不能声称只看P1观察；原生Human操作另按Annotator lane记录。任何源只要按明确合同可转换成所需轨迹即可支持环境采集，不限Source4/Human/鼠标。

用户已确定的开录门见定义4.1：新短段须有Play、未提交取消0label、EndTurn和至少一种其它非出牌，且原件完整/离线/Managed严格短重放均过；开录门已经确定，实际尚未通过。root自测通过后用户直接正式英文整局，不再另录真人短段；第一局开头已保存段先转换核对作为真人验证，不中断正式局。正式整局只由用户操作；联系不到用户、用户不方便或找不到入口时，正式录制及必需决定都等用户，其它独立工作并行继续。原3English/全幕/INFO/final40+20/R20等结果全部保留。

检查点自动化最后触发为Oct20 09:00 Brisbane，16:30唯一截止报告只由deadline自动化负责；暂停只停定时触发，不停工程。截止时若用户仍录制或某局转换中，不中断/丢原件，正常Close/pack收尾，唯一截止报告写pending，收尾补实情。root已更新自动化，本文不另改。

## 状态与当前任务

本节旧候选身份是历史事实；当前只从上面的P1-native路线和CURRENT指路执行，不恢复Source4取消专项。

develop733782b1已正常合入PR205/fullCI39124068850 PASS；本docs从rootI committed1deca5ed功能组合派生。实际installed d64/MVID494e234f在04 normal prepare/run解锁读回PASS，但Source4 complete=false（5unresolved/13gaps/2unknown）；producer471的39/179/65/55源码检查通过而未实装。Managed0426初始Neow fullP1+12Reads严格equal只是单点。42a Python consumer31/70源码PASS，rc38 package尚未实装，新产品与最终R20待验。旧原件/源码/build/install身份不互相追贴。

## Known differences and next gates

用户已批准共同机制新候选合计至少一次、Act2/3各自五类幕特有及三证同时PASS；冻结前非作者画面对照完整P1，事后独立Reference同种子前缀+原native提示/预览+CUA截图路径已定位，live待小验，详见[第6节](SCOPE_v0.3.zh-CN.md#6-已批准覆盖与冻结前独立信息复验42机制行4跨幕终局行)。

**已定/待批的11条状态和覆盖索引只从[合成定义](SCOPE_v0.3.zh-CN.md)查；实际差异/旧Reference缺陷登记见[A线差异](MANAGED_DIFFERENCES.md)；例外仍只有[P1第17节](../../protocols/P1_GLOBAL_R1.zh-CN.md#17-登记的协议例外必须经用户审核才能新增)。** 旧T1–T7/旧CURRENT和本目录旧资产基准快照不派发当前任务。

- A：A03两局原loss的完整terminal正式比较equal，选择点170/293、236/432，0unknown；中间并非全严格。旧中文Reference原件缺陷登记保留，真实Managed缺陷继续修，新英文整局严格验收仍0/3。A02 H02 ENOSPC及原not_started/unknown保持历史失败；S05只属于1482de5d有界存档，不转到a70d8de1新候选。
- B/root：P1-native核心与工程短段仍在实现，旧Human成熟owner/动作关联可复用，同次PB1和离线共享核接线待实际验收。Source4不再是当前强制主线，也没有新预检投入。E2/E3原格式条文保留，新路线适用定义待批，不回填旧FAIL。
- C/K6：现GLOBAL源码/接口、K6定位与R20外包/harness已存在；复用[API/库/CLI主入口、GUI薄映射](WORKBENCH_POSITION.md)，最终tuple实际产品短验/R20仍待。复杂GUI/团队系统不做，原信任/确认适配保留。
- K5：[云最简调查](CLOUD_MINIMAL.md)复用R2 storage库/块hash与push/pull，不强制Hub。新P1→R2完整回读→Modal消费尚未实测，样本/费用/上传与消费操作另批；Hub/团队/云页面仅保留状态/接口。
- 覆盖补验：Console仅Reference独立诊断，整session及console祖先的续玩都排除正式3局/覆盖/训练；保存续玩仅批准评估，非console suffix须实际三证PASS才计覆盖、不计3full且parent/suffix同组。A0 Act3可自然空奖励/Proceed+实际victory同点三证；不能胜利则Boss/BossReward两行gap交用户。详见[定义覆盖/续玩](SCOPE_v0.3.zh-CN.md)。
- 最终门：同候选英文≥3整局、实际第三幕/胜利行、严格完整轨迹/terminal、稳定候选40/20、原产品/R20与K；42+4行是可检索覆盖索引，不暗增每个共同机制在每幕重复通过的笛卡尔硬门。用户待定项见定义第8节，不能由底稿代批。

问题判层：先最短原始反例→原生事实/生命周期修Native Foundation/Host；公开绑定/Reads/原生投递修Connector；仅表示/运输且语义不变修原SDK/port/Input；Agent取得/等待/选择变动修composition；Input/Target/模型/recipe变动由研究owner；P1合法信息/粒度/失败语义变动修版本化P1。只有真实需求无法表达/重复权威/实测不成比例成本才最小修改上层抽象，详见 [Architecture变更规则](../../ARCHITECTURE.md#selected-convergence-and-change-rules)。

## 资产去向与K门

[K0](RECORDING_HISTORY.md)、[K1](ASSETS.md)、[磁盘表](DISK.md)已交付。K1共2063行资产；[完整CSV](asset-ledger.csv)按identity检索每目录/PR/ref/建议去向；[source hash index](asset-sources.json)固定原完整JSON和详情路径，不重复提交大型JSON。6 dirty、潜在未覆盖refs/缺对象与两大数据根先保留；135其它Codex目录仅metadata，多数功能迁入未核实，非祖先不等于功能独有，不能由此推广为其它资产可批量关闭；本轮24项实际处置另列下段。

抽查：[K1远端表](ASSET_REMOTE_REPORT.md)的PR181/J-min建议保留原数值/transport失败/unknown，由Q3定点吸收；PR178建议逻辑归档并定点迁适用决定，语义迁入未核实、仍open；[归档表](asset-archive-proposals.json)中的p1-codex-closeout-2026-10-16恢复包原地保留bundle/WIP，action_executed=false，旧pass未重跑。其他指定资产从CSV查，详情用source index原路径核对。

**K3表24项已按批准完成：24/24 exact tag远端核验后关闭，未合并；branch/worktree/data保留。** [逐项执行快照](K3_EXECUTION.json)固定真实head/tag/closed_at。 19项以后复用逐条见[链路用途表](CHAIN_PURPOSES.md#k3历史复用来源按链路逐项定位)；其中#141/#147/#150/#157/#158/#161/#163在Q-CHAIN-0与Q3开工前必查。已合topic自动删branch与新Mod先备份安装的单独授权不扩大到历史资产删除。 K4由未读历史的新agent只读本入口/链接实际回答十问；既有定向复验Q5/Q9通过，目前9/10，B实际操作单仍缺；本轮定义/why/云/工作台路由还须root/K4复核。新人能查建议不等于K3已完成，文档已存不等于整K通过。

| 新人问题 | 唯一路由 |
|---|---|
| 目标/主线 | 本页目标与范围书 |
| 采集到评估owner | 上层链路、Components |
| 录整局/转P1 | 格式与采集、K0、范围书；B预检仍缺 |
| 双Host与等价 | P1第11节、A根因表；完整公开图+选择/承诺+terminal，诊断非等价 |
| 现行/归档格式 | 本页格式表、P1第10节、Full-Run chain |
| Annotator/Source3/4状态 | K0及功能表；当前B实跑门独立 |
| 指定3历史PR/目录去向 | 完整asset CSV、source index、K1详情；建议/执行分别回答 |
| 训练读何处/冻结线 | 上层链路与格式段；immutable来源→STPD admission/Input/Target |
| 差异/例外 | A根因表/P1第17节；不再以旧CURRENT/旧T1–7作当前清单 |
| 问题改哪层 | 本页判层与Architecture/ADR0016 |
