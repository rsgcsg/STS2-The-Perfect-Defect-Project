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
   按[整局切分和确认冻结 SOP](RECOUNT-SOP.zh-CN.md)取得本轮**新 Snapshot ID**及用户已选每局权重，生成新的请求 JSON。旧“卡 A”是已交付的盘点任务，不是要重新派发的前置。
   不能按文件夹合并数据、跨 TRAIN/DEV/TEST 重分局，也不能把可读的数据包当作训练用途批准。
2. 正常打开独立的训练 Application 配置。`remote_compute.compute_store` 指向现有 R2 peer；
   `execution_producer` 固定当前 source/lock；`target_path` 可指向尚不存在的本轮学校目标文件。
   没有目标时，准备阶段不会构造 provider 或启动 Scheduler；不能伪造 GPU UUID。
   可在分配前先做 `package`；计量“冻结结果→提交”时，应先准备好实际分配和准确环境，
   再消费冻结结果。首次登录/分配/安装单独计时，不藏进提速数字。
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

成功包记录原 Run、Snapshot、TrainingInput、实际训练载荷 R2 读回收据和原 intent，带内容摘要。
全部来源manifest/父编号保留；原始录制仅引用，不随计算输入复制或重读。
这不声明原件完整冷恢复；原件保全仍用普通完整复制及原存储校验。
原件不覆盖；更新切分/权重/配方后用新 intent 和新输出路径重做。准备失败先按原 intent
查询 `training status --execution remote --intent-id ...`；响应丢失不盲目重投。
这不是免检 token：启动仍验证原训练用途、准确 Run/source/lock/目标，worker 再验输入。

由本人完成学校登录/MFA，分配一张 L40S；复用既有严格 SSH host-key/ProxyJump 入口，
在新的独立 checkout 安装同 source/lock、固定 `CUBLAS_WORKSPACE_CONFIG=:4096:8`，
核实际 Python/Torch/CUDA/GPU UUID/driver，冻结真实 SshTarget。私有 R2 环境文件在源码外，
不得复制进数据包、日志、Git或报告。关闭准备用 Application，绑定真实目标后正常重开同一配置；
`max_seconds` 须覆盖目标 timeout，Ops预算同时覆盖本轮分配（含闲置/失败）及存档余量。
GPU 小测计入整轮8小时上限；下次正式整轮按实际样本数、实测步时和剩余额度再核。
默认连接方式是本机 OpenSSH → 学校 moss 跳板机 → 本次实际分配的 GPU 主机，
复用已有 SshProvider/Ops/Scheduler。浏览器负责本人登录、MFA、分配与释放机器；
Jupyter 只用于首次核验或连接诊断，不另起一套浏览器训练控制器。新分配须核新主机身份；
若门户指纹字段与 SSH 返回值不一致，先从已认证学校入口核对主机公钥，不能关闭校验。

新学校目标使用 `spireagent/ssh-target-2`，保留严格 source/lock、实际环境和host-key绑定，
另固定 `preparation_seconds`（本轮300秒）。数据验证/加载/恢复结束、第一次控制检查前，
worker写出绑定同request/attempt的本机ready观察，随后才起 `timeout_seconds` 训练时钟。
准备和训练任一超时都停止同一进程组，unknown不重投。300秒存档余量仍保留在训练控制内。
Ops仍作为整个尝试的保守看门狗：`max_seconds`＋worker准备＋控制端准备300秒；
此学校配置的预约单位按秒，`reserved_units`必须至少覆盖这三项，否则提交前拒绝。
例如训练900秒、worker准备300秒，则预约至少1500秒；总 `budget_limit`仍按批准的剩余秒数，
且学校实际分配、安装、闲置、失败成本另外记入整轮8小时，不因“准备免费”而消失。
同Ops自动续段也继承准备预算并服从原绝对截止。
旧 `spireagent/ssh-target-1` 的序列化身份、总墙钟含义和旧配置不变，不静默迁移旧Run。

历史失败保留：2026-10-25 的10319 N短测在旧900秒总墙钟下，start-prepared116.29秒、
Scheduler输入校验174.68秒、用途登记175.37秒、SSH提交8.09秒，最终正常取消，无已确认更新或CP。
新源码/缓存/CI均不能改写该失败；新短测另建同数据/配方/权重的新Run，绑定新准确source。

Q已确认冻结后可直接用一条命令准备包并提交原Scheduler（已有分配/准确环境和正常Application）：

```sh
python -m spireagent.workbench training start --execution remote \
  --config /ABS/training-project.json --input /ABS/next-request.json \
  --cohort-plan /ABS/state/cohorts/CONFIG_ID/weight-candidates.json \
  --configuration-id CONFIG_ID --output /ABS/new-package.json --wait-seconds 900
```

读取Q同一schema、配置ID及snapshot_id；配方请求可省略source_id，由冻结文件提供。
若显式source_id与冻结文件不同，提交前拒绝。已有snapshot_id不另freeze，当前切分/用途/曝光仍实查。
先沿原package保存原intent，再沿原start-prepared提交，未增加控制器或训练链。
`--output`必须不存在；若响应丢失，查已存包/原intent/job/句柄，不能重复命令碰运气。
`--wait-seconds`只是单次HTTP等待，不能冒充三/五分钟性能已达标。
操作内缓存只保当前一次准备/提交，提交后或下一次准备时释放；跨进程重新验证实际输入。
Q的P1PreparationCache仍核代码/输入身份与实际SHA，缓存的是索引/局事实，未缓存模型Frame。


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

检查点/结果冷接收使用 `storage.input_transfer.copy_input_artifact` 和已安装消费者的
`stpd.p1.workload.result_read_payloads`；包括验证器实际需要的CP/模型/输入，全部raw编号仍在。
不能对引用式计算Store使用完整 `copy_artifact` 并把缺少raw解释成检查点损坏。
普通完整保全/恢复的接口及哈希要求保持不变。详见[计算输入约定](DATA_LIFECYCLE.md#p1-compute-input-availability)。

### 权重、配方与检查点

#### 下一轮 T2 的输入 v2

输入定义、集合/序列清单、固定公开词表和追加参数的办法归
[P1 M-std 输入 owner](P1_T0.md#model-input-v2-and-the-separate-content-sorted-438-projection)。
T2 从头初始化新血统；保留 FP32、batch64、AdamW 和 `mstd-training-v1`。
先生成普通 prepare 请求（只写文件，不创建 Run，不提交计算）：

```sh
python -m stpd.p1.input_cli request --max-epochs 15 --max-seconds 18000 \
  --output /ABS/new-t2-v2-request.json
```

请求内绑定完整 v2 InputSpec、全新 intent 和早期检查点策略，不带数据源或权重默认值。
要做学校短测，另生成 `--stop-after 8` 的请求并核实际目标时限；本轮未占用学校。
8 步从零小验与 438 步旧模型的 DEV Top1 只作描述比较，不能当成同预算质量证明。
小验须损失/梯度有限、固定 TRAIN 探针下降、打乱不变及 packed/reference 检查通过；
新血统是否更强不能用小验代替。当前 8 步 DEV 为 240/446（53.81%），
旧 438 为 300/446（67.26%）；因此没有取得“准确率不明显更差”的证据，
不据此放行 T2。正式训练前仍须按新冻结数据和已批预算明确质量比较方案。

**上游先完成**：W1 的新 A10 权重和每次训练/考试局选择、曝光台账，由原 Curation
负责；使用其最终、用户已确认冻结的配置和 Snapshot。录制/Q 新入库的合格局在冻结时
统一进入池子，由 W1 的结果选择，不能拿旧 25 局当全量。W1 代码已合入；真实分配/新权重未确认冻结就等它；
只有用户明确选择旧 414 才可使用旧权重，不会自动回退。DEV/TEST 不加权，考试局必须
是该模型没有训练过的；继承参数还须继承原模型的曝光记录，本卡不另建选局账。

训练 Application/worker 必须运行本次合入的准确源码和锁；合并不会升级正在用的服务。
学校须有真实分配、有效认证、严格主机身份和 R2 reader。原“约剩 6.8 小时”只是旧估计，
T2 启动前核当时额度。请求的 18000 秒是建议的 5 小时训练上限，不是已消费/新批准费用；
另外预留两段准备时间和至少 300 秒存档余量，整个分配/闲置/失败仍计总额度。
15 轮不是必须跑满；保留 patience5 和 DEV 选点，预算不足先收紧新 Run，不能热改旧包。

条件满足后沿原一条命令准备（不会提交 provider）：

```sh
python -m spireagent.workbench training package --execution remote \
  --config /ABS/training-project.json --input /ABS/new-t2-v2-request.json \
  --cohort-plan /ABS/confirmed-weight-candidates.json --configuration-id CONFIRMED_ID \
  --output /ABS/new-t2-v2-package.json --wait-seconds 7200
```

用户明确“开训”后用上文同一 `training start --input /ABS/new-t2-v2-package.json`
启动原 prepared Run。未准备/未确认的文件不是可执行训练许可。未知响应查原 intent/job，
不重投。当前没有替用户冻结 W1 或创建正式 T2 Run。

#### T2 新候选的评估账本

保留下面的 shortlist、逐检查点 `current` 导出、正常模型注册顺序；候选实际产生之前
不伪造 selection ID。先用 Managed 快筛，再以真游戏 Reference 判定，分账报告。
同样的 10 个 A0 + 2 个 A10 保留种子可复用，不能混入训练/录制选择。

原 `evaluation plan` 的全未开矩阵现已支持在冻结 matrix JSON 中给出明确 `conditions`：
`run_profile`、`display_mode`、`speed`、`evidence_peer` 四项全部具名。
例如 Reference 可用 extended / native-ui / fast 和实际 R2 peer；具体条件必须按该轮
已合格环境冻结。配套 CSV 每个槽为 not_started、run_id/原报告/原件 ID 为空。
矩阵仍须绑定实际 manifest SHA、真实已注册候选和完整分母；普通 plan 命令即可导入，
不需要先运行一局。不提供 conditions 且没有原运行条件仍拒绝；含历史结果时，声明条件
必须与原报告一致，不能借新配置改写旧成绩。Managed 继续使用原 `evaluation create`
和独立的精确 execution/manifest，不在此合并两种 Host 的结果。

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
  --config /ABS/evaluation-project.json --artifact MODEL_ID
```

`shortlist`读完整原事件索引，以DEV NLL最低、Top1最高、最早有DEV的已存状态、其余NLL最佳
依次去重，最多4个；不足3个明确标记。保留原attempt/重复进度分母，绑定准确checkpoint和指标事件。
只读DEV，不重评分TEST、不运行游戏；导出必须选 `current`，不能把几个检查点里的同一 `best_model`
重复当成多个候选。导出/登记仍走原Model入口，准确source/字节身份门不放宽。
登记使用将执行实战的已合格 Application 配置；438与新候选须在同一评估registry中可见。
导出端必须具备该检查点的准确数值代码，评估端须正常读到同一Store/R2的Model产物。

新3–4候选加第一轮438，使用 `stpd.evaluation.suites.checkpoint_reference_manifest` 已保留的
同10个Defect A0及2个A10种子。训练/采集排除这些种子。固定同版本、Host、速度、画面、
协议与预算；按自然胜负、unknown/失败、未开及配对局报告，不用HP或进程退出推导胜负。
本轮只交计划。训练后登记得到真实selection，再冻结矩阵/CSV，调用已有 `evaluation plan`；
运行必须再获用户同意。全新未开矩阵在显式冻结 `conditions` 后可导入，见上方 T2 账本说明；
不声明条件仍拒绝 `original_conditions_required`，不能伪造一次原局来绕过。
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
`--wait-seconds`同时传给原freeze和prepare的单次HTTP等待；默认freeze仍为600秒，显式值
限1–86400秒。它不改变整个任务的总截止、学校额度或worker时限，超时也不重投。

本卡用原数据生成的演练包固定 `stop_after=8`；它不是下一轮正式数据包。
录完新局后先重跑卡 A 的盘点和整局分配，再运行上述准备命令。正式请求的 `stop_after=null`、
目标 timeout 和总额度须在用户说“开训”之前明确；不修改已有包来改变这些条件。

### Repeating the P1 whole-run inventory before a new training batch

Curation still owns purpose and permanent exposure; `stpd.p1.cohort` only drafts
an explicit selection and reports its weights. It reads closed immutable Store
artifacts through the existing P1 reader, merges native-decision/seed/ancestry
fingerprints there, and retains existing Curation roles. Conflicting old/new
projections must be resolved by explicit projection IDs or a fixed Tool release;
there is no implicit “latest” union. Unknown ancestry remains unknown.

From `python/`, with the normal locked research environment and existing project:

```bash
uv run --locked python tools/p1_cohort.py \
  --config /ABS/project.json --selection /ABS/cohort-selection.json \
  --output /ABS/new-inventory-directory
```

The selection is the existing `selection` object from a frozen request, or
`{"tool_release_ids":["EXACT_RELEASE_ID"]}`. New recordings first use the normal
recordings import/derive entry; this command does not convert originals. It
requires 15 GiB free on the output and Store volumes, uses a read-only local
Store with its pinned peers, and never reserves purpose, records training use,
publishes a snapshot, starts a worker or controls a game. A missing read lease
must be handled by the existing Store owner, not by bypassing it. An older
installed App may be inventoried without replacing its combination; this is
read authority, not installed-version admission.

Outputs are `index.json`, `facts.json`, `allocation.json`, `plan.json` and the
source catalog. `plan.configuration_id` binds exact projection/source identities,
selected decisions, whole-group roles, per-run evidence and weight parameters.
The user-fixed progress rule is: win 3; loss or unknown outcome
`0.25 + 1.25 * (recorded_floor / finish_floor)^2 + 0.25 * witnessed_boss_victories`.
Use the same difficulty's recorded winning floor; if absent, read the final-act
boss map's final row in global floor coordinates. Only A10 may fall back to A0,
and that fallback is explicit. Conflicting denominators or any nonwin weight
at least 3 stop the plan. Unknown outcomes retain their observed progress and a
separate reason/count. Missing boss evidence grants no bonus; a boss map icon
or reaching a later floor is never a victory. The current positive witness is
an observed boss encounter followed by its reward at the same act/floor.
Distinct A10 final bosses occupy distinct recorded floors and count separately.

Weights apply only to TRAIN N losses; DEV and TEST remain unweighted. The plan
retains the former candidate 2 solely for a requested component-share comparison.
It does not implement numerical weighting: the training recipe/worker consumer
must bind this configuration, support weights, and prove all-one equivalence
before weighted training. A plan marked awaiting pre-freeze confirmation is not
a training input or permission to train.

To extend holdouts, optionally pass `--holdout-targets /ABS/targets.json`, e.g.
`{"dev":{"0":4,"10":3},"test":{"0":4,"10":3}}`. These are total whole-group
counts, not percentages or added counts. The draft preserves every existing
role and exposure; it fills missing strata only from new unexposed whole groups
with native start/end witnesses, selected by difficulty and a fixed salted hash,
without using win/loss or length to choose members. Shortfalls are reported and
`ready_to_freeze` stays false. Persist confirmed roles through the existing
snapshot/Curation path before adding the next batch, so subsequent discoveries
do not reshuffle old holdouts. Seed/restore ancestry still limits the independence
claim. Keep the first-round TEST separately labelled for historical comparison;
new unused TEST groups measure transfer to new runs. Never move old TRAIN into TEST.

Before freezing, inspect the per-run table, unknowns, exclusions and component
shares; confirm with the user when required by the active task. Then pass the
exact same plan to the training preparation owner. Do not silently refit
vocabulary/tokenizer from DEV/TEST. Current M-std public run `ascension` is a
numeric InputSpec field; TRAIN-only symbolic vocabularies still limit other
characters, and this workflow does not repair that limitation.

For repeated batches, the selection can also contain the frozen
`projection_ids` plus `new_source_tool_release_ids:["REVIEWED_TOOL_ID"]`.
Previously selected originals keep their exact version; newly discovered originals
are added only when exactly one r1.5 Human projection matches the reviewed Tool
set. Multiple matching versions stop with `new_source_projection_version_ambiguous`.
Originals without a selected/reviewed projection remain in `pending_sources.json`
with a reason, so a rerun cannot silently claim that eight new raw sessions added
eight usable runs. Review a genuinely new conversion Tool once before adding its
ID to this explicit policy. The same per-batch native start/end, gap, provenance,
whole-group and exposure constraints remain in effect.

## T2 outcome auxiliary heads

For a new input-v2 run, `config.value_heads=true` adds win/progress supervision;
`model_size` defaults to `standard`. The optional `large` comparison doubles
text/scene depth while preserving widths and the recipe. Missing optional fields
retain the historical N-only configuration and package reader. See
[T2 preparation](../../docs/evidence/T2_VALUE_PREPARATION_20261028.md).

Natural native terminal witnesses alone supervise outcomes. Unknown/technical
stops are masked, while their admitted imitation points remain unchanged. The
fixed A0=48/A10=49 progress map, 0.01 auxiliary loss coefficients, Architecture,
TargetSpec and full checkpoint schema are explicit identities. DEV action NLL is
still the sole selection/early-stop metric. Export removes both heads, so Runtime
continues to select solely by action logits.

`training package` sends preparation to the configured Application, whose executing
source must match `execution_producer`; running a newer CLI against an older daily
Application does not add the new graph. Use a separate training Application with
this exact source/lock and its own state/Ops/target configuration, bound to the same
Store and Curation owner. Do not silently replace the daily recording installation.
Prepare the package without submitting a provider, then start that saved Run once
the displayed split/weights and final freeze are user-confirmed. Never repeat a
start after an uncertain response; inspect its original intent/job/handle.

T2's fixed evaluation suite is `t2_managed_manifest`: 40 A0/10 A10, including the
old 10/2 prefix. All are reserved against collection/training and future RL seeds.
Freeze the current cohort once; later recordings belong to a later experiment.
