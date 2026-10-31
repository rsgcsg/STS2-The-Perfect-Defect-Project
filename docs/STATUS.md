# Current status and evidence index

This page indexes claims, not live service state. Each observation keeps its date,
source and scope; refresh its owning status/identity before an operational action.
CURRENT owns the active task and next step; its linked active plan owns progress.
Historical execution matrices retain their original observations and obligations.

## 对局台已安装（2026-11-01，待用户对局验收）

[PR344](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/344)与修复[PR345](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/345)已普通合入develop，最终修复合入4513f038；安装源码为ab816a93，[对应完整CI](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/41225831372)通过。当前Workbench包 `cc39dc7fde988a4d4c50922cf72fe12c077f772fb3abd66cf6035312de362d66`、LiveUI `0.1.0-rc.24`、Mod `0.2.0-rc.72` 已实际冷加载，Mod SHA为 `48a9d4c396e665fe64a1af1670787b1556767e4749b4ed09970dc85b7e04b2ef`。Runtime `0.1.0-rc.60` 原包SHA为 `f9f31492be93318161f6f7a6672d715b30f345b72e6c2f20d581b451f769d4e0`，实际加载代码SHA为 `1ebbf1b0494221d1ea9eb7bf6ab5808d0c1a39094fa72754dc173b105ebe0510`。

实点确认主窗/录制/模型/动作窗可用、动作窗底部不再溢出；三个功能窗的关闭重开、最小化还原、拖动、缩放不停止原Human Runtime，前后同一run且0次提交。原生鼠标准备动作列表完成；菜单不满足映射时如实显示“等局面稳定”。随后实际退出游戏，原维护观察器在入库关闭时仍完成关联Runtime Stop；重开核回同一Mod。此检查没有产生游戏动作，也不证明真人或模型对局流程。

“停录制并保存”最初报 `store_not_found`，原日常配置绑定的临时Store已不存在。用户随后明确选择新建持久本机库；经原工作空间管理器建立、备份旧配置并重绑，没有导入或改写旧数据、没有开启云端保存。重启App/游戏后再次实点同一正式保存按钮成功，并经原 `game-desk recording --artifact` 入口读回准确索引 `261cae842fab49ba10c24d48500d95763379096226adec707d58ba0205164a52`：0游戏动作、11控制事件、score_eligible=false。此结果证明安装检查索引的本地保存/读取，不是完整真人对局验证。

第二条[mac-daily部署](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/deployments) `7462443141` 保留旧failure，修复后新增success状态 `21072988355`，仍固定于安装源码 [deploy/mac-daily/20261031T181337Z](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/deploy/mac-daily/20261031T181337Z)。没有因本机配置修复重建或静默替换安装包。诊断仍按旧environment类型解析GLOBAL状态，故Runtime摘要与后台失联时的独立恢复未通过，另记[#346](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/issues/346)。正常主用例不经此诊断解析。

首包Mod71动作窗溢出及入库关闭时漏模型退出封存的失败历史保留：首部署 `7462014787` 为failure，固定于 [deploy/mac-daily/20261031T173540Z](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/deploy/mac-daily/20261031T173540Z)，旧Mod/Launcher/Tool曾经原owner回滚。旧Runtime私有pin缺归档URL，原installer拒绝恢复，未虚构成功；物理Runtime60保留为本卡明确依赖。首次准备unconfirmed未重发游戏动作，后续新意图和Mod72原生入口已完成准备。

[#297](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/issues/297)数据用途决定已随PR344落文档并关闭；[#300](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/issues/300)展示局接评估账本、[#343](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/issues/343)窗口记忆/双击后补。用户0局；[#340](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/issues/340)保持打开，完整选择/执行/切换/封存/入库/读回、真人来源资格及模型效果均不作通过声明。当前唯一下一步见[CURRENT](memory/CURRENT.md)。

## 统筹同步（2026-10-31，历史）

D2/D2b均已收口；RL2永久停止且十二路补拷12/12完成；RL3没有启动。当前完整事实与调查范围见[统筹-1](evidence/COORDINATION_1_20261031.md)，下一步只由[CURRENT](memory/CURRENT.md)及用户新派发决定。

| 声明 | 当前证据与限制 |
|---|---|
| D2拟合与保存 | 最高完整CP段内8856/累计12600，TRAIN94.470%/NLL0.154903；DEV63.817%/2.053911，TEST66.212%/1.896761。8928保存失败，72次更新未持久化；不是平台或正常预算结束。[#338](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/issues/338)保修复义务 |
| D2b实战 | 预选五版各50槽，246自然负/4unknown；5976对2016共同49seed差+2.55层，普通95%区间[+0.53,+4.73]，8对照家族区间均跨0。20/41共同94seed差+0.81，95%区间[-0.24,+1.95]；n10原审计未准入，新100未开。原元数据失败试跑27开局/0准入另留，不混成绩 |
| RL2保存 | [MAINT-1](evidence/MAINT_1_20261030.md)12/12、1967518文件逐SHA核同；不改旧0/12失败。v337推理权重与CP335完整恢复状态不是同一对象 |
| 工程与运行 | PR328真实短链通过，200步/完整首小时/长跑效果仍未验；后台预取默认关。H1源码合入不等于安装，日常最新收据仍为I1组合，本卡没有核新加载 |

原结果保存在原任务授权位置，公开索引不复制游戏、权重、私有计划或本机路径。以下各日期段都是历史观察，不派发旧任务，也不把旧“当前”或未完成声明当今天的事实。

## 维护同步（2026-10-30，历史）

RL2已收口且不恢复；RL3准备卡A/B、数据卡D已交。该时点D2仍在进行；后续D2/D2b结果以上节为准。MAINT-1执行范围和原分母见[维护收据](evidence/MAINT_1_20261030.md)。

## 最新可恢复入口（2026-10-29）

RL 长跑前的收口（G2）：10-28 到 10-29 这一批卡都已普通合入 develop，代码固定在 `445b9f98`（PR283 合并），本收口文档 PR 合并后打 `convergence-2026-10-29`。

| 卡 / PR | 结果 | 出处 |
|---|---|---|
| M2e（PR276/279/282） | Managed 最终版本 Host `1.1.0-rc.58` / Runtime `0.1.0-rc.59`（PR282 标题写的 Host56/57 是旧名，以合并内容为准）；回放覆盖不再开新轮，RL 中 unknown 超过 5% 再开修卡 | PR282 |
| D1（PR278/285/286/287） | 编译好的训练数据包、合并上报、后台检查点、宽微批 8；L40S 13 轮 1.40 秒/步，比 T2b 快约 2.9 倍，最佳 DEV NLL 0.9036 在容差内；Friday 3 模型 × 20 种子共 60 槽：60 自然负、0 unknown；RL 起点定原 2016 步模型（平均最高 13.75 层） | D1 报告、PR287 |
| RL1（PR288） | 打局者 / 学习者 / R2 交换合同，学习者能从检查点续训；Mac 4 路每小时 136 局、0 unknown | [接口](../python/docs/RL_ACTOR_LEARNER.zh-CN.md) |
| F2（PR283） | Friday x86 精确 Host，冻结部署 `445b9f98`，8 路 RL 配置已备未提交；fc430 每核每小时 22.4 局，c6220 13.5 局；Mac↔Friday 两组各 315 点和终局全等 | [手册](../python/docs/FRIDAY_ACTOR.md) |
| E2 / MQ / S23（PR280/284/281） | 共享依赖和资源预约、合并队列、Managed 批量保存与流水线评估 | 各 PR |
| H1（PR275） | 混合接管录制（人和模型同一局切换），源码已合并，**未安装**；混合数据只作隔离证据，人的片段算不算训练数据等用户另批 | PR275 |

长跑怎么跑看[运行图](../python/docs/RL_LONG_RUN.zh-CN.md)。还没证明：真实跨节点 RL 闭环、学校 L40S 续训、任何模型通关。日常安装没有随本轮升级。

## 最新可恢复入口（2026-10-28，已被上节替代，保留原文）

第一批八张卡（M1/X3/W1/M3/M1R2/T1/M2a/M2b）都已普通合入develop，固定在[`convergence-2026-10-28`](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/convergence-2026-10-28)。各卡事实只在原收据：[M1](evidence/MANAGED_SPEED_M1_20261027.md)、[X3](evidence/Q_POLISH_X3_20261027.md)、[M1R2](evidence/MANAGED_SAVE_M1R2_20261027.md)、[M2a](evidence/MANAGED_M2A_20261027.md)、[M2b](evidence/MANAGED_TIERS_M2B_20261027.md)、[M3](plans/MANAGED_COVERAGE_20261027.zh-CN.md)、[T1输入v2](../python/docs/P1_T0.md#model-input-v2-and-the-separate-content-sorted-438-projection)；W1/T1见PR258/263。该tag只是源码/文档基线：不含日常安装变化（日常Workbench另由T2a升级，r1.7未装），不含T2冻结/训练、E1a测试修复或任何新游戏/数据/模型证据。

Managed现状：Host52/Runtime53源码；M2a新40局1 unknown（已另版修根因），M2b正式档8局0 unknown、约118正常局/小时（4路）。仍开差异与归属见M2a收据和原例外表；整局等价、Human资格、RL资格均未授。

## 2026-10-27 入口（已被上节替代，保留原文）

收敛文档PR256已普通合入develop；获批6个旧PR/34条分支的归档处置完成，固定版本见[收敛基线](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/tree/convergence-2026-10-27)，[执行收据](evidence/CONVERGENCE_DISPOSITION_EXECUTED_20261027.md)保每项SHA与归档位置。这里只宣布文档/资产收口，不改变下面游戏、数据或模型证据层。

[本轮事实收据](evidence/CONVERGENCE_BASELINE_20261027.md)汇合D/F/Q2/Q3/X1/X2：第35槽已封存，旧100仍未完成；Managed20局与Reference分开；Q3/X2已在正常真人局验收，新局有种子/原样进度，个人profile的Managed整局重演未通过。日常安装与源码版本分别列出，不从Mod包65推所有组件相同。

当前能力入口见[地图](DOCUMENT_MAP.md)，当前派工见[CURRENT](memory/CURRENT.md)。下面各节都是其原日期、原候选的历史观察，不是今日待办，也不把旧“current”一词当作新指令。

## P1 integration baseline candidate, observed 2026-10-16

PR180 normally merged at02636a94. PR187/405ba001 full dual-OS run38724470159
passed its own current source/test gate. C1 repair8c847f88 subsequently passed
independent source/CLI review and is integrated in the candidate; its fresh selected
gate remains due. Green405 does not qualify this changed source.
The current [P1 delivery](plans/P1_FORWARD_2026-10-15.zh-CN.md) includes real capture,
Agent consumption and paired Host records; it remains incomplete.

Managed434 smoke20 retained17 natural losses/3 unsupported censors/0 recorded
delivery unknown and20 confirmed closes. A separate49e Seeker target reached natural
loss onfloor11 after120 deliveries. Reference rc34/d6 stopped at16 submissions;
cleanup succeeded but native errors and full-run/P1 qualification remain open.
These observations are separate from current source and installed/load identities.
The older sections below are historical; they do not dispatch a second work queue.

## Auto6 and separate Goal reader candidate, observed 2026-10-11

PR174 exact8f45 passed full dual-OS/portable CI38118653994, normal-merged as471cbdbb;
integration CI38121970826 passed its verified execution-reuse route. Old failed jobs
retain their outcomes. Auto6 loaded Runtime33 and unchanged Mod42, reaching natural
loss Summary at floor11/score161 after294.473s. All886 events verify; Stop/Host/caller
cleanup passed, untainted.194 prior-frame joins are known;79 remain unknown.

Separately reviewed readerb928 (89 outcome cases/mypy) creates a new exact prior-witness
mechanical result through the existing application owner: reportd319 completed/task_ended,
observed loss, strategy not_assessed. Originalcb93 remains unknown; all original files
are unchanged. This source awaits its own selected full CI. See the
[current packet](plans/AGENT_RUN_COMPLETION_2026-10-11.md#auto6-result-and-mechanical-goal-projection)
for exact boundaries. No extra game, training or strategy qualification follows.

Native Host candidatea196 has independent source/exact-build evidence, not installation
or three-mode/two-presentation qualification. Final package and default Instant remain
pending. Installed Runtime33/Mod42 retain their own identities and rollback owners.

## Runtime33 candidate after Auto5 repairs, observed 2026-10-11

Normal merges retain independently accepted admission4a5449d1, portable service
fixture2b3915a0 and Runtime readiness9756d147. Their recorded local gates passed;
CI38115573320 on old6dc ended failed (Linux2/Windows2 ten admission/endpoint cases;
Windows1 two common-service cases). Actual Windows success awaits a fresh candidate.

Runtime33 sourcefb906728/BOM and immutable archive6c87e66b/content8317363d/code4ffd923b
passed independent review. The actual compiled package passed extended synthetic
settling and original idle-deadline cancellation; both typed seals passed and730 files
stayed unchanged. Root's two common-service cases passed against the same archive.
The existing owner installed33, loadedfalse/activatedfalse; Mod42 and original weights
remain unchanged. No native33 result follows from these checks.

[Current packet](plans/AGENT_RUN_COMPLETION_2026-10-11.md#runtime33-candidate-and-one-fresh-auto6-envelope)
allocates one fresh bounded Auto6 only after its final tuple/caller/startup gates.
It has not dispatched at this snapshot. Auto5 and all historical failures below retain
original identities and outcomes; Runtime32 archive/pin remains available for rollback.

## Auto5 and current repair boundary, observed 2026-10-11

Frozen6dc0f722 loaded exact Runtime32/code95b769a2 and unchanged Mod42/artifact2ef3dcf7.
Auto5 closed as a valid sealed **tainted incomplete failure**:126.630s,109 submissions,
86 delivered/23 known stale,355 events,78 prior-frame joins/31 unknown. Three reward
cards and later rooms do not prove strategy or Summary. The original common request,
composition, preparation, run and immutable evaluation are exactly bound; independent
auditb778cf99 accepted integrity/association while retaining the failure.

Final End Turn was delivered; all41 subsequent diagnostic polls were complete newer
settling observations over10.820s, followed by `successor_not_stable`. This establishes
the exhausted readiness-wait policy, not native delivery uncertainty or a native stall.
The game was closed before eventual readiness was established. Maximum consecutive
stale refusals was2, so this run does not demonstrate the new stale window's benefit.
Stop released/unloaded; Host exited0 unforced and strict process discovery was empty.
No additional game is allocated. Source-only Auto waiting and application admission
compatibility repairs are now active in separate worktrees; see the
[current packet](plans/AGENT_RUN_COMPLETION_2026-10-11.md#auto5-closure-and-current-repair-packet).

PR174's fresh CI38115573320 passed Linux1 and failed Linux2 with10 Python failures;
Windows remained running at this observation. Frozen6dc is not eligible for merge.
Earlier source/package/installation claims below remain limited to their own snapshots.

## Common execution and finite recovery source candidate, 2026-10-11

Reviewed a164+b507 unify effective Agent composition/request through existing owners.
Teacher models=[] and stateful PublicM2 have actual same-service process/Stop/seal
checks; full six application-owner suites passed270 with real rc32/typed Evidence.
Fresh public projections retain only artifact ID/SHA. Prior metadata fixtures, an
introduced private-path defect and setup-only TIME_WAIT failures remain recorded.

Runtime32/source9b092a51 implements the reviewed10s per-episode recovery window in
one existing deadline owner. Final component461PASS/3optionalSKIP, repository and
SDK232 checks passed. Immutable archiveccaf03a4/contentd21ed735/code95b769a2 passed
four-known-stale-to-fresh-delivery and actual idle-expiry compiled tests with typed
seals. These source/package gates do not establish a new installed/live result.
Test-only Connector provenanceb3dd538c leaves Mod42 and bundled SDK code unchanged.

PR173 CI38109117835 still failed one Windows SDK operation-case timeout after its
Python stage passed. Reviewed7d moves compiler preparation before that unchanged
case; its local controlled RED/GREEN is not a new hosted Windows pass. The next
complete candidate requires its own full CI. One scoped Auto5 envelope is recorded
in the [active packet](plans/AGENT_RUN_COMPLETION_2026-10-11.md#integrated-common-execution-and-one-auto5-envelope);
its original operation/PR closeout records actual execution separately.

## Agent run Auto4 closure, observed 2026-10-11

Frozen6330b8ce with independently reviewed/built/installed/loaded Connector23/Mod42
(source1c7, artifact2ef3dcf7/MVIDddf4c579) and unchanged Runtime31 completed one
bounded operational attempt. Verified sealed354 events record110 submissions,
90 delivered/20 known stale; three reward-card choices and later3 combat/2 event
interaction IDs show progress. No Summary: final three stale refusals triggered
existing `action_not_delivered`. Common Task/terminal remain unknown; operation is
censored, not strategy-qualified. Stop released control, Host exited0 unforced,
strict game-process discovery was empty and the caller correctly exited1 incomplete.

Independent after-run audit4bfd1747 confirms exact identities,77 prior-frame joins/
33 unknown and original-history preservation. Available EndTurn successors include
seven settling/C0 and three interactive/C1 observations. Missing original scored
frames prevent exact per-stale attribution. No Connector defect or old Auto2 cause
is proved. Root allocated source/test work on a finite operational recovery window;
no further native attempt or Runtime upgrade has occurred. P1 common startup remains
isolated: two real service-process cases pass, remaining gates/review are pending.
See the [active packet](plans/AGENT_RUN_COMPLETION_2026-10-11.md#auto4-actual-closeout-and-next-source-decision).

## Agent run Auto3 closure, observed 2026-10-11

The [active packet](plans/AGENT_RUN_COMPLETION_2026-10-11.md) owns current source
state and promotion gates; [CURRENT](memory/CURRENT.md) routes the active work.
Root candidate `0817351f` has normally merged independently accepted Runtime
successor diagnostics, common application task outcome and native End Turn queue/
exact-offer correction. Runtime rc31, Connector rc22 and Mod rc41 identities/BOM are
source candidates. Annotator production/version is retained with docs/test provenance
only. Final local Runtime442 tests plus3 optional skips, application189, repository
and exact Connector/Mod builds passed. Independent package/artifact checks and
existing-owner installation passed for rc31/rc41. The exact tuple was loaded for
Auto3 at frozen `f559c96c`. That single comparison is consumed: typed sealed,
untainted incomplete `auto_surface_not_admitted`, no reward/summary; independent
audit and Stop/signed Host cleanup pass. It does not qualify strategy or prove old
Auto2's timeout fixed. Detailed original counts/identities remain in the active packet;
rc30/rc40 rollback material and original Auto1/Auto2 receipts remain preserved.
Root accepted bounded Connector readiness/window/profile and application terminal-tail
reason scopes. Connector design received independent architecture review; independent
implementation reviews remain pending. Source owners are active, without a new
implementation receipt or further native attempt allocated in this packet.

Common task outcome source separates qualified Public M2 loss-summary completion
from operational failure, observed result and unassessed strategy; M0 without an
original qualifying witness stays unknown. This is source acceptance, not a new
native outcome or Auto2 cause proof. The [previous closeout](plans/BASELINE_BOUNDARY_REGRESSION_ROUND_2026-10-10.md#current-supervisor-round-2026-10-11)
retains restoration/parity, paired TRAIN fits, data limits and original Auto1/Auto2
valid sealed failures. They do not establish basic strategy or full G2/V1 approval.

PR171 head74260b61 passed all four OS leaves and portable in CI38100661045,
then normally merged into develop09440e80 with the same tested tree. Merge
CI38104068580 reused its execution receipt but failed committed patch hygiene:
one extra EOF blank line in a test. A separate bounded hygiene repair is active;
this does not erase the completed source tests or count as a passed merge gate.
New-source checks remain separate. Provider spend is USD0 in the same USD20 envelope.
Historical observations below retain their original identities and limits.

## Earlier G2/V1 candidate, observed 2026-10-09

- [Current specification](BASELINE_V1_SPEC.zh-CN.md) and
  [execution matrix](plans/BASELINE_G2_V1_EXECUTION_2026-10-08.md): implementation
  and verification continue; complete G2/V1 is unapproved.
- [Bounded Source3 learning and native delivery](evidence/BASELINE_NATIVE_LEARNING_DELIVERY_2026-10-09.md):
  eight Agent labels, three actual optimizer updates and verified export; two
  separate Model attempts. The second proves one original delivery and Stop,
  with execution/effect/cancel unknown. Neither is Human or whole-game evidence.
- E6 rc30 / Live UI rc9 artifact `6557b5ae`, exact-built at `44ca35e6`, was
  installed and cold-loaded in an isolated session. Independent review accepts
  main menu and unforced exit only; authenticated panels and exit/resource health
  remain unresolved. Later compiled-source changes require a new build.
- Source3 product import/preparation, native Model reuse and sampled-current carry
  are integrated source/test candidates through `659490a9`. Dependency/package
  convergence and real sampled collection/training remain separate gates.
- [Data/Host/model inventory](plans/BASELINE_DATA_HOST_MODEL_NEXT_2026-10-09.zh-CN.md):
  scoped historical/local/cloud data and an observed Hub deployment. It does not
  establish that current development source is deployed or that cloud training ran.

Next gates and selected but unimplemented designs belong in
[CURRENT](memory/CURRENT.md) and the execution matrix. The following combinations
are historical accepted observations, not a current-installation recommendation
or qualification transferable to the new profile.

## Historical dataset-library deployment baseline

Workbench source is `fedbed097e30fb78bf0e86efff560fd8b1263c99`. The accepted
20-source dataset baseline used Hub source
`3a4b8997d06011952a2248902c67f64f531dbcff`; the subsequent
[export follow-up](evidence/DATASET_EXPORT_2026-09-17.md) records the download correction.
The release integration receipt pins the active deployed source and image. Dataset selection has recording-date
filters, whole-result selection, responsive navigation and direct accepted-project-data
access. Runtime process provenance is merged without weakening game/artifact identity;
batch workers have a separate bounded budget, one-pass coverage and incremental hash/file processing. See [selection evidence](evidence/DATASET_SELECTION_2026-09-16.md).
PR17 and the release receipt own final integration, real batch and publication facts.
New multi-runtime datasets require the corrected reader for rollback; the fedbed
fallback retains it but can reproduce batch timeout. Native/tool bytes and original
Human evidence remain. Windows PR9 is integrated through Evidence rc.11; defeat is
a native terminal, separate from strict research sequence conditions.

## Previously accepted unified task flow

The unified daily collection flow is installed on the verified macOS collector and
its Hub was deployed at source `f4a21a2d67392b557c593cd396b894d1c3439779`.
[Exact acceptance](evidence/UNIFIED_TASK_FLOW_2026-09-16.md) records the immutable kit,
native identities, source checks, deployment, rollback and fresh member download.
Formal integration/publication is recorded separately by PR #10 and release receipts.

The owner-operated canary contains 44 accepted actions and 43 proved/canonical
decisions (four nested selectors). Its final End Turn was closed before a successor
boundary: one unresolved / real-failure count remains, with the explicit reason
`session_closed_before_successor_boundary`. Two pauses/resumes, durable Close,
verified upload and byte-identical member download passed. This accepts the bounded
recording/control/transfer journey; it is not a zero-failure Human or Full-Run gate.
No raw disposition is reclassified. Only in-progress/resume run boundaries exist.

Local model preparation, Recorder-to-model handoff and separately authorized Agent
reports retain their own boundaries. That historical combination did not measure
native model outcomes; the newer bounded native Model attempts are reported above.
S1 requires CUDA. No Windows native, GPU training, scientific or production-throughput
qualification is added. That historical acceptance granted no compute budget.

See [the member task flow](../python/docs/UNIFIED_TASK_FLOW.zh-CN.md),
[collection orchestration](../python/docs/COLLECTION_FLOW.md),
[model tasks and sharing](../python/docs/LOCAL_MODEL_TASK_FLOW.md), and
[selected-decision unions](../python/docs/adr/0008-selected-decision-unions.md).
The prior qualified combinations below keep their exact identities and evidence.

## Previously qualified operating combinations

The [operating-flow acceptance](evidence/OPERATING_FLOW_2026-09-16.md) qualifies
the managed Workbench installation and same-schema Hub rollout: 60 new Human
decisions proved/canonical, zero real failures, automatic Close/upload and exact
R2 byte readback. It is a bounded incomplete-run canary, not a new Full-Run claim.
Runtime components intentionally retain their separately verified source/artifact
identities; the report and PR link the integration checks and rollback.

The monorepo release has its own [acceptance record](MONOREPO_MIGRATION.md) and
[bounded Human audit](evidence/MONOREPO_HUMAN_GATE_2026-09-15.md): 580 accepted
decisions, all proved/canonical, zero real failures, verified Close/upload/download.
This qualifies the sealed candidate; historical results below keep their original identities.

PR25 rc.4 passes the bounded Full-Run Human gate: two uninterrupted native starts
through natural defeat, all 524 accepted decisions proved/canonical, zero real
failures or unexplained loss, and independently verified bundle3.
See the [final exact Human audit](evidence/PR25_RC4_FULL_RUN_HUMAN_PASS_2026-09-12.md).
The native gate remains tied to its historical exact artifact. Resolve current release tags,
merge refs and CI for integration; no merge creates new Human or research evidence.

See [current context](memory/CURRENT.md), [data contracts](FULL_RUN_DATA_CHAIN.md)
and [collection / future repairs](ANNOTATOR_COLLECTION.md).

## Historical evidence

These dated reports describe their own exact candidates, not current readiness.

- [rc.3 audit and rc.4 repair](evidence/PR25_RC3_TWO_SESSION_AUDIT_2026-09-11.md)
- [reward-owner repair](evidence/PR25_TWO_SESSION_REWARD_HANDOFF_2026-09-11.md)
- [final engineering cleanup](evidence/PR25_FINAL_ENGINEERING_2026-09-11.md)

- [audit and repair](evidence/PR25_BLOCKING_CHOICE_REPAIR_2026-09-11.md)
- [audit and repair](evidence/PR25_REWARD_OWNER_NOOP_REPAIR_2026-09-11.md)
- [audit and repair](evidence/PR25_POTION_INPUT_VALIDATOR_REPAIR_2026-09-11.md)
- [audit,
repair and bounded canary](evidence/PR25_DEFERRED_POTION_INGRESS_REPAIR_2026-09-11.md)
- [audit, repair and limits](evidence/PR25_ANYTIME_POTION_REPAIR_2026-09-11.md)
- [audit and repair report](evidence/PR25_ACT_EXECUTION_REPAIR_2026-09-11.md)
- [audit and repair report](evidence/PR25_POPUP_PRECEDENCE_REPAIR_2026-09-10.md)
- [candidate report](evidence/PR25_NATIVE_SELECTOR_CANARY_2026-09-10.md)
- [Follow-up report](evidence/PR25_FULLRUN_FOLLOWUP_2026-09-10.md)
- [decision repair report](evidence/PR25_DECISION_REPAIR_2026-09-10.md)
- [dated integration closeout](evidence/LIVE_UI_HISTORY_INTEGRATION_SOURCE_CLOSEOUT_2026-09-03.md)
- [failed-session forensic inventory](evidence/FULL_RUN_COVERAGE_INVENTORY_2026-09-03.md)
- [source closeout](evidence/PR11_EXECUTION_SEMANTIC_ACTION_SPACE_SOURCE_CLOSEOUT_2026-09-02.md)
- [performance source closeout](evidence/PLATFORM_RECORDING_HOTPATH_PERFORMANCE_SOURCE_CLOSEOUT_2026-09-01.md)
- [continuation source closeout](evidence/NATIVE_FOUNDATION_FULL_RUN_SOURCE_CLOSEOUT_2026-08-31.md)
- [Treasure closeout](evidence/NATIVE_FOUNDATION_TREASURE_SOURCE_CLOSEOUT_2026-08-31.md)
- [completion-lineage source
closeout](evidence/NATIVE_FOUNDATION_COMPLETION_LINEAGE_SOURCE_CLOSEOUT_2026-09-01.md)
- [qualification repair source closeout](evidence/PR6_HUMAN_QUALIFICATION_REPAIR_SOURCE_CLOSEOUT_2026-09-01.md)
- [successor owner-ready
closeout](evidence/PR6_SUCCESSOR_OWNER_READY_SOURCE_CLOSEOUT_2026-09-01.md)
- [Windows pre-Human gate](evidence/NATIVE_FOUNDATION_WINDOWS_PRE_HUMAN_GATE_2026-08-31.md)
- [Windows closeout](evidence/NATIVE_FOUNDATION_WINDOWS_HUMAN_CLOSEOUT_2026-08-31.md)
- [final decision packet](evidence/RITSU_ROUTE_FINAL_DECISION_2026-08-31.md)
- [source closeout](evidence/NATIVE_FOUNDATION_SOURCE_CLOSEOUT_2026-08-30.md)
- [source closeout](evidence/NATIVE_SEMANTIC_RUNTIME_DISCRIMINATOR_SOURCE_CLOSEOUT_2026-08-30.md)
- [Human closeout](evidence/NATIVE_SEMANTIC_RUNTIME_DISCRIMINATOR_HUMAN_CLOSEOUT_2026-08-30.md)
- [schema-3 closeout](evidence/SCHEMA3_HUMAN_DATA_LIFECYCLE_CLOSEOUT_2026-08-29.md)
- [causal performance baseline](evidence/RECORDER_CAUSAL_PERFORMANCE_BASELINE_2026-08-29.md)
- [canonical causality decision](evidence/RECORDER_CANONICAL_CAUSALITY_DECISION_2026-08-29.md)
- [schema-2 owner closeout](evidence/SEMANTIC_TIMELINE_OWNER_CLOSEOUT_2026-08-27.md)
- [batch canary](evidence/FULL_RUN_BATCH1_OWNER_CANARY_2026-08-28.md)
- [runtime-seal report](evidence/RUNTIME_SEAL_CANDIDATE_2026-08-24.md)
- [V2 closeout](evidence/HUMAN_EVIDENCE_V2_READ_RICH_COMBAT_CLOSEOUT_2026-08-25.md)
- [owner validation](evidence/RECORDING_APPLICATION_OWNER_VALIDATION_2026-08-25.md)
- [decision gate](evidence/RECORDING_APPLICATION_DECISION_GATE_2026-08-25.md)
- [repair evidence](evidence/PR25_POTION_CAUSAL_REPAIR_2026-09-10.md)

## Collection delivery engineering

The historical Evidence 0.1.0-rc.7 delivery baseline includes opt-in fixed-tool
closed-session delivery and persistent HTTP receipt recovery. See [ADR 0008](adr/0008-release-bound-closed-session-delivery.md)
and [delivery operation](../components/evidence/DELIVERY.md). Native gameplay
source and the PR25 Human artifact are unchanged. Real cloud deployment and a
fresh Close-to-cloud Human delivery journey are not established by portable tests.

- First dedicated Human Close-to-R2 gate and delivery diagnostic repair:
  [bounded audit](evidence/B_PIPELINE_FIRST_HUMAN_UPLOAD_2026-09-13.md).

Evidence rc.7 extends B delivery with explicit same-upload credential recovery.
Typed Hub 401/403 blocks are recoverable under the owner lock; historical incident text is not
reclassified. No additional Human, game runtime or training claim follows from these tests.

The default developer collection/viewing route is now the [shared workbench handoff](ANNOTATOR_COLLECTION.md#default-project-workflow).
The [workflow release report](evidence/B_UNIFIED_WORKFLOW_RELEASE_2026-09-13.md) separates
historical Human/Hub verification from final merge/runtime receipts. Native Mod bytes remain
unchanged; the service/account and portable delivery evidence do not widen the native gate.

For distribution identity use the [explicit packaging metadata correction](evidence/B_WORKFLOW_PACKAGING_IDENTITY_CORRECTION_2026-09-13.md); the older baseline
BOM digest and private tool-source label were incorrect. Original bytes remain unchanged.

The [Windows CollectionTool inventory repair](evidence/WINDOWS_COLLECTION_TOOL_INVENTORY_REPAIR_2026-09-16.md)
aligns fixed-tool verification with the publisher's POSIX path order. It is source/test and
local-tool evidence only: no new Human or upload PASS is claimed. The refreshed rc11
combines this fix with current rc10 behavior; existing released kits retain their original
identities, while new consumers pin the combined source through BOM and the Python lock.

## External policy package candidate

Policy Runtime and Platform Workbench `0.1.0-rc.3` add a standalone locked
Runtime package, current-environment status decoding, and explicit unknown
command handling. The installed CPU gate checks synthetic modes and CLI
start/stop/sealing without contacting STS2. See the
[package and consumer boundary](../components/policy-runtime/README.md).
Release publication and real trained-model Shadow/One-Step/Auto remain separate
gates. The historical policy candidate and native/Human evidence retain their
original identities; no Full-Run, causal-successor or model-quality claim follows.

The HTTP-2 Runtime candidate binds versioned mutation routes to the intended immutable
Runtime run ID. It rejects stale instances and legacy command routes before dispatch.
Matching Live UI source changed and needs a new native install/load gate if deployed;
the historical Human-tested Recorder DLL and fixed collection tool remain at their
qualified identities. The current source is not a new Full-Run Human qualification.
