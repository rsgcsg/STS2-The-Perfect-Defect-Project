# P1训练前收敛：专题资料与原能力去向

**当前任务、分工、候选、时间窗口和下一步只从[CURRENT](../../memory/CURRENT.md)进入。** 本目录维护P1专题的合同、历史范围和功能/资产去向，不另外派工。工程实施授权不等于具名PR合入、发布、冻结、数据用途或费用批准；各项以最新直接用户指示和准确任务对象为准。

日常录制、数据管理、训练控制、模型/Agent、评测和维护操作从[根操作导航](../../DOCUMENT_MAP.md#按我要做什么进入)进入现有指南。本页不复制命令，不让旧16:30检查点、旧T0“尚未完成”或历史同候选门重新成为当前任务。父能力未验收仍保留在CURRENT链接任务及原功能表，不能靠移动目录/归档标签消失。

## 目标与职责

长期目标是能真实打完整局并持续改善的Agent；P1是当前可替换的交互合同。正式首训用GPU，当前工程/真实数据/策略质量分别验收。训练前须完成职责、唯一导航、支持操作SOP、历史复用、实际接手走查及同PR维护的收敛；这不要求实现已明确延期的Continue、信息曝光修订或尚无资料的学校资源。

[ARCHITECTURE](../../ARCHITECTURE.md#responsibility-refinement)拥有职责及必要局部细分，[COMPONENTS](../../COMPONENTS.md)拥有实现归属；[唯一链路图与requires/provides](CHAIN_PURPOSES.md#唯一上层图与连接要求新任务入口2026-10-19)连接在线、独立记录和研究闭环。职责树不是强制进程流水线，Model不等于完整Agent；GUI、CLI和API映射同一业务owner。

## 协议和格式从哪里查

| 要确认什么 | 原owner与边界 |
| --- | --- |
| 此刻公开信息、完整动作、承诺和失败/unknown | [P1全局合同](../../protocols/P1_GLOBAL_R1.zh-CN.md)，不是Model Input或底层游戏规则 |
| Runtime与完整Agent的consume/query/choice/control | [Agent-session端口](../../../components/policy-runtime/docs/AGENT_SESSION_PROTOCOL.md)；NDJSON仅是实现 |
| 原件保存了什么、顺序/缺口/身份如何表达 | [Full-Run记录链](../../FULL_RUN_DATA_CHAIN.md)、[Annotator地图](../../../components/annotator/docs/DOCUMENT_MAP.md)；不同lane保原义 |
| 原件怎样进入当前P1派生 | [P1 originals requires](../../protocols/P1_GLOBAL_R1.zh-CN.md#41-录制原件派生p1的requires用户已批准)与[采集/转换指南](../../ANNOTATOR_COLLECTION.md) |
| 模型输入、目标、谱系和训练用途 | [Data/Provenance](../../../python/docs/DATA_AND_PROVENANCE.md)、[P1 workload](../../../python/docs/STRUCTURED_WORKLOAD.md#p1-n-only-m-std-workload)；存储成功不授准入 |
| 为什么选定、什么被替代 | [DECISIONS](../../DECISIONS.md)、[ADR0016](../../adr/0016-agent-composition-and-execution-request.md)、[ADR0017](../../adr/0017-p1-forward-decision-profile.md) |
| 旧范围和历史差异怎样保留 | [范围定义及覆盖](SCOPE_v0.3.zh-CN.md)、[Managed差异](MANAGED_DIFFERENCES.md)、[P1例外](../../protocols/P1_GLOBAL_R1.zh-CN.md#17-登记的协议例外必须经用户审核才能新增)；旧快照不覆盖最新用户决定 |

当前原生P1采集沿成熟Human accepted动作、同次完整PB1、Close/pack、共享核离线派生和现Store；Source4格式/失败/实际旧consumer保留，不恢复为当前P1强制主线。Source3、canonical3/bundle3、兼容record2/bundle2、Runtime Agent-session目录以及旧episode-record单文件，分别按其合同读，不能仅凭同为JSON或目录可验互授Human、P1完整或训练资格。详细历史只在[录制历史](RECORDING_HISTORY.md)与[格式成本](RECORDING_FORMATS_AND_COST.md)，不在本页再维护一份版本清单。

## 每项功能和旧成果的去向

[原功能表](FEATURE_STATUS.md)及[104行CSV](feature-status.csv)是唯一功能入口。现行表示职责/合同/保留机制，不代表所有场景当前资格；归档表示保留原用途/实际consumer或reader，不代表代码已退休；废弃仅指明确退出当前权威；未完成保留原验收。当前能力缺口不得仅改标签成为未来，旧PASS也不能赋给新组合。

[原资产账本](ASSETS.md)及[2063行CSV](asset-ledger.csv)提供2026-10-18盘点范围、唯一成果线索和建议去向；目录/ref/文档行不是功能数或可释放空间。[source index](asset-sources.json)定位原详情；[远端表](ASSET_REMOTE_REPORT.md)、[本地表](ASSET_LOCAL_REPORT.md)和[归档提案](asset-archive-proposals.json)保建议/已执行区别。操作当前资源前须重新核进程、dirty、ref和消费者。K3已批准24项的真实关闭/tag回执保存在[K3执行快照](K3_EXECUTION.json)，不能推广为其余PR/数据可删除。

遇到同类问题直接从[已有解法](../../RESOLVED_ISSUES.zh-CN.md)找到native-ui、Recorder连录、Store/R2、旧GPU/恢复及失败的代码/证据和适用范围，不要求每个新人重读聊天。旧PublicM0/M2、Qwen、Full-Run、Source3和真实Hub/团队caller按原合同保留，不盲目全迁或假称全部已接当前P1。

[文档盘点索引](DOCUMENT_INDEX.md)、[归档索引](ARCHIVE_INDEX.md)与[文件CSV](document-inventory.csv)保原develop@60dc的401文件元数据范围；标记现行不代表该文件每句话适用今日。日常只沿根导航进入实际操作owner，新文件不追填旧盘点分母。

## 接手与后续维护

新人按README→CURRENT恢复当前任务，再经根导航完成一条适用操作、找到一个历史解法、选最低忠实检查并定位合同/文档改动归属。[共同规则](../../WORKING_RULES.zh-CN.md)给任务和停止条件，[AI协作](../../AI_COLLABORATION.md)给复用/新开与续接。走查失败修原入口或操作段，不加全史阅读门。

[后续录制/恢复/Continue专题卡](POSTFREEZE_RECORDING_WORK_CARD.zh-CN.md)保最初提案和来源；当前异常录制恢复与已延期Continue分别由CURRENT链接任务明确，不按该卡旧“冻结前不实现”自行停工或把Continue重新抬为首训前置。代码/合同/入口改变时，同PR维护对应原指南和决定，CURRENT只由集成者更新当前指路。
