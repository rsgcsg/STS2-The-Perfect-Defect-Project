# 首轮训练链路：每个组件、合同和格式为什么保留

> 2026-10-27参考定位：本目录保原任务/合同/盘点快照，当前能力和运行节点从[根地图](../../DOCUMENT_MAP.md)进入，派工只看[CURRENT](../../memory/CURRENT.md)。[本轮去向增量](../CONVERGENCE_DISPOSITION_20261027.md)补K后的变化；K3已有执行收据优先于旧“未执行”摘要。本轮未移动原目录或改写下面历史正文。

第19轮文档收敛；沿用[Architecture](../../ARCHITECTURE.md)、[Components](../../COMPONENTS.md)和[ADR0016](../../adr/0016-agent-composition-and-execution-request.md)。本表解释现有owner如何服务第一轮训练，不新增服务、schema或注册表。冻结定义仍待用户批准；当前事实由[范围/状态](SCOPE_v0.3.zh-CN.md)路由。

历史查过：以前遇到过queued输入时H目录为空或不同于执行态，见[09-11 rapid audit](../../evidence/PR25_RAPID_INPUT_EXECUTION_AUDIT_2026-09-11.md)。当时分存H/native input与执行S/A(S)，精确匹配后在[rc4限定两局](../../evidence/PR25_RC4_FULL_RUN_HUMAN_PASS_2026-09-12.md)取得524/524及47rapid handoffs。此次复用该执行接缝/身份约束用于⑤⑥，并补当前P1的完整PB1/Reads与共享核；不能把原rc4资格当新P1通过。其它组件继续复用旧K2地图、现行owner与K0/K5/K6成果。

## 2026-10-20冻结准备：所有功能都需明确归属与入口

用户在统筹chat直接明确：“所有功能，大的还是小的，不论是历史还是现在的，冻结的时候都要收敛到一个明确的入口，和单一组件”，并要求实现前先找是否已有成果。这里的“单一组件”是每项业务唯一责任owner，不是把所有功能并成一个巨型组件。沿下方同一上层图与[Components](../../COMPONENTS.md)、[现功能表](FEATURE_STATUS.md)/既有K资产账本落实，不另建总图或平行制度。

每项大小功能，包括启动/恢复/查看/下载/云/小工具，都须定位受支持入口→同一核心实现→上下游requires/provides、协议/格式版本、实际证据层和当前去向。GUI/CLI/API只是该owner的薄映射。历史条目逐项核现用、已替代、归档或未完成；归档需有真实用途/保留入口依据，不能用归档tag或登记行数消掉活跃重复路径、独有成果或未闭合接缝。不要求所有旧实验重新实现或重授资格。当前全功能映射尚未闭合，B已有K账本与C研究映射汇合后由主线抽查真实调用，未完成部分如实计冻结剩余。

收敛的验收是同一上层角色/连接能表示新、旧和后续方案，并实际支撑“采集→管理/用途→训练→Model/完整Agent→运行评估→改数据/模型/环境”的可重复闭环。不是只收目录、入口或停止点，也不要求不同角色共用一个schema。沿本图→合同→Components的owner→核心代码/真实caller→格式版本→证据和历史去向应能正反找到；未来组合仍须独立证明，不写成已实现。

应用/资源组织是横跨在线、记录、研究链的角色；工作台GUI只是人的可选映射，现`python/spireagent/workbench`目录内的Application/用例服务是具体实现，不新增上层权威，也不把评测→Workbench→Mod界面串成必经流水线。运行可能需要服务但不需要打开页面，关页面不等于Stop；纯离线库直接调用所属owner。全部项目GUI的业务/状态/失败处理都须有可发现且实际可用的非GUI入口（CLI/API/库/agent工具按方便分工），agent不依赖点击工作台；绕权限/数据库或私人日常脚本不算受支持入口。GUI呈现测试与业务执行分开，原生Human游玩仍由用户完成。定位与历史限定见[原K6](WORKBENCH_POSITION.md)，全功能逐项闭合尚未完成。

已有实现也须服从现在的合同：只差表示/传输，在原适配层改；组件没满足现requires，修或替换该owner实现；信息/动作/时序/失败/用途语义变化，修改拥有它的版本合同及consumer，未批重大取舍交用户。真实用例不能表达、职责反复重叠/消费者不断打补丁或有实测不可接受成本时，带证据与最小迁移回到上层，而不是为保历史实现强塞适配。复用有效机制和证据，不复活全部旧结构或改写旧失败。每功能只有一份业务语义、事实权威及核心状态机；复合用例由同一应用owner编排原组件，GUI/CLI/Agent入口薄映射，不各复制流程。

今天已启动准备，不等新的阶段许可；B*离线新投影已另获准确批准，旧默认/03/T0固定输入与原件保留。滚动采集的技术就绪、固定操作入口、正常Store与最终环境/模型验收分开报告，不以录一局代全功能收敛。日期是工作目标，最终通过依实际结果与用户确认。

## 唯一上层图与连接要求（新任务入口，2026-10-19）

目标是在不多于原生公开信息的交互下，做出最强、能真实使用的STS2 Agent，靠采集、管理、训练、评估与迭代完成。沿用[BASELINE §2](../../BASELINE_V1_SPEC.zh-CN.md#2-上层抽象与唯一责任)、[综合设计 §3](../../design/BASELINE_PROTOCOL_V1_SYNTHESIS.zh-CN.md)的实现R/投影Π/观察抽象α及[四类变更规则](../../ARCHITECTURE.md#selected-convergence-and-change-rules)，把现有三路图提升到工作入口；不新建框架、服务、注册表或schema。

```text
应用层：CLI / API / 库组织任务、资源、权限、预算；Workbench为薄映射；R2存取，Modal执行
在线链：世界(STS2) ─①─ Host能力 ─②─ Connector ─③─ 交互profile(当前P1) ─④─ 完整Agent(程序＋可选Model)
         Host Runtime管进程/隔离/恢复；Policy Runtime横切控制/Stop/unknown/消费与证据
记录链：记录来源（同一抽象角色：在声明接缝捕获事实）
         Runtime / 选择器 / 原生Human(Annotator) / Source4(后续profile) / 外部
          ─⑤─ 不可变原件Store / Evidence ─⑥─ 按所选profile派生 → profile轨迹
                  P1：accepted原生动作＋H来源＋执行S/A(S)完整PB1 → 同一共享核reducer/lowering
研究链：profile轨迹 ─⑦─ Curation(用途/划分) ─⑧─ Projection＋InputSpec/TargetSpec
          ─⑨─ Recipe / TrainRun → checkpoint / Model
          ─⑩─ AgentComposition → 运行/评估(本轮Reference；Managed严格延后) → 新原件回Store
```

编号表示逻辑连接，不要求每次运行经过全部节点；SDK、Mod包装和传输形式不另设语义层。**交互profile是可替换角色，P1只是当前选择。每个profile必须声明③④在线requires及⑥原件派生requires**：目标字段/时点/公开来源α、接受事实/顺序/结果、完整性与拒绝条件、投影Π及版本。新增来源只需证明其provides经声明的Π覆盖该profile的全部requires；更换profile换这些声明，图不变。原件尽量保存比单个profile更多的事实，但不因此宣称所有未来profile可用。实现不能自行重定义下游requires；真实语义取舍交用户。

**比较声明也沿同一图定位（2026-10-19用户明确）：** Managed是Host实现，不是P1专用执行器；可直接消费受支持的原生语义操作，或经P1/其它profile实现R执行。C1是同起点同P1操作下Host一致；C2是声明原生操作范围下Host一致；C3是⑥投影Π正确；C4是profile对目标功能够用。原生Human→有损Π→P1→Managed不同，不能直接归因Host。B已延后；其历史试验在已登记例外段补放原生实际操作，只证明混合路径的限定一致，不能自动授予P1-only全轨或全部原生内部状态等价；A独立原生重放与C新P1动作冻结后才做。原件、派生及比较报告保留假设、例外和经过的连接，验收逐项见[冻结表](SCOPE_v0.3.zh-CN.md#1-11条进度依据和首训价值)，不另建链路或注册表。

**2026-10-20现行决定与默认解释：** 原始记录、⑥转P1、跨Host对照和不同目的的重放分别判断，不要求每次都串过全部节点。P1里的出牌/用药状态默认按“前面已结算、现在轮到该动作”的串行执行态解释，沿既有rc4接缝取真实S/A(S)；原件仍保留真人实际连点/排队，不改游戏或伪造点击时点。按钮灰掉等暂存表现不能替代原生执行语义，执行目录也不授予新的公开输入权。Managed严格一致已延后，资产/差异收据保留，1a与T0实打用Reference；旧B实验和旧冻结表不构成当前派工。各类验证不互相代替，也不相互绑成未经用户要求的门。

| 连接 | 下游requires | 上游provides／当前owner与合同 | 当前状态 |
|---|---|---|---|
| ① 世界→Host能力 | 真实规则/RNG/原生合法性、状态与生命周期 | STS2原生事实；Native Foundation见证；[Host合同](../../../components/host-runtime/README.md) | 既有实现；实际组合另核 |
| ② Host能力→Connector | 同状态公开事实/Reads、精确操作、执行复验与结果 | Reference/Managed映射；[Player Environment](../../../components/connector/docs/player-environment/PROTOCOL.md) | 既有实现；Host差异不自动等价 |
| ③ Connector→profile | profile声明的信息时点、完整目录、结果/unknown | Connector Snapshot/all advertised Reads/BoundAction/Receipt；[P1](../../protocols/P1_GLOBAL_R1.zh-CN.md)由共享核聚合/lower | P1 r1.4；全机制按已批跨版本组合及受影响补证实证 |
| ④ profile→完整Agent | 声明的观察/动作/承诺与失败语义；原控制/消费保证 | P1共享核；AgentProgramPort/Policy Runtime；[ADR0016](../../adr/0016-agent-composition-and-execution-request.md) | 程序/Model组成完整Agent，Model不等于Agent |
| ⑤ 来源→Store/Evidence | 来源/原始顺序/内容身份、原件与缺口不变 | Annotator、Runtime、选择器等各producer；[记录合同](../../../components/annotator/docs/SOURCE_SESSION_PROFILE_V3.md)、[Evidence](../../../components/evidence/README.md) | 每来源分开核；封包不证明Human或整局 |
| **⑥ 原件→profile轨迹** | **每profile自己声明；P1以执行S/A(S)同snapshot完整PB1/all Reads/catalog派生；H仅来源，native key/全部operands精确匹配，结果/后继及起终/Close/pack** | **Annotator P1-native/AgentRun等原件；共享核reducer/lowering与声明的Π；[P1 §4.1](../../protocols/P1_GLOBAL_R1.zh-CN.md#41-录制原件派生p1的requires用户已批准)** | **定义4.1–4.2用户已批准；三自然原件及既有事务03/兼容已交，B*和最终资格见CURRENT，不沿旧“未开录”快照派工** |
| ⑦ profile轨迹→Curation | 来源资格、用途/use、谱系/划分与缺口处理 | 已验原件/派生轨迹；[Data/Provenance](../../../python/docs/DATA_AND_PROVENANCE.md) | 具体用途由Q链owner定；已批T0a实际读入，非正式研究准入 |
| ⑧ Curation→研究输入/目标 | Input/Target字段、曝光/历史/reset、target/mask与split | Projection/InputSpec/TargetSpec owner；[研究地图](../../../python/docs/DOCUMENT_MAP.md) | T0 owner接当前Input/Target；正式dataset/研究配置未因此冻结 |
| ⑨ 研究输入→训练/Model | recipe/初始化/输入身份、更新数/预算、checkpoint | TrainingService/engine/worker/provider；[workload](../../../python/docs/STRUCTURED_WORKLOAD.md) | 已有机制；P1实际consumer/首训资格未验 |
| ⑩ Model→Agent→评估/新原件 | 可加载权重、输入/状态语义、组合/请求和固定评估条件 | 注册/AgentComposition/ExecutionRequest、Runtime/result reader；[Agent执行](../../../python/docs/AGENT_EXECUTION_CONTRACT.md) | T0a离线已交；正式研究工程进行，T0b须准确新注册/独占窗口才实打 |

| 抽象节点 | 当前实现 | 合同/责任owner |
|---|---|---|
| 世界／Host能力 | STS2；Reference真游戏＋Mod、Managed无界面Host | Native Foundation、Host能力；Host Runtime只管进程 |
| 公开环境／交互profile | Connector；SDK `p1Global`当前实现P1 | Player Environment；P1 §4.1/ADR0017 |
| 完整Agent／执行 | 程序＋可选Model；独立Policy Runtime/NDJSON端口 | AgentComposition/ExecutionRequest及Runtime合同 |
| 记录来源／不可变原件 | Annotator Human/P1-native、Runtime、选择器、Source4及外部 | 来源声明＋Store/Evidence；Source4非P1必经层 |
| profile派生 | P1共享reducer/lowering、native-semantic adapter | P1 requires与来源reader；不以具体格式改定义 |
| 研究／应用 | Curation/STPD/TrainingService；Python应用/API/库/CLI | 原Data/Provenance/训练合同；GUI为原用例薄映射 |

P1原件整局完整、派生完整与新路线E2/E3适用性已由用户批准；方向与开录门也已确定。用户进一步批准恢复rc4执行边界S/A(S)作为P1派生决策，H仅来源；这是⑤⑥的原件/解释要求修订，不改③④或两Host。nonqueued证明H/S同边界，queued披露true及间隔；public action/native input互斥，不制造H BoundAction。原件完整而adapter暂缺可补实现后重派生；旧H/执行S缺失不回填。未提交拿牌取消无accepted决策/label；真实accepted选择/效果/unknown仍保留。具体实现与用户四项缺口检测见[P1 §4.1](../../protocols/P1_GLOBAL_R1.zh-CN.md#41-录制原件派生p1的requires用户已批准)。这不代表实际开录或最终冻结已PASS。

### 报告、PR与路线复查如何使用这张图

每份报告和PR加一行：**链路位置：节点/连接编号；变更类别：表示或传输／能力或交互语义／组件实现替换／回到上层抽象；requires/provides及实际缺口。** 四类名称与[Architecture](../../ARCHITECTURE.md#selected-convergence-and-change-rules)同一规则；能力/语义或上层取舍交用户，不由adapter隐改。普通实现修复在既有授权内推进，不新增冻结门。

两次预检因不同原因失败时，对图回答：失败在什么连接；是实现未满足requires，还是requires未写清/错误；复用哪个已有owner、何时交付最小可验结果。native01/02的已做路线复查定位⑤→⑥：原件采集/Close不完整，和adapter表示匹配错误分别归原owner修复；不重跑旧失败来改旧结论。native02有效前缀离线/Managed通过仍不能填正常Close/pack开录PASS；native03主动零动作收尾也不是通过。

**按当前已批T0a并行安排**由Q链owner明确⑦⑧的工程用途/划分→模型输入requires，正式研究准入仍另定；再考虑closeout对“改合同未写链路位置”的非阻塞轻提示。本次不实现checker、不加阻塞门、不重设已批Q3v3。

## 组件：位置、原因与首训价值

| 组件/角色 | 在链中的位置 | 为什么保留、首训的实际价值 | 当前范围/owner |
|---|---|---|---|
| STS2 | 在线世界 | 唯一规则/RNG/效果和原生合法性来源，训练环境不能另造规则 | exact game DLL；[Architecture](../../ARCHITECTURE.md) |
| Native Foundation | 世界→原生事实 | 复用决策/lifecycle/lineage，减少记录与执行各猜因果 | 当前既有原生seams；[README](../../../components/native-foundation/README.md) |
| Reference | 在线真实游戏路径 | 校对真实公开信息/操作，提供新英文示范和Managed对照 | Mod/模板/实录独立身份；[Host owner](../../../components/host-runtime/docs/COMPATIBILITY.md) |
| Managed native/投影 | 在线无界面路径 | 首训后的批量运行/评估与配对重放，实际差异按原件修 | 同DLL不自动等价；[差异表](MANAGED_DIFFERENCES.md) |
| Connector | 世界↔公开环境 | 完整公开Snapshot/Read/catalog及执行时复验，避免学到错误或隐藏信息 | 不做策略/二次规则；[协议](../../../components/connector/docs/player-environment/PROTOCOL.md) |
| Connector SDK/P1共享核心 | 环境↔P1、离线解释共享 | 在线/离线复用同一聚合、承诺和lowering，减少两套派生分叉 | 不把Model Input当P1；[P1](../../protocols/P1_GLOBAL_R1.zh-CN.md) |
| Host Runtime | 在线准备/隔离/close/恢复 | 相同API管理真实实例/候选，让实验与重放可复验且可停止 | 不判合法性；[Operations](../../../components/host-runtime/docs/OPERATIONS.md) |
| Policy Runtime | Agent受控执行 | 复用预算、Stop、控制释放、消费/ACK和unknown fence，首训Agent不复制控制循环 | 不做推理或筛catalog；[README](../../../components/policy-runtime/README.md) |
| 完整Agent/人工选择器 | 在线消费/选择 | 同一环境可接固定程序、真人chooser或后来Model；选择器收示范但不证明人只看P1 | Agent负责取得/历史/时序/策略；[ADR0016](../../adr/0016-agent-composition-and-execution-request.md) |
| Annotator/RecordingService | 独立来源记录 | 复用Start/Pause/Close、原始公开事实/输入和持久化；给P1转换提供真实材料 | Source4不等于Human或因果证明；[Source合同](../../../components/annotator/docs/SOURCE_SESSION_PROFILE_V3.md) |
| Evidence | 记录→独立校验/传输 | 内容身份、原件/封存验证和收据避免坏包进入后续数据；不代研究选样 | [README](../../../components/evidence/README.md)、[Delivery](../../../components/evidence/DELIVERY.md) |
| Game Mod | Reference装载包装 | 固定Connector/Recorder/UI实际构建和回滚身份，复用一个DLL装载 | 包装不合并职责；[README](../../../apps/game-mod/README.md) |
| Python Application/Workbench用例层 | API/库/CLI↔原服务 | 复用环境/Agent选择、prepare/run/one_step/Stop/查记录；首训后Model沿相同入口上场 | 正式应用是python/spireagent；[K6](WORKBENCH_POSITION.md) |
| 浏览器/Mod GUI | 同一用例的人类薄映射 | 方便显示/确认、配对和恢复，业务权威仍在原服务；不要求GUI全无代码 | 保留现成面板，不新建复杂GUI/团队页；[UI scope](../../UI_INTERACTION_SPEC.md) |
| apps/workbench旧诊断API | 旧诊断/兼容入口 | 仅保留实际依赖/历史诊断，不作为又一完整工作台来做 | 已无HTML；[README](../../../apps/workbench/README.md) |
| Store/Curation | 不可变原件→用途/划分 | 复用来源、去重、同seed/save谱系、Gold/use，后续dataset可追溯 | Q2/P1准入细则后定；[Data](../../../python/docs/DATA_AND_PROVENANCE.md) |
| STPD projector/Input/Target | 研究输入构造 | 明确模型看到什么、记忆怎样推进、训练监督来自哪，防把未来标签放当前输入 | P1来源/适配/词表及实际绑定待验，已批Q3模型/配方不改；[research map](../../../python/docs/DOCUMENT_MAP.md) |
| TrainingService/engine/worker | 研究学习 | 复用Run/attempt/checkpoint/更新数/预算与导出，不为新P1另写训练manager | 首轮配置/费用仍另批；[workload](../../../python/docs/STRUCTURED_WORKLOAD.md) |
| R2存储adapter | 原件/派生包↔存储 | 最简内容寻址存取给远程首训用，复用ManifestArtifactStore而不强制Hub | 本轮具名原件/旧压缩档已实际完整回读/按ID恢复；正常storage operation接线和当前资格见[CURRENT](../../memory/CURRENT.md)，不是所有历史数据云资格 |
| Modal/provider adapter | 资源执行/只读数据入口 | 复用远程资源机制；必须证明首轮所需数据真实可读，不能借旧job名冒充新P1接好 | 当前P1消费与费用未验/另批；[K5](CLOUD_MINIMAL.md) |
| Hub/团队账号/Cloudflare登录/云页面 | 原有团队/共享通道 | 历史现成能力保留原consumer和授权；不让首训最简R2/Modal依赖整个团队产品 | 冻结前保留现状、接口/占位，不扩建；[K5](CLOUD_MINIMAL.md) |
| Task/result reader与Q1评估 | 新运行→证据/质量 | 区分自然终局、技术停止、unknown与策略效果，回答是否优于随机 | Q1方案后固定；[evaluation](../../../python/docs/MANAGED_SUITE_EVALUATION.md) |

## 合同/协议：为什么不是同一个接口

| 合同/协议 | 位置和原因 | 首训价值/状态 |
|---|---|---|
| Player Environment | Connector提供公开观察/Read/完整原生目录/Receipt的具体合同 | P1派生与执行的原始基础，不是训练张量格式 |
| P1 r1.4 | 当前环境信息/前进动作/承诺/失败语义 | 线上和离线解释同意当前公开决策问题；[唯一语义owner](../../protocols/P1_GLOBAL_R1.zh-CN.md) |
| Agent-session/P1子程序端口 | Runtime↔完整Agent的消费、query/choice/control | 复用同一受控入口接未来模型，NDJSON只是实现；[owner](../../../components/policy-runtime/docs/AGENT_SESSION_PROTOCOL.md) |
| AgentComposition/ExecutionRequest/Goal | Agent描述与一次执行意图、资源/权限/预算、任务见证 | Model不等于Agent；沿原应用启动/结果，不添scheduler；[owner](../../../python/docs/AGENT_EXECUTION_CONTRACT.md) |
| Record grammar/来源声明 | 来源原件事实、顺序、身份、缺口、完整性 | 核验记录可解释什么；不冒充P1/Agent消费/训练准入 |
| Projection/InputSpec/TargetSpec | 原件→研究输入/标签 | 明示有损转换、历史/reset、target/mask；仍为后续Q链决定 |
| 旧native-logical/text-menu/score-vector | 原历史在线/研究consumer合同 | 保留真实consumer/reader；不恢复第二学习主线，也不借归档标签删兼容 |

## 格式：为什么保存，谁真正消费

| 格式族 | 链中用途/消费者 | 对首训的价值及去向 |
|---|---|---|
| Snapshot/Reads/BoundAction/Receipt原字节 | 环境→P1/Runtime/Recorder | 来源事实及选择绑定，离线复验可追溯；不直接当所有Model Input |
| P1-native决策原件＋PB1 | 成熟Human accepted动作owner→Evidence→共享核离线P1 | 执行S/A(S)解释与接线已实现，H只作来源；三自然来源已封包，当前重派生/缺口及资格从CURRENT查 |
| Source4 profile/bundle-v4/public-basis1 | 保留的原始输入/publication→P2/后续消费者 | 原report-v3和旧FAIL保持，不承担当前P1强制raw取消全分类门 |
| P1 observation/choice/result | P1在线边界及离线派生 | 当前公开决策语义；T0已按固定InputSpec/词表完成工程闭环，正式数据配方和研究准入另定 |
| agent-session-run-1目录/events/引用原件 | Runtime→typed verifier/replay/样本admission | 保存实际offer、Consume/ACK、Receipt/Result；直接Agent lane独立于Source4 |
| legacy episode-record-1单文件 | 原直接记录converter/reader | 有限复用原P1来源，保留原版本，不与当前目录格式混同 |
| Human manifest2/trace4/canonical3/action-space3/Read2/journal2/close1/bundle3 | 原生因果lane→Evidence/对应研究consumer | 成熟动作owner、H关联、原件和封存可复用；旧H缺当前P1事实不补造，不在未测下断言必须重建全部hooks或高迁移成本 |
| record2/bundle2兼容及trace1–3/canonical2/ledger1–2/record1/bundle1 | 真实旧consumer/归档reader/fixture | 原用途保留，不转贴新candidate资格；[格式链](../../FULL_RUN_DATA_CHAIN.md) |
| Source1/2/3及bundle | 原声明/epoch/有序basis研究lane | Source3默认及旧专用view继续；不回填Source4新事实 |
| ResearchTransition/Parquet/dataset/use/split manifests | 来源admission→固定研究样本/用途/划分 | 复用现有数据管理；不是所有源都强制S-a-S′或同一Parquet语义 |
| Artifact manifest/chunks/content hashes | Store/R2/transfer/materialize | 既有内容身份可分块核对；大数据路径/云费用另批，不擅调全局上限 |
| Model/checkpoint/Agent package | 学习→加载/注册→新运行 | T0已交固定M-std工程模型/恢复/export，真实环境运行待具名独占窗口；不把模型工程更新计正式策略资格 |

## 工作台停止边界

主操作面是内部API、库和CLI。复用已存在的登记/准备/运行/Stop/查记录服务；GUI只是薄映射，保留现有pending/unknown确认和配对适配，不新做复杂GUI、团队或Hub产品。K6没有新增源码要求；现GLOBAL登记已有API/库与浏览器按钮，CLI没有同名登记子命令，不虚构命令。产品路径实际短验和R20仍待root在最终tuple上安排，详见[定位/命令边界](WORKBENCH_POSITION.md)。

## K3历史复用来源：按链路逐项定位

用户2026-10-18已批准24项先exact tag远端核验再关闭，保留branch/worktree/data。下表是其中19项“以后复用”的逐条链路；现已据K1逐项回执核24/24 exact tag远端解引用匹配、24/24已关闭未合并；[紧凑执行快照](K3_EXECUTION.json)保留全部24项身份/时间。它们保留未迁入源码，不能据closed推断全功能已经承接。

**Q-CHAIN-0和Q3开工前必查 #141、#147、#150、#157、#158、#161、#163**，先复用训练、Modal、checkpoint及恢复的已有实现/已知限制；不改变已批准Q3第3版模型、配方、预算和开始时点。当前Q3批准方案 `/Users/fire/Desktop/SpireAgentProject/claude-handoff-2026-10-13/Q3_BATCH1_PLAN_2026-10-17.zh-CN.md` 保留这一提醒。

| 旧PR／对应链路 | 未迁入价值及当前owner | exact归档tag／实际状态 |
|---|---|---|
| [#26](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/26)；Workshop L3；build→批准→stage | 自身8路径：AWAITING_APPROVAL proposal、显式SHA批准后原字节finalize，不重建；当前无Workshop入口。 [现行owner](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/ea7c14e8969006fa0e470678198ef94aca471f90/apps/game-mod/build.mjs#L89)：现行唯一build/产物owner；Workshop编排待复用 | [`archive/p1-prefreeze-20261018/pr-26-4f26d8dfbbc5`](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/archive/p1-prefreeze-20261018/pr-26-4f26d8dfbbc5) → `4f26d8dfbbc58e3a4a0e546e8bc67f72a9f4c60f`；已远端核验exact tag；已关闭（2026-10-18T11:03:46Z） |
| [#30](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/30)；Workshop L4；prepared→Steam发布→对账 | 自身10路径：固定uploader、exact candidate、持久attempt/unknown、初始化前失败/成功追加对账；现行没有Steam发布consumer。 [现行owner](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/ea7c14e8969006fa0e470678198ef94aca471f90/apps/game-mod/package.json#L11)：现行仅本地build/deploy/verify/rollback，不等价Steam发布 | [`archive/p1-prefreeze-20261018/pr-30-b8e7f02db2c8`](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/archive/p1-prefreeze-20261018/pr-30-b8e7f02db2c8) → `b8e7f02db2c868604bbfd2fe682782acb976c568`；已远端核验exact tag；已关闭（2026-10-18T11:03:52Z） |
| [#45](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/45)；Workshop安装→环境采集 | 自身9路径：Steam已安装item与manual冲突识别、发布目录外可写状态；当前仍manual mods路径，Workshop隔离未迁入。 [现行owner](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/ea7c14e8969006fa0e470678198ef94aca471f90/apps/game-mod/collection-setup.mjs#L104)：manual采集入口；另由AnnotatorConfiguration/ConnectorMod管理配置 | [`archive/p1-prefreeze-20261018/pr-45-fbfab632c78d`](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/archive/p1-prefreeze-20261018/pr-45-fbfab632c78d) → `fbfab632c78dc1640be6e1345dae1bbb2b44d1df`；已远端核验exact tag；已关闭（2026-10-18T11:03:57Z） |
| [#140](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/140)；Public M0整合；投影→训练→注册→Host | 自身44路径：M0出口/注册已有当前owner；旧声明入口、native-window隔离、native-v1 M2 text/kernel未整体迁入。 [现行owner](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/ea7c14e8969006fa0e470678198ef94aca471f90/python/spireagent/workbench/public_m0_export_support.py#L23)：lineage/注册及historical_m0 recipe替代旧整栈入口 | [`archive/p1-prefreeze-20261018/pr-140-bffb01210e5d`](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/archive/p1-prefreeze-20261018/pr-140-bffb01210e5d) → `bffb01210e5d0800887a893e04159127925cc02f`；已远端核验exact tag；已关闭（2026-10-18T11:04:03Z） |
| [#141](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/141)；M0远程训练→checkpoint恢复；**开工前必查** | 相对#140自身66路径：固定M0 Modal/provider、journaled恢复/资格；专用controller/remote-update/qualification未迁入。 [现行owner](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/ea7c14e8969006fa0e470678198ef94aca471f90/python/spireagent/workbench/local_training.py#L160)：现行TrainingService/typed recipes；泛Modal不是旧M0功能替代 | [`archive/p1-prefreeze-20261018/pr-141-885e85ec1cf0`](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/archive/p1-prefreeze-20261018/pr-141-885e85ec1cf0) → `885e85ec1cf0e8ab66f56e6105e7bfc9e54d3486`；已远端核验exact tag；已关闭（2026-10-18T11:04:08Z） |
| [#144](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/144)；checkpoint诊断→导出/注册 | 自身12路径：当前已分300秒本地验导与22秒在线注册；旧paused-checkpoint诊断与M0 remote result复用未迁入。 [现行owner](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/ea7c14e8969006fa0e470678198ef94aca471f90/python/spireagent/workbench/local_model_registration.py#L1550)：当前注册分段owner；暂停诊断留STPD/训练owner以后复用 | [`archive/p1-prefreeze-20261018/pr-144-1d00b60fad28`](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/archive/p1-prefreeze-20261018/pr-144-1d00b60fad28) → `1d00b60fad2829ec58a0d79e95cc38cd99a64ecc`；已远端核验exact tag；已关闭（2026-10-18T11:04:15Z） |
| [#146](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/146)；member下载→archive准入→Curation | API累计99路径；#145后窄增量8路径：catalog/execute共用download收据、member源历史unknown；专用准入分支未迁入。 [现行owner](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/ea7c14e8969006fa0e470678198ef94aca471f90/python/spireagent/workbench/member_client.py#L127)：当前下载/收据与Importer/Curation owner；不等于旧archive执行准入 | [`archive/p1-prefreeze-20261018/pr-146-697880c06e52`](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/archive/p1-prefreeze-20261018/pr-146-697880c06e52) → `697880c06e52ef1e7e3d588627379abd50c28a5b`；已远端核验exact tag；已关闭（2026-10-18T11:04:26Z） |
| [#147](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/147)；大M0请求→压缩wire→持久恢复；**开工前必查** | API累计102路径；#146后窄增量10路径：有界zlib、逻辑hash、request分块持久化；旧codec/controller未迁入。 [现行owner](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/ea7c14e8969006fa0e470678198ef94aca471f90/python/stpd/cloud_jobs/modal.py#L119)：当前ComputeRequest泛provider，未来大输入由此与TrainingService定点复用 | [`archive/p1-prefreeze-20261018/pr-147-71309adfcdfd`](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/archive/p1-prefreeze-20261018/pr-147-71309adfcdfd) → `71309adfcdfd808a6b99d2bc43abda9079ef1d7a`；已远端核验exact tag；已关闭（2026-10-18T11:04:33Z） |
| [#148](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/148)；完整输入→不可变projection复用 | API累计108路径；#147后窄增量10路径：codec当前blob相同；通用压缩接线、selection/物理store+实现身份cache未迁入。 [现行owner](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/ea7c14e8969006fa0e470678198ef94aca471f90/python/stpd/fullrun/light_action_row_storage.py#L43)：当前codec/historical_public_m0；未迁入接线保留给输入owner | [`archive/p1-prefreeze-20261018/pr-148-c8d654b7aa87`](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/archive/p1-prefreeze-20261018/pr-148-c8d654b7aa87) → `c8d654b7aa87ad71da7c5f12e0a20815f2c16889`；已远端核验exact tag；已关闭（2026-10-18T11:04:39Z） |
| [#150](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/150)；Public M2训练→stage→云切片；**开工前必查** | API累计136路径；c8底座后28路径：数值engine/window当前字节相同；完整run/remote/sequence/Modal调度未迁入。 [现行owner](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/ea7c14e8969006fa0e470678198ef94aca471f90/python/stpd/workers/public_m2_engine.py#L445)：当前数值核保留；调度设计留TrainingService/Modal owner | [`archive/p1-prefreeze-20261018/pr-150-732a2d67bbd3`](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/archive/p1-prefreeze-20261018/pr-150-732a2d67bbd3) → `732a2d67bbd3718da62f87cd51eb3842abc23757`；已远端核验exact tag；已关闭（2026-10-18T11:04:50Z） |
| [#151](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/151)；Port4公开输入→Runtime→Evidence | API累计129路径；c8底座后23路径：Port4/Evidence已有后续owner；旧period1/2三轮SemanticCycleGuard未迁入。 [现行owner](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/ea7c14e8969006fa0e470678198ef94aca471f90/components/policy-runtime/src/public-observation.ts#L6)：当前PublicObservationTracker/decideV4/Evidence；guard以后按实需复用 | [`archive/p1-prefreeze-20261018/pr-151-d8b5c9ff277c`](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/archive/p1-prefreeze-20261018/pr-151-d8b5c9ff277c) → `d8b5c9ff277cba39921c533a06a22763818c0141`；已远端核验exact tag；已关闭（2026-10-18T11:04:56Z） |
| [#152](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/152)；Workbench模型生命周期→launcher恢复 | 自身21路径：exact-pair launcher替换已有演进owner；stopped保留selection/profile及旧stale replacement语义未全迁。 [现行owner](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/ea7c14e8969006fa0e470678198ef94aca471f90/python/tools/install_developer_kit.py#L1361)：当前签名native pairing/launcher与Application owner，旧恢复逻辑留复用 | [`archive/p1-prefreeze-20261018/pr-152-9f1177dae8a0`](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/archive/p1-prefreeze-20261018/pr-152-9f1177dae8a0) → `9f1177dae8a0bd3e701e947cb860c752b758b390`；已远端核验exact tag；已关闭（2026-10-18T11:05:03Z） |
| [#153](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/153)；developer kit→Python依赖环境 | 自身6路径：显式python_environment_profile/无Runtime pair的M0依赖路径；当前kit_environment缺失，仍schema v1按bundled pair推导。 [现行owner](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/ea7c14e8969006fa0e470678198ef94aca471f90/python/tools/package_developer_kit.py#L68)：当前kit owner；显式profile实现未迁入、保留供真实消费者取用 | [`archive/p1-prefreeze-20261018/pr-153-81ad4d1cb14a`](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/archive/p1-prefreeze-20261018/pr-153-81ad4d1cb14a) → `81ad4d1cb14a1d2661ed8f5be8770b03f3edfa1f`；已远端核验exact tag；已关闭（2026-10-18T11:05:09Z） |
| [#154](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/154)；游戏Workbench/launcher→Runtime恢复 | 自身37路径：launcher备份/恢复已有当前owner；旧scorer cache/semantic-cycle guard与冷启动报告未整迁，旧Windows门放宽不采用。 [现行owner](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/ea7c14e8969006fa0e470678198ef94aca471f90/python/tools/install_developer_kit.py#L957)：当前installer/PublicObservationTracker/Evidence；不整叠并回旧产品栈 | [`archive/p1-prefreeze-20261018/pr-154-1fe27bae96ed`](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/archive/p1-prefreeze-20261018/pr-154-1fe27bae96ed) → `1fe27bae96ed8219e96f2c75a4b73e8662cd288f`；已远端核验exact tag；已关闭（2026-10-18T11:05:15Z） |
| [#157](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/157)；已验M2 stage→复用→独立评估；**开工前必查** | API累计147路径；#150后14路径：checkpoint/消费lineage保留；epoch_stage_reused和独立evaluation owner未迁入。 [现行owner](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/ea7c14e8969006fa0e470678198ef94aca471f90/python/stpd/policy/public_m2_export.py#L63)：当前restoration只验既有stage；未来评估由research owner复用 | [`archive/p1-prefreeze-20261018/pr-157-96c86a2e03bc`](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/archive/p1-prefreeze-20261018/pr-157-96c86a2e03bc) → `96c86a2e03bcb6b0c9f7c64650b0f20d32c89731`；已远端核验exact tag；已关闭（2026-10-18T11:05:26Z） |
| [#158](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/158)；CUDA训练产物→GUI导出/注册；**开工前必查** | 自身4路径：训练来源/推理设备分离与CUDA public M0 fixture；backend识别public_m0，前端白名单仍缺public_m0/public_m2。 [现行owner](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/ea7c14e8969006fa0e470678198ef94aca471f90/python/spireagent/console/project.js#L3084)：当前export/registration owner；GUI CUDA修复不能宣称已迁入 | [`archive/p1-prefreeze-20261018/pr-158-c8714976de1e`](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/archive/p1-prefreeze-20261018/pr-158-c8714976de1e) → `c8714976de1e01beace89fb17d59891c273833fa`；已远端核验exact tag；已关闭（2026-10-18T11:05:32Z） |
| [#161](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/161)；prepared M2 campaign→切片/恢复；**开工前必查** | API累计161路径；#157后14路径：config/CLI、journal、saved handle、unknown不重提；campaign目录未迁入。 [现行owner](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/ea7c14e8969006fa0e470678198ef94aca471f90/python/docs/PUBLIC_M2_RESTORATION.md#L3)：当前恢复仅消费端；将来campaign按#163修正版接TrainingService | [`archive/p1-prefreeze-20261018/pr-161-41ab5cd17ab7`](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/archive/p1-prefreeze-20261018/pr-161-41ab5cd17ab7) → `41ab5cd17ab7c28be19eb34292c22a65be6a2e51`；已远端核验exact tag；已关闭（2026-10-18T11:05:44Z） |
| [#162](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/162)；CAS stage→export→注册→CPU Runtime | API66路径；consumer核心加BOM29路径：export/收据/CPU消费已承接；旧显式segment命令由bounded自动tracker取代。 [现行owner](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/ea7c14e8969006fa0e470678198ef94aca471f90/python/stpd/policy/public_m2_export.py#L228)：当前export_model/注册/Port4 owner；旧组合测试和证据留归档 | [`archive/p1-prefreeze-20261018/pr-162-829496cfaf01`](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/archive/p1-prefreeze-20261018/pr-162-829496cfaf01) → `829496cfaf012defd0f169341fef13ee248dc218`；已远端核验exact tag；已关闭（2026-10-18T11:05:50Z） |
| [#163](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/163)；campaign故障→批准的adapter恢复；**开工前必查** | API累计164路径；#161后12路径：保campaign/holds的pre-submit metadata恢复、单次event/新attempt；完整recovery目录未迁入。 [现行owner](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/ea7c14e8969006fa0e470678198ef94aca471f90/python/docs/PUBLIC_M2_RESTORATION.md#L3)：当前无campaign/provider job；以后复用#163，旧grant不是新授权 | [`archive/p1-prefreeze-20261018/pr-163-762907a641f6`](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/archive/p1-prefreeze-20261018/pr-163-762907a641f6) → `762907a641f603e1db32155058a70efbf3b11ee5`；已远端核验exact tag；已关闭（2026-10-18T11:05:55Z） |

执行与来源：K1封存批准表 `/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/p1-K1-20261018/execution-24/K3-old-drafts-batch-01.json`；逐项原回执固定为 `/Users/fire/Documents/Codex/2026-10-17/a-t7-runtime-connector-status-settling/work/p1-K1-20261018/execution-24/execution.json`。这24项批准不涵盖其它历史PR/分支或数据迁移。
