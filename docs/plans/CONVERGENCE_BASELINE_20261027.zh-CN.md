# 收敛与新基线：当前任务和收敛后待办

本轮目标：让新接手者从项目入口恢复目标、能力、操作、旧决定和下一步，减少下一轮查找/返工；不以文档数量算进展。用户已在本对话批准A/B/C（含Workshop stage1–stage5），[PR256](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/256)已普通合入develop；获批资产处置已完成。固定版本由[收敛基线](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/convergence-2026-10-27)定位，实际归档/关闭/删除见[执行收据](../evidence/CONVERGENCE_DISPOSITION_EXECUTED_20261027.md)。没有后续卡执行许可。唯一写入/验收负责人：本轮 Codex 主对话；只读子 Agent 不拥有产品或共享文件。

## 本轮边界与恢复

- 开工基线：`origin/develop=f71507217d895fa484cb0cfe589a38e154579cb7`；分支 `codex/convergence-new-baseline-20261027`。本机旧主 checkout 的 develop 为 `415e8e5a`，不能据它派工。最终候选/CI 在 PR 和[事实收据](../evidence/CONVERGENCE_BASELINE_20261027.md)核。
- 输入：用户本轮直接委托、第 7 版收敛卡及第 7 节三底稿、定向原始对话。原卡和主报告在既有交接根 `/Users/fire/Desktop/SpireAgentProject/claude-handoff-2026-10-13/replies/`；不是另一套永久规范。
- 可写：根入口、地图、规则/现有导航、已解决问题/待办/决定、去向和审计文档；工程工具仅 closeout 的 CURRENT ≤40 行/无历史段两项检查及其测试。**不改产品代码或合同含义。**
- 不用游戏，不操作安装/评估/训练、录制原件/Store/Q。用户本轮声明录制优先；旧 `GAME-SLOT.md` 的空闲不能覆盖这条声明。共享登记仍在上述 replies；本轮不占用它。
- 审批包于2026-10-27 02:34 AEST交付，原5小时交包硬限内完成，累计93分钟含CI。审批等待期间未推进工程，闹钟后来按用户要求撤除；本次批准后仅完成同一任务收尾，原累计成本不重置，不恢复旧待办。
- 当前服务保护：日常 Workbench/Q 为 X2 release 的既有服务；评估 App 为独立原服务。它们不是本轮启动的任务，聊天收口不停止它们。开工线程快照只有本轮在工作，Q3/X1/X2 已交付；标题/静态进程不证明任务通过。
- 本轮交付：一个文档/工程工具 Draft PR、去向表、重构建议、新人问答、集中待决表。已有 K 账本复用，增量以其准确基线 `60dc0a89` 比较，不按文件日期猜。
- 完成/停止点：用户批准后的文档、逐项归档处置和基线收尾后停止；没有执行T1/W1/M1/M2/M3/X3、训练、评估、游戏/安装或Q操作的权限。闹钟已删除，不再创建。

## 收敛后待派发（不是执行许可）

原始卡沿用上述 replies 的准确文件名；总顺序为 `PROMPT-ORDER-20261026.md` 第 4 版。下表只补当前位置、依赖与冲突，不改卡正文，不重新批准其研究含义、费用或运行窗口。收敛合入后，规划者先据新地图核卡，再由用户派发。

| 卡 / 原文件 | 放在哪、交什么 | 启动条件与交接 | 写入和资源冲突 |
|---|---|---|---|
| T1 `MODEL-INPUT-V2-CARD-20261026.md` | 研究输入/训练；438 新投影、新 InputSpec、价值头方案、T2 可执行准备 | 收敛后核卡；不正式训练、不改438权重，数据资格沿原 Curation | 与 W1 共用 cohort/训练接线须先分写入人；与 M1 保持输入/执行边界，adapter/注册版本仍有共享连接 |
| W1 `A10-WEIGHT-CARD-20261027.md` | 用户10-27扩展：录后入池、每次实验选局/权重、模型血统使用台账及T2草稿 | PR258；新规则首次看表说yes，原定义批准可复用；本卡只合代码不冻结 | 旧414原文件不改、DEV/TEST不加权；Curation核实际权重/父初始化，跨新Snapshot续训仍归训练实现 |
| M1 `MANAGED-SPEED-CARD-20261026.md` | 后续已由用户派发：查清/出方案、同snapshot早停与细分计时；[本轮事实](../evidence/MANAGED_SPEED_M1_20261027.md) / [下一版提案](MANAGED_SPEED_NEXT_20261027.zh-CN.md) | 本轮27新槽收尾，原unknown保留；释放执行位/后台队列按更新卡先出方案，未实施 | `managed_evaluation.py`/Runtime供M2接现有结果；Host service/driver诊断与M3后续普通merge协调，未改native-baseline；录制优先 |
| M2 `MANAGED-TIERS-AND-FIXES-CARD-20261026.md` | 同评估/Store owner；保存档位、打包、M051等根因和RL轨迹格式 | **M1合入后**；轨迹格式交RL准备consumer，正式成绩证据不减 | 与M3同Managed支持域/对照代码须错峰或同一写入人；不因一张卡已批就接受协议例外 |
| M3 `MANAGED-COVERAGE-CARD-20261026.md` | Host初始化→录后诊断；个人原样进度重建、自动重演/逐点覆盖 | X2的种子/进度保存已做，跳过2.1；先证个人profile支持，再接Q后诊断。真游戏headless诊断仍须用户选择/窗口 | 与M1/M2的Host及M2诊断档、X3的Q维护接线有实际依赖，不能宣称全文件不重叠；录制/Q写入另派窗口 |
| X3 `Q-POLISH-CARD-20261027.md`（可选） | Workbench维护器；快速状态、游戏启动不再开新重活、现有安装 | 用户不录且明确安装窗口；不改变原入库/派生/读回结果 | Q/Workbench与M3接线一人先改；正在做的一步不能靠强杀伪装可暂停 |
| T2、RL准备/试点（未发卡） | 正式第二轮模仿学习；随后再按采样/奖励方案建设RL | T2等T1/W1、整局切分/新数据/预算和用户“开训”；RL准备等规则与M2轨迹；试点再等T2/M2/准备 | 不自动启动，不借旧学校或云额度；Reference/Managed结果分开 |

**对排程的纠正**：原总顺序称四张卡文件不重叠，这还没有源码依据。独立部分可以并行；共用状态机、合同、原维护器或同一机器预算先定单写入人，不靠四个对话各自 ordinary merge 消除逻辑冲突。规则不按卡数机械限制项目。

**用户已采纳的发卡前纠正（C，仅方向和交接，不是执行许可）**：M1新增早停必然改变卡住样本的停止原因/时长，前后“逐局原因相同”只适用于未触发新早停的正常对照；早停另测 unknown 和资源释放。M3旧“等Q3安装/无种子”的事实已被X2取代。T1允许W1未完回退414，而总顺序要求W1完成才T2，建议按总顺序等待W1或由用户明确选择旧权重，不能静默回退改变实验。

## 待修与重构去向

本表是本次收口留下的未完义务，后续分配后更新原条目，不另建注册表。首次/证据来自原报告，不把本轮重新发现当第一次；每个未派发项均无执行者。

| 位置 / 首次 | 挡住什么、现在知道什么 | 负责人 / 去向与下一判据 |
|---|---|---|
| 输入→模型 M023/M024/M049；F2/F收尾10-26 | P1无序成员的排列仍影响438，1,936对齐决策有10个argmax差异；不是Host全面等价 | T1待派发：保旧输入/权重，新投影另版，实际shadow/打乱验证 |
| 执行→保存；F收尾10-26 | 19自然局生命周期中preserve累计占59.74%；位被占到保存完 | M1→M2待派发：同一账本有界队列，关闭/证据状态分开，保存失败留分母 |
| Managed M051；F收尾10-26 | 1局敌回合settling至512MiB证据限额，原生等待点未定；不是磁盘满 | M1早停，M2根因；原unknown只读保留，新诊断不得重投原offer |
| Managed M052/M053/M035/M038；F/F2 10-26 | 生成卡context/hidden、商店counter、Skip/金纸有差异；不同版本归因未全定 | M2待派发；缺同版本/UI证据则保未知，接受例外另交用户 |
| Managed M016/M018；既有例外表至10-26 | 支持域/连续处理未全覆盖；没到达不是通过 | M2/M3碰到再验证，不以自然终局比例消掉机制分母 |
| 原件→重演 M003/M048；Q3/F 10-26 | X2已修新局种子+原字节进度并真人验收；旧29组启动事实仍缺，个人profile未全重演 | M3待派发：从X2真人A10原件开始；旧未知不补造，首战4点不算整局 |
| Q状态/调度；Q/Q3/X2 10-25/26 | status会超时；单步cache-trim可能遇下一局，尚未证明零卡顿 | 可选X3待派发，M3接线与其协调；本轮不动生产Q |
| Reference多开 M020/M026；F2 10-26 | 可行性有1/2/4实例证据，关闭渲染错误未过正式资格 | 未派发；M3只可在获批窗口作诊断，不能作正式成绩 |
| 新候选评估计划；C报告10-25 | 全未开矩阵曾拒绝、未来候选身份未产生；当前旧100链可用不等于T2新矩阵可执行 | T1的T2准备核查，过大则保剩余，不重启旧100 |
| checkpoint/训练/数值；233与旧J-min | 旧数值失败和恢复边界保留；正式学校新训练与新模型强度未证 | 原consumer/收据保留；PR233适用SOP定点吸收，未做的多模型连续使用仍单列 |
| 环境/依赖和Managed大补丁；多次Q/F/X失败 | Python解释器、双Node consumer、可编辑源码绑定与补丁升级成本重复 | [重构建议](CONVERGENCE_REFACTOR_20261027.md)：下次真实环境/上游升级触发，不在本轮改代码 |

完整的旧M编号/失败/范围留 `replies/MANAGED-P1-EXCEPTIONS.md` 与 [Host原指南](../../components/host-runtime/docs/MANAGED_P1_EQUIVALENCE.md)，本表不接受例外，也不把全部编号改标已解决。Q3要求搬入的停用/空WAL/原异常和Tool换代问题已进入 [RESOLVED_ISSUES](../RESOLVED_ISSUES.zh-CN.md)。

## 已批准收尾结果与停止点

A：PR256按准确候选7397d6bd普通合并为`ec03de11c12b7d5fbf1066ff7c8d25e9824bcc12`，原完整CI与准确合并头CI均通过，同树证据保持。B：6个旧PR关闭、34条远端分支删除；10个新归档tag加24个K3既有tag逐一核目标SHA，先读回再处置；Workspace与本机资产保留，执行时的GitHub自动删本轮topic副作用已恢复并记录。C：重构建议和发卡前纠正已采纳到本任务/原建议页，未执行任何产品改动。

当前导航和收尾收据随本次文档收尾集成；最终固定提交只由[基线tag](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/convergence-2026-10-27)标识，远端tag核验和完整收尾结果归用户指定replies的`CONVERGENCE-NEW-BASELINE-CLOSEOUT-20261027.md`。基线不授予Managed整局、Human研究或模型效果资格，不冻结整个项目。下一步仅等待用户派发已列后续卡。

## 抽象审计：两个层面

这是本轮有界阅读结论，不是新的架构或全部产品审计。整体按现有连接看requires/provides和实际caller；细节只展开真实反例/重复成本。没有新发现的连接写“保留原owner”，不等于全资格通过。

| 连接 / 真实owner | 本轮核到什么、结论与去向 |
|---|---|
| 世界→Host→公开环境 | `managed-player-environment.mjs` / Reference driver各适配原生事实，Host管隔离/身份不判合法性；保原职责。Managed初始进度验证只认`normal-sp-all-unlocked-v1`且hash精确，个人profile支持属于M3真实缺口。 |
| Connector→P1 | 同一SDK reducer/lowering，顺序语义归协议；r1.6显式选择，旧r1.5没有自动升级。M035/38缺准确UI见证时不能在比较器忽略字段。 |
| P1→完整Agent / Runtime | 模型在研究侧评分，Runtime管控制/预算/unknown；438数组敏感是输入owner问题，不能把全部Host变成固定排序来冒充等价。T1另版处理。 |
| 环境/Agent→原件 | Annotator保存来源与私有启动附件，Runtime封Agent原件；种子/进度不进公平观察/模型。X2保存链已通过，旧缺失不可补造；不由记录组件决定训练用途。 |
| 原件→派生 | `p1_data_preparation.py` / 共享核消费封存原件，投影不是原件；Q2隔离坏派生、留原异常，修复方法进旧解法表。非P1来源各保合同，不把Source4恢复为主线前置。 |
| 派生→用途/切分 | Curation拥有每次实验的用途/曝光，cohort草拟整局切分；不把滚动31局当旧414冻结，也不因Q自动入库自动准训。W1仅新配置/预览。 |
| 输入→训练→Model/CP | 原TrainingService/Ops/Scheduler/worker/Store链已复用；school与Modal是资源选择，配方/数值/预算不是provider决定。8步短测与正式训练分开，T1准备T2不等于开训。 |
| Model→运行/评估 | 注册绑定实际Host/Runtime/Input，不等于模型语义分裂；正常Reference/Managed共用应用run_suite，另catalog-only旧CLI有真实consumer，无依据一刀合并。 |
| 评估执行→保存/结果 | 同一durable ledger管槽位/资源，executor等待preserve后才close，是真实热路径成本；M1/M2按责任拆阶段而非新控制器。每局unknown/cleanup/typed/R2状态不可混。 |
| 应用→人/Agent入口 | GUI映射同一native/workbench Model owner；PR233独有SOP本轮定点接回，源码能调用不代表每模型/暂停现场全通过。M3/X3的Q接线可能同文件，原“全独立并行”无依据。 |

细节检查已亲核：`evaluation_ledger.py:275–325` 的Managed dispatch实际调用应用run_suite；`managed_evaluation.py:346–485`的execute/finish/preserve/close顺序；`managed_suite.py:406`的catalog-only consumer；`native-baseline.mjs:55–64`初始进度门；Q维护器空WAL/异常/同原件别名；`native_workbench_api.py:994–1035`与Runtime mutation栅栏的GUI/非GUI同源。原测试/原报告是证据入口，本轮不运行这些产品路径。
