# P1 r1 训练前冻结定义 v0.3（仓库同步副本，待用户批准）

本页同步唯一共享待批定义；canonical精确路径为 `/Users/fire/Desktop/SpireAgentProject/claude-handoff-2026-10-13/P1_r1_SUPPORT_SCOPE_v0.3_2026-10-18.zh-CN.md`，其SHA见输入索引 (`/Users/fire/Desktop/SpireAgentProject/claude-handoff-2026-10-13/FREEZE_INPUTS.json`)。两处按同一次文档集成同步，不独立派生要求。它不是冻结批准，也不批准训练、上传、云费用、历史资产处置或新的协议取舍。用户已定的要求继续执行；尚待决定的范围在第8节明确列出，不能把Claude建议或本次文档合并当成用户已定。

**目的**：给第一轮训练一个确定、可复验的P1环境、采集/解释、独立Agent接入和可定位的项目全貌；快速复用已有成果，不扩建复杂GUI/团队平台。硬截止仍为2026-10-20 16:30 Brisbane；未完成交实际剩余清单，不暗改标准或延期。

历史查过：既有Architecture/Components/ADR0016/DOCUMENT_MAP、K0/K1/K2/K4、版本与数据owner、原用户消息的有界审计、K5和K6，以及当前已审同树源码。旧输入文档的完整原文和SHA已保留为合并附件；原记录/失败/旧资格不改。

## 1. 11条进度、依据和首训价值

以下状态是第19轮实际快照。11行是总报告的共同表头，不意味着每项细化均已获用户批准。全幕/英文/Source4整局、严格重放、最终40/20、原产品/R20与收敛已有用户依据；云具体消费门、信息复验时点/强度和逐幕硬门强度仍按第8节确认。

| # | 验收项 | 当前状态与真实缺口 | 为什么服务第一轮训练／依据层 |
|---|---|---|---|
| 1 | 全部幕Reference正确性 | **没开始**：最终候选同条件英文合格整局0/3，真实第三幕/胜利行仍缺 | 新示范公开事实与结局可靠；全幕/英文/Act3目标/尽量赢为直接要求，≥3为采纳计数口径；不是三局都赢/都到Act3 |
| 2 | Managed一致与最终40/20 | **进行中**：A03两loss完整terminal equal；选择点170/293、236/432，整轨不严格；新英文严格0/3，最终候选40/20未跑 | 保证训练/评估环境对照真实游戏；严格到底、最终40/20有直接用户依据 |
| 3 | 机制覆盖 | **进行中**：保留42+4检索行及到达办法；新候选实际覆盖待填，硬门位置集合待确认 | 把首训环境实际能力和遗漏说清；全幕不自动等于每种共同机制每幕都必须重复验 |
| 4 | 英文 | **进行中**：新6c Mod已精确load、英文可见；转换预检尚未PASS | 与第一轮文字输入一致；用B短段/整局共用证据，不再暗增额外一局 |
| 5 | 采集/转换路径 | **卡住**：native_input首战EndTurn阳性；initialpublication1失败/gap1的启动语境合同待用户选，首mapvote的NativeSourceWitness漏附属普通修复，均未当预检通过 | 原生示范能形成可核验P1来源；report-v3整局scope源码已交，不能当真实整局转换已过 |
| 6 | API/库/CLI产品路径、Workbench薄映射、R20 | **进行中**：已有GLOBAL应用源码/接口、K6定位与外包材料；最终tuple实际短验/R20未过 | 训练后完整Agent能从同一入口运行、Stop、查原件；保留原短验要求，不做复杂GUI |
| 7 | 文档与closeout | **进行中**：本定义待批；CURRENT本轮同步合入/装载/缺口，docs检查与复核待完成 | 消费者能找到准确身份、操作与差异，不再双份范围书 |
| 8 | K收敛 | **进行中**：K0/K1/K2/磁盘/K5/K6材料已交；K4既有9/10，B操作单仍缺；K3已交24行具体去向表，root已询批、用户待答，未执行 | 入口定位owner/状态/出处/去向；不是承诺全史穷尽或重验所有旧结论 |
| 9 | R2+Modal最简云 | **进行中**：已有存储/派发机制与K5方案；新P1→R2回读→Modal消费尚未实测，上传/费用需另批 | 首训能读同一获准数据；用户已定最简方向，具体对象/只读探针与细门待确认 |
| 10 | Agent公开信息复验 | **没开始**：仍保留复验要求；采样/机制范围与是否全部在冻结前完成的时点待确认 | 防止人看游戏能选而Agent缺必需公开事实；不把“之后再复验”自动升级为无界审查 |
| 11 | 组件/协议/格式的位置和原因 | **进行中**：已补三路链及逐行用途/首训价值，等root/K4复核 | 避免重复实现、错误依赖和权威混淆；复用已有组件框架 |

依据[审计附件](FREEZE_AUDIT.md)：U14是全幕/英文/采集/严格/40+20/产品要求；U15采纳提案§4/§6计数和时刻；UK要求K及资产批准边界；U19要求首训导向、API/库/CLI、GUI薄映射、最简云、审计合成与11条表。原消息ID/记录SHA见审计附件。旧中文Boss“最多3局”窗口不是本次英文“至少3局”的同一数字依据。

## 2. 三路链与本次交付边界

```text
在线：完整Agent ↔ Agent程序端口 ↔ Policy Runtime ↔ SDK(P1) ↔ Connector ↔ Host/STS2
      Host Runtime管启动/隔离/恢复；执行请求/权限/预算/Stop与证据关联横切
      Reference=真实游戏+Mod，Managed=无界面Host路径；Mod是包装

独立记录：原生变化/输入 → Annotator Source4原件 → Evidence → 共享SDK解释为P1轨迹
          Agent/选择器 → Runtime AgentRun原件 → 原typed verifier/replay（独立路线）

研究：获准来源/用途 → Projection/InputSpec/TargetSpec → 数据集/划分/TrainingRun
      → checkpoint/Model → 完整Agent组合 → 在线新运行 → 固定评估
      R2承载对象，Modal承载获批作业；不决定来源/游戏/研究准入语义
```

每个组件、协议、格式的位置、为什么存在、首训价值与保留/占位状态，见项目地图的[链路用途表](CHAIN_PURPOSES.md)。现行API/库/CLI是主操作面；GUI映射原应用用例，保留已有显示/确认/配对适配，不扩复杂GUI/团队/Hub。

“P1记录→研究消费”是本次交付的**阶段线**，不是已经冻结一个统一数据消费合同。固定的是发布组合、在线P1语义、可校验原件及解释身份；Projection/Input/Target、来源admission/use、数据集/划分/门槛、词表、训练配置和Q1方案仍属Q链owner后续决定。本批选用冻结P1，不废止其它历史合格来源或actual consumer。

既有Q3第一批第3版 (`/Users/fire/Desktop/SpireAgentProject/claude-handoff-2026-10-13/Q3_BATCH1_PLAN_2026-10-17.zh-CN.md`)已获用户批准在P1完成后执行：1a六Agent工程链、1b既定对照问题保留；本定义不撤销或重设它。P1前仅低优先级Q3-M0；实际数据划分/门槛、一次Modal基线之外费用和配置变更按原限制确认，不借文档合并提前fit。

## 3. 当前组合、发布身份与变化原则

当前develop `ea7c14e8969006fa0e470678198ef94aca471f90`，tree `4b796997314f63b59557d6d8daa7f3b6ad14e67d`与已审eafa9a7c相同。#202实际merge `beca367cf72516f982031423d3266c9536925dd2`；#204实际merge即ea7c；#203的808e祖先经#204完整进入develop，GitHub标MERGED，不是第三次独立develop merge。#204 source CI39115695661与最终integration CI39120207920均PASS；这些是源码/集成证据，不是新英文整局。

| 层 | 当前事实及冻结时要固定的内容 |
|---|---|
| 游戏 | 标准单人Defect/A0、正式新记录英文；DLL SHA `9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4`；最终模式/语言/模板和版本必须对应原件 |
| Reference Mod | 已安装/加载 `6c6b885fce9fcf56b69783ff9e29bae6f24e6521524af69cc66a672c94b3fb13` / MVID `5b2d62dc-c8fe-4a90-9304-2b0447880ab1`；编译workspace10215609原身份保留。回滚备份 `2026-10-18T10-39-06.814Z`；真实英文/原生输入已见，采集转换闭环未过 |
| Managed | A03实际native `a70d8de18578cc2666ec854d8d8eb6bb85868317335fb67fcfbb7151133d7f66` / MVID `c9acc642-499d-4af2-ba24-2e90db5ef8cb` / patch `54bfa3a3e8beb21ebf72b62da4675c9ec6953764ffa746340990931e71e369dc`。两局A03执行workspace cd47f09c，不追贴ea7c |
| P1/SDK/驱动/Runtime | P1 r1.3唯一语义owner；固定共享核心/Host mapping/Agent端口/比较器和实际发布包身份。typed tooltip用原全局双射，仅已声明typed shop detail尾部无序，usable_potions有序，500000工作上限保留 |
| 采集/转换 | Source4原profile/bundle、capture身份、转换器/core、选择器来源/显示v4与原始记录grammar；report-v3的bounded/whole_run分别核验 |
| 模板/显示 | Reference eng模板实际payload，Managed en；隔离Reference默认instant，native-ui/headless为独立显示维度。原888六格短验保持旧身份，不给6c转贴资格 |
| 校验/产品/文档 | 原verifier/replay/comparator、API/库/CLI调用链、外包R20、范围/覆盖/差异和K入口；冻结时填实际成功原件，空格不当ready |

当前源组件元数据如下（ea7c原platform-bom；Runtime6a448仅终局fixture同步，实际A03生产运行仍属原cd47绑定）；最终发布沿现有BOM/组件manifest与安装/加载回执填写，不重建新身份系统。旧Workbench component元数据是退役Platform诊断壳，不代表正式Python Application的产品验收。

| 组件 | 版本 | 当前已审source / scoped digest |
|---|---|---|
| Connector/SDK | `1.3.0-rc.25` | `137d1c3c64961d6df47233ab3c3e37d35f54cb23` / `4826537d68dfdfc661cb0a9a9faf6a4de4f4e2e55942348a83ebe2a7ce217f09` |
| Host Runtime | `1.1.0-rc.31` | `316f6d77094204213bf1992f5ab8f75453f7bf89` / `b199f5e77ad9d8cf7436337de1dae57dc93024246bcb76c215192c51870e8d48` |
| Policy Runtime | `0.1.0-rc.37` | `6a448289a1db46bb710d7537a877d6e3142bd972` / `5c1948b3e45cf589e6a16065e8ee6ed672b9d767df9579d5d0199f11360815f4` |
| Annotator | `0.3.0-rc.19` | `2859aa6bb5b8d5faf139051aee71676c7d8ae36c` / `4ac47a72bbf90f1e9146de8a8d62d753058dec1caf9ed275ef34a127edc52fc1` |
| Evidence | `0.1.0-rc.31` | `c60c3691fe45590a12eb10ff99a130b2903c6623` / `6418df7cf4f65460b2f619506d379c21c670e8dfaa780dcece757b4c82f6ebb0` |
| 旧Platform诊断API | `0.1.0-rc.3` | `01debc74ba24c61c5cbfbe9023ac8d4b3a7e9bc3` / `2accc441c50968fc2f8ca0bafba1fb7d9e71eaa47dc24fc60d5a4c1b768f80b4` |

版本修订按真实公开语义、实际consumer兼容、解释和数据影响决定，不能机械用“r1.x就事实保持/r2才会影响数据”判断。原件/producer身份不改，新解释另记；r1.x也可能须重派生/重验。冻结不永久禁止架构演进；只有当前发布组合受批准的范围约束。

## 4. 记录、转换、比较与计数

Record grammar ≠ P1环境合同 ≠ Agent程序端口 ≠ Model Input/Target。通用环境采集不限Human或鼠标；直接Agent原件独立于Annotator/Source4。当前Runtime写 `sts2.policy-runtime/agent-session-run-1`目录（global_source、frame/Consume/ACK、Receipt/Result），旧 `sts2.p1/episode-record-1`单文件保留原converter/reader；不能混成一个当前格式。

Source4 report-v3可显式 `--run-continuity`选择运行域：原菜单和域外事实仍保留并有原因，不能借scope排除运行内partial、unknown、gap、遗漏输入或终局。`complete`表示所选/声明范围的保留capture分类完整；`whole_run`另需真实new-run起点、连续来源及完整自然terminal，最终真整局证明不能由bounded complete、Close或fixture替代。Human来源、完整性、P1解释、轨迹/行为监督和研究训练准入分别由原owner判断。

正式计数继续用同一最终候选、英文、≥3完整Human局，环境录制/选择器分来源列；自然loss可算完整局，不能当第三幕/胜利覆盖。不同候选/语言、locator诊断、原not_started跳过、Agent预检和save恢复片段不拼成最终三局；存档续玩保留父局/存档SHA/new continuity，数据按同谱系分组。

严格比较固定tuple/语言/角色/难度/种子/起点，在Managed重放Reference真实选择，核完整公开观察、目录、选择、承诺及完整terminal。原全局实体双射、重复实例、owner/关系和文本保留；只有合同已声明集合可无序；different/incomparable/unknown/预算/技术中断/自然终局分别计。

旧中文Reference缺陷按最新root转交的用户指令登记，新英文整局承担严格验收，真实Managed缺陷继续修。A03两个终局equal是实质进展，但中间170/293、236/432，不是整轨严格；不能把所有description差异都推广为已证旧缓存。原Human01 occurrence41同typed卡4→新6、药水顺序原事实均不改。终局后Return/timeline仍是产品限制；当前P1胜负终局不新增为必须执行结算后导航的门。

## 5. 工作台与最简云

按[K6定位](WORKBENCH_POSITION.md)，工作台是现有Python Application用例层及可选人类面板，复用环境/完整Agent登记、prepare/start、one_step、Stop、查原件；GLOBAL已有API/库/浏览器入口，CLI没有同名register子命令，不虚构命令。`model stop`停Agent，`project stop`停Application服务，不混用。原Workbench短验和外包R20两Host要求保留；现成2684B外包与harness可复用，材料/源码不当实际验收。复杂GUI/团队系统本次不做，既有薄映射/信任边界保留。

云按[K5最简建议](CLOUD_MINIMAL.md)复用storage库/现有push/pull：原始/P1产物及来源清单→ArtifactStore→R2→完整回读同hash→Modal消费获准同一对象。R2分块/manifest库可直接用，不强制Hub登录；现Modal派发器消费旧FeatureJob/TrainingRun，不自动等于新P1训练reader。K5查到的本地合成分块检查只证明局部机制，不是当前R2/Modal实测。

Hub、Cloudflare登录、Evidence delivery、campaign/团队账号和Workbench云页面保留现状/接口，不扩建也不先迁全系统。两个历史大数据根合计约40.286GiB逻辑大小属于保全候选，不是自动训练数据；先批具体源/目的prefix/清单/校验，再传，原地删除另批。8MiB块/原blob限额、内存archive_bundle和临时整payload复制各有范围，不能为40G迁移直接放宽全局上限。

云详细样本/读回/Modal CPU消费、上传对象及费用需明确批准后才执行；当前没有新上传/Modal消费实证。其目标是第一轮可读的最小接缝，不顺带启动训练或消耗Q3未来基线额度。

## 6. 覆盖索引（42机制行+4跨幕/终局行，硬门集合待确认）


本节保留42个按幕检索的机制行和4个跨幕/终局行，以及原位置/到达办法。它是**覆盖索引，不是“每个共同机制×每幕全部通过”的新增硬门**。共同机制、幕特有机制和实际必验位置的最终集合交用户确认；未遇项继续写到达办法，不自行删要求或增加穷尽任务。下列历史状态原义保留，新候选证据由原件逐行关联。

旧证据：H1=中文Human01，317输入、Act1 Boss后Act2 floor22 loss；H2=中文Human02，434输入、Act1 Boss后Act2 floor28 loss。M1=C11、M2=C09均为诊断新执行，分别67/293、47/432公开图相等，终局图也不同。原件索引与逐occ归因 (`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/outputs/P1-round12-Managed-diagnostic-2026-10-18-details.md`)。本表按幕核对了原轨迹的选择位置，完整索引见 历史动作索引 (`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/p1-sprint-root-20261018/historical-act-mechanism-index.json`)。索引说明原始选择/catalog与场景；只有对应Receipt/Result和后继才能证明效果完成，旧not_started不计成功。不从“打到某幕”推断所有该幕机制都遇到。

| 幕 | 必验机制 | 已有记录及真实状态 | 新冻结验收状态 | 到达办法 |
|---|---|---|---|---|
| 第1幕 | 普通/精英战：出牌、目标、结束回合、公开状态与牌堆 | H1/H2 Act1普通战实录；精英种类仍需逐场核，不从combat_turn推断 | 最终待验 | 正常推进并选精英路线；每场核完整观察/选择 |
| 第1幕 | 战斗药水使用、丢弃及槽/目标顺序 | H1 occ119/120是可用药水列表顺序反例；实际potion.use选项见78/131/158，需分场核Result | 最终待验 | 保留药水至有合法目标的战斗；验证后继与库存 |
| 第1幕 | 地图、跨房间、非地图公开map | H1/H2通过Act1并在Act2终局；H2 occ321地图→新事件 | 最终待验 | 同种子公开前缀；记录房间变更和owner |
| 第1幕 | 金币/遗物/药水奖励、满槽与离开 | 历史普通奖励已见；满槽/linked/mandatory分支未逐项证明 | 最终待验 | 保留满槽；按实际奖励分支记录，未出现不填通过 |
| 第1幕 | 卡奖领取、跳过/替代、生成卡详情 | H1 Act1 occ16/29等领取卡，occ159 REBOOT生成选择；拒绝/替代逐项待核 | 最终待验 | 战斗后自然奖励；保留完整详情与后继牌组 |
| 第1幕 | 营火休息、离开 | H1 Act1 occ100–102旧rest修复链；H2 occ136/137等rest.choose；休息具体意图核原件 | 最终待验 | 路线选营火，分别记录休息和可继续页 |
| 第1幕 | 升级选择、预览、确认 | H1 Act1 occ101/116/142/195、H2 occ166选择upgrade.choose；预览/后继待最终验收 | 最终待验 | 进入升级营火并选合法卡，核Preview/Result/后继 |
| 第1幕 | 附魔选择、预览与结果 | H1 Act1 occ75、H2 occ54选择enchant.choose；旧公开summary差异在修 | 最终待验 | 遇附魔机会保留原始前缀；不能按名字推支持 |
| 第1幕 | 商店购买、移除等服务、关闭再开及离开 | H1 Act1 occ104–108、145–147商店链，含原RNG/投影反例；服务效果待逐项核 | 最终待验 | 有金币的商店路线；核售罄/价格/服务后继 |
| 第1幕 | 该幕事件：选项、继续、owner与嵌套 | H1 Act1 occ49/50、74/76；H2 occ77事件及78/79八选二；不是Act2证据 | 最终待验 | 各幕真人有意走事件；每类出现的选择都校验 |
| 第1幕 | 宝箱打开、取遗物、跳过 | H1 Act1 occ110–113；H2 occ132–134宝箱动作，原H1跳过not_started另记 | 最终待验 | 路线选宝箱；不在开箱前揭示未来遗物 |
| 第1幕 | 单选、多选、生成卡、选堆/包选择 | H2 Act1 occ77事件八选二、89弃牌选择；H1 occ159生成选择，其他类型待核 | 最终待验 | 复用已发生公开前缀，其他类型仅在真实出现后登记 |
| 第1幕 | 移除/变换及后继牌组 | 旧事件/商店有入口线索；未逐幕证明全部效果 | 最终待验 | 使用自然服务/事件；核实体消失或新实体及后继 |
| 第1幕 | Boss战斗、掉落与Boss遗物选择 | H1/H2确实经过Act1 Boss；Act2/3 Boss未有合格证明 | 最终待验 | 真人目标第三幕，保留Boss战及奖励完整链 |
| 第2幕 | 普通/精英战：出牌、目标、结束回合、公开状态与牌堆 | H1 Act2 occ243起/H2 occ255起普通战实录；精英种类待核 | 最终待验 | 正常推进并选精英路线；每场核完整观察/选择 |
| 第2幕 | 战斗药水使用、丢弃及槽/目标顺序 | H1 Act2 occ310选择potion.use；场景与Result须原件验收；H2 occ323事件喝药不代替战斗用药 | 最终待验 | 保留药水至有合法目标的战斗；验证后继与库存 |
| 第2幕 | 地图、跨房间、非地图公开map | H1/H2通过Act1并在Act2终局；H2 occ321地图→新事件 | 最终待验 | 同种子公开前缀；记录房间变更和owner |
| 第2幕 | 金币/遗物/药水奖励、满槽与离开 | 历史普通奖励已见；满槽/linked/mandatory分支未逐项证明 | 最终待验 | 保留满槽；按实际奖励分支记录，未出现不填通过 |
| 第2幕 | 卡奖领取、跳过/替代、生成卡详情 | H1 Act2 occ265、H2 occ279特殊奖励；H2 occ281/315/390领取卡、357拒绝组 | 最终待验 | 战斗后自然奖励；保留完整详情与后继牌组 |
| 第2幕 | 营火休息、离开 | 尚无确认的Act2营火操作链；不从Act1迁移 | 最终待验 | 路线选营火，分别记录休息和可继续页 |
| 第2幕 | 升级选择、预览、确认 | H1 Act2 occ237–240选择upgrade.choose；不迁移Act1证据 | 最终待验 | 进入升级营火并选合法卡，核Preview/Result/后继 |
| 第2幕 | 附魔选择、预览与结果 | H2 Act2 occ252选择enchant.choose；最终完整比较待验 | 最终待验 | 遇附魔机会保留原始前缀；不能按名字推支持 |
| 第2幕 | 商店购买、移除等服务、关闭再开及离开 | H2 Act2 occ287仅shop.leave，不能当购买/移除完整链 | 最终待验 | 有金币的商店路线；核售罄/价格/服务后继 |
| 第2幕 | 该幕事件：选项、继续、owner与嵌套 | H2 Act2 occ320–323旧event→map→新event反例，原322 not_started；888定点短测越过 | 最终待验 | 各幕真人有意走事件；每类出现的选择都校验 |
| 第2幕 | 宝箱打开、取遗物、跳过 | H2 Act2 occ360–362 treasure.open/activate，需核各自Result与奖品后继 | 最终待验 | 路线选宝箱；不在开箱前揭示未来遗物 |
| 第2幕 | 单选、多选、生成卡、选堆/包选择 | H1 Act2 occ248/289、H2 occ301/346/400 selector.choose；具体类型按原scene逐项核 | 最终待验 | 复用已发生公开前缀，其他类型仅在真实出现后登记 |
| 第2幕 | 移除/变换及后继牌组 | 旧事件/商店有入口线索；未逐幕证明全部效果 | 最终待验 | 使用自然服务/事件；核实体消失或新实体及后继 |
| 第2幕 | Boss战斗、掉落与Boss遗物选择 | 未有Act2 Boss完成记录；两局均在floor22/28自然loss | 最终待验 | 真人目标第三幕，保留Boss战及奖励完整链 |
| 第3幕 | 普通/精英战：出牌、目标、结束回合、公开状态与牌堆 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 正常推进并选精英路线；每场核完整观察/选择 |
| 第3幕 | 战斗药水使用、丢弃及槽/目标顺序 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 保留药水至有合法目标的战斗；验证后继与库存 |
| 第3幕 | 地图、跨房间、非地图公开map | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 同种子公开前缀；记录房间变更和owner |
| 第3幕 | 金币/遗物/药水奖励、满槽与离开 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 保留满槽；按实际奖励分支记录，未出现不填通过 |
| 第3幕 | 卡奖领取、跳过/替代、生成卡详情 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 战斗后自然奖励；保留完整详情与后继牌组 |
| 第3幕 | 营火休息、离开 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 路线选营火，分别记录休息和可继续页 |
| 第3幕 | 升级选择、预览、确认 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 进入升级营火并选合法卡，核Preview/Result/后继 |
| 第3幕 | 附魔选择、预览与结果 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 遇附魔机会保留原始前缀；不能按名字推支持 |
| 第3幕 | 商店购买、移除等服务、关闭再开及离开 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 有金币的商店路线；核售罄/价格/服务后继 |
| 第3幕 | 该幕事件：选项、继续、owner与嵌套 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 各幕真人有意走事件；每类出现的选择都校验 |
| 第3幕 | 宝箱打开、取遗物、跳过 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 路线选宝箱；不在开箱前揭示未来遗物 |
| 第3幕 | 单选、多选、生成卡、选堆/包选择 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 复用已发生公开前缀，其他类型仅在真实出现后登记 |
| 第3幕 | 移除/变换及后继牌组 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 使用自然服务/事件；核实体消失或新实体及后继 |
| 第3幕 | Boss战斗、掉落与Boss遗物选择 | 未有实际Act3记录；不从前两幕迁移资格 | 最终待验 | 真人目标第三幕，保留Boss战及奖励完整链 |
| 1→2 | 跨幕选择、地图与状态连续性 | H1/H2实际进入Act2，旧中文Reference已见；Managed仅诊断执行 | 最终待验 | 完成Act1 Boss奖励后跨幕，核进入新幕前后 |
| 2→3 | 跨幕与Act3初始化 | 未有实际记录 | 最终待验 | 真人完成Act2 Boss并进入Act3 |
| 终局 | 自然loss、完整终局公开图与释放 | H1 Act2/22、H2 Act2/28均真实loss；M1/M2到loss但终局图不同 | 最终待验 | 新英文整局自然终局，核胜负/HP/Receipt/Stop/释放 |
| 终局 | 胜利 | 未有合格记录 | 必验缺口 | 真人尽量获胜；取得困难时由用户决定，不替换标准 |

关联/强制奖励等只有特殊模式证据的机制，先列实际标准局可达实例；不凭fixture宣称标准模式覆盖，也不因暂未遇到而宣称不存在。多人、未知身份与隐藏未来仍为当前不支持范围。



## 7. 收敛、发布与停止边界

项目地图应让新人定位owner、状态、出处、适用范围、资产去向和当前操作，不承诺穷尽历史全部知识，也不重验每条旧结论。复用K1的2063资产行、K2的100功能/401文档基准索引；135其它Codex目录仅metadata、语义迁入未核实等限制保留。

K3必须从“尚无立即关闭结论”推进到逐旧PR可批的关闭/保留/以后复用表；关闭前保全exact head及独有成果，用户批准后执行。历史数据/资产迁移/删除不由本定义自动授权。冲刺普通已合topic自动删branch和新Mod安装（先备份）已有本轮单独许可，与历史未合分支/原始记录分开。

发布继续用原版本/组件BOM、游戏/Mod/Managed/模板/工具原件和build/install/load身份；“develop冻结PR+tag、是否推main”的交付形式留用户确认，不先开新发布/registry。最终冻结PR用户亲批；普通source修复依已有冲刺授权推进。源码/CI、已装、真实游戏、Human来源、研究准入和策略强度各据实证。

B是关键路径；新Mod真实预检→用户短段→新英文整局→转换/严格重放。录制闭环未过不告诉用户“可以录”。遇到的producer/转换/工具问题在原owner修，不从GUI黑图或click错误推断游戏黑屏；持续卡住按已定时限报最短反例和可选方案。A、K3/K5/K6/文档并行不挡用户录制。

## 8. 提交用户确认的实际取舍

Source4当前合同只承认结构完整的waiting context；本次原生setup→Launch后inputordinal0的首帧明确settling但raw failed/missing造成实际gap。root已向用户提交受限启动语境方案，**尚待答复、未实施**：仅在同run/epoch、native setup→Launch且inputordinal0原证据齐备时保留该失败首帧为非决策启动语境，不发Agent、不用后来事实补全、不称NativeLogical完整前缀；第一决策及以后完整门不变。详情原件：`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/p1-round19-20261018/source4-startup-context-decision.md`。这不是已批准例外或本定义自行放宽；MapNativeSourceWitness漏附的普通修复独立推进。


1. 本合成冻结定义整体及最终发布形式（原BOM、freeze PR/tag、main另定）；在确认前仍是待批稿。
2. 覆盖索引哪些共同机制需在哪个位置实际验、哪些幕特有/跨幕/terminal行必验；不默认42组合全部通过，不自行删原全幕/第三幕/胜利要求。
3. 云最小对象/目的prefix/费用与一次有界Modal读取的授权和细门；40G历史迁移独立表，本地删除另批。
4. Agent公开信息复验的采样、机制范围和完成时点；必需公开事实缺失照原合同修，不偷换信息范围。
5. 胜利终局确实取得困难时，由用户决定剩余范围；旧中文缺陷的记录保留，新英文严格口径按最新指令执行。
6. 上述Source4受限启动语境合同的用户选择；未获答复前不把raw failed改成完整、不实施语义放宽。

首轮训练的数据划分/门槛、P1 Input/Target/词表及实际执行绑定、新费用、Q1固定评测方案仍在冻结后确认；已有Q3第3版模型/配方不因本定义被撤销，若要改变仍先请用户确认，本定义不代批。到截止未齐就交11条实际剩余表，不把待定/未测写成通过。

## 9. 依据、历史输入与当前入口

- 合并前完整原文：较新replies输入 (`/Users/fire/Desktop/SpireAgentProject/claude-handoff-2026-10-13/replies/P1-r1-support-scope-v0.3-2026-10-18-attachments/replies-original.md`)、旧root输入 (`/Users/fire/Desktop/SpireAgentProject/claude-handoff-2026-10-13/replies/P1-r1-support-scope-v0.3-2026-10-18-attachments/root-original.md`)。历史证据完整保留，不从旧append恢复旧任务。
- Claude冻结提案/全景仍作审计过的背景底稿；本定义没有将它们的待定条件默认为批准。
- 当前操作/状态：START (`/Users/fire/Desktop/SpireAgentProject/claude-handoff-2026-10-13/00_START_HERE.md`)与项目[文档入口](../../DOCUMENT_MAP.md)；K4完整操作单仍待B真实预检闭环。
- 已装6c原件：`/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/p1-round19-20261018/installed-6c-provenance.json`；合并/CI回执由root保留；A03实际两局报告：`/Users/fire/Desktop/SpireAgentProject/claude-handoff-2026-10-13/replies/P1-round16-A-new-identity-Managed-diagnostics-2026-10-18.md`。

此定义等待用户批准；文档合并/本地检查不发布冻结、不声明最终英文或R20/云已完成。
