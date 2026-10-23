# Full-Run Training and Disposable Workers

This is an engineering implementation, not a Full-Run dataset, cloud or scientific result.
The pooled path below retains its historical contract. The additive [Stage 1a token workflow](research/STAGE1A_WORKFLOW.md)
supports B/D scratch and frozen-Qwen graphs, reusing storage, reporting and metrics with separate versioned schemas.
A pinned Platform bundle3 adapter and a separately scoped synthetic adapter are installed.
Real corpus sufficiency and scientific admission remain separate. Historical combat-v0 and
its scientific protocol are retained unchanged.

## Training performance belongs to the existing chain

Performance engineering measures and improves the complete useful result per wall
second and per cost under a stated quality requirement. It is a process across
owners, not another top-level training manager or a new model authority.
[Architecture roles](../../docs/ARCHITECTURE.md) and
[chain purposes](../../docs/plans/p1-prefreeze-2026-10-18/CHAIN_PURPOSES.md) continue to own those boundaries.

| Work or fact | Owning implementation |
| --- | --- |
| Objective, data/split, architecture, optimizer, selection and quality protocol | Research recipe and its existing evaluation protocol |
| Tensor packing, numerical kernels, microbatches, precision, synchronization and model-compatible export | That recipe's trainer/model implementation; freeze the selected execution in its Run and package |
| Source/data-use admission, one intent, budget, attempt, fencing, continuation and accepted result | Existing Application / TrainingService / Curation / Ops / Scheduler |
| Immutable bytes, verified readers, event indexes, checkpoint transfer and durability | Existing Store / RunReporter; no model-specific second cache authority |
| GPU/CPU/RAM/timeouts, launch, original-handle observation and termination | Existing Compute provider/worker deployment |
| Fair public observations, complete legal action catalogs and receipts | Platform contracts; training optimization adds no gameplay authority |

Changing a numerical implementation is distinct from changing the learning
objective or judging quality. Same mathematical intent need not mean the same
floating-point path. No throughput result alone grants exact resume, quality,
installed use or live-game qualification. CPU/GPU code separation is an
implementation choice within the recipe; a machine or process is not a new
logical owner. CLI/API and optional GUI call the same use cases.

Use this bounded lifecycle in the existing task and original recipe guide:

1. Before training, freeze input/shape, recipe, execution, library/device identity,
   budget and restore requirements. Reuse a measured profile whose conditions
   still apply. For new or materially changed conditions, run a short real-TRAIN
   clone through the recipe's actual update path. Check complete updates and
   full save/restore before committing a long allocation.
2. Separate preparation/read, tensor construction/transfer, forward/backward,
   optimizer, DEV/selection, checkpoint publication, reconciliation and downstream
   consumption. Normalize examples and complete optimizer updates; distinguish
   cold start, warmup and steady state. Compare interleaved/reverse controls on
   the same allocation. Collect diagnostic traces outside throughput windows.
   Measure only enough to decide the first bottleneck; do not profile every step.
3. During training, observe phase wall times, actual updates, loss/gradients,
   checkpoint durability and the original job/attempt/handle. Investigate a
   measured regression or stalled phase in its owner. An apparent empty GPU or
   blank UI is not enough to change a live numerical policy. Unknown execution
   stays on the original handle and is not resubmitted.
4. After training, apply the frozen DEV/TEST protocol, CPU Model consumption and
   required Agent/Reference evaluation with all failed/unknown/censored attempts.
   Record useful outcome, quality, total wall/cost, remaining limits and applicable
   performance conditions. Keep negative experiments and original source records.

Same-Run recovery creates a new attempt with the same admitted source/config and
exact compatible full checkpoint. An intentional repeated independent training
creates a new Run/use/cost with its declared seed; it can reuse valid measurement
knowledge without rerunning every profiler. Changed data/shape/model/library/
hardware/precision invalidates affected claims, not every unrelated result.
Cross-source state transfer requires an explicit reviewed derivation preserving
full optimizer/RNG/sampler/cursor/DEV-best/early-stop/history and original lineage;
it is not ordinary resume or a weights-only import. Keep the old Run immutable.
A new implementation may also run from scratch under a new Run without claiming
state continuity.

Future RL can reuse these lifecycle/Store/Compute boundaries, while an RL recipe
must own its actor/learner, policy version and lag, buffer/exposure, environment
steps versus learner updates, and return/censor/cost evaluation. Those are future
recipe contracts; this change implements neither RL nor a generic executor
framework. Current N-only M-std remains supervised imitation. Quality changes
follow the existing [research roadmap](research/RESEARCH_ROADMAP.zh-CN.md), independently of a
performance-only comparison.

The concrete P1 operation and measured limits are in [P1 T0](P1_T0.md#bounded-real-train-performance-calibration)
and the [current optimization task](../../docs/plans/p1-prefreeze-2026-10-18/TRAINING_PERFORMANCE_20261023.zh-CN.md).

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
