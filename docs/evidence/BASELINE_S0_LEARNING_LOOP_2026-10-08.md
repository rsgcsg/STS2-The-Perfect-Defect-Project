# G2-S0：真实 Agent 学习与原生执行闭环

日期：2026-10-08（Australia/Brisbane）。结论：**ACCEPTED_G2_S0_ENGINEERING_LOOP**，仅指下述已取样的 text-menu-v2 兼容实例。依据[唯一规范](../BASELINE_V1_SPEC.zh-CN.md)和[执行包](../plans/BASELINE_V1_EXECUTION_2026-10-08.md)。操作者及示范均为 Agent，不是 Human。

## 固定身份与集成

- 仓库：`rsgcsg/STS2-The-Perfect-Defect-Project`；base `develop@8b4ff5e6caba620a0100b39f998e8140fb93bc64`。
- 已验证源码/训练 producer：`05a1ce373b84aeb168f73576f61d7cc7bc31e55e`。源码 PR168：[PR #168](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/168)，普通 merge `14b26095137a3b754767c55f9341a7eff32a322d`。最终source/test head为 `29498e1bfd2bc3040d0ee9a8a10f517277411a17`；其相对producer仅修改测试资源关闭顺序、检查先后及对应文档/断言，未改变可执行组件、模型或数据身份。
- 当前源码 hosted CI：[37698381490](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/37698381490)，Linux、Windows、portable 汇总均通过。其含义限 source/test；原生运行及训练分别由本报告的本地证据支持。
- Connector `1.3.0-rc.14`、SDK `1.3.0-rc.6`、Host `1.1.0-rc.25`、Policy Runtime `0.1.0-rc.23`、Game Mod `0.2.0-rc.28`。
- 精确游戏：macOS arm64 `v0.111.0 / 41cef1ea`；DLL SHA256 `9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4`。
- 统一安装/实际加载 DLL SHA256：`ffedb91cb84295f1880f6e43bf2568526e6a4123e6e5ed70f095d2c909ecd8d1`；MVID `511a2e0e-bc5b-4548-b228-9808e1b04aa4`。进程以明确的 exact canary admission 启动；不等于通用发行资格。

组件 revision、源码 digest、workspace provenance、产物及 runtime instance 保持各自含义。该包普通 merge 到 develop，保留 path-scoped provenance；没有 main/云服务晋级。

## 原生与输入资格

完整 `npm run check:exact-game`、382 项精确游戏 C# 测试及 repository/identity/BOM/closeout 通过。结构模型及旧输入回归160项、S0 runner15项、数据转换40项；完整 Mypy288个源文件通过。完整跨平台 source gates 见上方 exact-head 回执。

stale/unknown/Stop义务有明确source回归：`components/policy-runtime/test/text-menu-v2-runtime.test.ts`覆盖fresh decision替换已知stale、连续stale交还、unknown原动作保留且不复活；`components/policy-runtime/test/policy-runtime.test.ts`覆盖unknown禁止重试、held controller/未完成policy的Stop、deadline/预算交还；`tools/test/baseline-s0-runner.test.mjs`覆盖unknown后不执行第二tick、fresh控制释放、Stop先于Host关闭及失败清理。`components/connector/host/tests/STS2Connector.Host.Tests/TextMenuV2Tests.cs`覆盖不完整C和unknown不能声称出牌成功。这些是source/faithful-fixture证据；最终实采为0个unknown，不能冒称在最终产物上人为制造并验证了真实unknown恢复。

独立审查分工：native owner/公开presentation由`g1_environment_audit`审查，v2曝光与capsule/data合同由`s0_connector_review`审查，模型/用途及实际cohort由`g1_ai_closed_loop_audit`审查，模型位置关系/实际导出和learned重放由`g1_learning_audit`审查，root生命周期/CI由`g1_workflow_execution_audit`审查，最终证据冷审由`g1_cold_engineering_audit`完成。Lead `/root`在源码`05a1ce373b84aeb168f73576f61d7cc7bc31e55e`亲自核对实际文件、测试、refs和runtime记录，接受上述G2-S0限定范围；worker文字不替代这些原件。

新的独立 Defect A0 canary 重复读取同一封存观察的第一页5次，再按1024字节分块组装4508字节，字节/hash一致；下一次 ReadCurrent 的封存编号从1到2。Source 路径确认 ReadSealed 不调用 native capture；封存编号本身不是所有 legacy/native 扫描的计数器。该 canary 已记录 fresh controller handoff、原生进程 exit code0、`forced=false`。

真实反例促成以下 owning-layer 修复并经独立审查：

- 同一张牌的提示再次打开必须恢复其原生 hover 生命周期；有效空提示与读取失败分开。
- 信息动作绑定当前公开主体/owner，不能只靠不同 opaque action ID。
- 退役 creature 与淡出 orb 不冒充当前 navigation 控件；真正损坏的 owner/ring 仍明确失败。
- **撤回 logical queue 必须等于 UI roster 的假设**。原生 Channel 会先入队、等待后才更新UI；独立 UI control referent 保留实际 slot/显示文字，logical context 单独保留。训练组 capture71–73、开发组65–67实际捕获此差异，仍为完整交互；其中共5个进入了实际 policy offer。
- 只保留原生已经声明的 settling；partial/missing继续存在且零choices，缺数据不能创造新的 readiness。
- text-v2移除未打开的 orb/intent/power 提示正文及其 referent 副本，保留当前牌面、公开数值和真正进入后的原生提示内容。最终547份raw capture的对应路径检查为0项提前暴露；测试组实际空Strike提示有效。

S0仅把**实际准入的 policy offers**交给模型。其余已取到的 raw captures 保留为诊断，不能回填成 W 历史。I/F关闭，receipt/control/history/opaque ID不进入模型。每次新capture仍重建 E/绑定；同公共state只重评分不重复推进W。

## 数据与用途

三次新进程、新profile generation、新种子，共享同一无active-run的解锁/偏好模板；Defect A0。模板 SHA256 `b2d8c8ff84d32e50a03d205fde28a78c2082a05ec575ecf10d77c1ed0057cd8f`。这是条件相同的独立游戏起点，不是强统计独立性或泛化资格。AI原生console只在隔离准备中解锁 `DEFECT1_EPOCH`，未更改共享存档或游戏规则。

| split | run | raw captures | 实际 offers | N labels | native delivery / play |
| --- | --- | ---: | ---: | ---: | ---: |
| train | `s0-collect-10bf537c-ab73-4f84-8663-bef01a3921ed` | 197 | 60 | 59 | 23 / 14 |
| dev | `s0-collect-87df3e9e-9d78-4e2d-a08d-008aa6f09cbc` | 157 | 54 | 53 | 22 / 13 |
| test | `s0-collect-ca03e2ed-2700-4c32-8ac7-d71bdc6c092e` | 193 | 60 | 60 | 25 / 15 |

总计547个capture、174个实际policy offers/model inputs、172个标签；373个未offer的capture明确排除。三组均reset1次，174行均advance；实际数据没有score-only行，该路径由独立fixture验证。每组有2次同一张牌的提示进入，均经过原生返回再进入。train/dev在奖励页teacher明确abstain；test达到预设预算。实采出现17次已知stale_snapshot非投递，按既有Runtime重新取得当前basis再作新决策；全部无unknown/run_error，Stop与fresh controller release确认通过。

已审核 converter 从原始bytes、SHA、complete C、真实offer/result、Runtime decision/dispatch/result/successor、seed/template/profile/episode证据逐项重建，独立审查重建结果与保存source完全一致。Source SHA256：`0df0115783238c99070e6cc597ff3964a9541ae6cb8fa0c4c8170a331bdda584`。失败及前代候选记录保留，未用于本批训练。

使用已有 ManifestArtifactStore、RunReporter、SQLite CurationLedger。新私有ledger有1个training claim、2个test-purpose claim（分别标dev/test）；**仅train group/source记training use**。这是负责人准入的新Agent工程数据，不伪造 Human/Managed exact indexes，不主张完成全库Gold盘点。计划与实际run ID完全一致，产物父链保留combined source和固定split。

## 实际训练与导出

固定 S-M2-0：180545参数、d96/K1、一轮关系聚合、共享byte encoder、observation-only W；CPU双线程/FP32、AdamW1e-3、seed0、TBPTT4个advance、3epochs/max80updates。没有测试驱动选模或追加训练。

- 实际45次optimizer update、177次训练标签使用，3epochs完成；本机耗时 17.491 秒。
- train mean CE：1.428863 → 0.792203。
- 单个dev run的teacher一致率 0.735849；单个test run为 0.600000。这些仅为小样本描述性工程结果，不是游戏强度、胜率或可靠泛化结论。
- 付费累计 **USD0 / USD20上限**；无外部model/API/GPU任务。

| artifact | identity |
| --- | --- |
| training run | `971f38d69dc2989aa93db01494b3a1602ea83320ebf5c5e97c9d17a73f3e136d` |
| training input | `e4f6fce12813daef045103f2048d34102eae6ebaf2e83785dbfe142b803f120b` |
| checkpoint | `8638e0f43068c2aca29209d5fdbee2ce17c6dbef5500446194b4e317e172a1ad` |
| model artifact | `d21804b37b886621755f5d3ea27c0d4f8704d21ae9cf7221a1f352bb087019bb` |
| completed result | `5765bd30de3396e3cb59471718f5a0a6f4f8ab0c910c61baba51f8943cad4c31` |
| Agent model ID | `cf6ec294be21b0844c196f350e85f175e4a991ac56950d3770945e52f3a01333` |
| adapter code SHA256 | `2a29d76851a6d80f16253de9c028375cecfae3c279f5155627a775bc64534a1f` |
| model manifest SHA256 | `728e5496e0ca9732ecfca2c7360fd01b828929f50ac782518be8e6916ebc48b9` |

safe tensor-tree保存模型及AdamW状态。独立核验CAS completion、所有父链、payload bytes与哈希；完整dev54＋test60行中，checkpoint离线计算与导出包在线scorer的logits/W逐行严格一致，I/F/receipt/history排除通过。数据与权重不入Git。

## Learned Agent 原生执行

新种子 `S0LEARN1`，run `s0-evaluate-00f06143-390e-4acf-88ab-950e2ac6da05`，从零W启动可信NDJSON2 adapter，使用上述已训练包，无teacher fallback。实际194个capture、60个policy offers、32次native input delivery：11次play、18次activate、3次end_turn。包含选牌/选目标/确认出牌，达到奖励及卡牌选择页；不是只在单候选地图上执行一次。

独立重放全部60次实际输入，完整C、实际logits逐值相等、argmax、actor/package/manifest/code、Runtime decision/tick关联均通过；6份不可变Runtime evidence文件校验通过。现场trace未序列化W，因此这里只证明实际logits与相同输入重放一致，不声称直接观测到了进程内W逐位相等。native delivery不是causal Commit或`S'`证明。

模型在奖励页出现反复打开选牌再跳过的循环；训练示范在那里abstain，尚未解决奖励推进策略。预设60次调用预算正常终止：fresh控制快照确认exact runtime无controller，Runtime stopped/released，无unknown/error。Runner返回exit0；之后通过Host-owned进程盘点确认没有游戏进程。**该learned run没有保存episode.close返回的native exit-code/forced字段，不能把后续进程盘点冒充该字段或补写历史退出回执。** 上述read-canary的graceful exit记录属于它自己的独立episode。

## 证据位置、回退与后续

本机原始文件保留于实施worktree的 `.local/s0/`：`read-canary-9/`、`final-collection-rc14/`、`data/source.json`、`training-use.json`、`training-summary.json`、`training/`、`artifacts/`、`export-verification.json`、`learned-evaluation/`、`learned-run-verification.json`、`native-presentation-verification.json`、`post-run-process-observation.json`。它们是本机复核入口，不是公开下载承诺；报告中的hash/IDs才是固定引用。

安装前产物由Game Mod lifecycle保留于 `apps/game-mod/.local/deployments/2026-10-07T21-56-36.431Z`；更早候选亦保留。回退使用该owner的rollback入口，不手写复制文件或改历史producer。共享存档、既有工作台服务及原队列保持原身份。

下一门槛是扩展示范/场景和奖励推进、原生flattened/event profile及Human CaptureProfile、产品GUI与恢复，再按固定矩阵进入V1/G3/R1。此报告**不证明**全角色A0–A10、所有L/SX/SC机制、完整瞬态历史、Human来源、完整游戏/产品、云部署、长期稳定性或科学质量；也不把Source/CI、installed/loaded、bounded native、learning各层混为一项。
