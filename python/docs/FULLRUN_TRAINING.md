# Full-Run Training and Disposable Workers

This is an engineering implementation, not a Full-Run dataset, cloud or scientific result.
The pooled path below retains its historical contract. The additive [Stage 1a token workflow](research/STAGE1A_WORKFLOW.md)
supports B/D scratch and frozen-Qwen graphs, reusing storage, reporting and metrics with separate versioned schemas.
A pinned Platform bundle3 adapter and a separately scoped synthetic adapter are installed.
Real corpus sufficiency and scientific admission remain separate. Historical combat-v0 and
its scientific protocol are retained unchanged.

## Training performance belongs to the existing chain

Training optimization is bounded engineering of the existing research training edge:
turn approved data and a learning protocol into a recoverable, consumable result
in less total wall time and cost. It spans existing owners; it creates no manager.
The **execution plan** is its versioned trainer/model configuration, not the whole
optimization process, trainer, resource allocation or lifecycle. See [chain purposes](../../docs/plans/p1-prefreeze-2026-10-18/CHAIN_PURPOSES.md).

| Independent fact | Authority and relationship to a plan |
| --- | --- |
| Learning goal, architecture, optimizer, sampling, loss, selection/early stopping | Research recipe; a plan must preserve the approved protocol |
| Immutable membership, splits and exposure | Snapshot/Curation; read-only input, never modified to fit a speed target |
| Packing, kernels, synchronization, microbatches, precision and CPU preparation | Recipe trainer/model; this is the execution plan, frozen by the Run/source |
| Hardware, libraries, CPU/RAM, launch and timeout | Compute provider/worker; measured applicability conditions, not plan-owned resources |
| Intent, admission, budget, checkpoint cadence, pause, continuation and unknown | Application/TrainingService/Ops/Scheduler; independent lifecycle, no second controller |
| Checkpoint/event bytes, immutable indexes, transfer and cold readback | Store/RunReporter; tune their own parameters, no second cache authority |
| Export/registration and downstream consumption | Existing artifact/contracts; a plan cannot silently change their meaning |
| Stage timings, failed/unknown attempts and cost | Existing events/receipts; evidence for a plan, not authority to train |

Execution equivalence preserves sample/update membership, weights, complete inputs,
loss weighting and stochastic draws. FP32 accumulation order may differ within
predeclared loss/gradient tolerances; compare gradients including None versus zero
and full shape tails. Microbatching/precision are not classified by their names:
changed dropout draws, update semantics or precision need a separate quality
comparison. A speed result grants neither quality nor exact old-checkpoint resume.

Measure the complete path (cold prepare/read, update, DEV, save/readback, recovery,
export/consume); change one candidate; check equal-weight real-TRAIN loss/gradients,
full TRAIN shapes and bounded short training with replacement-process restore;
then freeze a named plan and its applicable shape/model/runtime envelope in a new Run.
Use E + V + H + T/s < T to decide if nonoverlapped implementation/verification,
transition and remaining training costs justify a measured speedup. Do not multiply
independent short-window factors into an end-to-end promise.

Reuse evidence only while its conditions hold. New shape tails, material data growth,
model/representation, hardware/library or precision changes require affected short
calibration. During a Run, retain its plan and original-handle unknown handling;
stop on a regression, preserve that Run, and choose reference on a new Run.
CLI/API and optional GUI use the same application; machines do not define owners.

### Historical choices and their present use

These are bounded original results, not inherited qualification. Exact raw evidence
and archived-source references are in [the 2026-10-25 evidence record](../../docs/evidence/P1_TRAINING_SPEEDUP_2026-10-25.md).

| Method / owner | Original outcome | Present disposition and reason |
| --- | --- | --- |
| Packed gathers / model | CP312+256 TRAIN, 4 updates: ABBA 19.949→2.186s/update; FP32 model/Adam differ, same RNG in that window | Candidate for real-tail/gradient/restore qualification; old 9.13x is not a full-run result |
| Row-fused / model | Same short window about 1.19x, exact model/Adam/RNG | Retain as explicit diagnostic/control and prior consumer; not the preferred v1 |
| Micro2 / execution | 1.530s/update but RNG changed and scores differed 0.1833 | Exclude v1; needs quality comparison, not equivalent-execution admission |
| Deferred finite checks / trainer | Old row short window 10.702→10.764s: no measured gain | Retest alone; retain full scores/loss/gradient guards before step, including unselected -inf |
| Parallel events / Store reporter | 647 events 73.089→9.037s, identical event-ID digest | Reuse existing read_concurrency=8 consumers; retain serial default/API and integrity checks |
| Migration lineA / Run+Ops | Normal start HTTP400; no jobs/attempts/GPU submissions; original detailed cause unknown | No migration in v1; new Run keeps original Run/checkpoints immutable |
| lineB / qualification | Five calls support short-window throughput, not full quality/restore | Preserve limited verdict and original failures; add current evidence |
| Auto continuation PR225 / Ops | Original final events: 9 pauses; final delivery records six automatic new segments | Reuse same Ops/Scheduler as failure insurance; longer segments still save periodic CP/DEV |
| Token/CPU packing caches / model | Existing token memoization and host text packing already exist | Reuse; do not claim existing work as a new speed gain or cache learned embeddings |
| CPU threads / resource+execution | Earlier 2/4-thread variants did not establish a repeatable gain | Keep recipe CPU2; no resource expansion based on low utilization |
| J-min CUDA / old numeric worker | Fixed-weight math checks close, q07 raw parameter tolerance failed; later observation-only | Reuse predeclared tolerances and exact restore discipline; do not erase historical FAIL |
| #141 / old M0 provider | Journal original call once; unknown output is never resubmitted | Reuse principle through current Ops, not the old model-specific controller |
| #147 / old request transport | Bounded compressed wire validates logical length/hash; rejects truncated/trailing input | Current Store streaming suffices; no extra codec without a measured need |
| #150 / old M2 engine | Real formal Adam+stage exceeded 128MiB; tiny fixture missed scale and repeated DEV cost | Verify real tails and full CP, not tiny-fixture capacity |
| #158 / export/GUI | CUDA-trained artifact consumed by CPU with training/inference device separate | Reuse normal export/load/register contract; GPU training does not require GPU inference |
| #161/#163 / old campaign | Submit intent persisted first; unknown original paid result blocks redispatch | Reuse existing Ops; do not resurrect campaign journal/scheduler |

The concrete current plan and operator steps are in [P1 T0](P1_T0.md#bounded-real-train-performance-calibration).
Future RL may reuse lifecycle/Store/Compute, while its recipe owns actor/learner,
policy lag, buffer exposure and environment/update denominators. This task adds no RL.

## Immutable input pipeline

Dataset -> ModelView -> FeatureSet -> TrainingInput -> Experiment/Run -> Checkpoint -> Model
-> OfflineEvaluation. Every link uses typed manifests, hashes and exact producer/source/lock
identity, not directory names, a mutable database row or a provider's job number.

ModelView is re-derived from its admitted Dataset during loading. Candidate order, labels,
splits and text must agree exactly. FeatureSet is keyed by exact Qwen identity plus state/action
text; consumers recompute the complete index and candidate alignment. Real Qwen uses the
existing exact pinned backend and scientific identity validator; FakeQwen is engineering-only.
Large weights are not fetched in portable CI. Rebuilt features are not source evidence.

TrainingInput freezes Dataset, ModelView, FeatureSet, Qwen, serializer, source, uv.lock,
entrypoint and complete TrainingConfig. Worker admission precedes optimizer creation. A
research-purpose run also requires a frozen, matching protocol and qualified data; fixture
engineering never authorizes a scientific campaign. Sealed test is not the worker's default.

## Shared model and training

One shared Scheme1 Linear or 256-unit GELU MLP scores all exact candidates across surfaces.
The small head factory is shared with the existing live-Qwen scorer, avoiding two architecture
authorities. This changes the reviewed policy source closure, so v2/v3 manifests receive new
code hashes and manifest identities. No earlier runtime/Human evidence transfers to them.

Training uses immutable precompiled features and listwise cross-entropy, train-only sampling,
explicit seeds/dtype/device, finite checks and absent backbone gradients. Candidate permutation
preserves labels; a label-permutation control operates only within train candidate-count
buckets. Step order is deterministic from seed/epoch/step. This is not a claim of identical
floating-point behavior across different GPUs or framework versions.

## Disposable execution and recovery

The provider-neutral execute function consumes a Run and exact runtime Producer. It records
immutable attempt events and segmented metrics. Checkpoints are durable before their events;
a reporting failure never manufactures success. Resume is explicit by exact checkpoint ID
and verifies Run/Input/source/lock/config/feature plan/framework/step identities.

New worker checkpoints use a bounded typed JSON tree and safetensors, not Python pickle.
The codec supports only primitive values, mappings, sequences and finite dense tensors;
unknown kinds, repeated/missing tensor references and excessive size/depth fail closed.
Final model weights are safetensors too. These validations are not permission to trust an
arbitrary external model producer. PyTorch's official security guidance recommends a small
serialization surface and warns that weights_only does not eliminate all risks:
https://github.com/pytorch/pytorch/security/policy
https://docs.pytorch.org/docs/main/notes/serialization.html

RunReporter is a port. ObjectStoreRunReporter persists events as manifests, so an event-index
write failure is recoverable. One conditional immutable completion slot selects the final
RunResult; identical retries are idempotent and conflicting results fail. This does not
claim exactly-once process execution. In the B Hub lane, CandidateReporter writes immutable
candidates and events without that local completion slot. The Hub validates source/plan/bytes
and selects with its durable attempt fence; it never runs a second competing completion
ledger. See [B operations](CLOUD_PIPELINE_B.md).

## Offline evaluation

The worker evaluates dev only, emits Top1/MRR/NLL, margin, confidence/calibration, surface,
family, candidate-count and run stratification, plus whole-run bootstrap intervals when there
are enough independent runs. Equal-score ties are treated as uniform tie permutations, not
resolved by candidate position. Uniform-legal and train-fitted action-only baselines use the
same exact candidate sets. All reports explicitly say scientific verdict not claimed.

Platform qualification, final Standard serializer profiling/freeze, real Human Gold, real
cloud execution and final STS2 live evaluation remain separate later evidence gates. The [local operations](PREFULLRUN_OPERATIONS.md) surface now provides CLI, generic provider
launch, dashboard/analysis, Gold tooling and the clean-checkout CPU E2E. Latest-head
qualification receipts independently establish the result of those gates.

## 下一轮 M-std：先准备，再一条命令开训

本入口沿同一 Snapshot → TrainingInput → Run → Ops/Scheduler → SSH worker → Store/R2；
`package` 只准备和读回，不分配 GPU、不提交 worker。正式训练须在新数据和整局切分完成、
用户选定权重并明确说“开训”后启动。短校准使用独立 intent、固定 `stop_after`，不冒作正式训练。

1. 使用当前干净源码及 `uv sync --locked --all-extras` 环境，保持原 Curation/Store owner。
   卡 A 更新切分后，取其**新 Snapshot ID**及已选的每局权重，生成一个新的请求 JSON。
   不能按文件夹合并数据、跨 TRAIN/DEV/TEST 重分局，也不能把可读的数据包当作训练用途批准。
2. 正常打开独立的训练 Application 配置。`remote_compute.compute_store` 指向现有 R2 peer；
   `execution_producer` 固定当前 source/lock；`target_path` 可指向尚不存在的本轮学校目标文件。
   没有目标时，准备阶段不会构造 provider 或启动 Scheduler；不能伪造 GPU UUID。
   登录学校和分配机器之前先完成下面的 `package`，记录实测准备耗时。
3. 请求字段为 `intent_id`（新32位hex）、`source_id`（A的Snapshot）、`config`（完整 MStdConfig）、
   `max_seconds`、`stop_after`（正式为 null），可附 `loss_weighting` 和 `checkpoint_policy`。
   新 Run 默认 `mstd-training-v1`。`loss_weighting` 例子是
   `{"schema":"stpd/p1-game-loss-weights-1","rule_id":"用户选定的规则版本","weights_by_root":{"准确TRAIN原局键":2.0}}`。
   必须列全且只列 TRAIN 原局；值须有限且大于0。剔除局在 A 的 Snapshot 里处理。

```sh
python -m spireagent.workbench training package --execution remote \
  --config /ABS/training-project.json --input /ABS/next-request.json \
  --output /ABS/next-package.json --wait-seconds 7200
```

成功包记录原 Run、Snapshot、TrainingInput、完整 R2 闭包读回收据和原 intent，带内容摘要。
原件不覆盖；更新切分/权重/配方后用新 intent 和新输出路径重做。准备失败先按原 intent
查询 `training status --execution remote --intent-id ...`；响应丢失不盲目重投。
这不是免检 token：启动仍验证原训练用途、准确 Run/source/lock/目标，worker 再验输入。

准备完成才由本人登录学校/MFA，分配一张 L40S；复用既有严格 SSH host-key/ProxyJump 入口，
在新的独立 checkout 安装同 source/lock、固定 `CUBLAS_WORKSPACE_CONFIG=:4096:8`，
核实际 Python/Torch/CUDA/GPU UUID/driver，冻结真实 SshTarget。私有 R2 环境文件在源码外，
不得复制进数据包、日志、Git或报告。关闭准备用 Application，绑定真实目标后正常重开同一配置；
`max_seconds` 须覆盖目标 timeout，Ops预算同时覆盖本轮分配（含闲置/失败）及存档余量。
GPU 小测计入整轮8小时上限；下次正式整轮按实际样本数、实测步时和剩余额度再核。

用户说“开训”以后只需：

```sh
python -m spireagent.workbench training start --execution remote \
  --config /ABS/training-project.json --input /ABS/next-package.json
```

这会启动**原已准备 Run**，不重复拟合词表或重新构造数据闭包。原 Scheduler 负责执行和结果接纳，
worker 把周期、DEV最佳、早期、暂停和最终检查点直接发布到已选 R2 Store。
正常 `training status/progress` 查看原 job；unknown先查原句柄，不自动重投。
本命令不会替用户在学校门户分配机器，也不保证学校认证会话永久有效。
需要续段时使用既有同Ops显式 `auto-continue`，保留总绝对截止与剩余额度；不另起监督进程。

### 权重、配方与检查点

每个训练决策乘所属原局权重；以**全 TRAIN 决策的权重均值**归一化到1，再对原 optimizer batch
取均值。微批不单独归一化；原顺序、随机过程和 batch64 保留。长局仍贡献更多决策，
这不是“每局总损失相同”。全1使用原计算表达式；DEV/TEST不加权。规则、完整原局权重表、
Snapshot selection 摘要共同进入 TrainingInput 身份，规范化逐点权重摘要进入检查点恢复身份。
换规则须开新 Run，不在旧 Run 上热换。TRAIN 的 `mean_N_loss` 在带权Run中是带权损失。

建议下轮先保持 AdamW峰值LR3e-4、weight_decay0.01、dropout0.1、ID-mask0.1、warmup5%、
FP32、batch64、CPU2、patience5；将**最多15轮**作为建议上限。第一轮10→15轮TRAIN继续降而
DEV NLL升14.82%，且DEV仅1个原局，不能由此断言LR或模型容量必错；先改善独立局覆盖，
本轮不引入未经质量对照的AMP、微批或更强正则。15轮改变余弦总计划，是新Run配方，
不宣称与旧30轮轨迹等价；用户可保留30轮，须在请求中明确且预算够用。

可选 `checkpoint_policy: "early-quarter-epochs-v1"`：前3轮每四分之一轮（按update向上取整）
做DEV并存当前状态，之后每完整轮保留；同时保留原5/10/20/40/60/80/100%与1800秒周期存档。
同一步重叠只存一次。额外DEV不提前消耗“完整轮”patience，但可选出更早最佳。
学校短测须实测新增DEV/保存成本；更多存档不等于更好模型。

### 训练后马上筛候选，再按实战定

将 `{"run_id":"完成Run的64位ID"}` 保存为 `/ABS/completed-run.json`：

```sh
python -m spireagent.workbench training shortlist --execution remote \
  --config /ABS/training-project.json --input /ABS/completed-run.json --output /ABS/shortlist.json
python -m spireagent.workbench project model --action export-checkpoint \
  --config /ABS/training-project.json --artifact CHECKPOINT_ID --checkpoint-selection current
python -m spireagent.workbench project model --action register \
  --config /ABS/training-project.json --artifact MODEL_ID
```

`shortlist`读完整原事件索引，以DEV NLL最低、Top1最高、最早有DEV的已存状态、其余NLL最佳
依次去重，最多4个；不足3个明确标记。保留原attempt/重复进度分母，绑定准确checkpoint和指标事件。
只读DEV，不重评分TEST、不运行游戏；导出必须选 `current`，不能把几个检查点里的同一 `best_model`
重复当成多个候选。导出/登记仍走原Model入口，准确source/字节身份门不放宽。

新3–4候选加第一轮438，使用 `stpd.evaluation.suites.checkpoint_reference_manifest` 已保留的
同10个Defect A0及2个A10种子。训练/采集排除这些种子。固定同版本、Host、速度、画面、
协议与预算；按自然胜负、unknown/失败、未开及配对局报告，不用HP或进程退出推导胜负。
本轮只交计划。训练后登记得到真实selection，再冻结矩阵/CSV，调用已有 `evaluation plan`；
运行必须再获用户同意。当前该 `plan` 只支持导入带原运行条件的历史矩阵，全新未开矩阵会拒绝
`original_conditions_required`；此缺口归评估链owner，本卡不改其代码，不伪造一次原局来绕过。
未来候选尚不存在时只给显式待绑定草案，不伪造Model/selection或宣称可执行账本已完成。

卡 A 的已确认 `weight-candidates.json` 可直接作为上游输入。将完整配方、全新 intent、
`max_seconds` 和 `stop_after` 保存为请求 JSON，**不填 `source_id`**；命令会把该计划的
原成员、去重选择和整局切分直接交给已有 `p1-data freeze(reserve=True)`，再核实际 Snapshot，
把确认的 configuration_id、规则定义与逐局权重一起交原 prepare。不会重新选择规则或切分。
正常冻结/恢复会由 Store owner 建好 lease；缺 lease 应先走原 owner 恢复，不能在此绕过。

```sh
python -m spireagent.workbench training package --execution remote \
  --config /ABS/training-project.json --input /ABS/recipe-request.json \
  --cohort-plan /ABS/weight-candidates.json --configuration-id CONFIRMED_64_HEX \
  --output /ABS/new-package.json --wait-seconds 7200
```

已有准确 Snapshot 时也可在请求中填 `source_id`，此时只核上游计划与它一致，不另冻数据。
更新数据后使用新配置ID、新 intent、新输出；已有输出会在冻结或预留之前拒绝。
只接受用户已确认的配置；配置文件可读或哈希正确本身不构成训练授权。若 freeze/prepare
响应不明，保留原 Snapshot/预留/intent 并查原状态，不自动重冻或重投。

本卡用原数据生成的演练包固定 `stop_after=8`；它不是下一轮正式数据包。
录完新局后先重跑卡 A 的盘点和整局分配，再运行上述准备命令。正式请求的 `stop_after=null`、
目标 timeout 和总额度须在用户说“开训”之前明确；不修改已有包来改变这些条件。
