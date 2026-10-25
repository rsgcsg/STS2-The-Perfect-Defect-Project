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
