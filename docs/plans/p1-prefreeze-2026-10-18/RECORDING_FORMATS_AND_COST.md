# K0 格式、工具、缺口与成本表

**历史查过**：现行 Annotator/Source/Agent 合同、旧 STPD 与现行 Python owner 文档、Workbench 源、既有录制摘要及本机 metadata；已有采集、封存和数据服务直接复用。此表是只读调查，不批准改造旧数据。

本轮后续实查补充：现行 [PlatformBundle3SourceAdapter](../../../python/stpd/fullrun/platform_bundle3.py) 已读取 canonical3/trace4 并产 ResearchDecision，[public_bc](../../../python/stpd/fullrun/public_bc.py) 与 historical M0 也有实际消费者；下表 record2 importer 的能力边界不代表没有独立 canonical3 reader。M0 是模型视图，raw 无需与 P1 一一对应。当前继续复用 Source4 原件与共享 SDK 逻辑 span；用户批准的 E3 源码已整合，187项相关测试、22项CLI检查通过，新实装/录制验收仍待。旧数据转换成本高可暂不做，历史调查与收敛继续服务当前主线。

估算为熟悉该代码的一名工程师的专注人时，包含适配/忠实回归/离线核验，不含新真人局、完整游戏运行、等待用户、云部署或训练。**各行共享工作，不能相加**。成本是设计前估算；缺失事实是不可恢复的能力边界，不是多花时间就能补出的字段。保留原版本/来源与 unresolved，只对已留事实派生。

| 格式或工具 | 现状、包含什么 | 转 P1 缺什么 / 可复用什么 | 估计成本与建议 |
|---|---|---|---|
| Annotator semantic trace4 | 当前 Human 因果原件：H/S/后继不同角色、原生生命周期、disposition、不可变对象引用 | 可复用真实输入/执行/后继证据；不是完整 Source4 PB1、所有同实例 Read 或 P1 意图序列；须新 schema-pinned adapter 与可推导范围声明 | 有界覆盖24–48+人时；缺的事实仍 unresolved。旧数据先不转 |
| canonical3 | 当前 durable proved decisions，root/parent/native lineage 与原生选择器；不是全部点击 | 可复用 exact choices/lineage；要桥接 P1 聚合观察、机械多步意图/commitment、未进入 canonical 的上下文 | 有界覆盖16–32人时；不能凭行数承诺完整整局 P1。先归档 |
| bundle3 + 官方 pack/Evidence | 当前默认传输：原 raw、canonical export、audit、profile、hash inventory；可装失败证据 | 封装可直接核验复用，转 P1 的语义工作仍取决于 raw 中保留什么；不能把 bundle3 改名 bundle2 | 单独包解码/核验接入2–4人时，trace/canonical适配另计；不建新封装 |
| decision-record2 / bundle2 | 兼容投影；已有 STPD importer，完整 pre/catalog/chosen/successor 和 Read，但省略部分 native-input/selector 决策 | 可复用受限单步记录；缺少被省略的动作/浏览/整轨迹，无法无损还原 canonical3 或 Source4 | 有界投影16–32人时；全轨迹不可保证。当前不迁移 |
| record1 / bundle1、旧 trace1–3 / canonical2 / ledger1–2 | 显式历史读取，旧来源/能力仍按原合同 | 已有 archival readers 与 V1 STPD corpus；版本/公开事实差异较大，不能借当前 Host 重捕 | 每个被选版本24–48+人时且可能不可完整转换；保留库存，按明确消费者再选 |
| Source1/2 | 同一通用采集引擎的先驱格式，来源可声明 Human/UI agent/protocol/unknown | 生命周期/存储经验可复用；原始次序与 epoch/输入基底能力依版本，缺 Source4 PB1/Read 集 | 有界适配24–48+人时；不统一回填新字段，先不做 |
| Source3 / bundle-v3 | 当前通用有序观察/输入/epoch/segment/fence；实际 pilot 27 publication/8 input，typed structure pass但原 SDK join fail仍保留 | 引擎、原始顺序、Pause/Resume、Close/pack直接复用；缺独立 PB1/all-Read/native facts 的新能力，旧shape不保证整轨迹 P1 | 有界转换16–32人时；不能保证补全旧轨迹。新 P1 采集明确选 Source4 |
| Source4 / bundle-v4 / source4-to-p1 report-v2 | 同引擎保留 PB1/all advertised Read实例/carrier Task，官方 converter 已复用 SDK；888一EndTurn动作通过，新0db零输入不算阳性 | **当前首选**。先修 SourceSnapshot selector事实装配＋SDK selector-follow，再用真实/忠实机制原件验；complete只表示声明 bounded prefix全分类，不等于整局 | 最小两接缝＋定向核验6–16人时；新增机制/整局另计。优先投入此处 |
| 当前 P1 Agent CLI：agent-session-run-1；旧 episode-record-1 独立保留 | 当前 tools/p1-suite→p1-session-bridge→原Agent CLI，profile=p1-global-r1；AgentRunEvidence目录存global_source、原Agent frame、Consume/ACK、Dispatch/Receipt/Result与封存inventory。episode-record-1是独立保留的旧/offline lane，不是当前合并格式 | 当前目录由global-replay核原件，保留exact core；**直接AgentRun独立于Annotator/Source4，二者不是前提**。旧单NDJSON仍用原convert消费者。源：[CLI createSession](../../../components/policy-runtime/src/cli.ts#L181)、[目录replay](../../../components/policy-runtime/src/global-replay.ts#L27) | exact版本当前直验1–3人时；跨revision有界adapter4–12人时起，不承诺语义等价。旧episode-record消费者另行保留 |
| 其他 AgentRunEvidence / sampled inputs | 原 Agent/InputSpec/原始样本 C、ACK/directive/result/source identity；Source3是可选overlay | 可复用真实程序来源、offered bytes和joins；native-logical/text等不自动等于PB1，按实际留存字段决定可推导范围 | 已有PB1完整输入4–8人时；仅legacy输入16–32+人时且可有不可恢复缺口 |
| STPD HUMAN_CORPUS / ResearchTransition / Parquet | 已有严格原件验证、registry、去重、完整run split、B0、immutable snapshots和训练分权；本机4 bundle1/5重叠快照可查 | **研究消费者而非录制格式**；复用登记/来源/分割。要转 P1须回到原bundle，训练列不具备缺失公开事实；record2 importer不含canonical3全决策 | 库存/来源接线2–4人时；真正转换按原件行计，当前不逆造 P1，也不更改训练范围 |
| B pipeline / Workbench 采集页 | 成熟配置→录制→Close→pack→delivery receipt/接收；本机原生页支持Source3/4、显式来源、Pause/Resume/ChangeSource/Close，Source3默认 | **工具/编排层**，不是新记录格式。复用正常launch、持久root binder、loadedidentity、原Close owner；离线P1无需新账号/上传。旧fixedtool支持矩阵应核对，不能假定v4自动兼容 | 持久配置/路径readback与现有操作链预检1–3人时（实际操作待主任务安排）；不重写Workbench/Recorder |

最低可交付顺序：已有正常原生启动/持久root → 显式Source4与真实来源 → 两接缝忠实回归/修复 → 代表输入转换 → 用户已定短段/英文整局机制与实际终局。独立 storage seal、P1 complete、Human来源、整局/第三幕/胜利、研究训练资格分别说明，不用一个状态替代其它事实。

主要出处（以下均为本轮实读 source/docs 或已有调查报告）：

- [格式总表](../../FULL_RUN_DATA_CHAIN.md#L8)、[canonical3/record2关系](../../../components/annotator/docs/DATA_CONTRACT.md#L191)、[bundle3合同](../../../components/annotator/docs/SESSION_BUNDLES.md#L3)。
- [Source3/4与complete语义](../../../components/annotator/docs/SOURCE_SESSION_PROFILE_V3.md#L189)、[SourceSnapshot接缝](../../../components/connector/host/PlayerEnvironment/Observation/SnapshotBuilder.cs#L319)、[source matcher](../../../components/connector/sdk/typescript/src/p1Global/sourceMapping.ts#L249)。
- [Human corpus ownership和版本](../../../python/docs/HUMAN_CORPUS.md#L33)、[多条数据路线](../../../python/docs/DATA_AND_PROVENANCE.md#L13)、[B日常流程](../../../python/docs/B_PIPELINE_HANDOFF.md#L25)、[partial资料与训练界限](../../../python/docs/B_PIPELINE_HANDOFF.md#L471)。
- [Workbench Source4显式选择](../../../python/spireagent/console/project.js#L4057)、[原生配对API同入口](../../../python/spireagent/workbench/native_workbench_api.py#L413)、[NativeTasks同owner](../../../python/spireagent/workbench/native_tasks.py#L210)。
- [Agent原件](../../../components/policy-runtime/src/evidence.ts#L24)、[已有P1原Host bytes记录](../../../components/policy-runtime/src/p1-global/recorder.ts#L5)、[已有P1离线重建](../../../components/policy-runtime/src/p1-global/convert.ts#L8)。
- [历史性能与身份重复hash根因](../../evidence/PLATFORM_RECORDING_HOTPATH_PERFORMANCE_SOURCE_CLOSEOUT_2026-09-01.md#L34)、[缓冲写与durable Close](../../../components/annotator/docs/SEMANTIC_EVIDENCE_STORAGE.md#L100)、先前B历史详细报告，K0不重读raw (`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/p1-sprint-B-20261018/P1-sprint-B-history-2026-10-18-details.md`)。
