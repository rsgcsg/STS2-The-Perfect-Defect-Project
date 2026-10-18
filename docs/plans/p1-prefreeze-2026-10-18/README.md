# P1 训练前收敛：项目地图与当前执行入口

第16轮，2026-10-18。沿用 [Architecture](../../ARCHITECTURE.md)、[Components](../../COMPONENTS.md)、[ADR0016](../../adr/0016-agent-composition-and-execution-request.md) 和 [Document map](../../DOCUMENT_MAP.md)；本页是它们的任务导航，不另建协议、全局registry或治理检查器。**当前还未冻结：K0/K1/K2文档已交付，K3未批准/未执行，K4新人验收发现的问题正在修。** 最终冻结PR仍需用户批准。硬截止2026-10-20 16:30 Brisbane，未完成交剩余清单，不降低门槛。

历史查过：四份既有框架、设计沿革、旧19项任务、各组件/Python地图、Full-Run data chain、Source3/4、Data/Provenance、PR25 rc4原报告、K0/K1及当前冲刺。复用已有owner、采集链和来源证据；旧green、旧派发和旧资格不转贴当前组合。

## 目标

长期目标是能打完整局、持续变强的Agent；下一阶段要在固定条件下可复验地优于随机。当前先完成P1技术组合与历史收敛，再做Q1评测、Q2用途/划分、Q3输入适配和训练链。P1是当前环境合同，不是永久唯一格式。详见 [范围书v0.3及三幕覆盖表](SCOPE_v0.3.zh-CN.md)。

## 上层链路

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

**冻结线**在“不可变P1记录及校验/比较工具→导入数据集/Input/Target”之间。本次固定协议、游戏DLL、Reference Mod、Managed、SDK/Host驱动/Runtime端口、采集/转换器、校验器、产品路径及范围文档的确切发布身份；以后在线Agent也消费该冻结P1。Q1方案、Q3词表/适配器、数据集、训练配置和Model不在本次冻结。r1.x修复仍需数据影响分析/重新派生，能力或信息变化由r2 owner处理。

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
| Source4 | source-v4/bundle-v4、frozen public-basis/全部Reads/carrier/Task | 独立Evidence reader→共享SDK转换；report-v2逐capture matched/context_only/unresolved |
| 当前P1 Agent/chooser | **目录** `sts2.policy-runtime/agent-session-run-1`，profile `p1-global-r1`，adapter `p1-session-bridge` | `tools/p1-suite.mjs`→bridge→外部stdio Agent→现有Runtime；events.jsonl、global_source原件、frame/Consume/ACK/Receipt/Result与封存清单；独立于Annotator/Source4 |
| 旧P1直接记录 | **单文件** `sts2.p1/episode-record-1` | 原converter/reader的独立保留lane，不是当前Agent CLI唯一格式 |

当前CLI按 `sts2-p1 replay --run-directory`验Agent目录，typed证据由 [Evidence Agent-session reader](../../../components/evidence/README.md)独立核验。研究训练从已获来源资格的immutable原件经 [STPD admission](../../../python/docs/DATA_AND_PROVENANCE.md)读入；Source3与direct-Agent样本各走原reader。P1新Q链消费者仍须在冻结后验，不能以目录可验推出训练已可用。

Source4 `complete=true`仅指声明的**bounded前缀**原件全部分类且无必需事实缺失/排除pause，不等于整局；natural_terminal_observed单列。chooser真人会同时看游戏画面，不能声称只看P1观察；原生Human操作另按Annotator lane记录。任何源只要按明确合同可转换成所需轨迹即可支持环境采集，不限Source4/Human/鼠标。

录一整局的门：最终英文tuple预检→约15分钟短段→Close/pack/独立验→转换complete→整局→离线及Managed严格配对重放，最终≥3局来源分开列、第三幕实到、胜利行尽验或交用户定。**B最终可操作预检单与真实整局转换未闭合，不指示用户依据本页立即开录。** 当前步骤/根因/范围见下节与 [范围书](SCOPE_v0.3.zh-CN.md)。

## 状态与当前任务

已合入基准develop `60dc0a89`，与67adeccb同树；待合入产品候选 [PR202](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/202) head `0affa651` 已通过source CI39108923063和独立复核、Ready，**未合并**。merge自动删除分支与当前禁删分支要求的权限接缝由root处理，不以CI绿写成merged。本次文档分支基于该未合并候选；不改任何BOM或产品源。

Reference当前新A1 Mod为0db06ad7，Managed候选1482de5d，具体source/MVID/patch见 [范围书身份表](SCOPE_v0.3.zh-CN.md)。旧888/7513与旧Human诊断保持原身份。C的Managed S05有界save→父正常close→fresh冷启完整公开恢复相等→新绑定实际前进已通过；Reference存档仍待用户窗口，S03 incomplete/S04 fail保留，不推广为任意checkpoint或整局资格。

## Known differences and next gates

**差异的实际位置/owner/修复与未验状态只从 [A线根因表](MANAGED_DIFFERENCES.md) 查；例外只从 [P1第17节](../../protocols/P1_GLOBAL_R1.zh-CN.md#17-登记的协议例外必须经用户审核才能新增) 查。** P1第18节与旧Round12仅作历史路由，不能替代这里的当前门。

- A：保留完整公开图/选择/承诺/终局比较。旧Human01 occurrence41同typed卡原4/新6，不能改原件；其它description不能推广为同因。药水顺序原事实不改为无序；比较器500000上限返回incomparable不能当equal。selector hidden、shop detail尾部集合等source修复仍需新的真实范围核验；simple-grid父context、完整terminal UI和其它公开差异按表处理。A02 Human02原425 ENOSPC技术停止，未到终局；已有诊断到loss也不是严格重放。
- B：[K0历史](RECORDING_HISTORY.md)/[成本表](RECORDING_FORMATS_AND_COST.md)确认成熟采集/Close/pack可复用。Source4 legacy selector装配、SDK sourceMapping缺selector-follow需忠实反例后按owner修验，估6–16人时，整局另计；高成本旧转换16–48+人时先保留不做。新Mod+英文+记录器预检、用户短段、自然终局整局complete与严格Managed重放仍缺。
- 最终门：[范围书v0.3](SCOPE_v0.3.zh-CN.md)逐幕列机制和到达办法。最终候选英文≥3Human整局、第三幕/胜利行、完整严格重放、Managed40/Reference20、Workbench短旅程及R20独立包两Host、文档closeout与K均未齐。旧中文Human、Agent预检、locator诊断、恢复片段和旧批次不拼成最终分母。

问题判层：先最短原始反例→原生事实/生命周期修Native Foundation/Host；公开绑定/Reads/原生投递修Connector；仅表示/运输且语义不变修原SDK/port/Input；Agent取得/等待/选择变动修composition；Input/Target/模型/recipe变动由研究owner；P1合法信息/粒度/失败语义变动修版本化P1。只有真实需求无法表达/重复权威/实测不成比例成本才最小修改上层抽象，详见 [Architecture变更规则](../../ARCHITECTURE.md#selected-convergence-and-change-rules)。

## 资产去向与K门

[K0](RECORDING_HISTORY.md)、[K1](ASSETS.md)、[磁盘表](DISK.md)已交付。K1共2063行资产；[完整CSV](asset-ledger.csv)按identity检索每目录/PR/ref/建议去向；[source hash index](asset-sources.json)固定原完整JSON和详情路径，不重复提交大型JSON。6 dirty、潜在未覆盖refs/缺对象与两大数据根先保留；135其它Codex目录仅metadata，多数功能迁入未核实，非祖先不等于功能独有，无可立即批量关闭结论。

抽查：[K1远端表](ASSET_REMOTE_REPORT.md)的PR181/J-min建议保留原数值/transport失败/unknown，由Q3定点吸收；PR178建议逻辑归档并定点迁适用决定，语义迁入未核实、仍open；[归档表](asset-archive-proposals.json)中的p1-codex-closeout-2026-10-16恢复包原地保留bundle/WIP，action_executed=false，旧pass未重跑。其他指定资产从CSV查，详情用source index原路径核对。

**K3未批准/未执行；本次仅导航归档标签，无物理移动、关闭PR、删除branch或数据。** K4由未读历史的新agent只读本入口/链接实际回答十问；首轮已发现B操作单、差异旧路由、记录lane混淆，后两项本次修文档，B仍是实际缺口。新人能查建议不等于K3已完成，文档已存不等于整K通过。

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
