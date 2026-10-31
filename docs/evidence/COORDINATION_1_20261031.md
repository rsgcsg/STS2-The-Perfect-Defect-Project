# 统筹-1：全景、入口与未完义务复核（2026-10-31）

项目已有采集、保存、分配数据、训练、模型接入和评估的共同链路；当前主要问题是入口过时、源码交付与实际可用混写，以及研究建议被写得比证据更确定。本轮修文档与导航，不重建架构，不启动后续运行。详细私有计划和原始实验结果保留在原授权位置。

## 调查对象与当前事实

调查基线为 `1693f70fd1c5a8e301034d9782e728ae96c62db2`，先核远端develop与此一致。复核用户已定方向、10-27收敛、G2、E1/E1b、MAINT、D2/D2b原结果，沿真实入口读当前源码。开工时29个open Issue、0个open PR；这是时点快照，不是实时状态服务。没有重跑游戏、训练、安装、R2全量保全或全部数据使用账。

- 目标仍是能实际使用且持续变强的STS2 Agent；[L1–L5](../DECISIONS.md)为方向尺子：A0约5%、20%、80%、100%，并逐步到A10约5%、50%、95%。本批模型结果未见通关，不宣称达到L1。
- 原T2数据为53局/24,341点，41/5/7整局划分。长训提高TRAIN拟合，不能据此证明泛化、数据无噪声或模型容量已到顶。
- D2最高完整检查点为段内8856/累计12600；8928保存失败，末72次更新未持久化，未达到经验平台。125份完整CP的原保全收据保留；修复归[#338](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/issues/338)。
- D2b预选五版各50槽，246自然负/4unknown；5976对2016共同49seed层差+2.55，普通95%区间[+0.53,+4.73]，8对照家族区间均跨0；不是稳定越训越强。20/41局共同94seed差+0.81，95%区间[-0.24,+1.95]，分不出来。n10水晶球小游戏未支持，原审计未准入，新100未开。元数据失败试跑27开/0准入另存，不并入更正后成绩。
- RL2永久停止、不恢复；v337推理权重为2691更新，CP335完整恢复状态为2675更新。MAINT补拷12/12、1,967,518文件核同，旧0/12失败不是当前保存状态，原失败不回写。[维护收据](MAINT_1_20261030.md)拥有准确保存范围。
- PR328通过20更新/实际换模/两完整CP的工程短链；200步、完整首小时及长跑学习效果仍未验。后台预取保批次门未过，默认关闭；没有新RL3配置授权。
- 日常最近已核安装收据为I1的Workbench `0009eb35` / Mod67，带r1.7原件见证；T2输入仍是冻结r1.5。H1源码已合，未装，用户实测0局。本轮未重新核实际加载，不能用develop的Mod70或Friday的Host58/Runtime59代替日常身份。

## 入口是否单一

[DOCUMENT_MAP](../DOCUMENT_MAP.md)拥有按功能导航；[COMPONENTS](../COMPONENTS.md)拥有职责；[原链路](../plans/p1-prefreeze-2026-10-18/CHAIN_PURPOSES.md)拥有关系与连接要求。本页是一次有日期的复核，不是第二套架构或操作指南。

| 功能 | 同一业务owner / 入口 | 多个表面的真实含义与剩余 |
|---|---|---|
| 打局 | Workbench `project model` / Runtime受控执行 | 实战页、游戏内按钮、CLI映射同一应用；Runtime工程CLI是下层入口 |
| 评估 | Workbench `evaluation` → `EvaluationLedger` | Reference/Managed是执行器，Friday `evaluate.py`直接创建原ledger；离线`local_evaluation`只读训练报告。已知GUI评估接缝仍归[#300](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/issues/300)，不因此重写全部评估 |
| 录制 | Annotator原生命周期，游戏内Platform为日常入口 | Source3/4保各自范围；H1混合是组合用例，不自动等于纯Human记录 |
| 入库/派生/保全 | 原Q维护器、Store/Evidence、Curation | 正常局自动处理；显式CLI供查状态、恢复和冻结，不是第二套日常流程 |
| 模仿训练 | Workbench training package/start/status → 原Run/Ops/worker | package不占GPU、不等于开训；provider和训练数值核分别负责资源与算法 |
| RL | RL3 recipe prepare/start → 原learner/actor/watch | 配方是顺序编排，不新增游戏循环；模板尚未批准 |
| 安装/发布 | Game Mod生命周期与developer kit原owner | 两种资产的安装不是重复；main正式发布仍按[#295](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/issues/295)触发 |
| 看状态 | CURRENT派工，STATUS证据，原App/job/ledger运行状态 | 不能把几份职责不同的页面合成一个手工实时数据库 |

源码抽核：[Friday包装](../../tools/friday/evaluate.py)109–118行调用原ledger；[reference_suite](../../python/spireagent/workbench/reference_suite.py)是调度；[离线报告](../../python/spireagent/workbench/local_evaluation.py)不打局。多命令不是重复业务状态机的证据。

CURRENT只给当前任务/下一步；STATUS路由有日期的声明；PROJECT_SYSTEM管文档归属；WORKING_RULES管共同行为；DOCUMENT_MAP给能力和SOP入口；旧FEATURE_STATUS保原版本细表，不再派工。启动统一为AGENTS→README→CURRENT→DOCUMENT_MAP，再按任务查，不增加全员必读。

## 有证据的混乱与本轮处置

| 原事实（基线1693f70f） | 后果 / 处置 |
|---|---|
| CURRENT/STATUS仍写D2进行中；地图还写T2等冻结、r1.7未装 | 会重启旧工作或误判输入；本轮刷新现态，不改旧实验原件 |
| PROJECT_SYSTEM与AI_COLLABORATION的初始阅读顺序和README/AGENTS不同 | 小事出现两套必读；本轮统一入口顺序 |
| H1指南仍等已完成的I1；RECOUNT把旧kit补依赖写成一般要求 | 旧依赖挡当前使用；本轮在原SOP补版本适用范围，H1仍待实际安装/用户验收 |
| 10-27重构/待修表仍把保存流水线、输入v2、X3写成待派发 | 易重复开卡；[原表逐行归位](../plans/CONVERGENCE_BASELINE_20261027.zh-CN.md)保已交与未验分开 |
| 旧Issue正文仍写48h/查GPU/一天后自动第三阶段，当前事实藏评论 | 本轮对少量失效正文补当前触发，保留原描述与评论；不批量销账 |
| 长训完整history持续进CP，codec有100,000节点上限 | 已发生保存失败；[训练owner](../../python/stpd/p1/formal_training.py)与[codec](../../python/stpd/workers/checkpoint_codec.py)负责修表示和全状态恢复，不能只抬上限再原样开跑 |
| H1用所选动作的原分数softmax决定低把握停线 | [mixed_runs](../../python/spireagent/workbench/mixed_runs.py)的阈值未证明适配长训概率；先量触发率/漏停，不宣称必定失效 |
| 已选Deployment规则尚无对应登记交付 | E3仅写规则，不能把Deployments页当完整现态；下次真实部署补实际登记，保原收据，不补造历史 |

没有证据支持“缺一套数据用途账”：[Curation](../../python/spireagent/research_curation.py)57–64行已有同SQLite三表；[p1_curation](../../python/spireagent/p1_curation.py)428–443行沿模型与全部父阶段的实际使用检查留出污染。私有索引只能指向原ledger/Snapshot/Run/manifest，不能另立准入权威。本轮未逐局审计近期实验是否都登记，完整性仍须各训练任务交付。

## 新用法的连接位置

下表区分用户已选的使用方式与尚未实现的接线建议；不定义新公开schema、不扩大P1/Runtime权限、不授数据用途。继续沿原图④（完整Agent）、⑤⑥（原件/投影）、⑦⑧（用途/输入目标）、⑨⑩（训练/运行评估）。

| 新东西 / owner | requires → provides | 现状与下一次忠实验收 |
|---|---|---|
| 人类排序：完整Agent决策选择；Workbench负责UI/记录，Runtime执行 | 原Connector公开完整候选、occurrence与稳定choice绑定；人看真游戏并点部分顺序 → 保存展示/排序/问题说明及第一选意图，走原choose/Receipt；未排序表示未评价 | 用户已选：全部动作、执行第一、可标P1/Connector问题，不需后台模型。[human-chooser](../../components/policy-runtime/examples/p1/human-chooser.mjs)目前只有单选与终端UI；未来复用候选/绑定，需验过期选择、Stop/unknown、封存及来源。问题标注不自动成为坏动作或训练标签 |
| 交替段标注：Workbench混合用例；用途归Curation/研究 | 原Annotator原生通道、Runtime模型原件、持久cuts与准确模型manifest → 可追溯的人/模型区间、每步可证或unknown的来源 | 用户已选按普通录制分段标人/哪版模型。现H1是两通道并非统一逐步chooser；统一端口在设计上可行，须另版验证切换、未发动作围栏、已发回执和来源。旧原件不改标；混合整局不作成绩 |
| 同种子组调度：actor/Exchange与`stpd.rl`优势计算 | 训练专用seed、固定行为模型/探索配置、group/replicate身份、原终局资格 → 同组真实回报与选择的组基线 | 尚未选定算法/组大小；不是改评估目录排序。先验组内信号和成本；unknown不重试，完整组排除会有选择偏差，必须事先定义缺组/零方差处理并保分母 |
| 训练内argmax评估：原评估ledger，训练调度仅触发 | 冻结已发布模型、专用评估manifest、原Host/Runtime/审计/保存预算 → 可比的argmax监测结果 | 复用原评估，不把采样训练局当成绩；永久隔离评估seed，保独立确认。具体频率/资源另定，不能称额外免费或用监测池作独立确认 |

人工chooser的P1记录不独立证明Human来源；H1的`native_operator`也不是自动的人类示范准入。可以复用同一端口，不表示要把GUI、标签政策和推理塞进Runtime。

## NLL翻倍而一选近似不变

| 无权动作指标 / 原样本 | 原2016 | 最新完整评分（段8856/累计12600） |
|---|---|---|
| DEV 2,725点 | 65.101% / NLL0.915823 | 63.817% / NLL2.053911 |
| TEST 3,226点 | 65.933% / NLL0.882810 | 66.212% / NLL1.896761 |

这是已发生的排名与概率质量分离，不足以证明错误全是动作多解。在模型选错的样本上，进一步压低示范答案的概率，即使argmax不变也会增大NLL；也可能只有少量灾难自信点贡献多数损失，须查逐点分布。DEV NLL不能单独当实战停止线，但仍是概率/拟合诊断；不因此把DEV并TRAIN或宣布数据无噪声。

建议下轮先复用既有预测/权重做CPU诊断：分正确/错误、决策类型、强制/多选统计置信度/熵/间隔、NLL分位与贡献、逐局不确定性；在DEV拟合正温度并冻结后，仅用TEST验证诊断，不用TEST调温或挑CP。温度缩放的概率校准思路见[Guo等原论文](https://proceedings.mlr.press/v70/guo17a.html)；它不是游戏能力证明。H1停线要另测漏停；RL采样温度需按起点模型真实熵/非argmax率试验，不直接继承某个数。排序/集合标签只在真实标注支持时另做对照，不因相似顺序就宣布动作等价。

## 重构与下一步建议（均非新执行许可）

成本是工程粗估，不是已批预算；包含受影响消费者验证，真实游戏/GPU预算另定。

| 建议 | 成本 / 风险 | 收益与推荐时机 |
|---|---|---|
| 修现态导航、旧表和SOP适用范围 | 本轮文档改动，低风险；避免篡改旧结论 | 立即归位，减少重复开工；不加新治理检查器 |
| CP恢复必要状态与增长history分离/有界编码 | 约1–2工程日，中风险：optimizer/RNG/sampler/cursor/LR精确恢复与历史兼容 | 下次长训前，解决已发生的数据损失；不单纯加常数 |
| 排序与交替复用原P1端口/控制，应用层留标注 | 约2–4工程日加正常用户窗口；来源/绑定/切换风险中等 | 下轮采集功能，先交最短真实全链，再谈排序损失/统计尺子，避免一张卡混安装、研究和新RL |
| 全TRAIN锚点覆盖及新轮采用测量 | 约0.5–1.5工程日，另有真实短链/首小时；采样目标与曝光风险 | 下一RL配置承接[#326](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/issues/326)、#319/#321，不只放大旧512前缀系数 |
| GUI评估接原ledger | 约0.5–1.5工程日，历史报告兼容风险 | 下次GUI评估改动时接[#300](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/issues/300)，保旧结果，不重写两个执行器 |
| Managed大补丁职责拆分 | 数日到数周且需准确上游验证，高风险 | 上游升级/重复改动热点触发[#301](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/issues/301)，现在不为行数重写 |
| 统一所有评估循环、手工第二用途账、全局共享可变环境、集中搬旧结果目录 | 不推荐；双权威/迁移风险大于已证收益 | 保原owner；私有实验登记提供固定索引，结果可原地保存 |

推荐顺序：先交本轮归位；再分别选择“排序/分段最短产品”和“既存结果概率诊断”，两者无共享写入时可并行；随后定输入/锚点/起点/探索和原小规模可学性检查，出现正证据再批下一RL运行。组基线、critic、奖励、探索和重用率不要一口气全改；未能提升也不自动升级到第三阶段。第三阶段只保未来可能，运行时仍为游戏与模型，不引入搜索。

同种子组基线可降低某些种子差异，但GRPO与RLOO不是同一估计器，也不提供逐动作因果归功。[RLOO原论文](https://arxiv.org/abs/2402.14740)与[GRPO原论文](https://arxiv.org/abs/2402.03300)的任务是语言模型，不能直接推出本游戏有效。GAE使用`r + gamma V(next) - V(now)`的残差及其累积，λ=1仍减当前价值基线；不能简化为“前后估值差”或“完全不信价值头”。见[GAE原论文](https://arxiv.org/abs/1506.02438)。这些是对草稿解释的纠正，没有选择新算法或阈值。
