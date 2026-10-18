# 首轮训练链路：每个组件、合同和格式为什么保留

第19轮文档收敛；沿用[Architecture](../../ARCHITECTURE.md)、[Components](../../COMPONENTS.md)和[ADR0016](../../adr/0016-agent-composition-and-execution-request.md)。本表解释现有owner如何服务第一轮训练，不新增服务、schema或注册表。冻结定义仍待用户批准；当前事实由[范围/状态](SCOPE_v0.3.zh-CN.md)路由。

历史查过：旧K2地图/100功能表、现行源码和组件合同、K0采集、K5云调查、K6工作台定位及第19轮审计；复用成熟事实/记录/应用/存储owner，不重做采集器或复杂GUI。旧consumer和证据继续按原范围读取。

## 三条相连但不串成单一路径的链

```text
在线闭环：STS2/Reference Mod 或 Managed
          ↕ 原生事实/公开环境映射（Native Foundation、Connector）
          ↕ P1共享语义（SDK）
          ↕ Policy Runtime的受控执行 ↔ Agent程序端口 ↔ 完整Agent（可含Model）

独立记录：原生/程序输入与公开事实 ─→ Annotator Source4 ─→ Evidence ─→ 离线P1解释
          Runtime实际offer/Consume/Receipt/Result ─→ AgentRunEvidence
          旧Human因果lane ─→ canonical3/bundle3及原版本consumer

研究闭环：不可变来源 → 来源专用admission/用途/划分 → Projection/InputSpec/TargetSpec
          → 原TrainingService/worker/provider → checkpoint/Model
          → AgentComposition/ExecutionRequest → 在线新运行 → 固定评估
```

Mod是装载包装；Host Runtime是进程管理；P1是环境合同；AgentProgramPort是程序边界；Source4是独立录制来源。它们不是依次经过的五层服务。SDK/传输格式也不定义另一套交互语义。

P1记录到研究数据集之间是**阶段边界**：这次固定环境、采集/解释和可核对原件，让后续能构造第一轮数据。没有因此声明统一数据消费合同、dataset split、Input/Target或P1训练reader已经冻结；这些仍由Q链owner按实际用途确定。

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
| STPD projector/Input/Target | 研究输入构造 | 明确模型看到什么、记忆怎样推进、训练监督来自哪，防把未来标签放当前输入 | Q3适配器/词表/配方待确认；[research map](../../../python/docs/DOCUMENT_MAP.md) |
| TrainingService/engine/worker | 研究学习 | 复用Run/attempt/checkpoint/更新数/预算与导出，不为新P1另写训练manager | 首轮配置/费用仍另批；[workload](../../../python/docs/STRUCTURED_WORKLOAD.md) |
| R2存储adapter | 原件/派生包↔存储 | 最简内容寻址存取给远程首训用，可复用原库而不强制Hub | 路径/核验提案和授权见[K5](CLOUD_MINIMAL.md)；当前无新上传 |
| Modal/provider adapter | 资源执行/只读数据入口 | 复用远程资源机制；必须证明首轮所需数据真实可读，不能借旧job名冒充新P1接好 | 当前P1消费与费用未验/另批；[K5](CLOUD_MINIMAL.md) |
| Hub/团队账号/Cloudflare登录/云页面 | 原有团队/共享通道 | 历史现成能力保留原consumer和授权；不让首训最简R2/Modal依赖整个团队产品 | 冻结前保留现状、接口/占位，不扩建；[K5](CLOUD_MINIMAL.md) |
| Task/result reader与Q1评估 | 新运行→证据/质量 | 区分自然终局、技术停止、unknown与策略效果，回答是否优于随机 | Q1方案后固定；[evaluation](../../../python/docs/MANAGED_SUITE_EVALUATION.md) |

## 合同/协议：为什么不是同一个接口

| 合同/协议 | 位置和原因 | 首训价值/状态 |
|---|---|---|
| Player Environment | Connector提供公开观察/Read/完整原生目录/Receipt的具体合同 | P1派生与执行的原始基础，不是训练张量格式 |
| P1 r1.3 | 当前环境信息/前进动作/承诺/失败语义 | 线上和离线解释同意当前公开决策问题；[唯一语义owner](../../protocols/P1_GLOBAL_R1.zh-CN.md) |
| Agent-session/P1子程序端口 | Runtime↔完整Agent的消费、query/choice/control | 复用同一受控入口接未来模型，NDJSON只是实现；[owner](../../../components/policy-runtime/docs/AGENT_SESSION_PROTOCOL.md) |
| AgentComposition/ExecutionRequest/Goal | Agent描述与一次执行意图、资源/权限/预算、任务见证 | Model不等于Agent；沿原应用启动/结果，不添scheduler；[owner](../../../python/docs/AGENT_EXECUTION_CONTRACT.md) |
| Record grammar/来源声明 | 来源原件事实、顺序、身份、缺口、完整性 | 核验记录可解释什么；不冒充P1/Agent消费/训练准入 |
| Projection/InputSpec/TargetSpec | 原件→研究输入/标签 | 明示有损转换、历史/reset、target/mask；仍为后续Q链决定 |
| 旧native-logical/text-menu/score-vector | 原历史在线/研究consumer合同 | 保留真实consumer/reader；不恢复第二学习主线，也不借归档标签删兼容 |

## 格式：为什么保存，谁真正消费

| 格式族 | 链中用途/消费者 | 对首训的价值及去向 |
|---|---|---|
| Snapshot/Reads/BoundAction/Receipt原字节 | 环境→P1/Runtime/Recorder | 来源事实及选择绑定，离线复验可追溯；不直接当所有Model Input |
| Source4 profile/bundle-v4/public-basis1 | Annotator→Evidence→source4-to-p1 | 新完整公开basis/Read及输入供P1解释；[report-v3当前scope](SCOPE_v0.3.zh-CN.md)区分bounded/whole_run，真实整局仍待 |
| P1 observation/choice/result | P1在线边界及离线派生 | 当前公开决策语义；模型投影、词表和学习目标后定 |
| agent-session-run-1目录/events/引用原件 | Runtime→typed verifier/replay/样本admission | 保存实际offer、Consume/ACK、Receipt/Result；直接Agent lane独立于Source4 |
| legacy episode-record-1单文件 | 原直接记录converter/reader | 有限复用原P1来源，保留原版本，不与当前目录格式混同 |
| Human manifest2/trace4/canonical3/action-space3/Read2/journal2/close1/bundle3 | 原生因果lane→Evidence/对应研究consumer | 成熟原件/经验可复用；旧数据转新P1高成本且缺失事实不补造 |
| record2/bundle2兼容及trace1–3/canonical2/ledger1–2/record1/bundle1 | 真实旧consumer/归档reader/fixture | 原用途保留，不转贴新candidate资格；[格式链](../../FULL_RUN_DATA_CHAIN.md) |
| Source1/2/3及bundle | 原声明/epoch/有序basis研究lane | Source3默认及旧专用view继续；不回填Source4新事实 |
| ResearchTransition/Parquet/dataset/use/split manifests | 来源admission→固定研究样本/用途/划分 | 复用现有数据管理；不是所有源都强制S-a-S′或同一Parquet语义 |
| Artifact manifest/chunks/content hashes | Store/R2/transfer/materialize | 既有内容身份可分块核对；大数据路径/云费用另批，不擅调全局上限 |
| Model/checkpoint/Agent package | 学习→加载/注册→新运行 | 后续冻结数据/配置/权重/状态身份；本次P1冻结不包含一个尚未批准的新Model |

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
